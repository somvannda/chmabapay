"""W1 PaymentDetectionWorker — reconcile pending payments against ABA/Bakong ledgers.

EDD Milestone 1.4. Wraps the old `bakong_verify_loop` into Worker.process(Job).
- Semaphore concurrency 20 (NFR-6).
- SSR TTLCache 30s / 2000 keys for ABA SSR fetch (NFR-5).
- Test-mode bypass AC-13: if job.payload.api_key_mode == 'test' → 5s asyncio.sleep → mark_paid directly.
- Every attempt appends dict to Payment.attempt_history JSON list (AC-14).
"""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import UTC, datetime
from typing import Any

from cachetools import TTLCache

from .. import models
from ..config import get_settings
from ..db import session_factory
from ..services.bakong import get_bakong_client
from ..services.status_reconciler import reconcile_payment
from .base import Worker
from .job import Job

log = logging.getLogger(__name__)

QUEUE = "payments.detection"

_ABA_SSR_CACHE: TTLCache = TTLCache(maxsize=2000, ttl=30)

# How long we keep reconciling an unsettled payment is `detection_window_seconds`
# in settings, not a constant here — see the comment there for why the bound has to
# outlast the rail rather than be tuned to it.
#
# Per-sweep cap. An ABA status call takes seconds, so a large burst here both
# starves the queue that carries the create-time polls — which are the ones the
# 180s QR window actually depends on — and keeps polling settled rows for the
# rest of the lookback. The sweep repeats every 30s, so a backlog still drains.
_ORPHAN_BATCH = 20

# Per-sweep cap for the one last look each payment gets as it leaves the window.
# Separate from `_ORPHAN_BATCH` because these are the payments we are about to stop
# watching, and a payment that leaves the window unexamined is the failure this
# whole mechanism exists to prevent.
_FINAL_BATCH = 20


def _now() -> datetime:
    return datetime.now(UTC)


def _has_hosted_session(payment: models.Payment) -> bool:
    """True when ABA minted this payment's QR and can be asked about it."""
    gw = payment.gateway_status_raw
    return isinstance(gw, dict) and isinstance(gw.get("payway_hosted"), dict)


