# Public API spec (v1)

Mirrors CutLuy's developer surface so existing mental models (and SDK examples) map 1:1.
Base URL `https://pay.chmaba.com/api/v1` — the API is served from the same origin as the dashboard,
so there is no separate `api.` host. JSON in/out. Auth: `Authorization: Bearer ck_live_…`.

Only `ck_live_` keys are issued: `POST /api/v1/keys` always mints live mode, and there is no
`ck_test_` issuance path. `POST /api/v1/keys` is also session-cookie only as of 2026-09-23 — a key
must not be able to mint, revoke or rotate keys, or a leaked one could replace itself and
outlive its own revocation (decision D-8). The rest of `/api/v1/me` and `/api/v1/billing/*` are
session-cookie only too, except `GET /api/v1/billing/plans`, which is public. Those surfaces are
in the second section below.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/payments` | Create a payment (KHQR + hosted checkout) |
| GET | `/api/v1/payments/:id` | Fetch current state |
| GET | `/api/v1/payments` | List payments (newest first); whole account, or scoped by `store`/`merchant` |
| POST | `/api/v1/payments/:id/reissue` | Replace a dead code (expired/failed only; a still-live code answers `409 payment_not_expired`) |
| POST | `/api/v1/payments/:id/reverse` | Record a refund of a settled payment |
| GET | `/pay/:id` | **Public** hosted checkout page (no auth) |
| GET | `/pay/:id/qr.svg` | **Public** QR image; `410` once the code is dead |
| GET | `/api/v1/khqr/render.svg` | KHQR SVG renderer (no auth), `ecc`/`scale` params |
| PUT | `/api/v1/stores/:id` | Update a store (alias of `PATCH /api/v1/stores/:id`) |
| POST | `/api/v1/transactions/token/renew` | Request a fresh short-lived Bakong JWT |

## Two surfaces, and why they are separate

The public page is the **integration API** and nothing else: the routes a merchant's backend
calls with `ck_live_…` — stores, payments, reconciliation, webhooks, reports, the hosted
checkout pages and the KHQR helpers.

This document is that plus the **dashboard API**: the routes the platform's own web dashboard
drives with a session cookie. They are not integration surface — an API key is refused on all of
them — and they were removed from the public page on 2026-09-23 (decision D-7), because listing
them advertised a surface an integrator has no credential for.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/keys` | List the account's API keys; the raw key is never returned again |
| POST | `/api/v1/keys` | Create a key — session only, `name` required (1–64 chars), `raw_key` returned once, `403 terms_not_accepted` until the Terms are accepted, capped by the plan (`400 Max API keys (n) reached…`) |
| POST | `/api/v1/keys/:id/revoke` | Revoke a key; any request using it fails from this call on (`404 key_not_found`) |
| POST | `/api/v1/keys/:id/rotate` | Create a replacement and suspend the old key in the same call |
| PATCH | `/api/v1/account` | Update the account name (alias of `PATCH /api/v1/me`) |
| POST | `/api/v1/me/password` | Rotate the password (`401 invalid_password`, `400 password_unchanged`, `409 no_password_set`, `400 password_too_long` past bcrypt's 72 bytes) |
| POST | `/api/v1/me/email` | Move the account's email (`400 email_already_taken`, `409 email_change_requires_password` for a Google-only account) |
| DELETE | `/api/v1/me` | Close and anonymise the account (`400 confirm_email_does_not_match`, `401 invalid_password`, `409 platform_admin_cannot_self_delete`) |
| POST | `/api/v1/me/terms` | Record Terms acceptance (`409 terms_version_superseded`) |
| GET | `/api/v1/billing/subscription` | The current subscription and plan |
| POST | `/api/v1/billing/change-plan` | Change plan; a paid tier is bought, so the plan activates when its invoice is paid (`409 open_invoice_unpaid`, `400 plan_unchanged`, `404 plan_not_found`, `400 plan_not_available`) |
| GET | `/api/v1/billing/invoices` | List plan invoices, filtered by `period_month=YYYY-MM` |
| GET | `/api/v1/billing/invoices/:id/khqr` | Mint a KHQR to settle a plan invoice (`400 invoice_already_paid`, `404 invoice_not_found`, `503 billing_not_open`) |
| GET | `/api/v1/billing/notices` | The most urgent billing notice, or empty when nothing is owed |

`POST /api/v1/transactions/token/renew` is withheld from the public page with the rest of the Bakong
ledger group (decision D-3). The platform-admin routes (`/api/v1/admin/*`) and the dev rail
(`/_dev/*`, mounted only when `ENABLE_DEV_GATEWAY=true`) are internal and are not part of any
public surface.

## POST /api/v1/payments

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
Missing/invalid key → `401 unauthorized`; suspended account → `403 account_suspended`;
account frozen for an unpaid plan invoice → `403 account_restricted`. Quota exhausted →
`402 quota_exceeded`. No link / disabled → `400 payment_link_disabled` / `400 store_disabled`;
held by the platform for billing (`store_billing_suspended`) → `400`. Unknown store →
`404 store_not_found`, or `404 merchant_not_found` when addressed by `merchant=`.

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

## GET /api/v1/payments/:id and GET /api/v1/payments

`GET /api/v1/payments` lists newest first. Scoped by `store` (a store's public id) or `merchant`
(its `external_id`) when either is given; with neither it lists **every** store on the account.
Also accepts `status` (filter), `limit` (default 20, max 100) and `offset` (default 0, the
newest-first index to start at, so a merchant can page past the first page; a negative offset is
a `422` and an absurd one is clamped), and returns under `data`.

Each row carries `store` — which store took the payment — and `paid_at`. It does *not* carry
`metadata`, `qr_string` or `checkout_url`; `metadata` and `qr_string` are on `GET /api/v1/payments/:id`.
`checkout_url` is returned by the calls that mint or replace a code — `POST /api/v1/payments`,
`POST /api/v1/payments/:id/reissue` and `POST /api/v1/payments/:id/reverse` — and by neither read, so a
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
QR image that carry the dead state, and `GET /api/v1/payments/:id` returns `200` with
`status: "expired"` rather than an error.) But **that is not the end of the sale.**
ABA keeps accepting the payment, and on 2026-09-17 one settled nine minutes after its
own expiry event. We keep reconciling for
`detection_window_seconds` (1 h by default) and promote the row when the money turns
up, so `expired → paid` is a normal, expected transition.

Statuses, by what they mean for money:

- `paid` — settled. Counts toward quota, fires `payment.completed`.
- `reversed` — settled, then refunded. `paid_at` is kept and `reversed_at` is added,
  so a report can say "collected, then given back" rather than pretending the sale
  never happened. Set only by `POST /api/v1/payments/:id/reverse`, because ABA gives us
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

## POST /api/v1/payments/:id/reverse

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
| `account_restricted` | 403 | The account is frozen for an unpaid plan invoice. No route is served except the billing allowlist (`GET /api/v1/billing/*`, `POST /api/v1/billing/change-plan`) — including every API key, which is refused wholesale. Settling the invoice, moving to Free, or moving to a smaller paid plan lifts it |
| `quota_exceeded` | 402 | Plan quota reached |
| `whitelabel_not_enabled` | 403 | Branding fields sent without the entitlement |
| `invalid_amount` / `amount_too_low` / `amount_too_high` | 422 (or 400 from the service) | Amount rejected |
| `payment_link_disabled` | 400 | Store has no active link |
| `store_disabled` | 400 | The store is switched off — by the merchant, or by an operator |
| `store_billing_suspended` | 400 | The platform is holding the store: the account is on a plan smaller than its store count. Unlike `store_disabled` this is not the merchant's own switch, and it clears when the plan is settled or a slot is swapped back on the billing page |
| `offline_qr_requires_a_confirmation_source` | 400 | `hosted_qr=false` where nothing can confirm it |
| `payload_too_long` | 400 | `GET /api/v1/khqr/render.svg` only: the payload does not fit a QR code. Not a request-body limit. |
| `invalid_payload` | 400 | `GET /api/v1/khqr/render.svg`: the payload could not be encoded |
| `payment_not_found` / `store_not_found` / `merchant_not_found` | 404 | Not found in this account |
| `payment_not_paid` / `payment_already_reversed` | 409 | Reversal preconditions |
| `email_already_taken` | 400 | Profile email in use |
| `terms_version_superseded` | 409 | Accepted a terms version we no longer publish |
| `bakong_not_configured` | 503 | Bakong ledger endpoints without platform credentials |
| `billing_not_open` | 503 | `GET /api/v1/billing/invoices/{id}/khqr`: the platform's own payment destination is not configured |
| `invalid_password` | 401 | Password proof failed (`POST /api/v1/me/email`, `POST /api/v1/me/password`, `DELETE /api/v1/me`) |
| `no_password_set` / `password_unchanged` | 409 / 400 | `POST /api/v1/me/password` preconditions |
| `confirm_email_does_not_match` | 400 | `DELETE /api/v1/me` typed confirmation did not match |
| `platform_admin_cannot_self_delete` | 409 | `DELETE /api/v1/me` on the console's own account |
| `email_required` / `bakong_error` | 400 | `POST /api/v1/transactions/token/renew` |
| `rate_limited: <rule>` | 429 | Rate limit hit; the value is prefixed, e.g. `rate_limited: auth` |

## Quota

- Quota = successful (`paid`) transactions in the current **calendar month**, pooled across all
  stores on the account. Pending, scanned, expired and failed payments never count, and the
  window is keyed on `paid_at` — the month the money landed, not the month the code was minted.
  Exhausted → create returns `402` until the month rolls over or the plan is upgraded.
- **A plan given up this month is not metered against the plan that replaced it.** The count is a
  calendar-month total, so a merchant who settles 40,000 payments and then moves to a smaller plan
  would be over the new allowance the instant they chose it — a total stop, for the rest of the
  month, arrived at by accident. The new allowance therefore starts at the next calendar-month
  boundary, and until then the store cap is the lever that bites. A merchant who was never on a
  paid plan is metered from their first day: the deferral is keyed on a paid plan being given up,
  not on a plan starting.
  `GET /api/v1/billing/subscription` reports it as `quota_deferred_until` — the instant the new
  allowance starts being enforced, `null` when it already is. It exists for the portal: usage is
  counted against the plan *in force*, so a mid-month downgrade reads over-limit while every code
  still mints, and this is the only way a client can explain that rather than contradict it.
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

## Billing and plan renewal

Plan fees are **prepaid** and collected by KHQR, so there is no stored credential to charge and
nothing renews itself: the merchant has to be asked, and has to pay. The whole sequence follows
from that, and it is written down here because it reaches into the API an integrator's own systems
call — a frozen account is refused, and a capped store stops minting.

Timeline for one 30-day period, where `D` is the day the period ends (the invoice's `due_at`):

| When | What happens |
| --- | --- |
| `D-7` | The renewal invoice is raised for the next period, and the first notice appears. |
| `D-3`, `D-1`, `D` | Further notices, each more urgent than the last. |
| `D+1`, `D+3`, `D+6` | Overdue notices. |
| `D+7` | Grace ends and the account is **frozen** (`status: restricted`): no store can mint a code, the dashboard is read-only, and every API key is refused with `403 account_restricted`. Nothing is deleted and not one store row is written. |
| on payment | The account is unfrozen and everything works again, because only its status was ever changed. |

- **Three ways out of a freeze**, all on the billing page: settle the invoice; move to Free (the
  unpaid invoice is withdrawn); or move to a smaller paid plan (that invoice is withdrawn and the
  new plan starts when its own invoice is paid). Each ends with no open invoice and an active
  account.
- **Paying late bills from the day you paid.** An invoice settled *after* the freeze is voided
  rather than marked paid — a `paid` invoice always describes a window the merchant actually
  received — and a new invoice covers `[paid_at, paid_at + 30 days)`.
- **A smaller plan caps the stores immediately.** Stores over the new allowance are held
  (`billing_suspended_at` on the store object) and a new code against one answers
  `400 store_billing_suspended`, while a payment already in a customer's hand still settles.
  `POST /api/v1/stores/{public_id}/activate` swaps a held store back in, holding whichever store makes
  room, so the count never changes.
- Only `GET /api/v1/billing/plans` is public. The rest of `/api/v1/billing/*` is session-cookie only, and
  while an account is frozen those routes plus reads are the *only* ones served.

### The plan invoice object

| field | notes |
| --- | --- |
| id | used by `POST /api/v1/billing/invoices/{id}/khqr` to mint a payable code |
| period_month | `YYYY-MM` label derived from `due_at` — a label, not the window |
| period_start, period_end | the window this invoice is a claim for |
| due_at | when it was expected; `null` only on pre-2026 rows |
| days_until_due, is_overdue | derived from `due_at` on every read, never stored |
| status | `open` / `paid` / `waived` / `credited` / `void` |
| base_fee_cents, overage_fee_cents, total_due_cents | the amount, in cents, with `_formatted` string twins |
| paid_at | written only by a real settlement; a waiver and a credit carry none |
| voided_at, void_reason | set when a claim was withdrawn: `downgraded`, `superseded`, `grace_expired`, `pre_lifecycle`, `operator` |

`void` is unpaid-and-abandoned rather than settled: the window it named is released and can be
billed again, which is why voiding is not how a debt is forgiven. A voided invoice is still
**payable** — settling one is how a merchant who let a period lapse buys it back.

### GET /api/v1/billing/notices

At most one notice, most urgent first. `level` is `info` / `warning` / `critical`; `state` is
`issuance`, `due_3`, `due_1`, `due_today`, `overdue_1`, `overdue_3`, `overdue_final` or `frozen`,
with `frozen` outranking every tier and describing the account rather than the invoice — an
operator can freeze an account with no invoice at all, and then the notice carries no amount.
`title`, `body` and `action_label` are rendered server-side, and `action_url` deep-links to the
billing page with the invoice preselected. `dismissible` is a policy the server owns, so a tier
that must not be dismissed stays visible without a client release. Empty when nothing is owed.

The state is derived from `due_at` on every read, never from the platform's own record of what it
said, so a worker outage cannot hide a warning the merchant was owed — the endpoint itself says
nothing about that table.

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
| `payment_create` | API key | `POST /api/v1/payments`, `POST /api/v1/payments/{id}/reissue` — the calls that mint an ABA QR |
| `api` | API key | everything else under `/api/v1/` |
| `khqr` | IP | `/api/v1/khqr/*` (unauthenticated by design) |
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
