"""Plan invoicing: what a merchant owes, and what a settled invoice unlocks.

Split out of `routers/billing.py` because two callers need it and neither of them is
a router: the billing worker issues invoices on a schedule, and
`services.payments.mark_paid` settles one the moment its KHQR payment is confirmed.
Leaving it in the router would have made the payment path import a router, which is
the dependency direction this codebase keeps out.

Two rules run through the whole module:

* **The period is the key.** An invoice is for an account and a `YYYY-MM`, and there
  is exactly one of those — enforced by `uq_plan_invoice_period`, not by a check the
  next caller might forget. A retried job is therefore a no-op rather than a second
  bill.
* **Nothing activates until it is paid.** A move to a paid tier parks the new
  subscription in `pending`, and `_get_active_sub` only ever reports `trial` or
  `active` — so the merchant keeps their current plan until the money arrives, and
  `settle_invoice_for_payment` is where that promise is kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..config import get_settings

# The credit period a subscription buys. Used for `next_billing_at` on every path, so
# it lives here rather than being spelled `timedelta(days=30)` in three files.
CREDIT_PERIOD = timedelta(days=30)

# A subscription that has been paid for but is not yet in force. Named because the
# string is compared in two modules and a typo would silently grant a paid plan.
SUBSCRIPTION_PENDING = "pending"

# The name of the platform's own store — the one plan fees are collected into. It is
# the identity an operator sets up from the admin console (`PUT /v1/admin/hq-store/link`),
# and the thing `_get_hq_store` looks for when no store id is configured. Lives here
# because three modules have to agree on it: the sign-in seed, the billing lookup, and
# the admin endpoint that writes it.
HQ_STORE_NAME = "ChmabaPay HQ"

# How many missed periods one sweep will catch up on for a single subscription. A
# bounded loop rather than `while due`, because a pathological `next_billing_at` (a
# bad import, a clock jump) would otherwise spin inside a request-scoped worker.
MAX_CATCH_UP_PERIODS = 36


def utcnow() -> datetime:
    return datetime.now(UTC)


def period_month_for(moment: datetime) -> str:
    """The billing period a moment belongs to, as `YYYY-MM`."""
    return f"{moment.year:04d}-{moment.month:02d}"


async def period_usage(
    session: AsyncSession, account_id: int, period_month: str
) -> tuple[int, int]:
    """(net payments, net volume cents) for one account-month, from the ledger.

    Read from `plan_ledger_entries` rather than counted from `payments` on purpose:
    the ledger is the accounting record, it carries one row per movement, and a
    reversal carries a negative one — so a refunded sale stops counting here, which
    is what the merchant expects to be billed on. The quota is enforced from a
    different count; this is the number shown on the invoice.
    """
    res = await session.execute(
        select(
            func.coalesce(func.sum(models.PlanLedgerEntry.total_payments_count), 0),
            func.coalesce(func.sum(models.PlanLedgerEntry.amount_cents_delta), 0),
        ).where(
            models.PlanLedgerEntry.account_id == account_id,
            models.PlanLedgerEntry.period_month == period_month,
        )
    )
    counted, volume_cents = res.one()
    return int(counted), int(volume_cents)


@dataclass(frozen=True)
class HqStoreResolution:
    """Where the platform's collection store came from, and what it is.

    `source` is reported to the operator because the interesting case is a
    *disagreement*: a store pinned by `CHMABAPAY_HQ_STORE_ID` beats the one set in the
    console, so an operator who has just saved a link needs to be able to see that the
    environment variable is the one actually in use.
    """

    store: models.Store | None
    source: str  # "env" | "console" | "fallback" | "none"


async def resolve_hq_store(session: AsyncSession) -> HqStoreResolution:
    """Find the store plan fees are collected into, most explicit source first.

    1. `CHMABAPAY_HQ_STORE_ID` — a deployment that pins the store by hand.
    2. A store named `ChmabaPay HQ` under a platform admin — what an operator sets up
       from the admin console. Named rather than "whichever store the admin happens to
       own", so plan fees cannot land in an operator's unrelated business store.
    3. A platform admin's first active store — a net for a deployment configured before
       the console had this field.

    One implementation, because two callers need the same answer and a second copy
    would eventually disagree with it: the billing route that collects the fee, and the
    console that configures where it goes.
    """
    pinned = (get_settings().chmabapay_hq_store_id or "").strip()
    if pinned:
        stmt = select(models.Store)
        try:
            pinned_int = int(pinned)
            stmt = stmt.where(
                (models.Store.id == pinned_int) | (models.Store.public_id == pinned)
            )
        except ValueError:
            stmt = stmt.where(models.Store.public_id == pinned)
        store = (await session.execute(stmt)).scalar_one_or_none()
        if store is not None:
            return HqStoreResolution(store=store, source="env")

    named = (
        await session.execute(
            select(models.Store)
            .join(models.Account, models.Account.id == models.Store.account_id)
            .where(
                models.Store.name == HQ_STORE_NAME,
                models.Account.is_platform_admin.is_(True),
            )
            .order_by(models.Store.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if named is not None:
        return HqStoreResolution(store=named, source="console")

    admin_account = (
        await session.execute(
            select(models.Account)
            .where(models.Account.is_platform_admin.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if admin_account is not None:
        store = (
            await session.execute(
                select(models.Store)
                .where(
                    models.Store.account_id == admin_account.id,
                    models.Store.status == models.STORE_ACTIVE,
                )
                .order_by(models.Store.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if store is not None:
            return HqStoreResolution(store=store, source="fallback")

    return HqStoreResolution(store=None, source="none")


async def find_period_invoice(
    session: AsyncSession, account_id: int, period_month: str
) -> models.PlanInvoice | None:
    res = await session.execute(
        select(models.PlanInvoice).where(
            models.PlanInvoice.account_id == account_id,
            models.PlanInvoice.period_month == period_month,
        )
    )
    return res.scalar_one_or_none()


async def issue_invoice(
    session: AsyncSession,
    subscription: models.PlanSubscription,
    plan: models.Plan,
    *,
    period_month: str,
    now: datetime | None = None,
) -> models.PlanInvoice | None:
    """Raise the invoice for one subscription-period, or return the existing one.

    Returns None when there is nothing to collect. A free plan bills nothing, and a
    monthly $0 invoice is noise on a merchant's billing page rather than
    information — the quota still applies, it just has no price attached.

    Overage is counted and recorded, never charged. There is no overage price on
    `Plan` to charge at, and the product enforces a quota (`402 quota_exceeded`)
    rather than billing past it. Writing the count keeps the invoice honest about
    the month's usage without inventing a fee, which would be the platform charging
    a number nobody agreed to.

    The caller commits. On a duplicate the existing row is returned untouched: a
    retry must not restamp an invoice that may already have been sent.
    """
    if plan.monthly_fee_cents <= 0:
        return None

    existing = await find_period_invoice(session, subscription.account_id, period_month)
    if existing is not None:
        return existing

    counted, _volume = await period_usage(session, subscription.account_id, period_month)
    included = plan.base_payments_included or 0
    overage = max(0, counted - included)

    invoice = models.PlanInvoice(
        account_id=subscription.account_id,
        subscription_id=subscription.id,
        period_month=period_month,
        status="open",
        base_fee_cents=plan.monthly_fee_cents,
        usage_payments_count=counted,
        overage_payments_count=overage,
        overage_fee_cents=0,
        total_due_cents=plan.monthly_fee_cents,
    )
    session.add(invoice)
    await session.flush()
    return invoice


async def issue_due_invoices(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> dict[str, int]:
    """Issue every invoice that has come due, and advance each schedule.

    `next_billing_at` advances whether or not an invoice was written, and that is the
    part that is easy to get wrong: if a period that produced nothing (a free plan,
    or a row that already existed) left the due date alone, the same subscription
    would be re-examined on every sweep forever — and because the loop would never
    get past that date, the account would never be billed for any later month either.

    The schedule advances from the **due date**, not from `now`, and each step bills
    its own period. A worker that was down for two months must catch those months up
    one at a time rather than forgiving them or collapsing them into one invoice for
    the current month.

    The caller commits; the caller also owns the transaction it commits.
    """
    moment = now or utcnow()
    res = await session.execute(
        select(models.PlanSubscription, models.Plan)
        .join(models.Plan, models.Plan.id == models.PlanSubscription.plan_id)
        .where(
            models.PlanSubscription.status.in_(["trial", "active"]),
            models.PlanSubscription.next_billing_at <= moment,
        )
        .order_by(models.PlanSubscription.next_billing_at)
        .limit(limit)
    )
    due = res.all()

    invoiced = 0
    nothing_to_bill = 0
    advanced = 0
    for subscription, plan in due:
        steps = 0
        while subscription.next_billing_at <= moment:
            if steps >= MAX_CATCH_UP_PERIODS:
                # A pathological due date (a bad import, a clock jump) must not spin
                # here. The remaining periods are left for the next sweep, which will
                # pick up where this one stopped.
                break
            due_period = period_month_for(subscription.next_billing_at)
            invoice = await issue_invoice(
                session,
                subscription,
                plan,
                period_month=due_period,
                now=moment,
            )
            if invoice is None:
                nothing_to_bill += 1
            else:
                invoiced += 1
            subscription.next_billing_at = subscription.next_billing_at + CREDIT_PERIOD
            steps += 1
        subscription.updated_at = moment
        advanced += steps

    await session.commit()
    return {
        "subscriptions_due": len(due),
        "periods_advanced": advanced,
        "invoices": invoiced,
        "nothing_to_bill": nothing_to_bill,
    }


async def settle_invoice_for_payment(
    session: AsyncSession,
    payment: models.Payment,
    *,
    paid_at: datetime,
) -> models.PlanInvoice | None:
    """Mark the invoice this payment settles as paid, and put its plan in force.

    Called from `services.payments.mark_paid` **before its commit**, so the payment,
    the invoice and the plan change become durable together. An invoice reading
    `paid` while the plan it bought never activated would be worse than either
    failure on its own: the merchant has been charged and received nothing.

    Returns None when this payment is not an invoice payment, which is the common
    case — every merchant payment on the platform comes through here.
    """
    res = await session.execute(
        select(models.PlanInvoice).where(
            models.PlanInvoice.chmabapay_payment_id == payment.id
        )
    )
    invoice = res.scalar_one_or_none()
    if invoice is None or invoice.status == "paid":
        return None

    invoice.status = "paid"
    invoice.paid_at = paid_at
    invoice.updated_at = utcnow()

    if invoice.subscription_id is not None:
        await activate_subscription(session, invoice.subscription_id)
    return invoice


async def activate_subscription(
    session: AsyncSession, subscription_id: int
) -> models.PlanSubscription | None:
    """Put a paid-for subscription in force and retire whatever it replaces.

    Exactly one subscription per account can be `active`, because `_get_active_sub`
    reads a single row and would raise on two. So the retirement of the old plan and
    the activation of the new one are one operation, not two steps a caller could
    half-finish.

    A subscription that is not `pending` is left alone: this is called from the
    settlement path, and re-activating something already active (or canceled) would
    turn a stray retry into a plan change.
    """
    subscription = await session.get(models.PlanSubscription, subscription_id)
    if subscription is None or subscription.status != SUBSCRIPTION_PENDING:
        return None

    now = utcnow()
    others = (
        await session.execute(
            select(models.PlanSubscription).where(
                models.PlanSubscription.account_id == subscription.account_id,
                models.PlanSubscription.id != subscription.id,
                models.PlanSubscription.status.in_(["trial", "active"]),
            )
        )
    ).scalars().all()
    for other in others:
        other.status = "canceled"
        other.canceled_at = now
        other.updated_at = now

    subscription.status = "active"
    subscription.started_at = subscription.started_at or now
    subscription.updated_at = now
    return subscription
