"""The activity feed: what an operator group is told, and what it must not cost.

Settled payments, raised plan invoices and unhandled exceptions are posted to
`ACTIVITY_TELEGRAM_CHAT_ID` — a group, separate from the single-operator
`OPS_TELEGRAM_CHAT_ID`. Three properties are worth pinning, because each is a way the
feed could either lie or break something that has already succeeded:

* a settled payment is announced **once**, naming the amount and the store;
* with no chat id configured the feed is silent and nothing raises — the platform is
  under no obligation to notify, so an unconfigured deployment must not error;
* a Telegram outage never fails the caller. `mark_paid` and the invoice sweep call
  this *after* their own commits, so an exception raised here would report a settled
  payment as a failure — the one outcome worse than a missed notification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import make_account, make_store

from chmabapay import alerts, models
from chmabapay.config import Settings
from chmabapay.db import session_factory
from chmabapay.services import billing as billing_svc
from chmabapay.services import notifications, telegram
from chmabapay.services import payments as payment_svc

BOT_TOKEN = "8123456789:AAF-test-bot-token"
GROUP_ID = "-1002712101901"
OPERATOR_ID = "424242"


@pytest.fixture
def outbound(monkeypatch):
    """Capture what would leave for Telegram instead of sending it."""
    sent: list[dict] = []

    async def fake_post(url: str, payload: dict) -> httpx.Response:
        sent.append({"url": url, "payload": payload})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(telegram, "http_post", fake_post)
    return sent


@pytest.fixture(autouse=True)
def _a_deployment_that_may_page(monkeypatch):
    """Keep the delivery guard from silencing these tests.

    The suite runs with the dev gateway on, which `alerts.delivery_allowed()` reads as
    "a development deployment" — the guard working, and it means an unhandled error
    would reach nobody here. `ALERTS_ALLOW_NON_PRODUCTION` is the documented override a
    development machine uses to exercise the alert path; this is that, with no file.
    """
    monkeypatch.setattr(
        alerts, "get_settings", lambda: Settings(alerts_allow_non_production=True)
    )


@pytest.fixture
def grouped(monkeypatch, outbound):
    """A deployment whose feed is pointed at the operator group."""
    settings = Settings(
        telegram_bot_token=BOT_TOKEN, activity_telegram_chat_id=GROUP_ID
    )
    monkeypatch.setattr(telegram, "get_settings", lambda: settings)
    monkeypatch.setattr(notifications, "get_settings", lambda: settings)
    return outbound


@pytest.fixture
def no_webhook_payload(monkeypatch):
    """Keep the webhook payload builder out of these tests.

    It reads `payment.paid_at > payment.expires_at`, and SQLite hands the second back
    naive while the first is set in memory as aware — a disagreement that belongs to
    the webhook tests, not to the feed. Stubbing it here keeps this subject isolated
    rather than letting an unrelated comparison decide the outcome.
    """
    from chmabapay import webhooks

    async def no_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(webhooks, "enqueue_event", no_enqueue)


async def _settle(public_id: str, *, cents: int, reference: str) -> None:
    """A payment on a real store, settled through the real settlement path."""
    account = await make_account(email="feed@billing.test", name="Sokha")
    store = await make_store(account, name="Sokha Cafe", external_id="sokha-cafe")

    async with session_factory() as session:
        link = await session.get(models.PaymentLink, store.id)
        assert link is not None
        session.add(
            models.Payment(
                public_id=public_id,
                store_id=store.id,
                payment_link_id=link.id,
                amount_cents=cents,
                reference_id=reference,
                status=models.PAYMENT_PENDING,
                qr_string="00020101021229",
                bill_number=f"bill-{public_id}",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.commit()

    async with session_factory() as session:
        assert await payment_svc.mark_paid(session, public_id) is not None


async def test_a_settled_payment_reaches_the_group(grouped, no_webhook_payload):
    await _settle("pay_feed_1", cents=1250, reference="ORDER-9")

    assert len(grouped) == 1, "one settlement, one message"
    assert grouped[0]["payload"]["chat_id"] == GROUP_ID
    assert grouped[0]["url"] == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    text = grouped[0]["payload"]["text"]
    assert "$12.50" in text
    assert "Sokha Cafe" in text
    assert "ORDER-9" in text


async def test_the_feed_is_silent_when_no_group_is_configured(
    monkeypatch, outbound
):
    """Unset is silence, not failure: there is nothing to report and nothing broke."""
    settings = Settings(telegram_bot_token=BOT_TOKEN)
    monkeypatch.setattr(notifications, "get_settings", lambda: settings)

    assert notifications.configured() is False
    assert await notifications.notify_activity("nobody should see this") is False
    assert outbound == []


async def test_a_telegram_outage_does_not_fail_a_settlement(
    monkeypatch, outbound, no_webhook_payload
):
    """The money moved, so the caller must be told it moved — even if the group is not."""
    settings = Settings(
        telegram_bot_token=BOT_TOKEN, activity_telegram_chat_id=GROUP_ID
    )
    monkeypatch.setattr(telegram, "get_settings", lambda: settings)
    monkeypatch.setattr(notifications, "get_settings", lambda: settings)

    async def unreachable(url: str, payload: dict) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(telegram, "http_post", unreachable)

    # Raises out of here if the feed is allowed to break a settlement.
    await _settle("pay_feed_2", cents=500, reference="ORDER-10")


async def test_a_raised_plan_invoice_reaches_the_group(grouped):
    """Money owed is announced at the moment it is raised, not only if it is settled."""
    account = await make_account(email="invoiced@billing.test", name="Dara")

    async with session_factory() as session:
        plan = models.Plan(
            code="feed-plan",
            name="Feed Plan",
            monthly_fee_cents=999,
            is_public=True,
            is_active=True,
        )
        session.add(plan)
        await session.flush()
        subscription = models.PlanSubscription(
            account_id=account.id,
            plan_id=plan.id,
            status=billing_svc.SUBSCRIPTION_PENDING,
            next_billing_at=datetime.now(UTC) + timedelta(days=30),
        )
        session.add(subscription)
        await session.flush()
        invoice = models.PlanInvoice(
            account_id=account.id,
            subscription_id=subscription.id,
            period_month="2026-09",
            status="open",
            base_fee_cents=999,
            total_due_cents=999,
        )
        session.add(invoice)
        await session.flush()

        await billing_svc._announce_raised_invoices(session, [(invoice, plan)])

    assert len(grouped) == 1
    text = grouped[0]["payload"]["text"]
    assert grouped[0]["payload"]["chat_id"] == GROUP_ID
    assert "invoiced@billing.test" in text
    assert "Feed Plan" in text
    assert "2026-09" in text
    assert "$9.99" in text


async def test_an_unhandled_error_reaches_the_operator_and_the_group(
    monkeypatch, outbound
):
    """An error is the one thing that must not depend on somebody watching the console."""
    from chmabapay import errors

    monkeypatch.setattr(
        errors,
        "get_settings",
        lambda: Settings(
            ops_telegram_chat_id=OPERATOR_ID,
            activity_telegram_chat_id=GROUP_ID,
        ),
    )
    monkeypatch.setattr(
        telegram, "get_settings", lambda: Settings(telegram_bot_token=BOT_TOKEN)
    )

    await errors.report_exception(
        ValueError("feed-duplicate-check-one"), where="api", context="GET /v1/things"
    )

    assert [sent["payload"]["chat_id"] for sent in outbound] == [OPERATOR_ID, GROUP_ID]
    assert all("feed-duplicate-check-one" in s["payload"]["text"] for s in outbound)


async def test_pointing_both_channels_at_one_chat_does_not_post_twice(
    monkeypatch, outbound
):
    """One chat id in both settings is a reasonable setup, not a request to duplicate."""
    from chmabapay import errors

    monkeypatch.setattr(
        errors,
        "get_settings",
        lambda: Settings(
            ops_telegram_chat_id=GROUP_ID, activity_telegram_chat_id=GROUP_ID
        ),
    )
    monkeypatch.setattr(
        telegram, "get_settings", lambda: Settings(telegram_bot_token=BOT_TOKEN)
    )

    await errors.report_exception(
        KeyError("feed-duplicate-check-two"), where="api"
    )

    assert [sent["payload"]["chat_id"] for sent in outbound] == [GROUP_ID]
