"""Pydantic request/response schemas for the public API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ErrorOut(BaseModel):
    error: str
    message: str


# --------------------------------------------------------------------------- #
# Stores (merchants) + payment links
# --------------------------------------------------------------------------- #
class LinkCreate(BaseModel):
    """A store's settlement destination: an ABA PayWay share link."""

    raw_link: str = Field(min_length=3, max_length=512)
    merchant_account_id: str = Field(min_length=3, max_length=120)
    merchant_name: str | None = Field(default=None, max_length=120)


class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    external_id: str | None = Field(default=None, max_length=255)
    city: str = Field(default="Phnom Penh", max_length=15)
    support_email: str | None = Field(default=None, max_length=255)
    redirect_success_url: str | None = None
    redirect_failure_url: str | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    brand_color: str | None = Field(default=None, max_length=16)
    logo_image_url: str | None = None
    whitelabel_css: str | None = None
    link: LinkCreate | None = None


class LinkOut(BaseModel):
    link_type: str
    raw_link: str
    merchant_account_id: str
    merchant_name: str | None
    currency: str
    verification: str
    status: str

    @classmethod
    def from_model(cls, link: Any) -> LinkOut:
        return cls(
            link_type=link.link_type,
            raw_link=link.raw_link,
            merchant_account_id=link.merchant_account_id,
            merchant_name=link.merchant_name,
            currency=link.currency,
            verification=link.verification,
            status=link.status,
        )


class StoreOut(BaseModel):
    id: str
    db_id: int
    name: str
    external_id: str | None
    city: str
    status: str
    support_email: str | None
    redirect_success_url: str | None
    redirect_failure_url: str | None
    telegram_chat_id: str | None
    brand_color: str | None
    logo_image_url: str | None
    whitelabel_css: str | None
    # True when the store belongs to the platform itself rather than a merchant tenant —
    # "ChmabaPay HQ", where plan fees are collected. Surfaced so the platform owner's
    # dashboard can label its own store rather than silently hide it.
    is_internal: bool
    # The platform's billing hold, set when a store is over the plan's store allowance
    # after a downgrade. Orthogonal to `status` on purpose: a held store keeps whatever
    # status it had, so clearing the hold restores exactly what billing took and a store
    # an operator disabled stays disabled. Surfaced so the billing page can offer the
    # merchant the slot chooser — which stores of the fifty stay live.
    billing_suspended_at: datetime | None
    link: LinkOut | None
    created_at: datetime

    @classmethod
    def from_model(cls, store: Any, link: Any | None) -> StoreOut:
        return cls(
            id=store.public_id,
            db_id=store.id,
            name=store.name,
            external_id=store.external_id,
            city=store.city,
            status=store.status,
            support_email=store.support_email,
            redirect_success_url=store.redirect_success_url,
            redirect_failure_url=store.redirect_failure_url,
            telegram_chat_id=store.telegram_chat_id,
            brand_color=store.brand_color,
            logo_image_url=store.logo_image_url,
            whitelabel_css=store.whitelabel_css,
            is_internal=store.is_internal,
            billing_suspended_at=store.billing_suspended_at,
            link=LinkOut.from_model(link) if link else None,
            created_at=store.created_at,
        )


class StorePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    external_id: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=15)
    support_email: str | None = Field(default=None, max_length=255)
    redirect_success_url: str | None = None
    redirect_failure_url: str | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    brand_color: str | None = Field(default=None, max_length=16)
    logo_image_url: str | None = None
    whitelabel_css: str | None = None
    link: LinkCreate | None = None

    def apply_to(self, store: Any) -> None:
        for field_name, value in self.model_dump(
            exclude_unset=True, exclude={"link"}
        ).items():
            setattr(store, field_name, value)


class LinkIn(LinkCreate):
    pass


