"""Plan billing: what a plan change costs, and what paying for it unlocks.

The subject throughout is one property: **a paid plan is bought, not clicked.** The
route used to cancel the current subscription and activate the new one in the same
request, collecting nothing — so `POST /v1/billing/change-plan {"plan_code": "pro"}`
was a free upgrade to the top tier, and the only thing standing between a merchant and
$59.99/month of features was that they had to find the button.

The other half is that an invoice must be payable. It was not: with no HQ store
seeded, `GET /v1/billing/invoices/{id}/khqr` answered a raw 500 code and the merchant
had nothing to scan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from conftest import make_account, make_store
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from chmabapay import models
from chmabapay.config import get_settings
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.main import app
from chmabapay.security import hash_password
from chmabapay.services import billing as billing_svc
from chmabapay.services import payments as payment_svc
from chmabapay.services import stores as store_svc

PASSWORD = "correct horse battery"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    # localhost over http, so the session cookie is not marked Secure and the jar
    # sends it back — the same reason the other session tests use this base URL.
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


async def _plans() -> dict[str, models.Plan]:
    async with session_factory() as session:
        await seed_default_plans(session)
        rows = (await session.execute(select(models.Plan))).scalars().all()
        return {plan.code: plan for plan in rows}


async def _signed_in(client, *, with_subscription_for: str | None = None):
    """A merchant with a password, signed in, optionally already on a plan."""
    account = await make_account(email="sokha@billing.test", name="Sokha")
    if with_subscription_for is not None:
        # The schema is rebuilt for every test, so the plans have to be seeded
        # before one can be attached.
        await _plans()
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.password_hash = hash_password(PASSWORD)
        if with_subscription_for is not None:
            plan = (
                await session.execute(
                    select(models.Plan).where(models.Plan.code == with_subscription_for)
                )
            ).scalar_one()
            session.add(
                models.PlanSubscription(
                    account_id=account.id,
                    plan_id=plan.id,
                    status="active",
                    started_at=datetime.now(UTC),
                    next_billing_at=datetime.now(UTC) + timedelta(days=30),
                )
            )
        await session.commit()

    sign_in = await client.post(
        "/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert sign_in.status_code == 200, sign_in.text
    return account


async def _subscriptions_for(account_id: int) -> list[models.PlanSubscription]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.PlanSubscription)
                    .where(models.PlanSubscription.account_id == account_id)
                    .order_by(models.PlanSubscription.id)
                )
            )
            .scalars()
            .all()
        )


async def _invoice(account_id: int) -> models.PlanInvoice | None:
    async with session_factory() as session:
        return (
            await session.execute(
                select(models.PlanInvoice).where(
                    models.PlanInvoice.account_id == account_id
                )
            )
        ).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Buying a plan
# --------------------------------------------------------------------------- #
async def test_upgrading_to_a_paid_plan_raises_an_invoice_instead_of_granting_it(
    client,
):
    """The revenue leak, closed: clicking Pro must produce a bill, not a plan."""
    account = await _signed_in(client, with_subscription_for="free")

    res = await client.post("/v1/billing/change-plan", json={"plan_code": "starter"})
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["payment_required"] is True
    assert body["invoice"]["total_due_cents"] == 999
    assert body["subscription"]["status"] == "pending"
    parked_plan_id = body["subscription"]["plan_id"]

    subs = await _subscriptions_for(account.id)
    assert {sub.status for sub in subs} == {"active", "pending"}
    active = [s for s in subs if s.status == "active"]
    assert len(active) == 1
    assert active[0].plan_id != parked_plan_id

    # And the API agrees the account is still on Free, which is the part that
    # actually stops the leak: every plan gate reads this.
    sub = (await client.get("/v1/billing/subscription")).json()
    assert sub["plan"]["code"] == "free"


async def test_paying_the_invoice_is_what_puts_the_new_plan_in_force(client):
    account = await _signed_in(client, with_subscription_for="free")
    await client.post("/v1/billing/change-plan", json={"plan_code": "pro"})
    invoice = await _invoice(account.id)
    assert invoice is not None and invoice.status == "open"

    # A real store and link, because settling runs the same ledger and webhook path
    # a merchant payment does — a hand-written row would skip most of what is being
    # tested here.
    store = await make_store(account, name="Sokha Cafe", external_id="sokha-cafe")

    async with session_factory() as session:
        link = await store_svc.load_link(session, store.id)
        assert link is not None
        payment = models.Payment(
            public_id="pay_invoice_settle",
            store_id=store.id,
            payment_link_id=link.id,
            amount_cents=invoice.total_due_cents,
            status=models.PAYMENT_PENDING,
            qr_string="00020101021229",
            bill_number="bill-inv",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(payment)
        await session.flush()
        # Loaded in *this* session: the invoice read by the helper above is detached,
        # and mutating it there would write nothing.
        attached = await session.get(models.PlanInvoice, invoice.id)
        assert attached is not None
        attached.chmabapay_payment_id = payment.id
        await session.commit()

    async with session_factory() as session:
        settled = await payment_svc.mark_paid(session, "pay_invoice_settle")
    assert settled is not None

    refreshed = await _invoice(account.id)
    assert refreshed is not None
    assert refreshed.status == "paid"
    assert refreshed.paid_at is not None

    subs = await _subscriptions_for(account.id)
    active = [s for s in subs if s.status == "active"]
    assert len(active) == 1, "exactly one plan may be in force"
    assert active[0].id == refreshed.subscription_id
    assert all(s.status == "canceled" for s in subs if s.id != active[0].id)

    sub = (await client.get("/v1/billing/subscription")).json()
    assert sub["plan"]["code"] == "pro"


async def test_a_downgrade_still_applies_immediately(client):
    """Nothing to collect, and making a merchant wait to *leave* a paid plan would
    trap them on it."""
    account = await _signed_in(client, with_subscription_for="starter")

    res = await client.post("/v1/billing/change-plan", json={"plan_code": "free"})
    assert res.status_code == 200
    assert res.json()["payment_required"] is False
    assert res.json()["invoice"] is None

    subs = await _subscriptions_for(account.id)
    assert [s.status for s in subs] == ["canceled", "active"]
    sub = (await client.get("/v1/billing/subscription")).json()
    assert sub["plan"]["code"] == "free"


async def test_reselecting_the_current_plan_is_refused(client):
    """Otherwise a merchant on Pro can invoice themselves a second time by clicking
    the plan they are already on."""
    await _signed_in(client, with_subscription_for="pro")

    res = await client.post("/v1/billing/change-plan", json={"plan_code": "pro"})
    assert res.status_code == 400
    assert res.json()["detail"] == "plan_unchanged"


async def test_a_period_can_only_be_invoiced_once(client):
    """`uq_plan_invoice_period` is the guarantee; this is the answer the client gets
    instead of a constraint violation."""
    account = await _signed_in(client, with_subscription_for="free")
    assert (
        await client.post("/v1/billing/change-plan", json={"plan_code": "starter"})
    ).status_code == 200

    second = await client.post("/v1/billing/change-plan", json={"plan_code": "pro"})
    assert second.status_code == 409
    assert second.json()["detail"] == "period_already_invoiced"

    async with session_factory() as session:
        invoices = list(
            (
                await session.execute(
                    select(models.PlanInvoice).where(
                        models.PlanInvoice.account_id == account.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(invoices) == 1
    subs = await _subscriptions_for(account.id)
    assert len([s for s in subs if s.status == "pending"]) == 1


async def test_the_database_refuses_two_invoices_for_one_period():
    """The constraint itself, not the route that happens to check first. Without it,
    a retried sweep or a second plan change in the same month would bill twice and
    nothing in the row would say which invoice was which."""
    plans = await _plans()
    account = await make_account(email="dupe@billing.test", name="Dupe")

    def _invoice_row(period: str) -> models.PlanInvoice:
        return models.PlanInvoice(
            account_id=account.id,
            subscription_id=None,
            period_month=period,
            status="open",
            base_fee_cents=plans["starter"].monthly_fee_cents,
            total_due_cents=plans["starter"].monthly_fee_cents,
        )

    async with session_factory() as session:
        session.add(_invoice_row("2026-09"))
        await session.commit()

    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_invoice_row("2026-09"))
            await session.commit()


# --------------------------------------------------------------------------- #
# Issuing invoices on a schedule
# --------------------------------------------------------------------------- #
async def test_a_due_subscription_is_invoiced_and_its_schedule_advances():
    plans = await _plans()
    account = await make_account(email="due@billing.test", name="Due")
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plans["starter"].id,
                status="active",
                started_at=due_at - timedelta(days=30),
                next_billing_at=due_at,
            )
        )
        await session.commit()

    async with session_factory() as session:
        summary = await billing_svc.issue_due_invoices(session)

    assert summary["subscriptions_due"] == 1
    assert summary["invoices"] == 1
    assert summary["periods_advanced"] == 1

    invoice = await _invoice(account.id)
    assert invoice is not None
    assert invoice.status == "open"
    assert invoice.total_due_cents == 999
    assert invoice.period_month == billing_svc.period_month_for(due_at)

    subs = await _subscriptions_for(account.id)
    assert subs[0].next_billing_at == due_at + billing_svc.CREDIT_PERIOD


async def test_a_second_sweep_over_the_same_period_is_inert():
    """The worker may run twice — a retry, a restart mid-sweep — and the second run
    must not bill the same month again."""
    plans = await _plans()
    account = await make_account(email="twice@billing.test", name="Twice")
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plans["starter"].id,
                status="active",
                started_at=due_at - timedelta(days=30),
                next_billing_at=due_at,
            )
        )
        await session.commit()

    # Freeze "now", so the two sweeps look at the same moment. Without that the
    # schedule advance alone would make the second sweep a no-op and the test would
    # pass without exercising the period key at all.
    frozen = datetime.now(UTC)
    async with session_factory() as session:
        await billing_svc.issue_due_invoices(session, now=frozen)
    async with session_factory() as session:
        second = await billing_svc.issue_due_invoices(session, now=frozen)

    assert second["subscriptions_due"] == 0
    assert second["invoices"] == 0

    async with session_factory() as session:
        invoices = list(
            (
                await session.execute(
                    select(models.PlanInvoice).where(
                        models.PlanInvoice.account_id == account.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(invoices) == 1


async def test_a_missed_period_is_caught_up_rather_than_forgiven():
    """A worker that was down must bill each missed month rather than skipping them.

    The number of *invoices* can be one fewer than the number of periods advanced,
    and legitimately so: periods are calendar months, so two 30-day steps can land in
    the same month and a month may only be invoiced once. That errs toward
    under-billing, never toward billing twice, which is the direction to fail in —
    the assertions below pin both halves.
    """
    plans = await _plans()
    account = await make_account(email="stale@billing.test", name="Stale")
    two_months_ago = datetime.now(UTC) - timedelta(days=65)
    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plans["starter"].id,
                status="active",
                started_at=two_months_ago - timedelta(days=30),
                next_billing_at=two_months_ago,
            )
        )
        await session.commit()

    async with session_factory() as session:
        summary = await billing_svc.issue_due_invoices(session)

    assert summary["periods_advanced"] == 3, "three 30-day periods were owed"

    async with session_factory() as session:
        invoices = list(
            (
                await session.execute(
                    select(models.PlanInvoice)
                    .where(models.PlanInvoice.account_id == account.id)
                    .order_by(models.PlanInvoice.period_month)
                )
            )
            .scalars()
            .all()
        )
    periods = [inv.period_month for inv in invoices]
    assert len(periods) == len(set(periods)), "a month may never be invoiced twice"
    assert 2 <= len(invoices) <= 3
    assert all(inv.total_due_cents == 999 for inv in invoices)


async def test_a_free_subscription_advances_without_an_invoice():
    """A $0 invoice every month is noise on a billing page, not information — but the
    schedule still has to move, or the sweep would revisit it forever."""
    plans = await _plans()
    account = await make_account(email="free@billing.test", name="Free")
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plans["free"].id,
                status="active",
                started_at=due_at - timedelta(days=30),
                next_billing_at=due_at,
            )
        )
        await session.commit()

    async with session_factory() as session:
        summary = await billing_svc.issue_due_invoices(session)

    assert summary["periods_advanced"] == 1
    assert summary["nothing_to_bill"] == 1
    assert await _invoice(account.id) is None


# --------------------------------------------------------------------------- #
# Paying an invoice
# --------------------------------------------------------------------------- #
async def test_the_same_invoice_gives_the_same_payment_twice(client):
    """Reopening the billing page must not mint a second payable code: the merchant
    could pay both, and the platform would have taken two months' fees for one."""
    await _plans()
    account = await _signed_in(client, with_subscription_for="free")
    admin = await make_account(email="operator@billing.test", name="Operator")
    store = await make_store(admin, name="ChmabaPay HQ", external_id="hq")
    async with session_factory() as session:
        row = await session.get(models.Account, admin.id)
        assert row is not None
        row.is_platform_admin = True
        await session.commit()

    settings = get_settings()
    original = settings.chmabapay_hq_store_id
    settings.chmabapay_hq_store_id = store.public_id
    try:
        await client.post("/v1/billing/change-plan", json={"plan_code": "starter"})
        invoice = await _invoice(account.id)
        assert invoice is not None

        first = await client.get(f"/v1/billing/invoices/{invoice.id}/khqr")
        assert first.status_code == 201, first.text
        second = await client.get(f"/v1/billing/invoices/{invoice.id}/khqr")
        assert second.status_code == 201, second.text

        assert first.json()["payment_id"] == second.json()["payment_id"]
        assert first.json()["amount_cents"] == 999
    finally:
        settings.chmabapay_hq_store_id = original


