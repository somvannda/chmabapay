"""The hosted-checkout endpoint moved once without notice; this is the alarm.

On 2026-10-24 ``pwapp.ababank.com/api`` began answering 403 and the PayWay page's
own build had moved to ``link.payway.com.kh/api``. Both the mint and the status
call read that base, so the move broke QR generation and left issued sessions
unpollable until a merchant reported it. These tests pin the probe that is meant
to find the next move first: what counts as "the endpoint exists", what counts as
"it moved", and how the new base is found and named.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest

from chmabapay.alerts import AlertWatcher, Condition
from chmabapay.services import payway_endpoint_probe, payway_parser
from chmabapay.services.payway_endpoint_probe import (
    PaywayEndpointProbe,
    check_payway_endpoint,
    condition_for,
    discover_hosted_api_bases,
    extract_api_bases,
    probe_endpoint,
)
from chmabapay.services.payway_parser import PAYWAY_BASE
from chmabapay.workers import Q_DETECTION, Q_WEBHOOK

CONFIGURED = "https://old.example/api"
NEW_BASE = "https://new.example/api"
STATUS_PATH = "/pw-app/v1/payment-link/check-payment-status"

BUNDLE = 'window.__NUXT__.config={API_BASE_PUBLIC_URL:"https://new.example/api"};'


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_extract_api_bases_handles_both_bundle_shapes() -> None:
    assert extract_api_bases('API_BASE_PUBLIC_URL="https://a.example/api"') == [
        "https://a.example/api"
    ]
    assert extract_api_bases('{"API_BASE_PUBLIC_URL":"https://b.example/api"}') == [
        "https://b.example/api"
    ]
    assert extract_api_bases("no constant here") == []


async def test_a_validation_refusal_proves_the_endpoint_exists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CONFIGURED + STATUS_PATH
        return httpx.Response(400, json={"message": "client_id is required"})

    probe = await probe_endpoint(CONFIGURED, client=_client(handler))

    assert probe.outcome == "answers"
    assert probe.healthy
    assert probe.latency_ms is not None


async def test_a_json_auth_refusal_is_the_route_answering_not_the_edge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "UNAUTHORIZED", "message": "token"})

    probe = await probe_endpoint(CONFIGURED, client=_client(handler))

    assert probe.outcome == "answers"
    assert probe.healthy


async def test_the_configured_base_answering_is_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "missing"})

    verdict = await check_payway_endpoint(configured_base=CONFIGURED, client=_client(handler))

    assert verdict.status == "ok"
    assert verdict.healthy
    assert condition_for(verdict, configured_base=CONFIGURED) is None


async def test_a_moved_endpoint_is_found_in_the_pages_own_bundle() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append((request.method, url))
        if request.method == "POST" and url == CONFIGURED + STATUS_PATH:
            return httpx.Response(404, text="Not Found")
        if request.method == "GET" and url == PAYWAY_BASE:
            return httpx.Response(
                200, text='<html><script src="/_nuxt/app.js"></script></html>'
            )
        if request.method == "GET" and url == PAYWAY_BASE + "/_nuxt/app.js":
            return httpx.Response(200, text=BUNDLE)
        if request.method == "POST" and url == NEW_BASE + STATUS_PATH:
            return httpx.Response(400, json={"message": "client_id is required"})
        return httpx.Response(500)

    verdict = await check_payway_endpoint(configured_base=CONFIGURED, client=_client(handler))

    assert verdict.status == "moved"
    assert verdict.working == NEW_BASE
    assert NEW_BASE in verdict.discovered

    condition = condition_for(verdict, configured_base=CONFIGURED)
    assert condition is not None
    assert condition.key == "payway_hosted_endpoint"
    assert condition.summary == "ABA moved the hosted checkout API"
    assert NEW_BASE in condition.detail
    assert "PAYWAY_HOSTED_API_BASE" in condition.detail
    assert "no QR can be minted" in condition.impact
    assert ("GET", PAYWAY_BASE) in seen


async def test_a_redirect_names_the_new_base_without_crawling_the_page() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append((request.method, url))
        if request.method == "POST" and url == CONFIGURED + STATUS_PATH:
            return httpx.Response(301, headers={"location": NEW_BASE + STATUS_PATH})
        if request.method == "POST" and url == NEW_BASE + STATUS_PATH:
            return httpx.Response(405)
        return httpx.Response(500)

    verdict = await check_payway_endpoint(configured_base=CONFIGURED, client=_client(handler))

    assert verdict.status == "moved"
    assert verdict.working == NEW_BASE
    assert all(method == "POST" for method, _ in seen), "a redirect should not crawl the page"


async def test_a_403_that_hides_a_move_is_reported_as_one() -> None:
    """The 2026-10-24 shape: the old base 403s while the page names one that answers.

    A move and a datacenter refusal look identical on the wire (both are nginx's
    HTML 403), so the page bundle is the only thing that separates them. Reporting
    this as "set PAYWAY_PROXY_URL" would send the operator down the wrong path.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url == CONFIGURED + STATUS_PATH:
            return httpx.Response(403, text="<html>nginx</html>")
        if request.method == "GET" and url == PAYWAY_BASE:
            return httpx.Response(200, text=f"<html><script>{BUNDLE}</script></html>")
        if request.method == "POST" and url == NEW_BASE + STATUS_PATH:
            return httpx.Response(400, json={"message": "client_id is required"})
        return httpx.Response(404)

    verdict = await check_payway_endpoint(configured_base=CONFIGURED, client=_client(handler))

    assert verdict.status == "moved"
    assert verdict.working == NEW_BASE
    condition = condition_for(verdict, configured_base=CONFIGURED)
    assert condition is not None
    assert NEW_BASE in condition.detail
    assert "PAYWAY_PROXY_URL" not in condition.detail


