"""Store (merchant) provisioning and payment-link management.

Auditing here follows one rule: **any change to where a store's money is sent
writes a `store.link_set` row.** A store created with a link, a link attached to
an existing store and a link replaced by a PATCH all produce it, because "who
pointed this store's payouts at that account" is the question an audit trail
exists to answer. The generic `store.created` / `store.updated` / `store.disabled`
rows describe the rest of the mutation.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..schemas import LinkCreate, LinkIn, StoreCreate, StorePatch
from .payments import gen_public_id


def _link_details(payload: LinkIn | LinkCreate) -> dict:
    # Both link schemas reach here: `LinkIn` from the dedicated attach route,
    # `LinkCreate` nested inside StoreCreate/StorePatch. They carry the same
    # fields, which is why `attach_link_to_store` accepts either.
    return {
        "merchant_account_id": payload.merchant_account_id,
        "merchant_name": payload.merchant_name,
    }

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
        await attach_link_to_store(session, store, payload.link)
        store.status = models.STORE_ACTIVE
        audit.record(
            session,
            actor=account,
            action="store.link_set",
            target_type="Store",
            target_id=store.id,
            details=_link_details(payload.link),
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

    await attach_link_to_store(session, store, payload)
    store.status = models.STORE_ACTIVE
    audit.record(
        session,
        actor=account,
        action="store.link_set",
        target_type="Store",
        target_id=store.id,
        details=_link_details(payload),
    )
    await session.commit()
    await session.refresh(store)
    return store


async def attach_link_to_store(
    session: AsyncSession, store: models.Store, payload: LinkIn | LinkCreate
) -> None:
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
        verification=models.LINK_VERIFIED,
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
        await attach_link_to_store(session, store, payload.link)
        if store.status == models.STORE_DRAFT:
            store.status = models.STORE_ACTIVE
        audit.record(
            session,
            actor=account,
            action="store.link_set",
            target_type="Store",
            target_id=store.id,
            details=_link_details(payload.link),
        )
    await session.commit()
    await session.refresh(store)
    return store


async def load_link(session: AsyncSession, store_id: int) -> models.PaymentLink | None:
    res = await session.execute(
        select(models.PaymentLink).where(models.PaymentLink.store_id == store_id)
    )
    return res.scalar_one_or_none()
