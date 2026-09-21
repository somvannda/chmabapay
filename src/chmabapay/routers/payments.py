"""Public payment API: create / retrieve / list payments."""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models, schemas
from ..auth import AuthContext, get_current_auth_context
from ..config import get_settings
from ..db import get_session
from ..openapi import (
    AUTH_ERRORS,
    AUTH_SECURITY,
    CONFLICT_ERROR,
    QUOTA_ERROR,
    merged,
)
from ..schemas import money_to_str
from ..services import payments as svc
from ..workers import Q_DETECTION, get_global_transport

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/payments",
    tags=["payments"],
    dependencies=AUTH_SECURITY,
    responses=AUTH_ERRORS,
)


class PaymentListResponse(BaseModel):
    data: list[schemas.PaymentListed]


class PaymentReverseIn(BaseModel):
    """Why the money went back.

    Optional, because refusing to record a real refund for want of a note would
    leave the ledger knowingly wrong. It exists anyway because a reversal with no
    reason is indistinguishable from a mistake six months later.
    """

    reason: str | None = Field(default=None, max_length=255)


def _ctx_mode(ctx: AuthContext) -> str:
    """Returns 'live' or 'test' mode for an auth context. Session (portal) users are
    always treated as live; only payments created with a test-mode key are test mode."""

    if ctx.api_key_mode:
        return ctx.api_key_mode
    return "live"


def _offline_qr_is_confirmable() -> bool:
    """Can a QR we build ourselves ever be matched to a real payment here?

    An ABA-issued QR carries a session ABA answers for. A code we build offline
    has to be matched against a ledger instead, which needs Bakong Open API
    credentials — or the dev fake rail, which settles by hand.
    """
    settings = get_settings()
    return bool(
        settings.enable_dev_gateway
        or settings.bakong_api_token
        or settings.bakong_developer_email
    )


def payment_out(
    payment: models.Payment,
    checkout_url: str | None,
    store_id: str,
    external_id: str | None = None,
    reissued_from: str | None = None,
) -> schemas.PaymentOut:
    return schemas.PaymentOut(
        id=payment.public_id,
        status=payment.status,
        amount=money_to_str(payment.amount_cents),
        currency=payment.currency,
        reference_id=payment.reference_id,
        metadata=payment.metadata_,
        store=store_id,
        external_id=external_id,
        checkout_url=checkout_url,
        qr_string=payment.qr_string,
        scanned_at=payment.scanned_at,
        paid_at=payment.paid_at,
        approved_at=payment.approved_at,
        created_at=payment.created_at,
        expires_at=payment.expires_at,
        # Without this the dashboard's "Bakong ref" row is permanently "-" even
        # for a settled payment, so the merchant's only reconciliation handle
        # (ABA's tran id) never reaches them.
        bakong_ref=payment.bakong_ref,
        reissued_from=reissued_from,
        reversed_at=payment.reversed_at,
        reversal_reason=payment.reversal_reason,
        detection_closed_at=payment.detection_closed_at,
    )


async def _enqueue_detection(
    payment: models.Payment, mode: str, store_public_id: str
) -> None:
    """Hand the new payment to W1 so it is polled for payment status."""
    tp = get_global_transport()
    if tp is None:
        return
    try:
        await tp.enqueue(
            Q_DETECTION,
            dedup_key=f"detect:{payment.public_id}",
            payload={
                "payment_public_id": payment.public_id,
                "api_key_mode": mode,
                "store_id": store_public_id,
            },
        )
    except Exception as err:  # noqa: BLE001 - never block HTTP response
        logger.warning(
            "W1 detection enqueue failed for %s: %s", payment.public_id, err
        )


async def _single_active_store(session: AsyncSession, account_id: int) -> models.Store | None:
    res = await session.execute(
        select(models.Store).where(
            models.Store.account_id == account_id,
            models.Store.status == models.STORE_ACTIVE,
        )
    )
    stores = list(res.scalars().all())
    return stores[0] if len(stores) == 1 else None


