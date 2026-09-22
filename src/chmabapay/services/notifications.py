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
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import models
from ..config import get_settings
from . import telegram

log = logging.getLogger(__name__)

# The merchant's own timezone, for the dates that appear inside copy. An instant is stored and
# compared in UTC (§9 of the billing spec); "renews on Oct 12" is a claim about the merchant's
# calendar, so the one place a date becomes words has to know the zone. Cambodia has no DST, but
# the named zone is still what to hold — a hardcoded +07:00 is correct until the day it is not,
# and it would silently mis-render every historical instant if the offset ever changed.
DISPLAY_TZ = ZoneInfo("Asia/Phnom_Penh")


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


def money(cents: int | None) -> str:
    """`$9.99`. Public because a notice carries a formatted amount beside its cents, and two
    formatters for one currency is one formatter too many."""
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
        f"Amount: {money(payment.amount_cents)} {payment.currency}",
        f"Store: {store.name}",
    ]
    if payment.reference_id:
        lines.append(f"Reference: {payment.reference_id}")
    lines.append(f"Payment: {payment.public_id}")
    if invoice is not None:
        lines.append(f"Invoice: {invoice.period_month} ({money(invoice.total_due_cents)})")
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
            f"Due: {money(invoice.total_due_cents)}",
        ]
    )


def format_account_frozen(
    invoice: models.PlanInvoice,
    *,
    account_label: str,
) -> str:
    """One account put on hold for non-payment.

    The audience is the operator, and the two things they need are who and how much — because
    the next thing they will do is either take a bank transfer by phone or answer the merchant
    who calls about it. The grace period is deliberately not restated: the merchant has had a
    week of in-app notices naming this date (§5.2.2), and an ops line that repeats the policy
    invites an argument about the policy instead of a payment.
    """
    return "\n".join(
        [
            "🧊 Account frozen for non-payment",
            f"Account: {account_label}",
            f"Period: {invoice.period_month}",
            f"Unpaid: {money(invoice.total_due_cents)}",
            f"Invoice: {invoice.id}",
        ]
    )


def _local_date(moment: datetime | None) -> str:
    """`Oct 12`, on the merchant's calendar.

    The day is written as a number rather than with `%d` because a leading zero reads like a data
    dump in the middle of a sentence, and because `%-d` is not portable to the platform this is
    developed on.
    """
    if moment is None:
        return ""
    local = moment.astimezone(DISPLAY_TZ)
    return f"{local:%b} {local.day}"


def _period_window(start: datetime | None, end: datetime | None) -> str:
    """` for Oct 12 – Nov 11`, or nothing at all when the window is not known."""
    if start is None or end is None:
        return ""
    return f" for {_local_date(start)} – {_local_date(end)}"


def billing_notice(
    state: str,
    *,
    plan_name: str | None = None,
    amount_cents: int | None = None,
    due_at: datetime | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    freeze_at: datetime | None = None,
) -> tuple[str, str, str]:
    """One notice as (title, body, action label) — the §5.2.2 table, in one place.

    Built here rather than in the portal, for the same reason as every other message in this
    module: one event should read one way whoever renders it, and a second copy of the wording is
    a second version of the truth. It matters more here than elsewhere, because the banner and the
    tier recorded in `plan_invoice_reminders` describe the same moment — the answer to "were we
    warned?" is a *row*, and the sentence the merchant saw has to match it.

    The dates inside the prose are the merchant's local days, which only the server can resolve
    from the UTC instants.

    Every state in `billing.NOTICE_ORDER` has copy, and a test walks that list to prove it rather
    than trusting two modules to be edited together. An unknown state raises: it means a state was
    added to the order without wording, and a silent fallback would render a frozen merchant a
    blank banner.
    """
    plan = plan_name or "your plan"
    amount = money(amount_cents) if amount_cents is not None else None
    due = _local_date(due_at)
    window = _period_window(period_start, period_end)

    if state == "issuance":
        return (
            f"Your {plan} plan renews on {due}",
            f"{amount}{window}. Pay any time before then.",
            "Pay now",
        )
    if state == "due_3":
        return (
            f"Your {plan} plan renews in 3 days",
            f"{amount}{window}. Pay any time before {due}.",
            "Pay now",
        )
    if state == "due_1":
        return (
            f"Your {plan} plan renews tomorrow",
            f"{amount}{window}.",
            "Pay now",
        )
    if state == "due_today":
        return (
            f"Your {plan} plan is due today",
            f"Pay today to keep {plan} active.",
            "Pay now",
        )
    if state == "overdue_1":
        return (
            "Your payment is overdue",
            f"Your plan is still active. Settle {amount} to avoid an interruption.",
            "Pay now",
        )
    if state == "overdue_3":
        # Names the date and what stops. The point of this tier is that the consequence is no
        # longer hypothetical, so it says "will be frozen" rather than "may be".
        return (
            "Your payment is 3 days overdue",
            f"{plan} is still active. On {_local_date(freeze_at)} your account will be frozen "
            "and your stores will stop generating payment codes.",
            "Pay now",
        )
    if state == "overdue_final":
        return (
            "Your account will be frozen tomorrow",
            f"Pay {amount} today. From tomorrow your stores cannot generate new payment codes "
            "and the dashboard becomes read-only, until the invoice is settled or you move to a "
            "plan you can afford.",
            "Pay now",
        )
    if state == "frozen":
        # Says what is *kept*. A merchant whose business just went dark needs the difference
        # between a hold and a deletion, and nothing else in the product tells them.
        kept = "Your data is untouched either way."
        if amount is None:
            # Reachable when an operator froze the account by hand: there is no invoice to settle,
            # and quoting $0.00 would be worse than saying nothing about money.
            body = (
                "New payment codes are stopped and the dashboard is read-only. Contact support "
                f"if you believe this is a mistake. {kept}"
            )
        else:
            body = (
                "New payment codes are stopped and the dashboard is read-only. Settle "
                f"{amount}, or move to a plan you can afford — both are one tap. {kept}"
            )
        return ("Your account is on hold", body, "Resolve billing")

    raise ValueError(f"no notice copy for state {state!r}")


def billing_email(
    state: str,
    *,
    link: str,
    plan_name: str | None = None,
    amount_cents: int | None = None,
    due_at: datetime | None = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    freeze_at: datetime | None = None,
) -> tuple[str, str]:
    """One tier as (subject, body) — the same words the banner uses, plus a way to pay.

    Built by *calling* `billing_notice` rather than writing a second table, which is the whole
    reason this function exists instead of a template per tier. The banner and the email
    describe one moment, and the record of that moment is a row in `plan_invoice_reminders`; if
    the two channels could drift, the row would answer "we warned them" with a sentence the
    merchant never saw. So there is one sentence, in one place.

    No QR is embedded, deliberately. The QR a merchant must scan is minted per attempt and
    bound to a payment row with its own expiry (§6), so an image pasted into an email would be
    a code that is either dead on arrival or alive with no payment behind it. The link lands on
    the billing page, which mints one on tap.

    Plain text, not HTML: there is no design system to keep in sync, no tracking pixel, and a
    dunning notice that survives being forwarded to an accountant is worth more than one that
    looks like a receipt.
    """
    subject, body, action_label = billing_notice(
        state,
        plan_name=plan_name,
        amount_cents=amount_cents,
        due_at=due_at,
        period_start=period_start,
        period_end=period_end,
        freeze_at=freeze_at,
    )
    return subject, "\n".join([body, "", f"{action_label}: {link}", "", "— ChmabaPay"])
