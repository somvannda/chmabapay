"""Metrics, trace-correlated logs, and the alerts that page someone (P1-3).

Counter assertions are always expressed as deltas: the Prometheus registry is a
process-wide singleton, so an absolute value would depend on which tests ran
first.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import make_account, make_key, make_store

from chmabapay import alerts as alerting
from chmabapay import observability
from chmabapay.alerts import AlertWatcher, Condition, TelegramNotifier
from chmabapay.config import get_settings
from chmabapay.db import session_factory
from chmabapay.services import payments as payments_svc
from chmabapay.workers import Q_DETECTION, Q_EXPIRY, Q_WEBHOOK, InProcessTransport
from chmabapay.workers.base import Worker
from chmabapay.workers.job import Job

TRACE_ID = "6f1c0f7a-6e1e-4f4e-8f3f-1a2b3c4d5e6f"


# --------------------------------------------------------------------------- #
# Reading the exposition text
# --------------------------------------------------------------------------- #
def rendered() -> str:
    body, content_type = observability.render_metrics()
    assert content_type.startswith("text/plain")
    return body.decode()


def sample(text: str, name: str, **labels: str) -> float:
    """One sample out of the rendered metrics, or 0.0 when it is absent."""
    for line in text.splitlines():
        if not line.startswith(name):
            continue
        rest = line[len(name) :]
        if rest[:1] not in ("{", " "):
            continue  # `_bucket` / `_count` / `_sum` of a longer name
        if not all(f'{key}="{value}"' in line for key, value in labels.items()):
            continue
        return float(line.rsplit(" ", 1)[1])
    return 0.0


# --------------------------------------------------------------------------- #
# Trace-correlated logs
# --------------------------------------------------------------------------- #
def test_the_log_format_carries_the_trace_id_of_the_current_request():
    record = logging.LogRecord(
        name="chmabapay.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="payment confirmed", args=(), exc_info=None,
    )
    token = observability.bind_trace_id(TRACE_ID)
    try:
        assert observability.TraceIdFilter().filter(record) is True
        assert TRACE_ID in logging.Formatter(observability.LOG_FORMAT).format(record)
    finally:
        observability.release_trace_id(token)


def test_a_record_outside_a_request_says_so_rather_than_inventing_an_id():
    """Two calls used to return two different uuids, which correlates nothing."""
    record = logging.LogRecord(
        name="chmabapay.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="worker tick", args=(), exc_info=None,
    )
    observability.TraceIdFilter().filter(record)
    assert record.trace_id == "-"
    assert observability.current_trace_id() == "-"


async def test_a_request_binds_its_trace_id_for_the_logs_inside_it(
    client, monkeypatch
):
    bound: list[str] = []
    real = observability.bind_trace_id

    def spy(trace_id: str):
        bound.append(trace_id)
        return real(trace_id)

    monkeypatch.setattr(observability, "bind_trace_id", spy)

    response = await client.get("/health", headers={"X-ChmabaPay-Trace": TRACE_ID})
    assert response.headers["X-ChmabaPay-Trace"] == TRACE_ID
    assert bound == [TRACE_ID]


def test_configure_logging_installs_one_handler_and_is_idempotent():
    observability.configure_logging()
    observability.configure_logging()

    logger = logging.getLogger(observability.APP_LOGGER)
    assert len(logger.handlers) == 1
    assert any(
        isinstance(f, observability.TraceIdFilter) for f in logger.handlers[0].filters
    )


# --------------------------------------------------------------------------- #
# HTTP metrics
# --------------------------------------------------------------------------- #
async def test_every_request_is_counted_by_route_and_status(client):
    text = rendered()
    before = sample(
        text, "chmabapay_http_requests_total", method="GET", route="/health", status="200"
    )

    assert (await client.get("/health")).status_code == 200

    after = sample(
        rendered(),
        "chmabapay_http_requests_total",
        method="GET",
        route="/health",
        status="200",
    )
    assert after == before + 1


async def test_latency_is_recorded_for_the_route(client):
    before = sample(rendered(), "chmabapay_http_request_duration_seconds_count", route="/health")
    await client.get("/health")
    after = sample(
        rendered(), "chmabapay_http_request_duration_seconds_count", route="/health"
    )
    assert after == before + 1


async def test_the_route_label_is_the_template_never_the_identifier(client):
    """A label per URL is unbounded, and a payment id in a label is an outage."""
    await client.get("/pay/pay_aaaaaaaaaaaaaaaaaaaa")
    await client.get("/pay/pay_bbbbbbbbbbbbbbbbbbbb")

    text = rendered()
    assert 'route="/pay/{public_id}"' in text
    assert "pay_aaaaaaaaaaaaaaaaaaaa" not in text
    assert "pay_bbbbbbbbbbbbbbbbbbbb" not in text


async def test_a_request_the_limiter_short_circuits_is_still_counted(client, monkeypatch):
    """A 429 never reaches a route, so it lands in the unmatched bucket by design."""
    monkeypatch.setattr(
        get_settings(), "rate_limit_checkout_per_minute", 1, raising=False
    )

    await client.get("/pay/whatever")
    refused = await client.get("/pay/whatever")
    assert refused.status_code == 429

    assert (
        sample(
            rendered(),
            "chmabapay_http_requests_total",
            method="GET",
            route="<unmatched>",
            status="429",
        )
        > 0
    )


# --------------------------------------------------------------------------- #
# Payment metrics
# --------------------------------------------------------------------------- #
async def test_a_settled_payment_increments_created_and_paid(client):
    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created_before = sample(rendered(), "chmabapay_payment_events_total", event="created")
    paid_before = sample(rendered(), "chmabapay_payment_events_total", event="paid")
    settled_before = sample(rendered(), "chmabapay_payment_settlement_seconds_count")

    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 2.5, "hosted_qr": False},
            headers=headers,
        )
    ).json()

    async with session_factory() as session:
        assert await payments_svc.mark_paid(session, created["id"], bakong_ref="ref-1")

    text = rendered()
    assert sample(text, "chmabapay_payment_events_total", event="created") == (
        created_before + 1
    )
    assert sample(text, "chmabapay_payment_events_total", event="paid") == paid_before + 1
    assert (
        sample(text, "chmabapay_payment_settlement_seconds_count") == settled_before + 1
    )


async def test_expiring_payments_counts_them(client):
    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post(
            "/v1/payments", json={"amount": 1.0, "hosted_qr": False}, headers=headers
        )
    ).json()

    before = sample(rendered(), "chmabapay_payment_events_total", event="expired")

    async with session_factory() as session:
        payment = await payments_svc.get_payment_for_store(session, created["id"])
        payment.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        assert await payments_svc.expire_due_payments(session)

    assert sample(rendered(), "chmabapay_payment_events_total", event="expired") == (
        before + 1
    )


def test_settlement_time_survives_a_naive_timestamp():
    """SQLite hands back naive datetimes for tz-aware columns; Postgres never does.

    Subtracting one from the other raises, so this would work in production and
    blow up in the test suite — or the reverse, which is worse.
    """
    before = sample(rendered(), "chmabapay_payment_settlement_seconds_count")
    observability.observe_settlement(
        datetime.now(UTC) - timedelta(seconds=30),  # naive, as SQLite returns it
        datetime.now(UTC),
    )
    assert sample(rendered(), "chmabapay_payment_settlement_seconds_count") == before + 1


def test_settlement_time_ignores_an_unpaid_payment():
    before = sample(rendered(), "chmabapay_payment_settlement_seconds_count")
    observability.observe_settlement(datetime.now(UTC), None)
    assert sample(rendered(), "chmabapay_payment_settlement_seconds_count") == before


# --------------------------------------------------------------------------- #
# Queue metrics
# --------------------------------------------------------------------------- #
async def test_queue_depth_is_the_current_backlog_not_the_lifetime_total():
    """The regression this metric shipped with.

    `metrics()` spread the cumulative counters last, so `pending` reported every
    job ever enqueued. A backlog alert built on that would fire hardest exactly
    when the workers were keeping up.
    """
    transport = InProcessTransport()
    for _ in range(3):
        await transport.enqueue(Q_WEBHOOK, {"type": "fanout_scan"})

    assert (await transport.metrics())[Q_WEBHOOK]["pending"] == 3

    for _ in range(3):
        await transport.dequeue(Q_WEBHOOK, n=1, timeout=0.01)

    counts = (await transport.metrics())[Q_WEBHOOK]
    assert counts["pending"] == 0
    assert counts["enqueued"] == 3


async def test_the_drain_loop_reports_a_heartbeat():
    transport = InProcessTransport()
    assert await transport.heartbeats() == {}

    await transport.dequeue(Q_DETECTION, n=1, timeout=0.01)

    assert Q_DETECTION in await transport.heartbeats()


async def test_a_queue_with_no_jobs_still_reports_its_depth():
    transport = InProcessTransport()
    await transport.dequeue(Q_EXPIRY, n=1, timeout=0.01)
    assert (await transport.metrics())[Q_EXPIRY]["pending"] == 0


async def test_the_metrics_endpoint_publishes_the_queue_figures(client):
    from chmabapay.main import app

    transport = InProcessTransport()
    await transport.enqueue(Q_WEBHOOK, {"type": "fanout_scan"})
    await transport.dequeue(Q_WEBHOOK, n=1, timeout=0.01)
    previous = getattr(app.state, "worker_transport", None)
    app.state.worker_transport = transport
    try:
        response = await client.get("/metrics")
    finally:
        app.state.worker_transport = previous

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert f'chmabapay_queue_pending{{queue="{Q_WEBHOOK}"}} 0.0' in body
    assert f'chmabapay_worker_heartbeat_age_seconds{{queue="{Q_WEBHOOK}"}}' in body


async def test_metrics_works_without_a_worker_transport(client):
    """Workers off is a normal deployment; a scrape must not 500."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "chmabapay_http_requests_total" in response.text


