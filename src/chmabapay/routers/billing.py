"""Billing and subscription management router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..db import get_session
from ..openapi import (
    AUTH_ERRORS,
    CONFLICT_ERROR,
    SESSION_SECURITY,
    merged,
)
from ..services import billing as billing_svc
from ..services import notifications
from ..services import payments as svc
from ..services import stores as store_svc
from .auth import get_current_session_account

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


def _money_str(cents: int) -> str:
    return f"${cents / 100:.2f}"


class PlanOut(BaseModel):
    name: str
    code: str
    monthly_fee_cents: int
    monthly_fee_formatted: str
    base_payments_included: int
    max_stores: int | None
    max_keys_per_account: int
    max_webhooks_per_account: int
    priority_support: bool
    is_public: bool
    # Pricing-page copy, owned by the admin console.
    tagline: str | None
    features: list[str] | None
    is_featured: bool

    @classmethod
    def from_model(cls, plan: models.Plan) -> PlanOut:
        return cls(
            name=plan.name,
            code=plan.code,
            monthly_fee_cents=plan.monthly_fee_cents,
            monthly_fee_formatted=_money_str(plan.monthly_fee_cents),
            base_payments_included=plan.base_payments_included,
            max_stores=plan.max_stores,
            max_keys_per_account=plan.max_keys_per_account,
            max_webhooks_per_account=plan.max_webhooks_per_account,
            priority_support=plan.priority_support,
            is_public=plan.is_public,
            tagline=plan.tagline,
            features=list(plan.features) if plan.features else None,
            is_featured=plan.is_featured,
        )


class ChangePlanIn(BaseModel):
    plan_code: str = Field(min_length=2, max_length=32)


class ChangePlanOut(BaseModel):
    subscription: dict[str, Any]
    # A move to a paid tier no longer takes effect on the click. It raises an
    # invoice, and the plan changes when that invoice is settled — so the client has
    # to be able to tell the two outcomes apart, or it will tell a merchant they are
    # on Pro while they are still on Free.
    payment_required: bool = False
    invoice: dict[str, Any] | None = None


def _subscription_out(sub: models.PlanSubscription, plan: models.Plan) -> dict[str, Any]:
    return {
        "id": sub.id,
        "plan_id": sub.plan_id,
        "plan_code": plan.code,
        "plan_name": plan.name,
        "status": sub.status,
        "started_at": sub.started_at,
        "canceled_at": sub.canceled_at,
        "next_billing_at": sub.next_billing_at,
        "trial_ends_at": sub.trial_ends_at,
        "created_at": sub.created_at,
        "updated_at": sub.updated_at,
    }


async def _get_active_sub(
    session: AsyncSession, account_id: int
) -> tuple[models.PlanSubscription | None, models.Plan | None]:
    res = await session.execute(
        select(models.PlanSubscription, models.Plan)
        .join(models.Plan, models.Plan.id == models.PlanSubscription.plan_id)
        .where(
            models.PlanSubscription.account_id == account_id,
            models.PlanSubscription.status.in_(["trial", "active"]),
        )
    )
    row = res.one_or_none()
    if row is None:
        return None, None
    return row


class SubscriptionOut(BaseModel):
    subscription: dict[str, Any] | None
    plan: PlanOut | None
    # When the coverage the merchant paid for ends — the date the dashboard's plan card shows.
    # Top-level rather than a key inside `subscription`, so it reads the same whether or not a
    # subscription row accompanies it: a frozen merchant's row may be gone while their debt is not.
    current_period_end: datetime | None = None
    # Set while a plan invoice is unpaid: what the billing page's Pay action should open, and the
    # reason a notice exists at all.
    outstanding_invoice_id: int | None = None
    # Set while the current plan's allowance is not yet being enforced, because a paid plan was
    # given up during this calendar month: the instant enforcement starts (§7.7). A date rather
    # than a flag, because the sentence that explains it names the date and the month boundary is
    # policy the server owns. The usage bar reads its allowance from the plan in force, so a
    # mid-month downgrade reads over-limit while nothing is blocked — this is what lets the page
    # say why instead of contradicting itself.
    quota_deferred_until: datetime | None = None


@router.get("/notices", dependencies=SESSION_SECURITY, responses=AUTH_ERRORS)
async def list_notices(
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    """The one notice the portal's banner should show, or none at all.

    At most one, and the copy is computed server-side with it (see `billing.derive_notices`), so
    the sentence a merchant reads here is the same one the reminder worker recorded having
    delivered. Session-only on purpose: this is what the shell renders, not an API a merchant's
    own systems consume.
    """
    return {"notices": await billing_svc.derive_notices(session, account)}


async def _void_open_invoices(
    session: AsyncSession, account_id: int, *, reason: str, now: datetime
) -> list[models.PlanInvoice]:
    """Retire every unpaid invoice on the account, and record why.

    Used where a merchant resolves a balance by choosing a different plan rather than paying
    it (§7.5). Voiding is not tidiness — it *is* the resolution: the freeze is keyed on an open
    overdue invoice, so a merchant who moved to Starter while the Pro invoice stayed open would
    be frozen *on Starter*, permanently, blocked by a plan they have already declined to buy.

    Every unpaid one, not just the oldest, because `find_open_invoice` refuses a new purchase
    while anything is outstanding — leaving one behind would recreate the same trap on the next
    click. The rows are kept, marked `void` with a reason: "the merchant walked away" and "the
    platform never should have billed this" are different stories to tell a year later.
    """
    rows = list(
        (
            await session.execute(
                select(models.PlanInvoice).where(
                    models.PlanInvoice.account_id == account_id,
                    models.PlanInvoice.status.in_(billing_svc.UNPAID_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    for invoice in rows:
        invoice.status = billing_svc.INVOICE_VOID
        invoice.void_reason = reason
        invoice.voided_at = now
        invoice.updated_at = now
    return rows


@router.get(
    "/subscription",
    response_model=SubscriptionOut,
    dependencies=SESSION_SECURITY,
    responses=AUTH_ERRORS,
)
async def get_subscription(
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    sub, plan = await _get_active_sub(session, account.id)
    # Read here rather than inside `_subscription_out`, which has no session: the outstanding
    # invoice belongs to the account, not to the subscription, and it is the one thing the plan
    # card needs when there is no subscription left to describe.
    outstanding = await billing_svc.find_open_invoice(session, account.id)
    outstanding_id = outstanding.id if outstanding is not None else None
    if sub is None or plan is None:
        return SubscriptionOut(
            subscription=None, plan=None, outstanding_invoice_id=outstanding_id
        )
    return SubscriptionOut(
        subscription=_subscription_out(sub, plan),
        plan=PlanOut.from_model(plan),
        current_period_end=sub.next_billing_at,
        outstanding_invoice_id=outstanding_id,
        # Only asked when there is a plan in force: with no subscription there is no allowance
        # for a deferral to postpone.
        quota_deferred_until=await svc.quota_deferred_until(session, account.id),
    )


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    session: AsyncSession = Depends(get_session),
):
    res = await session.execute(
        select(models.Plan)
        .where(
            models.Plan.is_public,
            models.Plan.is_active,
        )
        # cheapest first, so the pricing grid reads left to right
        .order_by(models.Plan.monthly_fee_cents, models.Plan.id)
    )
    plans = list(res.scalars().all())
    return [PlanOut.from_model(p) for p in plans]


@router.post(
    "/change-plan",
    response_model=ChangePlanOut,
    dependencies=SESSION_SECURITY,
    responses=merged(AUTH_ERRORS, CONFLICT_ERROR),
)
async def change_plan(
    body: ChangePlanIn,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    """Move an account to a plan, collecting for it first.

    This used to cancel the old subscription and insert the new one as `active` in
    one step, collecting nothing and prorating nothing. Since Pro costs $59.99 a
    month and the dashboard exposes this as a single button, the effect was a
    self-serve upgrade to the top tier for free — the revenue leak the audit called
    out. A paid tier is now *bought*: the new subscription is parked as `pending`,
    an invoice is raised for the period, and `services.payments.mark_paid` activates
    it when the money arrives. `_get_active_sub` only reads `trial`/`active`, so a
    pending subscription grants nothing in the meantime.

    A move to a free tier still applies immediately — there is nothing to collect,
    and making a downgrade wait on a payment would trap a merchant on a plan they
    are trying to leave. It does enforce the allowance that choice implies
    (`apply_store_cap`), because otherwise "downgrade to Free" would be a way to keep
    every store running for nothing.

    An unpaid invoice blocks a new purchase rather than accruing beside it: two open
    periods through one QR flow is a conversation, not a checkout.
    """
    res = await session.execute(
        select(models.Plan).where(models.Plan.code == body.plan_code)
    )
    new_plan = res.scalar_one_or_none()
    if new_plan is None:
        raise HTTPException(status_code=404, detail="plan_not_found")
    if not new_plan.is_public or not new_plan.is_active:
        raise HTTPException(status_code=400, detail="plan_not_available")

    old_sub, old_plan = await _get_active_sub(session, account.id)
    old_code = old_plan.code if old_plan else None
    now = datetime.now(UTC)

    if old_plan is not None and old_plan.id == new_plan.id:
        # Re-selecting the plan you are on is not a purchase. Without this, a
        # merchant on Pro could click Pro and invoice themselves a second time.
        raise HTTPException(status_code=400, detail="plan_unchanged")

    if new_plan.monthly_fee_cents <= 0:
        # Moving to Free writes off whatever is outstanding, because there is nothing left to
        # collect against it: the plan it was billing for is the one being left. Without this a
        # merchant mid-renewal-window keeps an open claim on a plan they have just left, which
        # blocks their next purchase (`find_open_invoice`) and, for a frozen account, keeps them
        # frozen on Free — the exact trap §7.5 exists to prevent.
        await _void_open_invoices(
            session, account.id, reason=billing_svc.VOID_DOWNGRADED, now=now
        )
        # And a freeze follows the debt it is keyed on: the debt is gone, so the hold is too.
        # A no-op for an ordinary account, which is not restricted in the first place.
        billing_svc.lift_billing_hold(account)

        if old_sub is not None:
            old_sub.status = "canceled"
            old_sub.canceled_at = now
            old_sub.updated_at = now
        new_sub = models.PlanSubscription(
            account_id=account.id,
            plan_id=new_plan.id,
            status="active",
            started_at=now,
            next_billing_at=now + billing_svc.CREDIT_PERIOD,
        )
        session.add(new_sub)
        await session.flush()

        # A downgrade enforces the allowance it is choosing. Without this, "downgrade to
        # Free" is a menu item that keeps every store running for free — the same leak as
        # not paying, reached without a lapse.
        await store_svc.apply_store_cap(
            session, account, max_stores=new_plan.max_stores
        )

        audit.record(
            session,
            actor=account,
            action="plan.changed",
            target_type="Account",
            target_id=account.id,
            details={"from_plan": old_code, "to_plan": new_plan.code},
        )
        await session.commit()
        await session.refresh(new_sub)
        return ChangePlanOut(subscription=_subscription_out(new_sub, new_plan))

    # An unpaid invoice blocks a second purchase. Two periods settled through one QR flow is
    # a conversation rather than a checkout. This replaces the old calendar-month guard,
    # which drifted against the 30-day credit and happily let a merchant accrue two unpaid
    # periods in two different months.
    #
    # A *frozen* account is the exception, and it is the whole point of §7.5's chooser: the
    # invoice standing in the way is the one the freeze is keyed on, so this click is the
    # merchant resolving it by choosing a plan they can afford. Refusing would leave them frozen
    # on a plan they have already declined to buy, with no path forward but support.
    outstanding = await billing_svc.find_open_invoice(session, account.id)
    if outstanding is not None:
        if account.status != models.ACCOUNT_RESTRICTED:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"open_invoice_unpaid: invoice {outstanding.id} for "
                    f"{outstanding.period_month} is still unpaid"
                ),
            )
        await _void_open_invoices(
            session, account.id, reason=billing_svc.VOID_DOWNGRADED, now=now
        )
        # The coverage whose window was just written off is retired with it. Leaving it `active`
        # would let W3 re-raise that window an hour later — the void frees the period key — and
        # the merchant would be back at this same click with the same balance. Their plan is not
        # lost: the purchase below is parked as `pending` and in force the moment it is paid,
        # which is the rule everywhere else a paid tier is bought.
        await billing_svc.retire_live_subscriptions(session, account.id, now=now)

    pending = models.PlanSubscription(
        account_id=account.id,
        plan_id=new_plan.id,
        status=billing_svc.SUBSCRIPTION_PENDING,
        started_at=None,
        next_billing_at=now + billing_svc.CREDIT_PERIOD,
    )
    session.add(pending)
    await session.flush()

    # `issue_invoice` returns `(invoice, created)`. Unpacking it matters: a tuple is
    # never `None`, so treating the result as a single invoice skipped the guard below
    # and then raised on `invoice.id`, turning every paid-plan change into a 500.
    #
    # The window starts now: this invoice buys the period the merchant is about to use,
    # which is why a first purchase is prepaid while a renewal is billed in its lead
    # window. `due_at = now` because nothing is in force yet — there is no service to
    # lapse, so this one is collectible immediately and carries no dunning.
    invoice, _created = await billing_svc.issue_invoice(
        session,
        pending,
        new_plan,
        period_start=now,
        period_end=now + billing_svc.CREDIT_PERIOD,
        due_at=now,
    )
    if invoice is None:
        # Unreachable while the fee check above is `> 0`. It is here so that a zero
        # fee and a zero price can never quietly add up to a free upgrade.
        raise HTTPException(status_code=500, detail="invoice_not_raised")

    audit.record(
        session,
        actor=account,
        action="plan.change_requested",
        target_type="Account",
        target_id=account.id,
        details={
            "from_plan": old_code,
            "to_plan": new_plan.code,
            "invoice_id": invoice.id,
            "period_month": invoice.period_month,
        },
    )
    await session.commit()
    await session.refresh(pending)
    await session.refresh(invoice)

    # A self-serve upgrade is platform revenue the moment the invoice exists, so the
    # group hears about it now rather than only if it is ever settled.
    await notifications.notify_activity(
        notifications.format_invoice_issued(
            invoice, plan_name=new_plan.name, account_label=account.email
        )
    )

    return ChangePlanOut(
        subscription=_subscription_out(pending, new_plan),
        payment_required=True,
        invoice=_serialize_invoice(invoice),
    )


async def _get_hq_store(session: AsyncSession) -> models.Store:
    """The platform's own store — where a plan fee is actually collected.

    Resolution lives in `services.billing.resolve_hq_store`, because the admin console
    needs the same answer to show the operator where the money goes, and two copies of
    this order would eventually disagree about which store is in use.

    When nothing resolves, the refusal is a **503 with something a merchant can act
    on**. It used to be a 500 whose body was the raw string
    `platform_hq_store_not_configured: ask admin to set ...`, which the dashboard
    passes straight into a toast — so the person who cannot fix it was shown an
    instruction addressed to somebody else, in an error code they would never
    recognise as "billing is not switched on".
    """
    resolution = await billing_svc.resolve_hq_store(session)
    if resolution.store is not None:
        return resolution.store

    raise HTTPException(
        status_code=503,
        detail=(
            "billing_not_open: plan payments are not switched on yet, so this "
            "invoice cannot be paid. Contact support and we will settle it with you."
        ),
    )


def _serialize_invoice(invoice: models.PlanInvoice) -> dict[str, Any]:
    """One invoice, including where its window sits and whether it is late.

    `issued_at` used to be read here through a `getattr` default, because the column was
    intended and never added. The real dates replace it: `due_at` is what every reminder and
    every grace clock is measured from, and `is_overdue` is derived from it rather than
    stored, because a stored flag needs a writer to keep it true.

    `as_utc` is the service's, not a local copy: the same naive-from-SQLite guard decides
    `is_overdue` here and `lapsed` in `settle_invoice`, and two copies could disagree about
    what "overdue" means on the two sides of one payment.
    """
    now = datetime.now(UTC)
    due_at = billing_svc.as_utc(invoice.due_at)
    return {
        "id": invoice.id,
        "period_month": invoice.period_month,
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
        "due_at": invoice.due_at,
        "days_until_due": None if due_at is None else (due_at - now).days,
        "is_overdue": (
            due_at is not None
            and due_at < now
            and invoice.status in billing_svc.UNPAID_STATUSES
        ),
        "status": invoice.status,
        "base_fee_cents": invoice.base_fee_cents,
        "base_fee_formatted": _money_str(invoice.base_fee_cents),
        "usage_payments_count": invoice.usage_payments_count,
        "overage_payments_count": invoice.overage_payments_count,
        "overage_fee_cents": invoice.overage_fee_cents,
        "overage_fee_formatted": _money_str(invoice.overage_fee_cents),
        "total_due_cents": invoice.total_due_cents,
        "total_due_formatted": _money_str(invoice.total_due_cents),
        "paid_at": invoice.paid_at,
        "voided_at": invoice.voided_at,
        "void_reason": invoice.void_reason,
        "chmabapay_payment_id_ref": (
            str(invoice.chmabapay_payment_id) if invoice.chmabapay_payment_id is not None else None
        ),
    }


@router.get("/invoices", dependencies=SESSION_SECURITY, responses=AUTH_ERRORS)
async def list_invoices(
    period_month: str | None = None,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(models.PlanInvoice)
        .where(models.PlanInvoice.account_id == account.id)
        .order_by(models.PlanInvoice.period_month.desc())
    )
    if period_month:
        stmt = stmt.where(models.PlanInvoice.period_month == period_month)
    res = await session.execute(stmt)
    invoices = list(res.scalars().all())
    return {
        "data": [_serialize_invoice(inv) for inv in invoices],
        "meta": {"filter_period_month": period_month or "all"},
    }


async def _invoice_payment(
    session: AsyncSession,
    invoice: models.PlanInvoice,
    store: models.Store,
    *,
    reference_id: str,
    metadata: dict[str, Any],
) -> models.Payment:
    """The payable code for this invoice: reuse it while live, replace it when dead.

    `create_payment` is idempotent on the invoice, which is what stops a merchant being
    handed a second payable code for a month they may already have paid — paying both
    would charge them twice against one invoice. The cost of that safety is that a
    *dead* code gets handed back too, and ABA's window is 180s with nothing to extend
    it, so a merchant who closed the tab returned to a QR their wallet refuses. The
    only way back to a payable code is a new ABA session, and it has to be minted here:
    this payment sits on the platform's own HQ store, so the merchant cannot reach the
    merchant-facing reissue route for it.
    """
    if invoice.chmabapay_payment_id is not None:
        stored = await session.get(models.Payment, invoice.chmabapay_payment_id)
        if stored is not None:
            if stored.status in (models.PAYMENT_EXPIRED, models.PAYMENT_FAILED):
                successor, _created = await svc.reissue_payment(session, stored)
                return successor
            return stored

    payment, _created = await svc.create_payment(
        session,
        store=store,
        amount_cents=invoice.total_due_cents,
        reference_id=reference_id,
        metadata=metadata,
        idempotency_key=reference_id,
    )
    return payment


@router.get(
    "/invoices/{invoice_id:int}/khqr",
    status_code=201,
    dependencies=SESSION_SECURITY,
    responses=AUTH_ERRORS,
)
async def get_invoice_khqr(
    invoice_id: int,
    request: Request,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    res = await session.execute(
        select(models.PlanInvoice).where(
            models.PlanInvoice.id == invoice_id,
            models.PlanInvoice.account_id == account.id,
        )
    )
    invoice = res.scalar_one_or_none()
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice_not_found")

    if invoice.status == billing_svc.INVOICE_PAID:
        raise HTTPException(status_code=400, detail="invoice_already_paid")

    # A `void` invoice is still payable, on purpose: a merchant whose account was frozen,
    # or who walked away and came back, buys their plan back by settling the code they
    # already have. Refusing it would remove the only route back.

    hq_store = await _get_hq_store(session)
    reference_id = f"INV-{invoice.id}-{invoice.period_month}"
    metadata = {"source": "billing_invoice", "invoice_id": invoice.id}

    payment = await _invoice_payment(
        session,
        invoice,
        hq_store,
        reference_id=reference_id,
        metadata=metadata,
    )

    invoice.chmabapay_payment_id = payment.id
    # Minting a code is what turns a raised invoice into a collectible one. `draft` and
    # `issued` were the old spellings of that state; they are still *read* as unpaid, but
    # nothing writes them again.
    if invoice.status in billing_svc.LEGACY_UNPAID:
        invoice.status = billing_svc.INVOICE_OPEN
    await session.commit()
    await session.refresh(invoice)

    if request.url.hostname:
        port_part = ""
        if request.url.port not in (80, 443, None):
            port_part = f":{request.url.port}"
        checkout_url = f"{request.url.scheme}://{request.url.hostname}{port_part}/pay/{payment.public_id}"
    else:
        checkout_url = f"/pay/{payment.public_id}"

    return {
        "payment_id": payment.public_id,
        "qr_string": payment.qr_string,
        "checkout_url": checkout_url,
        "expires_at": payment.expires_at,
        "amount_cents": invoice.total_due_cents,
        "amount_formatted": _money_str(invoice.total_due_cents),
    }
