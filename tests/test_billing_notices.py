"""What the merchant is shown, and the one rule that keeps it honest.

A notice is *derived* from `due_at` and the account's state — never from the
`plan_invoice_reminders` rows the worker writes. That asymmetry is the point: those rows are the
record of what the platform said, so a worker outage that wrote none of them must not be able to
hide a warning the merchant was owed.

One notice, not a list: a banner is one line, and the billing page lists every invoice with its
own due date, which is what stops a dismissed banner from becoming a hidden debt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import make_account
from sqlalchemy import select

from chmabapay import models
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.main import app
from chmabapay.routers.auth import SESSION_COOKIE, _make_session_jwt
from chmabapay.services import billing as billing_svc
from chmabapay.services import notifications

BASE_URL = "http://localhost"

# Every field the portal reads, pinned so one cannot be renamed out from under it — the failure
# `period_month` versus `period` already produced once on the billing page.
NOTICE_FIELDS = {
    "state",
    "level",
    "title",
    "body",
    "invoice_id",
    "period_label",
    "amount_cents",
    "amount_formatted",
    "due_at",
    "days_until_due",
    "action_label",
    "action_url",
    "dismissible",
}


async def _plans() -> dict[str, models.Plan]:
    async with session_factory() as session:
        await seed_default_plans(session)
        rows = (await session.execute(select(models.Plan))).scalars().all()
        return {plan.code: plan for plan in rows}


def _session_as(row: models.Account) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        cookies={SESSION_COOKIE: _make_session_jwt(row, "password")},
    )


async def _invoice_due(account_id: int, plan_id: int, *, due: datetime) -> models.PlanInvoice:
    """An account whose covered window ended at `due`, and the invoice raised for it."""
    async with session_factory() as session:
        session.add(
            models.PlanSubscription(
                account_id=account_id,
                plan_id=plan_id,
                status="active",
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


async def _set_status(account_id: int, status: str) -> None:
    async with session_factory() as session:
        row = await session.get(models.Account, account_id)
        assert row is not None
        row.status = status
        await session.commit()


async def _notice(account: models.Account) -> dict | None:
    async with _session_as(account) as client:
        res = await client.get("/v1/billing/notices")
        assert res.status_code == 200, res.text
        return (res.json()["notices"] or [None])[0]


# --------------------------------------------------------------------------- #
# The states
# --------------------------------------------------------------------------- #
async def test_the_lead_window_is_an_informational_notice():
    """The invoice is raised seven days ahead, so the first thing the merchant sees is a date and
    a price — not a problem. `issuance` is a notice with no tier behind it, which is why nothing
    is recorded for it in the reminder table."""
    plans = await _plans()
    account = await make_account(email="calm@notices.test", name="Calm")
    due = datetime.now(UTC) + timedelta(days=5)
    await _invoice_due(account.id, plans["starter"].id, due=due)

    notice = await _notice(account)
    assert notice is not None
    assert notice["state"] == billing_svc.STATE_ISSUANCE
    assert notice["level"] == "info"
    assert notice["dismissible"] is True
    assert notice["amount_cents"] == 999
    assert notice["action_url"] == f"/dashboard/billing?pay={notice['invoice_id']}"
    # The sentence names the merchant's own day — the one thing only the server can resolve from
    # a UTC instant.
    local = due.astimezone(notifications.DISPLAY_TZ)
    assert f"{local:%b} {local.day}" in notice["title"]
    assert "Starter" in notice["title"]
    assert notice["period_label"] is not None


async def test_a_tier_after_the_threshold_is_a_warning_that_cannot_be_dismissed():
    plans = await _plans()
    account = await make_account(email="late@notices.test", name="Late")
    await _invoice_due(
        account.id, plans["starter"].id, due=datetime.now(UTC) - timedelta(days=2)
    )

    notice = await _notice(account)
    assert notice is not None
    assert notice["state"] == "overdue_1"
    assert notice["level"] == "warning"
    assert notice["dismissible"] is False
    assert notice["days_until_due"] < 0
    assert "$9.99" in notice["body"]


async def test_warnings_survive_a_worker_that_never_ran():
    """A-17, and the reason this endpoint never reads `plan_invoice_reminders`.

    The reminder rows are bookkeeping; the banner is derived. Deleting every row changes nothing
    about what the merchant is told, and re-running the worker restores the record without
    changing the message — so an outage costs accountability, never a warning.
    """
    plans = await _plans()
    account = await make_account(email="outage@notices.test", name="Outage")
    invoice = await _invoice_due(
        account.id, plans["starter"].id, due=datetime.now(UTC) - timedelta(days=1)
    )

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(models.PlanInvoiceReminder).where(
                    models.PlanInvoiceReminder.invoice_id == invoice.id
                )
            )
        ).scalars().all()
    assert list(rows) == [], "no worker has run, so there is no record"

    before = await _notice(account)
    assert before is not None and before["state"] == "overdue_1"

    # The worker catches up: the record appears, the message does not change.
    async with session_factory() as session:
        await billing_svc.record_due_reminders(session)
    after = await _notice(account)
    assert after is not None
    assert after["state"] == before["state"]
    assert after["title"] == before["title"]

    # And the record can be deleted again without the merchant losing the warning.
    async with session_factory() as session:
        for row in (
            await session.execute(
                select(models.PlanInvoiceReminder).where(
                    models.PlanInvoiceReminder.invoice_id == invoice.id
                )
            )
        ).scalars().all():
            await session.delete(row)
        await session.commit()

    assert (await _notice(account))["state"] == "overdue_1"


async def test_a_frozen_account_is_told_what_is_kept():
    """`frozen` outranks every tier, and the copy is the only place the product says the merchant's
    data was not deleted. A business that went dark this morning needs that distinction more than
    it needs a due date."""
    plans = await _plans()
    account = await make_account(email="frozen@notices.test", name="Frozen")
    await _invoice_due(
        account.id, plans["starter"].id, due=datetime.now(UTC) - timedelta(days=20)
    )
    await _set_status(account.id, models.ACCOUNT_RESTRICTED)

    notice = await _notice(account)
    assert notice is not None
    assert notice["state"] == billing_svc.STATE_FROZEN, "outranks the tier this invoice reached"
    assert notice["level"] == "critical"
    assert notice["dismissible"] is False
    assert notice["action_label"] == "Resolve billing"
    assert "untouched" in notice["body"]
    assert "$9.99" in notice["body"]


async def test_a_frozen_account_with_no_invoice_still_gets_the_hold_notice():
    """An operator can freeze an account directly, and then there is no invoice to point at. The
    merchant still has to be told, and quoting $0.00 as the amount to settle would be a lie."""
    account = await make_account(email="held@notices.test", name="Held")
    await _set_status(account.id, models.ACCOUNT_RESTRICTED)

    notice = await _notice(account)
    assert notice is not None
    assert notice["state"] == billing_svc.STATE_FROZEN
    assert notice["invoice_id"] is None
    assert notice["amount_cents"] is None
    assert notice["action_url"] == "/dashboard/billing"
    assert "Contact support" in notice["body"]


async def test_nothing_is_due_so_there_is_no_notice():
    """An empty list, not a reassuring banner: a merchant who owes nothing needs no interruption."""
    account = await make_account(email="clear@notices.test", name="Clear")
    async with _session_as(account) as client:
        res = await client.get("/v1/billing/notices")
    assert res.status_code == 200
    assert res.json() == {"notices": []}


# --------------------------------------------------------------------------- #
# The contract behind the states
# --------------------------------------------------------------------------- #
async def test_the_payload_carries_every_field_the_portal_reads():
    plans = await _plans()
    account = await make_account(email="fields@notices.test", name="Fields")
    await _invoice_due(
        account.id, plans["starter"].id, due=datetime.now(UTC) - timedelta(days=2)
    )

    notice = await _notice(account)
    assert notice is not None
    assert set(notice) == NOTICE_FIELDS


def test_every_state_has_copy_and_a_level():
    """The copy lives in `notifications` and the state list in `billing`, which are edited in
    different files. Walking the list is what keeps them from drifting apart: a state added
    without wording raises here instead of rendering a blank banner to a frozen merchant."""
    context = dict(
        plan_name="Starter",
        amount_cents=999,
        due_at=datetime.now(UTC),
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + billing_svc.CREDIT_PERIOD,
        freeze_at=datetime.now(UTC) + timedelta(days=7),
    )
    for state in billing_svc.NOTICE_ORDER:
        title, body, action = notifications.billing_notice(state, **context)
        assert title and body and action, state
        assert state in billing_svc.NOTICE_LEVELS, state

    with pytest.raises(ValueError):
        notifications.billing_notice("invented_state", **context)


async def test_the_subscription_response_names_the_window_and_the_debt():
    """The plan card reads this: when the paid coverage ends, and which invoice to open. Both are
    top-level because a frozen merchant can have a debt with no subscription row to hang it on."""
    plans = await _plans()
    account = await make_account(email="card@notices.test", name="Card")
    due = datetime.now(UTC) + timedelta(days=3)
    invoice = await _invoice_due(account.id, plans["starter"].id, due=due)

    async with _session_as(account) as client:
        res = await client.get("/v1/billing/subscription")
        assert res.status_code == 200, res.text
        body = res.json()
        invoices = (await client.get("/v1/billing/invoices")).json()["data"]

    assert body["current_period_end"] is not None
    assert body["outstanding_invoice_id"] == invoice.id
    assert body["subscription"]["next_billing_at"] == body["current_period_end"]

    row = next(item for item in invoices if item["id"] == invoice.id)
    assert {
        "period_start",
        "period_end",
        "due_at",
        "days_until_due",
        "is_overdue",
        "voided_at",
        "void_reason",
    } <= set(row)
    assert row["is_overdue"] is False
