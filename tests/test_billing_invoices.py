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

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from conftest import make_account, make_key, make_store, make_webhook
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from chmabapay import models
from chmabapay.config import get_settings
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.main import app
from chmabapay.routers.auth import SESSION_COOKIE, _make_session_jwt
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


async def test_an_unpaid_invoice_blocks_a_second_purchase(client):
    """One open period at a time, and the refusal names the invoice to settle.

    The guard used to key on `(account_id, period_month)`, which meant a merchant could
    accrue two unpaid periods in two different months and end up billed for both. An open
    balance blocks the next purchase whatever month it falls in.
    """
    account = await _signed_in(client, with_subscription_for="free")
    assert (
        await client.post("/v1/billing/change-plan", json={"plan_code": "starter"})
    ).status_code == 200

    second = await client.post("/v1/billing/change-plan", json={"plan_code": "pro"})
    assert second.status_code == 409
    assert second.json()["detail"].startswith("open_invoice_unpaid")

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


async def test_the_database_refuses_two_live_invoices_for_one_window():
    """The constraint itself, not the route that happens to check first. Without it, a
    retried sweep or two replicas would bill twice and nothing in the row would say which
    invoice was which."""
    plans = await _plans()
    account = await make_account(email="dupe@billing.test", name="Dupe")
    async with session_factory() as session:
        sub = models.PlanSubscription(
            account_id=account.id,
            plan_id=plans["starter"].id,
            status="active",
            next_billing_at=datetime.now(UTC) + timedelta(days=30),
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        sub_id = sub.id

    window = datetime.now(UTC).replace(microsecond=0)

    def _row(status: str = "open") -> models.PlanInvoice:
        return models.PlanInvoice(
            account_id=account.id,
            subscription_id=sub_id,
            period_month="2026-09",
            period_start=window,
            period_end=window + timedelta(days=30),
            due_at=window,
            status=status,
            base_fee_cents=999,
            total_due_cents=999,
        )

    async with session_factory() as session:
        session.add(_row())
        await session.commit()

    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_row())
            await session.commit()

    # Voiding the claim releases the window — which is the whole reason the index is
    # partial. With a plain unique key an operator could never re-raise a period they had
    # voided by mistake.
    async with session_factory() as session:
        row = (
            await session.execute(
                select(models.PlanInvoice).where(
                    models.PlanInvoice.account_id == account.id
                )
            )
        ).scalar_one()
        row.status = "void"
        row.void_reason = "operator"
        await session.commit()

    async with session_factory() as session:
        session.add(_row())
        await session.commit()

    async with session_factory() as session:
        live = list(
            (
                await session.execute(
                    select(models.PlanInvoice).where(
                        models.PlanInvoice.account_id == account.id,
                        models.PlanInvoice.status != "void",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(live) == 1


async def test_legacy_invoices_without_a_window_do_not_occupy_one():
    """Pre-0012 rows have `period_start = NULL`, and NULLs are distinct in both SQLite and
    Postgres — so two of them can sit side by side under one subscription, which is what a
    migration of historical rows needs."""
    plans = await _plans()
    account = await make_account(email="legacy@billing.test", name="Legacy")
    async with session_factory() as session:
        sub = models.PlanSubscription(
            account_id=account.id,
            plan_id=plans["starter"].id,
            status="active",
            next_billing_at=datetime.now(UTC),
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        sub_id = sub.id

    async with session_factory() as session:
        for _ in range(2):
            session.add(
                models.PlanInvoice(
                    account_id=account.id,
                    subscription_id=sub_id,
                    period_month="2026-08",
                    status="paid",
                    base_fee_cents=999,
                    total_due_cents=999,
                )
            )
        await session.commit()


# --------------------------------------------------------------------------- #
# Issuing invoices on a schedule
# --------------------------------------------------------------------------- #
def _utc(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes for a tz-aware column; Postgres does not."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _sub_on(account_id: int, plan_id: int, *, next_billing_at: datetime) -> None:
    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account_id,
                plan_id=plan_id,
                status="active",
                started_at=next_billing_at - timedelta(days=30),
                next_billing_at=next_billing_at,
            )
        )
        await session.commit()


async def test_a_renewal_is_invoiced_in_its_lead_window_and_moves_nothing():
    """A-01 and A-02, which are one behaviour seen from two sides.

    The invoice appears *before* the coverage ends — with no mandate to charge, an invoice
    raised afterwards has no leverage and no time to be paid. And raising it does not touch
    the schedule: that is what keeps a non-payer's period end in the past, where the lapse
    is visible. Advancing on issue, which the sweep used to do, is precisely how a merchant
    kept a paid plan indefinitely without paying for it.
    """
    plans = await _plans()
    account = await make_account(email="lead@billing.test", name="Lead")
    period_end = datetime.now(UTC) + timedelta(days=7)
    await _sub_on(account.id, plans["starter"].id, next_billing_at=period_end)

    # A day before the window opens: nothing is owed yet.
    async with session_factory() as session:
        early = await billing_svc.issue_due_invoices(
            session, now=period_end - timedelta(days=8)
        )
    assert early["subscriptions_due"] == 0
    assert await _invoice(account.id) is None

    # Inside it: exactly one invoice, for the window that is about to start.
    async with session_factory() as session:
        on_time = await billing_svc.issue_due_invoices(
            session, now=period_end - timedelta(days=7)
        )
    assert on_time["subscriptions_due"] == 1
    assert on_time["invoices"] == 1

    invoice = await _invoice(account.id)
    assert invoice is not None
    assert invoice.status == "open"
    assert invoice.total_due_cents == 999
    assert _utc(invoice.period_start) == period_end
    assert _utc(invoice.due_at) == period_end
    assert _utc(invoice.period_end) == period_end + billing_svc.CREDIT_PERIOD

    subs = await _subscriptions_for(account.id)
    assert _utc(subs[0].next_billing_at) == period_end, "issuing must not move the schedule"


async def test_a_second_sweep_over_the_same_window_is_inert():
    """The worker may run twice — a retry, a restart mid-sweep — and the second run must
    not bill the same window again."""
    plans = await _plans()
    account = await make_account(email="twice@billing.test", name="Twice")
    period_end = datetime.now(UTC) + timedelta(days=1)
    await _sub_on(account.id, plans["starter"].id, next_billing_at=period_end)

    frozen = datetime.now(UTC)
    async with session_factory() as session:
        await billing_svc.issue_due_invoices(session, now=frozen)
    async with session_factory() as session:
        second = await billing_svc.issue_due_invoices(session, now=frozen)

    assert second["subscriptions_due"] == 0, "the window is already claimed"
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


async def test_a_non_payer_is_billed_once_and_their_schedule_never_moves():
    """The leak, in one assertion.

    A merchant on a paid plan who never pays must be invoiced exactly once — not once per
    sweep, and never re-invoiced for a window that is already claimed — and their period end
    must stay where it is. The old sweep advanced `next_billing_at` on issue whether or not
    anything was paid, so this merchant kept Starter indefinitely for nothing.
    """
    plans = await _plans()
    account = await make_account(email="stale@billing.test", name="Stale")
    period_end = datetime.now(UTC) - timedelta(days=1)
    await _sub_on(account.id, plans["starter"].id, next_billing_at=period_end)

    start = datetime.now(UTC)
    for day in range(10):
        moment = start + timedelta(days=day)
        async with session_factory() as session:
            await billing_svc.issue_due_invoices(session, now=moment)

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
    assert len(invoices) == 1, "ten sweeps over one unpaid window is still one invoice"

    subs = await _subscriptions_for(account.id)
    assert _utc(subs[0].next_billing_at) == period_end, "coverage did not renew"


async def test_a_free_subscription_is_never_invoiced_or_advanced():
    """A $0 invoice every month is noise on a billing page rather than information, so free
    plans are not swept at all — and nothing has to advance, because there is no coverage
    being bought."""
    plans = await _plans()
    account = await make_account(email="free@billing.test", name="Free")
    period_end = datetime.now(UTC) - timedelta(days=1)
    await _sub_on(account.id, plans["free"].id, next_billing_at=period_end)

    async with session_factory() as session:
        summary = await billing_svc.issue_due_invoices(session)

    assert summary["subscriptions_due"] == 0
    assert summary["invoices"] == 0
    assert await _invoice(account.id) is None

    subs = await _subscriptions_for(account.id)
    assert _utc(subs[0].next_billing_at) == period_end


async def test_settling_is_what_moves_the_schedule():
    """A-03: coverage ends where the invoice says it does, and only a payment gets there.

    `next_billing_at = period_end` is the single rule, which is why a first purchase and a
    renewal need no proration branch between them.
    """
    plans = await _plans()
    account = await make_account(email="settle@billing.test", name="Settle")
    period_end = datetime.now(UTC) + timedelta(days=1)
    await _sub_on(account.id, plans["starter"].id, next_billing_at=period_end)
    store = await make_store(account, name="Settle Cafe", external_id="settle-cafe")

    async with session_factory() as session:
        await billing_svc.issue_due_invoices(session)
    invoice = await _invoice(account.id)
    assert invoice is not None

    async with session_factory() as session:
        link = await store_svc.load_link(session, store.id)
        assert link is not None
        payment = models.Payment(
            public_id="pay_window_settle",
            store_id=store.id,
            payment_link_id=link.id,
            amount_cents=invoice.total_due_cents,
            status=models.PAYMENT_PENDING,
            qr_string="00020101021229",
            bill_number="bill-window",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(payment)
        await session.flush()
        attached = await session.get(models.PlanInvoice, invoice.id)
        assert attached is not None
        attached.chmabapay_payment_id = payment.id
        await session.commit()

    async with session_factory() as session:
        assert await payment_svc.mark_paid(session, "pay_window_settle") is not None

    subs = await _subscriptions_for(account.id)
    assert _utc(subs[0].next_billing_at) == period_end + billing_svc.CREDIT_PERIOD


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


# --------------------------------------------------------------------------- #
# A plan smaller than the account's store count
# --------------------------------------------------------------------------- #
async def _hold(account_id: int, max_stores: int) -> list[models.Store]:
    async with session_factory() as session:
        account = await session.get(models.Account, account_id)
        assert account is not None
        held = await store_svc.apply_store_cap(session, account, max_stores=max_stores)
        await session.commit()
        return held


async def test_a_shrinking_plan_holds_the_surplus_and_leaves_a_way_to_earn():
    """A-18 and part of A-20.

    Every limit here is enforced at create time, so before this a downgrade cost a merchant
    nothing they were already using: 50 stores kept taking payments on a plan that allows
    one. The hold stops *new codes* only — a payment already in a customer's hand still
    settles — and it must leave one store working, because that income is what pays the
    invoice.
    """
    await _plans()
    account = await make_account(email="cap@billing.test", name="Cap")
    stores = [
        await make_store(account, name=f"Branch {i}", external_id=f"branch-{i}")
        for i in range(3)
    ]

    held = await _hold(account.id, max_stores=1)
    assert [s.id for s in held] == [stores[1].id, stores[2].id], "the oldest store survives"

    # Idempotent: a second run has nothing left to hold.
    assert await _hold(account.id, max_stores=1) == []

    # Held stores refuse a new code, and say which reason applies.
    async with session_factory() as session:
        for store in held:
            with pytest.raises(HTTPException) as refused:
                await payment_svc.create_payment(
                    session,
                    store=await session.get(models.Store, store.id),
                    amount_cents=100,
                    reference_id=None,
                    metadata=None,
                    idempotency_key=None,
                )
            assert refused.value.detail == "store_billing_suspended"

    # The survivor still earns.
    async with session_factory() as session:
        survivor = await session.get(models.Store, stores[0].id)
        assert survivor is not None
        payment, created = await payment_svc.create_payment(
            session,
            store=survivor,
            amount_cents=100,
            reference_id=None,
            metadata=None,
            idempotency_key=None,
            hosted_qr=False,
        )
        await session.commit()
    assert created is True
    assert payment.amount_cents == 100


async def test_an_upgrade_releases_the_holds():
    """The cap is symmetric, which is what makes it safe on any plan change: going back up
    restores what the downgrade took."""
    await _plans()
    account = await make_account(email="release@billing.test", name="Release")
    for i in range(3):
        await make_store(account, name=f"Branch {i}", external_id=f"rel-{i}")

    await _hold(account.id, max_stores=1)
    assert await _hold(account.id, max_stores=3) == []

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(models.Store).where(models.Store.account_id == account.id)
                )
            )
            .scalars()
            .all()
        )
    assert all(row.billing_suspended_at is None for row in rows)


async def test_the_cap_never_resurrects_a_store_an_operator_disabled():
    """`disable_store` overwrites `status`; the hold is a separate flag. Keeping them apart
    is what stops a billing release from quietly undoing an abuse decision."""
    await _plans()
    account = await make_account(email="abuse@billing.test", name="Abuse")
    store = await make_store(account, name="Branch", external_id="abuse-1")

    async with session_factory() as session:
        attached = await session.get(models.Account, account.id)
        assert attached is not None
        await store_svc.disable_store(session, attached, store.public_id)

    await _hold(account.id, max_stores=5)

    async with session_factory() as session:
        row = await session.get(models.Store, store.id)
        assert row is not None
        assert row.status == models.STORE_DISABLED
        assert row.billing_suspended_at is None


# --------------------------------------------------------------------------- #
# The chooser — which stores stay live
# --------------------------------------------------------------------------- #
async def _live_ids(account_id: int) -> list[int]:
    """The ids of the stores billing is *not* holding, in id order."""
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.Store.id)
                    .where(
                        models.Store.account_id == account_id,
                        models.Store.billing_suspended_at.is_(None),
                    )
                    .order_by(models.Store.id)
                )
            )
            .scalars()
            .all()
        )


