"""The activity feed: what happened on the platform, pushed to an operator group.

Settled payments, raised plan invoices and error reports go to
``ACTIVITY_TELEGRAM_CHAT_ID`` — a Telegram *group*, kept separate from the
single-operator ``OPS_TELEGRAM_CHAT_ID`` the alert watcher uses, because this is a
feed a team watches rather than one person's pager.

Two properties, both deliberate:

* **It never raises.** It is called from ``mark_paid`` and from the invoice sweep
  *after* their own commits. A Telegram outage must not turn a settled payment or a
  raised invoice into a failure for a caller that already succeeded, so a message
  that could not be sent is logged and that is the entire cost.
* **Unset is silence, not failure.** With no chat id the feed is simply off. There is
  no obligation to notify, so there is nothing to report and nothing to warn about.

The message text is built here rather than at the call sites so the same event reads
the same way whoever posts it, and so a test can assert on the words without a
database or a Telegram client.
"""

from __future__ import annotations

import logging

from .. import models
from ..config import get_settings
from . import telegram

log = logging.getLogger(__name__)


def configured() -> bool:
    """Whether this deployment can post to the activity group."""
    return bool(_chat_id() and telegram.is_configured())


def _chat_id() -> str | None:
    return (get_settings().activity_telegram_chat_id or "").strip() or None


async def notify_activity(text: str) -> bool:
    """Post one line to the activity group. Never raises; says whether it went."""
    chat_id = _chat_id()
    if not chat_id:
        log.debug("activity feed off (ACTIVITY_TELEGRAM_CHAT_ID unset): %s", _flat(text))
        return False
    try:
        await telegram.send_message(chat_id, text)
    except telegram.TelegramError as exc:
        # Losing a notification must not lose the fact it described, so the text goes
        # to the log too.
        log.error("activity feed delivery failed (%s): %s", exc, _flat(text))
        return False
    return True


def _flat(text: str) -> str:
    return text.replace("\n", " | ")


def _money(cents: int | None) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def format_payment_paid(
    payment: models.Payment,
    store: models.Store,
    *,
    invoice: models.PlanInvoice | None = None,
) -> str:
    """One settled payment, as a line an operator can read on a phone.

    `invoice` is set when this payment settled a plan invoice — the money is the
    platform's own revenue rather than a merchant's sale, and labelling it stops an
    operator reconciling it against the merchant's GMV.
    """
    lines = [
        "✅ Payment settled" if invoice is None else "✅ Plan fee settled",
        f"Amount: {_money(payment.amount_cents)} {payment.currency}",
        f"Store: {store.name}",
    ]
    if payment.reference_id:
        lines.append(f"Reference: {payment.reference_id}")
    lines.append(f"Payment: {payment.public_id}")
    if invoice is not None:
        lines.append(f"Invoice: {invoice.period_month} ({_money(invoice.total_due_cents)})")
    return "\n".join(lines)


def format_invoice_issued(
    invoice: models.PlanInvoice,
    *,
    plan_name: str,
    account_label: str,
) -> str:
    """One raised plan invoice — money owed, whether or not it has been collected."""
    return "\n".join(
        [
            "🧾 Plan invoice raised",
            f"Account: {account_label}",
            f"Plan: {plan_name}",
            f"Period: {invoice.period_month}",
            f"Due: {_money(invoice.total_due_cents)}",
        ]
    )
