# ChmabaPay - API Integration Completeness (P0 Docs Accuracy + P1 Backend Blockers)

## Overview
- **Summary**: Bring the ChmabaPay API integration surface to production-readiness: (a) correct the landing `/api/docs` page so every documented route is actually callable against the backend; (b) implement the P0 backend blockers (Admin KYC approval flow, SaaS Sub-Merchants router + payment resolver, Billing invoices with self-pay KHQR) that currently prevent Business accounts from going live; (c) implement P1 critical surface (Reports CSV/JSON exports, Webhook delivery log listing, concrete Getting Started curl workflow).
- **Purpose**: Currently the landing docs page is a marketing skeleton with mismatched route prefixes/methods, 12+ implemented Bakong/transaction endpoints are undocumented, and three BRD-value-prop features (KYC approval, Sub-Merchants, plan invoices) are unimplemented backend-only despite the BRD §10 requirements and the landing CTA promising them. After this spec, an Individual merchant can follow the docs from signup to first paid webhook without reading source code, and a Business Scale merchant can onboard their own end-merchants via the Sub-Merchants API.
- **Target Users**: Backend integrators (PHP/Node/POS engineers for Individual vendors), SaaS platform partners (KhmerPOS class Business Scale/Ent), ChmabaPay platform admin (KYC reviewers), finance ops (reconciliation via reports).

## Goals
1. **Docs Accuracy**: Every row in the landing API docs `endpointGroups` array maps 1:1 to an actual backend route with the correct HTTP verb, prefix, and path parameter naming.
2. **Coverage**: Every *existing* working backend endpoint (Stores, Transactions, KHQR extras, Billing, Account/KYC, Checkout, Dev rail) is listed in the landing docs page with a minimal description.
3. **Admin KYC Workflow**: Platform admin can list pending KYC submissions, approve, or reject — results in `kyc_status` flip, `kyc_live_blocked` flip, and AuditLog rows. Without this, Business KYC submissions are stuck forever.
4. **SaaS Sub-Merchants (Platform Router)**: Business Scale/Ent accounts get a full `POST/GET/PATCH/DELETE /api/v1/platform/sub-merchants` surface, plus the `sub_merchant_id` resolver branch in payment creation and counter increments on mark_paid — exactly per BRD §4.2 Step 2 and §6 Step 9.
5. **Billing Invoices + Self-Pay KHQR**: `GET /api/v1/billing/invoices` lists invoices; `GET /api/v1/billing/invoices/{id}/khqr` creates a KHQR payment via chmabapay's own `create_payment` API (dog-food) so customers pay their platform subscription fee using the product itself.
6. **Reports + Reconciliation**: `GET /api/v1/reports/payments.csv` (plan-gated, Starter hides) and `GET /api/v1/reports/payments.json` return filtered payments with totals.
7. **Webhook Deliveries Log**: `GET /api/v1/webhooks/{endpoint_id}/deliveries` exposes the last 200 delivery attempt rows so merchants can audit failures.
8. **Getting Started Concrete Flow**: The docs page "Getting Started" 4 cards contain **copy-pasteable curl** commands in a new code-panel section showing: Create Store → Attach Payment Link → Create Key → Create Payment → Poll Until Paid → Verify Webhook Signature.
9. **No regressions**: Existing `pytest -x` and `ruff check src/chmabapay` clean; CORS + auth patterns unchanged for existing integrators; FastAPI openapi.json still renders.

## Non-Goals
1. Admin panel full dashboard UI (Accounts CRUD, Plan CRUD, Impersonation, Platform Settings, MRR reports — BRD §11 except KYC approve/reject). Only KYC list/approve/reject endpoints go live here.
2. Billing invoice generation scheduler loop and PlanInvoice auto-generation at T+1 midnight. We add the endpoint *surface* and data model wiring for manual invoice creation + KHQR pay; the cron loop is Milestone 2 scope.
3. Email notifications (12.9 gap), Khmer i18n (12.5), rate limit middleware (12.6), PDF receipts (12.8) — separate Milestone 2 items.
4. Frontend Dashboard UI wiring for Admin KYC / Sub-Merchants pages. We ship the REST endpoints only; dashboard pages consume them later.
5. Bulk Sub-Merchants CSV import endpoint (BRD §10.2 line 922). Ship CRUD only; import is P2.
6. Sub-Merchant whitelabel CSS override on hosted checkout page render. That's a checkout router + frontend follow-up, not strictly an API integration gap.