async def resolve_target(
    session: AsyncSession,
    ctx: AuthContext,
    store_public_id: str | None,
    merchant_external_id: str | None = None,
) -> models.Store:
    """Resolve which store a request acts on.

    Resolution order (highest first):
      (a) merchant (external_id)  → the platform's own merchant identifier
      (b) store_public_id         → store lookup by public id
      (c) exactly one ACTIVE store for this account → implicit fallback
    """
    # -- (a) the caller's own merchant id --
    if merchant_external_id:
        res = await session.execute(
            select(models.Store).where(
                models.Store.external_id == merchant_external_id,
                models.Store.account_id == ctx.account.id,
            )
        )
        store = res.scalar_one_or_none()
        if store is None:
            raise HTTPException(status_code=404, detail="merchant_not_found")
        if store.status != models.STORE_ACTIVE:
            raise HTTPException(status_code=400, detail="merchant_store_disabled")
        return store

    # -- (b) explicit store public id --
    if store_public_id:
        return await svc.load_store_by_public(session, ctx.account.id, store_public_id)

    # -- (c) single active store fallback --
    single = await _single_active_store(session, ctx.account.id)
    if single is None:
        raise HTTPException(status_code=400, detail="store_required_or_merchant_required")
    return single


@router.post(
    "",
    response_model=schemas.PaymentOut,
    responses=QUOTA_ERROR,
)
async def create_payment(
    body: schemas.PaymentCreate,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    store = await resolve_target(session, ctx, body.store, body.merchant)
    mode = _ctx_mode(ctx)
    # A live offline QR with no confirmation source is a payment that can never
    # be marked paid: we would hand out a code, take no ABA session for it, and
    # have no credentials to match it against Bakong. Refuse rather than create
    # a row nobody can ever settle. Test-mode keys are exempt because W1 settles
    # them through the test-mode bypass, without any ledger.
    if (
        body.hosted_qr is False
        and mode == "live"
        and not _offline_qr_is_confirmable()
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "offline_qr_requires_a_confirmation_source: this deployment has no "
                "Bakong Open API credentials, so a QR we build ourselves could never "
                "be confirmed. Omit hosted_qr to let ABA issue the code."
            ),
        )
    await svc.check_plan_quota(session, ctx.account, mode)
    cents = int(Decimal(str(body.amount)) * 100)
    payment, created = await svc.create_payment(
        session,
        store=store,
        amount_cents=cents,
        reference_id=body.reference_id,
        metadata=body.metadata,
        idempotency_key=body.idempotency_key,
        hosted_qr=body.hosted_qr,
    )
    if created:
        await svc._enqueue_or_skip_test_mode_bypass(payment, mode)
        await session.commit()
        await session.refresh(payment)
        await _enqueue_detection(payment, mode, store.public_id)
    response.status_code = 201 if created else 200
    base = str(request.base_url).rstrip("/")
    return payment_out(
        payment,
        checkout_url=f"{base}/pay/{payment.public_id}",
        store_id=store.public_id,
        external_id=store.external_id,
    )


