"""The hosted-checkout API base is ABA's, and it moves without notice.

On 2026-10-24 the host that had served every mint and every status check
(``pwapp.ababank.com/api``) started answering with nginx's plain-text 403 for any
request — the same body and hash that the link page itself sends. The link page's
own build had moved to ``link.payway.com.kh/api``, which accepts the identical
payload, so the fix was a one-line constant change. Both calls share that constant,
which is why a single move broke QR generation *and* left issued sessions
unpollable.

These tests pin the base by observing the URL the code actually calls, rather than
asserting the constant against itself, so a future move fails here instead of in
production as a 502. Re-derive the value from the page bundle (search for
``API_BASE_PUBLIC_URL``) and update this file with it.
"""

from __future__ import annotations

import httpx
import pytest

from chmabapay.services import payway_parser

# Just enough of the SSR payload for `extract_link_state` to find both fields.
LINK_PAGE = '<html>window.__NUXT__=(aba_data="abc" request_time="1700000000")</html>'

CHECKOUT_BODY = {
    "qr_string": "000201010212",
    "client_id": "cid",
    "token": "tok",
    "status": {"code": "00", "message": "Success"},
}


@pytest.fixture
def calls(monkeypatch):
    """Capture the outbound URL, with no network and no live ABA."""
    seen: dict[str, str] = {}

    async def fetch_link_html(slug_or_url: str, *, timeout: float = 8.0) -> str:
        return LINK_PAGE

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=CHECKOUT_BODY)

    def client(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(payway_parser, "fetch_link_html", fetch_link_html)
    monkeypatch.setattr(payway_parser, "_client", client)
    return seen


async def test_the_checkout_is_minted_against_the_link_pages_own_api(calls):
    await payway_parser.create_hosted_checkout("ABAPAYpe518710Y", "1.00")

    assert calls["url"] == (
        "https://link.payway.com.kh/api"
        "/pw-app/v1/payment/gateway/list-payment-options"
    )
    assert "pwapp.ababank.com" not in calls["url"]


async def test_the_status_check_uses_the_same_base(calls):
    """A move must not fix minting while silently stranding issued sessions."""
    await payway_parser.fetch_hosted_status(
        client_id="2364634-518710-32966040",
        request_time="20261022095234",
        token="tok",
    )

    assert calls["url"] == (
        "https://link.payway.com.kh/api"
        "/pw-app/v1/payment-link/check-payment-status"
    )
