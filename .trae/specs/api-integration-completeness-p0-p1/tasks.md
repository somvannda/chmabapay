# ChmabaPay - API Integration Completeness Implementation Plan

## Task 1: Admin KYC Router (Queue List + Approve + Reject)
- **Status**: `superseded`
- **Verified**: KYC was dropped entirely by `supabase/migrations/6-drop-kyc.sql`, which removes `accounts.kyc_status`, `kyc_approved_at`, `kyc_reject_reason` and `kyc_live_blocked` plus `plans.kyc_required_for_live`; the word `kyc` no longer appears anywhere under `src/`, and `routers/admin.py` today is a different operator console (`/v1/admin/accounts`, `/v1/admin/plans`, `/v1/admin/invoices`, `/v1/admin/payments`, `/v1/admin/hq-store`) with no KYC queue/approve/reject routes.
- **Priority**: high
- **Depends On**: None
- **Description**:
  - Create new file `src/chmabapay/routers/admin.py` with prefix `/v1/admin`.
  - Implement hybrid auth helper (session OR Bearer ck_ key) that ALSO checks `session_account.is_platform_admin == True`; returns 403 `forbidden` otherwise.
  - Route `GET /kyc/pending`: select Account where `kyc_status IN ('submitted','reviewing')`, return paginated list with KYC fields + submitted_at computed.
  - Route `POST /kyc/{account_id}/approve`: load target account, set kyc_status='approved', kyc_live_blocked=False, kyc_approved_at=now UTC, updated_at=now. Write AuditLog: action='kyc.approved', target_type='Account', target_id=account_id, admin_account_id=admin.id, details={}. Return updated account profile subset.
  - Route `POST /kyc/{account_id}/reject`: body JSON reject_reason string (max 500 chars, min 10). Set kyc_status='rejected', save reject_reason, keep kyc_live_blocked=True, updated_at=now. AuditLog action='kyc.rejected' details include {reject_reason}.
  - Include in `main.py` `app.include_router(admin.router)` near the other routers (after account before billing or at end).
- **Acceptance Criteria Addressed**: AC-3, AC-4, AC-5, AC-17
- **Test Requirements**:
  - `rule` TR-1.1: Non-admin session GET /v1/admin/kyc/pending → HTTP 403. Evidence: pytest httpx test.
  - `rule` TR-1.2: Approve flow: submit Business KYC → login admin → POST approve → target kyc_status/approved_at/kyc_live_blocked cols flipped; AuditLog row exists. Evidence: pytest assertions on DB.
  - `rule` TR-1.3: Reject flow: submit Business KYC → admin POST reject with reason → kyc_status=rejected, blocked=True, reason saved, AuditLog row. Evidence: pytest.
- **Notes**: Reuse existing AuditLog model from `models.py` (M1 already implemented per prior spec). Test helper to create an `is_platform_admin=True` account fixture.

---

