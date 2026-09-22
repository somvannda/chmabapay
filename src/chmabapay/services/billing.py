"""Plan invoicing: what a merchant owes, and what a settled invoice unlocks.

Split out of `routers/billing.py` because two callers need it and neither of them is
a router: the billing worker issues invoices on a schedule, and
`services.payments.mark_paid` settles one the moment its KHQR payment is confirmed.
Leaving it in the router would have made the payment path import a router, which is
the dependency direction this codebase keeps out.

Three rules run through the whole module:

* **The window is the key.** An invoice is for a subscription and a `period_start`, and
  there is exactly one *live* one of those — enforced by a partial unique index, not by a
  check the next caller might forget. A retried job is therefore a no-op rather than a
  second bill. The key used to be `(account_id, period_month)`, which drifted against the
  30-day credit and skipped months outright.
* **The schedule moves only on payment.** `next_billing_at` is the instant coverage ends,
  and issuing an invoice never touches it. That is what makes a lapse visible: a merchant
  who does not pay has a period end in the past and no new invoice, because the window is
  already claimed.
* **Nothing activates until it is paid.** A move to a paid tier parks the new
  subscription in `pending`, and `_get_active_sub` only ever reports `trial` or
  `active` — so the merchant keeps their current plan until the money arrives, and
  `settle_invoice_for_payment` is where that promise is kept.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..config import get_settings
from . import notifications, resend

log = logging.getLogger(__name__)

# The credit period a subscription buys. Used for `next_billing_at` on every path, so
# it lives here rather than being spelled `timedelta(days=30)` in three files.
CREDIT_PERIOD = timedelta(days=30)

# A subscription that has been paid for but is not yet in force. Named because the
# string is compared in two modules and a typo would silently grant a paid plan.
SUBSCRIPTION_PENDING = "pending"

# Invoice statuses. `draft` and `issued` were both once written for "unpaid"; they are now
# legacy *aliases* that are only ever read, because two names for one state is how the
# wrong one ends up in a query — which is exactly what happened here.
INVOICE_OPEN = "open"
INVOICE_PAID = "paid"
INVOICE_VOID = "void"
LEGACY_UNPAID = ("draft", "issued")
UNPAID_STATUSES = (INVOICE_OPEN, *LEGACY_UNPAID)

# Why an invoice was voided. Recorded rather than inferred, because "this never should
# have been raised" and "the merchant chose a cheaper plan" are different stories to tell
# a year later, and only one of them is a churn signal.
VOID_DOWNGRADED = "downgraded"
VOID_SUPERSEDED = "superseded"
VOID_GRACE_EXPIRED = "grace_expired"
VOID_PRE_LIFECYCLE = "pre_lifecycle"
VOID_OPERATOR = "operator"

# The name of the platform's own store — the one plan fees are collected into. It is
# the identity an operator sets up from the admin console (`PUT /v1/admin/hq-store/link`),
# and the thing `_get_hq_store` looks for when no store id is configured. Lives here
# because three modules have to agree on it: the sign-in seed, the billing lookup, and
# the admin endpoint that writes it.
HQ_STORE_NAME = "ChmabaPay HQ"

# The dunning clock, as offsets from `due_at`: when the platform says each of these things.
# One table, read by both the reminder worker and the notice endpoint, so the banner and the
# recorded tiers cannot disagree about what the merchant is being told (§5.2).
TIER_OFFSETS: tuple[tuple[str, timedelta], ...] = (
    ("due_3", timedelta(days=-3)),
    ("due_1", timedelta(days=-1)),
    ("due_today", timedelta(days=0)),
    ("overdue_1", timedelta(days=1)),
    ("overdue_3", timedelta(days=3)),
    ("overdue_final", timedelta(days=6)),
)
# The tiers that can appear in `plan_invoice_reminders.tier`.
TIERS: tuple[str, ...] = tuple(name for name, _offset in TIER_OFFSETS)

# States that are derived rather than recorded. `issuance` is a notice — "your plan renews on
# the 12th" — and not a tier, because a row per invoice saying "we told them" before anything
# was due would make the reminder table bookkeeping rather than evidence (§5.2.2).
STATE_ISSUANCE = "issuance"
# Derived from the *account*, not from the invoice, and outranks every tier: it is the one
# thing a frozen merchant needs to see (§5.2.2).
STATE_FROZEN = "frozen"

# The only channel Phase A has. It is in the `(invoice_id, tier, channel)` key from the start
# so email can be added later without re-sending the in-app rows (§4.2).
CHANNEL_IN_APP = "in_app"

# Phase B. A second row for the same tier rather than a flag on the first, because the two
# channels can fail independently: an email provider that was down for `overdue_1` must not
# make the platform forget that the banner showed it.
CHANNEL_EMAIL = "email"

# How loud each state is, and the portal styles from this rather than from the state's name — so
# adding a tier is a copy decision, not a client release.
NOTICE_LEVELS: dict[str, str] = {
    STATE_ISSUANCE: "info",
    "due_3": "info",
    "due_1": "info",
    "due_today": "warning",
    "overdue_1": "warning",
    "overdue_3": "warning",
    "overdue_final": "critical",
    STATE_FROZEN: "critical",
}

# Ascending urgency, and the order the copy table reads in. A state's position *is* its rank, so
# the list stays the single place a new state has to be added — a tier inserted out of order here
# would be a banner that shows the less urgent of two notices.
NOTICE_ORDER: tuple[str, ...] = (STATE_ISSUANCE, *TIERS, STATE_FROZEN)

# Only the informational notices can be dismissed. A warning about money that will stop the
# merchant's business should not be one scroll-past away from invisible, and `frozen` is the
# state itself rather than news about it (§5.2.3).
DISMISSIBLE_LEVELS: frozenset[str] = frozenset({"info"})


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(moment: datetime | None) -> datetime | None:
    """Normalize a timestamp read back from the database.

    SQLite hands back naive datetimes for a tz-aware column and Postgres does not, so an
    unguarded comparison against `datetime.now(UTC)` works in production and raises in the
    suite. Public rather than private because `routers/billing.py` needs the same guard to
    derive `is_overdue`.
    """
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def grace_period() -> timedelta:
    """How long an unpaid invoice may run past its due date before the account is frozen.

    A setting rather than a constant because it is policy — it is the number support gets
    asked about. It is also the line `settle_invoice` uses to tell "paid before the freeze"
    from "paid after it", so the two sides read it from one place.
    """
    return timedelta(days=max(0, int(get_settings().billing_grace_days)))


def period_month_for(moment: datetime) -> str:
    """The billing period a moment belongs to, as `YYYY-MM`.

    A display label now, not a key: nothing is decided from it. `period_month_for` on the
    invoice's `period_start` produces the same string for the same window on every path.
    """
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


async def find_invoice_for_window(
    session: AsyncSession, subscription_id: int, period_start: datetime
) -> models.PlanInvoice | None:
    """The live invoice for one subscription-window, if there is one.

    "Live" means not void, which is the same predicate the partial unique index enforces —
    so this lookup and the constraint agree by construction rather than by coincidence.
    """
    res = await session.execute(
        select(models.PlanInvoice).where(
            models.PlanInvoice.subscription_id == subscription_id,
            models.PlanInvoice.period_start == period_start,
            models.PlanInvoice.status != INVOICE_VOID,
        )
    )
    return res.scalar_one_or_none()


async def find_open_invoice(
    session: AsyncSession, account_id: int
) -> models.PlanInvoice | None:
    """The oldest unpaid invoice on the account, or None.

    Used to refuse a new purchase while one is already outstanding. Two unpaid periods
    settled through one QR flow is a conversation rather than a checkout, and oldest-first
    means the refusal can name the invoice the merchant should actually pay.
    """
    res = await session.execute(
        select(models.PlanInvoice)
        .where(
            models.PlanInvoice.account_id == account_id,
            models.PlanInvoice.status.in_(UNPAID_STATUSES),
        )
        .order_by(models.PlanInvoice.id)
        .limit(1)
    )
    return res.scalar_one_or_none()


async def issue_invoice(
    session: AsyncSession,
    subscription: models.PlanSubscription,
    plan: models.Plan,
    *,
    period_start: datetime,
    period_end: datetime,
    due_at: datetime,
) -> tuple[models.PlanInvoice | None, bool]:
    """Raise the invoice for one subscription-window, or return the existing one.

    Returns ``(invoice, created)``, and ``(None, False)`` when there is nothing to
    collect. A free plan bills nothing, and a monthly $0 invoice is noise on a
    merchant's billing page rather than information — the quota still applies, it just
    has no price attached.

    ``created`` is what lets a caller announce a *new* invoice without re-announcing
    one a retry found already there: the second element is False on every path that
    returned a row rather than wrote one.

    Overage is counted and recorded, never charged. There is no overage price on
    `Plan` to charge at, and the product enforces a quota (`402 quota_exceeded`)
    rather than billing past it. Because nothing is metered, nothing about the amount
    depends on waiting for the period to finish — which is what makes billing *ahead* of
    the period possible at all.

    The caller commits. On a duplicate the existing row is returned untouched: a
    retry must not restamp an invoice that may already have been sent.
    """
    if plan.monthly_fee_cents <= 0:
        return None, False

    existing = await find_invoice_for_window(session, subscription.id, period_start)
    if existing is not None:
        return existing, False

    label = period_month_for(period_start)
    counted, _volume = await period_usage(session, subscription.account_id, label)
    included = plan.base_payments_included or 0
    overage = max(0, counted - included)

    invoice = models.PlanInvoice(
        account_id=subscription.account_id,
        subscription_id=subscription.id,
        period_month=label,
        period_start=period_start,
        period_end=period_end,
        due_at=due_at,
        status=INVOICE_OPEN,
        base_fee_cents=plan.monthly_fee_cents,
        usage_payments_count=counted,
        overage_payments_count=overage,
        overage_fee_cents=0,
        total_due_cents=plan.monthly_fee_cents,
    )
    session.add(invoice)
    await session.flush()
    return invoice, True


async def issue_due_invoices(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> dict[str, int]:
    """Raise the renewal invoices that have entered their lead window.

    The predicate is `next_billing_at - lead <= now`, not `next_billing_at <= now`. With no
    mandate to charge a stored credential, a merchant has to be *asked* before the coverage
    they paid for runs out — an invoice raised after the fact has no leverage behind it and
    no time to be paid.

    **`next_billing_at` is deliberately not advanced here.** It moves when the invoice is
    settled (`settle_invoice_for_payment`), which is what makes a lapse visible: a merchant
    who does not pay keeps a period end in the past, and the guard below refuses to raise a
    second invoice for a window that is already claimed. Advancing on issue — which this
    used to do, in a catch-up loop — made the lapse invisible and let a merchant keep a paid
    plan indefinitely without paying for it.

    The guard and the partial unique index say the same thing: one live invoice per
    subscription-window. A retried run, a restarted process and two replicas therefore
    converge on one invoice; a lost race costs the worker a retry, not the merchant a second
    charge.

    Free plans are not swept at all. A $0 invoice is noise on a billing page rather than
    information, and there is no period to advance toward, so the fee filter replaces the
    old "advance even when nothing was written" rule that made the loop terminate.

    The caller commits; the caller also owns the transaction it commits.
    """
    settings = get_settings()
    lead = timedelta(days=max(0, int(settings.billing_lead_days)))
    moment = now or utcnow()

    already_claimed = (
        select(models.PlanInvoice.id)
        .where(
            models.PlanInvoice.subscription_id == models.PlanSubscription.id,
            models.PlanInvoice.period_start == models.PlanSubscription.next_billing_at,
            models.PlanInvoice.status != INVOICE_VOID,
        )
        .correlate(models.PlanSubscription)
        .exists()
    )

    res = await session.execute(
        select(models.PlanSubscription, models.Plan)
        .join(models.Plan, models.Plan.id == models.PlanSubscription.plan_id)
        .where(
            models.PlanSubscription.status.in_(["trial", "active"]),
            models.Plan.monthly_fee_cents > 0,
            models.PlanSubscription.next_billing_at <= moment + lead,
            ~already_claimed,
        )
        .order_by(models.PlanSubscription.next_billing_at)
        .limit(limit)
    )
    due = res.all()

    invoiced = 0
    nothing_to_bill = 0
    raised: list[tuple[models.PlanInvoice, models.Plan]] = []
    for subscription, plan in due:
        invoice, created = await issue_invoice(
            session,
            subscription,
            plan,
            period_start=subscription.next_billing_at,
            period_end=subscription.next_billing_at + CREDIT_PERIOD,
            due_at=subscription.next_billing_at,
        )
        if invoice is None:
            nothing_to_bill += 1
        else:
            invoiced += 1
            if created:
                raised.append((invoice, plan))

    await session.commit()
    # After the commit, never before: an announcement is a claim that the invoice
    # exists, and the row is only real once it is durable. A delivery failure here
    # cannot fail the sweep — `notify_activity` swallows its own errors — so a
    # Telegram outage never costs the billing run.
    await _announce_raised_invoices(session, raised)
    return {
        "subscriptions_due": len(due),
        "invoices": invoiced,
        "nothing_to_bill": nothing_to_bill,
    }


async def _announce_raised_invoices(
    session: AsyncSession,
    raised: list[tuple[models.PlanInvoice, models.Plan]],
) -> None:
    """Post each newly raised invoice to the activity feed.

    Batched on purpose: one lookup names every account in the sweep, rather than a
    query per invoice. An account with no email row is labelled by id rather than
    dropped — the invoice still happened.
    """
    if not raised:
        return
    account_ids = {invoice.account_id for invoice, _plan in raised}
    rows = await session.execute(
        select(models.Account.id, models.Account.email).where(
            models.Account.id.in_(account_ids)
        )
    )
    labels = {row.id: row.email for row in rows}
    for invoice, plan in raised:
        await notifications.notify_activity(
            notifications.format_invoice_issued(
                invoice,
                plan_name=plan.name,
                account_label=labels.get(
                    invoice.account_id, f"account {invoice.account_id}"
                ),
            )
        )


def derived_state(
    account: models.Account | None,
    invoice: models.PlanInvoice,
    now: datetime,
) -> str | None:
    """What this merchant is owed for this invoice right now, or None for silence.

    Two callers — the reminder worker records it, the notice endpoint renders it — and exactly
    one implementation, because a banner reading "due today" while the reminder table says
    `overdue_3` is the platform contradicting itself about money.

    *The most advanced threshold reached*, not every threshold passed: a sweep that was down for
    four days must not deliver four stale warnings at once. Selecting the last tier whose offset
    has passed collapses the backlog into the one message that is true now, and because the
    answer is derived from `due_at` rather than read from a stored pointer, that collapse needs
    no bookkeeping to recover from an outage.

    None means there is nothing to say: a settled (or voided) invoice asks for nothing, one with
    no window has no clock to read, and a suspended account cannot sign in to see a notice
    anyway.
    """
    if account is None or invoice.status not in UNPAID_STATUSES:
        return None
    # `suspended` is asked about *before* `restricted`, and the order is the rule rather than a
    # shortcut. An operator's lockout outranks a billing state, so nothing here may describe a
    # suspended account as merely on a billing hold — and nothing may weaken that lockout into
    # an account that can sign in to a read-only dashboard (§9). Reading a suspended account's
    # invoice as a billing matter is the first step towards treating it as one.
    if account.status == models.ACCOUNT_SUSPENDED:
        return None
    if account.status == models.ACCOUNT_RESTRICTED:
        return STATE_FROZEN
    due_at = as_utc(invoice.due_at)
    if due_at is None:
        return None
    state = STATE_ISSUANCE
    for tier, offset in TIER_OFFSETS:
        if now >= due_at + offset:
            state = tier
    return state


async def find_reminder(
    session: AsyncSession,
    invoice_id: int,
    tier: str,
    *,
    channel: str = CHANNEL_IN_APP,
) -> models.PlanInvoiceReminder | None:
    """The row recording that this tier was announced, if it ever was.

    `channel` is a parameter rather than a constant read, because the two channels ask the same
    question about different records: "did the banner show this?" and "did the email go?" are
    distinct claims, and the whole point of keying the table on `(invoice_id, tier, channel)` is
    that they can differ.
    """
    return (
        await session.execute(
            select(models.PlanInvoiceReminder).where(
                models.PlanInvoiceReminder.invoice_id == invoice_id,
                models.PlanInvoiceReminder.tier == tier,
                models.PlanInvoiceReminder.channel == channel,
            )
        )
    ).scalar_one_or_none()


def portal_url(path: str) -> str:
    """A notice's deep link, as something clickable from an inbox.

    `_render_notice` hands the portal a *path*, because the browser it is rendered in already
    knows the origin. Email has no origin to resolve against, so the deployment's public one is
    prepended — and where `PUBLIC_ORIGIN` is unset (a dev deployment) the path is returned
    alone, which is a worse link than an absolute one but not a broken one.
    """
    origin = (get_settings().public_origin or "").rstrip("/")
    return f"{origin}{path}" if origin else path


def _unpaid_invoice_query(*, respect_admin_exemption: bool):
    """Invoices that have a dunning clock, with the account and subscription they hang off.

    These guards are load-bearing in both callers and they say the same four things:

    * the invoice is unpaid — a settled one asks for nothing;
    * it has a `due_at` — a pre-lifecycle row has no clock to read;
    * its subscription is `trial`/`active` — a `pending` invoice is an upgrade the merchant is
      not on the hook for yet, and dunning it would be chasing a plan nobody is using (§5.5);
    * the account is `active` — a suspended account is an operator's decision, and one already
      `restricted` has been frozen once and does not need freezing again (§5.4).

    `respect_admin_exemption` is on for the caller that *changes* something and off for the one
    that only records, and the asymmetry is the whole point. The platform-admin account owns the
    HQ store plan fees are collected into (`resolve_hq_store`), so freezing it is the platform
    locking itself out of its own collection path — a billing job must never be able to do that.
    Reminders keep the exemption off because the platform owner should still be told their own
    invoice is due: that is a message, not a consequence.
    """
    stmt = (
        select(models.PlanInvoice, models.PlanSubscription, models.Account)
        .join(
            models.PlanSubscription,
            models.PlanSubscription.id == models.PlanInvoice.subscription_id,
        )
        .join(models.Account, models.Account.id == models.PlanInvoice.account_id)
        .where(
            models.PlanInvoice.status.in_(UNPAID_STATUSES),
            models.PlanInvoice.due_at.is_not(None),
            models.PlanSubscription.status.in_(["trial", "active"]),
            models.Account.status == models.ACCOUNT_ACTIVE,
        )
        .order_by(models.PlanInvoice.due_at)
    )
    if respect_admin_exemption:
        stmt = stmt.where(models.Account.is_platform_admin.is_(False))
    return stmt


async def record_due_reminders(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 500
) -> dict[str, int]:
    """Record the tier each unpaid invoice has reached, and deliver it by email.

    For `in_app` the insert *is* the whole operation: nothing is delivered afterwards, so
    nothing can fail after it, and two replicas racing produce one row because the loser's
    insert hits `(invoice_id, tier, channel)`. That asymmetry is deliberate — this table is the
    record that the platform warned them, which is what answers "were they warned before we
    froze them?" during a dispute.

    It is deliberately *not* what makes a warning visible. The banner is derived from `due_at`
    by the notice endpoint, so a worker that never ran cannot hide a notice the merchant was
    owed; this table only ever adds accountability.

    The email channel (Phase B) is the second half, and it is a second pass rather than a step
    inside this loop, because it is the only part that can fail on someone else's infrastructure:
    the in-app rows are committed before a single message is handed to the provider, so a Resend
    outage costs the email and never the record of the warning.

    The caller commits.
    """
    moment = now or utcnow()
    rows = list(
        (
            await session.execute(
                _unpaid_invoice_query(respect_admin_exemption=False).limit(limit)
            )
        ).all()
    )

    recorded = 0
    silent = 0
    # Every invoice at a tier this hour, whether the in-app row was written just now or in an
    # earlier sweep. The email phase wants the first kind for its own reasons and the second
    # kind for one specific reason: a deployment that switches the channel on mid-cycle owes
    # the merchant the tier they are currently at, and owes them nothing for the tiers already
    # past — which is the "non-retroactive" property §4.2 buys with the `channel` column.
    candidates: list[tuple[models.PlanInvoice, models.PlanSubscription, models.Account, str]] = []
    for invoice, subscription, account in rows:
        state = derived_state(account, invoice, moment)
        if state is None or state not in TIERS:
            # `issuance` is a notice with no tier, and the invoice itself is the record that it
            # was raised. Nothing to write.
            silent += 1
            continue
        candidates.append((invoice, subscription, account, state))
        if await find_reminder(session, invoice.id, state) is not None:
            silent += 1
            continue
        try:
            # A savepoint, so a sibling replica that got here first costs this run one skipped
            # row instead of the whole sweep's transaction.
            async with session.begin_nested():
                session.add(
                    models.PlanInvoiceReminder(
                        invoice_id=invoice.id,
                        tier=state,
                        channel=CHANNEL_IN_APP,
                        sent_at=moment,
                    )
                )
        except IntegrityError:
            silent += 1
            continue
        recorded += 1

    await session.commit()

    emailed, email_failed, email_silent = await _email_reminders(session, candidates, moment)
    return {
        "considered": len(rows),
        "recorded": recorded,
        "silent": silent,
        "emailed": emailed,
        "email_failed": email_failed,
        "email_silent": email_silent,
    }


async def _email_reminders(
    session: AsyncSession,
    candidates: list[tuple[models.PlanInvoice, models.PlanSubscription, models.Account, str]],
    moment: datetime,
) -> tuple[int, int, int]:
    """Deliver the tier by email and record what happened. Returns (sent, failed, silent).

    Three things about this shape, all of them the point rather than the plumbing:

    * **The row is claimed before the message is sent.** The unique key on
      `(invoice_id, tier, channel)` is what stops two replicas sending the same tier, and it can
      only do that if the row exists first — a send-then-record order would double-send in
      exactly the race the key was added to prevent.
    * **A failure is recorded, not retried.** The row stays with the reason in `detail`, so an
      absent email row always means "we never tried", which is the claim a dispute turns on. A
      transient provider error therefore costs one tier and the next tier is a new row; the
      in-app banner, which needs no provider, remains the channel the merchant is guaranteed.
    * **`detail` is written after the send because it *is* the delivery result.** "We recorded
      that we sent it" and "they received it" are different claims, and the provider id is the
      only bridge between the two.
    """
    if not resend.is_configured():
        # Silence, not failure: no key means this deployment has no email channel, which is the
        # state every deployment was in before Phase B.
        return 0, 0, len(candidates)

    plans = {plan.id: plan for plan in (await session.execute(select(models.Plan))).scalars()}

    sent = 0
    failed = 0
    silent = 0
    for invoice, subscription, account, state in candidates:
        if await find_reminder(session, invoice.id, state, channel=CHANNEL_EMAIL) is not None:
            silent += 1
            continue
        plan = plans.get(subscription.plan_id)
        due_at = as_utc(invoice.due_at)
        subject, text = notifications.billing_email(
            state,
            link=portal_url(f"/dashboard/billing?pay={invoice.id}"),
            plan_name=plan.name if plan is not None else None,
            amount_cents=invoice.total_due_cents,
            due_at=due_at,
            period_start=as_utc(invoice.period_start),
            period_end=as_utc(invoice.period_end),
            # The same instant the banner promises, from the same grace period the enforcement
            # job reads — so the date in the email and the date acted on cannot differ.
            freeze_at=due_at + grace_period() if due_at is not None else None,
        )
        row = models.PlanInvoiceReminder(
            invoice_id=invoice.id,
            tier=state,
            channel=CHANNEL_EMAIL,
            sent_at=moment,
        )
        try:
            async with session.begin_nested():
                session.add(row)
        except IntegrityError:
            silent += 1
            continue
        try:
            provider_id = await resend.send_email(
                to=account.email,
                subject=subject,
                text=text,
                # Deterministic rather than clock-derived: a sweep that dies between the send
                # and the commit retries the same tier under the same key, and Resend collapses
                # it instead of delivering a second copy.
                idempotency_key=f"plan-invoice-{invoice.id}-{state}",
            )
        except resend.ResendError as exc:
            row.detail = {"delivery": "failed", "error": str(exc)}
            failed += 1
            log.error(
                "billing reminder email not delivered (invoice=%s tier=%s to=%s): %s",
                invoice.id,
                state,
                account.email,
                exc,
            )
        else:
            row.detail = {"delivery": "sent", "provider_id": provider_id}
            sent += 1

    await session.commit()
    return sent, failed, silent


async def derive_notices(
    session: AsyncSession, account: models.Account, *, now: datetime | None = None
) -> list[dict[str, object]]:
    """At most one notice: the most urgent thing this merchant is owed, or nothing at all.

    One notice, because a banner is one line. The rest are on the billing page, where every
    invoice is listed with its own due date and badge — that page is the backstop which keeps a
    dismissed banner from becoming a hidden debt (§5.2.3), so the endpoint never has to return a
    queue of them.

    The state is *derived* here, never read from `plan_invoice_reminders`. That table records what
    the platform said; this decides what the merchant is owed. A worker that never ran therefore
    cannot hide a warning the merchant was due (A-17) — deleting every tier row changes nothing
    about what this returns.

    `frozen` is a property of the account rather than of any row, so a frozen merchant gets the
    hold notice even with no open invoice at all. That is the one state which must always be
    reachable: it is the only place the product tells them their data was not deleted.
    """
    moment = now or utcnow()
    rows = (
        await session.execute(
            select(models.PlanInvoice, models.Plan)
            .join(
                models.PlanSubscription,
                models.PlanSubscription.id == models.PlanInvoice.subscription_id,
            )
            .join(models.Plan, models.Plan.id == models.PlanSubscription.plan_id)
            .where(
                models.PlanInvoice.account_id == account.id,
                models.PlanInvoice.status.in_(UNPAID_STATUSES),
                models.PlanInvoice.due_at.is_not(None),
                models.PlanSubscription.status.in_(["trial", "active"]),
            )
            .order_by(models.PlanInvoice.due_at)
        )
    ).all()

    if account.status == models.ACCOUNT_RESTRICTED:
        # Outranks every tier, and does not depend on the invoice that caused it still existing —
        # a merchant freed by an operator's hand, or frozen by one, still needs to be told.
        invoice, plan = rows[0] if rows else (None, None)
        return [_render_notice(STATE_FROZEN, invoice=invoice, plan=plan, moment=moment)]

    best: dict[str, object] | None = None
    for invoice, plan in rows:
        state = derived_state(account, invoice, moment)
        if state is None:
            # A suspended account, or an invoice whose clock cannot be read. Silence is right for
            # both: `suspended` has no session to render a banner in.
            continue
        notice = _render_notice(state, invoice=invoice, plan=plan, moment=moment)
        if best is None or _notice_rank(notice) > _notice_rank(best):
            best = notice
    return [best] if best is not None else []


def _notice_rank(notice: dict[str, object]) -> tuple[int, int]:
    """Sort key for "most urgent": the more advanced state first, then the older debt.

    The state's position in `NOTICE_ORDER` is the whole ordering, which is why that tuple is
    declared in the order the copy table reads in. A tie — two invoices at the same state — goes
    to the one that has been waiting longest, so the banner is stable rather than flipping
    between two equally-true sentences.
    """
    days = notice["days_until_due"]
    return (
        NOTICE_ORDER.index(str(notice["state"])),
        -(int(days) if isinstance(days, int) else 0),
    )


def _render_notice(
    state: str,
    *,
    invoice: models.PlanInvoice | None,
    plan: models.Plan | None,
    moment: datetime,
) -> dict[str, object]:
    """One state, as the payload the portal renders.

    Every field the portal reads is computed here and not derived client-side: the level (so the
    portal does not keep its own state-to-colour map), `dismissible` (so the dismissal policy is
    not re-implemented in the browser), and the copy itself. The client's job is to place the
    strings and follow the link.
    """
    due_at = as_utc(invoice.due_at) if invoice is not None else None
    days_until_due = None if due_at is None else (due_at - moment).days
    amount_cents = invoice.total_due_cents if invoice is not None else None

    title, body, action_label = notifications.billing_notice(
        state,
        plan_name=plan.name if plan is not None else None,
        amount_cents=amount_cents,
        due_at=due_at,
        period_start=as_utc(invoice.period_start) if invoice is not None else None,
        period_end=as_utc(invoice.period_end) if invoice is not None else None,
        # What the copy promises: the day the account stops working. Computed from the same grace
        # period the enforcement job uses, so the date named and the date acted on cannot differ.
        freeze_at=due_at + grace_period() if due_at is not None else None,
    )
    level = NOTICE_LEVELS[state]
    invoice_id = invoice.id if invoice is not None else None
    return {
        "state": state,
        "level": level,
        "title": title,
        "body": body,
        "invoice_id": invoice_id,
        "period_label": invoice.period_month if invoice is not None else None,
        "amount_cents": amount_cents,
        "amount_formatted": (
            notifications.money(amount_cents) if amount_cents is not None else None
        ),
        "due_at": due_at,
        "days_until_due": days_until_due,
        "action_label": action_label,
        "action_url": (
            f"/dashboard/billing?pay={invoice_id}"
            if invoice_id is not None
            else "/dashboard/billing"
        ),
        "dismissible": level in DISMISSIBLE_LEVELS,
    }


async def enforce_grace(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 500
) -> dict[str, int | bool]:
    """Freeze the accounts whose grace has run out, and retire purchases nobody will pay for.

    The one irreversible step in the sequence, and the only writer of `restricted`. It is
    written as "change exactly one field", on purpose: the invoice is **not** voided — it is the
    debt, and settling it is one of the three ways out (§7.5), so voiding it here would remove
    the very thing the merchant is being asked to pay — the plan is not changed, and not one
    store row is touched. That is what makes unfreezing a single status write (§7.9), and why a
    merchant's 50 stores come back the moment they pay.

    Gated by `billing_enforce_enabled`. With the flag off it reports what it *would* have frozen
    and changes nothing, so the whole sequence can run in production — invoices raised, tiers
    recorded, notices rendered — before it is allowed to cost anyone anything.

    The abandoned-purchase half is not gated: cancelling a `pending` subscription grants nothing
    and takes nothing away, and the guard it releases is one merchants meet in normal use.

    **Three accounts are never candidates, each for its own reason.** A `restricted` one is
    already frozen. A `suspended` one is an operator's abuse decision, and a billing job must not
    be able to turn that lockout into a read-only account that can sign in — a weaker state, on a
    decision that was not its own (§9). And a platform admin is exempt (D4), because that account
    owns the HQ store plan fees are collected into: freezing it is the platform locking itself out
    of collecting its own revenue. All three come from the one query above rather than from a
    guard in the loop, so there is no path into the freeze that skips them.

    The caller commits; each function here owns its own transaction and commits it.
    """
    settings = get_settings()
    moment = now or utcnow()
    grace = grace_period()

    rows = list(
        (
            await session.execute(
                _unpaid_invoice_query(respect_admin_exemption=True).limit(limit)
            )
        ).all()
    )
    due: list[tuple[models.PlanInvoice, models.Account]] = []
    for invoice, _subscription, account in rows:
        due_at = as_utc(invoice.due_at)
        if due_at is not None and due_at + grace <= moment:
            due.append((invoice, account))

    if not settings.billing_enforce_enabled:
        for invoice, account in due:
            log.warning(
                "W6 enforce is off: would freeze account %s (invoice %s, %s for %s) at %s",
                account.id,
                invoice.id,
                invoice.period_month,
                invoice.total_due_cents,
                moment.isoformat(),
            )
    else:
        for invoice, account in due:
            account.status = models.ACCOUNT_RESTRICTED
            account.updated_at = moment
            audit.record(
                session,
                actor=None,
                action="billing.account_frozen",
                target_type="Account",
                target_id=account.id,
                details={
                    "invoice_id": invoice.id,
                    "period_month": invoice.period_month,
                    "total_due_cents": invoice.total_due_cents,
                    "due_at": as_utc(invoice.due_at).isoformat() if invoice.due_at else None,
                    "grace_days": int(settings.billing_grace_days),
                },
            )

    abandoned = await _expire_abandoned_purchases(session, now=moment)
    await session.commit()

    if settings.billing_enforce_enabled:
        # After the commit, never before: an announcement is a claim that the freeze is durable.
        # `notify_activity` swallows its own failures, so a Telegram outage cannot fail the run.
        for invoice, account in due:
            await notifications.notify_activity(
                notifications.format_account_frozen(invoice, account_label=account.email)
            )

    return {
        "enforced": bool(settings.billing_enforce_enabled),
        "due": len(due),
        "frozen": len(due) if settings.billing_enforce_enabled else 0,
        "abandoned_purchases": abandoned,
    }


async def _expire_abandoned_purchases(
    session: AsyncSession, *, now: datetime, limit: int = 500
) -> int:
    """Retire the parked purchases nobody paid for, and release the guard they hold.

    An upgrade parks a `pending` subscription and an invoice; `change_plan` refuses a new
    purchase while any invoice is open (T-05). A merchant who changes their mind and never pays
    would therefore block their own next purchase forever. One credit period is long enough that
    the offer has certainly gone stale, and the merchant has had a full renewal cycle of notices
    about it.

    Voided `superseded`, not `downgraded`: nobody chose a different plan, the offer simply
    expired — and those two stories should not look the same in a churn report a year later.
    """
    cutoff = now - CREDIT_PERIOD
    parked = list(
        (
            await session.execute(
                select(models.PlanSubscription)
                .where(
                    models.PlanSubscription.status == SUBSCRIPTION_PENDING,
                    models.PlanSubscription.created_at <= cutoff,
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for subscription in parked:
        invoices = list(
            (
                await session.execute(
                    select(models.PlanInvoice).where(
                        models.PlanInvoice.subscription_id == subscription.id,
                        models.PlanInvoice.status.in_(UNPAID_STATUSES),
                    )
                )
            )
            .scalars()
            .all()
        )
        for invoice in invoices:
            invoice.status = INVOICE_VOID
            invoice.void_reason = VOID_SUPERSEDED
            invoice.voided_at = now
            invoice.updated_at = now
        subscription.status = "canceled"
        subscription.canceled_at = now
        subscription.updated_at = now
    return len(parked)


async def settle_invoice_for_payment(
    session: AsyncSession,
    payment: models.Payment,
    *,
    paid_at: datetime,
) -> models.PlanInvoice | None:
    """Find the invoice this payment settles, and settle it.

    The payment-shaped entry point, because `services.payments.mark_paid` has a `Payment` and
    nothing else. The rules live in `settle_invoice`, which the admin console also calls — so a
    bank transfer settled by hand leaves the account in exactly the state a QR payment would.

    Returns None when this payment is not an invoice payment, which is the common case: every
    merchant payment on the platform comes through here.
    """
    res = await session.execute(
        select(models.PlanInvoice).where(
            models.PlanInvoice.chmabapay_payment_id == payment.id
        )
    )
    invoice = res.scalar_one_or_none()
    if invoice is None:
        return None
    return await settle_invoice(
        session, invoice, payment=payment, paid_at=paid_at, source="khqr"
    )


def lift_billing_hold(account: models.Account | None) -> bool:
    """Clear a billing freeze — and nothing else.

    The freeze is that one status field (§7.2 touches no store row, no plan and no invoice),
    which is what makes lifting it a single write. Every path out of the freeze goes through
    here rather than each caller restoring fields on its own, because "unfreeze restores exactly
    what billing took" is only true while there is one place that takes it.

    `suspended` is left alone. An abuse or review lockout is an operator's decision, and a plan
    payment is not a reason to undo it — so a suspended account stays suspended even as the
    invoice it owed is settled.

    Returns whether anything changed. When a plan is chosen instead of paid,
    `services.stores.apply_store_cap` restores the stores; here the freeze never held any.
    """
    if account is None or account.status != models.ACCOUNT_RESTRICTED:
        return False
    account.status = models.ACCOUNT_ACTIVE
    account.updated_at = utcnow()
    return True


async def settle_invoice(
    session: AsyncSession,
    invoice: models.PlanInvoice,
    *,
    paid_at: datetime,
    source: str,
    payment: models.Payment | None = None,
) -> models.PlanInvoice | None:
    """Settle one invoice, and put the coverage it bought in force.

    Called before its caller's commit, so the payment, the invoices and the plan change become
    durable together. An invoice reading `paid` while the plan it bought never activated would
    be worse than either failure alone: the merchant has been charged and received nothing.

    Which of two things happens turns on one question — **did they pay before the freeze, or
    after it** — measured on `due_at + grace`, the same line the enforcement job uses, so both
    sides agree on what "lapsed" means:

    * **Before it**, the window the invoice describes is still theirs. The invoice is paid and
      `next_billing_at = period_end`: the anniversary is preserved and the grace days are given
      away free, which is the cheapest possible resolution of a late payment.
    * **After it**, coverage had lapsed — the account was frozen and could not take a single
      payment — so this invoice is *not* marked paid. Marking it paid would book revenue into a
      month that was never served and leave "paid" meaning nothing. The lapsed window is voided
      and a fresh invoice is issued for the window actually granted, from `paid_at`, with the
      payment re-pointed at it.

    Either way the account is unfrozen — unless an operator has suspended it, because an abuse
    lockout is not business a payment can undo.

    Deliberately not keyed on `subscription.status`. The freeze is account-level and leaves the
    subscription `active` on purpose — that is what makes unfreezing one status write — so a
    status test would send every frozen merchant down the first branch and cost them the month
    they were frozen.

    The caller commits. Returns None when there was nothing to do — the invoice was already
    settled — which is what makes a retried payment a no-op rather than a second grant.
    """
    if invoice.status == INVOICE_PAID:
        return None

    now = utcnow()
    account = await session.get(models.Account, invoice.account_id)
    subscription = (
        await session.get(models.PlanSubscription, invoice.subscription_id)
        if invoice.subscription_id is not None
        else None
    )

    # Nothing to put in force. Pre-lifecycle rows have no subscription, and the sweep retires
    # them; settling the claim is all that is left to do.
    if subscription is None:
        invoice.status = INVOICE_PAID
        invoice.paid_at = paid_at
        invoice.updated_at = now
        return invoice

    due_at = as_utc(invoice.due_at)
    lapsed = due_at is not None and paid_at > due_at + grace_period()

    if lapsed:
        await _reinstate(
            session, invoice, subscription, payment=payment, paid_at=paid_at, source=source
        )
    else:
        invoice.status = INVOICE_PAID
        invoice.paid_at = paid_at
        invoice.updated_at = now
        await activate_subscription(session, subscription.id)
        await extend_coverage(session, invoice)

    if account is not None:
        # `suspended` outranks `restricted`: an abuse or review lockout is an operator
        # decision, and a plan payment is not a reason to lift it.
        lift_billing_hold(account)

        # Whichever way they paid, the merchant comes back — so any store this platform was
        # holding on their behalf is released. Deferred import: `services.stores` reaches
        # `services.payments`, which imports this module, so a top-level import would be a cycle.
        from . import stores as stores_svc

        plan = await session.get(models.Plan, subscription.plan_id)
        if plan is not None:
            await stores_svc.apply_store_cap(
                session, account, max_stores=plan.max_stores
            )
    return invoice


async def _reinstate(
    session: AsyncSession,
    invoice: models.PlanInvoice,
    subscription: models.PlanSubscription,
    *,
    payment: models.Payment | None,
    paid_at: datetime,
    source: str,
) -> models.PlanInvoice:
    """Grant a fresh window from the payment instant, after coverage had lapsed.

    A *new* invoice rather than flipping the old one, because the old one describes a window
    the merchant never received and against which nothing was collected. **The invariant that
    buys: every `paid` invoice's window is one the merchant was actually entitled to**, which is
    what keeps a revenue report readable a year later. The lapsed row is voided rather than
    deleted — it is the evidence of the lapse, and the reason `grace_expired` exists as a
    distinct `void_reason`.

    The payment reference moves with it — the void invoice gives it up — because
    `settle_invoice_for_payment` looks the invoice up by it, and two rows pointing at one payment
    would be ambiguous on the next retry.

    The subscription is replaced rather than restamped: the old row records "ran until the lapse"
    and the new one records "started when they paid", which is the truth, and `started_at` on the
    old row stops meaning something it did not. It also has to be cancelled regardless — a frozen
    subscription is still `active`, so leaving it would put two rows in `trial`/`active` and
    `_get_active_sub` reads exactly one. The new row is created first so the granted invoice can
    name it: a `paid` invoice belonging to a *canceled* subscription would be a lie in every join
    a revenue report might make.
    """
    now = utcnow()
    period_end = paid_at + CREDIT_PERIOD

    await retire_live_subscriptions(session, invoice.account_id, now=now)

    reinstated = models.PlanSubscription(
        account_id=invoice.account_id,
        plan_id=subscription.plan_id,
        status="active",
        started_at=paid_at,
        next_billing_at=period_end,
    )
    session.add(reinstated)
    await session.flush()

    granted = models.PlanInvoice(
        account_id=invoice.account_id,
        subscription_id=reinstated.id,
        period_month=period_month_for(paid_at),
        period_start=paid_at,
        period_end=period_end,
        due_at=paid_at,
        status=INVOICE_PAID,
        paid_at=paid_at,
        base_fee_cents=invoice.base_fee_cents,
        total_due_cents=invoice.total_due_cents,
        chmabapay_payment_id=payment.id if payment is not None else None,
    )
    session.add(granted)
    await session.flush()

    # The claim this replaced is retired, not paid. `open` would leave the merchant owing a
    # period the platform never served — and the guard in `change_plan` (`409
    # open_invoice_unpaid`) would keep refusing them a purchase forever.
    invoice.status = INVOICE_VOID
    invoice.void_reason = VOID_GRACE_EXPIRED
    invoice.voided_at = now
    if payment is not None:
        invoice.chmabapay_payment_id = None
    invoice.updated_at = now

    gap_days = (paid_at - (as_utc(invoice.due_at) or paid_at)).days
    audit.record(
        session,
        actor=None,
        action="billing.reinstated",
        target_type="Account",
        target_id=invoice.account_id,
        details={
            "lapsed_invoice_id": invoice.id,
            "granted_invoice_id": granted.id,
            "gap_days": gap_days,
            "period_end": period_end.isoformat(),
            "source": source,
        },
    )
    return granted


async def retire_live_subscriptions(
    session: AsyncSession, account_id: int, *, now: datetime | None = None
) -> list[models.PlanSubscription]:
    """Cancel every subscription in force, so the next one can be the only live row.

    Called wherever a subscription is *replaced* rather than restamped: a reinstatement after a
    lapse (§5.3), and a merchant whose coverage has lapsed choosing a different plan (§7.5). Two
    things depend on it. `_get_active_sub` reads a single row and raises on two, so the old row
    has to go before the new one arrives. And a canceled row drops out of the issue sweep's
    `status IN ('trial','active')` filter, which is what stops W3 from re-raising the window
    whose invoice was just voided — the void frees the `(subscription_id, period_start)` key, so
    without this a frozen merchant choosing Starter would find the Pro invoice raised again an
    hour later and be back where they started.

    The caller commits.
    """
    moment = now or utcnow()
    rows = (
        await session.execute(
            select(models.PlanSubscription).where(
                models.PlanSubscription.account_id == account_id,
                models.PlanSubscription.status.in_(["trial", "active"]),
            )
        )
    ).scalars().all()
    for row in rows:
        row.status = "canceled"
        row.canceled_at = moment
        row.updated_at = moment
    return list(rows)


async def extend_coverage(session: AsyncSession, invoice: models.PlanInvoice) -> None:
    """Coverage ends where the invoice says it does. One rule, both paths.

    A first purchase has `period_end = now + CREDIT_PERIOD`; a renewal has
    `period_end = old_end + CREDIT_PERIOD`. Both fall out of the same assignment, so there
    is no proration branch to get wrong. Paying inside grace therefore keeps the renewal
    anniversary and gives the grace days away for free, which is the cheapest possible
    resolution of a late payment.

    This is the only writer of `next_billing_at` on a live subscription. Issuing an invoice
    must never touch it, or a merchant who never pays would silently keep renewing — which
    is the leak this whole module exists to close.
    """
    if invoice.period_end is None or invoice.subscription_id is None:
        return
    subscription = await session.get(models.PlanSubscription, invoice.subscription_id)
    if subscription is None:
        return
    subscription.next_billing_at = invoice.period_end
    subscription.updated_at = utcnow()


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
