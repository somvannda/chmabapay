"""Attaching a payment destination to a store.

A store's link is where its money goes. It used to be written with no check and
stamped `verified`, so a mistyped slug produced a store that read "active" and
failed at the merchant's first customer. These tests cover the three answers PayWay
can give, and what each one is allowed to do to a store.
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from conftest import make_account, make_key
from sqlalchemy import select

from chmabapay import models
from chmabapay.db import session_factory
from chmabapay.main import app
from chmabapay.services import payway_parser


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


PAYWAY_LINK = "https://link.payway.com.kh/ABAPAYpe518710Y"


def _slug_of(raw_link: str) -> str:
    """The last path segment, which is the merchant account id PayWay resolves."""
    trimmed = raw_link.split("?")[0].split("#")[0].rstrip("/")
    return trimmed.rsplit("/", 1)[-1] or trimmed


def probe_returning(
    monkeypatch,
    outcome: str,
    *,
    merchant_name: str | None = "Sokha Cafe",
    calls: list[str] | None = None,
):
    """Replace the PayWay link probe with a fixed answer.

    The autouse `_no_live_aba` fixture already keeps this suite off the network with
    an `ok` answer; this is how a test asks for the other two outcomes, which are the
    ones with behaviour attached.
    """

    async def fake(raw_link: str, *, timeout: float = 6.0):
        if calls is not None:
            calls.append(raw_link)
        return payway_parser.PayWayLinkCheck(
            outcome=outcome,
            slug=payway_parser._extract_slug(raw_link),
            merchant_name=merchant_name if outcome == "ok" else None,
            detail=None if outcome == "ok" else "stubbed",
        )

    monkeypatch.setattr(payway_parser, "verify_link", fake)


async def _store_payload(client, raw_link: str, **link_extra):
    account = await make_account()
    raw_key, _ = await make_key(account)
    return (
        account,
        {"Authorization": f"Bearer {raw_key}"},
        {
            "name": "Sokha Cafe",
            "link": {
                "raw_link": raw_link,
                "merchant_account_id": _slug_of(raw_link),
                **link_extra,
            },
        },
    )


async def link_row(store_public_id: str) -> models.PaymentLink:
    async with session_factory() as session:
        store_id = (
            await session.execute(
                select(models.Store.id).where(
                    models.Store.public_id == store_public_id
                )
            )
        ).scalar_one()
        return (
            await session.execute(
                select(models.PaymentLink).where(
                    models.PaymentLink.store_id == store_id
                )
            )
        ).scalar_one()


async def test_a_link_payway_confirms_activates_the_store(client, monkeypatch):
    probe_returning(monkeypatch, "ok", merchant_name="Sokha Cafe")
    _account, headers, payload = await _store_payload(client, PAYWAY_LINK)

    res = await client.post("/api/v1/stores", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    store = res.json()
    assert store["status"] == "active"
    assert store["link"]["verification"] == models.LINK_VERIFIED
    # The caller left the merchant name blank, so PayWay's own answer fills it in
    # rather than leaving the payer looking at nothing in their banking app.
    assert store["link"]["merchant_name"] == "Sokha Cafe"


async def test_the_callers_merchant_name_wins_over_the_probe(client, monkeypatch):
    """It is what the payer sees, and the merchant knows their outlet."""
    probe_returning(monkeypatch, "ok", merchant_name="PROBED NAME PVT LTD")
    _account, headers, payload = await _store_payload(
        client, PAYWAY_LINK, merchant_name="Sokha Cafe"
    )

    res = await client.post("/api/v1/stores", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    assert res.json()["link"]["merchant_name"] == "Sokha Cafe"


async def test_a_link_payway_does_not_know_is_refused(client, monkeypatch):
    """The typo case, and the reason this check exists at all."""
    probe_returning(monkeypatch, "not_found")
    _account, headers, payload = await _store_payload(
        client, "https://link.payway.com.kh/ABAPAYpe518710X"
    )

    res = await client.post("/api/v1/stores", json=payload, headers=headers)
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail.startswith("payway_link_not_found:")

    # Refused means refused: no store was created to sit there looking active.
    assert (await client.get("/api/v1/stores", headers=headers)).json()["data"] == []


async def test_a_value_that_is_not_a_payway_link_never_reaches_the_probe(
    client, monkeypatch
):
    """A shape check first, so an obvious mistake costs no outbound call."""
    calls: list[str] = []
    probe_returning(monkeypatch, "ok", calls=calls)
    _account, headers, payload = await _store_payload(
        client, "bakong://126071610243081"
    )

    res = await client.post("/api/v1/stores", json=payload, headers=headers)
    assert res.status_code == 400, res.text
    assert res.json()["detail"].startswith("payway_link_invalid:")
    assert calls == []


async def test_a_link_that_is_too_short_to_be_a_slug_is_refused(client, monkeypatch):
    calls: list[str] = []
    probe_returning(monkeypatch, "ok", calls=calls)
    _account, headers, payload = await _store_payload(client, "abc")

    res = await client.post("/api/v1/stores", json=payload, headers=headers)
    assert res.status_code == 400, res.text
    assert res.json()["detail"].startswith("payway_link_invalid:")
    assert calls == []


async def test_payway_being_unreachable_does_not_block_the_merchant(client, monkeypatch):
    """Our inability to check is not the merchant's mistake.

    The link is stored so nothing is lost and the merchant can carry on, but it is
    recorded as unverified and the store stays `draft` — "active" would be a claim
    about a destination we never confirmed. A draft store with a link still accepts
    payments, so this does not stop the integration.
    """
    probe_returning(monkeypatch, "inconclusive")
    _account, headers, payload = await _store_payload(
        client, PAYWAY_LINK, merchant_name="Sokha Cafe"
    )

    res = await client.post("/api/v1/stores", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    store = res.json()
    assert store["status"] == "draft"
    assert store["link"]["verification"] == models.LINK_UNVERIFIED

    created = await client.post(
        "/api/v1/payments",
        json={"amount": 2.0, "store": store["id"], "hosted_qr": False},
        headers=headers,
    )
    assert created.status_code == 201, created.text


async def test_replacing_a_live_stores_link_does_not_downgrade_it(client, monkeypatch):
    """Promotion is one-way here: a failed re-check must not take a store offline."""
    probe_returning(monkeypatch, "ok")
    _account, headers, payload = await _store_payload(client, PAYWAY_LINK)
    created = (await client.post("/api/v1/stores", json=payload, headers=headers)).json()
    assert created["status"] == "active"

    probe_returning(monkeypatch, "inconclusive")
    res = await client.put(
        f"/api/v1/stores/{created['id']}/link",
        json={"raw_link": PAYWAY_LINK, "merchant_account_id": "ABAPAYpe518710Y"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "active"
    assert (await link_row(created["id"])).verification == models.LINK_UNVERIFIED
