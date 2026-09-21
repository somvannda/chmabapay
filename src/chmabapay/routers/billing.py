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
from ..services import payments as svc
from .auth import get_current_session_account

router = APIRouter(prefix="/v1/billing", tags=["billing"])


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
    if sub is None or plan is None:
        return SubscriptionOut(subscription=None, plan=None)
    return SubscriptionOut(
        subscription=_subscription_out(sub, plan),
        plan=PlanOut.from_model(plan),
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
    are trying to leave.
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

    # One invoice per account per period is a database constraint, so a second plan
    # change in the same month cannot be billed. Refusing it here turns what would
    # otherwise be a constraint violation (a 500) into an answer the client can
    # explain, and stops two paid subscriptions being parked against one invoice.
    period = billing_svc.period_month_for(now)
    existing = await billing_svc.find_period_invoice(session, account.id, period)
    if existing is not None:
        raise HTTPException(status_code=409, detail="period_already_invoiced")

    pending = models.PlanSubscription(
        account_id=account.id,
        plan_id=new_plan.id,
        status=billing_svc.SUBSCRIPTION_PENDING,
        started_at=None,
        next_billing_at=now + billing_svc.CREDIT_PERIOD,
    )
    session.add(pending)
    await session.flush()

    invoice = await billing_svc.issue_invoice(
        session, pending, new_plan, period_month=period, now=now
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
            "period_month": period,
        },
    )
    await session.commit()
    await session.refresh(pending)
    await session.refresh(invoice)

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
    return {
        "id": invoice.id,
        "period_month": invoice.period_month,
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
        "issued_at": getattr(invoice, "issued_at", None),
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

    if invoice.status == "paid":
        raise HTTPException(status_code=400, detail="invoice_already_paid")

    hq_store = await _get_hq_store(session)
    reference_id = f"INV-{invoice.id}-{invoice.period_month}"
    metadata = {"source": "billing_invoice", "invoice_id": invoice.id}

    payment, created = await svc.create_payment(
        session,
        store=hq_store,
        amount_cents=invoice.total_due_cents,
        reference_id=reference_id,
        metadata=metadata,
        # Keyed on the invoice, so asking twice for the same invoice's QR returns
        # the same payment instead of minting a second one. Without it, a merchant
        # who reopened the billing page got a fresh payable code every time and
        # could pay the same invoice twice — the platform would have taken double
        # for one month, with two payments and one invoice to reconcile. If the
        # code has since died, `POST /v1/payments/{id}/reissue` is the intended
        # path, not a second payment.
        idempotency_key=reference_id,
    )

    invoice.chmabapay_payment_id = payment.id
    if invoice.status == "draft":
        invoice.status = "issued"
    if hasattr(invoice, "issued_at") and getattr(invoice, "issued_at", None) is None:
        invoice.issued_at = datetime.now(UTC)
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
