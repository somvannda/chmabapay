"""SQLAlchemy ORM models for the tenant payment platform.

Hierarchy: account (platform or direct merchant) -> stores (merchants) -> payment_links
(money destination owned by the store). Keys + webhooks default to account level.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Status constants (plain strings on purpose for the POC)
# --------------------------------------------------------------------------- #
ACCOUNT_ACTIVE = "active"
ACCOUNT_SUSPENDED = "suspended"

STORE_DRAFT = "draft"
STORE_LINK_PENDING = "link_pending"
STORE_ACTIVE = "active"
STORE_DISABLED = "disabled"

KEY_ACCOUNT_SCOPE = "account"

PAYMENT_PENDING = "pending"
PAYMENT_SCANNED = "scanned"
PAYMENT_PAID = "paid"
PAYMENT_EXPIRED = "expired"
PAYMENT_FAILED = "failed"
# A reissue produced a replacement code and this one was never paid. Distinct from
# EXPIRED: expired means the window closed, superseded means a newer code took its
# place, so two payable codes can never exist for one sale at the same time.
PAYMENT_SUPERSEDED = "superseded"
# A settled payment that the merchant later refunded or reversed. The money moved
# and came back, so it is not PAID and it is not FAILED — collapsing it into either
# would misstate revenue in one direction or the other.
PAYMENT_REVERSED = "reversed"

# Statuses that mean "this code will never take money again", for the withdrawal
# rule in routers/checkout.py. SUPERSEDED and REVERSED belong here: a customer
# holding a retired code must not be shown a payable page for it.
PAYMENT_DEAD_STATUSES = (
    PAYMENT_EXPIRED,
    PAYMENT_FAILED,
    PAYMENT_SUPERSEDED,
    PAYMENT_REVERSED,
)

LINK_UNVERIFIED = "unverified"
LINK_VERIFIED = "verified"

# The only supported store destination: an ABA PayWay share link.
LINK_ABA_PAYWAY = "aba_payway"

WEBHOOK_ENDPOINT_ACTIVE = "active"

EVENT_COMPLETED = "payment.completed"
EVENT_SCANNED = "payment.scanned"
EVENT_EXPIRED = "payment.expired"
EVENT_FAILED = "payment.failed"
EVENT_SUPERSEDED = "payment.superseded"
EVENT_REVERSED = "payment.reversed"

DELIVERY_PENDING = "pending"
DELIVERY_RETRYING = "retrying"
DELIVERY_SUCCESS = "success"
DELIVERY_FAILED = "failed"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=ACCOUNT_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    account_type: Mapped[str] = mapped_column(String(16), default="individual")
    whitelabel_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    account_type_explicitly_set: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Optional password sign-in. Null means the account can only sign in with Google
    # (or the dev-login shortcut). The platform admin console uses this.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # Acceptance of the merchant agreement. The version is stored alongside the
    # timestamp because the one question worth answering later is "to which text
    # did they agree" — a timestamp alone cannot distinguish an account that
    # accepted the current terms from one that accepted a superseded draft.
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_accepted_version: Mapped[str | None] = mapped_column(String(32))


class Store(Base):
    __tablename__ = "stores"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_store_account_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    created_via: Mapped[str] = mapped_column(String(16), default="api")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    city: Mapped[str] = mapped_column(String(15), default="Phnom Penh")
    support_email: Mapped[str | None] = mapped_column(String(255))
    redirect_success_url: Mapped[str | None] = mapped_column(Text)
    redirect_failure_url: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    brand_color: Mapped[str | None] = mapped_column(String(16))
    logo_image_url: Mapped[str | None] = mapped_column(Text)
    whitelabel_css: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=STORE_DRAFT, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PaymentLink(Base):
    __tablename__ = "payment_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id"), nullable=False, unique=True, index=True
    )
    link_type: Mapped[str] = mapped_column(String(24), default=LINK_ABA_PAYWAY)
    raw_link: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_account_id: Mapped[str] = mapped_column(String(120), nullable=False)
    merchant_name: Mapped[str | None] = mapped_column(String(120))
    payway_client_id: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    verification: Mapped[str] = mapped_column(String(16), default=LINK_VERIFIED)
    is_sandbox: Mapped[bool] = mapped_column(default=False)
    min_amount_cents: Mapped[int] = mapped_column(Integer, default=1)
    max_amount_cents: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    can_manage_stores: Mapped[bool] = mapped_column(default=False)
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default="live")
    status: Mapped[str] = mapped_column(String(16), default=ACCOUNT_ACTIVE)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("store_id", "idempotency_key", name="uq_store_idem"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), nullable=False, index=True)
    payment_link_id: Mapped[int] = mapped_column(
        ForeignKey("payment_links.id"), nullable=False, index=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    reference_id: Mapped[str | None] = mapped_column(String(255), index=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=PAYMENT_PENDING, index=True)
    qr_string: Mapped[str] = mapped_column(Text, nullable=False)
    qr_md5: Mapped[str | None] = mapped_column(String(32), index=True)
    bill_number: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bakong_ref: Mapped[str | None] = mapped_column(String(255))
    # The moment we stopped trying to reconcile this payment. Written once, when it
    # crosses `detection_window_seconds` without settling. Recorded rather than
    # inferred because "we never looked" and "we looked and found nothing" are
    # different claims, and only one is defensible to a merchant whose customer
    # says the money left their account.
    detection_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a merchant records a refund or reversal. ABA offers no callback for
    # this, so it is the only source of that fact — and without it a refunded
    # payment reads PAID forever, overstating revenue permanently.
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[str | None] = mapped_column(String(255))
    # `none_as_null=True` because the default JSON behaviour is to store Python
    # None as the JSON *literal* `null` — verified, not assumed. A purge that
    # "cleared" this field would then leave a value that still satisfies
    # `IS NOT NULL`, so a retention sweep driven by that predicate would report
    # the same rows as purged every day, forever, while looking like it worked.
    # Here None means absent, in SQL as well as in Python.
    gateway_status_raw: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    attempt_history: Mapped[list | None] = mapped_column(JSON)
    # Set on the successor a reissue produced. ABA owns the QR's lifetime, so a
    # fresh code means a fresh ABA session — a new row — and the expired parent
    # stays behind as the audit trail of the session that died.
    reissued_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    secret_key: Mapped[str] = mapped_column(String(128), nullable=False)
    events: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=WEBHOOK_ENDPOINT_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), nullable=False, index=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventDelivery(Base):
    __tablename__ = "event_deliveries"
    __table_args__ = (UniqueConstraint("event_id", "endpoint_id", name="uq_event_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("webhook_endpoints.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default=DELIVERY_PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_response_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    monthly_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    base_payments_included: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    max_stores: Mapped[int | None] = mapped_column(Integer)
    max_keys_per_account: Mapped[int] = mapped_column(Integer, default=5)
    max_webhooks_per_account: Mapped[int] = mapped_column(Integer, default=5)
    csv_export_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority_support: Mapped[bool] = mapped_column(Boolean, default=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Pricing-page copy, edited in the admin console and rendered by the website and
    # the user portal so the DB is the only source of truth for plan presentation.
    tagline: Mapped[str | None] = mapped_column(String(160))
    features: Mapped[list | None] = mapped_column(JSON)
    # Which card gets the "Most popular" badge on the pricing page.
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PlanSubscription(Base):
    __tablename__ = "plan_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="trial")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_billing_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PlanInvoice(Base):
    __tablename__ = "plan_invoices"
    __table_args__ = (Index("ix_plan_invoices_period_month", "period_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("plan_subscriptions.id"))
    period_month: Mapped[str] = mapped_column(String(7), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    base_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    usage_payments_count: Mapped[int] = mapped_column(Integer, default=0)
    overage_payments_count: Mapped[int] = mapped_column(Integer, default=0)
    overage_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_due_cents: Mapped[int] = mapped_column(Integer, default=0)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chmabapay_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PlanLedgerEntry(Base):
    __tablename__ = "plan_ledger_entries"
    # One row per movement, and the unique key is what makes the writer's
    # `ON CONFLICT DO NOTHING` mean anything: without it a retry double-counts, which
    # is how the original version inflated. `resource_type` is part of the key so a
    # reversal can sit beside the payment it gives back — both share a `resource_id`.
    # See P0-6 in docs/production-readiness.md.
    __table_args__ = (
        UniqueConstraint(
            "period_month",
            "resource_type",
            "resource_id",
            name="uq_plan_ledger_resource",
        ),
        Index("ix_plan_ledger_account_period", "account_id", "period_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    period_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_cents_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    total_payments_count: Mapped[int] = mapped_column(Integer, default=0)
    total_volume_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Whoever performed the action: an operator on an admin route, but a merchant
    # on their own key, webhook or store — which is most of what lands here. Named
    # for the actor rather than the admin so the column cannot be read as "an
    # operator did this".
    actor_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