## Task 2: Sub-Merchant Model + Platform Router (CRUD)
- **Status**: `superseded`
- **Verified**: `supabase/migrations/4-merge-sub-merchants-into-stores.sql` folds the whole sub-merchant capability into stores — it adds `stores.external_id` and `stores.whitelabel_css`, folds `sub_merchants` rows into the store each one owned, then `DROP TABLE IF EXISTS sub_merchants` and drops `accounts.saas_sub_merchants_enabled` and `plans.max_sub_merchants` / `allow_saas_sub_merchants`; `src/chmabapay/routers/platform.py` no longer exists and there is no `SubMerchant` model in `src/chmabapay/models.py`.
- **Priority**: high
- **Depends On**: None
- **Description**:
  - **Model layer**: Add `SubMerchant` SQLAlchemy model class to `src/chmabapay/models.py` per BRD §8.2 spec: id PK, public_id (sm_… 20+ urlsafe chars unique index), account_id FK accounts.id, external_id (Saas partner id, indexed, unique constraint per (account_id, external_id)), display_name, status default='active', shadow_store_id FK stores.id UNIQUE (1:1), link_type enum (aba_payway_link|bakong_id|bank_account), raw_link nullable, merchant_account_id nullable (resolved bakong_id Tag 30.01), merchant_name nullable, payway_client_id nullable, bank_code nullable, account_number nullable, resolved_bakong_id nullable, redirect_success_url nullable, redirect_failure_url nullable, support_email nullable, whitelabel_css nullable (TEXT), total_payments_count default=0, total_volume_cents default=0, created_at + updated_at datetime.
  - **DB apply**: Ensure `create_all` in `db.py` includes SubMerchant table if not done automatically by SQLAlchemy metadata.
  - **Public id generator**: Helper `new_sub_merchant_id()` in security.py (or inline) generating `sm_` prefix + 16 urlsafe chars consistent with `st_`, `ck_`, `pay_` prefix patterns.
  - **Router file**: Create `src/chmabapay/routers/platform.py` prefix `/v1/platform`. Hybrid auth: ck_ Bearer OR session. Reject st_ scoped keys with 403. Plan gate: `Account.saas_sub_merchants_enabled == True` via active plan (same `_get_active_plan` helper pattern from `keys.py` lines 97–111). If Growth or Starter → 403.
  - **Cap enforcement**: `Plan.max_sub_merchants` count check on create; if cap hit → 402 detail="Sub-Merchant cap reached ({N} on {plan_code} plan). Upgrade to Enterprise for unlimited."
  - **Route CRUD**:
    - `POST /sub-merchants`: Create. Payload fields: external_id (str), display_name (str), khqr_config (dict: link_type, raw_link?, bakong_id?, bank_code?, account_number?), redirect_success_url?, redirect_failure_url?, support_email?, whitelabel_css?. Behavior: (a) if external_id already exists for same account → return existing (idempotent replay 200 not 201). (b) create shadow Store: name=display_name+" (shadow)", status=ACTIVE, under same account_id. (c) create PaymentLink for that Store: depending on link_type, save raw_link/payway_client_id etc, status=ACTIVE. (d) resolve bakong_id (reuse derive_bakong_id helper from khqr.py if Style B bank account chosen — import cross-module carefully). (e) Return 201 or 200: {sub_merchant_id, external_id, resolved_bakong_id, tag30_00 derived from bank profile, shadow_store_id (hide in production? — no, return it per BRD for debugging), status}.
    - `GET /sub-merchants`: list, optional query ?status=active|disabled, ?external_id=, ?page=1 ?per_page=50 max 100.
    - `GET /sub-merchants/{public_id}`: detail (include counters total_payments_count + total_volume_cents formatted).
    - `PATCH /sub-merchants/{public_id}`: edit display_name, redirects, whitelabel_css, support_email, khqr_config fields. If khqr_config changed → update shadow Store's PaymentLink accordingly.
    - `POST /sub-merchants/{public_id}/disable`: set status=disabled; Store.status=inactive. Return SubMerchant object.
  - **Include**: Add `app.include_router(platform.router)` in `main.py`.
- **Acceptance Criteria Addressed**: AC-6, AC-7, AC-8
- **Test Requirements**:
  - `rule` TR-2.1: Scale account POST Sub-Merchant → 201; 2nd POST same external_id → 200 same id. Evidence: pytest.
  - `rule` TR-2.2: Growth account try → 403 with "Upgrade from Growth" detail; Individual Starter → 403. Evidence: pytest 2 requests.
  - `rule` TR-2.3: Cap enforcement with mock max=2. Create 2 OK; 3rd → 402 cap detail string. Evidence: pytest.
  - `rule` TR-2.4: Cross-account: Account A tries GET /sub-merchants/{SM owned by B.public_id} → 404. Scope enforcement. Evidence: pytest.
- **Notes**: Shadow stores should be marked conceptually. Simplest approach: add nullable `sub_merchant_id` FK to Store model pointing back OR just rely on the UNIQUE from SubMerchant.shadow_store_id side for lookups. Choose UNIQUE only without new Store FK to minimize model churn if possible — payment resolver joins via SubMerchant table.

