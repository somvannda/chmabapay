# Public API spec (v1)

Mirrors CutLuy's developer surface so existing mental models (and SDK examples) map 1:1.
Base URL `https://<host>/v1`. JSON in/out. Auth: `Authorization: Bearer ck_live_…` / `ck_test_…`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/payments` | Create a payment (KHQR + hosted checkout) |
| GET | `/v1/payments/:id` | Fetch current state |
| GET | `/v1/payments` | List own store's payments (newest first) |
| GET | `/pay/:id` | **Public** hosted checkout page (no auth) |
| GET | `/api/render/khqr/:payload.svg` | Free KHQR-card SVG renderer (no auth) |

Dashboard/settings/billing/webhook-management are web UI endpoints, not part of the public API.

## POST /v1/payments

Request body:

```json
{ "amount": 1.50, "reference_id": "order_1024", "metadata": {}, "idempotency_key": "retry-safe-key" }
```

`reference_id`, `metadata`, `idempotency_key` optional. `idempotency_key` also honoured via the
`Idempotency-Key` header. Validation: `amount ≥ 0.01`, ≤ link max, ≤ 2 decimals, `amount_too_low`,
`amount_too_high`, `invalid_amount` otherwise. Missing/invalid key → `401 unauthorized`.
Quota exhausted → `402 quota_exceeded`. No link / disabled → `404 payment_link_not_found` /
`400 payment_link_disabled`. API provider unreachable → `502 payment_provider_error`.

Response `201 Created` (or `200` on idempotent replay):

```json
{
  "id": "PUETcMUOKStjZsCb6zAl8kg9fMRGM85x",
  "status": "pending",
  "amount": "1.50",
  "currency": "USD",
  "reference_id": "order_1024",
  "metadata": null,
  "qr_string": "00020101021229…6304AB12",
  "checkout_url": "https://pay.example.com/pay/PUETcMUOKStjZsCb6zAl8kg9fMRGM85x",
  "approved_at": null,
  "created_at": "2026-09-09T12:00:00.000Z",
  "expires_at": "2026-09-09T12:05:00.000Z"
}
```

Amounts serialized as decimal strings. `qr_string` is the raw EMVCo/KHQR payload to hand to any
QR library (render at ECC H so the centre medallion survives). Generated server-side only.

## GET /v1/payments/:id and GET /v1/payments

`GET /v1/payments` params: `status` (filter), `limit` (default 20, max 100). Returns under `data`.
`metadata` only present on retrieve (parity with CutLuy), `qr_string`/`checkout_url` on create+list.

## Payment object / statuses

| field | type | notes |
| --- | --- | --- |
| id | string | opaque, used in checkout_url + lookups |
| status | string | pending / scanned / paid / expired / failed / superseded / reversed |
| amount / currency | string | decimal string / "USD" |
| reference_id | string \| null | merchant-supplied |
| qr_string | string | KHQR payload |
| checkout_url | string | hosted branded page |
| approved_at | string \| null | when paid |
| paid_at / reversed_at | string \| null | settlement, and the refund if one was recorded |
| detection_closed_at | string \| null | when we stopped reconciling this payment |
| created_at / expires_at | string | see the note on expiry below |

**Expiry is the rail's, not ours.** For an ABA-hosted checkout ABA returns
`expire_in_sec` (180 s observed) and we mirror it, so the 5-minute
`checkout_ttl_seconds` applies only to codes we build ourselves. Once the window
closes the code stops being served (`410`) and `payment.expired` fires — but **that
is not the end of the sale.** ABA keeps accepting the payment, and on 2026-09-17 one
settled nine minutes after its own expiry event. We keep reconciling for
`detection_window_seconds` (1 h by default) and promote the row when the money turns
up, so `expired → paid` is a normal, expected transition.

Statuses, by what they mean for money:

- `paid` — settled. Counts toward quota, fires `payment.completed`.
- `reversed` — settled, then refunded. `paid_at` is kept and `reversed_at` is added,
  so a report can say "collected, then given back" rather than pretending the sale
  never happened. Set only by `POST /v1/payments/:id/reverse`, because ABA gives us
  no callback for a refund.
- `expired` — the code stopped being served. **Not terminal:** still reconcilable, and
  it becomes `paid` if the money arrives.
- `superseded` — a newer code replaced this one (the merchant reissued, and the
  original then settled). Its QR is withdrawn (`410`) so one sale cannot be paid twice.
- `failed` — nothing moved.
- `detection_closed_at` — set once, when the detection window closes. Until then we
  are still looking. After it, the outcome is as final as it gets; if we could not get
  an answer from the rail at that moment, an operator is alerted rather than the
  question being dropped.

## POST /v1/payments/:id/reverse

Record that a settled payment was refunded. Body: `{"reason": "…"}` (optional, and
worth supplying — six months later a reversal with no reason is indistinguishable
from a mistake).

`409 payment_not_paid` if the payment never settled, `409 payment_already_reversed`
on replay, `404` if it belongs to another account. Fires `payment.reversed`.

This exists because ABA provides no callback and its status endpoint reports no
reversal, so nothing in the platform can discover a refund on its own. Without it a
refunded payment reads `paid` for the rest of its retention window and every report
built on it overstates revenue permanently.


## Hosted checkout `/pay/:id`

Mobile-friendly page: store name, KHQR card with countdown, live status. Polls **our** backend
(never exposes API keys to the browser) and stops on terminal status. On terminal outcome,
redirects to the store's configured success/failure URL appending
`?status=<paid|expired|failed>&payment_id=…&reference_id=…` (the raw payment status, not
`success`/`failed`).

Branding — the store logo, `brand_color`, and `whitelabel_css` — renders only when the account
has the white-label entitlement (`Account.whitelabel_enabled`, granted by the platform operator
in the admin console). Without it the page stays platform-branded, and the store API refuses
those three fields with `403 whitelabel_not_enabled`. Clearing them is always allowed.

## Webhooks

POSTed to each enabled endpoint URL on state change. Headers:

- `X-ChmabaPay-Event`: `payment.completed | payment.scanned | payment.expired |
  payment.failed | payment.superseded | payment.reversed`
- `X-ChmabaPay-Signature`: `t=<unix>,v1=<hex>` — HMAC-SHA256 of `t.<rawBody>` with the endpoint's
  secret. Verify against the **raw body**, constant-time, reject if `|now - t| > 300 s`.

Body:

```json
{
  "id": "b3f1c2a0-9e2d-4a1b-8c7f-1e2d3c4b5a6f",
  "type": "payment.completed",
  "created": "2026-09-09T12:03:11.000Z",
  "financial": true,
  "data": {
    "payment": {
      "id": "PUETcMUOKStjZsCb6zAl8kg9fMRGM85x",
      "status": "paid",
      "amount": "1.50",
      "currency": "USD",
      "reference_id": "order_1024",
      "metadata": null,
      "approved_at": "2026-09-09T12:03:10.000Z",
      "created_at": "2026-09-09T12:01:44.000Z",
      "expires_at": "2026-09-09T12:04:44.000Z",
      "paid_at": "2026-09-09T12:03:10.000Z",
      "reversed_at": null,
      "settled_late": false
    }
  }
}
```

### Reporting contract

**Branch on `financial`, not on the event name.** It is `true` for
`payment.completed` and `payment.reversed` and `false` for everything else, which
makes the two money-moving events the only ones an accounting system may book.

`payment.expired` in particular is **not** a lost sale. It means the code stopped
being served, and the same payment can still settle: on 2026-09-17 one did, nine
minutes after `payment.expired` had been delivered. A consumer that books it as a
write-off will be wrong, and will then receive a `payment.completed` for the same
`payment.id` to contradict it. Treat `expired` as an operational event — "stop
showing this code" — and wait for `completed` before recognising anything.

Attributes the two ends of the money's journey separately:

- `data.payment.created_at` — when the sale was made. Use it for *business-day*
  attribution, so a till closed at midnight reconciles with the POS.
- `data.payment.paid_at` — when the cash arrived. Use it for *cash* recognition.
- `data.payment.settled_late` — `true` when `paid_at` is after `expires_at`, i.e. the
  money arrived after the code had been withdrawn. A late recovery is a different
  operational story from an ordinary sale and should not be invisible in a report.
- `data.payment.reversed_at` — set on `payment.reversed`, which is the negative of a
  completion: book it against `paid_at`, not instead of it.

`payment.superseded` is operational: a replacement code took this one's place because
the payment it replaced settled. Stop offering the old code; recognise nothing.

At most one `payment.completed` and one `payment.reversed` exist per payment id, so a
ledger keyed on `data.payment.id` cannot double-count a sale.

Delivery: any 2xx = ack; else retry with exponential backoff up to 8 times. Dashboard can resend;
"send test" fires a synthetic event. Event `id` is stable across retries (dedupe on it).

## Errors

Every failure uses FastAPI's default envelope — a single `detail` field, not a
`{ "error", "message" }` pair.

```json
{ "detail": "amount_too_low" }
```

Schema validation failures (`422`) nest one entry per bad field:

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "amount"],
      "msg": "Value error, invalid_amount",
      "input": 0.001
    }
  ]
}
```