# --------------------------------------------------------------------------- #
# Payments
# --------------------------------------------------------------------------- #
class PaymentCreate(BaseModel):
    amount: float
    reference_id: str | None = Field(default=None, max_length=255)
    metadata: dict | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)
    store: str | None = Field(default=None, description="Store public id (account-scoped keys)")
    merchant: str | None = Field(
        default=None,
        description="Store external_id — the platform's own merchant identifier",
    )
    hosted_qr: bool | None = Field(
        default=None,
        description=(
            "Let ABA issue the QR instead of building it offline. Omit for auto: ABA is used "
            "whenever the store's link is an ABA PayWay link, because a QR we build ourselves for one "
            "carries no ABA transaction — wallets answer 'QR not found' and the payment can never be "
            "confirmed. Set false only to exercise the offline builder deliberately; on a deployment "
            "with no Bakong Open API credentials and no dev rail, a live request with false is "
            "refused outright, because nothing could ever confirm it."
        ),
    )

    @field_validator("amount")
    @classmethod
    def _validate_amount(cls, v: float) -> float:
        if v is None:
            raise ValueError("amount is required")
        import decimal

        d = decimal.Decimal(str(v))
        if d <= 0:
            raise ValueError("amount_too_low")
        scaled = d * 100
        if scaled != scaled.to_integral_value():
            raise ValueError("invalid_amount")
        return float(d)


class PaymentListed(BaseModel):
    id: str
    status: str
    amount: str
    currency: str
    reference_id: str | None
    # Which store took the payment. Required by any list that spans stores, which is
    # exactly what an account-wide list is — without it the portal can only guess.
    store: str | None = None
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None
    # When the money actually arrived. This was missing while `PaymentOut` had it, so
    # the store overview filtered a field the list endpoint never sent and its
    # "Paid today" card read $0.00 no matter how much had been collected.
    paid_at: datetime | None = None


class PaymentOut(PaymentListed):
    store: str
    external_id: str | None = None
    metadata: dict | None = None
    checkout_url: str | None = None
    qr_string: str | None = None
    scanned_at: datetime | None = None
    paid_at: datetime | None = None
    bakong_ref: str | None = None
    gateway_raw: dict | None = Field(default=None, alias="gateway_status_raw")
    # When the merchant recorded the refund. `paid_at` stays populated alongside it,
    # because the money did move and then moved back — collapsing the two into a
    # single state would misstate the period in one direction or the other.
    reversed_at: datetime | None = None
    # The merchant's own note for the reversal, when they gave one. Without it the
    # dashboard can say *that* a payment was refunded but never *why*, which is the
    # half that makes the record auditable months later.
    reversal_reason: str | None = None
    # When we stopped reconciling this payment. Null means still being watched; set
    # means the detection window closed and the outcome is as final as it gets.
    detection_closed_at: datetime | None = None
    reissued_from: str | None = Field(
        default=None,
        description="Public id of the expired payment this code replaced, if any.",
    )

    model_config = {"populate_by_name": True}


class CheckoutPaymentStatusOut(BaseModel):
    id: str
    status: str
    amount: str
    currency: str
    store_name: str
    store: str
    created_at: datetime
    expires_at: datetime


def money_to_str(amount_cents: int) -> str:
    return f"{amount_cents / 100:.2f}"


# --------------------------------------------------------------------------- #
# Bakong transaction verification (reverse-engineered from api-bakong.nbc.gov.kh)
# --------------------------------------------------------------------------- #
BakongSearchType = Literal["hash", "md5", "short_hash", "instruction_ref", "external_ref"]


class TransactionSearchRequest(BaseModel):
    search_type: BakongSearchType = Field(
        default="hash",
        description="Lookup key type: hash (64-char SHA-256), md5 (32-char), short_hash, instruction_ref, or external_ref",
    )
    value: str = Field(
        min_length=3,
        max_length=255,
        description="The identifier value to search for",
    )
    amount: float | None = Field(
        default=None,
        gt=0,
        description="Optional exact amount filter (required for reliable short_hash lookups, e.g. 4.99)",
    )
    currency: str | None = Field(
        default=None,
        description="Optional ISO currency code, e.g. USD, KHR. Defaults to USD for KHQR payments.",
    )

    @field_validator("search_type")
    @classmethod
    def _normalize_type(cls, v: str) -> str:
        return v.lower().replace("-", "_")

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, v: str | None) -> str | None:
        return v.upper() if v else None


class TransactionPollRequest(TransactionSearchRequest):
    require_status: str | None = Field(
        default="Success",
        description="Required transaction status before returning, e.g. 'Success'. Pass null to return on any match.",
    )
    interval_seconds: float = Field(
        default=2.0,
        ge=0.5,
        le=30.0,
        description="Seconds to wait between retries",
    )
    max_attempts: int = Field(
        default=60,
        ge=1,
        le=900,
        description="Max attempts before giving up",
    )