---

## Task 3: Payment sub_merchant_id Resolver + Sub-Merchant Paid Counters
- **Status**: `superseded`
- **Verified**: the shadow-store resolver was replaced by the merged store model — `routers/payments.py:148 resolve_target()` now branches on `body.merchant` → `Store.external_id` and `body.store` (no `sub_merchant_id` field or branch exists anywhere under `src/`), and per-merchant paid metering moved to `PlanLedgerEntry` (`services/billing.py:166 period_usage`, written in `services/payments.py`), both following `supabase/migrations/4-merge-sub-merchants-into-stores.sql`.
- **Priority**: high
- **Depends On**: Task 2 (SubMerchant model must exist)
- **Description**:
  - **Payment create schema**: Add optional `sub_merchant_id: str | None = None` to `PaymentCreate` pydantic model in `schemas.py` or the inline class in payments.py (find where body is defined).
  - **Resolver in `payments.py` `resolve_target()`**: Add STEP 2 branch from BRD §4.2. Before the `elif body.store_public_id:` check: if body.sub_merchant_id is set → (a) require ctx.api_key.scope == account scope (if store-scope key 403); (b) require ctx.account.saas_sub_merchants_enabled (403 if not); (c) select SubMerchant by public_id + account_id; not found → 404 sub_merchant_not_found; (d) target store = SubMerchant.shadow_store_id. Fall through if not set.
  - **PaymentOut schema**: Add optional `sub_merchant_id: str | None` + `external_id: str | None` fields. Populate in `payment_out()` helper: after resolving payment.store_id, do a quick SubMerchant query "where shadow_store_id = payment.store_id LIMIT 1". Null if not a shadow payment.
  - **mark_paid counter**: In `services.payments.mark_paid()` (find it — grep for mark_paid definition), after the existing PlanLedgerEntry counter UPDATE block: run a 2nd atomic UPDATE `SubMerchant` `total_payments_count = total_payments_count + 1`, `total_volume_cents = total_volume_cents + :amt_cents` `WHERE shadow_store_id = payment.store_id AND status='active'`. Execute even if no row matches (no-op safe — zero rows updated = OK).
- **Acceptance Criteria Addressed**: AC-9, AC-10
- **Test Requirements**:
  - `rule` TR-3.1: Create payment with `sub_merchant_id=SM.public_id` → Payment.store_id == SM.shadow_store_id; PaymentOut JSON contains sub_merchant_id field. Evidence: pytest.
  - `rule` TR-3.2: mark_paid → SubMerchant total_payments_count += 1; total_volume_cents += amount (1000 cents → $10 → +1000). Evidence: SQL SELECT assertions.
  - `rule` TR-3.3: Concurrency safe. 2 concurrent async mark_paid for 2 different payments (2000 + 3000 cents) to same SubMerchant → counters 2 and 5000 exactly. Evidence: asyncio.gather test with both.
- **Notes**: If mark_paid lives in a different module, cross-reference correct file. SubMerchant import from models OK circular? Use string reference or TYPE_CHECKING import guard if needed.

---

