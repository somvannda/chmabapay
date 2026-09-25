"""Error tracking: what gets reported, and what deliberately does not (P1-3).

Two things are being asserted here, and the second matters as much as the first.
That a new exception reaches a person — and that a *repeated* one does not, because
a channel that every occurrence can flood is a channel someone mutes, which is the
same as having none.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from sqlalchemy.exc import InterfaceError

from chmabapay import errors, observability
from chmabapay.main import app
from chmabapay.routers.auth import get_current_session_account
from chmabapay.workers import InProcessTransport
from chmabapay.workers.base import Worker
from chmabapay.workers.job import Job


class _Stub:
    def __init__(self, interval: float) -> None:
        self.error_report_interval_seconds = interval


def _capture(monkeypatch, *, interval: float = 300.0) -> list[str]:
    """Capture what would be sent. `_send` is the delivery boundary.

    Patching there exercises the decision — fingerprint, window, count — without
    reaching Telegram, and the dedup map is process-wide by design, so it needs
    clearing between tests.
    """
    sent: list[str] = []

    async def fake_send(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(errors, "_send", fake_send)
    monkeypatch.setattr(errors, "get_settings", lambda: _Stub(interval))
    errors.reset_for_tests()
    return sent


def _clock(monkeypatch, start: float = 1000.0) -> list[float]:
    """A hand-cranked monotonic clock, so the window is tested rather than slept.

    `errors.time` is replaced, not `time.monotonic` patched. The latter would edit
    the shared `time` module object and stop the clock for everything else in the
    process, which is a side effect well outside this test.
    """
    now = [start]
    monkeypatch.setattr(errors, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def _sample(text: str, name: str, **labels: str) -> float:
    """One sample out of the rendered metrics, or 0.0 when it is absent."""
    for line in text.splitlines():
        if not line.startswith(name):
            continue
        rest = line[len(name) :]
        if rest[:1] not in ("{", " "):
            continue
        if not all(f'{key}="{value}"' in line for key, value in labels.items()):
            continue
        return float(line.rsplit(" ", 1)[1])
    return 0.0


# --------------------------------------------------------------------------- #
# Reported once
# --------------------------------------------------------------------------- #
async def test_a_new_error_is_sent_and_a_repeat_is_not(monkeypatch):
    sent = _capture(monkeypatch)

    for _ in range(5):
        await errors.report_exception(ValueError("boom"), where="api")

    assert len(sent) == 1


async def test_the_next_window_carries_how_many_were_suppressed(monkeypatch):
    sent = _capture(monkeypatch)
    now = _clock(monkeypatch)

    await errors.report_exception(ValueError("boom"), where="api")
    now[0] += 1
    await errors.report_exception(ValueError("boom"), where="api")
    now[0] += 1
    await errors.report_exception(ValueError("boom"), where="api")
    assert len(sent) == 1

    # Past the window the error is worth mentioning again, and the point of the
    # repeat is the count: "still happening, 4 in total" is different news from
    # "it happened once".
    now[0] += 400
    await errors.report_exception(ValueError("boom"), where="api")

    assert len(sent) == 2
    assert "still happening" in sent[1]
    assert "Count:   4 since" in sent[1]


async def test_the_window_is_per_error_not_global(monkeypatch):
    sent = _capture(monkeypatch)

    await errors.report_exception(ValueError("first"), where="api")
    await errors.report_exception(ValueError("second"), where="api")

    assert len(sent) == 2


# --------------------------------------------------------------------------- #
# What counts as the same error
# --------------------------------------------------------------------------- #
async def test_variable_parts_of_a_message_are_one_error(monkeypatch):
    """Ids and amounts differ per occurrence; the bug does not."""
    sent = _capture(monkeypatch)

    await errors.report_exception(ValueError("payment 41 not found"), where="api")
    await errors.report_exception(ValueError("payment 92 not found"), where="api")
    await errors.report_exception(
        ValueError("payment 8f3c1d2e-4a5b-6c7d-8e9f-0a1b2c3d4e5f not found"),
        where="api",
    )

    assert len(sent) == 1


async def test_a_different_message_is_a_different_error(monkeypatch):
    """Collapsing has to stop short of merging genuinely different faults."""
    sent = _capture(monkeypatch)

    await errors.report_exception(ValueError("payment 41 not found"), where="api")
    await errors.report_exception(ValueError("store 41 not found"), where="api")

    assert len(sent) == 2


async def test_the_same_message_from_two_sites_is_two_errors(monkeypatch):
    sent = _capture(monkeypatch)

    await errors.report_exception(ValueError("boom"), where="api GET /health")
    await errors.report_exception(ValueError("boom"), where="worker:w1")

    assert len(sent) == 2


# --------------------------------------------------------------------------- #
# The counter is not the alert channel
# --------------------------------------------------------------------------- #
async def test_every_occurrence_is_counted_even_when_it_is_not_sent(monkeypatch):
    """The gap between the counter and the message is the severity."""
    sent = _capture(monkeypatch)
    labels = {"where": "api", "type": "ValueError"}
    before = _sample(
        observability.render_metrics()[0].decode(), "chmabapay_errors_total", **labels
    )

    for _ in range(6):
        await errors.report_exception(ValueError("boom"), where="api")

    after = _sample(
        observability.render_metrics()[0].decode(), "chmabapay_errors_total", **labels
    )
    assert len(sent) == 1
    assert after - before == 6


# --------------------------------------------------------------------------- #
# Never break the caller
# --------------------------------------------------------------------------- #
async def test_a_failed_report_does_not_replace_the_failure(monkeypatch):
    class Unreachable:
        configured = True

        async def send(self, text: str) -> None:
            raise RuntimeError("telegram unreachable")

    # Patched at the *notifier*, not at `_send`. Replacing `_send` would remove the
    # very guard under test and assert only that a monkeypatch takes effect.
    monkeypatch.setattr(errors, "_notifier", lambda: Unreachable())
    errors.reset_for_tests()

    # The whole point: absence of delivery must not turn into a second exception
    # thrown from the error handler.
    await errors.report_exception(ValueError("boom"), where="api")


# --------------------------------------------------------------------------- #
# The two places it attaches
# --------------------------------------------------------------------------- #
async def test_an_unhandled_route_exception_is_reported_and_still_answers_500(
    monkeypatch,
):
    sent = _capture(monkeypatch)

    async def explode():
        raise RuntimeError("dependency exploded")

    app.dependency_overrides[get_current_session_account] = explode
    try:
        # `raise_app_exceptions=False` on purpose: with the default, httpx re-raises
        # what Starlette already turned into a response, and the test would be
        # asserting on the exception rather than on what a caller receives.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ) as c:
            res = await c.get("/api/v1/me")
    finally:
        app.dependency_overrides.clear()

    assert res.status_code == 500
    assert len(sent) == 1
    assert sent[0].startswith("ChmabaPay · ERROR · API request")
    assert "What:    RuntimeError: dependency exploded" in sent[0]
    assert "Call:    GET /api/v1/me" in sent[0]
    # The trace id is bound only for the life of the request, and the report is the
    # one place it can still be quoted — which is why the capture happens before the
    # middleware releases it.
    assert "Trace:   none" not in sent[0]


async def test_a_worker_exception_is_reported_with_its_queue(monkeypatch):
    sent = _capture(monkeypatch)

    class Exploding(Worker):
        async def process(self, job: Job) -> dict:
            raise RuntimeError("detection blew up")

    transport = InProcessTransport()
    # `max_attempts=1` so the job is dead on the first failure and no retry timer
    # outlives the test; the report happens on the failure either way.
    await transport._run_one(
        Exploding(),
        Job(
            queue_name="w1",
            payload={},
            dedup_key="detect:pmt_1",
            max_attempts=1,
        ),
        "w1",
    )

    assert len(sent) == 1
    assert sent[0].startswith("ChmabaPay · ERROR · background worker")
    assert "What:    RuntimeError: detection blew up" in sent[0]
    assert "Worker:  w1" in sent[0]
    assert "Job:     detect:pmt_1 (attempt 1 of 1)" in sent[0]


async def test_a_known_infrastructure_failure_is_explained_not_just_quoted(monkeypatch):
    """`InterfaceError: connection is closed` means nothing to the person paged.

    The type name and the message stay, because they are what a search turns up; what
    is added is the sentence that says the database is gone and everything stops.
    """
    sent = _capture(monkeypatch)

    await errors.report_exception(
        InterfaceError("SELECT 1", None, Exception("connection is closed")),
        where="worker:webhooks.send",
    )

    assert sent[0].startswith("ChmabaPay · OUTAGE · background worker")
    assert "Meaning: The platform lost its connection" in sent[0]
    assert "Worker:  Webhook delivery (webhooks.send)" in sent[0]
