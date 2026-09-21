# Public API spec (v1)

Mirrors CutLuy's developer surface so existing mental models (and SDK examples) map 1:1.
Base URL `https://pay.chmaba.com/v1` — the API is served from the same origin as the dashboard,
so there is no separate `api.` host. JSON in/out. Auth: `Authorization: Bearer ck_live_…`.

Only `ck_live_` keys are issued: `POST /v1/keys` always mints live mode, and there is no
`ck_test_` issuance path. Most `/v1/me` and `/v1/billing/*` endpoints are session-cookie
only and an API key is not accepted there; the one exception is `GET /v1/billing/plans`,
which is public. Those are marked below.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/payments` | Create a payment (KHQR + hosted checkout) |
| GET | `/v1/payments/:id` | Fetch current state |
| GET | `/v1/payments` | List payments (newest first); whole account, or scoped by `store`/`merchant` |
| POST | `/v1/payments/:id/reissue` | Replace a dead code (expired/failed only; a still-live code answers `409 payment_not_expired`) |
| POST | `/v1/payments/:id/reverse` | Record a refund of a settled payment |
| GET | `/pay/:id` | **Public** hosted checkout page (no auth) |
| GET | `/pay/:id/qr.svg` | **Public** QR image; `410` once the code is dead |
| GET | `/v1/khqr/render.svg` | KHQR SVG renderer (no auth), `ecc`/`scale` params |
| PUT | `/v1/stores/:id` | Update a store (alias of `PATCH /v1/stores/:id`) |
| PATCH | `/v1/account` | Update the account name (alias of `PATCH /v1/me`) |
| POST | `/v1/me/password` | Rotate the account password (session only) |
| DELETE | `/v1/me` | Close and anonymise the account (session only) |
| POST | `/v1/transactions/token/renew` | Request a fresh short-lived Bakong JWT |

Keys, stores, webhooks, billing, reports and the account profile are all real HTTP endpoints on
this same API (`/v1/keys`, `/v1/stores`, `/v1/webhooks`, `/v1/billing`, `/v1/reports`, `/v1/me`);
they are documented on the public API page rather than repeated here. The five operations added
to the table above are the ones that page had omitted: `PUT /v1/stores/:id` and `PATCH /v1/account`
are write aliases, `POST /v1/me/password` rotates the password (`401 invalid_password` when the
current one is wrong, `400 password_unchanged`, `409 no_password_set`), `DELETE /v1/me` closes and
anonymises the account (`400 confirm_email_does_not_match`, `401 invalid_password`,
`409 platform_admin_cannot_self_delete`), and `POST /v1/transactions/token/renew` asks NBC for a
Bakong JWT (`400 email_required`, `400 bakong_error`). The platform-admin routes
(`/v1/admin/*`) and the dev rail (`/_dev/*`, mounted only when `ENABLE_DEV_GATEWAY=true`) are
internal and are not part of the public surface.

## POST /v1/payments

Request body:

```json
{ "amount": 1.50, "reference_id": "order_1024", "metadata": {}, "idempotency_key": "retry-safe-key" }
```

`reference_id`, `metadata`, `idempotency_key` optional. Also required to address the store:
`store=<store public id>` (account-scoped keys) or `merchant=<store external_id>`. `hosted_qr`
defaults to on and is what makes ABA issue a payable code; `hosted_qr=false` builds the code
offline and is refused with `400 offline_qr_requires_a_confirmation_source` unless the
deployment can confirm it. `amount` is a decimal in the store link's currency, not necessarily
USD.

Idempotency is the **body field** `idempotency_key` only — the `Idempotency-Key` header is not
read. Retry with the same value to get the same payment back.

Validation: `amount ≥ 0.01`, ≤ link max, ≤ 2 decimals — `amount_too_low` / `amount_too_high`
(`400` from the service, `422` from the request schema) and `invalid_amount` (`422`).
Missing/invalid key → `401 unauthorized`; suspended account → `403 account_suspended`.
Quota exhausted → `402 quota_exceeded`. No link / disabled → `400 payment_link_disabled` /
`400 store_disabled`. Unknown store → `404 store_not_found`, or `404 merchant_not_found` when
addressed by `merchant=`.

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

`GET /v1/payments` lists newest first. Scoped by `store` (a store's public id) or `merchant`
(its `external_id`) when either is given; with neither it lists **every** store on the account.
Also accepts `status` (filter), `limit` (default 20, max 100) and `offset` (default 0, the
newest-first index to start at, so a merchant can page past the first page; a negative offset is
a `422` and an absurd one is clamped), and returns under `data`.

Each row carries `store` — which store took the payment — and `paid_at`. It does *not* carry
`metadata`, `qr_string` or `checkout_url`; `metadata` and `qr_string` are on `GET /v1/payments/:id`.
`checkout_url` is returned by the calls that mint or replace a code — `POST /v1/payments`,
`POST /v1/payments/:id/reissue` and `POST /v1/payments/:id/reverse` — and by neither read, so a
payment fetched by id has to be turned into a link as `/pay/{id}` on your own checkout origin.

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
closes the QR image stops being served — `GET /pay/:id/qr.svg` answers `410` — and
`payment.expired` fires. (`GET /pay/:id` still renders; it is the JSON status and the
QR image that carry the dead state, and `GET /v1/payments/:id` returns `200` with
`status: "expired"` rather than an error.) But **that is not the end of the sale.**
ABA keeps accepting the payment, and on 2026-09-17 one settled nine minutes after its
own expiry event. We keep reconciling for
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
- `superseded` — this is the **replacement** code, withdrawn because the original it
  replaced then settled. Its QR is withdrawn (`410`) so one sale cannot be paid twice.
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

- `X-ChmabaPay-Event`: `payment.completed | payment.expired |
  payment.superseded | payment.reversed`
  (a development-only `payment.scanned` is emitted by the local gateway and never in
  production; `payment.failed` has no producer)
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

`payment.superseded` is operational: a replacement code was withdrawn because the
payment it replaced then settled. Stop offering that replacement code; recognise nothing.

At most one `payment.completed` and one `payment.reversed` exist per payment id, so a
ledger keyed on `data.payment.id` cannot double-count a sale.

Delivery: any 2xx = ack; else retry with exponential backoff up to 8 times. Dashboard can resend;
"send test" fires a synthetic event. Event `id` is stable across retries (dedupe on it).

## Errors

Every failure uses FastAPI's default envelope — a single `detail` field, not a
`{ "error", "message" }` pair. The one exception is `429`, which carries `limit`,
`window_seconds` and `retry_after` next to `detail` (see “Rate limits”).

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

Codes returned in `detail` (verified against `src/`):

| Code | Status | Meaning |
| --- | --- | --- |
| `unauthorized` | 401 | Missing or invalid key |
| `invalid_session` | 401 | Cookie-only endpoint called without a valid session |
| `account_suspended` | 403 | The account is suspended |
| `quota_exceeded` | 402 | Plan quota reached |
| `whitelabel_not_enabled` | 403 | Branding fields sent without the entitlement |
| `invalid_amount` / `amount_too_low` / `amount_too_high` | 422 (or 400 from the service) | Amount rejected |
| `payment_link_disabled` / `store_disabled` | 400 | Store has no link, or is disabled |
| `offline_qr_requires_a_confirmation_source` | 400 | `hosted_qr=false` where nothing can confirm it |
| `payload_too_long` | 400 | `GET /v1/khqr/render.svg` only: the payload does not fit a QR code. Not a request-body limit. |
| `invalid_payload` | 400 | `GET /v1/khqr/render.svg`: the payload could not be encoded |
| `payment_not_found` / `store_not_found` / `merchant_not_found` | 404 | Not found in this account |
| `payment_not_paid` / `payment_already_reversed` | 409 | Reversal preconditions |
| `email_already_taken` | 400 | Profile email in use |
| `terms_version_superseded` | 409 | Accepted a terms version we no longer publish |
| `bakong_not_configured` | 503 | Bakong ledger endpoints without platform credentials |
| `billing_not_open` | 503 | `GET /v1/billing/invoices/{id}/khqr`: the platform's own payment destination is not configured |
| `invalid_password` | 401 | Password proof failed (`POST /v1/me/email`, `POST /v1/me/password`, `DELETE /v1/me`) |
| `no_password_set` / `password_unchanged` | 409 / 400 | `POST /v1/me/password` preconditions |
| `confirm_email_does_not_match` | 400 | `DELETE /v1/me` typed confirmation did not match |
| `platform_admin_cannot_self_delete` | 409 | `DELETE /v1/me` on the console's own account |
| `email_required` / `bakong_error` | 400 | `POST /v1/transactions/token/renew` |
| `rate_limited: <rule>` | 429 | Rate limit hit; the value is prefixed, e.g. `rate_limited: auth` |

## Quota

- Quota = successful (`paid`) transactions in the current **calendar month**, pooled across all
  stores on the account. Pending, scanned, expired and failed payments never count, and the
  window is keyed on `paid_at` — the month the money landed, not the month the code was minted.
  Exhausted → create returns `402` until the month rolls over or the plan is upgraded.
- A store can be marked **internal** on the store object (`is_internal`), which means it belongs
  to the platform itself rather than a merchant tenant — "ChmabaPay HQ" is the one such store,
  and it is where plan fees are collected. An internal store's payments are exempt from quota,
  count no usage against the owning account, and are reported as platform revenue rather than
  merchant volume. Merchant stores are `is_internal: false`.
- `metadata` is **not** validated: no key count, byte size or nesting depth is enforced, so keep
  it small yourself. The only hard ceiling on a request body is the edge's
  `client_max_body_size`, which is nginx's 1 MB default — nothing in this repository raises it.
  (The "5 MB / 1,000 keys / 8 levels" figures previously written here were a spec that was never
  implemented.)

## Rate limits

Limits are real and enforced per key or per IP, in a 60-second window. A rejected request
returns `429` with a JSON body and three headers — `Retry-After`, `X-RateLimit-Limit`,
`X-RateLimit-Remaining` — all of which are CORS-exposed so browser clients can read them:

```json
{ "detail": "rate_limited: auth", "limit": 20, "window_seconds": 60, "retry_after": 12 }
```

Rules, by route (each limit is configurable per minute — see `config.py`):

| Rule | Identity | Applies to |
| --- | --- | --- |
| `payment_create` | API key | `POST /v1/payments`, `POST /v1/payments/{id}/reissue` — the calls that mint an ABA QR |
| `api` | API key | everything else under `/v1/` |
| `khqr` | IP | `/v1/khqr/*` (unauthenticated by design) |
| `auth` | IP | `/auth/*`, `/api/v1/auth/*`, `/user/google/auth/*` |
| `checkout` | IP | `/pay/*` |

Counters live in the API process, so with N replicas the effective allowance is N times the
configured value. Treat the numbers as a ceiling, not a guarantee.

## Signature details (reference implementation sketch)

```python
# compute: HMAC-SHA256(key=endpoint.secret, data=f"{t}.{raw_body}")
# header value: f"t={t},v1={hexdigest}"
# compare via hmac.compare_digest; reject if not fresh (|now-t|>300) or format wrong.
```