async def _slot_moves() -> list[models.AuditLog]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.AuditLog).where(
                        models.AuditLog.action == "store.slot_moved"
                    )
                )
            )
            .scalars()
            .all()
        )


async def _branches(account, count: int, *, prefix: str) -> list[models.Store]:
    return [
        await make_store(account, name=f"Branch {i}", external_id=f"{prefix}-{i}")
        for i in range(count)
    ]


async def test_the_chooser_moves_the_allowance(client):
    """A-19.

    `apply_store_cap` keeps the oldest stores, which is deterministic but arbitrary. This
    is how a merchant whose real business is one of the others gets it back without ever
    holding six stores — that income is what funds their next payment.
    """
    await _plans()
    account = await _signed_in(client, with_subscription_for="starter")  # 5 stores
    branches = await _branches(account, 8, prefix="pick")

    assert [s.id for s in await _hold(account.id, max_stores=5)] == [
        s.id for s in branches[5:]
    ], "the newest three are held by default"

    # Re-pick, one store at a time. Each pick brings one back and holds the newest live
    # one, so the number live never changes.
    expected = [
        (branches[7].public_id, branches[4].id),
        (branches[6].public_id, branches[7].id),
        (branches[5].public_id, branches[6].id),
    ]
    for public_id, displaced_id in expected:
        res = await client.post(f"/v1/stores/{public_id}/activate")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["moved"] is True
        assert body["store"]["billing_suspended_at"] is None
        assert body["displaced"]["db_id"] == displaced_id
        assert len(await _live_ids(account.id)) == 5, "never six, never four"

    assert await _live_ids(account.id) == [
        branches[i].id for i in (0, 1, 2, 3, 5)
    ]

    # Quiet on a repeat: the store is already live, so nothing changes and no second row
    # is written — a double-click is not a second decision.
    again = await client.post(f"/v1/stores/{branches[5].public_id}/activate")
    assert again.status_code == 200, again.text
    assert again.json()["moved"] is False
    assert again.json()["displaced"] is None
    assert await _live_ids(account.id) == [branches[i].id for i in (0, 1, 2, 3, 5)]
    assert len(await _slot_moves()) == 3

    # And the platform's own rule now agrees with the merchant's choice: the cap has
    # nothing left to hold, so a later sweep cannot undo it.
    assert await _hold(account.id, max_stores=5) == []
    assert await _live_ids(account.id) == [branches[i].id for i in (0, 1, 2, 3, 5)]


