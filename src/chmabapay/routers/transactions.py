"""Bakong transaction verification endpoints.

Reverse-engineered from https://api-bakong.nbc.gov.kh/ transaction search UI.
Supports all 5 lookup modes exposed on the NBC portal:
  - Hash (64-char full SHA-256)
  - Short Hash (truncated)
  - MD5 (of KHQR QR string)
  - Instruction Ref
  - External Ref

Plus admin helpers: token renewal, Bakong account existence check, and
payment-status reconciliation with the internal payments ledger.

Priority-order status policy (user requirement 2026-09-10 POINT #1):
  1. When the payment was created from an ABA PayWay link → check the ABA
     PayWay SSR page FIRST (#1 priority checkpoint — the user said "this link
     shows PAID after scan so we use it as point number one").
  2. On any other payment OR if the ABA page signal is still PENDING →
     fall back to the Bakong Open API (md5/short_hash cascading search).
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, schemas
from ..auth import AuthContext, get_current_auth_context
from ..config import get_settings
from ..db import get_session
from ..services.bakong import BakongApiClient, BakongApiError, get_bakong_client
from ..services.status_reconciler import (
    ReconcileResult,
    reconcile_payment,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/transactions", tags=["transactions"])


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _require_configured_token(client: BakongApiClient) -> None:
    if not client.token and not client.developer_email:
        raise HTTPException(
            status_code=503,
            detail="bakong_not_configured: set BAKONG_API_TOKEN or BAKONG_DEVELOPER_EMAIL in env",
        )


def _md5_of_qr(qr_string: str) -> str:
    """MD5 of a KHQR string, matching the key Bakong indexes paid QR codes by."""
    return hashlib.md5(qr_string.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Public: unified transaction search
# --------------------------------------------------------------------------- #
@router.post("/search", response_model=schemas.BakongTransactionOut)
async def search_transaction(
    body: schemas.TransactionSearchRequest,
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Look up a Bakong transaction status by any of the 5 identifier types.

    `short_hash` lookups are significantly more reliable when you also supply
    `amount` + `currency` — this is exactly how the official NBC portal uses
    them (discovered by reverse-engineering the UI on 2026-09-09).
    """
    _require_configured_token(client)
    try:
        tx = await client.search(
            body.search_type, body.value, amount=body.amount, currency=body.currency
        )
    except BakongApiError as e:
        raise HTTPException(status_code=400, detail=f"bakong_error: {e}") from e
    return schemas.BakongTransactionOut.from_service(tx)