Codes returned in `detail`: `unauthorized` 401, `quota_exceeded` 402, `whitelabel_not_enabled` 403,
`invalid_request` 400, `invalid_amount` 400, `invalid_status` 400, `payload_too_large` 413,
`amount_too_low`/`amount_too_high` 400, `payment_link_not_found` 404, `payment_link_disabled` 400,
`payment_not_found` 404, `store_not_found` 404, `store_disabled` 400, `method_not_allowed` 405,
`payment_provider_error` 502.

## Quota

- Quota = successful (`paid`) transactions over the account's current 30-day period, pooled
  across stores. Exhausted → create returns `402` until reset/upgrade.
- Body ≤ 5 MB; metadata ≤ 1,000 top-level keys / 1 MB / 8 nesting levels.
- There is **no per-key rate limiting** in the API today: no `429`, no `Retry-After`, and no
  `X-RateLimit-*` headers are emitted. (The `retry_after` values inside `src/chmabapay/workers/`
  are internal job-queue backoff, not an HTTP contract.) Any 429 handling on the client side is
  dead code until this is implemented.

## Signature details (reference implementation sketch)

```python
# compute: HMAC-SHA256(key=endpoint.secret, data=f"{t}.{raw_body}")
# header value: f"t={t},v1={hexdigest}"
# compare via hmac.compare_digest; reject if not fresh (|now-t|>300) or format wrong.
```