## Task 4: Billing Invoices List + Self-Pay KHQR via Own Gateway
- **Status**: `complete`
- **Verified**: `routers/billing.py:491 list_invoices` serves `GET /v1/billing/invoices` (session-only, `?period_month=`, ordered `period_month` DESC) and `routers/billing.py:556 get_invoice_khqr` serves `GET /v1/billing/invoices/{invoice_id}/khqr` (201, mints the payment via `_invoice_payment` → `services.payments.create_payment` on the resolved HQ store), pinned by `tests/test_billing_invoices.py::test_the_same_invoice_gives_the_same_payment_twice`.
- **Priority**: high
- **Depends On**: None (can work even if PlanInvoice model needs to be added; it was in M1)
- **Description**:
  - **Verify PlanInvoice model**: Check `models.py` for `PlanInvoice`. If missing any enum: add status: draft|issued|paid|void with default='draft'. Ensure cols: period_month (str "YYYY-MM"), base_fee_cents, usage_payments_count, overage_payments_count, overage_fee_cents, total_due_cents, paid_at nullable, chmabapay_payment_id nullable FK payments.id.
  - **HQ store helper**: In billing router module, helper `_get_hq_store(session)` returns a Store row used by chmabapay to receive subscription self-payment: first, if env var `CHMABAPAY_HQ_STORE_ID` is set → load Store by that id; else → find account with `is_platform_admin=True` → pick first Store under that account. If none → raise HTTP 500 detail="platform_hq_store_not_configured". Don't block startup; only raise when endpoint hit.
  - **Extend `routers/billing.py`** with 2 new endpoints:
    - `GET /invoices`: list, query optional `?period_month=2026-09`. Filter by account via session (account from hybrid or session-only auth since invoices are account-level). Order by period_month DESC. Response: list of objects {id, period_month, status, base_fee_cents_formatted, total_due_cents_formatted, paid_at, issued_at, chmabapay_payment_id_ref}.
    - `GET /invoices/{invoice_id}/khqr`: Load invoice by id + account ownership check. If already paid (status='paid') return 400 detail="invoice_already_paid". Call the internal `services.payments.create_payment(session, store=hq_store, amount_cents=invoice.total_due_cents, reference_id=f"INV-{invoice.id}-{invoice.period_month}", metadata={"source":"billing_invoice","invoice_id":invoice.id}, ...)` — exactly the same call flow as the payments router. Save invoice.chmabapay_payment_id = payment.id. Update invoice.status='issued' if it was 'draft' (auto-issue when KHQR generated). Build full checkout_url. Return 201 {payment_id, qr_string: payment.qr_string, checkout_url: fully qualified URL (using settings app_host or base from request URL host), expires_at: payment.expires_at}.
- **Acceptance Criteria Addressed**: AC-11
- **Test Requirements**:
  - `rule` TR-4.1: Seed 1 PlanInvoice row for account. GET /invoices → 200 list len 1 with correct period_month/status/total_due_cents. Evidence: pytest.
  - `rule` TR-4.2: GET /invoices/{id}/khqr → 201 returns qr_string starts with "000201" (valid KHQR payload header), new Payment row created targeting HQ store, amount_cents matches invoice total_due_cents. Evidence: SQL verify payment store + amount.
- **Notes**: Keep auth consistent — invoices use `get_current_session_account` session-only auth (finance pages always session not keyed). No need to expose via ck_ key for now.

---

## Task 5: Reports Router (CSV + JSON Payments Export, Plan-Gated)
- **Status**: `partial`
- **Verified**: both endpoints exist today — `routers/reports.py:131 export_payments_csv` (`GET /v1/reports/payments.csv`, streaming, exercised by `tests/test_settlement_lifecycle.py`) and `routers/reports.py:199 export_payments_json` (`GET /v1/reports/payments.json` with the totals summary) — but the plan gate half was deliberately removed by `alembic/versions/0010_drop_csv_export_gate.py`, which drops `plans.csv_export_enabled` because the 403 was unreachable (every plan, Free included, had it true).
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - **New file**: `src/chmabapay/routers/reports.py` prefix `/v1/reports`.
  - **Hybrid auth**: Exactly same pattern as keys/webhooks (session OR ck_ Bearer). Scope for store-scope keys → filter to that store only automatically (no privilege escalation to cross-store).
  - **Plan gate helper**: `_ensure_csv_export_allowed(session, account)` → get active plan for account; if plan.csv_export_enabled == False OR Starter → raise 403 detail="CSV exports are not available on the Starter plan. Upgrade to Growth to unlock.". Call this helper inside both CSV and JSON endpoints? Actually JSON report should be accessible to all but CSV is Starter-gated. Only gate the CSV endpoint with 403. JSON is always 200 if authed.
  - **Shared filter parser**: Parse query params: `from` (ISO date), `to` (ISO date), `store_id` (store public_id str), `sub_merchant_id` (sm_ public_id), `statuses` comma-separated list (pending,paid,expired,failed,scanned). Build SQL where clause accordingly. Join Store for owner account scoping.
  - **CSV endpoint**: `GET /payments.csv`. Return `StreamingResponse` with media_type='text/csv', header Content-Disposition inline; filename="payments_{from}_{to}.csv". Header row: "public_id,status,amount,currency,reference_id,store_public_id,store_name,sub_merchant_public_id,external_id,qr_md5_8,paid_at,created_at,approved_at". Iterate through SQL results, csv.writer write each row. No pagination for CSV — stream all.
  - **JSON endpoint**: `GET /payments.json`. Same filters. Pagination: query `?page=1 default, ?per_page=20 default, max 100`. Response shape: {data: [..., each row same as CSV fields as dict], summary: {count: N rows in this page, total_matching_rows (int), total_amount_cents_sum (sum of all matching rows, not just page)}, pagination: {page, per_page, total_pages, total_rows}}.
