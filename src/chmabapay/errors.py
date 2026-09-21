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


@dataclass
class _Seen:
    first_at: float
    reported_at: float
    summary: str
    suppressed: int = 0


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
    return str(exc).strip().splitlines()[0] if str(exc).strip() else "(no message)"


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _remember(key: str, record: _Seen) -> None:
    if len(_seen) >= _MAX_FINGERPRINTS:
        # Drop the least recently reported. The oldest first-seen is the wrong
        # victim: a long-lived noisy error would evict a brand new one.
        stalest = min(_seen, key=lambda k: _seen[k].reported_at)
        _seen.pop(stalest, None)
    _seen[key] = record


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
    from .services import notifications

    if _notifier().configured or notifications.configured():
        return
    log.warning(
        "error tracking will log unhandled exceptions but cannot send them "
        "(set OPS_TELEGRAM_CHAT_ID or ACTIVITY_TELEGRAM_CHAT_ID, plus "
        "TELEGRAM_BOT_TOKEN)"
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
    to the log; only the first of each fingerprint, and then one per interval, is sent.
    """
    type_name = type(exc).__name__
    ERRORS_TOTAL.labels(where=where, type=type_name).inc()

    trace = trace_id or current_trace_id()
    detail = f"UNHANDLED {where} {type_name}: {_first_line(exc)}"
    if context:
        detail = f"{detail}\n  at      {context}"
    detail = f"{detail}\n  trace   {trace}"

    # First, unconditionally: whatever else happens, the traceback is in the log.
    log.error("%s", detail.replace("\n", " | "), exc_info=exc)

    key = _fingerprint(where, exc)
    now = time.monotonic()
    record = _seen.get(key)

    if record is None:
        _remember(
            key,
            _Seen(
                first_at=now,
                reported_at=now,
                summary=f"{type_name} at {where}: {_first_line(exc)}",
            ),
        )
        await _send(detail)
        return

    window = max(0.0, get_settings().error_report_interval_seconds)
    if now - record.reported_at < window:
        record.suppressed += 1
        return

    suppressed = record.suppressed
    record.reported_at = now
    record.suppressed = 0
    suffix = (
        f"\n\n{suppressed} more of this same error in the last "
        f"{_duration(now - record.first_at)}; first seen then"
        if suppressed
        else ""
    )
    await _send(f"{detail}{suffix}")