# --------------------------------------------------------------------------- #
# Public: poll-until-paid (blocks until Success status or attempt budget)
# --------------------------------------------------------------------------- #
@router.post("/poll", response_model=schemas.BakongTransactionOut)
async def poll_until_paid(
    body: schemas.TransactionPollRequest,
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Long-poll Bakong until the transaction reaches Success status (or until timeout).

    Perfect for a frontend that needs to wait for a real user payment: call this
    endpoint once with short_hash+amount+currency and it returns the moment the
    transaction settles, or after `max_attempts` x `interval_seconds`.
    """
    _require_configured_token(client)
    try:
        tx = await client.poll_until_found(
            body.search_type,
            body.value,
            amount=body.amount,
            currency=body.currency,
            require_status=body.require_status,
            interval_seconds=body.interval_seconds,
            max_attempts=body.max_attempts,
        )
    except BakongApiError as e:
        raise HTTPException(status_code=400, detail=f"bakong_error: {e}") from e
    return schemas.BakongTransactionOut.from_service(tx)


# --------------------------------------------------------------------------- #
# Public: convenience routes per search type (mirrors the NBC website tabs)
# --------------------------------------------------------------------------- #
@router.get("/hash/{hash_value}", response_model=schemas.BakongTransactionOut)
async def get_by_hash(
    hash_value: str,
    amount: float | None = Query(default=None, gt=0, description="Optional amount filter"),
    currency: str | None = Query(default=None, description="Optional ISO currency, e.g. USD"),
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Lookup by 64-character full transaction hash (SHA-256)."""
    if len(hash_value) != 64:
        raise HTTPException(status_code=400, detail="hash_must_be_64_chars")
    _require_configured_token(client)
    try:
        tx = await client.check_by_hash(hash_value.lower(), amount=amount, currency=currency)
    except BakongApiError as e:
        raise HTTPException(status_code=400, detail=f"bakong_error: {e}") from e
    return schemas.BakongTransactionOut.from_service(tx)


@router.get("/md5/{md5_value}", response_model=schemas.BakongTransactionOut)
async def get_by_md5(
    md5_value: str,
    amount: float | None = Query(default=None, gt=0, description="Optional amount filter"),
    currency: str | None = Query(default=None, description="Optional ISO currency, e.g. USD"),
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Lookup by 32-character MD5 (of the KHQR QR string)."""
    if len(md5_value) != 32:
        raise HTTPException(status_code=400, detail="md5_must_be_32_chars")
    _require_configured_token(client)
    try:
        tx = await client.check_by_md5(md5_value.lower(), amount=amount, currency=currency)
    except BakongApiError as e:
        raise HTTPException(status_code=400, detail=f"bakong_error: {e}") from e
    return schemas.BakongTransactionOut.from_service(tx)


@router.get("/short-hash/{short_hash}", response_model=schemas.BakongTransactionOut)
async def get_by_short_hash(
    short_hash: str,
    amount: float | None = Query(default=None, gt=0, description="Highly recommended for reliable short hash lookups, e.g. 4.99"),
    currency: str | None = Query(default="USD", description="ISO currency, e.g. USD or KHR"),
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Lookup by truncated short hash. Always pair with `amount` + `currency`!"""
    if len(short_hash) != 8:
        raise HTTPException(
            status_code=400,
            detail="short_hash_must_be_8_chars: use /search endpoint or POST /poll for non-standard lengths",
        )
    _require_configured_token(client)
    try:
        tx = await client.check_by_short_hash(short_hash, amount=amount, currency=currency)
    except BakongApiError as e:
        raise HTTPException(status_code=400, detail=f"bakong_error: {e}") from e
    return schemas.BakongTransactionOut.from_service(tx)


@router.get("/instruction-ref/{ref}", response_model=schemas.BakongTransactionOut)
async def get_by_instruction_ref(
    ref: str,
    amount: float | None = Query(default=None, gt=0, description="Optional amount filter"),
    currency: str | None = Query(default=None, description="Optional ISO currency, e.g. USD"),
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Lookup by instruction reference ID."""
    _require_configured_token(client)
    try:
        tx = await client.check_by_instruction_ref(ref, amount=amount, currency=currency)
    except BakongApiError as e:
        raise HTTPException(status_code=400, detail=f"bakong_error: {e}") from e
    return schemas.BakongTransactionOut.from_service(tx)


@router.get("/external-ref/{ref}", response_model=schemas.BakongTransactionOut)
async def get_by_external_ref(
    ref: str,
    amount: float | None = Query(default=None, gt=0, description="Optional amount filter"),
    currency: str | None = Query(default=None, description="Optional ISO currency, e.g. USD"),
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Lookup by external merchant reference."""
    _require_configured_token(client)
    try:
        tx = await client.check_by_external_ref(ref, amount=amount, currency=currency)
    except BakongApiError as e:
        raise HTTPException(status_code=400, detail=f"bakong_error: {e}") from e
    return schemas.BakongTransactionOut.from_service(tx)


# --------------------------------------------------------------------------- #
# Public: bulk search
# --------------------------------------------------------------------------- #
@router.post("/bulk", response_model=schemas.BulkTransactionSearchResponse)
async def bulk_search(
    body: schemas.TransactionBulkSearchRequest,
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Bulk lookup: multiple identifiers of the same type (max 100)."""
    _require_configured_token(client)

    if body.search_type == "md5":
        raw = await client.check_by_md5_list(body.values)
    elif body.search_type == "hash":
        raw = await client.check_by_hash_list(body.values)
    else:
        raw: dict = {}
        for v in body.values:
            try:
                raw[v] = await client.search(body.search_type, v)
            except BakongApiError:
                raw[v] = None

    results = {k: schemas.BakongTransactionOut.from_service(v) for k, v in raw.items()}
    return schemas.BulkTransactionSearchResponse(results=results)


# --------------------------------------------------------------------------- #
# Public: cascading receipt verify
#   Paste whatever fields you have from a KHQR / ABA / ACLB / any bank receipt.
#   The service tries every identifier in priority order against the real Bakong
#   ledger until it finds the transaction — or exhausts them all.
# --------------------------------------------------------------------------- #
@router.post("/verify-receipt", response_model=schemas.ReceiptVerifyResponse)
async def verify_receipt(
    body: schemas.ReceiptVerifyRequest,
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Verify a payment using ANY fields you have from a receipt.

    Simply paste everything from the receipt (transaction hash #, purchase #,
    reference #, trx id, apv, amount, currency, etc.) and the service tries
    them in priority order (md5 first — the key Bakong indexes QR payments by)
    against the real Bakong ledger.
    """
    if not any(
        [
            body.short_hash,
            body.full_hash,
            body.md5,
            body.purchase_number,
            body.reference_number,
            body.transaction_id,
            body.apv_number,
        ]
    ):
        raise HTTPException(
            status_code=400,
            detail="at_least_one_identifier_required: provide short_hash / full_hash / md5 / purchase_number / reference_number / transaction_id / apv_number",
        )
    _require_configured_token(client)

    result = await client.verify_receipt(
        short_hash=body.short_hash,
        amount=body.amount,
        currency=body.currency,
        purchase_number=body.purchase_number,
        reference_number=body.reference_number,
        transaction_id=body.transaction_id,
        apv_number=body.apv_number,
        remark=body.remark,
        md5=body.md5,
        full_hash=body.full_hash,
        require_status=body.require_status,
    )
    return schemas.ReceiptVerifyResponse(
        found=result.found,
        via=result.via,
        attempts=[schemas.ReceiptAttemptOut(**a) for a in result.attempts],
        transaction=schemas.BakongTransactionOut.from_service(result.transaction),
    )


# --------------------------------------------------------------------------- #
# Public: token renewal
# --------------------------------------------------------------------------- #
@router.post("/token/renew", response_model=schemas.BakongTokenRenewResponse)
async def renew_token(
    body: schemas.BakongTokenRenewRequest | None = None,
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Request a fresh short-lived JWT from the NBC using a registered developer email."""
    email = body.email if body else None
    if not email and not client.developer_email:
        raise HTTPException(status_code=400, detail="email_required")
    try:
        token = await client.renew_token(email)
    except BakongApiError as e:
        raise HTTPException(status_code=400, detail=f"bakong_error: {e}") from e
    return schemas.BakongTokenRenewResponse(
        token=token,
        message="token_renewed" if token else "token_unavailable",
    )


# --------------------------------------------------------------------------- #
# Payment reconciliation: confirm our internal DB state against the Bakong
# ledger, which is the only authoritative status source.
# --------------------------------------------------------------------------- #
@router.post(
    "/verify-payment/{payment_public_id}",
    response_model=schemas.BakongTransactionOut,
)
async def verify_internal_payment(
    payment_public_id: str,
    use_hash: bool = Query(default=False, description="Prefer full hash lookup over MD5 (if you stored bakong_ref)."),
    prefer_aba_page: bool = Query(default=True, description="Also fetch the ABA PayWay link page for display. It is never used to decide the status: it is a static template with no per-transaction data."),
    aba_slug_hint: str | None = Query(default=None, description="Optional ABA PayWay link slug hint, e.g. ABAPAYpe518710Y. If omitted, we derive from payment metadata / link_type."),
    session: AsyncSession = Depends(get_session),
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
):
    """Re-verify a payment we created: ABA PayWay page FIRST (if applicable),
    then Bakong Open API cascading search as fallback. Optionally mark the
    internal payment PAID if any source confirms it.

    Priority order (user requirement 2026-09-10 POINT #1):
      1. ABA PayWay link page SSR status → if PAID, transition the row immediately.
      2. Bakong Open API (md5 → short_hash → instruction_ref → external_ref)
    """
    get_settings()
    stmt = (
        select(models.Payment, models.Store.public_id)
        .join(models.Store, models.Store.id == models.Payment.store_id)
        .where(models.Payment.public_id == payment_public_id)
        .where(models.Store.account_id == ctx.account.id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    payment, _store_id = row

    # If use_hash was passed and we have a raw bakong_ref we still route through
    # the reconciler — but we set aba_slug_hint to disable ABA checks, so the
    # reconciler will only try Bakong. (Preserves the pre-existing use_hash
    # semantics while also supporting the new priority-order path.)
    if use_hash:
        prefer_aba_page = False

    # A hosted ABA checkout answers for itself, so Bakong credentials are only
    # required for payments that have no hosted session to poll.
    _gw = payment.gateway_status_raw if isinstance(payment.gateway_status_raw, dict) else {}
    if not isinstance(_gw.get("payway_hosted"), dict):
        _require_configured_token(client)

    rec: ReconcileResult = await reconcile_payment(
        payment,
        bakong_client=client,
        session=session,
        aba_slug_hint=aba_slug_hint,
        prefer_aba=prefer_aba_page,
    )

    # Legacy return: return the BakongTx shape if we found one; otherwise synthesize
    tx = rec.bakong_tx
    if tx is None and rec.status == "PAID":
        # No Bakong tx object, but ABA page said PAID. Return a synthesized
        # BakongTransactionOut so callers still get status=Success.
        return schemas.BakongTransactionOut.from_service(
            schemas.BakongTransactionOut(  # type: ignore[arg-type]
                hash=payment.bakong_ref or rec.aba_status.matched_bill if rec.aba_status else None,
                short_hash=_md5_of_qr(payment.qr_string)[:16] if payment.qr_string else None,
                md5=_md5_of_qr(payment.qr_string) if payment.qr_string else None,
                from_account_id=None,
                to_account_id=None,
                from_account_name=None,
                to_account_name=None,
                currency=payment.currency or "USD",
                amount=rec.matched_amount or (payment.amount_cents / 100),
                description=None,
                created_date_ms=None,
                acknowledged_date_ms=None,
                instruction_ref=getattr(payment, "bill_number", None),
                external_ref=getattr(payment, "reference_id", None),
                status="Success" if rec.status == "PAID" else rec.status,
                raw=None,
            )
        )
    if tx is None:
        # Still no info: return a pending Bakong shape
        try:
            return schemas.BakongTransactionOut.from_service(None)
        except Exception:
            raise HTTPException(status_code=404, detail="tx_not_found_yet; signals=" + ";".join(rec.signals[-5:]))
    return schemas.BakongTransactionOut.from_service(tx)


@router.get(
    "/check-status/{payment_public_id}",
    response_model=schemas.PaymentStatusOut,
    summary="Payment status from the Bakong ledger (authoritative)",
)
async def check_payment_status(
    payment_public_id: str,
    prefer_aba_page: bool = Query(default=True),
    aba_slug_hint: str | None = Query(default=None),
    mark_paid: bool = Query(default=True, description="If status source reports PAID and we have a DB session, transition the internal Payment row to PAID."),
    session: AsyncSession = Depends(get_session),
    client: BakongApiClient = Depends(get_bakong_client),
    ctx: AuthContext = Depends(get_current_auth_context),
) -> schemas.PaymentStatusOut:
    """User-requested #1 priority status endpoint.

    Status comes from the Bakong ledger. The ABA PayWay link page is fetched for
    display only (its signals are returned in `aba_signals`) because it is a
    static template with no per-transaction data — it cannot report a payment.

    If Bakong credentials are missing, `error` explains it instead of silently
    reporting PENDING as though the QR had simply not been paid yet.
    """
    get_settings()
    stmt = (
        select(models.Payment, models.Store.public_id)
        .join(models.Store, models.Store.id == models.Payment.store_id)
        .where(models.Payment.public_id == payment_public_id)
        .where(models.Store.account_id == ctx.account.id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    payment, _store_id = row

    effective_session = session if mark_paid else None

    rec: ReconcileResult = await reconcile_payment(
        payment,
        bakong_client=client,
        session=effective_session,
        aba_slug_hint=aba_slug_hint,
        prefer_aba=prefer_aba_page,
    )
    aba_sigs = rec.aba_status.signals if rec.aba_status else []
    return schemas.PaymentStatusOut(
        payment_public_id=payment.public_id,
        status=rec.status,
        source=rec.source,
        matched_amount=rec.matched_amount,
        transitioned_to_paid=rec.transitioned_to_paid,
        signals=rec.signals,
        aba_signals=aba_sigs,
        bakong_via=rec.bakong_result.via if rec.bakong_result else None,
        bakong_tx_status=rec.bakong_tx.status if rec.bakong_tx else None,
        bakong_tx=schemas.BakongTransactionOut.from_service(rec.bakong_tx) if rec.bakong_tx else None,
        error=rec.error,
    )


# --------------------------------------------------------------------------- #
# Legacy path: single Bakong lookup by a specific key. Kept because it was the
# old verify_internal_payment before priority-order status was introduced.
# --------------------------------------------------------------------------- #
