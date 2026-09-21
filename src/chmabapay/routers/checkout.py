"""Hosted, branded checkout page (public, read-only by payment token)."""

from __future__ import annotations

import html
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, schemas
from ..db import get_session
from ..khqr import render_qr_svg
from ..openapi import ErrorOut
from ..schemas import money_to_str

router = APIRouter(tags=["checkout"])

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")

_STYLE = """
:root{--red:#d92b2b;--ink:#1f2430;--muted:#6b7280;--ok:#16a34a;--warn:#d97706}
*{box-sizing:border-box;margin:0}
body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:#f3f4f6;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px;color:var(--ink)}
.card{background:#fff;border-radius:18px;box-shadow:0 18px 50px rgba(0,0,0,.14);width:100%;max-width:380px;overflow:hidden}
.head{background:var(--red);color:#fff;padding:16px 20px;font-weight:600;font-size:15px;text-align:center}
.logo{height:22px;vertical-align:-4px;margin-right:8px;border-radius:4px}
.body{padding:20px 24px 24px}
.amount{font-size:34px;font-weight:700;letter-spacing:-.5px;margin:4px 0 2px}
.amount small{font-size:16px;color:var(--muted);font-weight:500}
.meta{color:var(--muted);font-size:13px;margin-bottom:14px}
.panel{border-radius:12px;padding:14px;text-align:center;font-size:14px;font-weight:600}
.pending{background:#fef3c7;color:var(--warn)}
.scanned{background:#e0f2fe;color:#0369a1}
.paid{background:#dcfce7;color:var(--ok)}
.expired,.failed{background:#fee2e2;color:#b91c1c}
#countdown{text-align:center;margin-top:12px;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.foot{text-align:center;font-size:11px;color:#9ca3af;padding-bottom:14px}
"""

_JS = """
const TERMINAL = new Set(["paid","expired","failed"]);
const INITIAL = %(initial)s;
let state = INITIAL;
function render(s){
  const p = document.getElementById("panel");
  const c = document.getElementById("countdown");
  const klasses = {pending:"pending",scanned:"scanned",paid:"paid",expired:"expired",failed:"failed"};
  p.className = "panel " + (klasses[s.status]||"pending");
  const map = {
    pending: "Waiting for you to scan and pay…",
    scanned: "QR scanned — confirm the payment in your banking app.",
    paid: "Payment received. You can close this page.",
    expired: "This code has expired. Ask the seller for a new one.",
    failed: "Payment failed."
  };
  p.textContent = map[s.status] || s.status;
  const done = TERMINAL.has(s.status);
  if (c) c.hidden = done;
  if (done){
    const url = s.status === "paid" ? s.redirect_success : s.redirect_failure;
    if (url){
      const sep = url.includes("?") ? "&" : "?";
      window.location.replace(url + sep + "status=" + s.status + "&payment_id=" + s.id + "&reference_id=" + encodeURIComponent(s.reference_id||""));
    }
  }
}
function tick(){
  const el = document.getElementById("countdown");
  if (!el || state.expires_ms == null) return;
  const left = Math.max(0, Math.ceil((state.expires_ms - Date.now()) / 1000));
  el.textContent = "Expires in " + Math.floor(left/60) + ":" + String(left%60).padStart(2,"0");
}
setInterval(async () => {
  if (TERMINAL.has(state.status)) return;
  try {
    const r = await fetch("/pay/" + state.id + "/status", {cache:"no-store"});
    if (r.ok) { state = await r.json(); render(state); }
  } catch (e) {}
}, 2000);
render(state); tick(); setInterval(tick, 1000);
"""


def _panel_class(status: str) -> str:
    return {
        "pending": "pending",
        "scanned": "scanned",
        "paid": "paid",
        "expired": "expired",
        "failed": "failed",
        # Both mean "this code will not take money", and both already have a visual
        # language: `expired` for a code that lapsed, `failed` for one that is over.
        # Mapped rather than given their own classes so no new CSS is needed for
        # states a customer should almost never reach.
        "superseded": "expired",
        "reversed": "failed",
    }.get(status, "pending")


def _brand_accent(value: str | None) -> str:
    """Store owners set brand_color as a hex string; anything else keeps the default."""
    if value and _HEX_COLOR.match(value.strip()):
        return value.strip()
    return "#d92b2b"