async def test_a_network_refusal_names_the_proxy_escape_hatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>nginx</html>")

    verdict = await check_payway_endpoint(configured_base=CONFIGURED, client=_client(handler))

    assert verdict.status == "refused"
    condition = condition_for(verdict, configured_base=CONFIGURED)
    assert condition is not None
    assert "PAYWAY_PROXY_URL" in condition.detail
    assert "403" in condition.detail


async def test_everything_dead_is_down_and_says_no_candidate_was_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    verdict = await check_payway_endpoint(configured_base=CONFIGURED, client=_client(handler))

    assert verdict.status == "down"
    assert verdict.discovered == []
    condition = condition_for(verdict, configured_base=CONFIGURED)
    assert condition is not None
    assert "none found" in condition.detail


async def test_discovery_returns_nothing_when_the_link_page_is_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    assert await discover_hosted_api_bases(client=_client(handler)) == []


async def test_the_probe_is_clamped_to_its_interval() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(400)

    probe = PaywayEndpointProbe(base_url=CONFIGURED, interval_seconds=1800)
    # Inject the mocked client into the one network call the probe makes.
    async def mocked_check(**kwargs):
        return await check_payway_endpoint(client=_client(handler), **kwargs)

    original = payway_endpoint_probe.check_payway_endpoint
    payway_endpoint_probe.check_payway_endpoint = mocked_check
    try:
        assert await probe.conditions() == []
        assert await probe.conditions() == []
        assert len(calls) == 1, "a second watcher tick must reuse the cached verdict"
        probe.interval_seconds = 0
        await probe.conditions()
        assert len(calls) == 2
    finally:
        payway_endpoint_probe.check_payway_endpoint = original


@pytest.fixture
def parser_calls(monkeypatch):
    """Capture the URL the parser posts to, with no network."""
    seen: dict[str, str] = {}

    async def fetch_link_html(slug_or_url: str, *, timeout: float = 8.0) -> str:
        return '<html>window.__NUXT__=(aba_data="abc" request_time="1700000000")</html>'

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "qr_string": "000201010212",
                "client_id": "cid",
                "token": "tok",
                "status": {"code": "00", "message": "Success"},
            },
        )

    def client(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(payway_parser, "fetch_link_html", fetch_link_html)
    monkeypatch.setattr(payway_parser, "_client", client)
    return seen


def test_the_mint_follows_the_configured_base(monkeypatch, parser_calls) -> None:
    monkeypatch.setattr(
        payway_parser,
        "get_settings",
        lambda: SimpleNamespace(
            payway_hosted_api_base="https://override.example/api",
            payway_proxy_url=None,
        ),
    )

    async def run() -> None:
        await payway_parser.create_hosted_checkout("ABAPAYpe518710Y", "1.00")

    import asyncio

    asyncio.run(run())

    assert parser_calls["url"] == (
        "https://override.example/api"
        "/pw-app/v1/payment/gateway/list-payment-options"
    )


class _Transport:
    """The two reads the watcher performs, with healthy values."""

    async def heartbeats(self) -> dict[str, float]:
        return {Q_DETECTION: time.monotonic()}

    async def metrics(self) -> dict[str, dict[str, int]]:
        return {Q_WEBHOOK: {"pending": 0}}


class _Notifier:
    configured = True

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


def _watcher(conditions: list[Condition], notifier: _Notifier) -> AlertWatcher:
    async def external() -> list[Condition]:
        return conditions

    return AlertWatcher(
        transport=_Transport(),
        queues=[Q_DETECTION],
        notifier=notifier,
        interval_seconds=30,
        stall_seconds=60,
        backlog_threshold=50,
        external_conditions=external,
    )


async def test_the_watcher_reports_the_move_once_and_then_the_recovery() -> None:
    notifier = _Notifier()
    condition = Condition(
        key="payway_hosted_endpoint",
        summary="ABA moved the hosted checkout API",
        detail=f"{CONFIGURED} no longer answers; the page points at {NEW_BASE}.",
        impact="no QR can be minted",
    )
    current: list[Condition] = []
    watcher = _watcher(current, notifier)

    await watcher.check_once()
    assert notifier.sent == []

    current.append(condition)
    await watcher.check_once()
    await watcher.check_once()
    assert len(notifier.sent) == 1
    assert "ABA moved the hosted checkout API" in notifier.sent[0]
    assert "no QR can be minted" in notifier.sent[0]

    current.clear()
    await watcher.check_once()
    assert len(notifier.sent) == 2
    assert "RESOLVED" in notifier.sent[1]


async def test_a_failed_probe_keeps_the_previous_alert_open() -> None:
    """A network blip in the probe must not read as "the endpoint came back"."""
    notifier = _Notifier()
    condition = Condition(
        key="payway_hosted_endpoint",
        summary="ABA hosted checkout unreachable",
        detail="no answer",
    )
    failing = {"raise": False}

    async def external() -> list[Condition]:
        if failing["raise"]:
            raise RuntimeError("probe crashed")
        return [condition]

    watcher = AlertWatcher(
        transport=_Transport(),
        queues=[Q_DETECTION],
        notifier=notifier,
        interval_seconds=30,
        stall_seconds=60,
        backlog_threshold=50,
        external_conditions=external,
    )
    await watcher.check_once()
    assert len(notifier.sent) == 1

    failing["raise"] = True
    await watcher.check_once()
    assert len(notifier.sent) == 1, "a probe that cannot run must not send RESOLVED"
