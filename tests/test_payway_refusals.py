"""An ABA refusal has to arrive as an answer, not as a crash.

Two failure modes are pinned here, and they are different problems.

**The refusal escaping as a 500.** `fetch_link_html` calls `raise_for_status()`, so a
403 comes back as an `httpx.HTTPStatusError`. That type was not caught on the QR path,
so it travelled all the way to the API's exception handler: the merchant saw a 500 and
the operator got paged about a traceback instead of a configuration gap. It now becomes
a `PayWayHostedError` carrying what an operator can act on.

**A refusal read as a typo.** `verify_link` treated every 4xx as "PayWay has no link
at that slug". A 403 is ABA refusing *this host's egress* — observed from datacenter
ranges while the same URL answers 200 from an ordinary connection — so the old rule
told a merchant their correct link was wrong, and refused the store.
"""

from __future__ import annotations

import httpx
import pytest

from chmabapay.services import payway_parser

# The `_no_live_aba` fixture in conftest replaces `payway_parser.verify_link` with a
# stub for every test, so the suite never probes the real rail on store-write. These
# tests are *about* that function, so they hold a direct reference to the real one and
# stub one level further down (`fetch_link_html`) instead.
from chmabapay.services.payway_parser import verify_link as real_verify_link

LINK = "https://link.payway.com.kh/ABAPAYpe518710Y"


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", LINK)
    return httpx.HTTPStatusError(
        f"Client error '{status}' for url '{LINK}'",
        request=request,
        response=httpx.Response(status, request=request),
    )


@pytest.fixture
def refusing(monkeypatch):
    """Make the link page answer with whatever status a test asks for."""

    def refuse_with(status: int):
        async def refuse(slug_or_url: str, *, timeout: float = 8.0) -> str:
            raise _status_error(status)

        monkeypatch.setattr(payway_parser, "fetch_link_html", refuse)

    return refuse_with


async def test_a_403_becomes_a_typed_refusal_that_names_the_way_out(refusing):
    refusing(403)

    with pytest.raises(payway_parser.PayWayHostedError) as raised:
        await payway_parser.create_hosted_checkout("ABAPAYpe518710Y", "9.99")

    message = str(raised.value)
    assert "link_page_http_403" in message
    # The remedy, not just the symptom: the operator reading this cannot fix ABA.
    assert "PAYWAY_PROXY_URL" in message


async def test_a_500_upstream_is_still_a_typed_refusal(refusing):
    """A server error is not a configuration mistake, and it must not be a crash."""
    refusing(500)

    with pytest.raises(payway_parser.PayWayHostedError) as raised:
        await payway_parser.create_hosted_checkout("ABAPAYpe518710Y", "9.99")

    assert "link_page_http_500" in str(raised.value)


async def test_verify_link_reads_a_refusal_as_inconclusive_not_missing(refusing):
    """403 is about us; only a 404 is about the merchant's link."""
    refusing(403)

    check = await real_verify_link(LINK)

    assert check.outcome == "inconclusive"
    assert check.verified is False


@pytest.mark.parametrize("status", [404, 410])
async def test_a_missing_link_is_still_not_found(refusing, status):
    """The typo case the check exists for has to keep working."""
    refusing(status)

    check = await real_verify_link(LINK)

    assert check.outcome == "not_found"


async def test_rate_limiting_is_not_a_verdict_on_the_link(refusing):
    """A 429 says we asked too often, which is not evidence the link is wrong."""
    refusing(429)

    check = await real_verify_link(LINK)

    assert check.outcome == "inconclusive"