async def test_metrics_can_be_gated_behind_a_token(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "s3cret", raising=False)

    assert (await client.get("/metrics")).status_code == 401
    assert (
        await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    ).status_code == 401
    allowed = await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert allowed.status_code == 200


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
class FakeTransport:
    """Whatever the watcher should see, without a worker loop in the way."""

    def __init__(self, *, heartbeats=None, metrics=None) -> None:
        self._heartbeats = heartbeats or {}
        self._metrics = metrics or {}

    async def heartbeats(self) -> dict[str, float]:
        return dict(self._heartbeats)

    async def metrics(self) -> dict:
        return dict(self._metrics)


class RecordingNotifier(TelegramNotifier):
    def __init__(self, chat_id: str | None = "ops-chat") -> None:
        super().__init__(chat_id)
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        if not self.chat_id:
            return False
        self.sent.append(text)
        return True


def make_watcher(
    transport,
    *,
    queues=(Q_DETECTION, Q_WEBHOOK),
    notifier=None,
    interval=30.0,
    stall=60.0,
    backlog=50,
    delivery_outcomes=None,
    failure_rate=0.1,
    failure_min_sample=20,
) -> AlertWatcher:
    return AlertWatcher(
        transport=transport,
        queues=list(queues),
        notifier=notifier or RecordingNotifier(),
        interval_seconds=interval,
        stall_seconds=stall,
        backlog_threshold=backlog,
        delivery_outcomes=delivery_outcomes,
        failure_rate=failure_rate,
        failure_min_sample=failure_min_sample,
    )