- **Acceptance Criteria Addressed**: AC-12, AC-13
- **Test Requirements**:
  - `rule` TR-5.1: Starter account GET /v1/reports/payments.csv → 403 with exact detail substring "Upgrade to Growth". Evidence: pytest.
  - `rule` TR-5.2: Growth account GET CSV → 200, Content-Type text/csv, body starts with header "public_id,status,amount,...". Evidence: pytest httpx Content-Type + body startswith.
  - `rule` TR-5.3: JSON endpoint 3 paid payments ($5, $10, $2) → summary.count=3, summary.total_amount_cents=1700. Evidence: pytest JSON assertions.
- **Notes**: Sub-amount sum: sum payment.amount_cents for all matching (not just page) for totals. Do 2 queries: list + aggregate SUM + COUNT for totals. Always safe.

---

## Task 6: Webhook Deliveries Listing Endpoint
- **Status**: `complete`
- **Verified**: `routers/webhooks.py:377 list_webhook_deliveries` serves `GET /v1/webhooks/{endpoint_id}/deliveries`, returning `WebhookDeliveryOut` rows (delivery_id, event_type, http_status, attempt_count, response_body_preview truncated to 500 chars, created_at, completed_at) read from `models.EventDelivery` (`models.py:285`, columns `last_response_status` / `attempts` / `last_error`) joined to `Event`, newest first with `?limit=` (default 200).
- **Priority**: medium
- **Depends On**: None
- **Description**:
  - **Check WebhookDelivery model**: If a WebhookDelivery or EventDelivery model exists in models.py → use it. If only Event table: derive from Event joined to endpoint — try to return best-effort fields. For minimal implementation, create a dedicated endpoint if deliveries not tracked yet: for now return events list scoped to endpoint_id + created_at + event_type, set http_status=null, response_body_preview=null, attempt_count=1 and note in the endpoint description: "delivery-level HTTP status tracking coming soon; current endpoint returns event-level attempts".
  - **Add endpoint** to existing `routers/webhooks.py`: right after `POST /{endpoint_id}/test` add:
    - `GET /{endpoint_id}/deliveries`: Owner scope check matches existing `_load_endpoint` helper. Query: default limit=200, max=500 via `?limit=`; `?page=` optional offset pagination. Sort by created_at DESC.
    - Return shape: list of objects {event_id (or delivery_id), event_type, http_status: int|null, attempt_count, response_body_preview: str|null (max 500 chars truncate with …), created_at, completed_at: datetime|null}.
- **Acceptance Criteria Addressed**: AC-14
- **Test Requirements**:
  - `rule` TR-6.1: Endpoint exists (GET 200 for owner), cross-account returns 404. Evidence: pytest.
  - `rule` TR-6.2: Seed 3 events or delivery rows; list len=3 ordered desc. Evidence: pytest.