class TransactionBulkSearchRequest(BaseModel):
    search_type: BakongSearchType = "md5"
    values: list[str] = Field(
        min_length=1,
        max_length=100,
        description="List of identifiers (all the same type)",
    )


class BakongTransactionOut(BaseModel):
    found: bool
    hash: str | None = None
    short_hash: str | None = None
    md5: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    currency: str | None = None
    amount: str | None = None
    description: str | None = None
    instruction_ref: str | None = None
    external_ref: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    acknowledged_at: datetime | None = None

    @classmethod
    def from_service(cls, tx: Any | None) -> BakongTransactionOut:
        if tx is None:
            return cls(found=False)
        created = None
        if tx.created_date_ms is not None:
            created = datetime.fromtimestamp(tx.created_date_ms / 1000, tz=UTC)
        ack = None
        if tx.acknowledged_date_ms is not None:
            ack = datetime.fromtimestamp(tx.acknowledged_date_ms / 1000, tz=UTC)
        amount_str = None
        if tx.amount is not None:
            try:
                amount_str = f"{float(tx.amount):.2f}"
            except (TypeError, ValueError):
                amount_str = str(tx.amount)
        return cls(
            found=True,
            hash=tx.hash,
            short_hash=tx.short_hash,
            md5=tx.md5,
            from_account_id=tx.from_account_id,
            to_account_id=tx.to_account_id,
            from_account_name=tx.from_account_name,
            to_account_name=tx.to_account_name,
            currency=tx.currency,
            amount=amount_str,
            description=tx.description,
            instruction_ref=tx.instruction_ref,
            external_ref=tx.external_ref,
            status=tx.status,
            created_at=created,
            acknowledged_at=ack,
        )


class BulkTransactionSearchResponse(BaseModel):
    results: dict[str, BakongTransactionOut]


class BakongTokenRenewRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255, description="Registered Bakong developer email")


class BakongTokenRenewResponse(BaseModel):
    token: str | None = None
    message: str | None = None


# --------------------------------------------------------------------------- #
# Receipt-level cascading verification: paste any receipt data you have
# (from ABA / ACLB / Bakong / any KHQR bank receipt) and the service tries
# every identifier in priority order against the Bakong ledger.
#
# Priority order (empirically determined on the NBC portal 2026-09-09):
#   1. short_hash + amount + currency   (HIGHEST CONFIDENCE — only 1 match)
#   2. purchase #       -> instruction_ref (merchant internal order id)
#   3. reference #      -> external_ref    (bank tracking id)
#   4. trx id (trx_id)  -> instruction_ref then external_ref
#   5. apv              -> both
#   6. remark           -> NOT an index (only appears in Description)
# --------------------------------------------------------------------------- #
class ReceiptVerifyRequest(BaseModel):
    short_hash: str | None = Field(
        default=None,
        description="Transaction hash # (8-char). From receipt field like 'Transaction hash #: 40ae2382'",
    )
    amount: float | None = Field(
        default=None,
        gt=0,
        description="Original amount (recommended for reliable short_hash lookups). E.g. 0.99",
    )
    currency: str | None = Field(
        default="USD",
        description="Currency from receipt: USD or KHR (normalized to uppercase)",
    )
    purchase_number: str | None = Field(
        default=None,
        description="Purchase # from receipt (merchant internal order id). E.g. 178893859933472",
    )
    reference_number: str | None = Field(
        default=None,
        description="Reference # from receipt (bank/Bakong tracking id). E.g. 100FT38935395561",
    )
    transaction_id: str | None = Field(
        default=None,
        description="Trx. ID / Transaction ID from receipt header. E.g. 60940135993",
    )
    apv_number: str | None = Field(
        default=None,
        description="APV / Approval # from receipt. E.g. 455326",
    )
    remark: str | None = Field(
        default=None,
        description="Remark / Description text. Not an index key but logged for audit.",
    )
    md5: str | None = Field(
        default=None,
        description="Optional 32-char MD5 of the KHQR QR string. Index key Bakong uses for QR payments.",
    )
    full_hash: str | None = Field(
        default=None,
        description="Optional 64-char full SHA-256 transaction hash.",
    )
    require_status: str | None = Field(
        default="Success",
        description="Only return Success transactions by default; null = return any match.",
    )

    @field_validator("currency")
    @classmethod
    def _norm_currency(cls, v: str | None) -> str | None:
        return v.upper() if v else None


