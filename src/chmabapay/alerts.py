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
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from . import models
from .config import get_settings
from .db import session_factory
from .observability import ALERTS_RAISED
from .services import telegram
from .workers import Q_BILLING, Q_DETECTION, Q_EXPIRY, Q_RETENTION, Q_WEBHOOK
from .workers.w6_billing_lifecycle import QUEUE as Q_LIFECYCLE

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Operator-facing vocabulary
# --------------------------------------------------------------------------- #
# The queue name is the transport's identifier: it appears in the metrics, the config
# and the logs, and it is deliberately kept in every message. It is not, though, what
# someone holding a phone at 3am should have to decode before knowing whether this is
# a wake-up. So each queue carries a name in words and, more to the point, the reason
# anyone should care.
DISPLAY_TZ = ZoneInfo("Asia/Phnom_Penh")

QUEUE_LABELS: dict[str, str] = {
    Q_DETECTION: "Payment detection",
    Q_WEBHOOK: "Webhook delivery",
    Q_BILLING: "Plan invoicing",
    Q_EXPIRY: "Payment expiry sweep",
    Q_RETENTION: "Retention sweep",
    Q_LIFECYCLE: "Billing lifecycle",
}

# What stops happening while this queue is not draining, in the terms the business
# uses. "worker stalled — webhooks.send" says nothing about whether money is moving;
# "merchants are not being told about payments" says all of it.
QUEUE_IMPACT: dict[str, str] = {
    Q_DETECTION: "payments that have already been made may never be marked as paid",
    Q_WEBHOOK: "merchants are not being told about payments",
    Q_BILLING: "plan invoices are not being raised, so the platform is not billing",
    Q_EXPIRY: "unpaid payment codes are not being expired",
    Q_RETENTION: "old data is not being pruned",
    Q_LIFECYCLE: "billing reminders and account freezes are not running",
}

_UNKNOWN_IMPACT = "work on this queue is not being processed"


def queue_label(queue: str) -> str:
    """`webhooks.send` as an operator says it: `Webhook delivery (webhooks.send)`."""
    name = QUEUE_LABELS.get(queue)
    return f"{name} ({queue})" if name else queue


def queue_impact(queue: str) -> str:
    """Why a stalled queue matters, or a plain fallback for one with no script."""
    return QUEUE_IMPACT.get(queue, _UNKNOWN_IMPACT)


# --------------------------------------------------------------------------- #
# The guard that keeps a development deployment off the operator chat
# --------------------------------------------------------------------------- #
# Blank credentials in `.env` are not enough, and that was learned the expensive way: the
# real bot token was removed from the dev config and alerts still arrived, because the
# launcher exported `WORKERS_ENABLED` into the process environment and a stray credential
# delivered regardless. A page that fires because a laptop's database is stopped teaches
# the reader to mute the channel, and a muted channel is the same as none.
#
# Two signals, each conclusive on its own, because both have to be right for the product
# to work at all: `enable_dev_gateway` mounts the fake payment rail, and the production
# compose file hardcodes it false rather than interpolating it; `PUBLIC_ORIGIN` is the
# origin merchants are sent to, which cannot be a local address where real payments are
# taken. An empty `PUBLIC_ORIGIN` is *unknown* rather than evidence, so it suppresses
# nothing — losing a production page would be far worse than the noise this prevents.
_LOCAL_ORIGINS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "host.docker.internal")


def development_reason() -> str | None:
    """Why this deployment must not page, or `None` when nothing says it must not."""
    settings = get_settings()
    if settings.enable_dev_gateway:
        return "ENABLE_DEV_GATEWAY is on, so this is a development deployment"
    origin = (settings.public_origin or "").strip().lower()
    if origin and any(host in origin for host in _LOCAL_ORIGINS):
        return f"PUBLIC_ORIGIN is a local address ({settings.public_origin})"
    return None


def delivery_allowed() -> bool:
    """Whether an alert may leave this deployment at all.

    `ALERTS_ALLOW_NON_PRODUCTION=true` overrides it, which is the only way to exercise
    the alert path from a development machine — deliberately against a throwaway chat.
    """
    if get_settings().alerts_allow_non_production:
        return True
    return development_reason() is None


def local_now() -> datetime:
    """The wall clock the timestamps in an alert are written against.

    Instants are stored and compared in UTC; a message is read by a person, and a bare
    `2026-09-24 12:11` with no zone is an invitation to misread it. Cambodia has no
    DST, but the named zone is still what to hold — a hardcoded +07:00 is correct until
    the day it is not, and it would then mis-render every historical instant.
    """
    return datetime.now(DISPLAY_TZ)


def format_moment(moment: datetime) -> str:
    """`24 Sep 2026, 17:31 +07` — a time someone can line up against their own watch."""
    local = moment.astimezone(DISPLAY_TZ)
    # `%z` is `+0700`; the minutes are noise for a zone that is always on the hour.
    offset = local.strftime("%z")[:3]
    return f"{local.day} {local:%b %Y}, {local:%H:%M} {offset}"


