"""The dunning clock: what the platform records, and the one transition it makes.

Two behaviours over one clock. `remind` records the tier an unpaid invoice has reached — a
record, never the thing that makes a warning visible, because the banner is derived from
`due_at` and a worker outage must not be able to hide a notice the merchant was owed. `enforce`
is the single irreversible step: past the grace period the account is frozen, and nothing about
it changes except its status.

The timeline is placed by hand rather than driven through W3, because every assertion here is
about a specific distance from `due_at` — a sweep at `D-3` and a sweep at `D+6` are the same
code path at different hours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from conftest import make_account, make_store
from sqlalchemy import select

from chmabapay import models
from chmabapay.config import get_settings
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.services import billing as billing_svc
from chmabapay.services import payments as payment_svc
from chmabapay.services import resend as resend_svc
from chmabapay.services import stores as store_svc
from chmabapay.workers.job import Job
from chmabapay.workers.w6_billing_lifecycle import (
    JOB_ENFORCE,
    JOB_REMIND,
    QUEUE,
    BillingLifecycleWorker,
    enforce_heartbeat_job_payload,
    reminder_heartbeat_job_payload,
)


async def _plans() -> dict[str, models.Plan]:
    async with session_factory() as session:
        await seed_default_plans(session)
        rows = (await session.execute(select(models.Plan))).scalars().all()
        return {plan.code: plan for plan in rows}


async def _invoice_due(
    account_id: int, plan_id: int, *, due: datetime, subscription_status: str = "active"
) -> models.PlanInvoice:
    """An account with coverage ending at `due`, and the invoice raised for that window.

    `due_at == period_start == due`, which is the renewal shape: the invoice is raised in its
    lead window and the window it buys starts the day it is due.
    """
    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account_id,
                plan_id=plan_id,
                status=subscription_status,
                started_at=due - billing_svc.CREDIT_PERIOD,
                next_billing_at=due,
            )
        )
        await session.commit()
    async with session_factory() as session:
        await billing_svc.issue_due_invoices(session, now=due)
    async with session_factory() as session:
        return (
            await session.execute(
                select(models.PlanInvoice).where(
                    models.PlanInvoice.account_id == account_id
                )
            )
        ).scalar_one()


async def _sweep_reminders(start: datetime, hours: int) -> None:
    """The hourly heartbeat, run once per hour across a window."""
    for hour in range(hours):
        async with session_factory() as session:
            await billing_svc.record_due_reminders(session, now=start + timedelta(hours=hour))


async def _tiers(
    invoice_id: int, *, channel: str = billing_svc.CHANNEL_IN_APP
) -> list[str]:
    """The tiers recorded on one channel, in the order they were written.

    Channel-scoped, because the table holds a row per channel and the two are independent: a
    test about the banner must not start counting emails.
    """
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.PlanInvoiceReminder.tier)
                    .where(
                        models.PlanInvoiceReminder.invoice_id == invoice_id,
                        models.PlanInvoiceReminder.channel == channel,
                    )
                    .order_by(models.PlanInvoiceReminder.id)
                )
            )
            .scalars()
            .all()
        )


async def _email_rows(invoice_id: int) -> list[models.PlanInvoiceReminder]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.PlanInvoiceReminder)
                    .where(
                        models.PlanInvoiceReminder.invoice_id == invoice_id,
                        models.PlanInvoiceReminder.channel == billing_svc.CHANNEL_EMAIL,
                    )
                    .order_by(models.PlanInvoiceReminder.id)
                )
            )
            .scalars()
            .all()
        )


def _email_channel(monkeypatch, *, fails: bool = False) -> list[dict]:
    """Turn the email channel on with a captured transport, and return what it was asked to send.

    The *transport* is replaced rather than `resend.send_email`, so the payload, the sender and
    the idempotency key are all things the test can assert on — and the idempotency key in
    particular is only meaningful if the real client is the thing that builds it.

    `PUBLIC_ORIGIN` comes along because the deep link is the one part of the message that cannot
    be built without knowing where the portal lives, and pinning it makes the assertion on the
    link deterministic instead of a function of the developer's `.env`.
    """
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://pay.chmaba.test")
    # `get_settings` is `lru_cache`d, so the environment has to be re-read explicitly.
    get_settings.cache_clear()
    sent: list[dict] = []

    async def fake_post(url: str, payload: dict, headers: dict) -> httpx.Response:
        sent.append({"url": url, "payload": payload, "headers": headers})
        if fails:
            return httpx.Response(
                500, json={"name": "application_error", "message": "provider is down"}
            )
        return httpx.Response(200, json={"id": f"msg_{len(sent):03d}"})

    monkeypatch.setattr(resend_svc, "http_post", fake_post)
    return sent


async def _enforce(now: datetime) -> dict:
    async with session_factory() as session:
        return await billing_svc.enforce_grace(session, now=now)


async def _account(account_id: int) -> models.Account:
    async with session_factory() as session:
        row = await session.get(models.Account, account_id)
        assert row is not None
        return row


async def _invoice(invoice_id: int) -> models.PlanInvoice:
    async with session_factory() as session:
        row = await session.get(models.PlanInvoice, invoice_id)
        assert row is not None
        return row


# --------------------------------------------------------------------------- #
# T-08: the tiers, recorded
# --------------------------------------------------------------------------- #
async def test_every_tier_is_recorded_once_and_issuance_is_not_a_tier():
    """A-04, over a real timeline.

    Eleven days of hourly sweeps produce exactly six rows — one per tier — and none for
    `issuance`. The lead window is when the merchant is first told money will be needed, so it
    is a notice with no threshold behind it; recording it would make this table a log of the
    platform talking rather than the evidence of what it said before it froze someone.

    The second pass over the same hours is what a retry or a second replica looks like, and it
    adds nothing.
    """
    plans = await _plans()
    account = await make_account(email="tiers@dunning.test", name="Tiers")
    due = datetime.now(UTC).replace(microsecond=0)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    await _sweep_reminders(due - timedelta(days=4), hours=24 * 11)
    assert await _tiers(invoice.id) == [
        "due_3",
        "due_1",
        "due_today",
        "overdue_1",
        "overdue_3",
        "overdue_final",
    ]

    await _sweep_reminders(due - timedelta(days=4), hours=24 * 11)
    assert len(await _tiers(invoice.id)) == 6, "a repeat sweep records nothing new"


async def test_a_worker_that_was_down_for_days_sends_one_message_not_four():
    """A-05, and the reason `derived_state` reads the clock instead of storing a pointer.

    Four days of backlog collapse into the single tier that is true right now. Replaying the
    missed ones would tell a merchant "your plan renews in 3 days" three days before they are
    frozen — every message individually true and the sequence as a whole a lie.
    """
    plans = await _plans()
    account = await make_account(email="backlog@dunning.test", name="Backlog")
    due = datetime.now(UTC) - timedelta(days=4)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    await _sweep_reminders(due + timedelta(days=4), hours=1)
    assert await _tiers(invoice.id) == ["overdue_3"]


async def test_a_settled_invoice_is_never_reminded():
    """A-06. Nothing is owed, so there is nothing to say — and a stale tier row would be a
    warning about money the merchant has already paid."""
    plans = await _plans()
    account = await make_account(email="settled@dunning.test", name="Settled")
    due = datetime.now(UTC)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    async with session_factory() as session:
        row = await session.get(models.PlanInvoice, invoice.id)
        assert row is not None
        row.status = billing_svc.INVOICE_PAID
        row.paid_at = due - timedelta(days=2)
        await session.commit()

    await _sweep_reminders(due - timedelta(days=4), hours=24 * 11)
    assert await _tiers(invoice.id) == []


async def test_an_upgrade_invoice_is_not_dunned_and_does_not_freeze():
    """A-07 and §5.5.

    An upgrade is carried by a `pending` subscription, so the merchant is on their old plan and
    loses nothing by not paying: no reminders, and no freeze when the grace runs out. Dunning
    someone for a plan they are not on is the platform billing a decision the merchant has not
    made.
    """
    plans = await _plans()
    account = await make_account(email="upgrade@dunning.test", name="Upgrade")
    due = datetime.now(UTC) - timedelta(days=20)

    async with session_factory() as session:
        parked = models.PlanSubscription(
            account_id=account.id,
            plan_id=plans["pro"].id,
            status=billing_svc.SUBSCRIPTION_PENDING,
            started_at=None,
            next_billing_at=due + billing_svc.CREDIT_PERIOD,
        )
        session.add(parked)
        await session.flush()
        invoice, _created = await billing_svc.issue_invoice(
            session,
            parked,
            plans["pro"],
            period_start=due,
            period_end=due + billing_svc.CREDIT_PERIOD,
            due_at=due,
        )
        assert invoice is not None
        await session.commit()
        invoice_id = invoice.id

    await _sweep_reminders(due, hours=24 * 14)
    assert await _tiers(invoice_id) == []

    summary = await _enforce(datetime.now(UTC))
    assert summary["due"] == 0
    assert (await _account(account.id)).status == models.ACCOUNT_ACTIVE
    assert (await _invoice(invoice_id)).status == billing_svc.INVOICE_OPEN


# --------------------------------------------------------------------------- #
# T-09: the freeze
# --------------------------------------------------------------------------- #
async def test_the_freeze_happens_once_at_due_plus_grace(monkeypatch):
    """A-08, the whole shape of the irreversible step.

    Off by default, so the sequence can be watched in production before it costs anyone
    anything. On, it changes one field: the invoice stays open because it *is* the debt, the
    plan is untouched because the merchant has not chosen to change it, and not one store row is
    written because the freeze is account-level — which is what makes unfreezing one write.
    """
    plans = await _plans()
    account = await make_account(email="freeze@dunning.test", name="Freeze")
    stores = [
        await make_store(account, name=f"Branch {i}", external_id=f"freeze-{i}")
        for i in range(3)
    ]
    due = datetime.now(UTC) - timedelta(days=9)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)
    grace = timedelta(days=int(get_settings().billing_grace_days))
    assert grace == timedelta(days=7)

    # A day before the grace runs out: nothing has happened yet.
    early = await _enforce(due + grace - timedelta(days=1))
    assert early["due"] == 0
    assert (await _account(account.id)).status == models.ACCOUNT_ACTIVE

    # Flag off — the default. The candidates are reported and nothing is touched.
    off = await _enforce(due + grace + timedelta(hours=1))
    assert off == {
        "enforced": False,
        "due": 1,
        "frozen": 0,
        "abandoned_purchases": 0,
    }
    assert (await _account(account.id)).status == models.ACCOUNT_ACTIVE

    monkeypatch.setattr(get_settings(), "billing_enforce_enabled", True, raising=False)
    on = await _enforce(due + grace + timedelta(hours=1))
    assert on["frozen"] == 1

    assert (await _account(account.id)).status == models.ACCOUNT_RESTRICTED
    # The debt survives, the plan survives, every store survives.
    assert (await _invoice(invoice.id)).status == billing_svc.INVOICE_OPEN
    live = [
        sub
        for sub in await _subscriptions(account.id)
        if sub.status in ("trial", "active")
    ]
    assert len(live) == 1
    assert live[0].plan_id == plans["starter"].id
    for store in stores:
        row = await _store(store.id)
        assert row.billing_suspended_at is None
        assert row.status == models.STORE_ACTIVE

    # A second run is a no-op: the account is no longer `active`, which is the guard.
    again = await _enforce(due + grace + timedelta(hours=2))
    assert again["due"] == 0
    assert again["frozen"] == 0

    async with session_factory() as session:
        entries = list(
            (
                await session.execute(
                    select(models.AuditLog).where(
                        models.AuditLog.action == "billing.account_frozen"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(entries) == 1
    assert entries[0].details["invoice_id"] == invoice.id
    assert entries[0].details["grace_days"] == 7


async def test_unfreezing_restores_every_store_with_no_restore_step(monkeypatch):
    """A-10. The freeze never flagged a store, so paying has nothing to undo — every store mints
    again the moment the account is `active`."""
    monkeypatch.setattr(get_settings(), "billing_enforce_enabled", True, raising=False)
    plans = await _plans()
    account = await make_account(email="thaw@dunning.test", name="Thaw")
    store = await make_store(account, name="Thaw Cafe", external_id="thaw-cafe")
    due = datetime.now(UTC) - timedelta(days=9)
    await _invoice_due(account.id, plans["starter"].id, due=due)

    await _enforce(due + timedelta(days=8))
    assert (await _account(account.id)).status == models.ACCOUNT_RESTRICTED

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        assert billing_svc.lift_billing_hold(row) is True
        await session.commit()

    async with session_factory() as session:
        payment, created = await payment_svc.create_payment(
            session,
            store=await session.get(models.Store, store.id),
            amount_cents=100,
            reference_id=None,
            metadata=None,
            idempotency_key=None,
            hosted_qr=False,
        )
        await session.commit()
    assert created is True
    assert payment.amount_cents == 100


async def test_a_free_account_is_never_invoiced_reminded_or_frozen():
    """A-11 and §7.8. Free creates no debt, so there is nothing to bill, nothing to warn about
    and nothing to freeze — across a whole window, not merely at one instant."""
    plans = await _plans()
    account = await make_account(email="free@dunning.test", name="Free")
    store = await make_store(account, name="Free Cafe", external_id="free-cafe")

    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plans["free"].id,
                status="active",
                started_at=datetime.now(UTC) - billing_svc.CREDIT_PERIOD,
                next_billing_at=datetime.now(UTC) - timedelta(days=6),
            )
        )
        await session.commit()

    async with session_factory() as session:
        issued = await billing_svc.issue_due_invoices(session)
    assert issued["invoices"] == 0
    assert await _invoices(account.id) == []

    await _sweep_reminders(datetime.now(UTC), hours=24 * 8)
    summary = await _enforce(datetime.now(UTC) + timedelta(days=8))

    assert summary["due"] == 0
    assert (await _account(account.id)).status == models.ACCOUNT_ACTIVE
    assert (await _store(store.id)).billing_suspended_at is None


# --------------------------------------------------------------------------- #
# T-14: who is never a dunning target
# --------------------------------------------------------------------------- #
async def test_a_suspended_account_is_never_dunned_and_never_frozen(monkeypatch):
    """An operator who suspends an account has made a decision, and a billing message on top of
    it is noise. The stronger reason is the ordering: `suspended` outranks `restricted`, so a
    billing job must never be able to trade an abuse lockout for a read-only account that can
    sign in — a weaker state, arrived at on a decision that was not the job's to make (§9).
    """
    monkeypatch.setattr(get_settings(), "billing_enforce_enabled", True, raising=False)
    plans = await _plans()
    account = await make_account(email="suspended@dunning.test", name="Suspended")
    due = datetime.now(UTC) - timedelta(days=9)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.status = models.ACCOUNT_SUSPENDED
        await session.commit()

    # Eleven days of hourly sweeps, then a sweep well past the grace: no reminder is recorded,
    # and the account is neither frozen nor moved into a state a billing job owns.
    await _sweep_reminders(due - timedelta(days=4), hours=24 * 11)
    assert await _tiers(invoice.id) == []

    summary = await _enforce(due + timedelta(days=8))
    assert summary["due"] == 0
    assert summary["frozen"] == 0
    assert (await _account(account.id)).status == models.ACCOUNT_SUSPENDED
    # The debt is untouched too: it is not the job's to write off either.
    assert (await _invoice(invoice.id)).status == billing_svc.INVOICE_OPEN


async def test_a_platform_admin_is_told_about_their_own_invoice_but_never_frozen(monkeypatch):
    """D4. The platform-admin account owns the HQ store that plan fees are collected into, so
    freezing it is the platform locking itself out of its own collection path — the one account
    a billing job must never be able to stop.

    The exemption is on the *consequence*, not on the message, and the asymmetry is deliberate:
    the reminder is still recorded, because the platform owner should know its own bill is due.
    """
    monkeypatch.setattr(get_settings(), "billing_enforce_enabled", True, raising=False)
    plans = await _plans()
    account = await make_account(email="owner@dunning.test", name="Owner")
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.is_platform_admin = True
        await session.commit()

    due = datetime.now(UTC) - timedelta(days=9)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    await _sweep_reminders(due - timedelta(days=4), hours=24 * 11)
    assert await _tiers(invoice.id) == [
        "due_3",
        "due_1",
        "due_today",
        "overdue_1",
        "overdue_3",
        "overdue_final",
    ]

    summary = await _enforce(due + timedelta(days=8))
    assert summary["due"] == 0
    assert summary["frozen"] == 0
    assert (await _account(account.id)).status == models.ACCOUNT_ACTIVE


async def test_an_abandoned_upgrade_is_retired_even_with_enforce_off():
    """The other half of T-09, and deliberately ungated.

    A parked purchase nobody pays for holds `change_plan`'s "one open invoice" guard forever, so
    a merchant who changed their mind could never buy anything again. Cancelling it grants
    nothing and takes nothing away, so it does not need the switch that the freeze does.
    """
    plans = await _plans()
    account = await make_account(email="abandoned@dunning.test", name="Abandoned")
    stale = datetime.now(UTC) - billing_svc.CREDIT_PERIOD - timedelta(days=1)

    async with session_factory() as session:
        parked = models.PlanSubscription(
            account_id=account.id,
            plan_id=plans["pro"].id,
            status=billing_svc.SUBSCRIPTION_PENDING,
            started_at=None,
            next_billing_at=stale + billing_svc.CREDIT_PERIOD,
            # `created_at` is the clock this half reads: the offer is stale because it was made
            # a month ago, not because of anything about the window it bills for.
            created_at=stale,
        )
        session.add(parked)
        await session.flush()
        invoice, _created = await billing_svc.issue_invoice(
            session,
            parked,
            plans["pro"],
            period_start=stale,
            period_end=stale + billing_svc.CREDIT_PERIOD,
            due_at=stale,
        )
        assert invoice is not None
        await session.commit()
        invoice_id = invoice.id
        parked_id = parked.id

    summary = await _enforce(datetime.now(UTC))
    assert summary["abandoned_purchases"] == 1
    assert summary["frozen"] == 0

    assert (await _subscription(parked_id)).status == "canceled"
    retired = await _invoice(invoice_id)
    assert retired.status == billing_svc.INVOICE_VOID
    assert retired.void_reason == billing_svc.VOID_SUPERSEDED, "nobody chose this, it expired"


# --------------------------------------------------------------------------- #
# T-16, Phase B: the same tiers, by email
# --------------------------------------------------------------------------- #
async def test_every_tier_is_emailed_once_in_the_banner_wording(monkeypatch):
    """A-04's email half, and the three things a second channel gets wrong.

    One message per tier, worded by the *same* function that words the banner — the record in
    `plan_invoice_reminders` claims the merchant was told something, and a second copy of that
    sentence is a second version of the truth. Then the delivery details: the account's own
    address, the configured sender, an absolute deep link (email has no origin to resolve
    against), and an idempotency key derived from the message rather than the clock.
    """
    sent = _email_channel(monkeypatch)
    plans = await _plans()
    account = await make_account(email="emailed@dunning.test", name="Emailed")
    due = datetime.now(UTC).replace(microsecond=0)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    await _sweep_reminders(due - timedelta(days=4), hours=24 * 11)

    assert await _tiers(invoice.id) == list(billing_svc.TIERS), "the in-app record is untouched"
    assert await _tiers(invoice.id, channel=billing_svc.CHANNEL_EMAIL) == list(billing_svc.TIERS)

    assert len(sent) == len(billing_svc.TIERS)
    assert [m["payload"]["to"] for m in sent] == [[account.email]] * len(sent)
    assert {m["payload"]["from"] for m in sent} == {get_settings().billing_email_from}
    assert all(m["url"] == f"{resend_svc.API_BASE}/emails" for m in sent)

    # The plan name is in the copy, which is what proves the subscription's plan was joined
    # rather than the email falling back to "your plan".
    assert sent[0]["payload"]["subject"] == "Your Starter plan renews in 3 days"

    link = f"https://pay.chmaba.test/dashboard/billing?pay={invoice.id}"
    assert all(link in m["payload"]["text"] for m in sent)

    assert [m["headers"]["Idempotency-Key"] for m in sent] == [
        f"plan-invoice-{invoice.id}-{tier}" for tier in billing_svc.TIERS
    ]

    rows = await _email_rows(invoice.id)
    assert [row.detail["delivery"] for row in rows] == ["sent"] * len(rows)
    assert [row.detail["provider_id"] for row in rows] == [
        f"msg_{i:03d}" for i in range(1, len(rows) + 1)
    ]

    # A second pass over the same hours is a retry or a second replica, and it says nothing new
    # on either channel.
    await _sweep_reminders(due - timedelta(days=4), hours=24 * 11)
    assert len(sent) == len(billing_svc.TIERS)


async def test_switching_the_channel_on_mid_cycle_sends_only_the_tier_they_are_at(
    monkeypatch,
):
    """§4.2's non-retroactive claim, which is the whole reason `channel` is in the key.

    A merchant four days into a dunning cycle has already been told five things in the
    dashboard. Turning email on owes them the tier they are *at* — not five messages at once,
    and not the four tiers they are past. Nothing is recorded while the channel is off, which is
    why enabling it later can still deliver the current tier.
    """
    plans = await _plans()
    account = await make_account(email="mid-cycle@dunning.test", name="Mid Cycle")
    due = datetime.now(UTC).replace(microsecond=0)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    start = due - timedelta(days=4)
    await _sweep_reminders(start, hours=24 * 8 + 1)
    assert await _tiers(invoice.id) == ["due_3", "due_1", "due_today", "overdue_1", "overdue_3"]
    assert await _email_rows(invoice.id) == [], "no key, no channel, and not even a row"

    sent = _email_channel(monkeypatch)
    await _sweep_reminders(start + timedelta(hours=24 * 8 + 1), hours=1)

    assert await _tiers(invoice.id, channel=billing_svc.CHANNEL_EMAIL) == ["overdue_3"]
    assert len(sent) == 1
    assert sent[0]["payload"]["subject"] == "Your payment is 3 days overdue"


async def test_a_provider_outage_costs_the_email_and_never_the_record(monkeypatch):
    """A send that fails is recorded, not retried, and never takes the sweep down with it.

    Two claims, and the second is the one that matters. The in-app rows survive a total
    provider outage, because they were committed before a message was handed over — the
    merchant's warning does not depend on Resend. And the failed tier is left as a row saying it
    failed, so an absent email row always means "we never tried" rather than "we tried and it
    bounced"; a later sweep therefore does not silently retry it.
    """
    sent = _email_channel(monkeypatch, fails=True)
    plans = await _plans()
    account = await make_account(email="outage@dunning.test", name="Outage")
    due = datetime.now(UTC).replace(microsecond=0)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    start = due - timedelta(days=4)
    await _sweep_reminders(start, hours=24 * 8 + 1)
    attempts = len(sent)
    assert attempts == 5

    assert await _tiers(invoice.id) == ["due_3", "due_1", "due_today", "overdue_1", "overdue_3"]
    rows = await _email_rows(invoice.id)
    assert [row.tier for row in rows] == await _tiers(invoice.id)
    assert [row.detail["delivery"] for row in rows] == ["failed"] * len(rows)
    assert all("provider is down" in row.detail["error"] for row in rows)

    async with session_factory() as session:
        summary = await billing_svc.record_due_reminders(
            session, now=start + timedelta(hours=24 * 8 + 2)
        )
    assert summary["email_failed"] == 0, "a recorded failure is not an attempt to make again"
    assert summary["email_silent"] == 1, "the invoice was considered and had nothing to do"
    assert len(sent) == attempts


async def test_a_settled_invoice_is_never_emailed(monkeypatch):
    """A-06 on the second channel: nothing is owed, so there is nothing to send."""
    sent = _email_channel(monkeypatch)
    plans = await _plans()
    account = await make_account(email="settled-email@dunning.test", name="Settled Email")
    due = datetime.now(UTC)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    async with session_factory() as session:
        row = await session.get(models.PlanInvoice, invoice.id)
        assert row is not None
        row.status = billing_svc.INVOICE_PAID
        row.paid_at = due - timedelta(days=2)
        await session.commit()

    await _sweep_reminders(due - timedelta(days=4), hours=24 * 11)
    assert sent == []
    assert await _email_rows(invoice.id) == []


# --------------------------------------------------------------------------- #
# The job envelope
# --------------------------------------------------------------------------- #
async def test_both_job_types_run_and_an_unknown_one_is_skipped():
    """The worker's dispatch, including the case a rolling deploy creates: an older worker
    receiving a type a newer one enqueued must skip it rather than fail into the retry loop."""
    plans = await _plans()
    account = await make_account(email="jobs@dunning.test", name="Jobs")
    due = datetime.now(UTC) + timedelta(days=1)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    worker = BillingLifecycleWorker()
    reminded = await worker.process(
        Job(queue_name=QUEUE, payload=reminder_heartbeat_job_payload())
    )
    assert reminded["ok"] is True
    assert reminded["job"] == JOB_REMIND

    enforced = await worker.process(
        Job(queue_name=QUEUE, payload=enforce_heartbeat_job_payload())
    )
    assert enforced["ok"] is True
    assert enforced["job"] == JOB_ENFORCE
    assert enforced["frozen"] == 0

    unknown = await worker.process(Job(queue_name=QUEUE, payload={"type": "explode"}))
    assert unknown == {"skipped": True, "reason": "unknown_job_type", "type": "explode"}
    assert invoice.id > 0


# --------------------------------------------------------------------------- #
# Small readers
# --------------------------------------------------------------------------- #
async def _invoices(account_id: int) -> list[models.PlanInvoice]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.PlanInvoice).where(
                        models.PlanInvoice.account_id == account_id
                    )
                )
            )
            .scalars()
            .all()
        )


async def _subscriptions(account_id: int) -> list[models.PlanSubscription]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.PlanSubscription).where(
                        models.PlanSubscription.account_id == account_id
                    )
                )
            )
            .scalars()
            .all()
        )


async def _subscription(subscription_id: int) -> models.PlanSubscription:
    async with session_factory() as session:
        row = await session.get(models.PlanSubscription, subscription_id)
        assert row is not None
        return row


async def _store(store_id: int) -> models.Store:
    async with session_factory() as session:
        row = await session.get(models.Store, store_id)
        assert row is not None
        return row


# --------------------------------------------------------------------------- #
# The whole timeline, in one walk
# --------------------------------------------------------------------------- #
async def _settle(store: models.Store, invoice_id: int) -> models.Payment:
    """Mint the payment the rail would, attach it to the invoice, and confirm it.

    The payment has to be tied to the invoice *before* `mark_paid`, because that link is
    how settlement finds the invoice at all — so it is spelled out here rather than hidden
    behind a fixture. Every other test in this module places one moment on the clock; this
    is the only one that needs money to move.
    """
    async with session_factory() as session:
        link = await store_svc.load_link(session, store.id)
        assert link is not None
        invoice = await session.get(models.PlanInvoice, invoice_id)
        assert invoice is not None
        payment = models.Payment(
            public_id=f"pay_timeline_{invoice_id}",
            store_id=store.id,
            payment_link_id=link.id,
            amount_cents=invoice.total_due_cents,
            status=models.PAYMENT_PENDING,
            qr_string="00020101021229",
            bill_number=f"bill-{invoice_id}",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(payment)
        await session.flush()
        invoice.chmabapay_payment_id = payment.id
        await session.commit()
        public_id = payment.public_id

    async with session_factory() as session:
        settled = await payment_svc.mark_paid(session, public_id)
        assert settled is not None
        return settled


async def test_the_whole_timeline_from_d_minus_31_to_d_plus_16(monkeypatch):
    """The end-to-end walk: issuance, six tiers, the freeze, and the late payment.

    Every claim here is asserted on its own somewhere above. This is the only place they run
    **in sequence**, which is where the ordering claims live: an invoice that must appear in
    its lead window and not before, a schedule that must not move while the debt stands, six
    tiers and no more, one freeze at `D+7` that writes no store row, and a payment at `D+16`
    that reinstates the merchant instead of back-billing a window they never received.

    `D` is sixteen days in the past so that `paid_at` — the real clock, because settlement
    reads the rail's own timestamp — lands after `due_at + grace`. That is what puts this
    walk on the late-payment branch (§5.3) rather than on an in-grace renewal.
    """
    monkeypatch.setattr(get_settings(), "billing_enforce_enabled", True, raising=False)
    plans = await _plans()
    account = await make_account(email="timeline@dunning.test", name="Timeline")
    store = await make_store(account, name="Timeline Cafe", external_id="timeline-cafe")

    due = datetime.now(UTC).replace(microsecond=0) - timedelta(days=16)
    lead = timedelta(days=int(get_settings().billing_lead_days))
    grace = timedelta(days=int(get_settings().billing_grace_days))

    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plans["starter"].id,
                status="active",
                started_at=due - billing_svc.CREDIT_PERIOD,
                next_billing_at=due,
            )
        )
        await session.commit()

    # The heartbeat, hourly from D-31 to D+16, in the order the runtime schedules the three
    # jobs: issuance, then the reminder clock, then enforcement.
    start = due - timedelta(days=31)
    issued_at: datetime | None = None
    frozen_at: datetime | None = None
    for hour in range(24 * 47 + 1):
        moment = start + timedelta(hours=hour)
        async with session_factory() as session:
            await billing_svc.issue_due_invoices(session, now=moment)
        async with session_factory() as session:
            await billing_svc.record_due_reminders(session, now=moment)
        await _enforce(moment)

        # Two reads, each confined to the window where the fact can first become true. An
        # hourly read for forty-seven days would be 1,129 round trips to prove two things
        # that change once — and reading from an hour *before* each window opens is what
        # makes "and not earlier" part of the assertion rather than an assumption.
        if issued_at is None and moment >= due - lead - timedelta(hours=1):
            if await _invoices(account.id):
                issued_at = moment
        if frozen_at is None and moment >= due + grace - timedelta(hours=1):
            if (await _account(account.id)).status == models.ACCOUNT_RESTRICTED:
                frozen_at = moment

    # One invoice, raised at D-7 and not before, for the window it is about to buy.
    invoices = await _invoices(account.id)
    assert len(invoices) == 1, "one window, one invoice — however many sweeps saw it"
    assert issued_at == due - lead, "raised in the lead window, not on the due date"
    invoice = invoices[0]
    assert invoice.status == billing_svc.INVOICE_OPEN
    assert billing_svc.as_utc(invoice.period_start) == due
    assert billing_svc.as_utc(invoice.due_at) == due
    assert billing_svc.as_utc(invoice.period_end) == due + billing_svc.CREDIT_PERIOD

    # Issuance moved nothing, which is what leaves the lapse visible to the enforcement job
    # and keeps a non-payer's period end in the past until they actually pay.
    live = [
        sub
        for sub in await _subscriptions(account.id)
        if sub.status in ("trial", "active")
    ]
    assert len(live) == 1
    assert billing_svc.as_utc(live[0].next_billing_at) == due

    # Six tiers and no more, across seventeen days of hourly sweeps.
    assert await _tiers(invoice.id) == list(billing_svc.TIERS)

    # One freeze, at D+7, that moves the account and not one store row.
    assert frozen_at == due + grace, "frozen at D+7, once"
    assert (await _account(account.id)).status == models.ACCOUNT_RESTRICTED
    held = await _store(store.id)
    assert held.billing_suspended_at is None, "a freeze holds nothing per store"
    assert held.status == models.STORE_ACTIVE
    async with session_factory() as session:
        freezes = list(
            (
                await session.execute(
                    select(models.AuditLog).where(
                        models.AuditLog.action == "billing.account_frozen"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(freezes) == 1

    # Then the payment at D+16. The window they were cut off from is written off — not
    # marked paid — and the thirty days they actually receive start when the money arrived.
    settled = await _settle(store, invoice.id)
    paid_at = settled.paid_at
    assert paid_at is not None

    assert (await _account(account.id)).status == models.ACCOUNT_ACTIVE, "the payment unfreezes"
    lapsed = await _invoice(invoice.id)
    assert lapsed.status == billing_svc.INVOICE_VOID
    assert lapsed.void_reason == billing_svc.VOID_GRACE_EXPIRED

    granted = [
        row
        for row in await _invoices(account.id)
        if row.status == billing_svc.INVOICE_PAID
    ]
    assert len(granted) == 1, "the reinstatement is a new, paid invoice"
    granted_start = billing_svc.as_utc(granted[0].period_start)
    granted_end = billing_svc.as_utc(granted[0].period_end)
    assert granted_start == billing_svc.as_utc(paid_at)
    assert granted_end == granted_start + billing_svc.CREDIT_PERIOD

    reinstated = [
        sub
        for sub in await _subscriptions(account.id)
        if sub.status in ("trial", "active")
    ]
    assert len(reinstated) == 1
    assert billing_svc.as_utc(reinstated[0].next_billing_at) == granted_end