class PaymentDetectionWorker(Worker):
    prefetch = 10
    concurrency = 20
    queue_name = QUEUE

    def __init__(self, concurrency: int | None = None) -> None:
        super().__init__()
        settings = get_settings()
        self._concurrency = concurrency or getattr(settings, "worker_w1_concurrency", 20) or 20
        self._sem = asyncio.Semaphore(self._concurrency)

    async def process(self, job: Job) -> dict[str, Any]:
        async with self._sem:
            return await self._process_one(job)

    async def _process_one(self, job: Job) -> dict[str, Any]:
        settings = get_settings()
        if job.payload.get("type") == "orphan_scan_pending":
            return await self._orphan_scan(job)

        public_id: str | None = job.payload.get("payment_public_id")
        key_mode: str | None = job.payload.get("api_key_mode") or job.payload.get("key_mode")
        if not public_id:
            raise ValueError("missing payment_public_id in job payload")

        # ————————————— AC-13: test-mode 5s bypass (no Bakong network call) ———————
        if key_mode == "test":
            return await self._test_mode_bypass(public_id, job)

        # ————————————— Real reconciliation path against live ledgers ———————
        async with session_factory() as session:
            from sqlalchemy import select

            stmt = select(models.Payment).where(models.Payment.public_id == public_id)
            res = await session.execute(stmt)
            payment = res.scalar_one_or_none()
            if payment is None:
                return {"skipped": True, "reason": "payment_not_found", "public_id": public_id}
            # REVERSED is terminal: the money arrived and was given back, and ABA's
            # session knows nothing about the refund, so polling it would only ever
            # report "approved" for a sale the merchant has already reversed.
            # SUPERSEDED is deliberately absent — its code is still live at ABA.
            if payment.status in (
                models.PAYMENT_PAID,
                models.PAYMENT_FAILED,
                models.PAYMENT_REVERSED,
            ):
                return {"skipped": True, "reason": f"already_{payment.status}"}

            # A hosted ABA checkout answers for itself, so Bakong credentials are
            # only needed for payments with no hosted session to poll. Testing
            # them before loading the row (as this did) made W1 refuse every job
            # once Bakong registration became unavailable — including the
            # ABA-hosted payments it can confirm perfectly well.
            if not _has_hosted_session(payment) and not (
                settings.bakong_api_token or settings.bakong_developer_email
            ):
                log.warning("W1 skipped for %s — Bakong API credentials missing", public_id)
                raise RuntimeError(
                    "bakong credentials missing; cannot reconcile in live mode. "
                    "Use ApiKey.mode='test' for local development.",
                )

            attempt: dict[str, Any] = {
                "source": "w1_detection_start",
                "attempt_at": _now().isoformat(),
                "matched_amount_cents": None,
                "signals": [],
                "note": "",
            }
            bakong_client = get_bakong_client()
            try:
                # Monkeypatch SSR fetch to use TTLCache for ABA PayWay pages (NFR-5)
                from ..services import payway_parser as _payway_mod

                wrapped = _payway_mod.fetch_payment_status
                if getattr(wrapped, "__wrapped__", None) is None:
                    import functools

                    @functools.wraps(wrapped)
                    async def _cached_fetch(*args, **kwargs):  # type: ignore[no-untyped-def]
                        cache_key = (args, tuple(sorted(kwargs.items())))
                        if cache_key in _ABA_SSR_CACHE:
                            return copy.deepcopy(_ABA_SSR_CACHE[cache_key])
                        result = await wrapped(*args, **kwargs)
                        _ABA_SSR_CACHE[cache_key] = copy.deepcopy(result)
                        return result

                    _cached_fetch.__wrapped__ = wrapped  # type: ignore[attr-defined]
                    _payway_mod.fetch_payment_status = _cached_fetch  # type: ignore[assignment]

                reconcile = await reconcile_payment(
                    payment,
                    bakong_client=bakong_client,
                    session=session,
                )
                attempt["signals"] = list(reconcile.signals)
                attempt["source"] = (
                    reconcile.source or "bakong_open_api"
                )
                attempt["matched_amount_cents"] = (
                    int(round(reconcile.matched_amount * 100))
                    if reconcile.matched_amount is not None
                    else None
                )
                attempt["note"] = (
                    reconcile.error[:120] if reconcile.error else reconcile.status
                )
                attempt["transitioned"] = bool(reconcile.transitioned_to_paid)
                # The last look a payment ever gets. Handled here rather than in the
                # sweep so the outcome is recorded in the same transaction as the
                # attempt that produced it.
                outcome_alert: tuple[str, str] | None = None
                if job.payload.get("final"):
                    outcome_alert = self._close_detection_window(payment, reconcile, attempt)
                result = {
                    "ok": True,
                    "final_status": reconcile.status,
                    "transitioned_to_paid": reconcile.transitioned_to_paid,
                    "payment_id": payment.id,
                    "public_id": public_id,
                    "attempt_saved": True,
                    "final": bool(job.payload.get("final")),
                }
            finally:
                # Always append attempt record to attempt_history JSON (AC-14)
                await self._append_attempt(session, payment.id, attempt)
                await session.commit()

            # Only once the closure is durable. Alerting before the commit would
            # report a state that a failed transaction could roll back.
            if outcome_alert is not None:
                from ..alerts import alert_discrete

                await alert_discrete(*outcome_alert)
            return result

    @staticmethod
    def _close_detection_window(
        payment: models.Payment,
        reconcile: Any,
        attempt: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Record that this payment is no longer being watched, and on what basis.

        Three outcomes, and only one of them warrants waking a human:

          settled        — the caller promotes it; nothing to escalate.
          answered, unpaid — ABA told us it is not paid. Closed quietly. Most
                           payments that never settle end here and that is correct.
          no answer      — ABA could not be reached, or would not answer for the
                           session. We are about to stop looking *without knowing*,
                           which is the one outcome that can hide money: the
                           customer's bank app may show a debit that our ledger never
                           sees, and the merchant has no way to discover that either.
        """
        marked = _now()
        payment.detection_closed_at = marked
        attempt["final"] = True
        attempt["detection_closed_at"] = marked.isoformat()

        if reconcile.status == "PAID":
            attempt["note"] = "closed: settled"
            return None
        if reconcile.error:
            attempt["note"] = f"closed unresolved: {reconcile.error[:160]}"
            return (
                "payment outcome unknown",
                (
                    f"{payment.public_id} left the detection window without a usable "
                    f"answer from the rail ({reconcile.error[:200]}). The customer may "
                    "have paid: check the merchant's ABA account before treating this "
                    "sale as lost."
                ),
            )
        attempt["note"] = "closed: rail answered, not paid"
        return None

    async def _orphan_scan(self, job: Job) -> dict[str, Any]:
        """Re-poll recent payments that nothing else is watching.

        The create-time job fires while the payment is still PENDING — the
        customer has not scanned yet — so on its own it can never observe a
        payment. Without this sweep a QR that does get paid stays PENDING until a
        human refreshes the dashboard, and the merchant's webhook never fires.
        main.py has enqueued this heartbeat since M1; it went unanswered because
        W1 only looked for payment_public_id and raised ValueError otherwise.

        EXPIRED rows are included on purpose. A hosted ABA QR lives 180s while
        W4 sweeps every 60s, so a payment made near the deadline can be marked
        expired before the confirming poll lands. ABA saying "paid" is proof the
        money moved, and mark_paid accepts the promotion — dropping those rows
        would silently discard a real payment.

        The window is `detection_window_seconds`, and it is deliberately longer
        than the rail's own willingness to accept: a real payment was observed
        settling 9.5 minutes after its code had been withdrawn, and ABA gave no
        indication that was the limit.
        """
        from datetime import timedelta

        from sqlalchemy import select

        from . import get_global_transport

        window = timedelta(seconds=get_settings().detection_window_seconds)
        cutoff = _now() - window
        async with session_factory() as session:
            res = await session.execute(
                select(models.Payment.public_id)
                .where(
                    models.Payment.status.in_(
                        (
                            models.PAYMENT_PENDING,
                            models.PAYMENT_SCANNED,
                            models.PAYMENT_EXPIRED,
                            # A retired replacement code is still live at ABA, and the
                            # customer may be holding it. If it settles, that is a
                            # second charge for one sale and the duplicate alert has to
                            # be able to see it — so it stays under watch.
                            models.PAYMENT_SUPERSEDED,
                        )
                    ),
                    models.Payment.created_at >= cutoff,
                )
                # Oldest first: those are the ones closest to being expired out
                # from under a payment that may already have settled.
                .order_by(models.Payment.id.asc())
                .limit(_ORPHAN_BATCH)
            )
            public_ids = list(res.scalars().all())

        transport = get_global_transport()
        if transport is None:
            return {"ok": True, "swept": len(public_ids), "enqueued": 0, "final": 0}

        enqueued = 0
        for public_id in public_ids:
            try:
                await transport.enqueue(
                    QUEUE,
                    payload={"payment_public_id": public_id},
                    # No dedup_key: the create-time job holds `detect:{id}` in the
                    # dedup cache for an hour, so reusing that key would make every
                    # sweep after the first a silent no-op.
                    dedup_key=None,
                )
                enqueued += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the sweep
                log.debug("W1 orphan enqueue skip for %s: %s", public_id, exc)
        # Then the payments leaving the window — see `_close_crossed_window`.
        final = await self._close_crossed_window(transport, window)
        _ = job
        return {
            "ok": True,
            "swept": len(public_ids),
            "enqueued": enqueued,
            "final": final,
        }

    async def _close_crossed_window(self, transport, window) -> int:
        """Give every payment that has just left the window one last look.

        The window has to end — polling a payment forever is not a plan — so the
        engineering is not in the boundary but in what we know when we reach it.
        Each crossing payment gets a single `final` job whose only difference from a
        normal poll is that it records the moment we stopped looking, and raises an
        operator alert when it cannot get an answer. See
        `_close_detection_window` for why only that third outcome is escalated.

        Deliberately bounded to rows that crossed during the last window-length of
        operation. Older rows were abandoned under the previous fixed-900s policy and
        are left alone: re-polling months of history would be a burst of live ABA
        calls to learn something no merchant is waiting on.
        """
        from sqlalchemy import select

        now = _now()
        async with session_factory() as session:
            res = await session.execute(
                select(models.Payment.public_id)
                .where(
                    models.Payment.status.in_(
                        (
                            models.PAYMENT_PENDING,
                            models.PAYMENT_SCANNED,
                            models.PAYMENT_EXPIRED,
                            models.PAYMENT_SUPERSEDED,
                        )
                    ),
                    # `detection_closed_at IS NULL` is the gate, not a dedup key: the
                    # job clears it when it actually runs, so a job lost to a restart
                    # is retried by the next sweep instead of being suppressed.
                    models.Payment.detection_closed_at.is_(None),
                    models.Payment.created_at < now - window,
                    models.Payment.created_at >= now - window - window,
                )
                .order_by(models.Payment.id.asc())
                .limit(_FINAL_BATCH)
            )
            public_ids = list(res.scalars().all())

        enqueued = 0
        for public_id in public_ids:
            try:
                await transport.enqueue(
                    QUEUE,
                    payload={"payment_public_id": public_id, "final": True},
                    dedup_key=None,
                )
                enqueued += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the sweep
                log.debug("W1 final-check enqueue skip for %s: %s", public_id, exc)
        if enqueued:
            log.info("W1: %d payment(s) left the detection window — final check queued", enqueued)
        return enqueued

    async def _test_mode_bypass(self, public_id: str, job: Job) -> dict[str, Any]:
        """AC-13: asyncio.sleep(5), call mark_paid directly — zero network calls."""
        await asyncio.sleep(5.0)
        async with session_factory() as session:
            from sqlalchemy import select

            stmt = select(models.Payment).where(models.Payment.public_id == public_id)
            res = await session.execute(stmt)
            payment = res.scalar_one_or_none()
            if payment is None:
                return {"test_mode": True, "error": "payment_not_found"}
            attempt = {
                "source": "test-mode-fake-delay",
                "attempt_at": _now().isoformat(),
                "matched_amount_cents": payment.amount_cents,
                "signals": ["mock_delay_5s", "ttl_bypassed"],
                "note": "dev test mode; no Bakong/ABA network call made",
            }
            if payment.status in (models.PAYMENT_PENDING, models.PAYMENT_SCANNED):
                from ..services.payments import mark_paid as _mark_paid

                await _mark_paid(
                    session,
                    public_id,
                    bakong_ref=f"test:{public_id}",
                    gateway_raw={
                        "source": "test-mode-fake-delay",
                        "slept_for_seconds": 5,
                        "job_id": str(job.job_id),
                    },
                )
            await self._append_attempt(session, payment.id, attempt)
            await session.commit()
            return {"test_mode": True, "status": payment.status, "public_id": public_id}

    @staticmethod
    async def _append_attempt(
        session,
        payment_id: int,
        attempt: dict[str, Any],
    ) -> None:
        from sqlalchemy import select

        res = await session.execute(
            select(models.Payment).where(models.Payment.id == payment_id),
        )
        payment = res.scalar_one_or_none()
        if payment is None:
            return
        existing = payment.attempt_history
        if not isinstance(existing, list):
            existing = []
        # A *new* list, never `existing.append(...)` and then assigning `existing`
        # back. `attempt_history` is a plain JSON column, so the only thing that
        # makes SQLAlchemy write it is the attribute's value changing — and
        # re-assigning the object that is already stored reads as "unchanged", so
        # the UPDATE is dropped at flush.
        #
        # The failure was silent and one-sided: the first attempt persisted
        # (`None -> [entry]` is a real change), which made the field look like it
        # worked, while every attempt after it vanished. Observed on the live rail
        # — three payments, each polled across the whole of ABA's ~180s window,
        # all three showing exactly one attempt.
        payment.attempt_history = [*existing, attempt]
        session.add(payment)
