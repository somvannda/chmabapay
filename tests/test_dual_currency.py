"""Dual-currency support: a store settles in USD or KHR, and a payment carries it.

The one rule worth protecting is that a currency the destination cannot honour is
*refused*, not silently downgraded. An API that accepts KHR on a USD ABA PayWay
link and quietly mints a USD code is worse than one that says no, because the
merchant finds out from their customer.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from conftest import make_account, make_key, make_store
from fastapi import HTTPException

from chmabapay import models
from chmabapay.config import get_settings
from chmabapay.db import session_factory
from chmabapay.khqr import parse_tlv
from chmabapay.main import app
from chmabapay.schemas import LinkCreate, StoreCreate, StorePatch, money_to_str
from chmabapay.security import hash_password
from chmabapay.services import payway_parser
from chmabapay.services import stores as store_svc

BASE_URL = "http://localhost"
ADMIN_PASSWORD = "correct horse battery"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


async def _store_with(account, currency: str, *, owner: str = "Sokha") -> models.Store:
    # The slug encodes the currency so the stubbed PayWay check (see conftest) reports
    # it back: a slug naming "khr" reads as a link PayWay confirmed as KHR. This is
    # what the fail-closed currency check requires to allow a KHR store.
    slug = f"{owner.lower()}-{currency.lower()}"
    async with session_factory() as session:
        return await store_svc.create_store(
            session,
            account,
            StoreCreate(
                name=f"{owner} {currency}",
                link=LinkCreate(
                    raw_link=f"https://link.payway.com.kh/{slug}",
                    merchant_account_id=slug,
                    merchant_name=owner,
                    currency=currency,
                ),
            ),
        )


def test_money_to_str_drops_decimals_for_khr():
    # 5,000 riel is stored as 500000 minor units, the same ×100 scale as USD.
    assert money_to_str(500000, "KHR") == "5,000"
    assert money_to_str(500000, "USD") == "5,000.00"
    # The default stays USD so every existing caller is unchanged.
    assert money_to_str(500000) == "5,000.00"


def test_money_to_str_groups_thousands_unless_asked_not_to():
    # Below a thousand nothing changes, which is what makes the separator safe
    # to add at all.
    assert money_to_str(1) == "0.01"
    assert money_to_str(99999) == "999.99"
    # Grouped is the human form: the portal cards, the invoice totals and the
    # hosted checkout page all render this.
    assert money_to_str(472829) == "4,728.29"
    # Ungrouped is the machine form the CSV export writes, so a spreadsheet
    # still reads the cell as a number rather than as text.
    assert money_to_str(472829, grouping=False) == "4728.29"
    assert money_to_str(500000, "KHR", grouping=False) == "5000"


async def test_store_created_in_khr_keeps_and_reports_khr():
    merchant = await make_account(email="khr@chmaba.test", name="KHR Merchant")
    store = await _store_with(merchant, "KHR")

    async with session_factory() as session:
        link = await store_svc.load_link(session, store.id)
    assert link is not None
    # The store's own currency was accepted, not silently rewritten to USD.
    assert link.currency == "KHR"
    # And it surfaces through the read model the portal renders.
    from chmabapay.schemas import StoreOut

    assert StoreOut.from_model(store, link).link.currency == "KHR"


async def test_payment_inherits_the_stores_khr_currency(client):
    merchant = await make_account(email="khr2@chmaba.test", name="KHR Merchant")
    store = await _store_with(merchant, "KHR")
    raw_key, _key = await make_key(merchant)

    res = await client.post(
        "/api/v1/payments",
        json={"amount": 5000, "reference_id": "khr_order", "store": store.public_id, "hosted_qr": False},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert res.status_code < 300, res.text
    body = res.json()
    assert body["currency"] == "KHR"
    # KHR is shown without a fractional part.
    assert body["amount"] == "5000"
    # And the KHQR payload addresses the riel (ISO 116) in Tag 53.
    tags = dict(parse_tlv(body["qr_string"]))
    assert tags.get("53") == "116"


async def test_khr_request_on_a_usd_payway_link_is_refused(client):
    merchant = await make_account(email="usd@chmaba.test", name="USD Merchant")
    store = await make_store(merchant, owner="Usd")
    raw_key, _key = await make_key(merchant)

    res = await client.post(
        "/api/v1/payments",
        json={
            "amount": 5,
            "currency": "KHR",
            "reference_id": "mismatch",
            "store": store.public_id,
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert res.status_code == 400, res.text
    assert "currency_mismatch" in res.text


async def test_explicit_usd_on_a_usd_store_is_accepted(client):
    merchant = await make_account(email="usd2@chmaba.test", name="USD Merchant")
    store = await make_store(merchant, owner="Usd2")
    raw_key, _key = await make_key(merchant)

    res = await client.post(
        "/api/v1/payments",
        json={
            "amount": 5,
            "currency": "USD",
            "reference_id": "same",
            "store": store.public_id,
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert res.status_code < 300, res.text
    assert res.json()["currency"] == "USD"


async def test_reports_summary_is_per_currency(client):
    """A workspace settling in both currencies must not have them summed.

    `total_matching_paid_by_currency` is the honest figure; the legacy summed
    `*_cents` fields exist only for single-currency consumers.
    """
    merchant = await make_account(email="rep@chmaba.test", name="Rep Merchant")
    usd_store = await make_store(merchant, owner="Rep")
    khr_store = await _store_with(merchant, "KHR", owner="RepKhr")
    raw_key, _key = await make_key(merchant)

    async def create_and_pay(store, amount: float) -> None:
        res = await client.post(
            "/api/v1/payments",
            json={
                "amount": amount,
                "reference_id": f"r{amount}",
                "store": store.public_id,
                "hosted_qr": False,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert res.status_code < 300, res.text
        pid = res.json()["id"]
        assert (await client.post(f"/_dev/payments/{pid}/pay")).status_code == 200

    await create_and_pay(usd_store, 10)
    await create_and_pay(khr_store, 5000)

    res = await client.get(
        "/api/v1/reports/payments.json",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert res.status_code == 200, res.text
    by_currency = res.json()["summary"]["total_matching_paid_by_currency"]
    assert by_currency["USD"]["cents"] == 1000
    assert by_currency["USD"]["formatted"] == "10.00"
    assert by_currency["KHR"]["cents"] == 500000
    # KHR carries no fractional unit, and the summary is a display field, so it
    # carries the thousands separator the portal renders.
    assert by_currency["KHR"]["formatted"] == "5,000"


async def _make_admin(email: str = "admin-dc@chmaba.test") -> models.Account:
    account = await make_account(email=email, name="Admin")
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.is_platform_admin = True
        row.password_hash = hash_password(ADMIN_PASSWORD)
        await session.commit()
    return account


async def test_admin_overview_paid_today_is_per_currency(client, monkeypatch):
    """The console's merchant-volume figure is broken out by currency too."""
    # A deployment that has configured an HQ PayWay link seeds an internal store on
    # admin sign-in; unset it so this test counts only what it created.
    from chmabapay.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "chmabapay_hq_store_id", None, raising=False)
    monkeypatch.setattr(settings, "chmabapay_hq_payway_link", None, raising=False)

    admin = await _make_admin()
    merchant = await make_account(email="adm-merch@chmaba.test", name="Merchant")
    usd_store = await make_store(merchant, owner="AdmUsd")
    khr_store = await _store_with(merchant, "KHR", owner="AdmKhr")
    raw_key, _key = await make_key(merchant)

    async def create_and_pay(store, amount: float) -> None:
        res = await client.post(
            "/api/v1/payments",
            json={
                "amount": amount,
                "reference_id": f"a{amount}",
                "store": store.public_id,
                "hosted_qr": False,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert res.status_code < 300, res.text
        pid = res.json()["id"]
        assert (await client.post(f"/_dev/payments/{pid}/pay")).status_code == 200

    await create_and_pay(usd_store, 3)
    await create_and_pay(khr_store, 2000)

    login = await client.post(
        "/auth/login", json={"email": admin.email, "password": ADMIN_PASSWORD}
    )
    assert login.status_code == 200, login.text

    overview = (await client.get("/api/v1/admin/overview")).json()
    by_currency = overview["paid_today_by_currency"]
    assert by_currency["USD"]["cents"] == 300
    assert by_currency["USD"]["count"] == 1
    assert by_currency["KHR"]["cents"] == 200000
    assert by_currency["KHR"]["count"] == 1
    # The legacy summed fields still exist for single-currency consumers.
    assert overview["paid_today_count"] == 2
    assert overview["paid_today_cents"] == 200300


# --------------------------------------------------------------------------- #
# The KHR gate: a currency the destination cannot honour is refused, never
# silently downgraded.
# --------------------------------------------------------------------------- #
async def test_khr_is_gated_by_the_flag(monkeypatch):
    """With the feature flag off, a KHR store is refused outright."""
    monkeypatch.setattr(get_settings(), "enable_khr_currency", False, raising=False)
    merchant = await make_account(email="gate@chmaba.test", name="Gate")

    with pytest.raises(HTTPException) as exc:
        await _store_with(merchant, "KHR")
    assert exc.value.status_code == 400
    assert "khr_currency_disabled" in exc.value.detail


async def test_khr_on_a_usd_link_is_refused(monkeypatch):
    """A link PayWay says settles in USD cannot be stored as a KHR store."""
    merchant = await make_account(email="usdlink@chmaba.test", name="USDL")

    async def fake_verify_link(raw_link: str, *, timeout: float = 6.0):
        return payway_parser.PayWayLinkCheck(
            outcome="ok", slug="sokha-khr", merchant_name="M", currency="USD"
        )

    monkeypatch.setattr(payway_parser, "verify_link", fake_verify_link)

    with pytest.raises(HTTPException) as exc:
        await _store_with(merchant, "KHR")
    assert "payway_link_currency_mismatch" in exc.value.detail


async def test_khr_with_unreadable_currency_is_refused(monkeypatch):
    """Fail closed: when PayWay does not report the currency, KHR is not assumed."""
    merchant = await make_account(email="unread@chmaba.test", name="Unread")

    async def fake_verify_link(raw_link: str, *, timeout: float = 6.0):
        return payway_parser.PayWayLinkCheck(
            outcome="ok", slug="sokha-khr", merchant_name="M", currency=None
        )

    monkeypatch.setattr(payway_parser, "verify_link", fake_verify_link)

    with pytest.raises(HTTPException) as exc:
        await _store_with(merchant, "KHR")
    assert "payway_link_currency_unverified" in exc.value.detail


async def test_usd_on_an_unreadable_link_is_still_allowed(monkeypatch):
    """The default currency keeps working when the link's currency cannot be read."""
    merchant = await make_account(email="usdok@chmaba.test", name="USDOk")

    async def fake_verify_link(raw_link: str, *, timeout: float = 6.0):
        return payway_parser.PayWayLinkCheck(
            outcome="ok", slug="sokha-usd", merchant_name="M", currency=None
        )

    monkeypatch.setattr(payway_parser, "verify_link", fake_verify_link)

    store = await _store_with(merchant, "USD")
    async with session_factory() as session:
        link = await store_svc.load_link(session, store.id)
    assert link is not None
    assert link.currency == "USD"


async def test_fractional_khr_amount_is_refused(client):
    """The riel has no subunit, so a fractional KHR amount is refused, not rounded."""
    merchant = await make_account(email="frackhr@chmaba.test", name="FracKhr")
    store = await _store_with(merchant, "KHR")
    raw_key, _key = await make_key(merchant)

    res = await client.post(
        "/api/v1/payments",
        json={
            "amount": 5000.5,
            "reference_id": "frac",
            "store": store.public_id,
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert res.status_code == 400, res.text
    assert "khr_amount_must_be_whole" in res.text


async def test_partial_link_update_keeps_a_khr_store_khr():
    """A link update that omits `currency` must not reset a KHR store to USD."""
    merchant = await make_account(email="keepkhr@chmaba.test", name="KeepKhr")
    store = await _store_with(merchant, "KHR")

    async with session_factory() as session:
        await store_svc.update_store(
            session,
            merchant,
            store.public_id,
            StorePatch(
                link=LinkCreate(
                    raw_link="https://link.payway.com.kh/sokha-khr",
                    merchant_account_id="sokha-khr",
                    merchant_name="KeepKhr",
                )
            ),
        )
        link = await store_svc.load_link(session, store.id)

    assert link is not None
    assert link.currency == "KHR"