async def test_a_pick_on_a_plan_with_room_displaces_nothing(client):
    """The swap is a swap only when the allowance is full. On a plan with room there is
    nothing to make room for, and holding a working store to release one would be damage
    for no reason."""
    await _plans()
    account = await _signed_in(client, with_subscription_for="pro")  # 50 stores
    branches = await _branches(account, 3, prefix="room")

    assert len(await _hold(account.id, max_stores=2)) == 1

    res = await client.post(f"/v1/stores/{branches[2].public_id}/activate")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moved"] is True
    assert body["displaced"] is None
    assert await _live_ids(account.id) == [s.id for s in branches]


async def test_the_chooser_refuses_a_store_an_operator_disabled(client):
    """Three refusals exist now — `store_disabled` (the merchant switched it off),
    `store_billing_suspended` (the platform is holding this store) and `account_restricted`
    — and the correct response to each is different. A store an operator disabled is not
    something a billing chooser may bring back."""
    await _plans()
    account = await _signed_in(client, with_subscription_for="starter")
    branches = await _branches(account, 3, prefix="abuse")

    await _hold(account.id, max_stores=1)
    async with session_factory() as session:
        attached = await session.get(models.Account, account.id)
        assert attached is not None
        await store_svc.disable_store(session, attached, branches[2].public_id)

    res = await client.post(f"/v1/stores/{branches[2].public_id}/activate")
    assert res.status_code == 409
    assert res.json()["detail"] == "store_disabled"
    # Nothing moved: the refusal is not a partial swap.
    assert await _live_ids(account.id) == [branches[0].id]
    assert await _slot_moves() == []

    # A store that was never held is a no-op, not a swap.
    live = await client.post(f"/v1/stores/{branches[0].public_id}/activate")
    assert live.status_code == 200
    assert live.json()["moved"] is False
    assert await _live_ids(account.id) == [branches[0].id]
    assert await _slot_moves() == []


