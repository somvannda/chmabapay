# Data model (PostgreSQL)

Conventions: integer PKs internally, opaque public tokens (`payment_id`, `key_id`, `store_id`)
exposed over API. All monetary values stored as **integer minor units (cents)** to avoid float
errors; timestamps UTC `timestamptz`. Money-touching tables append-only where possible + audit logging.

Tenant hierarchy: **account** (hosting platform such as chmaba POS) → **stores** (sub-merchants,
auto-provisioned via API) → **payment_links** (each store's own money destination). API keys and
webhooks default to the **account** level; stores own the money destinations. The platform account
never receives its sub-merchants' funds.

## accounts

Platform tenants — the party that signs in and pays us. Auth via Google OAuth, session cookie.

| column | notes |
| --- | --- |
| id | pk |
| email | unique, from Google |
| name | |
| google_sub | unique |
| status | active / suspended / closed |
| whitelabel_enabled | white-label checkout entitlement; only the platform operator grants it (`PATCH /v1/admin/accounts/{id}`) |
| is_platform_admin | platform owner; unlocks the `/v1/admin/*` surface |
| created_at, updated_at | |

## stores  — a sub-merchant of the account (the account's own customer)

Each store is one merchant a hosting platform onboarded. Its **own** payment link is the money
destination: payments for this store land in **this store owner's** bank account, never the
account's. Stores are created manually in the dashboard **or automatically via the API**
(account-scoped key, `POST /v1/stores`). `public_id` targets the store in payment calls.

| column | notes |
| --- | --- |
| id / public_id | pk / opaque public token (`st_…`) used in API calls |
| account_id | fk → accounts (the hosting platform) |
| created_via | dashboard / api |
| name | merchant name shown on QR (≤ 25 chars KHQR limit) |
| logo_image_url, brand_color, whitelabel_css | white-label branding for hosted checkout; rendered only when the owning account has `whitelabel_enabled` |
| city | KHQR field (≤ 15 chars) |
| support_email | |
| redirect_success_url / redirect_failure_url | success/failure redirects for this store's checkouts |
| telegram_chat_id | optional notify |
| is_internal | true = the platform's own store ("ChmabaPay HQ", where plan fees are collected), not a merchant tenant. Exempt from quota, writes no usage ledger, and reported as platform revenue rather than merchant volume |
| billing_suspended_at | a **platform hold**, set when the account is on a plan smaller than its store count. Orthogonal to `status`, which it never touches: a held store keeps reading `active`, so this flag is the only thing that says it cannot mint a code. It is a flag rather than a `status` value because `disable_store` overwrites `status` and `enable_store` then has to infer what to restore — keeping them apart means a billing release cannot undo an operator's abuse disable, and clearing a hold is one condition instead of a guess |
| status | draft / link_pending / active / disabled |
| created_at, updated_at | |

Lifecycle: `draft` on creation → `link_pending` when a link attach is requested → `active` only
after its destination link verifies. Disabling a store keeps its payment records and stops new
payments. Each store has exactly one active payment link (partial unique index). A held store
(`billing_suspended_at`) is the platform's doing rather than the merchant's, and clears without a
restore step: `settle_invoice` releases the hold, and `POST /v1/stores/{id}/activate` swaps one
back in while holding whichever store makes room.

## payment_links

The store's actual money destination + Bakong connectivity. Attached via API or dashboard; the
destination is whatever the sub-merchant entered (e.g. their ABA Payway link or Bakong id).

| column | notes |
| --- | --- |
| id | pk |
| store_id | fk → stores |
| link_type | 'aba_payway' / 'bakong_id' / … (raw format recorded; parsing rules = Phase 0) |
| raw_link | the link/id exactly as the sub-merchant entered it |
| merchant_account_id | resolved destination (e.g. `name@abaa`) that our KHQR pays to |
| merchant_name | name registered at the bank |
| currency | USD for v1 |
| verification | unverified / verified (must pass before store goes `active`) |
| gateway_provider | which BakongGateway impl this link uses |
| gateway_creds_ref | pointer to encrypted creds in vault (never plaintext in DB) |
| is_sandbox | |
| min_amount_cents / max_amount_cents | per-payment validation |
| status | active / disabled / requires_review |
| created_at, updated_at | |

## api_keys

Keys default to the **account** level (can provision stores and create payments for any of the
account's stores). **Store-scoped** keys are optional (CutLuy parity: act only on one store). A key
never encodes a money destination — the store's payment link does.

| column | notes |
| --- | --- |
| id | pk |
| account_id | fk → accounts (always set) |
| store_id | NULL for account-scoped; set for store-scoped |
| scope | account / store |
| can_manage_stores | bool (account keys only) — allow store provisioning endpoints |
| key_prefix | `ck_live_` + the first 4 characters of the secret (`raw[:12]`), for display |
| key_hash | SHA-256 of full key (full key shown once at creation) |
| mode | `live` only. `new_api_key` mints `ck_live_` and nothing else; the column is kept for the Phase-4 sandbox (decision D-2) |
| status | active / revoked |
| last_used_at | |
| created_at, revoked_at | |

## payments  ← the core table

| column | notes |
| --- | --- |
| id | pk |
| public_id | opaque 22-char token (URL-safe) = API `payment.id` + checkout path |
| store_id | fk → stores — decides the money destination |
| payment_link_id | fk → payment_links (the store's own link; never the account's) |
| amount_cents | int, ≥ 1 |
| currency | 'USD' |
| reference_id | merchant's own id (≤ 255), indexed |
| metadata | jsonb (≤ 1 MB, depth ≤ 8) |
| idempotency_key | unique per store; NULL allowed |
| status | pending / scanned / paid / expired / failed |
| qr_string | generated KHQR payload |
| bill_number | our unique reference embedded in the QR (matching key) |
| expires_at | ≈ created_at + 5 min |
| scanned_at, paid_at, approved_at | timestamps of transitions |
| bakong_ref | transaction reference returned by the rail when paid |
| gateway_status_raw | jsonb, last raw gateway payload (debug/reconcile) |
| matched / orphaned | flags from reconcile sweep |
| created_at | |

Indexes: `(store_id, status, created_at)`, `(store_id, idempotency_key)` unique,
`(status, expires_at)` partial (expiry sweep), `(payment_link_id, created_at desc)`.

## webhook_endpoints  (SHARED at account level by default)

Endpoints live at the **account** level and are shared across all of the account's stores — one
URL + one signing secret for the whole hosting platform (the tenant model). Each event carries the
sub-merchant so the platform routes it. Optionally a store may attach its own endpoint/secret to
receive only its own events (store-level delegation).

| column | notes |
| --- | --- |
| id | pk |
| account_id | fk → accounts |
| store_id | NULL = account-wide (all stores); set = that store only |
| url | |
| secret_key | per-endpoint HMAC secret (encrypted) |
| events | enabled event types |
| status | enabled / disabled |
| created_at, updated_at | |

Delivery set for an event = all account-wide endpoints **plus** any endpoint of the event's store.

## events  (outbox — written atomically with the state change)

| column | notes |
| --- | --- |
| id | uuid pk (stable across retries) |
| account_id | fk → accounts |
| store_id | fk → stores (the sub-merchant the event belongs to) |
| type | payment.completed / payment.expired / payment.superseded / payment.reversed |
| payment_id | fk → payments |
| payload | jsonb, the serialized event body (self-contained; includes the store object) |
| created_at | |

## event_deliveries  (per endpoint × event attempt log)

| column | notes |
| --- | --- |
| id | pk |
| event_id | fk → events |
| endpoint_id | fk → webhook_endpoints |
| status | pending / success / retrying / failed |
| attempts | int |
| next_attempt_at | backoff scheduling |
| last_response_status, last_error | |
| created_at, updated_at | |

Unique `(event_id, endpoint_id)` → each endpoint gets each event exactly once (until success).

## billing / quotas

| table | notes |
| --- | --- |
| plans | code, name, monthly_fee_cents, base_payments_included (the monthly quota), max_stores, max_keys_per_account, max_webhooks_per_account, priority_support, is_public, is_active, plus the pricing-page copy the dashboard and website render from (tagline, features, is_featured) |
| plan_subscriptions | account_id, plan_id, status (trial / active / pending / canceled), started_at, canceled_at, next_billing_at, trial_ends_at. `next_billing_at` is the end of the paid coverage, and it moves **only** when an invoice is settled — the raising sweep never advances it, which is what makes a lapse visible instead of invisible |
| plan_invoices | the claim for one period: subscription_id, period_month (a `YYYY-MM` label derived from `due_at`, not the window itself), period_start, period_end, due_at, status, base_fee_cents, usage_payments_count, overage_payments_count, overage_fee_cents, total_due_cents, paid_at, chmabapay_payment_id. `days_until_due` and `is_overdue` are derived on read rather than stored, because a stored flag needs a writer to keep it true |
| plan_invoices — voiding | `voided_at` + `void_reason` (`downgraded` / `superseded` / `grace_expired` / `pre_lifecycle` / `operator`) mark a withdrawn claim. The unique index on `(subscription_id, period_start)` is **partial** — `WHERE status <> 'void'` — so a void releases its window for re-issue rather than reserving it forever. Pre-migration rows have a NULL `period_start` and do not participate |
| plan_invoice_reminders | what the platform *said*, and when: invoice_id, tier (`due_3` / `due_1` / `due_today` / `overdue_1` / `overdue_3` / `overdue_final`), channel (`in_app`; `email` is Phase B), sent_at, detail, unique across the first three so two replicas and a retry converge on one message. A row is **not** what makes a warning visible — the notice endpoint derives that from `due_at` — so this table is evidence, not mechanism, and deleting it hides nothing from the merchant |
| plan_ledger_entries | one row per movement of a plan's usage: period_month, resource_type, resource_id, amount_cents_delta, total_payments_count, total_volume_cents. Unique on `(period_month, resource_type, resource_id)`, which is what makes the writer's `ON CONFLICT DO NOTHING` mean anything rather than double-counting a retry |

Quota is per **account** (the platform pays us) and is pooled across all of its stores. Store-count
limits are enforced against `stores.account_id`. Paid-plan fees are collected via KHQR to our own
account (dogfooding), so we need our own store/payment-link in our own system — nice bootstrapping story.

A plan smaller than the account's store count leaves the surplus **held** rather than disabled or
deleted: `stores.billing_suspended_at` is set, the merchant re-picks which stores stay live on the
billing page, and a later upgrade clears it. Nothing about detection changes — in-flight payments
still settle, because the sweep reads payments rather than stores.

A plan given up mid-month is not metered against the plan that replaced it. The quota is a
calendar-month count, so the new allowance takes effect at the next month boundary and the store
cap is the immediate lever; otherwise choosing a smaller plan would stop every code until the
first. The deferral is keyed on a *paid* subscription ending this month, so a new Free account is
metered from its first day.

## Audit / ops

| table | notes |
| --- | --- |
| audit_log | operator + account changes (append-only; survives account deletion) |
| orphan_credits | unmatched incoming credits surfaced for manual reconcile |

## Public ↔ internal mapping

- API `payment.id` = `payments.public_id`; API `store` = `stores.public_id`
- API key header = looked up by SHA-256 of presented key; scope decides allowed stores
- Store count limit enforced at create-store; payment-create enforces tx quota first.

## Migration / integrity notes

- A store's money destination is chosen at payment-create time from the store's active
  `payment_link` — never from the account, never the API key's store scope alone (both must resolve
  to the same store).
- Guard transitions with `UPDATE ... WHERE status = <expected>` and check rowcount = 1.
  Terminal-state immutability is app-enforced and tested.