class ReceiptAttemptOut(BaseModel):
    type: str
    value: str
    note: str | None = None


class ReceiptVerifyResponse(BaseModel):
    found: bool
    via: str | None = Field(
        description="Which identifier matched: short_hash, purchase_as_instruction_ref, reference_as_external_ref, etc."
    )
    attempts: list[ReceiptAttemptOut]
    transaction: BakongTransactionOut
    """Best-effort attempt list of all lookup keys that were tried (for debugging / audit)."""


# --------------------------------------------------------------------------- #
# KHQR generator from bare ABA PayWay link (reverse-engineered, no API keys)
# --------------------------------------------------------------------------- #
class KHQRFromLinkRequest(BaseModel):
    """Generate a scannable KHQR using only a bare ABA PayWay link slug or URL.

    Supply the link slug (e.g. "ABAPAYpe518710Y" from
    https://link.payway.com.kh/ABAPAYpe518710Y) or the full URL. We fetch the
    SSR page to extract the merchant name and combine it with the optional
    bakong_id / payway_client_id hints to produce a real offline EMVCo TLV
    KHQR that any Bakong member app can scan and settle into your ABA account.
    """
    link: str = Field(
        min_length=6,
        max_length=512,
        description=(
            "ABA PayWay link: either the slug (ABAPAYpe518710Y) or the full URL "
            "(https://link.payway.com.kh/ABAPAYpe518710Y)"
        ),
    )
    amount: float = Field(
        description="Amount in link currency (usually USD). Must be a valid 2-decimal monetary value.",
    )
    bill_number: str | None = Field(
        default=None,
        max_length=64,
        description="Optional bill / invoice number. If omitted, a random 24-char id is generated.",
    )
    reference_id: str | None = Field(
        default=None,
        max_length=255,
        description="Optional external reference_id (copied to Bakong external_ref search key).",
    )
    currency: str | None = Field(
        default=None,
        description="Currency override (USD or KHR). Defaults to USD or whatever the PayWay link advertises.",
    )
    ttl_seconds: int | None = Field(
        default=None,
        ge=30,
        le=86400 * 30,
        description="How many seconds until the QR expires (defaults to checkout TTL from settings).",
    )
    bakong_id: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Optional Bakong merchant account id override (from Tag 30.01). "
            "If supplied, we skip the SSR merchant-name lookup and generate offline immediately. "
            "If omitted, we live-fetch the SSR page to confirm merchant identity."
        ),
    )
    payway_client_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional ABA PayWay client id override (e.g. 2364634-518710-26248177).",
    )
    bakong_direct: bool = Field(
        default=False,
        description=(
            "Build a plain Bakong KHQR addressed to `bakong_id` with no ABA PayWay token block "
            "and no Tag 99. Use this when the destination is a real Bakong account rather than a "
            "PayWay link: PayWay-issued QRs carry a server-side token that cannot be reproduced "
            "offline, whereas a direct Bakong QR needs nothing from ABA."
        ),
    )

    # No `gt=0` on the field above on purpose: a pydantic constraint is
    # evaluated before this validator and would replace the machine-readable
    # `amount_too_low` with its own prose, so the identical condition returned
    # `amount_too_low` on /api/v1/payments but "Input should be greater than 0"
    # here. Clients need one stable code per condition.
    @field_validator("amount")
    @classmethod
    def _validate_amount(cls, v: float) -> float:
        import decimal
        d = decimal.Decimal(str(v))
        if d <= 0:
            raise ValueError("amount_too_low")
        scaled = d * 100
        if scaled != scaled.to_integral_value():
            raise ValueError("invalid_amount")
        return float(d)


