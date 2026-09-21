"""Payment status reconciliation.

Status sources, in the order we trust them:

  0. ABA's own hosted checkout (pwapp.ababank.com) — AUTHORITATIVE for payments
     whose QR ABA issued. ABA creates the transaction, so ABA is the only party
     that can confirm it. The session handle (client_id/request_time/token) is
     stored on the payment at creation in gateway_status_raw.payway_hosted, and
     one call answers for it. This is the only source that works today, and it
     runs first. See services/payway_parser.py:fetch_hosted_status.

     It is also the reason a QR we build ourselves cannot work: /v1/khqr/from-link
     produces a syntactically valid EMVCo payload with no ABA transaction behind
     it, so a wallet resolving the payee answers "QR not found" and there is
     nothing anywhere to poll. Payments on an ABA PayWay link therefore default
     to hosted issuance (see services/payments.py:create_payment).

  1. Bakong Open API — UNAVAILABLE in practice. Every KHQR we issue settles on
     the Bakong rail, and Bakong indexes settled QR payments by the MD5 of the
     QR string, which we compute at creation (Payment.qr_md5) — exactly the key
     the wallet presented. It is the right primary source and it is implemented,
     but it needs a developer token and NBC declines to register developer
     emails, so verify_receipt answers 401. Kept for when credentials exist.

  2. The ABA PayWay link page (link.payway.com.kh/<slug>) — DISPLAY ONLY.

Measured 2026-09-15 against a real, successfully-paid QR for
link.payway.com.kh/ABAPAYpe518710Y: that page is a static Nuxt template. It
serves order_details.status="OPEN" and amount="0.00" on every request, ignores
?bill_number= and ?reference_id=, and carries no per-transaction data at all.
The only "paid"/"failed" strings in it are fixed UI copy ("paid successfully",
"payment failed") present whether or not anyone paid. Two consequences:

  - A payment routed through it can never be detected there, so polling it
    reports PENDING forever.
  - Treating its copy as signals risks a FALSE PAID, the worst possible failure
    for a payment platform.

It is still fetched so merchants can see the page state, but it can never set a
status in this module.

When a payment has a hosted session the module returns early on it: such a
payment is confirmed by ABA or not at all. Falling through to Bakong used to emit
a misleading `bakong_unauthorized` and blank the source, implying a second source
had been consulted that cannot possibly know about the transaction. A transient
ABA failure now reports PENDING with `payway_hosted_unavailable` instead.

For payments with no hosted session the Bakong stage still runs, and without
credentials `verify_receipt` answers 401 and the result carries
`result.error = "bakong_not_configured"`. That is deliberate — it says "we could
not look", which is different from "not paid".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from .bakong import (
    BAKONG_ERROR_UNAUTHORIZED,
    BakongApiClient,
    BakongApiError,
    BakongTransaction,
    ReceiptVerifyResult,
)
from .payway_parser import (
    PayWayHostedError,
    PayWayPaymentStatus,
    fetch_hosted_status,
    fetch_payment_status,
)

Status = Literal["PAID", "PENDING", "FAILED", "UNKNOWN"]

STATUS_SOURCE_BAKONG_OPEN_API = "bakong_open_api"
STATUS_SOURCE_PAYWAY_HOSTED = "payway_hosted_checkout"


@dataclass
class ReconcileResult:
    payment_id: int
    payment_public_id: str
    status: Status
    source: str | None
    matched_amount: float | None
    signals: list[str] = field(default_factory=list)
    aba_status: PayWayPaymentStatus | None = None
    bakong_result: ReceiptVerifyResult | None = None
    bakong_tx: BakongTransaction | None = None
    transitioned_to_paid: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("PAID", "PENDING", "FAILED")


def _payment_is_aba_payway_link(payment: models.Payment) -> tuple[bool, str | None]:
    """Heuristic: does this payment trace back to an ABA Payment Link slug?

    Checks (in order):
      1. PaymentLink.raw_link starts with https://link.payway.com.kh/  (explicit)
      2. PaymentLink.link_type == 'aba_payway'                        (explicit)
      3. Payment metadata {aba_payway_slug, payway_slug}              (API caller hint)
      4. Payment.qr_string contains 'ABA' Tag30.00 + '236…' payway client in Tag62.50.01  (heuristic)
    """
    raw_link: str | None = None
    meta = payment.metadata_ if isinstance(payment.metadata_, dict) else {}

    # Hint fields (fastest path)
    for key in ("aba_payway_slug", "payway_slug", "link_slug"):
        val = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(val, str) and val:
            return True, val
    for key in ("aba_payway_url", "payway_url", "payway_link_url"):
        val = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(val, str) and val:
            return True, val

    # PaymentLink relationship
    if payment.payment_link_id is not None:
        # Caller may eager-load via options(selectinload); otherwise we accept
        # the payment_link_id as a "not None = probably ABA" loose hint only
        # when combined with metadata. Real impl should load the link row. We
        # don't force a DB round-trip here; the API caller is expected to
        # supply the slug via metadata when calling verify_internal_payment.
        pass

    # QR string heuristic: Tag 30.00 = "GI abaakhppxxx@abaa" (ABA)
    qr = payment.qr_string or ""
    if qr and ("abaakhppxxx@abaa" in qr or "PAYWAY@ABA" in qr):
        # Extract the slug from metadata if present, otherwise fall back to
        # qr-bill_number only (bill_number is enough for status polling)
        return True, None

    return False, raw_link


async def reconcile_payment(
    payment: models.Payment,
    *,
    bakong_client: BakongApiClient,
    session: AsyncSession | None = None,
    aba_slug_hint: str | None = None,
    aba_client_id_hint: str | None = None,
    prefer_aba: bool = True,
) -> ReconcileResult:
    """Reconcile `payment` against the Bakong ledger.

    Bakong Open API is authoritative; the ABA PayWay page is fetched for display
    only and never sets a status (see the module docstring for the measurements).

    If Bakong reports the QR as settled and we have a DB session, we transition
    the internal payment row to PAID (idempotent via services.payments.mark_paid).
    """
    from . import payments as pay_svc  # Local import to avoid circular init

    signals: list[str] = []
    result = ReconcileResult(
        payment_id=payment.id or 0,
        payment_public_id=payment.public_id or "",
        status="UNKNOWN",
        source=None,
        matched_amount=None,
    )

    is_aba, slug_guess = _payment_is_aba_payway_link(payment)
    slug = aba_slug_hint or slug_guess
    expected_amount_usd = payment.amount_cents / 100.0 if payment.amount_cents else None
    bill_number: str | None = getattr(payment, "bill_number", None)
    reference_id: str | None = getattr(payment, "reference_id", None)

    # --------------------------------------------------------------- #
    # Stage 0 — ABA's own hosted checkout.
    #
    # When a payment was created with hosted_qr, ABA holds the transaction and
    # answers for it directly. This is the only source that can actually
    # confirm a PayWay payment, so it runs first and returns immediately on a
    # positive result.
    # --------------------------------------------------------------- #
    gw = payment.gateway_status_raw if isinstance(payment.gateway_status_raw, dict) else {}
    hosted = gw.get("payway_hosted") if isinstance(gw.get("payway_hosted"), dict) else None
    hosted_action: str | None = None
    if hosted:
        try:
            hs = await fetch_hosted_status(
                client_id=str(hosted.get("client_id") or ""),
                request_time=str(hosted.get("request_time") or ""),
                token=str(hosted.get("token") or ""),
            )
            hosted_action = hs.action
            signals.append(f"hosted_action:{hs.action}")
            if hs.paid:
                result.status = "PAID"
                result.source = STATUS_SOURCE_PAYWAY_HOSTED
                result.matched_amount = expected_amount_usd
                signals.append("resolved_via:payway_hosted")
                # EXPIRED is included: a hosted QR lives 180s and W4 sweeps every
                # 60s, so a payment made near the deadline can be timed out a beat
                # before the confirming poll lands. ABA holding the transaction
                # proves the money moved, so expiry must not be allowed to discard
                # it — mark_paid already accepts the promotion.
                #
                # SUPERSEDED is included for the same reason: retiring a replacement
                # code does not stop a customer who already holds it from paying, and
                # that settlement has to be recorded rather than hidden. mark_paid
                # refuses only PAID, FAILED and REVERSED.
                if session is not None and payment.status in (
                    models.PAYMENT_PENDING,
                    models.PAYMENT_SCANNED,
                    models.PAYMENT_EXPIRED,
                    models.PAYMENT_SUPERSEDED,
                ):
                    try:
                        transitioned = await pay_svc.mark_paid(
                            session,
                            payment.public_id,
                            bakong_ref=hs.tran_id or payment.bakong_ref,
                            # mark_paid replaces this column, so carry the session
                            # forward together with the evidence that settled it.
                            gateway_raw={
                                "payway_hosted": hosted,
                                "hosted_action": hs.action,
                                "hosted_status": hs.raw,
                                "receipt_url": hs.receipt_url,
                            },
                        )
                        result.transitioned_to_paid = transitioned is not None
                    except Exception as exc:
                        result.error = f"mark_paid_hosted:{exc}"
                result.signals = signals
                return result
        except PayWayHostedError as exc:
            signals.append(f"hosted_status_error:{exc or type(exc).__name__}")
        except Exception as exc:
            signals.append(f"hosted_status_error:{exc or type(exc).__name__}")

    if hosted:
        # A hosted payment is confirmed by ABA or not at all. When the session
        # exists but the call failed, falling through to Bakong used to emit a
        # misleading bakong_unauthorized and blank the source, implying we had
        # consulted a second source that cannot possibly know about this
        # transaction. Report the transient failure instead.
        result.status = "PENDING"
        result.source = STATUS_SOURCE_PAYWAY_HOSTED
        if hosted_action is None:
            result.error = "payway_hosted_unavailable: ABA did not answer for this session; retry"
        result.signals = signals
        return result

    # --------------------------------------------------------------- #
    # Stage 1 — Bakong Open API (AUTHORITATIVE, for every payment type)
    # --------------------------------------------------------------- #
    aba_status: PayWayPaymentStatus | None = None
    bakong_res: ReceiptVerifyResult | None = None
    bakong_tx: BakongTransaction | None = None
    # `short_hash` is deliberately not passed: our Payment row has no such
    # column, and the value we publish as short_hash is only md5[:16], which is
    # not Bakong's shortHash. Passing it would just add a doomed lookup.
    try:
        bakong_res = await bakong_client.verify_receipt(
            amount=expected_amount_usd,
            currency=payment.currency or "USD",
            purchase_number=bill_number,
            reference_number=reference_id,
            md5=getattr(payment, "qr_md5", None),
            full_hash=getattr(payment, "bakong_ref", None),
            require_status=None,  # We check status ourselves below
        )
        result.bakong_result = bakong_res
        bakong_tx = bakong_res.transaction
        result.bakong_tx = bakong_tx
        if bakong_res.found and bakong_tx is not None:
            signals.append(
                f"bakong_found_via:{bakong_res.via} status={bakong_tx.status}"
            )
    except BakongApiError as exc:
        bakong_res = None
        bakong_tx = None
        if exc.error_code == BAKONG_ERROR_UNAUTHORIZED:
            # Without credentials nothing can be confirmed. Say so out loud: a
            # silent PENDING here is indistinguishable from "not paid yet".
            result.error = (
                "bakong_not_configured: the Bakong Open API rejected our credentials, so "
                "payment status cannot be determined. Set BAKONG_API_TOKEN, or "
                "BAKONG_DEVELOPER_EMAIL so a token can be renewed automatically."
            )
            signals.append("bakong_unauthorized")
        else:
            signals.append(f"bakong_check_error:{exc}")
    except Exception as exc:
        signals.append(f"bakong_check_error:{exc}")
        bakong_res = None
        bakong_tx = None

    if bakong_tx is not None and (
        (bakong_tx.status or "").lower() in ("success", "completed", "paid")
    ):
        result.status = "PAID"
        result.source = STATUS_SOURCE_BAKONG_OPEN_API
        result.matched_amount = bakong_tx.amount or expected_amount_usd
        signals.append("resolved_via:bakong_open_api")
        if (
            session is not None
            and payment.status in (models.PAYMENT_PENDING, models.PAYMENT_SCANNED)
            and not result.transitioned_to_paid
        ):
            try:
                # Confirm the amount before marking paid — but only when the
                # ledger currency matches ours. A USD QR paid from a KHR wallet
                # is settled by ABA in KHR at ABA's own rate, so the recorded
                # amount legitimately differs and comparing raw numbers would
                # reject a real payment. Identity is already established by the
                # md5 lookup, which uniquely identifies this one QR.
                ok_amount = True
                tx_currency = (bakong_tx.currency or "").upper()
                our_currency = (payment.currency or "USD").upper()
                if tx_currency and tx_currency != our_currency:
                    signals.append(
                        f"amount_check_skipped:settled_in={tx_currency} expected={our_currency}"
                    )
                elif expected_amount_usd is not None and bakong_tx.amount is not None:
                    try:
                        ok_amount = abs(float(bakong_tx.amount) - expected_amount_usd) < 0.005
                    except (TypeError, ValueError):
                        ok_amount = False
                    if not ok_amount:
                        signals.append(
                            f"amount_mismatch:ledger={bakong_tx.amount} expected={expected_amount_usd}"
                        )
                if ok_amount:
                    transitioned = await pay_svc.mark_paid(
                        session,
                        payment.public_id,
                        bakong_ref=bakong_tx.hash or bakong_tx.md5 or payment.bakong_ref,
                        gateway_raw={
                            "reconciled_via": "bakong_open_api:" + str(bakong_res.via if bakong_res else "unknown"),
                            "bakong": bakong_tx.raw,
                            "via": bakong_res.via if bakong_res else None,
                            "attempts": bakong_res.attempts if bakong_res else [],
                        },
                    )
                    result.transitioned_to_paid = transitioned is not None
            except Exception as exc:
                result.error = f"mark_paid_bakong:{exc}"
        # Without this the PAID path returned an empty signal trail, so the
        # merchant saw a status with no evidence of how it was reached.
        result.signals = signals
        return result

    # --------------------------------------------------------------- #
    # Stage 2 — ABA PayWay link page. DISPLAY ONLY: it contributes signals
    # for the merchant to look at, and can never set a status (see module
    # docstring). Only fetched when we actually hold a slug or a link URL —
    # passing a bill number here used to build a nonsense URL like
    # link.payway.com.kh/CHM... and burn a request on a 404.
    # --------------------------------------------------------------- #
    if prefer_aba and (slug or aba_client_id_hint):
        try:
            aba_status = await fetch_payment_status(
                slug or "",
                bill_number=bill_number,
                reference_id=reference_id,
                expected_amount_usd=expected_amount_usd,
            )
            result.aba_status = aba_status
            signals.extend(aba_status.signals)
            signals.append(f"aba_page_status_ignored:{aba_status.status}")
        except Exception as exc:
            signals.append(f"aba_check_error:{exc}")
            aba_status = None

    # --------------------------------------------------------------- #
    # Status decision — Bakong is the only source allowed to set it.
    # --------------------------------------------------------------- #
    if bakong_tx is not None:
        ledger_status = (bakong_tx.status or "").lower()
        if ledger_status in ("failed", "rejected", "cancelled", "canceled", "refunded"):
            result.status = "FAILED"
        else:
            result.status = "PENDING"
        result.source = STATUS_SOURCE_BAKONG_OPEN_API
        result.matched_amount = bakong_tx.amount
    else:
        # Either the QR has not been paid yet, or we were unable to look. The
        # distinction matters and lives in result.error.
        result.status = "PENDING"
    result.signals = signals
    return result
