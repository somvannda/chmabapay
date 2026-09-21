"""The alerts that reach a person (P1-3).

A metric nobody looks at is not an alert. These are the two conditions the
readiness plan names — a worker loop that has stopped, and a webhook queue that is
backing up — pushed to an operator over the Telegram channel built in P0-5.

Two deliberate properties:

* **Heartbeats are checked against the queues that are supposed to exist**, not
  against the ones that happen to be reporting. A drain loop that never started is
  the most urgent case of all, and it is invisible to a check that only looks at
  queues it has already heard from.
* **Delivery admits its own limits.** With no `OPS_TELEGRAM_CHAT_ID` there is no
  page, so the watcher says so at startup instead of leaving a false impression of
  coverage.

Each condition is reported on its edge — once when it starts firing, once when it
clears — so a worker that stays down for an hour sends one message, not one a
minute.

Three conditions are evaluated. The first two read the queue transport. The third —
how many webhook deliveries are failing — cannot: the sender is a sweep that scans
`event_deliveries` for due rows and reports `ok` whatever an individual POST did, so
the queue counters never see a delivery fail. It reads the database instead, through
a callable the watcher is given rather than imports, because this module is
otherwise testable without a database.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from . import models
from .config import get_settings
from .db import session_factory
from .observability import ALERTS_RAISED
from .services import telegram
from .workers import Q_WEBHOOK

log = logging.getLogger(__name__)

# (failed, total) deliveries over the window. Callable so the watcher does not have to
# open a database session to be evaluated.
DeliveryOutcomes = Callable[[float], Awaitable[tuple[int, int]]]


async def webhook_delivery_outcomes(window_seconds: float) -> tuple[int, int]:
    """Failed and total webhook deliveries touched in the last `window_seconds`.

    Counts *attempts*, not deliveries: a row updated inside the window was tried
    inside the window, which is what "10 % are failing right now" means. A delivery
    that has been retrying for an hour appears once per attempt it made, which is
    the honest denominator.
    """
    since = datetime.now(UTC) - timedelta(seconds=window_seconds)
    async with session_factory() as session:
        total, failed = (
            await session.execute(
                select(
                    func.count(models.EventDelivery.id),
                    func.count(models.EventDelivery.id).filter(
                        models.EventDelivery.status == models.DELIVERY_FAILED
                    ),
                ).where(models.EventDelivery.updated_at >= since)
            )
        ).one()
    return int(failed or 0), int(total or 0)


@dataclass(frozen=True)
class Condition:
    """Something that is wrong right now, in a form an operator can read."""

    key: str
    summary: str
    detail: str


class TelegramNotifier:
    """Sends an operator alert, or says plainly why it cannot."""

    def __init__(self, chat_id: str | None) -> None:
        self.chat_id = chat_id

    @property
    def configured(self) -> bool:
        return bool(self.chat_id and telegram.is_configured())

    async def send(self, text: str) -> bool:
        if not self.chat_id:
            log.error("ALERT NOT SENT (OPS_TELEGRAM_CHAT_ID unset): %s", text)
            return False
        if not telegram.is_configured():
            log.error("ALERT NOT SENT (TELEGRAM_BOT_TOKEN unset): %s", text)
            return False
        try:
            await telegram.send_message(self.chat_id, text)
        except telegram.TelegramError as exc:
            # Losing the alert must not lose the reason it existed, so the text
            # goes to the log either way.
            log.error("alert delivery failed (%s): %s", exc, text)
            return False
        return True


async def alert_discrete(summary: str, detail: str) -> None:
    """Raise a one-off operator alert for something that happened once.

    `AlertWatcher` handles *ongoing* conditions — a stalled worker, a growing
    backlog — and reports each edge once so it does not repeat. Some incidents are
    not conditions at all: a suspected double charge, or a payment whose fate we can
    no longer determine. Each happens once, concerns one payment, and is over. Those
    come here, through the same notifier and the same counter, so there is still
    exactly one place alerts leave the platform from.

    Never raises. An alert is a courtesy, and losing the alert must not also lose
    the caller — the text reaches the log either way.
    """
    ALERTS_RAISED.labels(condition=summary).inc()
    log.error("ALERT %s: %s", summary, detail)
    try:
        await TelegramNotifier(get_settings().ops_telegram_chat_id).send(
            f"ALERT {summary}: {detail}"
        )
    except Exception as exc:  # noqa: BLE001 - an alert must never break its caller
        log.error("alert delivery raised (%s): %s", exc, detail)


class AlertWatcher:
    """Evaluates the conditions on a timer and reports each edge once."""

    def __init__(
        self,
        *,
        transport,
        queues: list[str],
        notifier: TelegramNotifier,
        interval_seconds: float,
        stall_seconds: float,
        backlog_threshold: int,
        delivery_outcomes: DeliveryOutcomes | None = None,
        failure_rate: float = 0.1,
        failure_window_seconds: float = 900.0,
        failure_min_sample: int = 20,
    ) -> None:
        self.transport = transport
        self.queues = queues
        self.notifier = notifier
        self.interval_seconds = interval_seconds
        self.stall_seconds = stall_seconds
        self.backlog_threshold = backlog_threshold
        self.delivery_outcomes = delivery_outcomes
        self.failure_rate = failure_rate
        self.failure_window_seconds = failure_window_seconds
        self.failure_min_sample = failure_min_sample
        self._firing: dict[str, Condition] = {}
        if not notifier.configured:
            log.warning(
                "alert delivery is not configured (set OPS_TELEGRAM_CHAT_ID and "
                "TELEGRAM_BOT_TOKEN); conditions will be logged, not sent"
            )

    @classmethod
    def from_settings(cls, *, transport, queues: list[str]) -> AlertWatcher:
        settings = get_settings()
        return cls(
            transport=transport,
            queues=queues,
            notifier=TelegramNotifier(settings.ops_telegram_chat_id),
            interval_seconds=settings.alert_interval_seconds,
            stall_seconds=settings.alert_worker_stall_seconds,
            backlog_threshold=settings.alert_webhook_backlog,
            delivery_outcomes=webhook_delivery_outcomes,
            failure_rate=settings.alert_webhook_failure_rate,
            failure_window_seconds=settings.alert_webhook_failure_window_seconds,
            failure_min_sample=settings.alert_webhook_failure_min_sample,
        )

    async def evaluate(self) -> list[Condition]:
        """Every condition true right now."""
        conditions: list[Condition] = []
        now = time.monotonic()
        heartbeats = await self.transport.heartbeats()

        for queue in self.queues:
            last = heartbeats.get(queue)
            if last is None:
                conditions.append(
                    Condition(
                        key=f"worker_stalled:{queue}",
                        summary=f"worker never started — {queue}",
                        detail=(
                            f"no dequeue has ever happened on {queue}; nothing on "
                            "that queue is being processed"
                        ),
                    )
                )
                continue
            age = now - last
            if age > self.stall_seconds:
                conditions.append(
                    Condition(
                        key=f"worker_stalled:{queue}",
                        summary=f"worker stalled — {queue}",
                        detail=f"no dequeue on {queue} for {age:.0f}s",
                    )
                )

        metrics = await self.transport.metrics()
        backlog = metrics.get(Q_WEBHOOK, {}).get("pending", 0)
        if backlog > self.backlog_threshold:
            conditions.append(
                Condition(
                    key="webhook_backlog",
                    summary="webhook backlog",
                    detail=(
                        f"{backlog} deliveries waiting on {Q_WEBHOOK} "
                        f"(threshold {self.backlog_threshold}); merchants are not "
                        "being told about payments"
                    ),
                )
            )

        if self.delivery_outcomes is not None:
            try:
                failed, total = await self.delivery_outcomes(
                    self.failure_window_seconds
                )
            except Exception as exc:  # noqa: BLE001 - a check that cannot run is not a condition
                # A measurement we could not take is not evidence of recovery, so the
                # last verdict stands rather than clearing. Otherwise a database blip
                # would send "RESOLVED" about a rail that is still broken.
                log.warning("webhook failure rate could not be measured: %s", exc)
                previous = self._firing.get("webhook_failure_rate")
                if previous is not None:
                    conditions.append(previous)
            else:
                minutes = self.failure_window_seconds / 60
                if total >= self.failure_min_sample and failed / total > self.failure_rate:
                    conditions.append(
                        Condition(
                            key="webhook_failure_rate",
                            summary="webhooks failing",
                            detail=(
                                f"{failed} of {total} delivery attempts failed in the "
                                f"last {minutes:.0f} min "
                                f"({failed / total:.0%}, threshold "
                                f"{self.failure_rate:.0%}); merchants' systems are "
                                "missing payments even though the queue is moving"
                            ),
                        )
                    )
        return conditions

    async def check_once(self) -> list[Condition]:
        """Evaluate, and report the edges since the last call."""
        conditions = await self.evaluate()
        active = {condition.key for condition in conditions}

        for condition in conditions:
            if condition.key in self._firing:
                continue
            self._firing[condition.key] = condition
            ALERTS_RAISED.labels(condition=condition.key).inc()
            await self.notifier.send(f"ALERT {condition.summary}: {condition.detail}")

        for key in list(self._firing):
            if key in active:
                continue
            resolved = self._firing.pop(key)
            await self.notifier.send(f"RESOLVED {resolved.summary}")

        return conditions

    async def watch(self, stop: asyncio.Event) -> None:
        """Tick until shutdown.

        Reschedules itself with `call_later` rather than looping: this codebase
        allows exactly one `while` in `src/` (workers/base.py), and that rule exists
        to stop this kind of background loop from being written casually.

        The first check is one full interval away on purpose — checking at boot
        would race the drain loops and page someone about a worker that is still
        starting up.
        """
        loop = asyncio.get_running_loop()

        async def tick() -> None:
            if stop.is_set():
                return
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001 - a failed check must not stop the watch
                log.warning("alert watcher tick failed: %s", exc)
            schedule()

        def schedule() -> None:
            if stop.is_set():
                return
            loop.call_later(self.interval_seconds, lambda: loop.create_task(tick()))

        schedule()
        await stop.wait()