def outcomes_are(failed: int, total: int):
    """A stand-in for the database read behind the failure-rate condition."""

    async def _outcomes(_window_seconds: float) -> tuple[int, int]:
        return failed, total

    return _outcomes


def healthy_queue() -> FakeTransport:
    """Both queues draining, nothing backing up — the slow-failure scenario."""
    now = time.monotonic()
    return FakeTransport(
        heartbeats={Q_DETECTION: now, Q_WEBHOOK: now}, metrics={Q_WEBHOOK: {"pending": 0}}
    )


async def test_a_queue_that_never_started_is_the_loudest_condition():
    """Invisible to a check that only looks at queues already reporting."""
    watcher = make_watcher(FakeTransport(heartbeats={}, metrics={}))

    conditions = await watcher.evaluate()

    assert len(conditions) == 2
    assert all(c.key.startswith("worker_stalled:") for c in conditions)
    assert any("never started" in c.summary for c in conditions)


async def test_a_stale_heartbeat_is_reported_but_a_fresh_one_is_not():
    now = time.monotonic()
    fresh = make_watcher(
        FakeTransport(heartbeats={Q_DETECTION: now, Q_WEBHOOK: now}, metrics={})
    )
    assert await fresh.evaluate() == []

    stalled = make_watcher(
        FakeTransport(
            heartbeats={Q_DETECTION: now - 120, Q_WEBHOOK: now - 1}, metrics={}
        )
    )
    conditions = await stalled.evaluate()
    assert [c.key for c in conditions] == [f"worker_stalled:{Q_DETECTION}"]


@pytest.mark.parametrize(
    ("detection_age", "webhook_age", "expected"),
    [
        (0.0, 0.0, []),
        (59.0, 0.0, []),  # just inside the 60s threshold
        (61.0, 0.0, [Q_DETECTION]),  # just outside it
        (900.0, 900.0, [Q_DETECTION, Q_WEBHOOK]),
    ],
)
async def test_the_stall_threshold_decides_from_both_sides(
    detection_age, webhook_age, expected
):
    now = time.monotonic()
    watcher = make_watcher(
        FakeTransport(
            heartbeats={Q_DETECTION: now - detection_age, Q_WEBHOOK: now - webhook_age},
            metrics={},
        )
    )

    conditions = await watcher.evaluate()
    assert [c.key for c in conditions] == [f"worker_stalled:{q}" for q in expected]