class KHQRFromLinkResponse(BaseModel):
    """Output of /api/v1/khqr/from-link — a scannable KHQR plus all search keys."""
    ok: bool = True
    qr_string: str = Field(description="Full EMVCo TLV payload (render as QR code).")
    qr_md5: str = Field(description="MD5 hash of qr_string — primary Strategy B search key.")
    short_hash: str = Field(description="First 16 chars of qr_md5 — secondary search key.")
    instruction_ref: str = Field(description="Bill number (Tag 62.01) — search as instruction_ref.")
    external_ref: str = Field(description="PayWay reference (Tag 62.50.06) — search as external_ref.")
    bill_number: str
    reference_id: str | None
    amount: str = Field(description="Human-readable amount with 2 decimals.")
    amount_cents: int
    currency: str
    merchant_name: str
    merchant_bakong_id: str
    payway_client_id: str | None
    expires_at: datetime = Field(description="When the embedded TTL expires (Tag 62.50.05 epoch).")
    created_at: datetime
    link_slug: str
    link_url: str
    merchant_source: str = Field(
        description="How merchant identity was resolved: 'ssr_fetch' (live PayWay SSR) or 'supplied' (caller passed bakong_id)."
    )
    tags: dict[str, str | dict[str, Any]] | None = Field(
        default=None,
        description="Optional parsed EMVCo Tag dump (for debugging).",
    )


# --------------------------------------------------------------------------- #
# ABA hosted checkout — the PayWay page's own API (reverse-engineered).
# Unlike /api/v1/khqr/from-link this does NOT build the QR ourselves: ABA mints it,
# which is what makes it payable and gives us a handle to poll for payment.
# --------------------------------------------------------------------------- #
class PayWayHostedCheckoutRequest(BaseModel):
    link: str = Field(
        min_length=3,
        max_length=255,
        description="ABA PayWay link slug or full URL, e.g. ABAPAYpe518710Y.",
    )
    amount: float = Field(description="Amount in the link's currency, e.g. 1.00")

    # No `gt=0` here for the same reason as KHQRFromLinkRequest: a pydantic
    # constraint would replace the stable `amount_too_low` code with prose.
    @field_validator("amount")
    @classmethod
    def _validate_amount(cls, v: float) -> float:
        import decimal

        d = decimal.Decimal(str(v))
        if d <= 0:
            raise ValueError("amount_too_low")
        if (d * 100) != (d * 100).to_integral_value():
            raise ValueError("invalid_amount")
        return float(d)


class PayWayHostedCheckoutResponse(BaseModel):
    """A checkout ABA created. Everything here came from ABA, not from us."""

    ok: bool = True
    qr_string: str = Field(description="ABA's own KHQR payload — the payable one.")
    qr_md5: str
    client_id: str = Field(description="Session key the status endpoint answers on.")
    request_time: str = Field(description="Server time ABA issued this session with.")
    token: str = Field(description="Session token; send it back to /payway/status.")
    tran_id: str | None = None
    expires_in_seconds: int | None = Field(
        default=None, description="ABA's own QR lifetime (observed: 180s)."
    )
    download_qr_url: str | None = None
    amount: str
    currency: str
    link_slug: str
    link_url: str
    merchant_name: str | None = None
    instructions: str = (
        "Scan qr_string, then poll POST /api/v1/khqr/payway/status with client_id, "
        "request_time and token until paid is true."
    )


class PayWayHostedStatusRequest(BaseModel):
    client_id: str = Field(min_length=3, max_length=64)
    request_time: str = Field(min_length=8, max_length=20)
    token: str = Field(min_length=16, max_length=4096)


class PayWayHostedStatusResponse(BaseModel):
    action: str = Field(
        description="One of request_qr | scanned | rqpay | processing-payment | approved."
    )
    paid: bool = Field(description="True only when action == 'approved'.")
    terminal: bool = Field(description="True when polling can stop (approved).")
    receipt_url: str | None = None
    tran_id: str | None = None
    raw: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Status check output (ABA link first, then Bakong Open API)
# --------------------------------------------------------------------------- #
class PaymentStatusOut(BaseModel):
    """Result of a priority-ordered status check on one payment."""

    payment_public_id: str
    status: str = Field(description="PAID / PENDING / FAILED / UNKNOWN")
    source: str | None = Field(
        description=(
            "Which source reported this result: 'payway_hosted_checkout' when an "
            "ABA-hosted session answered, 'bakong_open_api' when the Bakong ledger "
            "matched, or null when no source could be reached."
        )
    )
    matched_amount: float | None
    transitioned_to_paid: bool
    signals: list[str] = Field(default_factory=list)
    aba_signals: list[str] = Field(default_factory=list)
    bakong_via: str | None
    bakong_tx_status: str | None
    bakong_tx: BakongTransactionOut | None
    error: str | None

