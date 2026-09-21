"""Worker runtime: the registry, the transport, and the supervisor that starts them.

Extracted from `main.py` so that both topologies run identical code:

* **Workers inside the API** (default). `WORKERS_ENABLED=true`, transport
  `inprocess` or `redis`. The API enqueues, drains, and watches.
* **Workers as their own process** (P2-3). `python -m chmabapay.worker_main`, with
  `WORKER_TRANSPORT=redis` and `WORKERS_ENABLED=false` on the API. Same
  `Worker.process`, same drain loop, same supervising tasks — only the process
  boundary moves.

**The alert watcher starts here, with the drains.** It reads heartbeats, which are
per-process monotonic timestamps, so it is only meaningful in a process that owns
drain loops. Starting it beside the API while the workers lived elsewhere would make
every queue look like it had never started and page continuously — the opposite of
what it is for.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings, get_settings
from .base import QueueTransport, Worker
from .inprocess import InProcessTransport
from .w1_payment_detection import QUEUE as Q_DETECTION
from .w1_payment_detection import PaymentDetectionWorker
from .w2_webhook_sender import QUEUE as Q_WEBHOOK
from .w2_webhook_sender import WebhookSenderWorker
from .w3_billing import QUEUE as Q_BILLING
from .w3_billing import (
    BillingInvoiceWorker,
    billing_heartbeat_dedup_key,
    billing_heartbeat_job_payload,
)
from .w4_expiry_sweeper import QUEUE as Q_EXPIRY
from .w4_expiry_sweeper import (
    ExpirySweeperWorker,
    expiry_heartbeat_dedup_key,
    expiry_heartbeat_job_payload,
)
from .w5_retention_sweeper import QUEUE as Q_RETENTION
from .w5_retention_sweeper import (
    RetentionSweeperWorker,
    retention_heartbeat_dedup_key,
    retention_heartbeat_job_payload,
)

log = logging.getLogger(__name__)


def worker_registry() -> dict[str, Worker]:
    """One Worker per queue. The list is also the set of queues that must exist."""
    return {
        Q_DETECTION: PaymentDetectionWorker(),
        Q_WEBHOOK: WebhookSenderWorker(),
        Q_BILLING: BillingInvoiceWorker(),
        Q_EXPIRY: ExpirySweeperWorker(),
        Q_RETENTION: RetentionSweeperWorker(),
    }


def build_transport(settings: Settings | None = None) -> QueueTransport:
    """The transport this process should use, by `WORKER_TRANSPORT`.

    An unknown value is refused rather than silently treated as in-process: a typo
    in an environment variable would otherwise give every process its own private
    queue, which looks like a working platform until the second replica is added and
    the work is silently split in half.
    """
    settings = settings or get_settings()
    choice = (settings.worker_transport or "inprocess").strip().lower()

    if choice == "inprocess":
        return InProcessTransport()

    if choice == "redis":
        # Imported here so the redis client is only required by deployments that
        # actually use it.
        from .redis import RedisTransport

        return RedisTransport(
            settings.redis_url,
            queues=list(worker_registry()),
            dedup_ttl_sec=settings.queue_dedup_ttl_seconds,
            claim_idle_seconds=settings.queue_claim_idle_seconds,
        )

    raise ValueError(
        f"WORKER_TRANSPORT={choice!r} is not a known transport; expected 'inprocess' or 'redis'"
    )


def start_workers(transport: QueueTransport, stop: asyncio.Event) -> list[asyncio.Task]:
    """Start the drain loops, the heartbeat scheduler and the alert watcher.

    Returns the tasks rather than awaiting them, so the caller owns shutdown: the
    API adds them to its lifespan task list, the worker process cancels them when it
    receives a signal. Both then get the same graceful-drain path.
    """
    # Imported inside the function: `alerts` imports from this package (`Q_WEBHOOK`),
    # so a module-level import here would be a cycle.
    from ..alerts import AlertWatcher

    workers = worker_registry()
    tasks = [
        asyncio.create_task(
            transport.run_workers(workers, stop), name="queue-dispatcher"
        ),
        asyncio.create_task(
            _recurring_heartbeats(transport, stop), name="recurring-heartbeats"
        ),
    ]

    # Watches the queues above. The expected queue list is passed in rather than
    # discovered from whatever is reporting, so a drain loop that never started is
    # an alert instead of an absence.
    watcher = AlertWatcher.from_settings(transport=transport, queues=list(workers))
    tasks.append(asyncio.create_task(watcher.watch(stop), name="alert-watcher"))
    return tasks


# -------------------------------------------------------------------------------- #
# Recurring heartbeats — event loop time-scheduled callbacks ONLY (no `while` loop):
#   - W2 Webhook fanout fallback scan (1s) if enqueue-on-write missed
#   - W4 Expiry sweep minute-granularity job (60s) dedup per minute
#   - W1 Payment detection safety-net scan for orphans pending > 30s
#   - W3 Billing sweep hourly job, dedup per hour (invoice issuance is idempotent)
#
# NFR-2 compliance: this scheduler uses loop.call_at rescheduling, ZERO while
# loops anywhere except QueueTransport.run_workers in workers/base.py (count=1).
# -------------------------------------------------------------------------------- #
async def _recurring_heartbeats(transport: QueueTransport, stop: asyncio.Event) -> None:
    settings = get_settings()
    loop = asyncio.get_event_loop()

    async def w4_tick() -> None:
        from .w4_expiry_sweeper import _now as _w4now

        dedup_key = expiry_heartbeat_dedup_key(_w4now())
        try:
            await transport.enqueue(
                Q_EXPIRY,
                dedup_key=dedup_key,
                payload=expiry_heartbeat_job_payload(),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("W4 heartbeat enqueue skip: %s", exc)

    async def w5_tick() -> None:
        from .w5_retention_sweeper import _now as _w5now

        dedup_key = retention_heartbeat_dedup_key(_w5now())
        try:
            await transport.enqueue(
                Q_RETENTION,
                dedup_key=dedup_key,
                payload=retention_heartbeat_job_payload(),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("W5 heartbeat enqueue skip: %s", exc)

    async def w3_tick() -> None:
        from ..services.billing import utcnow

        dedup_key = billing_heartbeat_dedup_key(utcnow())
        try:
            await transport.enqueue(
                Q_BILLING,
                dedup_key=dedup_key,
                payload=billing_heartbeat_job_payload(),
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("W3 heartbeat enqueue skip: %s", exc)

    async def w2_fallback_fanout(batch: int = 20) -> None:
        try:
            await transport.enqueue(
                Q_WEBHOOK,
                dedup_key=None,
                payload={"type": "fanout_scan", "batch_size": batch},
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("W2 fallback enqueue skip: %s", exc)

    async def w1_orphan_scan() -> None:
        try:
            await transport.enqueue(
                Q_DETECTION,
                dedup_key=None,
                payload={"type": "orphan_scan_pending", "older_than_seconds": 30},
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("W1 orphan enqueue skip: %s", exc)

    # Intervals
    w4_interval = 60.0
    w2_interval = max(0.5, float(settings.webhook_poll_interval_seconds or 1.0))
    w1_interval = max(5.0, float(settings.worker_w1_fallback_poll_seconds or 30.0))
    w5_interval = max(60.0, float(settings.retention_sweep_interval_seconds or 86400.0))
    w3_interval = max(60.0, float(settings.billing_sweep_interval_seconds or 3600.0))

    # Fire once immediately for warm-up.
    #
    # W5 is here even though its interval is a day: a retention job that only ever
    # ran a day after boot would never run at all on a service that restarts more
    # often than that, and "the policy is enforced" would quietly become false.
    # Re-running it is free — the second sweep of a day finds nothing left to
    # purge — so boot is the safe place to guarantee it happens.
    await w4_tick()
    await w5_tick()
    await w2_fallback_fanout()
    # W3 too, and for the same reason W5 is here: a monthly job on a service that
    # restarts more often than monthly would otherwise never run. Issuing is
    # idempotent per (account, period), so a boot that finds nothing due costs one
    # indexed query.
    await w3_tick()

    # Recursive callback chains — stop flag checked each reschedule.
    def _schedule_w4() -> None:
        if stop.is_set():
            return

        async def _do() -> None:
            if stop.is_set():
                return
            await w4_tick()
            loop.call_later(w4_interval, _schedule_w4)

        loop.create_task(_do())

    def _schedule_w2() -> None:
        if stop.is_set():
            return

        async def _do() -> None:
            if stop.is_set():
                return
            await w2_fallback_fanout()
            loop.call_later(w2_interval, _schedule_w2)

        loop.create_task(_do())

    def _schedule_w1() -> None:
        if stop.is_set():
            return

        async def _do() -> None:
            if stop.is_set():
                return
            await w1_orphan_scan()
            loop.call_later(w1_interval, _schedule_w1)

        loop.create_task(_do())

    def _schedule_w5() -> None:
        if stop.is_set():
            return

        async def _do() -> None:
            if stop.is_set():
                return
            await w5_tick()
            loop.call_later(w5_interval, _schedule_w5)

        loop.create_task(_do())

    def _schedule_w3() -> None:
        if stop.is_set():
            return

        async def _do() -> None:
            if stop.is_set():
                return
            await w3_tick()
            loop.call_later(w3_interval, _schedule_w3)

        loop.create_task(_do())

    # Prime the first reschedules.
    loop.call_later(w4_interval, _schedule_w4)
    loop.call_later(w2_interval, _schedule_w2)
    loop.call_later(w1_interval, _schedule_w1)
    loop.call_later(w5_interval, _schedule_w5)
    loop.call_later(w3_interval, _schedule_w3)

    # Block on stop.wait() until shutdown. This is a simple .wait(), no while loop.
    await stop.wait()
