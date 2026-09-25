"""Error tracking: the unhandled exceptions, reported once (P1-3).

`alerts.py` covers two shapes of trouble. *Conditions* that persist — a stalled
worker, a growing backlog — reported on each edge. *Discrete incidents* that were
anticipated, like a suspected double charge, reported through `alert_discrete`.
This module covers the third shape: an exception nobody expected at all. An
unhandled request in the API, or a job that raised in a worker.

Two properties matter here, and both are about the channel surviving.

* **Reported once, then summarised.** A fingerprint — where it happened, the
  exception type, and the message with its variable parts collapsed — is sent the
  first time it is seen. Repeats inside `error_report_interval_seconds` are counted
  rather than sent, and the count goes out with the next report. Without this, one
  hot error sends a message per occurrence, someone mutes the channel, and the next
  genuine alert is lost. That failure mode is worse than no tracking at all.
* **Never raises, and never swallows.** The exception is logged with its traceback
  at ERROR before anything else, so the log is complete even when delivery is not.
  A failure to report must not replace the failure being reported.

**What this is not.** It is not a hosted error tracker: there is no grouping UI, no
stack-trace search, no release-regression view, and nothing survives a redeploy
except the log lines. It is the capture point plus the channel that already reaches
a human. A Sentry-style transport would attach at `_send` rather than replace
anything here.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .config import get_settings
from .observability import ERRORS_TOTAL, current_trace_id

if TYPE_CHECKING:
    from .alerts import TelegramNotifier

log = logging.getLogger(__name__)

# Fingerprints are derived from exception messages, so the map is caller-influenced:
# anything that can trigger a new message shape can add a key. Bounded, oldest-first,
# because unbounded state on caller-supplied input is a leak.
_MAX_FINGERPRINTS = 500

# Telegram rejects a message over 4096 characters, and an exception message can be an
# entire SQL statement with its parameters. Truncate well inside the limit rather than
# let the channel refuse a report about a failure because the failure was too verbose.
_MAX_FIRST_LINE = 400
_MAX_MESSAGE = 3500

# The variable parts of a message — ids, amounts, timestamps, hex and uuid blobs.
# Collapsing them is what makes "payment 41 not found" and "payment 92 not found"
# one fingerprint, without merging them with "store 7 not found". The dashed uuid
# alternative comes first on purpose: the bare hex run below would chew a uuid into
# `#-#-#-#-#` fragments, and the two spellings of one payment id — "41" and its
# uuid — would then stop matching each other, which is the whole point of the rule.
_VARIABLE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-fA-F]{8,}"
    r"|\d+"
)

# --------------------------------------------------------------------------- #
# Reading an exception the way the operator reads it
# --------------------------------------------------------------------------- #
# The type name and the message are for us; the sentence is for whoever is holding the
# phone. Only shapes we recognise get a sentence — inventing a meaning for an unknown
# exception would be worse than admitting we do not have one.
_CONNECTIVITY_ERRORS = {
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "ConnectTimeoutError",
    "DBAPIError",
    "InterfaceError",
    "OperationalError",
    "PendingRollbackError",
    "PoolTimeout",
}
# Matched against the message as well as the type, because the two spellings of the same
# failure do not share a class: SQLAlchemy raises `InterfaceError` ("connection is
# closed"), asyncpg raises its own, and a socket that is gone raises a builtin.
_CONNECTIVITY_HINTS = (
    "connection is closed",
    "connection was closed",
    "connection refused",
    "connection reset",
    "could not connect",
    "server closed the connection",
    "too many connections",
    "the remote computer refused",
)
_DATA_ERRORS = {
    "IntegrityError",
    "ForeignKeyViolationError",
    "NotNullViolationError",
    "UniqueViolationError",
}
_CODE_ERRORS = {
    "AssertionError",
    "AttributeError",
    "IndexError",
    "KeyError",
    "NotImplementedError",
    "TypeError",
    "ValueError",
}


def _classify(exc: BaseException) -> tuple[str, str | None]:
    """`(severity, plain-English meaning)` for one exception.

    The meaning is `None` when the exception is not a shape we recognise — silence is
    more useful than a confident guess about a fault nobody has seen before.
    """
    name = type(exc).__name__
    message = _first_line(exc).lower()

    if name in _CONNECTIVITY_ERRORS or any(h in message for h in _CONNECTIVITY_HINTS):
        return (
            "OUTAGE",
            "The platform lost its connection to a service it depends on, almost "
            "always the database. Anything that has to read or write is failing "
            "until it comes back.",
        )
    if name in _DATA_ERRORS:
        return (
            "ERROR",
            "A database write was rejected — a duplicate, a missing row or a "
            "constraint. Nothing was written silently; the operation that hit this "
            "did not happen.",
        )
    if name in _CODE_ERRORS:
        return ("ERROR", "The code reached a case it did not expect.")
    return ("ERROR", None)


def _area(where: str) -> str:
    """Where it happened, said the way a person would say it."""
    if where.startswith("worker:"):
        return "background worker"
    if where.startswith("api"):
        return "API request"
    return where


def _location_lines(where: str, context: str | None, queue_label) -> list[str]:
    """The `Worker:` / `Call:` / `Path:` lines that identify what was running."""
    if where.startswith("worker:"):
        queue = where.split(":", 1)[1]
        lines = [f"Worker:  {queue_label(queue)}"]
        if context:
            lines.append(f"Job:     {context}")
        return lines
    if where.startswith("api "):
        call = where[len("api ") :]
        path = call.split(" ", 1)[-1]
        lines = [f"Call:    {call}"]
        # The template is what the metrics group by; the path is what was actually
        # asked for. They differ only for a parameterised route, and that difference
        # is usually the answer to "which payment?".
        if context and context != path:
            lines.append(f"Path:    {context}")
        return lines
    lines = [f"Where:   {where}"]
    if context:
        lines.append(f"Context: {context}")
    return lines


@dataclass
class _Seen:
    first_at: float  # monotonic, so a duration survives a clock change
    first_seen: datetime  # wall clock, for the timestamp the reader compares to
    reported_at: float
    summary: str
    total: int = 1


_seen: dict[str, _Seen] = {}


def reset_for_tests() -> None:
    """Clear the dedup state.

    Tests assert on what was sent, and the state is module-level by design — a
    per-process view is the point, so it is not threaded through callers.
    """
    _seen.clear()


def _fingerprint(where: str, exc: BaseException) -> str:
    collapsed = _VARIABLE.sub("#", str(exc))[:120]
    return f"{where}|{type(exc).__name__}|{collapsed}"


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    first = text.splitlines()[0] if text else "(no message)"
    if len(first) <= _MAX_FIRST_LINE:
        return first
    return f"{first[: _MAX_FIRST_LINE - 1]}…"


def _remember(key: str, record: _Seen) -> None:
    if len(_seen) >= _MAX_FINGERPRINTS:
        # Drop the least recently reported. The oldest first-seen is the wrong
        # victim: a long-lived noisy error would evict a brand new one.
        stalest = min(_seen, key=lambda k: _seen[k].reported_at)
        _seen.pop(stalest, None)
    _seen[key] = record


def _format_report(
    exc: BaseException,
    *,
    where: str,
    context: str | None,
    trace: str,
    seen: _Seen | None,
) -> str:
    """One unhandled exception as the message an operator is sent.

    Written to be read on a phone, which is why it carries a severity, a sentence
    saying what it means for the platform, where it happened and when — and why the
    traceback stays in the log rather than in the message:

    ```
    ChmabaPay · OUTAGE · API request

    What:    InterfaceError: connection is closed
    Meaning: The platform lost its connection to a service it depends on, ...
    Call:    GET /v1/me
    Trace:   cf950f72-4ede-465a-9ab4-e071facc2490
    Time:    23 Sep 2026, 16:52 +07

    Count:   8 since 23 Sep 2026, 16:47 (5m ago) — still happening.
    ```
    """
    # Imported inside the call for the reason `_notifier` documents: `alerts` imports
    # `chmabapay.workers`, and `workers.base` imports this module.
    from .alerts import format_elapsed, format_moment, local_now, queue_label

    severity, meaning = _classify(exc)
    lines = [f"ChmabaPay · {severity} · {_area(where)}", ""]
    lines.append(f"What:    {type(exc).__name__}: {_first_line(exc)}")
    if meaning:
        lines.append(f"Meaning: {meaning}")
    lines.extend(_location_lines(where, context, queue_label))
    lines.append(
        f"Trace:   {trace}"
        if trace and trace != "-"
        else "Trace:   none (this happened outside a request)"
    )
    lines.append(f"Time:    {format_moment(local_now())}")
    lines.append("")
    if seen is None:
        lines.append("Count:   1 — first time this error has been seen.")
    else:
        elapsed = format_elapsed(time.monotonic() - seen.first_at)
        lines.append(
            f"Count:   {seen.total} since {format_moment(seen.first_seen)} "
            f"({elapsed} ago) — still happening."
        )

    body = "\n".join(lines)
    if len(body) > _MAX_MESSAGE:
        body = f"{body[: _MAX_MESSAGE - 16].rstrip()}\n… truncated"
    return body


def _notifier() -> TelegramNotifier:
    """Resolve the delivery channel at call time, not import time.

    `alerts` reaches into `chmabapay.workers` for the queue constant it watches, and
    `workers.base` reports in here. Importing it at module scope therefore lets the
    import order decide whether this module loads: `import chmabapay.errors` is fine,
    while `import chmabapay.workers` first raises on a half-initialised package.
    Resolving inside the call sidesteps that without loosening either annotation.
    """
    from .alerts import TelegramNotifier

    return TelegramNotifier(get_settings().ops_telegram_chat_id)


async def _send(text: str) -> None:
    """Deliver one error report to every configured operator channel.

    Both the single-operator ops chat and the team's activity group, because an
    unhandled exception is the one thing that should not depend on somebody having the
    console open. The ids are deduped: pointing both settings at the same chat is a
    reasonable configuration, and it should not post twice.
    """
    # Resolved inside the call for the reason `_notifier` documents: `alerts` imports
    # `chmabapay.workers`, and `workers.base` imports this module. The annotation at
    # module scope is a `TYPE_CHECKING` import, so it is not a runtime name.
    from .alerts import TelegramNotifier

    settings = get_settings()
    delivered: set[str] = set()
    for raw in (settings.ops_telegram_chat_id, settings.activity_telegram_chat_id):
        chat_id = (raw or "").strip()
        if not chat_id or chat_id in delivered:
            continue
        delivered.add(chat_id)
        try:
            await TelegramNotifier(chat_id).send(text)
        except Exception as exc:  # noqa: BLE001 - reporting must never break the reporter
            log.error("error report delivery raised (%s)", exc)


def warn_if_unconfigured() -> None:
    """Say at startup that capture is on but delivery is not.

    The same honesty `AlertWatcher` applies to conditions, and for the same reason:
    an operator should learn about the gap now, not while debugging an incident.
    """
    # Imported locally: `services` pulls in a package that is otherwise irrelevant to
    # capture, and this module is imported by `workers.base` while the package is
    # still initialising.
    from .alerts import delivery_allowed, development_reason
    from .services import notifications

    if not (_notifier().configured or notifications.configured()):
        log.warning(
            "error tracking will log unhandled exceptions but cannot send them "
            "(set OPS_TELEGRAM_CHAT_ID or ACTIVITY_TELEGRAM_CHAT_ID, plus "
            "TELEGRAM_BOT_TOKEN)"
        )
        return
    # Configured is not the same as allowed to speak: a development deployment holds the
    # credentials and still must not page. See `alerts.delivery_allowed`.
    if not delivery_allowed():
        log.warning(
            "error tracking will log unhandled exceptions but will not send them: %s. "
            "Set ALERTS_ALLOW_NON_PRODUCTION=true to send from here anyway.",
            development_reason(),
        )


async def report_exception(
    exc: BaseException,
    *,
    where: str,
    context: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Capture one unhandled exception.

    `where` names the site — "api", or "worker:<queue>" — and is a metric label, so
    it is deliberately a small fixed set rather than anything derived from the
    exception. `context` is free text for the message (a route, a job), and never
    becomes a label.

    Never raises. Every occurrence is counted in `chmabapay_errors_total` and written
    to the log; only the first of each fingerprint, and then one per interval, is sent —
    and what is sent is a report written for a person, not a stack-trace header.
    """
    type_name = type(exc).__name__
    ERRORS_TOTAL.labels(where=where, type=type_name).inc()

    trace = trace_id or current_trace_id()
    compact = f"UNHANDLED {where} {type_name}: {_first_line(exc)}"
    if context:
        compact = f"{compact} | at {context}"
    # First, unconditionally: whatever else happens, the traceback is in the log. The
    # log line stays a single line on purpose — it is grep'd, and whoever reads a log
    # file already knows the conventions the phone reader does not.
    log.error("%s | trace %s", compact, trace, exc_info=exc)

    key = _fingerprint(where, exc)
    now = time.monotonic()
    record = _seen.get(key)

    if record is None:
        # Imported locally for the same reason `_notifier` documents: `alerts` reaches
        # back into `workers`, which imports this module.
        from .alerts import local_now

        _remember(
            key,
            _Seen(
                first_at=now,
                first_seen=local_now(),
                reported_at=now,
                summary=f"{type_name} at {where}: {_first_line(exc)}",
            ),
        )
        await _send(
            _format_report(exc, where=where, context=context, trace=trace, seen=None)
        )
        return

    # Every occurrence counts toward the total, including the ones inside the window
    # that are not sent: "this has happened 47 times since 09:12" is the difference
    # between a blip and something that is genuinely broken.
    record.total += 1
    window = max(0.0, get_settings().error_report_interval_seconds)
    if now - record.reported_at < window:
        return

    record.reported_at = now
    await _send(
        _format_report(exc, where=where, context=context, trace=trace, seen=record)
    )