async def test_invoice_payment_is_refused_with_an_answer_a_merchant_can_use(client):
    """With no HQ store configured, billing cannot collect — and the merchant is
    told that, instead of being shown an instruction addressed to an operator."""
    await _plans()
    account = await _signed_in(client, with_subscription_for="free")
    await client.post("/v1/billing/change-plan", json={"plan_code": "starter"})
    invoice = await _invoice(account.id)
    assert invoice is not None

    settings = get_settings()
    original = settings.chmabapay_hq_store_id
    settings.chmabapay_hq_store_id = None
    try:
        res = await client.get(f"/v1/billing/invoices/{invoice.id}/khqr")
    finally:
        settings.chmabapay_hq_store_id = original

    assert res.status_code == 503
    assert res.json()["detail"].startswith("billing_not_open")
    assert "CHMABAPAY" not in res.json()["detail"]


async def test_a_lapsed_code_is_replaced_rather_than_handed_back_dead(client):
    """The safety that stops a double charge must not hand back a corpse.

    Reopening the billing page deliberately returns the *same* payment, so a merchant
    cannot be given two payable codes for one month. ABA owns a 180s window and
    nothing extends it, though, so a merchant who closed the tab came back to a QR
    their wallet refuses — payable exactly once, for three minutes. A dead code now
    gets an ABA-reissued successor instead, and the invoice follows it.
    """
    await _plans()
    account = await _signed_in(client, with_subscription_for="free")
    admin = await make_account(email="operator2@billing.test", name="Operator")
    store = await make_store(admin, name="ChmabaPay HQ", external_id="hq-2")
    async with session_factory() as session:
        row = await session.get(models.Account, admin.id)
        assert row is not None
        row.is_platform_admin = True
        await session.commit()

    settings = get_settings()
    original = settings.chmabapay_hq_store_id
    settings.chmabapay_hq_store_id = store.public_id
    try:
        await client.post("/v1/billing/change-plan", json={"plan_code": "starter"})
        invoice = await _invoice(account.id)
        assert invoice is not None

        first = await client.get(f"/v1/billing/invoices/{invoice.id}/khqr")
        assert first.status_code == 201, first.text

        # What the expiry sweeper does once ABA's window closes.
        async with session_factory() as session:
            attached = await session.get(models.PlanInvoice, invoice.id)
            assert attached is not None and attached.chmabapay_payment_id is not None
            payment = await session.get(models.Payment, attached.chmabapay_payment_id)
            assert payment is not None
            payment.status = models.PAYMENT_EXPIRED
            await session.commit()

        second = await client.get(f"/v1/billing/invoices/{invoice.id}/khqr")
        assert second.status_code == 201, second.text
        assert second.json()["payment_id"] != first.json()["payment_id"]
        assert second.json()["amount_cents"] == first.json()["amount_cents"]

        # And the invoice now points at the code that can actually be paid.
        moved = await _invoice(account.id)
        assert moved is not None
        assert moved.chmabapay_payment_id is not None
        async with session_factory() as session:
            successor = await session.get(models.Payment, moved.chmabapay_payment_id)
            assert successor is not None
            assert successor.reissued_from_id is not None

        # A live successor is reused, not replaced again: the same window that caused
        # the problem must not mint a third code.
        third = await client.get(f"/v1/billing/invoices/{invoice.id}/khqr")
        assert third.status_code == 201, third.text
        assert third.json()["payment_id"] == second.json()["payment_id"]
    finally:
        settings.chmabapay_hq_store_id = original
