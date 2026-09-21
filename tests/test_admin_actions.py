"""Operator actions on an account's money and access.

These exist because the platform console could *see* a problem and not touch it: the
only way to stop a leaked key was to suspend the whole merchant, and correcting an
invoice needed a database session. Each route here is admin-gated, audited, and quiet
on a repeat — an action that changed nothing records nothing.
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from conftest import make_account, make_key, make_store, make_webhook
from sqlalchemy import func, select

from chmabapay import models
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.main import app
from chmabapay.routers.auth import SESSION_COOKIE, _make_session_jwt
from chmabapay.security import hash_password

BASE_URL = "http://localhost"
PASSWORD = "correct horse battery"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


async def make_operator(
    *, admin: bool = True, email: str = "duke@chmaba.test"
) -> models.Account:
    """An account with a password, optionally a platform admin."""
    account = await make_account(email=email, name="Duke")
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.password_hash = hash_password(PASSWORD)
        row.is_platform_admin = admin
        await session.commit()
        await session.refresh(row)
        return row


async def make_merchant(*, with_password: bool = False) -> models.Account:
    account = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    if with_password:
        async with session_factory() as session:
            row = await session.get(models.Account, account.id)
            assert row is not None
            row.password_hash = hash_password(PASSWORD)
            await session.commit()
    return account


def signed_in(row: models.Account) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        cookies={SESSION_COOKIE: _make_session_jwt(row)},
    )


async def sign_in(client: httpx.AsyncClient, email: str) -> None:
    res = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert res.status_code == 200, res.text


async def rows(action: str) -> list[models.AuditLog]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.AuditLog).where(models.AuditLog.action == action)
                )
            )
            .scalars()
            .all()
        )


async def active_plan_code(account_id: int) -> str | None:
    async with session_factory() as session:
        return (
            await session.execute(
                select(models.Plan.code)
                .join(
                    models.PlanSubscription,
                    models.PlanSubscription.plan_id == models.Plan.id,
                )
                .where(
                    models.PlanSubscription.account_id == account_id,
                    models.PlanSubscription.status == "active",
                )
            )
        ).scalar_one_or_none()


async def seed_plans() -> None:
    async with session_factory() as session:
        await seed_default_plans(session)


# --------------------------------------------------------------------------- #
# Plan override
# --------------------------------------------------------------------------- #
async def test_assigning_a_plan_needs_no_invoice_and_records_the_reason(client):
    await seed_plans()
    operator = await make_operator()
    merchant = await make_merchant()

    assert (await client.post("/auth/login", json={
        "email": operator.email, "password": PASSWORD
    })).status_code == 200

    res = await client.patch(
        f"/v1/admin/accounts/{merchant.id}/plan",
        json={"plan_code": "pro", "reason": "comped for the pilot"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["plan_code"] == "pro"
    assert await active_plan_code(merchant.id) == "pro"

    # Comped, so nothing was billed for it: the whole point of the override.
    async with session_factory() as session:
        invoices = (
            await session.execute(
                select(func.count(models.PlanInvoice.id)).where(
                    models.PlanInvoice.account_id == merchant.id
                )
            )
        ).scalar_one()
    assert invoices == 0

    entry = (await rows("account.plan_assigned"))[0]
    assert entry.actor_account_id == operator.id
    assert entry.details["to_plan"] == "pro"
    assert entry.details["from_plan"] is None
    assert entry.details["reason"] == "comped for the pilot"
    # The fee that was given away, so the record shows what it cost.
    assert entry.details["monthly_fee_cents"] == 5999

    # Re-selecting the same plan is not a change, and must not log a second "granted".
    same = await client.patch(
        f"/v1/admin/accounts/{merchant.id}/plan",
        json={"plan_code": "pro", "reason": "again"},
    )
    assert same.status_code == 400
    assert same.json()["detail"] == "plan_unchanged"
    assert len(await rows("account.plan_assigned")) == 1

    # A reason is mandatory: it is the only thing that explains a free upgrade.
    for bad in ({}, {"plan_code": "pro"}, {"plan_code": "pro", "reason": "no"}):
        assert (
            await client.patch(f"/v1/admin/accounts/{merchant.id}/plan", json=bad)
        ).status_code == 422

    assert (
        await client.patch(
            f"/v1/admin/accounts/{merchant.id}/plan",
            json={"plan_code": "nope", "reason": "typo"},
        )
    ).status_code == 404


async def test_assigning_a_plan_replaces_the_one_the_account_had(client):
    await seed_plans()
    operator = await make_operator()
    merchant = await make_merchant()

    async with signed_in(operator) as admin:
        for code in ("starter", "pro"):
            res = await admin.patch(
                f"/v1/admin/accounts/{merchant.id}/plan",
                json={"plan_code": code, "reason": "moving them up"},
            )
            assert res.status_code == 200, res.text

    assert await active_plan_code(merchant.id) == "pro"
    # Exactly one active subscription, which is the invariant `_get_active_sub` needs.
    async with session_factory() as session:
        active = (
            await session.execute(
                select(func.count(models.PlanSubscription.id)).where(
                    models.PlanSubscription.account_id == merchant.id,
                    models.PlanSubscription.status == "active",
                )
            )
        ).scalar_one()
    assert active == 1

    history = await rows("account.plan_assigned")
    assert history[-1].details["from_plan"] == "starter"
    assert history[-1].details["to_plan"] == "pro"


# --------------------------------------------------------------------------- #
# Invoice resolution
# --------------------------------------------------------------------------- #
async def buy_an_invoice(client) -> tuple[models.Account, int]:
    """Drive the merchant flow so a real pending subscription and invoice exist."""
    await seed_plans()
    merchant = await make_merchant(with_password=True)
    store = await make_store(merchant, name="Sokha Cafe")
    assert store is not None

    await sign_in(client, merchant.email)
    res = await client.post("/v1/billing/change-plan", json={"plan_code": "pro"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["payment_required"] is True
    return merchant, int(body["invoice"]["id"])


async def test_waiving_an_invoice_puts_the_plan_in_force_without_income(client):
    merchant, invoice_id = await buy_an_invoice(client)
    operator = await make_operator()

    async with signed_in(operator) as admin:
        res = await admin.post(
            f"/v1/admin/invoices/{invoice_id}/resolve",
            json={"action": "waive", "reason": "goodwill after the outage"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "waived"
        # A waiver is not income, so it carries no settlement date.
        assert res.json()["paid_at"] is None

        # Resolving it again is refused rather than silently re-recorded.
        again = await admin.post(
            f"/v1/admin/invoices/{invoice_id}/resolve",
            json={"action": "mark-paid", "reason": "second attempt"},
        )
        assert again.status_code == 409
        assert again.json()["detail"] == "invoice_already_waived"

    # The plan is in force: the merchant is no longer stuck on their old tier waiting
    # for an invoice nobody is going to pay.
    assert await active_plan_code(merchant.id) == "pro"

    entry = (await rows("invoice.resolved"))[0]
    assert entry.actor_account_id == operator.id
    assert entry.details["resolution"] == "waive"
    assert entry.details["reason"] == "goodwill after the outage"
    assert entry.details["period_month"]
    assert entry.details["total_due_cents"] == 5999


async def test_marking_an_invoice_paid_records_the_settlement(client):
    merchant, invoice_id = await buy_an_invoice(client)
    operator = await make_operator()

    async with signed_in(operator) as admin:
        res = await admin.post(
            f"/v1/admin/invoices/{invoice_id}/resolve",
            json={"action": "mark-paid", "reason": "bank transfer received"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "paid"
        assert res.json()["paid_at"] is not None

    assert await active_plan_code(merchant.id) == "pro"
    assert (await rows("invoice.resolved"))[0].details["resolution"] == "mark-paid"


async def test_crediting_an_invoice_records_what_was_given_up(client):
    merchant, invoice_id = await buy_an_invoice(client)
    operator = await make_operator()

    async with signed_in(operator) as admin:
        # A partial credit: the invoice keeps saying what was billed, and the audit row
        # says how much of it was forgiven.
        res = await admin.post(
            f"/v1/admin/invoices/{invoice_id}/resolve",
            json={
                "action": "credit",
                "reason": "half credited for the duplicated period",
                "amount_cents": 3000,
            },
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "credited"
        assert res.json()["total_due_cents"] == 5999

    entry = (await rows("invoice.resolved"))[0]
    assert entry.details["credited_cents"] == 3000
    assert entry.details["total_due_cents"] == 5999
    assert await active_plan_code(merchant.id) == "pro"


# --------------------------------------------------------------------------- #
# Key revocation and store disabling
# --------------------------------------------------------------------------- #
async def test_revoking_a_key_stops_it_and_is_quiet_on_a_repeat(client):
    operator = await make_operator()
    merchant = await make_merchant()
    raw_key, api_key = await make_key(merchant)

    # It works to begin with.
    assert (
        await client.get(
            "/v1/payments", headers={"Authorization": f"Bearer {raw_key}"}
        )
    ).status_code == 200

    async with signed_in(operator) as admin:
        res = await admin.post(f"/v1/admin/keys/{api_key.id}/revoke")
        assert res.status_code == 200, res.text
        assert res.json()["revoked"] is True

    # Dead immediately: the whole reason this route exists is to stop a leak now.
    assert (
        await client.get(
            "/v1/payments", headers={"Authorization": f"Bearer {raw_key}"}
        )
    ).status_code == 401

    async with signed_in(operator) as admin:
        again = await admin.post(f"/v1/admin/keys/{api_key.id}/revoke")
        assert again.status_code == 200
        assert again.json()["revoked"] is False
        assert (
            await admin.post("/v1/admin/keys/999999/revoke")
        ).status_code == 404

    # One revocation, one row.
    assert len(await rows("key.revoked")) == 1
    assert (await rows("key.revoked"))[0].actor_account_id == operator.id


async def test_disabling_a_store_stops_new_payments(client):
    operator = await make_operator()
    merchant = await make_merchant()
    store = await make_store(merchant, name="Sokha Cafe")
    raw_key, _ = await make_key(merchant)

    async with signed_in(operator) as admin:
        res = await admin.post(f"/v1/admin/stores/{store.public_id}/disable")
        assert res.status_code == 200, res.text
        assert res.json()["disabled"] is True

    refused = await client.post(
        "/v1/payments",
        json={"amount": 1.0, "store": store.public_id, "hosted_qr": False},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert refused.status_code == 400
    assert refused.json()["detail"] == "store_disabled"

    async with signed_in(operator) as admin:
        again = await admin.post(f"/v1/admin/stores/{store.public_id}/disable")
        assert again.status_code == 200
        assert again.json()["disabled"] is False
        assert (
            await admin.post("/v1/admin/stores/st_nope/disable")
        ).status_code == 404

    assert len(await rows("store.disabled")) == 1


# --------------------------------------------------------------------------- #
# Webhook delivery retry
# --------------------------------------------------------------------------- #
async def make_failed_delivery(
    client, merchant, store
) -> models.EventDelivery:
    """A terminal delivery: attempts spent, nothing further scheduled.

    Built by hand rather than by making the sender fail, because the state under test
    is the one after `webhook_max_attempts` is exhausted, which the suite would
    otherwise have to wait out.
    """
    raw_key, _api_key = await make_key(merchant)
    created = (
        await client.post(
            "/v1/payments",
            json={
                "amount": 12.5,
                "reference_id": "order_retry",
                "store": store.public_id,
                "hosted_qr": False,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    ).json()

    endpoint = await make_webhook(merchant)
    async with session_factory() as session:
        store_row = (
            await session.execute(
                select(models.Store).where(models.Store.public_id == store.public_id)
            )
        ).scalar_one()
        payment_row = (
            await session.execute(
                select(models.Payment).where(
                    models.Payment.public_id == created["id"]
                )
            )
        ).scalar_one()
        session.add(
            models.Event(
                id="evt_retry_test",
                account_id=merchant.id,
                store_id=store_row.id,
                payment_id=payment_row.id,
                type=models.EVENT_COMPLETED,
                payload={},
            )
        )
        await session.flush()
        delivery = models.EventDelivery(
            event_id="evt_retry_test",
            endpoint_id=endpoint.id,
            status=models.DELIVERY_FAILED,
            attempts=6,
            last_error="connection refused",
        )
        session.add(delivery)
        await session.commit()
        await session.refresh(delivery)
        return delivery


async def test_retrying_a_delivery_reschedules_it_and_records_why(client):
    operator = await make_operator()
    merchant = await make_merchant()
    store = await make_store(merchant, name="Sokha Cafe")
    delivery = await make_failed_delivery(client, merchant, store)

    async with signed_in(operator) as admin:
        res = await admin.post(f"/v1/admin/deliveries/{delivery.id}/retry")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["retried"] is True
        assert body["status"] == models.DELIVERY_RETRYING
        # The attempt budget came back too, or a spent delivery would be queued only
        # to be refused as exhausted.
        assert body["attempts"] == 0
        assert body["next_attempt_at"] is not None

        async with session_factory() as session:
            row = await session.get(models.EventDelivery, delivery.id)
            assert row is not None
            assert row.status == models.DELIVERY_RETRYING
            assert row.last_error is None

        # Quiet on a repeat: already due, already fresh, so nothing changes and no
        # second row is written.
        again = await admin.post(f"/v1/admin/deliveries/{delivery.id}/retry")
        assert again.status_code == 200
        assert again.json()["retried"] is False

        assert (
            await admin.post("/v1/admin/deliveries/999999/retry")
        ).status_code == 404

    entries = await rows("admin.delivery_retried")
    assert len(entries) == 1
    assert entries[0].actor_account_id == operator.id
    assert entries[0].details["from_status"] == models.DELIVERY_FAILED
    assert entries[0].details["endpoint_id"] == delivery.endpoint_id


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #
async def test_every_operator_action_is_admin_gated(client):
    await seed_plans()
    merchant, invoice_id = await buy_an_invoice(client)
    store = await make_store(merchant, name="Sokha Cafe")
    _raw_key, api_key = await make_key(merchant)
    delivery = await make_failed_delivery(client, merchant, store)

    calls = [
        ("PATCH", f"/v1/admin/accounts/{merchant.id}/plan"),
        ("POST", f"/v1/admin/invoices/{invoice_id}/resolve"),
        ("POST", f"/v1/admin/keys/{api_key.id}/revoke"),
        ("POST", f"/v1/admin/stores/{store.public_id}/disable"),
        ("POST", f"/v1/admin/deliveries/{delivery.id}/retry"),
    ]
    body = {"plan_code": "pro", "action": "waive", "reason": "trying it on"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as anonymous:
        for method, path in calls:
            res = await anonymous.request(method, path, json=body)
            assert res.status_code == 401, f"{method} {path}"

    # The same merchant's own valid session is still not an operator session.
    async with signed_in(merchant) as own_session:
        for method, path in calls:
            res = await own_session.request(method, path, json=body)
            assert res.status_code == 403, f"{method} {path}"

    # And none of it happened.
    assert await active_plan_code(merchant.id) is None
    async with session_factory() as session:
        key = await session.get(models.ApiKey, api_key.id)
        assert key is not None
        assert key.status == models.ACCOUNT_ACTIVE
        row = await session.get(models.EventDelivery, delivery.id)
        assert row is not None
        assert row.status == models.DELIVERY_FAILED


# --------------------------------------------------------------------------- #
# Suspension guard and the detail page's new signals
# --------------------------------------------------------------------------- #
async def test_the_last_platform_admin_cannot_suspend_itself(client):
    operator = await make_operator()

    async with signed_in(operator) as admin:
        res = await admin.patch(
            f"/v1/admin/accounts/{operator.id}",
            json={"status": models.ACCOUNT_SUSPENDED, "reason": "stepping away"},
        )
        assert res.status_code == 409
        assert res.json()["detail"] == "last_platform_admin"

        # A second admin is a way back in, so the guard stands down.
        second = await make_operator(email="ops@chmaba.test")
        allowed = await admin.patch(
            f"/v1/admin/accounts/{second.id}",
            json={"status": models.ACCOUNT_SUSPENDED, "reason": "left the company"},
        )
        assert allowed.status_code == 200, allowed.text


async def test_account_detail_exposes_keys_limits_and_usage(client):
    await seed_plans()
    operator = await make_operator()
    merchant = await make_merchant()
    store = await make_store(merchant, name="Sokha Cafe")
    _raw_key, api_key = await make_key(merchant)

    async with signed_in(operator) as admin:
        res = await admin.patch(
            f"/v1/admin/accounts/{merchant.id}/plan",
            json={"plan_code": "starter", "reason": "onboarding"},
        )
        assert res.status_code == 200, res.text

        detail = (await admin.get(f"/v1/admin/accounts/{merchant.id}")).json()

    assert [key["id"] for key in detail["keys"]] == [api_key.id]
    assert detail["keys"][0]["key_prefix"] == api_key.key_prefix
    assert detail["usage"]["keys_active"] == 1
    assert detail["usage"]["payments_this_month"] == 0
    assert detail["limits"]["plan_code"] == "starter"
    assert detail["limits"]["max_stores"] == 5
    assert detail["limits"]["payments_included"] == 15000
    assert [s["id"] for s in detail["stores"]] == [store.public_id]