async def test_a_webhook_backlog_is_reported_with_the_depth():
    watcher = make_watcher(
        FakeTransport(
            heartbeats={Q_DETECTION: time.monotonic(), Q_WEBHOOK: time.monotonic()},
            metrics={Q_WEBHOOK: {"pending": 120}},
        ),
        backlog=50,
    )

    conditions = await watcher.evaluate()
    assert [c.key for c in conditions] == ["webhook_backlog"]
    assert "120 deliveries waiting on webhook" in conditions[0].detail


async def test_each_condition_is_sent_once_on_its_edge_and_once_on_recovery():
    notifier = RecordingNotifier()
    transport = FakeTransport(
        heartbeats={Q_DETECTION: time.monotonic() - 120, Q_WEBHOOK: time.monotonic()},
        metrics={},
    )
    watcher = make_watcher(transport, notifier=notifier)

    await watcher.check_once()
    await watcher.check_once()
    await watcher.check_once()
    # Still down after a minute: one message, not one per tick.
    assert len(notifier.sent) == 1
    assert notifier.sent[0].startswith("ChmabaPay · ALERT · worker stalled")
    # The queue is named in words as well as by its transport id, so the reader does
    # not have to know what `payments.detection` is to triage it.
    assert "Payment detection (payments.detection)" in notifier.sent[0]

    transport._heartbeats[Q_DETECTION] = time.monotonic()
    await watcher.check_once()
    assert notifier.sent[-1].startswith("ChmabaPay · RESOLVED · worker stalled")
    assert "has cleared after" in notifier.sent[-1]

    # And it does not announce the recovery twice either.
    await watcher.check_once()
    assert len(notifier.sent) == 2


async def test_the_alert_counter_moves_when_a_condition_fires():
    condition = f"worker_stalled:{Q_EXPIRY}"
    # Read the specific condition: an unlabelled read returns whichever sample
    # happens to come first, which is a different condition's number.
    before = sample(rendered(), "chmabapay_alerts_raised_total", condition=condition)

    watcher = make_watcher(
        FakeTransport(heartbeats={}, metrics={}), queues=(Q_EXPIRY,)
    )
    await watcher.check_once()

    after = sample(rendered(), "chmabapay_alerts_raised_total", condition=condition)
    assert after == before + 1


async def test_a_slowly_failing_webhook_rail_pages_even_though_the_queue_is_moving():
    """The condition the backlog alert cannot see.

    Every delivery is being attempted and the queue depth is zero — the rail is
    working, and failing. Nothing else on the platform notices.
    """
    watcher = make_watcher(
        healthy_queue(), delivery_outcomes=outcomes_are(30, 100)
    )

    conditions = await watcher.evaluate()

    assert [c.key for c in conditions] == ["webhook_failure_rate"]
    assert "30 of 100 delivery attempts failed" in conditions[0].detail
    assert "queue is moving" in conditions[0].detail


@pytest.mark.parametrize(
    ("failed", "total", "expected"),
    [
        (0, 40, []),  # nothing failing
        (1, 5, []),  # 20 %, but five attempts is not a rate — it is one bad night
        (10, 100, []),  # exactly at the threshold: the condition is "more than"
        (11, 100, ["webhook_failure_rate"]),  # just past it
        (19, 20, ["webhook_failure_rate"]),
        (0, 0, []),  # nothing to measure is not a failure rate
    ],
)
async def test_the_failure_rate_threshold_decides_from_both_sides(failed, total, expected):
    watcher = make_watcher(healthy_queue(), delivery_outcomes=outcomes_are(failed, total))

    conditions = await watcher.evaluate()

    assert [c.key for c in conditions] == expected


async def test_a_quiet_account_with_one_bad_delivery_does_not_page():
    """The sample floor, stated on its own because it is the whole defence."""
    watcher = make_watcher(
        healthy_queue(),
        delivery_outcomes=outcomes_are(1, 1),
        failure_min_sample=20,
    )

    assert await watcher.evaluate() == []


