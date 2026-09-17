"""Payment operations (create/list/status transitions/expiry)."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, observability, webhooks
from ..config import get_settings
from ..khqr import build_khqr_payload
from .payway_parser import PayWayHostedError, create_hosted_checkout

logger = logging.getLogger(__name__)


def gen_public_id(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_urlsafe(18)}"


def _now() -> datetime:
    return datetime.now(UTC)


def is_payway_link(link: models.PaymentLink) -> bool:
    """True when this link is served by ABA PayWay.

    For these links an offline-built QR is not merely worse — it is unusable.
    The wallet resolves the payee, finds no ABA transaction behind the code, and
    answers "QR not found". ABA has to issue the code, which is also what gives
    us a session we can poll afterwards.
    """
    return link.link_type == models.LINK_ABA_PAYWAY and "link.payway.com.kh" in (
        link.raw_link or ""
    )


async def load_store_by_public(
    session: AsyncSession, account_id: int, store_public_id: str
) -> models.Store:
    res = await session.execute(
        select(models.Store).where(
            models.Store.account_id == account_id,
            models.Store.public_id == store_public_id,
        )
    )
    store = res.scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="store_not_found")
    return store


async def load_active_link(session: AsyncSession, store_id: int) -> models.PaymentLink:
    res = await session.execute(
        select(models.PaymentLink).where(
            models.PaymentLink.store_id == store_id,
            models.PaymentLink.status == "active",
        )
    )
    link = res.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=400, detail="payment_link_disabled")
    return link


async def mint_qr(
    store: models.Store,
    link: models.PaymentLink,
    amount_cents: int,
    *,
    public_id: str,
    now: datetime,
    hosted_qr: bool | None = None,
) -> tuple[str, str | None, datetime, dict | None]:
    """Mint a payable QR and return (qr_string, qr_md5, expires_at, hosted_session).

    Shared by create and reissue so a regenerated code goes through exactly the
    path the original did — including ABA's own 180s window, which we must mirror
    or we would advertise a payment window the customer cannot use.
    """
    settings = get_settings()
    expires_at = now + timedelta(seconds=settings.checkout_ttl_seconds)
    hosted_session: dict | None = None
    # None means "decide from the link". An offline QR for a PayWay link is a
    # broken QR, so ABA issues it — a caller no longer has to know that. Explicit
    # false is how the offline builder is still exercised deliberately.
    use_hosted = is_payway_link(link) if hosted_qr is None else hosted_qr
    if use_hosted:
        if not is_payway_link(link):
            raise HTTPException(status_code=400, detail="not_a_payway_link")
        try:
            checkout = await create_hosted_checkout(link.raw_link, f"{amount_cents / 100:.2f}")
        except PayWayHostedError as exc:
            # Better a clear 502 than a QR nobody can pay.
            raise HTTPException(status_code=502, detail=f"payway_hosted_error: {exc}") from exc
        qr_string = checkout.qr_string
        qr_md5 = checkout.qr_md5
        # ABA's code is short-lived (180s observed) and ours must not outlive it,
        # or we would advertise a payment window the customer cannot use.
        expires_at = now + timedelta(
            seconds=checkout.expires_in_seconds or settings.checkout_ttl_seconds
        )
        hosted_session = {
            "client_id": checkout.client_id,
            "token": checkout.token,
            "request_time": checkout.request_time,
            "tran_id": checkout.tran_id,
            "expires_in_seconds": checkout.expires_in_seconds,
            # Kept so the exact string we handed out is recoverable for audit.
            "qr_string": checkout.qr_string,
        }
    else:
        qr_string = build_khqr_payload(
            store, link, amount_cents, bill_number=public_id, expires_at=expires_at
        )
        qr_md5 = hashlib.md5(qr_string.encode()).hexdigest()
    return qr_string, qr_md5, expires_at, hosted_session


async def create_payment(
    session: AsyncSession,
    *,
    store: models.Store,
    amount_cents: int,
    reference_id: str | None,
    metadata: dict | None,
    idempotency_key: str | None,
    hosted_qr: bool | None = None,
) -> tuple[models.Payment, bool]:
    if store.status == models.STORE_DISABLED:
        raise HTTPException(status_code=400, detail="store_disabled")
    link = await load_active_link(session, store.id)

    if idempotency_key:
        res = await session.execute(
            select(models.Payment).where(
                models.Payment.store_id == store.id,
                models.Payment.idempotency_key == idempotency_key,
            )
        )
        existing = res.scalar_one_or_none()
        if existing is not None:
            return existing, False

    if amount_cents < 1:
        raise HTTPException(status_code=400, detail="amount_too_low")
    if link.min_amount_cents and amount_cents < link.min_amount_cents:
        raise HTTPException(status_code=400, detail="amount_too_low")
    if link.max_amount_cents and amount_cents > link.max_amount_cents:
        raise HTTPException(status_code=400, detail="amount_too_high")

    public_id = gen_public_id()
    now = _now()

    qr_string, qr_md5, expires_at, hosted_session = await mint_qr(
        store,
        link,
        amount_cents,
        public_id=public_id,
        now=now,
        hosted_qr=hosted_qr,
    )

    payment = models.Payment(
        public_id=public_id,
        store_id=store.id,
        payment_link_id=link.id,
        amount_cents=amount_cents,
        currency=link.currency,
        reference_id=reference_id,
        metadata_=metadata,
        idempotency_key=idempotency_key,
        status=models.PAYMENT_PENDING,
        qr_string=qr_string,
        qr_md5=qr_md5,
        bill_number=public_id,
        expires_at=expires_at,
        gateway_status_raw={"payway_hosted": hosted_session} if hosted_session else None,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    observability.PAYMENT_EVENTS.labels(event="created").inc()
    return payment, True


async def reissue_payment(
    session: AsyncSession, parent: models.Payment
) -> tuple[models.Payment, bool]:
    """Mint a fresh QR for a dead payment by creating a successor row.

    ABA owns the 180s window and no call extends it, so the only way back to a
    payable code is a new ABA session — a new Payment row. The parent is left
    untouched as the record of the session that expired.

    Returns ``(successor, created)``. When the parent already has a live
    successor, that successor is returned with ``created=False`` instead of
    minting a second ABA session for the same sale.
    """
    if parent.status == models.PAYMENT_PAID:
        raise HTTPException(status_code=409, detail="payment_already_paid")
    if parent.status == models.PAYMENT_REVERSED:
        # The sale was refunded. A fresh code for it would invite the customer to pay
        # a second time for something they have already been reimbursed for.
        raise HTTPException(status_code=409, detail="payment_reversed")
    if parent.status not in (models.PAYMENT_EXPIRED, models.PAYMENT_FAILED):
        # Still legitimately payable — there is nothing to replace yet.
        raise HTTPException(status_code=409, detail="payment_not_expired")

    live = (
        await session.execute(
            select(models.Payment)
            .where(
                models.Payment.reissued_from_id == parent.id,
                models.Payment.status.in_(
                    [models.PAYMENT_PENDING, models.PAYMENT_SCANNED]
                ),
            )
            .order_by(models.Payment.id.desc())
        )
    ).scalars().first()
    if live is not None:
        # SQLite hands back naive datetimes for a tz-aware column, so normalize
        # before comparing or the guard only works on Postgres.
        live_expires = live.expires_at
        if live_expires.tzinfo is None:
            live_expires = live_expires.replace(tzinfo=UTC)
        if live_expires > _now():
            return live, False

    store = await session.get(models.Store, parent.store_id)
    if store is None or store.status == models.STORE_DISABLED:
        raise HTTPException(status_code=400, detail="store_disabled")
    link = await session.get(models.PaymentLink, parent.payment_link_id)
    if link is None:
        raise HTTPException(status_code=400, detail="payment_link_missing")

    now = _now()
    public_id = gen_public_id()
    # Same rail as the original: a replacement for an ABA-issued code has to be
    # ABA-issued too, or the wallet answers "QR not found" — and an offline
    # payment must stay offline rather than silently start spending ABA sessions.
    was_hosted = bool((parent.gateway_status_raw or {}).get("payway_hosted"))
    qr_string, qr_md5, expires_at, hosted_session = await mint_qr(
        store,
        link,
        parent.amount_cents,
        public_id=public_id,
        now=now,
        hosted_qr=was_hosted,
    )

    successor = models.Payment(
        public_id=public_id,
        store_id=parent.store_id,
        payment_link_id=parent.payment_link_id,
        amount_cents=parent.amount_cents,
        currency=parent.currency,
        reference_id=parent.reference_id,
        metadata_=parent.metadata_,
        # Deliberately not inherited: the key is unique per store, and a reissue
        # is a new rail session, not a replay of the one that expired.
        idempotency_key=None,
        status=models.PAYMENT_PENDING,
        qr_string=qr_string,
        qr_md5=qr_md5,
        bill_number=public_id,
        expires_at=expires_at,
        gateway_status_raw={"payway_hosted": hosted_session} if hosted_session else None,
        reissued_from_id=parent.id,
    )
    parent.attempt_history = [
        *(parent.attempt_history or []),
        {"type": "reissued", "successor": public_id, "at": now.isoformat()},
    ]
    session.add(successor)
    await session.commit()
    await session.refresh(successor)
    observability.PAYMENT_EVENTS.labels(event="reissued").inc()
    return successor, True


async def get_payment_for_store(
    session: AsyncSession, public_id: str, store_id: int | None = None
) -> models.Payment:
    stmt = select(models.Payment).where(models.Payment.public_id == public_id)
    if store_id is not None:
        stmt = stmt.where(models.Payment.store_id == store_id)
    res = await session.execute(stmt)
    payment = res.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    return payment


async def list_payments(
    session: AsyncSession,
    store_id: int,
    status: str | None = None,
    limit: int = 20,
) -> list[models.Payment]:
    stmt = (
        select(models.Payment)
        .where(models.Payment.store_id == store_id)
        .order_by(models.Payment.id.desc())
        .limit(max(1, min(limit, 100)))
    )
    if status:
        stmt = stmt.where(models.Payment.status == status)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def mark_scanned(session: AsyncSession, payment_id: int) -> None:
    res = await session.execute(
        update(models.Payment)
        .where(
            models.Payment.id == payment_id,
            models.Payment.status == models.PAYMENT_PENDING,
        )
        .values(status=models.PAYMENT_SCANNED, scanned_at=_now())
    )
    if res.rowcount == 1:
        await session.flush()
        payment = await session.get(models.Payment, payment_id)
        store = await session.get(models.Store, payment.store_id)
        await webhooks.enqueue_event(
            session, payment=payment, store=store, event_type=models.EVENT_SCANNED
        )
        await session.commit()


async def check_plan_quota(
    session: AsyncSession,
    account: models.Account,
    mode: str,
) -> None:
    """Enforce the monthly paid-transaction quota for the account's active plan.

    CutLuy-style: a successful (paid) payment counts as one transaction; the quota
    is monthly and shared across all stores on the account. Test-mode payments do
    not count. Returns 402 `quota_exceeded` when the plan limit is reached.
    """
    if mode != "live":
        return
    res = await session.execute(
        select(models.Plan.base_payments_included)
        .join(
            models.PlanSubscription,
            models.PlanSubscription.plan_id == models.Plan.id,
        )
        .where(
            models.PlanSubscription.account_id == account.id,
            models.PlanSubscription.status.in_(["trial", "active"]),
        )
    )
    included = res.scalar_one_or_none()
    if included is None:
        # No active plan yet — fall back to the Free tier default.
        included = 3000
    count = await _count_paid_payments_this_month(session, account.id)
    if count >= included:
        raise HTTPException(status_code=402, detail="quota_exceeded")


async def _count_paid_payments_this_month(
    session: AsyncSession, account_id: int
) -> int:
    month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    res = await session.execute(
        select(func.count(models.Payment.id))
        .join(models.Store, models.Store.id == models.Payment.store_id)
        .where(
            models.Store.account_id == account_id,
            models.Payment.status == models.PAYMENT_PAID,
            models.Payment.paid_at >= month_start,
        )
    )
    return res.scalar_one() or 0


async def _enqueue_or_skip_test_mode_bypass(
    payment: models.Payment,
    key_mode: str | None,
) -> None:
    if key_mode == "test":
        if payment.attempt_history is None:
            payment.attempt_history = []
        payment.attempt_history.append(
            {
                "type": "test_mode_bypass_hook",
                "api_key_mode": "test",
                "at": _now().isoformat(),
            }
        )


async def _record_plan_ledger_entry(
    session: AsyncSession,
    account_id: int,
    payment: models.Payment,
    *,
    reversal: bool = False,
) -> None:
    """Append this payment's own contribution to the month's usage ledger.

    One row per movement, carrying that movement's amount. Usage is
    `SUM(amount_cents_delta) GROUP BY period_month`.

    It used to be the opposite — a blank row per payment plus a period-wide `UPDATE`
    that added the amount to *every* row for the account-month. That inflated the
    totals quadratically (N movements left rows valued N, N-1, … 1, summing to
    N(N+1)/2 instead of N) and, worse, made a row mean two incompatible things at
    once: `resource_id` names one payment, while its counters described the whole
    period. The columns say "this movement", so the numbers do too. See P0-6 in
    docs/production-readiness.md.

    A reversal appends a **negative** row rather than editing the original. The
    ledger is a log; the history should show the credit and the give-back, and a
    reversal in a later month belongs to that later month. `resource_type` is what
    keeps the two apart, since the unique key is
    `(period_month, resource_type, resource_id)` and a reversal of a payment shares
    its `resource_id`.
    """
    amount = -payment.amount_cents if reversal else payment.amount_cents
    count = -1 if reversal else 1
    resource_type = "payment_reversal" if reversal else "payment"
    description = "payment reversed" if reversal else "payment settled"
    try:
        now_ts = _now()
        conn = await session.connection()
        await conn.execute(
            text(
                """
                INSERT INTO plan_ledger_entries
                    (account_id, period_month, resource_type, resource_id,
                     amount_cents_delta, total_payments_count, total_volume_cents,
                     description, created_at)
                VALUES
                    (:aid, :period, :rtype, :rid, :amount, :count, :amount,
                     :description, :now_ts)
                ON CONFLICT (period_month, resource_type, resource_id) DO NOTHING
                """
            ),
            {
                "aid": account_id,
                "period": now_ts.strftime("%Y-%m"),
                "rtype": resource_type,
                "rid": payment.public_id,
                "amount": amount,
                "count": count,
                "description": description,
                "now_ts": now_ts,
            },
        )
    except Exception as exc:
        # Never blocks a settlement: the ledger is reporting, not the source of truth
        # for whether money moved.
        logger.warning(
            "plan ledger entry failed for payment %s: %s", payment.public_id, exc
        )


async def _retire_successors(
    session: AsyncSession, payment: models.Payment, store: models.Store
) -> list[models.Payment]:
    """Retire any replacement code minted while this payment was still open.

    A merchant who sees a code lapse taps "generate new QR" and gets a second
    payable code for the same sale. If the customer then pays the *first* one —
    which is precisely what a late settlement is — both codes are live at once, and
    a customer who pays both has been charged twice for one sale. Observed as
    reachable on 2026-09-17: a payment settled nine minutes after its own code had
    been withdrawn.

    Retiring the successor the moment the parent settles closes that window. It
    cannot reach a code the customer already holds, so this is a mitigation rather
    than a guarantee — the duplicate check below is what catches the case where
    both were actually paid.
    """
    res = await session.execute(
        select(models.Payment).where(
            models.Payment.reissued_from_id == payment.id,
            models.Payment.status.in_(
                (models.PAYMENT_PENDING, models.PAYMENT_SCANNED, models.PAYMENT_EXPIRED)
            ),
        )
    )
    retired = list(res.scalars().all())
    for successor in retired:
        successor.status = models.PAYMENT_SUPERSEDED
        await webhooks.enqueue_event(
            session,
            payment=successor,
            store=store,
            event_type=models.EVENT_SUPERSEDED,
        )
    return retired


async def _find_double_charge(
    session: AsyncSession, payment: models.Payment
) -> models.Payment | None:
    """Another *settled* payment for what looks like the same sale."""
    if not payment.reference_id:
        # `reference_id` is the caller's own order identifier and the only basis we
        # have for saying two rows are one sale. Without it, matching on amount and
        # time would alert on coincidence — two customers genuinely buying the same
        # thing at the same price.
        return None
    res = await session.execute(
        select(models.Payment)
        .where(
            models.Payment.store_id == payment.store_id,
            models.Payment.reference_id == payment.reference_id,
            models.Payment.status == models.PAYMENT_PAID,
            models.Payment.id != payment.id,
        )
        .order_by(models.Payment.id)
    )
    return res.scalars().first()


async def _alert_if_double_charged(
    session: AsyncSession, payment: models.Payment, store: models.Store
) -> None:
    """Escalate a suspected double charge. Never lets its own failure affect settle."""
    try:
        other = await _find_double_charge(session, payment)
    except Exception as exc:  # noqa: BLE001 - a check must not break a settlement
        logger.warning("double-charge check failed for %s: %s", payment.public_id, exc)
        return
    if other is None:
        return

    observability.PAYMENT_EVENTS.labels(event="duplicate_paid").inc()
    logger.error(
        "DOUBLE CHARGE suspected: %s and %s are both paid for store %s (reference_id=%r)",
        other.public_id,
        payment.public_id,
        store.public_id,
        payment.reference_id,
    )
    # Imported here, not at module scope: `alerts` reaches back into `workers`,
    # which imports this package.
    from .. import alerts

    await alerts.alert_discrete(
        summary="double charge suspected",
        detail=(
            f"two settled payments share reference_id {payment.reference_id!r} on "
            f"store {store.public_id}: {other.public_id} and {payment.public_id}. "
            "One sale appears to have been charged twice — a refund is likely owed."
        ),
    )


async def mark_paid(
    session: AsyncSession,
    public_id: str,
    *,
    bakong_ref: str | None = None,
    gateway_raw: dict | None = None,
) -> models.Payment | None:
    """Confirm a credit from the rail. Idempotent once terminal."""
    res = await session.execute(
        select(models.Payment).where(models.Payment.public_id == public_id)
    )
    payment = res.scalar_one_or_none()
    # REVERSED is terminal here. The money arrived and was given back, and ABA's
    # session has no idea a refund happened — so a late poll still reporting
    # "approved" would otherwise resurrect a sale the merchant has already refunded.
    if payment is None or payment.status in (
        models.PAYMENT_PAID,
        models.PAYMENT_FAILED,
        models.PAYMENT_REVERSED,
    ):
        return None

    payment.status = models.PAYMENT_PAID
    payment.paid_at = _now()
    payment.approved_at = _now()
    payment.bakong_ref = bakong_ref
    payment.gateway_status_raw = gateway_raw

    store = await session.get(models.Store, payment.store_id)
    await _record_plan_ledger_entry(session, store.account_id, payment)
    # Before the completion event, so a consumer that reacts to `payment.completed`
    # by re-rendering the sale already sees the replacement code retired.
    await _retire_successors(session, payment, store)
    await webhooks.enqueue_event(
        session, payment=payment, store=store, event_type=models.EVENT_COMPLETED
    )
    await session.commit()
    observability.PAYMENT_EVENTS.labels(event="paid").inc()
    observability.observe_settlement(payment.created_at, payment.paid_at)
    # After the commit: the settlement is durable before anything tries to talk to
    # an operator about it.
    await _alert_if_double_charged(session, payment, store)
    return payment


async def reverse_payment(
    session: AsyncSession,
    payment: models.Payment,
    *,
    reason: str | None = None,
) -> models.Payment:
    """Record that a settled payment was refunded or reversed.

    ABA gives us no callback and the hosted status endpoint reports no reversal, so
    the platform cannot discover this and has to be told. Recording it is the only
    thing standing between us and revenue that is overstated permanently: without a
    reversal state a refunded payment reads `paid` for the rest of its retention
    window, and every report built on it is wrong.

    `paid_at` is kept, because the money did move, and `reversed_at` is added. Both
    travel to the merchant, so a report can say "collected, then given back" instead
    of pretending the sale never happened.
    """
    if payment.status == models.PAYMENT_REVERSED:
        raise HTTPException(status_code=409, detail="payment_already_reversed")
    if payment.status != models.PAYMENT_PAID:
        # Only settled money can be given back. Reversing anything else would
        # invent a credit that never existed.
        raise HTTPException(status_code=409, detail="payment_not_paid")

    payment.status = models.PAYMENT_REVERSED
    payment.reversed_at = _now()
    cleaned = (reason or "").strip()
    payment.reversal_reason = cleaned[:255] or None

    store = await session.get(models.Store, payment.store_id)
    # A negative row, not an edit of the original: the ledger is a log, so the history
    # shows both the credit and the give-back. This also settles the question P0-6 left
    # open — a refunded payment no longer counts toward the month's volume.
    await _record_plan_ledger_entry(session, store.account_id, payment, reversal=True)
    await webhooks.enqueue_event(
        session, payment=payment, store=store, event_type=models.EVENT_REVERSED
    )
    await session.commit()
    observability.PAYMENT_EVENTS.labels(event="reversed").inc()
    return payment


async def expire_due_payments(session: AsyncSession) -> list[models.Payment]:
    res = await session.execute(
        select(models.Payment)
        .where(
            models.Payment.status.in_([models.PAYMENT_PENDING, models.PAYMENT_SCANNED]),
            models.Payment.expires_at <= _now(),
        )
        .order_by(models.Payment.id)
        .limit(200)
    )
    due = list(res.scalars().all())
    for payment in due:
        payment.status = models.PAYMENT_EXPIRED
        store = await session.get(models.Store, payment.store_id)
        await webhooks.enqueue_event(
            session, payment=payment, store=store, event_type=models.EVENT_EXPIRED
        )
    if due:
        await session.commit()
        observability.PAYMENT_EVENTS.labels(event="expired").inc(len(due))
    return due