---

## Task 7: Landing API Docs Page Full Rewrite (Correct Paths + 6 New Groups + Getting Started Curl Panels)
- **Status**: `partial`
- **Verified**: the rewrite landed and holds today in `web/landing/app/api/docs/page.tsx` — a corrected `endpointGroups` array (e.g. `GET/POST /v1/keys`, `/v1/khqr/from-link`, `/v1/reports/payments.csv`; no `/v1/auth/api-keys` string) plus a `quickStartPanels` component with 6 curl/Node.js blocks — but the spec's "Admin (Platform Owner)" and "Platform (SaaS Sub-Merchants)" groups (and the raw `/_dev` rail group) are gone, because those routers were removed by `supabase/migrations/6-drop-kyc.sql` and `supabase/migrations/4-merge-sub-merchants-into-stores.sql`.
- **Priority**: high
- **Depends On**: None (but we write docs groups based on actual routes after backend tasks 1-6 so route list is accurate; run docs edit last to reflect reality)
- **Description**:
  - **Rewite `endpointGroups` array** in `web/landing/app/api/docs/page.tsx` (lines 28-66):
    - Rename old "Authentication" to "API Keys" with correct prefix `/v1/keys`. Correct 3 rows: `GET /v1/keys` "List current keys", `POST /v1/keys` "Create a key (account or store scope, live/test mode)", `POST /v1/keys/{key_id}/revoke` "Revoke a key", `POST /v1/keys/{key_id}/rotate` "Rotate (create new + deprecate old)". (4 rows total now, not 3 old wrong ones)
    - Fix "Payments" group: 4 rows kept but paths corrected: `POST /v1/payments` "Create a payment intent", `GET /v1/payments` "List own payments (filters, paginated)", `GET /v1/payments/{public_id}` "Get single payment + events". Remove the old POST /poll row from this group (it belongs under Transactions).
    - Add new group **"Stores"**: 5 rows — `POST /v1/stores` "Create a store", `GET /v1/stores` "List stores (paginated)", `GET /v1/stores/{public_id}` "Get store + destination", `PUT /v1/stores/{public_id}/link` "Update payment destination link", `POST /v1/stores/{public_id}/disable` "Disable a store".
    - Rework **"KHQR"** group: 4 rows — `POST /v1/khqr/from-link` "KHQR from an ABA PayWay share link", `POST /v1/khqr/from-account` "KHQR from bank code + account or direct Bakong ID", `GET /v1/khqr/bank-codes` "List supported bank codes + derivation rules", `POST /v1/khqr/probe-aba-status` "Check ABA PayWay SSR page status for a link slug".
    - Add new **"Transactions"** group (12 rows total): `POST /v1/transactions/search` "Bakong search by raw criteria", `POST /v1/transactions/poll` "Long-poll Bakong until a match is found or timeout", `GET /v1/transactions/md5/{md5_value}` "Lookup by QR MD5 hash", `GET /v1/transactions/hash/{hash_value}` "Lookup by full hash", `GET /v1/transactions/short-hash/{short_hash}` "Lookup by short hash", `GET /v1/transactions/instruction-ref/{ref}` "Lookup by instruction ref", `GET /v1/transactions/external-ref/{ref}` "Lookup by external ref", `POST /v1/transactions/bulk` "Batch bulk search", `POST /v1/transactions/verify-receipt` "7-tier Bakong receipt cascade lookup", `POST /v1/transactions/account/check` "Preflight Bakong account routability", `POST /v1/transactions/token/renew` "Renew Bakong Open API token".
    - Fix **"Webhooks"** group 4 rows (keep existing 3 + add deliveries): `GET /v1/webhooks` "List webhook endpoints", `POST /v1/webhooks` "Register a webhook endpoint", `PATCH /v1/webhooks/{endpoint_id}` "Edit endpoint URL/events/status", `DELETE /v1/webhooks/{endpoint_id}` "Deactivate an endpoint", `GET /v1/webhooks/{endpoint_id}/deliveries` "List delivery attempts (last 200)", `POST /v1/webhooks/{endpoint_id}/test` "Trigger a synthetic test event".
    - Add new **"Billing"** group: `GET /v1/billing/plans` "List public plans with feature matrix", `POST /v1/billing/change-plan` "Change plan (upgrade/downgrade)", `GET /v1/billing/invoices` "List plan invoices", `GET /v1/billing/invoices/{id}/khqr` "Generate KHQR to pay an invoice (dog-foods our own gateway)".
    - Add new **"Account & KYC"** group (session auth): `GET /v1/me` "Get profile", `PATCH /v1/me` "Update profile (name/email)", `POST /v1/kyc` "Submit KYC data (individual or business)", `GET /v1/kyc` "Get KYC status + required fields".
    - Add new **"Platform (SaaS Sub-Merchants)"** group (Scale/Ent only): `POST /v1/platform/sub-merchants` "Create a sub-merchant (idempotent by external_id)", `GET /v1/platform/sub-merchants` "List sub-merchants", `GET /v1/platform/sub-merchants/{id}` "Get sub-merchant + counters", `PATCH /v1/platform/sub-merchants/{id}` "Edit destination/whitelabel/redirects", `POST /v1/platform/sub-merchants/{id}/disable` "Disable sub-merchant".
    - Add new **"Admin (Platform Owner)"** group (is_platform_admin only): `GET /v1/admin/kyc/pending` "KYC review queue", `POST /v1/admin/kyc/{account_id}/approve` "Approve KYC", `POST /v1/admin/kyc/{account_id}/reject` "Reject KYC (with reason)".
    - Add new **"Reports"** group (plan-gated): `GET /v1/reports/payments.csv` "CSV export (Starter: blocked)", `GET /v1/reports/payments.json` "JSON with pagination + totals summary".
    - Add new **"Public Checkout"** group (no auth): `GET /pay/{public_id}` "Hosted branded checkout page", `GET /pay/{public_id}/status` "JSON status endpoint (for checkout page poller)".
    - Add new **"Dev Rail (Test only)"** group (dev gateway flag): `POST /_dev/payments/{id}/scan` "Mark as scanned (test)", `POST /_dev/payments/{id}/pay` "Mark as paid (test bypass rail)".
  - **Add Getting Started Curl Panels**: Below `<GettingStarted />` existing 4 cards section, insert a new React component (inline or extract `QuickStartPanels()`) that contains 6 terminal-code blocks with 6 labels. Structure: each block uses existing `.docs-signature-panel` + `.docs-signature-panel-head` + `.docs-signature-code` classes. Do NOT add inline styles — any required new CSS class names (e.g. `docs-qs-grid`, `docs-qs-block`, `docs-qs-title`) go to `globals.css` ONLY. Code content snippets:
    1. Block 1 "Create Store": curl POST /v1/stores with sample body name/city + Authorization Bearer session placeholder + create store response 201.
    2. Block 2 "Attach PayWay Link": curl PUT /v1/stores/{st_}/link, body {link_type, raw_link}.
    3. Block 3 "Create Key": curl POST /v1/keys scope=store, store_id, mode=live → returns raw_key once.
    4. Block 4 "Create Payment": curl POST /v1/payments amount 5.50 ref_id, returns qr_string, checkout_url, pay_ id.
    5. Block 5 "Poll Until Paid": curl POST /v1/transactions/poll, with qr_md5, timeout 30s.
    6. Block 6 "Verify Webhook Signature (Node.js)": reuse existing signature section code verbatim but label "(6) Verify Webhook Signature" consistent with Step 5.
  - **CSS updates**: If any new layout classes needed for the 6-block grid, add them to `web/landing/app/globals.css` — never inline `style={{}}`.
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-15, AC-16
- **Test Requirements**:
  - `rule` TR-7.1: endpointGroups length after rewrite contains at least the 12 required groups enumerated above. Evidence: count groups array len >=12, each group.title string matches the names listed (case-insensitive).
  - `rule` TR-7.2: No documented path references `/v1/auth/api-keys` (old wrong prefix). grep-assert on page.tsx for that string returns zero matches. Evidence: grep command.
  - `rule` TR-7.3: 6 curl code blocks rendered below GettingStarted section (each with a `<pre class="docs-signature-code">` or derived class — each block preceded by a head label that contains the required 6 title strings). Evidence: next build output TSX parse or grep of the built HTML for each of 6 required titles.
  - `rubric` TR-7.4: Docs accuracy. Dimension: path/verb accuracy rate vs backend openapi.json. Scale 1-5. Anchors: 1 = >5 mismatches; 3 = 2-4 mismatches; 5 = zero path/verb mismatches across all documented rows. Threshold >=4. Evidence: automated diff check or manual spot check of 20 docs rows vs backend routes.

