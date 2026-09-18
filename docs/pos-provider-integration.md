# POS provider integration guide

For a POS / merchant-platform ("provider") that wants to accept KHQR payments on behalf of
many merchants through ChmabaPay. One ChmabaPay account represents the whole provider.

For the endpoint-by-endpoint reference see [api.md](api.md); this document is the end-to-end
walkthrough and the parts that are easy to get wrong.

## The model in one minute

| Concept | What it is |
| --- | --- |
| Account | Your workspace. One account = one provider. |
| API key | `ck_live_…`, **account-scoped**: one key authenticates every store. |
| Webhook endpoint | Also **account-scoped**. One endpoint receives events for every store. Not per store. |
| Store | **A store *is* a merchant.** There is no separate "merchant" object. |
| `external_id` | *Your* merchant id, stored on the store. ChmabaPay never invents one. |
| Payment link | Each store settles to **one ABA PayWay share link** belonging to that merchant. |

Money flows merchant to merchant: the payer scans the merchant's KHQR and the funds land in
that merchant's own ABA PayWay account. ChmabaPay generates the QR and reports the outcome; it
does not hold or route funds.

## Flow

1. **Once** — create the account, create a live API key, create one webhook endpoint.
2. **Per merchant** — when a merchant onboards in your POS, `POST /v1/stores` with their
   `external_id`, branding and their ABA PayWay link.
3. **Per sale** — `POST /v1/payments` with `merchant: "<their external_id>"`.
4. **Present** — render `qr_string` yourself, or send the payer to `checkout_url`.
5. **Settle** — receive `payment.completed`, then route on `data.merchant.external_id`.

## 1. One-time setup

### API key

Dashboard → API keys → create. The raw key is shown **once**; store it server-side and never
ship it to a browser. Limits are account-wide and plan-based: Free 1, Starter 3, Pro 10.

```
Authorization: Bearer ck_live_xxxxxxxxxxxxxxxxxxxxxxxx
```

### Webhook endpoint

```http
POST /v1/webhooks
Content-Type: application/json

{ "url": "https://pos.example.com/chmabapay/webhook" }
```

The response includes `signing_secret` (`whsec_…`), shown once. Up to 10 endpoints per account
(enforced). The optional `events` array subscribes the endpoint to specific event types
(`["payment.completed", "payment.expired"]`); omit it or pass `["*"]` to receive all four.

Useful companions: `PATCH /v1/webhooks/{id}` to change the URL or disable it,
`POST /v1/webhooks/{id}/rotate-secret`, `POST /v1/webhooks/{id}/test` to fire a synthetic event,
`GET /v1/webhooks/{id}/deliveries` to inspect recent attempts.

## 2. Provision a merchant

```http
POST /v1/stores
Authorization: Bearer ck_live_…
Content-Type: application/json

{
  "name": "Alpha Mart",
  "external_id": "merchant-alpha-77",
  "support_email": "help@alphamart.example",
  "brand_color": "#0f766e",
  "logo_image_url": "https://cdn.example.com/alpha.png",
  "whitelabel_css": ".card { border-radius: 0; }",
  "redirect_success_url": "https://pos.example.com/paid/merchant-alpha-77",
  "redirect_failure_url": "https://pos.example.com/failed/merchant-alpha-77",
  "link": {
    "raw_link": "https://link.payway.com.kh/alphamart",
    "merchant_account_id": "alphamart",
    "merchant_name": "ALPHA MART CO., LTD"
  }
}
```

Fields:

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Shown to you in the dashboard; max 120 chars. |
| `external_id` | recommended | Your merchant id, unique per account. Payments and reports are keyed by it. |
| `link.raw_link` | yes for a live store | The merchant's ABA PayWay share link. |
| `link.merchant_account_id` | yes if `link` is sent | The PayWay slug — the last path segment of the link (`…/alphamart` → `alphamart`). |
| `link.merchant_name` | recommended | The business name the payer sees in their banking app. |
| `logo_image_url`, `brand_color`, `whitelabel_css` | no | Branding fields, see the note below. Requires the white-label entitlement. |
| `redirect_success_url`, `redirect_failure_url` | no | Where the hosted checkout page redirects the payer. |
| `city`, `support_email`, `telegram_chat_id` | no | Descriptive metadata. |

**Status.** A store created *with* a `link` becomes `active`. A store created *without* one stays
`draft` and cannot take payments (`400 payment_link_disabled`) until you attach a link.