@router.post(
    "/{public_id}/reissue",
    response_model=schemas.PaymentOut,
    responses=merged(AUTH_ERRORS, CONFLICT_ERROR),
)
async def reissue_payment(
    public_id: str,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    """Mint a fresh QR for a payment whose code died, as a successor payment.

    ABA owns the QR's lifetime and no call extends it, so a customer who comes
    back after the window needs a new ABA session. This is merchant-only on
    purpose: each call spends a real ABA checkout, and the caller has to
    authenticate to spend one.
    """
    stmt = (
        select(models.Payment, models.Store)
        .join(models.Store, models.Store.id == models.Payment.store_id)
        .where(models.Payment.public_id == public_id)
        .where(models.Store.account_id == ctx.account.id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    parent, store = row

    successor, created = await svc.reissue_payment(session, parent)
    if created:
        mode = _ctx_mode(ctx)
        await svc._enqueue_or_skip_test_mode_bypass(successor, mode)
        # Only when a successor was actually minted: a replay returns the existing
        # code and changes nothing, so it is not an event worth attributing.
        audit.record(
            session,
            actor=ctx.account,
            action="payment.reissued",
            target_type="Payment",
            target_id=successor.id,
            details={
                "replaces": parent.public_id,
                "successor": successor.public_id,
                "store": store.public_id,
            },
        )
        await session.commit()
        await session.refresh(successor)
        await _enqueue_detection(successor, mode, store.public_id)
    # 200 on replay so a merchant hammering the button reuses the same live QR
    # instead of stacking ABA sessions.
    response.status_code = 201 if created else 200
    base = str(request.base_url).rstrip("/")
    return payment_out(
        successor,
        checkout_url=f"{base}/pay/{successor.public_id}",
        store_id=store.public_id,
        external_id=store.external_id,
        reissued_from=parent.public_id if created else None,
    )


@router.post(
    "/{public_id}/reverse",
    response_model=schemas.PaymentOut,
    responses=merged(AUTH_ERRORS, CONFLICT_ERROR),
)
async def reverse_payment(
    public_id: str,
    body: PaymentReverseIn,
    request: Request,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    """Record that a settled payment was refunded to the customer.

    ABA gives us no callback and its hosted status endpoint reports no reversal, so
    nothing in the platform can discover a refund on its own — the merchant has to
    say so. This exists because the alternative is worse than manual: a refunded
    payment stayed `paid` for the rest of its retention window, so every report
    built on it overstated revenue permanently with no way to correct it.

    Only a settled payment can be reversed. Reversing anything else would record a
    refund of money that never arrived.
    """
    stmt = (
        select(models.Payment, models.Store)
        .join(models.Store, models.Store.id == models.Payment.store_id)
        .where(models.Payment.public_id == public_id)
        .where(models.Store.account_id == ctx.account.id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    payment, store = row

    # Recorded into the same transaction the service commits, so a reversal can
    # never exist without the trail that says who made it.
    audit.record(
        session,
        actor=ctx.account,
        action="payment.reversed",
        target_type="Payment",
        target_id=payment.id,
        details={
            "store": store.public_id,
            "amount": money_to_str(payment.amount_cents),
            "reason": (body.reason or "").strip() or None,
        },
    )
    await svc.reverse_payment(session, payment, reason=body.reason)
    await session.refresh(payment)
    base = str(request.base_url).rstrip("/")
    return payment_out(
        payment,
        checkout_url=f"{base}/pay/{payment.public_id}",
        store_id=store.public_id,
        external_id=store.external_id,
    )


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    store: str | None = None,
    merchant: str | None = None,
    status: str | None = None,
    limit: int = 20,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    """List payments, newest first.

    Scoped by `store` or `merchant` when given. With neither, the whole account is
    listed across all of its stores — which is what the portal's payments page asks
    for and what this endpoint has always documented ("list the account's
    payments"). It previously fell back to the account's only active store, so any
    account running two or more stores got 400 `store_required_or_merchant_required`
    and an empty list.
    """
    if store or merchant:
        target = await resolve_target(session, ctx, store, merchant)
        payments = await svc.list_payments(
            session, target.id, status=status, limit=limit
        )
        rows = [(payment, target) for payment in payments]
    else:
        rows = await svc.list_payments_for_account(
            session, ctx.account.id, status=status, limit=limit
        )
    items = [
        schemas.PaymentListed(
            id=payment.public_id,
            status=payment.status,
            amount=money_to_str(payment.amount_cents),
            currency=payment.currency,
            reference_id=payment.reference_id,
            store=target_store.public_id,
            created_at=payment.created_at,
            expires_at=payment.expires_at,
            approved_at=payment.approved_at,
            paid_at=payment.paid_at,
        )
        for payment, target_store in rows
    ]
    return PaymentListResponse(data=items)


@router.get("/{public_id}", response_model=schemas.PaymentOut)
async def get_payment(
    public_id: str,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(models.Payment, models.Store.public_id, models.Store.external_id)
        .join(models.Store, models.Store.id == models.Payment.store_id)
        .where(models.Payment.public_id == public_id)
        .where(models.Store.account_id == ctx.account.id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    payment, store_id, store_external_id = row
    parent_public_id: str | None = None
    if payment.reissued_from_id is not None:
        parent = await session.get(models.Payment, payment.reissued_from_id)
        if parent is not None:
            parent_public_id = parent.public_id
    return payment_out(
        payment,
        checkout_url=None,
        store_id=store_id,
        external_id=store_external_id,
        reissued_from=parent_public_id,
    )