## Background & Context
- **Current API docs state** (2026-09-11 page.tsx L28-66): Docs list 4 groups (Auth x3 / Payments x4 / KHQR x3 / Webhooks x3). Audit: 3 routes don't exist (`DELETE /api/v1/auth/api-keys/{id}`, `POST /api/v1/payments/{id}/poll`, `GET /api/v1/khqr/{id}`); 5 routes have wrong prefixes/verbs (`/api/v1/auth/api-keys` → `/api/v1/keys`; `GET /api/v1/transactions/search` → POST; `POST /api/v1/khqr/from-bank` → `/from-account`); DELETE key route actually is `POST /api/v1/keys/{key_id}/revoke`.
- **Implemented but undocumented backend endpoints** (from grep): 5 Stores endpoints, `GET /api/v1/payments` list, 12 Transactions endpoints (hash/md5/short/instruction/external/bulk/verify-receipt/account-check/token-renew/poll), 2 KHQR extras (bank-codes list + probe-aba-status), 2 public Checkout endpoints, 4 Account/KYC endpoints (me GET/PATCH, kyc POST/GET), 2 Billing endpoints, OAuth 3 endpoints, keys rotate, dev rail 2 endpoints.
- **Missing BRD §10.2 routers**: `routers/platform.py` (Sub-Merchants, not a single file exists), `routers/admin.py` (KYC approve/reject queue list, no file), `routers/reports.py` (CSV/JSON exports, no file). No `SubMerchant`-specific SQLAlchemy model found despite `max_sub_merchants` cols existing on Plan. `resolve_target()` in payments.py has only `store_public_id` resolver branch, not `sub_merchant_id`.
- **Plan tables + KYC cols exist**: M1 already seeded 4 plans (starter/growth/scale/enterprise); `Account.kyc_status`, `kyc_live_blocked`, `saas_sub_merchants_enabled`, `whitelabel_enabled`, `is_platform_admin` cols all there. `AuditLog` model exists per M1 AC-9.
- **Models confirmed**: `ApiKey`, `WebhookEndpoint`, `Payment`, `Store` all have scope/resolver patterns we can copy for Sub-Merchants. `mark_paid()` in services.payments already does atomic counter updates (PlanLedgerEntry update col=col+1 pattern). Reuse the same UPDATE pattern for SubMerchant.total_payments_count + total_volume_cents counters.