async def test_two_picks_at_once_still_leave_exactly_the_allowance(client):
    """Two chooser clicks landing together must not add up to six.

    The whole invariant is "the count before equals the count after", and two requests
    that each read the same live set and each release a store would break it by one. The
    account row is locked for the duration of the swap so the second one reads what the
    first committed.
    """
    await _plans()
    account = await _signed_in(client, with_subscription_for="starter")
    branches = await _branches(account, 7, prefix="race")
    await _hold(account.id, max_stores=5)

    first, second = await asyncio.gather(
        client.post(f"/v1/stores/{branches[5].public_id}/activate"),
        client.post(f"/v1/stores/{branches[6].public_id}/activate"),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(await _live_ids(account.id)) == 5


async def test_a_held_store_is_refused_with_its_own_code(client):
    """A-18, the integrator's half.

    Three refusals now describe three different situations — `store_disabled` (the merchant
    switched the store off), `store_billing_suspended` (the platform is holding it because the
    plan is smaller than the store count) and `account_restricted` (the whole account is on
    hold) — and the correct response to each is different. A caller that cannot tell them apart
    retries the wrong one, or phones support instead of settling the invoice.
    """
    await _plans()
    account = await _signed_in(client, with_subscription_for="starter")  # 5 stores
    branches = await _branches(account, 2, prefix="refuse")

    assert len(await _hold(account.id, max_stores=1)) == 1
    live_ids = await _live_ids(account.id)
    held = next(s for s in branches if s.id not in live_ids)
    live = next(s for s in branches if s.id in live_ids)

    refused = await client.post(
        "/v1/payments", json={"amount": 1.0, "store": held.public_id}
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == "store_billing_suspended"

    # The store that kept its slot is untouched, which is the point of naming only the one.
    minted = await client.post(
        "/v1/payments", json={"amount": 1.0, "store": live.public_id}
    )
    assert minted.status_code == 201, minted.text


async def test_an_in_flight_payment_survives_a_hold_and_a_freeze():
    """A-20. Both states stop *new codes*; neither retracts one a customer already has.

    The hold lives in `create_payment` and the freeze in the route's account gate, so both are
    enforced where a code is minted — which is exactly why a payment that already exists is
    untouched. That is the property worth pinning rather than assuming: the cheap alternative
    (void the pending payments, or refuse to settle them) would take money from a customer who
    has already scanned, and the merchant would find out from the customer.
    """
    await _plans()
    account = await make_account(email="inflight@billing.test", name="In Flight")
    store = await make_store(account, name="Branch", external_id="inflight-1")
    await make_webhook(account)

    # A code minted before anything was wrong, so it is in a customer's wallet by now.
    async with session_factory() as session:
        payment, created = await payment_svc.create_payment(
            session,
            store=await session.get(models.Store, store.id),
            amount_cents=2500,
            reference_id="in-flight",
            metadata=None,
            idempotency_key=None,
            hosted_qr=False,
        )
        await session.commit()
        public_id = payment.public_id
    assert created is True

    # State one: the plan shrank, so this store is the surplus and billing holds it.
    assert [s.id for s in await _hold(account.id, max_stores=0)] == [store.id]

    # State two: the grace ran out, so the whole account is frozen.
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.status = models.ACCOUNT_RESTRICTED
        await session.commit()

    # The customer pays anyway. The money lands on an account that can mint nothing and a
    # store billing has held — and it still settles, and the webhook still fires.
    async with session_factory() as session:
        settled = await payment_svc.mark_paid(session, public_id)
    assert settled is not None
    assert settled.status == models.PAYMENT_PAID

    async with session_factory() as session:
        deliveries = list(
            (
                await session.execute(
                    select(models.EventDelivery)
                    .join(models.Event, models.Event.id == models.EventDelivery.event_id)
                    .where(models.Event.payment_id == settled.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(deliveries) == 1, "the completion event is queued for the merchant"
    assert deliveries[0].status == models.DELIVERY_PENDING


# --------------------------------------------------------------------------- #
# Quota timing on a downgrade (§7.7)
# --------------------------------------------------------------------------- #
async def _quota_plans() -> dict[str, models.Plan]:
    """Two plans with an allowance small enough to exceed in a test.

    The seeded tiers allow 3,000 payments a month, which would be 3,000 rows to write before
    the quota could bite. The rule under test is the calendar-month *boundary*, not the size
    of the number, so the allowance here is two.
    """
    async with session_factory() as session:
        made: dict[str, models.Plan] = {}
        for code, fee in (("paid_tiny", 999), ("free_tiny", 0)):
            plan = (
                await session.execute(
                    select(models.Plan).where(models.Plan.code == code)
                )
            ).scalar_one_or_none()
            if plan is None:
                plan = models.Plan(
                    name=code,
                    code=code,
                    monthly_fee_cents=fee,
                    base_payments_included=2,
                    max_stores=1,
                    max_keys_per_account=1,
                    max_webhooks_per_account=1,
                    is_public=True,
                    is_active=True,
                )
                session.add(plan)
            made[code] = plan
        await session.commit()
        return made


async def _settle_payments(account_id: int, store_id: int, count: int) -> None:
    """`count` paid payments in this calendar month — all the quota counts."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        link = await store_svc.load_link(session, store_id)
        assert link is not None
        for i in range(count):
            session.add(
                models.Payment(
                    public_id=f"pay_quota_{store_id}_{i}",
                    store_id=store_id,
                    payment_link_id=link.id,
                    amount_cents=100,
                    status=models.PAYMENT_PAID,
                    paid_at=now,
                    qr_string="00020101021229",
                    bill_number=f"bill-quota-{store_id}-{i}",
                    expires_at=now + timedelta(minutes=5),
                )
            )
        await session.commit()


async def test_a_plan_choice_does_not_meter_the_month_it_was_made_in(client):
    """T-21 / §7.7.

    The quota is a calendar-month count, so month-to-date usage carries across a plan change.
    A merchant who chose a smaller plan mid-month would otherwise be over the new allowance the
    instant they chose it and stopped dead until the first — a total stop arrived at by
    accident, and one nothing on the billing page explains.
    """
    await _quota_plans()
    account = await _signed_in(client, with_subscription_for="paid_tiny")
    store = await make_store(account, name="Only Branch", external_id="quota-1")
    await _settle_payments(account.id, store.id, 2)

    body = {"amount": 1.0, "store": store.public_id}

    # The trap, demonstrated: over the allowance already, so the next code is refused.
    refused = await client.post("/v1/payments", json=body)
    assert refused.status_code == 402
    assert refused.json()["detail"] == "quota_exceeded"

    # Choosing a smaller plan mid-month is not a reason to stop serving them today.
    moved = await client.post(
        "/v1/billing/change-plan", json={"plan_code": "free_tiny"}
    )
    assert moved.status_code == 200, moved.text

    working = await client.post("/v1/payments", json=body)
    assert working.status_code == 201, working.text

    # The boundary is where it starts biting. Moving the cancellation into a previous month is
    # the same predicate the first of the next month satisfies, with no clock to fake.
    async with session_factory() as session:
        canceled = (
            await session.execute(
                select(models.PlanSubscription).where(
                    models.PlanSubscription.account_id == account.id,
                    models.PlanSubscription.status == "canceled",
                )
            )
        ).scalar_one()
        canceled.canceled_at = datetime.now(UTC) - timedelta(days=40)
        await session.commit()

    after = await client.post("/v1/payments", json=body)
    assert after.status_code == 402
    assert after.json()["detail"] == "quota_exceeded"


async def test_the_deferral_is_reported_and_the_date_is_the_one_enforced(client):
    """T-21's display half — what the usage bar needs, and the coupling that keeps it honest.

    The bar reads `used / included` from the plan in force, so after a mid-month downgrade it
    shows "2 / 1" with a full bar while every code still mints, and the page cannot work out why:
    the deferral is server state. So the server names the date enforcement starts — and that date
    has to be the one `check_plan_quota` honours, which is why this watches the field and the
    refusal turn over together rather than asserting the date on its own.
    """
    await _quota_plans()
    account = await _signed_in(client, with_subscription_for="paid_tiny")
    store = await make_store(account, name="Only Branch", external_id="quota-1")
    await _settle_payments(account.id, store.id, 2)
    body = {"amount": 1.0, "store": store.public_id}

    # Nothing has been given up, so nothing is deferred and the bar means what it says.
    quiet = (await client.get("/v1/billing/subscription")).json()
    assert quiet["quota_deferred_until"] is None

    moved = await client.post("/v1/billing/change-plan", json={"plan_code": "free_tiny"})
    assert moved.status_code == 200, moved.text

    reported = (await client.get("/v1/billing/subscription")).json()
    deferred = datetime.fromisoformat(reported["quota_deferred_until"])
    now = datetime.now(UTC)
    # A first-of-month instant, still ahead, and no further off than the next boundary can be —
    # which pins it without restating the month arithmetic the service does.
    assert deferred.day == 1
    assert (deferred.hour, deferred.minute, deferred.second, deferred.microsecond) == (0, 0, 0, 0)
    assert now < deferred <= now + timedelta(days=32)

    # Over the allowance and still served. That is the state the note exists to explain.
    assert (await client.post("/v1/payments", json=body)).status_code == 201

    # The same predicate the first of the next month satisfies, with no clock to fake.
    async with session_factory() as session:
        canceled = (
            await session.execute(
                select(models.PlanSubscription).where(
                    models.PlanSubscription.account_id == account.id,
                    models.PlanSubscription.status == "canceled",
                )
            )
        ).scalar_one()
        canceled.canceled_at = now - timedelta(days=40)
        await session.commit()

    cleared = (await client.get("/v1/billing/subscription")).json()
    assert cleared["quota_deferred_until"] is None
    assert (await client.post("/v1/payments", json=body)).status_code == 402


async def test_a_new_free_account_is_metered_from_its_first_month(client):
    """The deferral is keyed on a *paid* plan being given up, never on a subscription that
    simply ended: metering from day one is the point of the Free tier, and a deferral keyed on
    "a subscription started this month" would hand every new merchant an unmetered first
    month."""
    plans = await _quota_plans()
    account = await make_account(email="fresh@billing.test", name="Fresh")
    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plans["free_tiny"].id,
                status="active",
                started_at=datetime.now(UTC),
                next_billing_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()

    store = await make_store(account, name="Only Branch", external_id="fresh-1")
    raw_key, _ = await make_key(account)
    await _settle_payments(account.id, store.id, 2)

    refused = await client.post(
        "/v1/payments",
        json={"amount": 1.0, "store": store.public_id},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert refused.status_code == 402
    assert refused.json()["detail"] == "quota_exceeded"

    async with session_factory() as session:
        assert await payment_svc.downgraded_this_month(session, account.id) is False


# --------------------------------------------------------------------------- #
# Settling after a lapse — the reinstatement path
# --------------------------------------------------------------------------- #
async def _invoices_for(account_id: int) -> list[models.PlanInvoice]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.PlanInvoice)
                    .where(models.PlanInvoice.account_id == account_id)
                    .order_by(models.PlanInvoice.id)
                )
            )
            .scalars()
            .all()
        )


async def _held_stores(account_id: int) -> int:
    async with session_factory() as session:
        return len(
            (
                await session.execute(
                    select(models.Store.id).where(
                        models.Store.account_id == account_id,
                        models.Store.billing_suspended_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )


async def _pay_invoice(store: models.Store, invoice: models.PlanInvoice) -> models.Payment:
    """Mint a payment against an invoice and settle it, the way the rail does."""
    async with session_factory() as session:
        link = await store_svc.load_link(session, store.id)
        assert link is not None
        payment = models.Payment(
            public_id=f"pay_lapsed_{invoice.id}",
            store_id=store.id,
            payment_link_id=link.id,
            amount_cents=invoice.total_due_cents,
            status=models.PAYMENT_PENDING,
            qr_string="00020101021229",
            bill_number=f"bill-{invoice.id}",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(payment)
        await session.flush()
        attached = await session.get(models.PlanInvoice, invoice.id)
        assert attached is not None
        attached.chmabapay_payment_id = payment.id
        await session.commit()
        public_id = payment.public_id

    async with session_factory() as session:
        settled = await payment_svc.mark_paid(session, public_id)
        assert settled is not None
        return settled


async def _lapsed_account(*, days_ago: int, email: str):
    """An account whose covered window ended `days_ago` days ago, invoice already raised.

    Long enough past the due date that the grace period has run out, which is the state a
    frozen merchant is in when they finally pay.
    """
    plans = await _plans()
    account = await make_account(email=email, name="Lapsed")
    store = await make_store(account, name="Lapsed Cafe", external_id=f"lapsed-{days_ago}")
    await _sub_on(
        account.id,
        plans["starter"].id,
        next_billing_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    async with session_factory() as session:
        await billing_svc.issue_due_invoices(session)
    invoice = await _invoice(account.id)
    assert invoice is not None and invoice.status == billing_svc.INVOICE_OPEN
    return account, store, invoice, plans


async def test_paying_after_a_freeze_bills_from_the_day_they_paid():
    """A-22 and A-23: the merchant is billed for the 30 days they receive, not the 20 they
    did not.

    A frozen account could not take a single payment, so flipping the lapsed invoice to
    `paid` would book revenue into a window nobody served — and would make `paid` mean two
    different things on a revenue report. The lapsed claim is voided instead, and a fresh
    invoice is granted from the instant the money arrived, with the payment reference moving
    with it so the retry that follows cannot land on the wrong row.
    """
    account, store, lapsed, plans = await _lapsed_account(
        days_ago=20, email="lapsed@billing.test"
    )
    await make_store(account, name="Lapsed Annex", external_id="lapsed-annex")

    # The freeze, plus a store held from an earlier chosen downgrade. Settling has to undo
    # both: neither is something the merchant can restore from their own side.
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.status = models.ACCOUNT_RESTRICTED
        await session.commit()
    await _hold(account.id, max_stores=1)
    assert await _held_stores(account.id) == 1

    await _pay_invoice(store, lapsed)

    invoices = await _invoices_for(account.id)
    assert len(invoices) == 2, "one retired claim and one granted window"
    retired, granted = invoices

    assert retired.id == lapsed.id
    assert retired.status == billing_svc.INVOICE_VOID
    assert retired.void_reason == billing_svc.VOID_GRACE_EXPIRED
    assert retired.voided_at is not None
    assert retired.paid_at is None, "a window nobody served is not income"
    assert retired.chmabapay_payment_id is None, "the payment reference moves with it"

    paid_at = _utc(granted.paid_at)
    assert granted.status == billing_svc.INVOICE_PAID
    assert granted.chmabapay_payment_id is not None
    assert _utc(granted.period_start) == paid_at
    assert _utc(granted.period_end) == paid_at + billing_svc.CREDIT_PERIOD
    assert _utc(granted.period_end) > _utc(retired.period_end), (
        "the 20 days they were frozen are written off, not charged"
    )

    subs = await _subscriptions_for(account.id)
    live = [s for s in subs if s.status in ("trial", "active")]
    assert len(live) == 1, "`_get_active_sub` reads exactly one row"
    assert live[0].id == granted.subscription_id, "a paid invoice names what granted it"
    assert live[0].plan_id == plans["starter"].id, "the plan is what it was"
    assert _utc(live[0].next_billing_at) == _utc(granted.period_end)

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        assert row.status == models.ACCOUNT_ACTIVE, "paying is the way out of the freeze"
    assert await _held_stores(account.id) == 0, "and it restores the stores the cap took"

    async with session_factory() as session:
        entry = (
            await session.execute(
                select(models.AuditLog).where(
                    models.AuditLog.action == "billing.reinstated"
                )
            )
        ).scalar_one()
    assert entry.details["lapsed_invoice_id"] == retired.id
    assert entry.details["granted_invoice_id"] == granted.id
    assert entry.details["gap_days"] == 20
    assert entry.details["source"] == "khqr"


async def test_paying_inside_grace_keeps_the_renewal_anniversary():
    """The other side of the same line.

    Paid while the window is still theirs, the invoice is simply paid: the anniversary holds
    and the grace days are given away free, which is the cheapest possible resolution of a
    late payment — and no second invoice is involved.
    """
    account, store, invoice, _plans_by_code = await _lapsed_account(
        days_ago=3, email="grace@billing.test"
    )

    await _pay_invoice(store, invoice)

    invoices = await _invoices_for(account.id)
    assert len(invoices) == 1, "this window was served, so it is not replaced"
    assert invoices[0].status == billing_svc.INVOICE_PAID
    assert invoices[0].paid_at is not None

    subs = await _subscriptions_for(account.id)
    live = [s for s in subs if s.status in ("trial", "active")]
    assert len(live) == 1
    assert _utc(live[0].next_billing_at) == _utc(invoices[0].period_end), (
        "the anniversary is preserved and the three grace days are free"
    )


async def test_a_suspended_account_is_not_unfrozen_by_paying():
    """`suspended` outranks `restricted`.

    An abuse or review lockout is an operator's decision; a plan payment is not a reason to
    undo it. The invoice still settles — the money arrived and the debt is gone — but the
    account stays locked.
    """
    account, store, lapsed, _plans_by_code = await _lapsed_account(
        days_ago=20, email="suspended@billing.test"
    )

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.status = models.ACCOUNT_SUSPENDED
        await session.commit()

    await _pay_invoice(store, lapsed)

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        assert row.status == models.ACCOUNT_SUSPENDED


# --------------------------------------------------------------------------- #
# The other two ways out of a freeze — §7.5's chooser
# --------------------------------------------------------------------------- #
async def _freeze(account_id: int) -> None:
    async with session_factory() as session:
        row = await session.get(models.Account, account_id)
        assert row is not None
        row.status = models.ACCOUNT_RESTRICTED
        await session.commit()


def _session_as(row: models.Account) -> httpx.AsyncClient:
    """A portal session for an account, without going through the sign-in form.

    The gate under test reads the account's status from the database on every request, so a
    cookie minted before the freeze behaves exactly as one minted after it.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        cookies={SESSION_COOKIE: _make_session_jwt(row, "password")},
    )


async def test_a_frozen_account_choosing_free_writes_the_debt_off_and_is_unfrozen():
    """The cheapest way out, and it has to be instant.

    Choosing Free collects nothing, so the profit here is the merchant who comes back later
    rather than the $9.99 — which only works if the click *resolves* the freeze rather than
    starting a process. Anything less and the merchant is frozen on Free, blocked by a plan
    they have already declined to buy.
    """
    account, _store, lapsed, plans = await _lapsed_account(
        days_ago=20, email="chose-free@billing.test"
    )
    await _freeze(account.id)

    async with _session_as(account) as frozen:
        res = await frozen.post("/v1/billing/change-plan", json={"plan_code": "free"})
        assert res.status_code == 200, res.text
        assert res.json()["payment_required"] is False

    invoices = await _invoices_for(account.id)
    assert [inv.status for inv in invoices] == [billing_svc.INVOICE_VOID]
    assert invoices[0].id == lapsed.id
    assert invoices[0].void_reason == billing_svc.VOID_DOWNGRADED
    assert invoices[0].voided_at is not None

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        assert row.status == models.ACCOUNT_ACTIVE, "the debt is gone, so the hold is too"

    live = [s for s in await _subscriptions_for(account.id) if s.status == "active"]
    assert len(live) == 1
    assert live[0].plan_id == plans["free"].id


async def test_a_frozen_account_choosing_a_paid_plan_waits_for_the_money():
    """The third way out: move to a plan you can afford, and be unfrozen when it settles.

    The lapsed invoice cannot simply stay open beside the new one — that is the trap §7.5
    names, where the merchant is frozen *while on the plan they just chose*, blocked by a plan
    they have declined. And the subscription that claimed the written-off window is retired
    with it: voiding frees the period key, so leaving it live would have W3 raise that same
    invoice again within the hour and put the merchant back where they started.
    """
    account, store, lapsed, plans = await _lapsed_account(
        days_ago=20, email="chose-paid@billing.test"
    )
    await _freeze(account.id)

    async with _session_as(account) as frozen:
        res = await frozen.post("/v1/billing/change-plan", json={"plan_code": "pro"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["payment_required"] is True
        new_invoice_id = body["invoice"]["id"]

    invoices = await _invoices_for(account.id)
    retired = next(inv for inv in invoices if inv.id == lapsed.id)
    assert retired.status == billing_svc.INVOICE_VOID
    assert retired.void_reason == billing_svc.VOID_DOWNGRADED

    subs = await _subscriptions_for(account.id)
    assert [s.status for s in subs] == ["canceled", "pending"], (
        "the lapsed coverage is retired and nothing is in force until it is paid"
    )

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        assert row.status == models.ACCOUNT_RESTRICTED, "still frozen: the new invoice is open"

    granted = next(inv for inv in invoices if inv.id == new_invoice_id)
    await _pay_invoice(store, granted)

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        assert row.status == models.ACCOUNT_ACTIVE

    live = [s for s in await _subscriptions_for(account.id) if s.status == "active"]
    assert len(live) == 1
    assert live[0].plan_id == plans["pro"].id