**Attaching or replacing the link later** — `PUT /v1/stores/{public_id}/link` with the same
`link` object. This also promotes a `draft` store to `active`.

**Branding.** White-label branding is an entitlement, not a plan field: an operator grants it per
account in the admin console (`Account.whitelabel_enabled`). Without it, sending any of the three
fields returns `403 whitelabel_not_enabled`, and the hosted checkout page stays platform-branded
even if a store row already carries them. Sending `null` (clearing) is always allowed.

Once enabled, the hosted checkout page (`/pay/{id}`) renders all three: `logo_image_url` as a 22px
logo beside the store name in the header, `brand_color` as the header background (a
`#rgb`/`#rrggbb`/`#rrggbbaa` hex value; anything else falls back to the default red), and
`whitelabel_css` injected into a `<style>` tag after the built-in styles so it can override them.
Every `<` is stripped from the CSS before injection, so nothing can close the `<style>` element or
introduce markup.

Other store routes: `GET /v1/stores`, `GET /v1/stores/{public_id}`,
`PATCH /v1/stores/{public_id}`, `POST /v1/stores/{public_id}/disable`.
`max_stores` is per plan: Free 1, Starter 5, Pro 50. Exceeding it returns `400`.

## 3. Charge

```http
POST /v1/payments
Authorization: Bearer ck_live_…
Content-Type: application/json

{
  "amount": 12.50,
  "merchant": "merchant-alpha-77",
  "reference_id": "INV-2041",
  "metadata": { "cashier": "u_88", "table": "12" },
  "idempotency_key": "INV-2041-attempt-1"
}
```

Targeting the store — pass **one** of:

| Field | Meaning |
| --- | --- |
| `merchant` | Your `external_id`. Preferred: no id mapping table needed on your side. |
| `store` | The `st_…` public id returned when the store was created. |
| neither | Falls back to your single active store. With more than one active store the call fails with `400 store_required_or_merchant_required`. |

`amount` is a JSON number with at most 2 decimals (e.g. `12.50`). Responds `201` on create and
`200` on an idempotent replay — always send `idempotency_key` so a retry cannot double-charge.
`currency` is not a request field; it comes from the store's payment link (USD).

Response:

```json
{
  "id": "HeUWJ6IXj7oXCWFJO5sPt8fP",
  "status": "pending",
  "amount": "12.50",
  "currency": "USD",
  "reference_id": "INV-2041",
  "store": "st_ro-ZI6Qcwo5ZZwvXL8or-RY-",
  "external_id": "merchant-alpha-77",
  "qr_string": "00020101021229…6304AB12",
  "checkout_url": "https://pay.chmaba.com/pay/HeUWJ6IXj7oXCWFJO5sPt8fP",
  "created_at": "2026-09-14T08:06:31.873015Z",
  "expires_at": "2026-09-14T08:11:31.872499Z",
  "approved_at": null
}
```

`external_id` is echoed back so you can confirm which merchant was billed. Amounts are decimal
strings in responses; `expires_at` is 5 minutes after creation.

### Errors that matter to a provider

| Status | `detail` | Cause |
| --- | --- | --- |
| 400 | `merchant_not_found` | No store on your account with that `external_id`. |
| 400 | `merchant_store_disabled` | The store exists but is not `active` (draft or disabled). |
| 400 | `store_required_or_merchant_required` | No target given and you have 0 or 2+ active stores. |
| 400 | `payment_link_disabled` | The store has no active payment link. |
| 400 | `amount_too_low` / `amount_too_high` / `invalid_amount` | Amount outside the link's bounds, or not a clean 2-decimal value. |
| 402 | `quota_exceeded` | Monthly paid-payment quota for your plan is used up. |
| 404 | `store_not_found` | Unknown `store` public id. |

Note the payload shape: FastAPI returns `{"detail": "..."}` for errors, not the
`{"error": "...", "message": "..."}` envelope still shown in `api.md`.

Quota is pooled across **all** your stores and counts only `paid` payments, per calendar month:
Free 3,000, Starter 15,000, Pro 1,000,000.

## 4. Present to the payer

Two options:

- **`qr_string`** — the raw EMVCo/KHQR payload. Render it with any QR library, at ECC level H so
  the centre medallion survives. You keep the payer in your own UI.
- **`checkout_url`** — our hosted page with a live status countdown. It polls our backend, so your
  API key is never exposed to the browser. On a terminal outcome it redirects to the store's
  `redirect_success_url` / `redirect_failure_url` with `?status=success|failed&payment_id=…&reference_id=…`.