def format_elapsed(seconds: float) -> str:
    """`48s`, `4m 12s`, `2h 05m` — a duration in the units a person thinks in."""
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s" if secs else f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m" if mins else f"{hours}h"

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
    # What the business loses while this is true. Optional so a condition that has no
    # consequence worth stating does not have to invent one.
    impact: str = ""


@dataclass
class _Firing:
    """A condition that is true right now, and when it started being true.

    `condition` is replaced on every tick so the recovery message can quote the last
    reading; `since` never moves, because "how long was this broken" is measured from
    the first observation, not from the most recent one.
    """

    condition: Condition
    since: datetime


def format_condition_raised(condition: Condition, *, since: datetime) -> str:
    """One condition starting, as the message a person actually receives.

    ```
    ChmabaPay · ALERT · worker stalled — Webhook delivery

    No job has been picked up on Webhook delivery (webhooks.send) for 73s. ...

    Why it matters: merchants are not being told about payments
    Time: 24 Sep 2026, 17:31 +07
    ```
    """
    lines = [f"ChmabaPay · ALERT · {condition.summary}", "", condition.detail]
    if condition.impact:
        lines += ["", f"Why it matters: {condition.impact}"]
    lines.append(f"Time: {format_moment(since)}")
    return "\n".join(lines)


def format_condition_resolved(firing: _Firing) -> str:
    """One condition clearing, with how long it lasted.

    The duration is the point: "the webhook sender stalled" is not the same news at 40
    seconds as it is at 40 minutes, and the recovery message is often the first one
    anyone reads.
    """
    lasted = format_elapsed((local_now() - firing.since).total_seconds())
    return "\n".join(
        [
            f"ChmabaPay · RESOLVED · {firing.condition.summary}",
            "",
            f"This has cleared after {lasted}. It started at "
            f"{format_moment(firing.since)}.",
        ]
    )


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
        if not delivery_allowed():
            log.error(
                "ALERT NOT SENT (development deployment: %s; set "
                "ALERTS_ALLOW_NON_PRODUCTION=true to page from here anyway): %s",
                development_reason(),
                text,
            )
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
    # The detail is written for a person already; what it needs is a title they can
    # triage from and the time it happened. No second copy of the wording.
    text = (
        f"ChmabaPay · ALERT · {summary}\n\n"
        f"{detail}\n\n"
        f"Time: {format_moment(local_now())}"
    )
    try:
        await TelegramNotifier(get_settings().ops_telegram_chat_id).send(text)
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
        self._firing: dict[str, _Firing] = {}
        if not notifier.configured:
            log.warning(
                "alert delivery is not configured (set OPS_TELEGRAM_CHAT_ID and "
                "TELEGRAM_BOT_TOKEN); conditions will be logged, not sent"
            )
        elif not delivery_allowed():
            log.warning(
                "alert delivery is suppressed (%s); conditions will be logged, not "
                "sent. Set ALERTS_ALLOW_NON_PRODUCTION=true to page from here anyway.",
                development_reason(),
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
                        summary=f"worker never started — {QUEUE_LABELS.get(queue, queue)}",
                        detail=(
                            f"Nothing has ever been picked up on {queue_label(queue)}. "
                            "A drain loop that never started is the most urgent case "
                            "there is: no job on this queue has been processed since "
                            "the worker came up."
                        ),
                        impact=queue_impact(queue),
                    )
                )
                continue
            age = now - last
            if age > self.stall_seconds:
                conditions.append(
                    Condition(
                        key=f"worker_stalled:{queue}",
                        summary=f"worker stalled — {QUEUE_LABELS.get(queue, queue)}",
                        detail=(
                            f"No job has been picked up on {queue_label(queue)} for "
                            f"{format_elapsed(age)}. A healthy loop asks several times "
                            "a second, so this is a loop that has stopped, not one "
                            "that is busy."
                        ),
                        impact=queue_impact(queue),
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
                        f"(alert threshold {self.backlog_threshold}). More is being "
                        "queued than is being sent, so the queue is growing."
                    ),
                    impact=queue_impact(Q_WEBHOOK),
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
                    conditions.append(previous.condition)
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
                                f"({failed / total:.0%}, alert threshold "
                                f"{self.failure_rate:.0%}). The queue is moving, so the "
                                "backlog alert cannot see this: every delivery is "
                                "being attempted, and failing."
                            ),
                            impact="merchants' systems are missing payments",
                        )
                    )
        return conditions

    async def check_once(self) -> list[Condition]:
        """Evaluate, and report the edges since the last call."""
        conditions = await self.evaluate()
        active = {condition.key for condition in conditions}

        for condition in conditions:
            firing = self._firing.get(condition.key)
            if firing is not None:
                # Still down. Keep the newest reading so the recovery message describes
                # the state that cleared rather than the one that first tripped, but
                # send nothing — one message per edge is the whole point.
                firing.condition = condition
                continue
            firing = _Firing(condition=condition, since=local_now())
            self._firing[condition.key] = firing
            ALERTS_RAISED.labels(condition=condition.key).inc()
            await self.notifier.send(
                format_condition_raised(condition, since=firing.since)
            )

        for key in list(self._firing):
            if key in active:
                continue
            firing = self._firing.pop(key)
            await self.notifier.send(format_condition_resolved(firing))

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