async def test_a_failure_rate_that_cannot_be_measured_neither_pages_nor_clears():
    notifier = RecordingNotifier()
    calls = {"n": 0}

    async def flaky(_window_seconds: float) -> tuple[int, int]:
        calls["n"] += 1
        if calls["n"] == 1:
            return 30, 100
        raise RuntimeError("database is down")

    watcher = make_watcher(
        healthy_queue(), notifier=notifier, delivery_outcomes=flaky
    )

    await watcher.check_once()
    assert notifier.sent[0].startswith("ChmabaPay · ALERT · webhooks failing")

    # The database cannot answer. That is not evidence that the rail recovered.
    await watcher.check_once()
    assert notifier.sent == [notifier.sent[0]]

    # And when it can answer again, and the rail is healthy, it clears once.
    async def healthy(_window_seconds: float) -> tuple[int, int]:
        return 0, 120

    watcher.delivery_outcomes = healthy
    await watcher.check_once()
    assert notifier.sent[-1].startswith("ChmabaPay · RESOLVED · webhooks failing")


async def test_the_failure_rate_is_not_consulted_when_no_reader_is_supplied():
    watcher = make_watcher(healthy_queue())

    assert await watcher.evaluate() == []


async def test_a_worker_loop_that_dies_is_caught_by_its_heartbeat():
    """The chaos test: cancel the drain loop and the watcher notices."""

    class Noop(Worker):
        async def process(self, job: Job) -> dict:
            return {}

    transport = InProcessTransport()
    stop = asyncio.Event()
    loop = asyncio.create_task(
        transport.run_workers({Q_DETECTION: Noop()}, stop), name="chaos-dispatcher"
    )
    await asyncio.sleep(0.05)
    assert Q_DETECTION in await transport.heartbeats()

    loop.cancel()
    await asyncio.gather(loop, return_exceptions=True)

    watcher = make_watcher(transport, queues=(Q_DETECTION,), stall=0.0, notifier=RecordingNotifier())
    conditions = await watcher.evaluate()

    assert [c.key for c in conditions] == [f"worker_stalled:{Q_DETECTION}"]
    assert isinstance(conditions[0], Condition)


async def test_an_undeliverable_alert_reports_failure_rather_than_pretending():
    notifier = TelegramNotifier(chat_id=None)
    assert notifier.configured is False
    assert await notifier.send("ALERT something") is False

    watcher = make_watcher(FakeTransport(heartbeats={}, metrics={}), notifier=notifier)
    # The watcher keeps running; it just cannot deliver.
    assert await watcher.check_once()


# --------------------------------------------------------------------------- #
# Configured is not the same as allowed to speak
# --------------------------------------------------------------------------- #
# The regression this guards was real: the production token was removed from the dev
# `.env` and alerts still arrived, because the launcher exported settings into the process
# environment. Credentials are not a deployment's permission to page.
def _config(**overrides):
    """Only the settings `delivery_allowed` reads, without the rest of `Settings`."""
    base = {
        "enable_dev_gateway": False,
        "public_origin": "https://pay.chmaba.com",
        "alerts_allow_non_production": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_production_deployment_may_page(monkeypatch):
    monkeypatch.setattr(alerting, "get_settings", lambda: _config())
    assert alerting.delivery_allowed() is True
    assert alerting.development_reason() is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"enable_dev_gateway": True},
        {"public_origin": "http://localhost:3001"},
        {"public_origin": "http://127.0.0.1:8000"},
    ],
)
def test_a_development_deployment_is_not_allowed_to_page(monkeypatch, overrides):
    monkeypatch.setattr(alerting, "get_settings", lambda: _config(**overrides))
    assert alerting.delivery_allowed() is False
    assert alerting.development_reason() is not None


def test_an_unknown_origin_does_not_silence_a_production_page(monkeypatch):
    """An empty `PUBLIC_ORIGIN` is unknown, not evidence: fail toward alerting."""
    monkeypatch.setattr(alerting, "get_settings", lambda: _config(public_origin=None))
    assert alerting.delivery_allowed() is True


def test_the_override_lets_a_development_deployment_page_on_purpose(monkeypatch):
    monkeypatch.setattr(
        alerting,
        "get_settings",
        lambda: _config(
            public_origin="http://localhost:3001", alerts_allow_non_production=True
        ),
    )
    assert alerting.delivery_allowed() is True


async def test_the_guard_stops_a_message_before_it_reaches_telegram(monkeypatch):
    """The credentials are present and it still must not send. That is the point."""
    sent: list[str] = []

    async def fake_send(chat_id: str, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(alerting.telegram, "send_message", fake_send)
    monkeypatch.setattr(alerting.telegram, "is_configured", lambda: True)
    monkeypatch.setattr(
        alerting, "get_settings", lambda: _config(public_origin="http://localhost:3001")
    )

    notifier = TelegramNotifier("ops-chat")

    assert await notifier.send("ChmabaPay · ALERT · test") is False
    assert sent == []