---

## Task 8: Integration Verification, Rebuild + Regressions Cleanup
- **Status**: `partial`
- **Verified**: the verification intent survives as a standing test rather than a one-off run — `tests/test_openapi_schema.py` (11 tests, green when run today) asserts `openapi.json` validity, that every published path is on the docs page (`test_every_published_path_is_documented_or_declared_internal`), and that `/v1/admin` + `/_dev` are deliberately kept out of the schema; `tests/test_migrations.py` covers the model↔migration diff — but TR-8.3's "Platform" tag no longer exists (router removed by `supabase/migrations/4-merge-sub-merchants-into-stores.sql`) and the admin router is hidden from `openapi.json` on purpose, so two of its three required tags are moot.
- **Priority**: high
- **Depends On**: Tasks 1, 2, 3, 4, 5, 6, 7
- **Description**:
  - **Backend regressions**: Run `ruff check src/chmabapay` and fix any new lint errors introduced (unused imports, line length, etc). Run `pytest -x` and fix any test failures from M1 tests due to new model FKs or route ordering.
  - **FastAPI openapi.json**: Start dev server, curl `/openapi.json` → validate it parses as valid JSON, no duplicate operationIds (new Admin/Platform/Reports routes have unique operation ids via FastAPI auto or explicit if needed).
  - **Next.js rebuild per project_memory cache fix**: `Remove-Item -Recurse -Force web/landing/.next -ErrorAction SilentlyContinue`, then `cd web/landing && pnpm next build`, then start server `pnpm next start -p 3001`, then fetch `/api/docs` HTML and confirm it renders CSS correctly (no text/plain MIME issue, all `<link rel=stylesheet>` return 200).
  - **Final smoke tests**: For every newly-documented group, curl the running dev server with auth headers where needed to confirm routes return 401/403/200/405 as expected (no 404 unknowns).
  - **Doc cross-check**: Spot-check documented rows from 6 endpoint groups against actual openapi paths. Fix any last-remaining mismatch (should be zero after Task 7 but double-check).
- **Acceptance Criteria Addressed**: AC-17, AC-18, AC-16
- **Test Requirements**:
  - `rule` TR-8.1: `pytest -x` exit code 0. Evidence: terminal output line "X passed" saved.
  - `rule` TR-8.2: `ruff check src/chmabapay` exit 0 with zero new errors. Evidence: terminal output.
  - `rule` TR-8.3: `GET /openapi.json` returns 200, JSON parsed, contains "Admin" tag and "Platform" tag and "Reports" tag. Evidence: parsed JSON keys.
  - `rubric` TR-8.4: Landing rebuild stability and style fidelity. Scale 1-5: 1 = unstyled flash or missing CSS, 3 = styled but one console warning unresolved, 5 = styled, all CSS links 200, zero console warnings or errors. Threshold >=4. Evidence: dev server network tab log or curl of page + CSS refs status codes.
- **Notes**: If pytest fails due to missing tables (SubMerchant new), ensure tests use create_all() at session start. If any model circular import, wrap in TYPE_CHECKING with string FKs.