def _checkout_html(
    payment: models.Payment,
    store: models.Store,
    base_url: str,
    *,
    whitelabel: bool = False,
) -> str:
    initial = {
        "id": payment.public_id,
        "status": payment.status,
        "amount": money_to_str(payment.amount_cents),
        "currency": payment.currency,
        "reference_id": payment.reference_id,
        "expires_ms": int(payment.expires_at.timestamp() * 1000),
        "redirect_success": store.redirect_success_url,
        "redirect_failure": store.redirect_failure_url,
    }
    js = _JS.replace("%(initial)s", json.dumps(initial))
    merchant = html.escape(store.name or store.public_id)

    if whitelabel:
        logo_url = (store.logo_image_url or "").strip()
        logo = (
            f'<img class="logo" src="{html.escape(logo_url, quote=True)}" alt="">'
            if logo_url
            else ""
        )
        # Store CSS is the point of the whitelabel feature, but it lands inside a
        # <style> element, so no "<" may survive: one stray "<" is all it takes to
        # close the element and start injecting markup.
        custom_css = (store.whitelabel_css or "").replace("<", "")
        custom = f"<style>{custom_css}</style>" if custom_css.strip() else ""
        accent = _brand_accent(store.brand_color)
    else:
        # Branding is an entitlement; without it the page stays platform-branded
        # even if a store row still carries the fields.
        logo = custom = ""
        accent = "#d92b2b"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pay {merchant}</title>
<style>{_STYLE}</style>
<style>:root{{--red:{accent}}}</style>
{custom}</head>
<body><div class="card">
<div class="head">{logo}{merchant}</div>
<div class="body">
<div class="meta">Scan the QR shown by the seller, then confirm the payment in your banking app.</div>
<div class="amount">{money_to_str(payment.amount_cents)} <small>{payment.currency}</small></div>
<div class="panel {_panel_class(payment.status)}" id="panel">…</div>
<div id="countdown"></div>
</div><div class="foot">{base_url}</div>
</div><script>{js}</script></body></html>"""


@router.get("/pay/{public_id}")
async def checkout_page(public_id: str, session: AsyncSession = Depends(get_session)):
    row = (
        await session.execute(
            select(models.Payment, models.Store, models.Account)
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .join(models.Account, models.Account.id == models.Store.account_id)
            .where(models.Payment.public_id == public_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    payment, store, account = row
    return HTMLResponse(
        _checkout_html(
            payment, store, "/", whitelabel=account.whitelabel_enabled
        )
    )


@router.get(
    "/pay/{public_id}/qr.svg",
    response_class=Response,
    responses={
        404: {"model": ErrorOut, "description": "No such payment (`payment_not_found`), or none was minted for it."},
        410: {"model": ErrorOut, "description": "The code is dead (`payment_expired` / `payment_superseded` / `payment_reversed`) and is no longer served."},
    },
)
async def checkout_qr(public_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    """The payment's own QR, as a short stable URL.

    The payload itself is ~260 characters of EMVCo TLV. Handing that around as a
    query string on /v1/khqr/render.svg works in an <img src> but not for a human:
    it contains percent-escaped spaces, and anything that decodes or wraps it
    truncates the payload — which renders a perfectly valid QR for the *wrong*
    string, and the wallet answers "invalid QR". One stable URL per payment
    removes that whole failure mode.
    """
    row = (
        await session.execute(
            select(models.Payment).where(models.Payment.public_id == public_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    # A dead code must stop being served. The payload is still in the row, but
    # ABA's session behind it is gone — rendering it would hand the customer a
    # QR that a wallet reads as valid and then refuses to settle. 410 so a
    # caller can tell "expired" from "never existed" (404).
    #
    # `superseded` and `reversed` belong here too: a replacement code took this
    # one's place, or the sale was refunded. Serving either would offer a customer
    # a second chance to pay for a sale that is already settled or retired.
    if row.status in models.PAYMENT_DEAD_STATUSES:
        raise HTTPException(status_code=410, detail=f"payment_{row.status}")
    payload = (row.qr_string or "").strip()
    if not payload:
        raise HTTPException(status_code=404, detail="payment_has_no_qr")
    try:
        svg = render_qr_svg(payload)
    except Exception as exc:  # noqa: BLE001 - segno raises several concrete types
        raise HTTPException(status_code=500, detail=f"qr_render_failed: {exc}") from exc
    return Response(
        content=svg.encode("utf-8"),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/pay/{public_id}/status",
    response_model=schemas.CheckoutPaymentStatusOut,
    responses={404: {"model": ErrorOut, "description": "No such payment (`payment_not_found`)."}},
)
async def checkout_status(public_id: str, session: AsyncSession = Depends(get_session)):
    row = (
        await session.execute(
            select(models.Payment, models.Store)
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .where(models.Payment.public_id == public_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    payment, store = row
    return schemas.CheckoutPaymentStatusOut(
        id=payment.public_id,
        status=payment.status,
        amount=money_to_str(payment.amount_cents),
        currency=payment.currency,
        store_name=store.name,
        store=store.public_id,
        created_at=payment.created_at,
        expires_at=payment.expires_at,
    )
