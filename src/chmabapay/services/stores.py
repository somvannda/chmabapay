"""Store (merchant) provisioning and payment-link management.

Auditing here follows one rule: **any change to where a store's money is sent
writes a `store.link_set` row.** A store created with a link, a link attached to
an existing store and a link replaced by a PATCH all produce it, because "who
pointed this store's payouts at that account" is the question an audit trail
exists to answer. The generic `store.created` / `store.updated` / `store.disabled`
rows describe the rest of the mutation.
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..schemas import LinkCreate, LinkIn, StoreCreate, StorePatch
from . import payway_parser
from .payments import gen_public_id


def _link_details(payload: LinkIn | LinkCreate) -> dict:
    # Both link schemas reach here: `LinkIn` from the dedicated attach route,
    # `LinkCreate` nested inside StoreCreate/StorePatch. They carry the same
    # fields, which is why `attach_link_to_store` accepts either.
    return {
        "merchant_account_id": payload.merchant_account_id,
        "merchant_name": payload.merchant_name,
    }


# A store's destination must be an ABA PayWay share link or the slug of one. This is
# the cheap half of the check — a value that is not a PayWay link at all (a Bakong
# account id, an arbitrary URL) is refused here without spending an outbound call.
PAYWAY_LINK_HOST = "payway.com.kh"
_BARE_SLUG = re.compile(r"[A-Za-z0-9._-]{4,64}")


def _link_shape_error(raw_link: str) -> str | None:
    s = (raw_link or "").strip()
    if not s:
        return "a link is required"
    if "/" in s:
        if PAYWAY_LINK_HOST not in s.lower():
            return (
                "expected an ABA PayWay share link such as "
                "https://link.payway.com.kh/ABAPAYxxxxxxx"
            )
        return None
    if not _BARE_SLUG.fullmatch(s):
        return "expected an ABA PayWay share link, or its slug (4-64 characters)"
    return None


async def resolve_link(payload: LinkIn | LinkCreate) -> str:
    """Settle a link's verification against PayWay, or refuse it. Returns the state.

    A store used to be marked ``verified`` and ``active`` on write with no check at
    all, so a mistyped slug produced a store that looked ready and failed on the
    merchant's first customer as a 502. Two things are checked now:

      - the shape, which needs no network and catches a value that is not a PayWay
        link in the first place;
      - PayWay's own answer for the slug, which is the only thing that can catch a
        typo, because a mistyped slug is still a well-formed string.

    Only a positive answer marks the link verified. When PayWay cannot be reached the
    link is stored as *unverified*: an ABA outage must not block a signup, and an
    unverified store still takes payments.
    """
    raw = (payload.raw_link or "").strip()
    shape_error = _link_shape_error(raw)
    if shape_error:
        # Prefixed so the wizard can put this under the link field rather than in a
        # page-level banner — it is one field's problem, not the form's.
        raise HTTPException(status_code=400, detail=f"payway_link_invalid: {shape_error}")

    check = await payway_parser.verify_link(raw)
    if check.outcome == "not_found":
        raise HTTPException(
            status_code=400,
            detail=(
                "payway_link_not_found: ABA PayWay has no link at "
                f"{check.slug or raw}. Copy the share link again from the ABA app — "
                "a mistyped link takes payments nowhere."
            ),
        )

    if check.verified:
        # The caller's own name wins when they gave one: it is what the payer sees in
        # their banking app, and the merchant knows their outlet better than the SSR
        # payload does. ABA's name fills the gap when they left it blank.
        if not (payload.merchant_name or "").strip() and check.merchant_name:
            payload.merchant_name = check.merchant_name
        return models.LINK_VERIFIED
    return models.LINK_UNVERIFIED

# Checkout branding. These render on /pay/{public_id} only for accounts that are
# entitled to white-label, so setting them is refused without the entitlement.
BRANDING_FIELDS = ("brand_color", "logo_image_url", "whitelabel_css")


def reject_unentitled_branding(account: models.Account, payload) -> None:
    """Allow writes that clear branding; refuse writes that would set it."""
    if account.whitelabel_enabled:
        return
    if any(getattr(payload, name, None) for name in BRANDING_FIELDS):
        raise HTTPException(status_code=403, detail="whitelabel_not_enabled")


async def create_store(session: AsyncSession, account: models.Account, payload: StoreCreate):
    reject_unentitled_branding(account, payload)
    store = models.Store(
        public_id=gen_public_id("st_"),
        account_id=account.id,
        created_via="api",
        name=payload.name,
        external_id=payload.external_id,
        city=payload.city,
        support_email=payload.support_email,
        redirect_success_url=payload.redirect_success_url,
        redirect_failure_url=payload.redirect_failure_url,
        telegram_chat_id=payload.telegram_chat_id,
        brand_color=payload.brand_color,
        logo_image_url=payload.logo_image_url,
        whitelabel_css=payload.whitelabel_css,
        status=models.STORE_DRAFT,
    )
    session.add(store)
    await session.flush()
    audit.record(
        session,
        actor=account,
        action="store.created",
        target_type="Store",
        target_id=store.id,
        details={"name": store.name, "external_id": store.external_id},
    )

    if payload.link is not None:
        verification = await resolve_link(payload.link)
        await attach_link_to_store(
            session, store, payload.link, verification=verification
        )
        # Only a link PayWay confirmed promotes the store to active. An unverified
        # one is stored — so the merchant is not blocked, and can retry — but the
        # store keeps saying `draft`, because "active" is a claim about a
        # destination we have not been able to check.
        if verification == models.LINK_VERIFIED:
            store.status = models.STORE_ACTIVE
        audit.record(
            session,
            actor=account,
            action="store.link_set",
            target_type="Store",
            target_id=store.id,
            details={**_link_details(payload.link), "verification": verification},
        )

    await session.commit()
    await session.refresh(store)
    return store


async def attach_link(
    session: AsyncSession,
    account: models.Account,
    store_public_id: str,
    payload: LinkIn,
) -> models.Store:
    res = await session.execute(
        select(models.Store).where(
            models.Store.account_id == account.id,
            models.Store.public_id == store_public_id,
        )
    )
    store = res.scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="store_not_found")
    if store.status == models.STORE_DISABLED:
        raise HTTPException(status_code=400, detail="store_disabled")

    verification = await resolve_link(payload)
    await attach_link_to_store(session, store, payload, verification=verification)
    if verification == models.LINK_VERIFIED:
        store.status = models.STORE_ACTIVE
    audit.record(
        session,
        actor=account,
        action="store.link_set",
        target_type="Store",
        target_id=store.id,
        details={**_link_details(payload), "verification": verification},
    )
    await session.commit()
    await session.refresh(store)
    return store


async def attach_link_to_store(
    session: AsyncSession,
    store: models.Store,
    payload: LinkIn | LinkCreate,
    *,
    verification: str,
) -> None:
    """Write the destination. `verification` is required, not defaulted.

    The state of a link is the one thing this module cannot work out for itself, so
    every caller has to say where it came from: `resolve_link` for merchant-facing
    writes, an explicit `LINK_VERIFIED` for the operator console's own link, which
    is checked for shape there rather than probed.
    """
    res = await session.execute(
        select(models.PaymentLink).where(models.PaymentLink.store_id == store.id)
    )
    link = res.scalar_one_or_none()
    values = dict(
        link_type=models.LINK_ABA_PAYWAY,
        raw_link=payload.raw_link,
        merchant_account_id=payload.merchant_account_id,
        merchant_name=payload.merchant_name,
        currency="USD",
        verification=verification,
        status="active",
    )
    if link is None:
        link = models.PaymentLink(store_id=store.id, **values)
        session.add(link)
    else:
        for key, value in values.items():
            setattr(link, key, value)


async def list_stores(session: AsyncSession, account: models.Account) -> list[models.Store]:
    res = await session.execute(
        select(models.Store)
        .where(models.Store.account_id == account.id)
        .order_by(models.Store.id)
    )
    return list(res.scalars().all())


async def get_store(
    session: AsyncSession, account: models.Account, store_public_id: str
) -> models.Store:
    res = await session.execute(
        select(models.Store).where(
            models.Store.account_id == account.id,
            models.Store.public_id == store_public_id,
        )
    )
    store = res.scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="store_not_found")
    return store


async def disable_store(
    session: AsyncSession, account: models.Account, store_public_id: str
) -> models.Store:
    store = await get_store(session, account, store_public_id)
    store.status = models.STORE_DISABLED
    audit.record(
        session,
        actor=account,
        action="store.disabled",
        target_type="Store",
        target_id=store.id,
        details={"name": store.name},
    )
    await session.commit()
    return store


async def enable_store(
    session: AsyncSession, account: models.Account, store_public_id: str
) -> models.Store:
    """Bring a disabled store back.

    `disable_store` had no counterpart, and both `update_store` and `set_store_link`
    refuse a disabled store with `store_disabled` — so in practice disabling was
    one-way: the merchant's own dashboard offers the button, one press stops the
    store's payment links, keys and webhooks from working, and the only route back
    was a manual UPDATE on the database.

    The status it returns to is derived rather than remembered, because
    `disable_store` overwrites whatever was there. `active` only when the store still
    has a payment link; otherwise `draft`. Restoring `active` unconditionally would
    advertise a store as able to take payments when it has no destination to send
    them to — which is the state the link check in `create_payment` exists to catch.
    """
    store = await get_store(session, account, store_public_id)
    if store.status != models.STORE_DISABLED:
        # Already live. Nothing changed, so nothing is recorded — the same rule
        # `update_store` follows for an empty PATCH.
        return store

    has_link = (
        await session.execute(
            select(models.PaymentLink.id).where(
                models.PaymentLink.store_id == store.id
            )
        )
    ).scalar_one_or_none()
    restored = models.STORE_ACTIVE if has_link is not None else models.STORE_DRAFT
    store.status = restored
    audit.record(
        session,
        actor=account,
        action="store.enabled",
        target_type="Store",
        target_id=store.id,
        details={"name": store.name, "status": restored},
    )
    await session.commit()
    await session.refresh(store)
    return store


async def update_store(
    session: AsyncSession, account: models.Account, store_public_id: str, payload: StorePatch
) -> models.Store:
    store = await get_store(session, account, store_public_id)
    if store.status == models.STORE_DISABLED:
        raise HTTPException(status_code=400, detail="store_disabled")
    reject_unentitled_branding(account, payload)
    payload.apply_to(store)
    if payload.model_fields_set:
        # The fields the call submitted. An empty PATCH changed nothing, so it is
        # not recorded at all.
        audit.record(
            session,
            actor=account,
            action="store.updated",
            target_type="Store",
            target_id=store.id,
            details={"fields": sorted(payload.model_fields_set)},
        )
    if payload.link is not None:
        verification = await resolve_link(payload.link)
        await attach_link_to_store(
            session, store, payload.link, verification=verification
        )
        if verification == models.LINK_VERIFIED and store.status == models.STORE_DRAFT:
            store.status = models.STORE_ACTIVE
        audit.record(
            session,
            actor=account,
            action="store.link_set",
            target_type="Store",
            target_id=store.id,
            details={**_link_details(payload.link), "verification": verification},
        )
    await session.commit()
    await session.refresh(store)
    return store


async def load_link(session: AsyncSession, store_id: int) -> models.PaymentLink | None:
    res = await session.execute(
        select(models.PaymentLink).where(models.PaymentLink.store_id == store_id)
    )
    return res.scalar_one_or_none()