## Functional Requirements
- **FR-1 Docs Accuracy Rewrite**: Rewrite `endpointGroups` in landing page to match actual backend route prefixes/verbs/params. Add 6 new groups: Stores, Transactions, Billing, Account KYC, Public Checkout, Dev Rail. Total entries ≥40 endpoint rows (accurate count), each with Method + correct path + 1 line description.
- **FR-2 Admin KYC Router**: New `routers/admin.py` with prefix `/api/v1/admin`. Routes: `GET /kyc/pending` (list accounts where kyc_status in {submitted,reviewing}), `POST /kyc/{account_id}/approve` (flips kyc_status=approved, kyc_live_blocked=False, writes AuditLog with admin_account_id), `POST /kyc/{account_id}/reject` (flips status=rejected, saves reject_reason, keeps kyc_live_blocked=True, writes AuditLog). Every route gated by `session_account.is_platform_admin == True` → 403 otherwise.
- **FR-3 Sub-Merchant Model**: Add `SubMerchant` SQLAlchemy model per BRD §8.2 with `public_id` (sm_… unique), `account_id` FK, `external_id`, `display_name`, `status`, `shadow_store_id` FK stores.id unique, all KHQR destination cols (link_type, raw_link, merchant_account_id, bank_code, account_number, resolved_bakong_id…), redirect URLs, support_email, whitelabel_css, counters `total_payments_count default 0`, `total_volume_cents default 0`, created/updated_at. Add to models.py + create_all.
- **FR-4 Sub-Merchant Platform Router**: New `routers/platform.py` prefix `/api/v1/platform`. Routes: `POST /sub-merchants` (create — validates `Account.saas_sub_merchants_enabled == True` via active plan, creates shadow Store + PaymentLink internally, resolves bakong_id, returns 201 + sm_ id + resolved_bakong_id + tag30_00; if plan cap exceeded → 402 plan limit), `GET /sub-merchants` (paginated list filtered by account, optional ?status, ?external_id), `GET /sub-merchants/{public_id}` (detail with counters), `PATCH /sub-merchants/{public_id}` (edit destination, redirects, label, whitelabel_css), `POST /sub-merchants/{public_id}/disable` (status=disabled, disables shadow store too). Plan enforcement: Starter/Growth → 403 "Upgrade to Scale for Sub-Merchants".
- **FR-5 Payment sub_merchant_id Resolver**: In `payments.resolve_target()` add the STEP 2 branch from BRD §4.2: if body contains `sub_merchant_id` and ctx.api_key.scope is account → load SubMerchant by public_id under this account → target store = SubMerchant.shadow_store_id → if saas gate disabled on Account → 403. Update PaymentOut schema to include optional `sub_merchant_id` + `external_id` when present.
- **FR-6 Sub-Merchant Counters on Paid**: In `services.payments.mark_paid()`, after PlanLedgerEntry counter update: if `payment.store_id` matches any `SubMerchant.shadow_store_id`, atomically increment `total_payments_count += 1` and `total_volume_cents += payment.amount_cents` on that SubMerchant row using `UPDATE col=col+val WHERE id`.
- **FR-7 Billing Invoices Surface**: Extend `routers/billing.py` with `GET /invoices` (list filtered by account, ?period_month optional), `GET /invoices/{invoice_id}/khqr` (creates a payment via internal `create_payment` call targeting ChmabaPay HQ store — the plan invoice total amount; returns `{payment_id, qr_string, checkout_url}`). Add PlanInvoice manual-insert helper for now (auto-generator M2 later). Ensure Invoice.status enum draft|issued|paid|void exists on the model (or add if missing).
- **FR-8 Reports Router**: New `routers/reports.py` prefix `/api/v1/reports`. Routes: `GET /payments.csv` (StreamingResponse csv, filters ?from= ?to= ?store_id= ?sub_merchant_id= ?statuses= comma-list) — plan gate Starter → 403 with "Upgrade to Growth for CSV exports". `GET /payments.json` same filters + pagination + totals summary `{count, total_amount_cents, filters}`. Session or API-key auth accepted (hybrid).
- **FR-9 Webhook Deliveries List**: Add `GET /api/v1/webhooks/{endpoint_id}/deliveries` (paginated, 200 most recent) returning `event_type, http_status, attempt_count, response_body_preview_500, created_at, completed_at`. Scope resolution (owner-only) matches existing webhooks patch/delete. Ensure underlying WebhookDelivery model rows exist or add minimal fetch against Event table joined with deliveries.
- **FR-10 Getting Started Curl Panels**: Below the existing 4 landing feature cards, add 6 terminal-style code blocks (reuse `docs-signature-code` class) showing concrete curl for: (10.1) Create Store, (10.2) Attach PayWay Link (PUT link), (10.3) Create Store-scoped Key, (10.4) Create Payment with that key, (10.5) Long-Poll Until Paid via `/api/v1/transactions/poll`, (10.6) Example Node.js webhook signature verify matching the existing signature panel on the page.

## Non-Functional Requirements
- **NFR-1 Auth Pattern Consistency**: Every new router (admin, platform, reports) uses the **same hybrid auth pattern** as keys/webhooks: Bearer `ck_` account-scope API key OR session cookie via `get_current_session_account`. Store-scoped keys (`st_`) are REJECTED from platform/admin endpoints with 403.
- **NFR-2 AuditLog Every Destructive Admin Action**: `approve`, `reject`, `change-plan` via admin impersonation, `sub-merchant disable`, `api-key global-revoke` — every write that affects a user writes an AuditLog row.
- **NFR-3 Pytest Green**: `pytest -x` exit 0. Minimal new tests: (a) Admin approve KYC → account status flipped + AuditLog row; (b) Platform create Sub-Merchant → shadow store + PaymentLink created; (c) Payment with sub_merchant_id → resolves to shadow store, paid increments SubMerchant counters; (d) Reports CSV Starter account → 403, Growth → 200; (e) Landing docs endpointGroups paths match actual backend routes (string compare, docs smoke test).
- **NFR-4 Ruff Clean**: `ruff check src/chmabapay` exit 0 after all changes.
- **NFR-5 FastAPI OpenAPI JSON Valid**: After router includes in main.py, `GET /openapi.json` returns 200 valid JSON (schema-level no duplicate OperationIds).
- **NFR-6 Scope Enforcement**: Sub-Merchant 403 on Growth; account-scope keys 400 on Individual Starter; CSV reports 403 on Starter. Must be **server-side enforced**, not frontend-only. All with `detail=` strings that are copy-pasteable to the docs.
- **NFR-7 Idempotency-friendly**: SubMerchant create accepts optional `external_id` unique per (account_id, external_id). Duplicate POST same external_id returns the existing row (200, not 201) to match CutLuy mental model — prevents KhmerPOS accidental double-sub-merchant on retry.
- **NFR-8 Next.js Landing Page Rebuild Stable**: After docs rewrite, delete `.next`, rebuild, and verify on the running preview (port 3001) that there are 0 unstyled pages / missing CSS hashes (the known M1/M2 Next cache pitfall).