Statuses: `pending` → `scanned` → one of `paid` | `expired` | `failed`. The first three are
non-terminal. `GET /v1/payments/{id}` returns the current state; `GET /v1/payments?merchant=…`
lists payments for one merchant.

## 5. Webhooks

Sent to every endpoint on the account whenever a payment changes state. Headers:

```
X-ChmabaPay-Event: payment.completed
X-ChmabaPay-Signature: t=1757833591,v1=8f2c…b41
```

Body:

```json
{
  "id": "1f0c2c7e9a4d4a5e8d3f6b7c2a1e0d9c",
  "type": "payment.completed",
  "created": "2026-09-14T08:06:31.934684+00:00",
  "data": {
    "payment": {
      "id": "HeUWJ6IXj7oXCWFJO5sPt8fP",
      "status": "paid",
      "amount": "12.50",
      "currency": "USD",
      "reference_id": "INV-2041",
      "metadata": { "cashier": "u_88", "table": "12" },
      "approved_at": "2026-09-14T08:06:31.934684+00:00"
    },
    "store": {
      "id": "st_ro-ZI6Qcwo5ZZwvXL8or-RY-",
      "name": "Alpha Mart",
      "redirect_success_url": "https://pos.example.com/paid/merchant-alpha-77"
    },
    "merchant": { "external_id": "merchant-alpha-77" }
  }
}
```

`data.merchant.external_id` is your merchant id — route on it directly instead of maintaining a
store-id map. It is `null` for stores created without an `external_id`.

Event types: `payment.completed`, `payment.scanned`, `payment.expired`, `payment.failed`.

**Delivery.** Any 2xx acknowledges. Anything else retries with exponential backoff
(`2^attempt` seconds, capped at 1 hour) up to **8 attempts**, then the delivery is marked failed.
The event `id` is stable across retries — dedupe on it, because you will occasionally see the
same event twice.

### Verifying the signature

`v1` is `HMAC-SHA256(secret=signing_secret, message="<t>.<raw body>")` as a hex digest. Compare in
constant time, and reject if `t` is more than 300 seconds old. Use the **raw** request body, not a
re-serialized copy.

```js
import crypto from "node:crypto";

export function verifyChmabaPaySignature(rawBody, header, secret) {
  const parts = Object.fromEntries(
    header.split(",").map((chunk) => chunk.split("=").map((s) => s.trim())),
  );
  const t = Number(parts.t);
  if (!t || !parts.v1) return false;
  if (Math.abs(Date.now() / 1000 - t) > 300) return false;

  const expected = crypto
    .createHmac("sha256", secret)
    .update(`${t}.`)
    .update(rawBody)
    .digest("hex");

  const a = Buffer.from(expected, "utf8");
  const b = Buffer.from(parts.v1, "utf8");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
```

```python
import hashlib
import hmac
import time


def verify_chmabapay_signature(raw_body: bytes, header: str, secret: str) -> bool:
    parts = dict(p.strip().split("=", 1) for p in header.split(",") if "=" in p)
    t_raw, v1 = parts.get("t"), parts.get("v1")
    if not t_raw or not v1:
        return False
    t = int(t_raw)
    if abs(time.time() - t) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
```

## 6. Reporting

```http
GET /v1/reports/payments.json?merchant=merchant-alpha-77&from=2026-09-01&to=2026-09-30
GET /v1/reports/payments.csv?merchant=merchant-alpha-77&statuses=paid
```

Same filters on both: `from`, `to` (`YYYY-MM-DD`), `store_id`, `merchant` (your `external_id`),
`statuses` (comma-separated), plus `page` / `per_page` on JSON. CSV columns include
`store_public_id`, `store_name` and `external_id`. CSV export is available on every plan.

## Gotchas

- **Webhook endpoints are per account, not per merchant.** You cannot give each merchant their own
  URL through this API. Filter inside your handler using `data.merchant.external_id`.
- **One API key covers every store.** Leaking it exposes all merchants, so keep it server-side, and
  use `POST /v1/keys/{id}/rotate` if it is ever exposed.
- **Always send `idempotency_key`.** It is scoped per store, and a replay returns the original
  payment with `200` instead of creating a second charge.
- **Attach the payment link when you create the store.** Otherwise it stays `draft` and every
  payment fails.
- **`external_id` must be unique within your account** — reusing one for a second store is
  rejected.
- **A disabled store stops accepting payments** immediately (`400 merchant_store_disabled` on the
  `merchant` path, `400 store_disabled` on the store path).
