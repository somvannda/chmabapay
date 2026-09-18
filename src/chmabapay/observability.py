"""Metrics, trace-correlated logs, and the numbers worth alerting on (P1-3).

Three things live together here because they answer one question — what is this
service doing while nobody is watching it:

* **A trace id on every log record.** The `X-ChmabaPay-Trace` header already
  existed, so a request could be traced across *responses*; without the id in the
  logs it could not be traced *through* them.
* **Metrics in the Prometheus exposition format**, served from `/metrics`. Queue
  figures are pulled from the worker transport, payment figures are counted where
  each lifecycle event actually happens.
* **The series the plan names as decision-making**: payment outcomes, settlement
  time, queue depth, worker heartbeat age.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from .config import get_settings

# --------------------------------------------------------------------------- #
# Trace id — the same value that leaves in X-ChmabaPay-Trace
# --------------------------------------------------------------------------- #
_TRACE_ID_CTX: ContextVar[str] = ContextVar("x_trace_id", default="")


def bind_trace_id(trace_id: str) -> Token[str]:
    """Bind the current request's trace id. Callers reset with the token."""
    return _TRACE_ID_CTX.set(trace_id)


def release_trace_id(token: Token[str]) -> None:
    _TRACE_ID_CTX.reset(token)


def current_trace_id() -> str:
    """`-` outside a request, rather than a fresh uuid per call.

    The old implementation minted a uuid when unbound, so two calls in the same
    log line reported two different ids — worse than useless for correlation.
    """
    return _TRACE_ID_CTX.get() or "-"


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(trace_id)s] %(message)s"

APP_LOGGER = "chmabapay"


class TraceIdFilter(logging.Filter):
    """Puts the current trace id on every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = current_trace_id()
        return True


def configure_logging() -> None:
    """Give our namespace a handler, a level and the trace id.

    Scoped to `chmabapay` on purpose: uvicorn configures its own loggers, and
    reaching for the root logger would either duplicate its output or silence it.

    This also fixes something long-standing — `logging.getLogger(__name__).info()`
    anywhere in the package produced nothing, because the root logger has no
    handler and Python's last-resort handler drops everything below WARNING.
    """
    logger = logging.getLogger(APP_LOGGER)
    logger.setLevel(get_settings().log_level.upper())
    if logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(TraceIdFilter())
    logger.addHandler(handler)
    # Our handler owns our records; letting them also climb to the root would
    # double every line wherever something else has configured it.
    logger.propagate = False


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
HTTP_REQUESTS = Counter(
    "chmabapay_http_requests_total",
    "HTTP requests, by route template and status code.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "chmabapay_http_request_duration_seconds",
    "HTTP request duration, by route template.",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
PAYMENT_EVENTS = Counter(
    "chmabapay_payment_events_total",
    "Payment lifecycle events.",
    ["event"],
)
SETTLEMENT_SECONDS = Histogram(
    "chmabapay_payment_settlement_seconds",
    "Seconds from a customer being handed a code to us recording it paid. "
    "Includes the customer's own time, so it is not detection latency alone — "
    "the platform does not see the moment money moved, only the moment it looked.",
    buckets=(5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0),
)
# Gauges rather than counters for the queue figures: the source is an in-process
# snapshot that resets when the process does, and a `_total` that can go backwards
# is worse than a gauge that means what it says.
QUEUE_PENDING = Gauge(
    "chmabapay_queue_pending",
    "Jobs waiting in a queue right now.",
    ["queue"],
)
QUEUE_JOBS = Gauge(
    "chmabapay_queue_jobs",
    "Jobs finished since start, by outcome.",
    ["queue", "outcome"],
)
WORKER_HEARTBEAT_AGE = Gauge(
    "chmabapay_worker_heartbeat_age_seconds",
    "Seconds since this queue's drain loop last asked for work. Stops advancing "
    "when the loop dies, which is the failure nothing else would tell us about.",
    ["queue"],
)
ALERTS_RAISED = Counter(
    "chmabapay_alerts_raised_total",
    "Operator alerts raised, by condition.",
    ["condition"],
)
# Distinct from ALERTS_RAISED: this counts every occurrence of an unhandled
# exception, including the ones the alert channel deliberately suppressed. The gap
# between the two is how you tell "one error" from "one error, ten thousand times".
ERRORS_TOTAL = Counter(
    "chmabapay_errors_total",
    "Unhandled exceptions captured, by site and exception type.",
    ["where", "type"],
)


def _as_utc(value: datetime) -> datetime:
    # SQLite hands back naive datetimes for tz-aware columns, and subtracting a
    # naive from an aware one raises. Postgres never does this, so it is exactly
    # the kind of thing that works in production and breaks in a test.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def observe_settlement(created_at: datetime | None, paid_at: datetime | None) -> None:
    """Record how long a payment took from being handed out to being confirmed."""
    if created_at is None or paid_at is None:
        return
    seconds = (_as_utc(paid_at) - _as_utc(created_at)).total_seconds()
    if seconds >= 0:
        SETTLEMENT_SECONDS.observe(seconds)


async def refresh_queue_metrics(transport, now: float) -> None:
    """Pull the live queue figures. Called at scrape time, not on a timer.

    Scrape-time means the numbers are as fresh as the scrape, and a stalled
    process is visible as a heartbeat age that stops advancing rather than one
    that was last written before the stall.
    """
    for queue, counts in (await transport.metrics()).items():
        QUEUE_PENDING.labels(queue=queue).set(counts.get("pending", 0))
        for outcome in ("success", "dead", "retry"):
            QUEUE_JOBS.labels(queue=queue, outcome=outcome).set(counts.get(outcome, 0))

    # The heartbeat age has two possible sources and they are not interchangeable.
    # A shared transport can report ages measured against a wall clock by whichever
    # process is draining — which is the only view the API has when the workers live
    # elsewhere. Otherwise the transport's own monotonic timestamps are the accurate
    # ones, because `now` comes from the same clock.
    shared_ages = await transport.worker_age_seconds()
    if shared_ages:
        for queue, age in shared_ages.items():
            WORKER_HEARTBEAT_AGE.labels(queue=queue).set(max(0.0, age))
    else:
        for queue, last_dequeue in (await transport.heartbeats()).items():
            WORKER_HEARTBEAT_AGE.labels(queue=queue).set(max(0.0, now - last_dequeue))


def render_metrics() -> tuple[bytes, str]:
    """The registry as Prometheus text, plus the content type it must ship with."""
    return generate_latest(), CONTENT_TYPE_LATEST