## Constraints
- **Technical**: Backend FastAPI + SQLAlchemy async; SQLite for dev (use it for tests too, don't add postgres test harness). Frontend landing uses Next 14 App Router, globals.css centralized per project_memory rule — ZERO inline `style={{}}` React blocks allowed on the docs page rewrite; any new terminal/row classes go to globals.css only.
- **Business**: 2 account types only. Sub-Merchants unlocked only on Scale/Enterprise plans. Individual *never* gets Sub-Merchants, account-scope keys, or CSV reports. KYC: Business = hard block (live requires approved KYC, kyc_live_blocked must flip False). Existing enforcement kept.
- **Dependencies**: No new Python packages allowed unless absolutely required for CSV streaming (FastAPI already has StreamingResponse; csv module is stdlib so OK). No new JS packages for the landing page — style new terminal/code rows purely with CSS class extensions in globals.css matching the existing `.docs-signature-code` look.
- **Project Conventions**: All colors import from `web/shared/theme.ts` for TSX code if colors are added anywhere (per project_memory); backend strings follow existing `detail="snake_case"` error code convention (see keys.py line 124).

## Assumptions
1. `WebhookDelivery` rows are already being written in the current webhook fan-out (W2 worker). If not, FR-9 will only expose information derivable from Event + endpoint join, not raw delivery HTTP status; we'll note this limitation clearly in the response.
2. ChmabaPay has an internal "HQ store" for self-billing the KHQR invoice payment. The `GET /invoices/{id}/khqr` endpoint will target a configurable store (env var `CHMABAPAY_HQ_STORE_ID`), defaulting to the first store under the `is_platform_admin=True` account if unset.
3. `SubMerchant.shadow_store_id` 1:1 unique. A Store that is a "shadow" belongs to exactly one SubMerchant and should be hidden from the regular Stores list UI (filtered on shadow_store FK later) but still functions fully for payments + detection workers.
4. The docs page rewrite does not need a new "Playground" right column (P2); it only needs to correct the 4 existing groups, add 6 new groups, and add the Getting Started curl code panels.
5. Frontend build: Use existing `next build` + `next start` on port 3001 for verification as project_memory describes (fixes stale `.next` CSS hash issue by blowing away `.next` before rebuild).

## Open Questions
- [x] Does the existing W2 webhook worker persist delivery attempts with HTTP status? If not we'll expose Event timestamps + endpoint id only and mark the response_body_preview as "N/A for historical deliveries" until infra catches up. → resolved: yes, it does — `models.EventDelivery` (`src/chmabapay/models.py:285`) persists `last_response_status`, `attempts` and `last_error` per `(event, endpoint)`, and `GET /api/v1/webhooks/{endpoint_id}/deliveries` (`src/chmabapay/routers/webhooks.py:377`, `list_webhook_deliveries`) returns them as `http_status` / `attempt_count` / `response_body_preview`, so the Event-only fallback shape was never needed.
- [x] ChmabaPay HQ store id for invoice self-billing KHQR: hard-code to env var `CHMABAPAY_HQ_STORE_ID` (acceptable)? Yes assumed — if unset at runtime, fallback to first store owned by admin_account. → resolved: `resolve_hq_store` (`src/chmabapay/services/billing.py:204`) resolves in three steps, not two — `CHMABAPAY_HQ_STORE_ID` first (matches by numeric id or `public_id`, source `"env"`), then a store *named* `ChmabaPay HQ` owned by an `is_platform_admin` account (source `"console"`, so plan fees cannot land in an operator's unrelated store), and only then the admin account's first active store (source `"fallback"`); "first store owned by admin" is the last resort, not the default.

---

## Acceptance Criteria

### AC-1: Docs EndpointGroups Accuracy vs Backend (Rule)
- **Type**: `rule`
- **Given**: The landing docs page endpointGroups array and the actual FastAPI mounted routers.
- **When**: Compare every documented row (method + path prefix + params) against the actual router decorators from grep `@router.(get|post|...)` in src/chmabapay/routers with prefixes.
- **Then**: 100% of documented rows match actual route method, path, and parameter names. For example documented row `POST /api/v1/keys` (not `/api/v1/auth/api-keys`), `POST /api/v1/khqr/from-account` (not from-bank), `POST /api/v1/transactions/search` (not GET), no rows reference paths that don't exist like `POST /api/v1/payments/{id}/poll` or `GET /api/v1/khqr/{id}` unless implemented in this spec.
- **Pass Condition**: Zero mismatches; a script that enumerates each docs row and hits the running dev server route (404/405 means fail) returns 0 failures for docs-claimed routes.
- **Evidence**: Unit test scan of docs endpointGroups TS array vs actual openapi.json paths, or manual curl table in task completion evidence.

### AC-2: 100% Backend Public/Integrator-Facing Endpoints Listed in Docs (Rule)
- **Type**: `rule`
- **Given**: Current backend router grep: 49 route decorators (minus internal `_dev/*` if marked dev-gated, minus `/auth/google/callback` which is an OAuth internal redirect, minus `/health`).
- **When**: Count the total docs rows across all groups after rewrite.
- **Then**: Docs list >= 85% of the integrator-facing working routes (≥40 rows). Specifically: Stores (5), Payments (create+list+get=3), Transactions (all 12 incl poll + verify-receipt + account-check + md5/hash/external/instruction/short/bulk/search/token-renew), KHQR (from-link, from-account, bank-codes, probe-aba-status = 4), Webhooks (list/create/update/delete/test/deliveries = 6), Keys (list/create/rotate/revoke = 4), Billing (plans, change-plan, invoices list, invoices/khqr = 4), Account KYC (me GET/PATCH, kyc GET/POST = 4), Public Checkout (pay/{id}, pay/{id}/status = 2), Admin (kyc pending list, approve, reject = 3), Platform (sub-merchants CRUD + disable = 5), Reports (csv/json = 2). Dev rail (scan/pay) shown with "(Dev only)" suffix.
- **Pass Condition**: Group list includes every category above; spot-checked 10 routes randomly all present.
- **Evidence**: Full list paste of docs endpointGroups after implementation compared to router grep output.

### AC-3: Admin KYC Approve Flips kyc_live_blocked + Writes AuditLog (Rule)
- **Type**: `rule`
- **Given**: Account B with account_type=business, kyc_status=submitted, kyc_live_blocked=True. Admin account A with is_platform_admin=True session.
- **When**: POST /api/v1/admin/kyc/{B.id}/approve with admin session auth.
- **Then**: B.kyc_status == "approved". B.kyc_live_blocked == False. B.kyc_approved_at set (not null). AuditLog 1 row where action="kyc.approved", target_type="Account", target_id=B.id, admin_account_id=A.id. Subsequent live payment creation with B's key no longer returns KYC required 402.
- **Pass Condition**: 4 SQL assertions + HTTP 200 on live payment create after.
- **Evidence**: pytest httpx TestClient test output.

### AC-4: Admin Reject KYC Saves Reason + Keeps Blocked (Rule)
- **Type**: `rule`
- **Given**: Same B account above, submit kyc, admin A session.
- **When**: POST /api/v1/admin/kyc/{B.id}/reject body={"reason": "MoC PDF blurry. Please re-upload."}
- **Then**: B.kyc_status == "rejected". B.kyc_live_blocked == True (still blocked). B.kyc_reject_reason == that string. AuditLog row action="kyc.rejected" with same admin_account_id.
- **Pass Condition**: 3 SQL cols + AuditLog row.
- **Evidence**: pytest assertions.

### AC-5: Admin Endpoints Strictly Gate is_platform_admin (Rule)
- **Type**: `rule`
- **Given**: Non-admin session C (is_platform_admin=False) with valid login.
- **When**: GET /api/v1/admin/kyc/pending OR POST approve/reject.
- **Then**: HTTP 403 {"detail":"forbidden"}. No DB rows changed.
- **Pass Condition**: 3 endpoint calls all return 403.
- **Evidence**: pytest outputs.

### AC-6: Sub-Merchant Model + CRUD Surface (Rule)
- **Type**: `rule`
- **Given**: Business Scale account with saas_sub_merchants_enabled=True via active plan, session or account-scope key.
- **When**: POST /api/v1/platform/sub-merchants with body per BRD §6 Step 9 (external_id, display_name, khqr_config.link_type=aba_payway_link, raw_link=https://link.payway.com.kh/xxx, bakong_id hint, redirect_success_url, redirect_failure_url, support_email).
- **Then**: HTTP 201 response with keys {sub_merchant_id (sm_…), resolved_bakong_id, tag30_00}. SubMerchant row created. Store row created (shadow_store_id FK unique). PaymentLink for that store exists with the raw_link. External_id unique within that account (duplicate POST same external_id → 200 returns existing row idempotently).
- **Pass Condition**: 3 SQL joins verify shadow store + link + SubMerchant; idempotent 2nd POST returns same public_id.
- **Evidence**: pytest with 2 POSTs, assertions on returned ids.

### AC-7: Sub-Merchants Plan-Gate for Growth/Individual (Rule)
- **Type**: `rule`
- **Given**: Individual Starter account, and Business Growth account (saas gate False).
- **When**: Both try POST /api/v1/platform/sub-merchants.
- **Then**: Both return HTTP 403 with specific detail strings: Individual → "Sub-Merchants requires a Scale plan. Upgrade from Starter to access."; Growth → "Sub-Merchants requires a Scale plan. Upgrade from Growth ($99/mo) to unlock."
- **Pass Condition**: Two 403s with exact detail substrings.
- **Evidence**: pytest outputs.

### AC-8: Sub-Merchant Cap Enforced (Rule)
- **Type**: `rule`
- **Given**: Scale plan with max_sub_merchants=500. Account with 500 existing Sub-Merchants at cap.
- **When**: Try to create one more.
- **Then**: HTTP 402 detail="Sub-Merchant cap reached (500 on Scale plan). Upgrade to Enterprise for unlimited."
- **Pass Condition**: 402 response.
- **Evidence**: pytest assertion after fixture creates 500 rows (or mock cap=2 + 3rd request).

### AC-9: Payment create with sub_merchant_id Resolves to Shadow Store (Rule)
- **Type**: `rule`
- **Given**: SubMerchant SM created above (SM.shadow_store_id=S). Account-scope ck_ key.
- **When**: POST /api/v1/payments body {amount: 10.00, sub_merchant_id=SM.public_id, reference_id="test"}
- **Then**: Payment.store_id == S (the shadow). PaymentOut response includes sub_merchant_id=SM.public_id and external_id=SM.external_id if set.
- **Pass Condition**: SQL store_id equality + response body fields present.
- **Evidence**: pytest assertions.

### AC-10: Paid Payment Increments Sub-Merchant Atomic Counters (Rule)
- **Type**: `rule`
- **Given**: SubMerchant SM with counters 0.
- **When**: Create payment via sub_merchant_id=SM → call mark_paid.
- **Then**: SM.total_payments_count == 1. SM.total_volume_cents == 1000 (for $10.00 payment). 2 concurrent payments 2000 + 3000 cents → total_volume_cents == 5000, count==2, no race (UPDATE col=col+val).
- **Pass Condition**: 2 test scenarios (single + concurrent) with correct final counts.
- **Evidence**: pytest asyncio.gather concurrent test output.

### AC-11: Billing Invoices List + Self-Pay KHQR Endpoints (Rule)
- **Type**: `rule`
- **Given**: Business Growth account with 1 draft PlanInvoice row in the DB.
- **When**: GET /api/v1/billing/invoices → then GET /api/v1/billing/invoices/{id}/khqr.
- **Then**: Invoices list 200 returns data array with invoice id/period/status/total_due_cents. Invoice khqr endpoint 201 returns {payment_id, qr_string, checkout_url}. The payment points to ChmabaPay HQ store, amount_cents == invoice.total_due_cents.
- **Pass Condition**: 2 endpoint calls, response fields verified, payment store matches HQ store.
- **Evidence**: pytest requests + SQL verify payment created for correct amount/store.

### AC-12: Reports CSV Plan-Gate (Rule)
- **Type**: `rule`
- **Given**: Starter account (csv_export_enabled=False on plan), Growth account (True).
- **When**: GET /api/v1/reports/payments.csv with filters ?from=2026-09-01&to=2026-09-30.
- **Then**: Starter → 403 detail="CSV exports are not available on the Starter plan. Upgrade to Growth to unlock." Growth → 200 Content-Type text/csv, stream with header row (id,status,amount,currency,reference,store,paid_at,created_at).
- **Pass Condition**: 403+detail for Starter; 200+csv header row for Growth.
- **Evidence**: pytest httpx outputs + Content-Type header check.

### AC-13: Reports JSON with Totals (Rule)
- **Type**: `rule`
- **Given**: Growth account with 3 paid payments $5 + $10 + $2 = $17 total.
- **When**: GET /api/v1/reports/payments.json ?statuses=paid
- **Then**: 200 response shape {data: [...payments listed], summary: {count:3, total_amount_cents:1700, filters_applied:{statuses:"paid"}}, pagination: {page:1, per_page:20, total_pages:1}}.
- **Pass Condition**: Summary totals correct.
- **Evidence**: pytest assertions on JSON.

### AC-14: Webhook Deliveries List Endpoint (Rule)
- **Type**: `rule`
- **Given**: Webhook endpoint with 3 past deliveries (rows present or simulated).
- **When**: GET /api/v1/webhooks/{endpoint_id}/deliveries
- **Then**: 200 list of delivery objects, each with {event_type, http_status, attempt_count, response_body_preview (max 500 chars), created_at}. Scope: only owner account endpoint visible (other account 404). Pagination default limit 200, max 500.
- **Pass Condition**: list length matches fixture; 404 for cross-account probe.
- **Evidence**: pytest assertions.

### AC-15: Getting Started Curl Panels 6 Code Blocks Rendered Correctly (Rule)
- **Type**: `rule`
- **Given**: Landing page /api/docs rendered after rebuild.
- **When**: Inspect HTML below the 4-steps feature cards.
- **Then**: 6 code blocks (pre.docs-signature-code) present titled exactly: "(1) Create Store", "(2) Attach PayWay Link", "(3) Create Store-Scoped Key", "(4) Create Payment", "(5) Poll Until Paid", "(6) Verify Webhook Signature (Node.js)". Each contains a valid curl or JS code snippet that a copy-paste integrator can run with only their base URL + API key replaced. Not empty placeholder strings.
- **Pass Condition**: 6 blocks, each code length >30 chars, titles rendered in docs-label headings above.
- **Evidence**: Browser snapshot or HTML grep on the rendered page.

### AC-16: Next.js Landing CSS Stable (Rule)
- **Type**: `rule`
- **Given**: Landing page rebuilt with `rm -rf .next` + `pnpm next build` + `next start` on port 3001.
- **When**: Fetch `/api/docs` HTML page.
- **Then**: No unstyled "flash of raw text" — document `<head>` contains the correct CSS link (not 404). Page loaded without MIME type text/plain (the known stale cache symptom).
- **Pass Condition**: HTTP 200 for the page, HTTP 200 for every CSS <link> referenced, 0 console errors.
- **Evidence**: `next build` log + page fetch + CSS requests 200 status.

### AC-17: Pytest + Ruff Clean After (Rule)
- **Type**: `rule`
- **Given**: After all implementation tasks done.
- **When**: Run `pytest -x` → exit 0. Run `ruff check src/chmabapay` → exit 0.
- **Then**: Zero test failures; zero new ruff errors.
- **Evidence**: Full terminal log pasted.

### AC-18: FastAPI OpenAPI Valid No Duplicates (Rubric)
- **Type**: `rubric`
- **Dimension**: OpenAPI schema completeness and operation naming quality
- **Scale**: 1-5
- **Anchors**: 1 = openapi.json 500 or missing due to duplicate operationId; 3 = openapi.json renders but 3+ endpoints missing tags or summaries; 5 = every new Admin/Platform/Reports/Invoices route appears with correct tag (Admin, Platform, Reports, Billing), operationId is unique, summary line non-empty for each route.
- **Pass Threshold**: >= 4
- **Evidence**: openapi.json paths enumeration, tag list check.
