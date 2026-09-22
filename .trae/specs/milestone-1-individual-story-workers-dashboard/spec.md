# ChmabaPay M1 — Individual Story + Workers Scaffold + Dashboard Skeleton

## Overview
- **Summary**: Implement Milestone 1 from BRD §13: Individual user Sokha can sign in with Google, create a store with ABA PayWay link or bank account, generate test-mode payments that auto-complete (no Bakong needed), and see results in a Next.js dashboard. Refactor existing `while True` loops in `webhooks.py` into pluggable Worker classes with QueueTransport ABC so Phase 2→3 scale-up requires zero logic rewrite.
- **Purpose**: First runnable demo of the full ChmabaPay product (sign-in → create store → test pay → webhook → invoice). Foundation with scalable workers built-in from day 1.
- **Target Users**: Individual accounts (Sokha), Business accounts (auto-type-switch after plan upgrade to Growth), Platform admin (basic KYC status display).

## Goals
1. Individual sign-in with Google OAuth, JWT session cookie, onboarding account_type=individual flow.
2. Full refactor of all 3 existing bg loops into `Worker.process(Job)` pattern; enqueue-on-write everywhere.
3. Account schema + Plan tables; seed 4 default plans (Starter/Growth/Scale/Enterprise).
4. Next.js 13 App Router User Portal with: Login, Dashboard Overview, Stores (wizard 3 screens), Payments list + detail, Keys, Webhooks, Settings (Profile/KYC/Billing).
5. Test-mode mark_paid 5s fake delay flow with webhook fan-out working against sink.dev server.
6. Individual → Growth plan upgrade auto-switches account_type=business and shows shared-key option on Key Create.
7. Observability hooks (trace ID header + AuditLog + attempt_history JSON list) instrumented, not bolted on.
8. Design tokens from DESIGN_TOKENS_AND_UX_LAWS.md applied: violet #6957F5 CTAs, ABA green #009639 trust badges, 8px button radius, Khmer-first text on Individual pages, dark mode toggle hidden for Individual users.

## Non-Goals
1. Sub-Merchants + SaaS whitelabel → Milestone 3 only.
2. Admin KYC approve/reject UI + full admin pages → Milestone 2 (M1 seeds `is_platform_admin` bool + AuditLog table rows on actions so M2 admin UI can use them).
3. Real Redis Transport (Phase 2) → Milestone 2.7 (M1 writes skeleton stub comment only in workers/redis.py).
4. Real KYC verification flow (automated MoC PDF parse) → M2 admin manual review only.
5. Email notifications → M2.5.
6. Rate limit middleware + i18n EN/KH toggle + PDF receipts + CSV export → M2.

## Background & Context
- Existing code state verified 2026-09-10:
  - `models.py:L67-L217` Account/Store/PaymentLink/ApiKey/Payment/WebhookEndpoint/Event/EventDelivery tables exist; missing: account_type enum cols, KYC cols, feature gates; NEW tables Plan/PlanSubscription/PlanInvoice/PlanLedgerEntry/AuditLog.
  - `main.py:L17-L62` lifespan starts 3 raw `while True` loops: webhook_loop, expiry_loop, bakong_verify_loop (from `webhooks.py`). MUST refactor all 3 into Worker.process(Job).
  - Routers existing: payments, stores, checkout, transactions, khqr, dev. NEW needed: auth, account, keys, webhooks, billing.
  - Brand tokens extracted live from chmaba.com today (violet #6957F5, lime #BFFA6A, Inter + Noto Sans Khmer fonts) → persisted in `web/DESIGN_TOKENS_AND_UX_LAWS.md` — MUST apply theme.ts copy-paste code as-is.
- Account model: 2 types only. Business LTD vs SaaS = same Business type with different plan tier + `saas_sub_merchants_enabled` plan gate. (User explicitly corrected this twice.)
- Account scope keys: Individual forced store-scope only (enforced server-side). Business Growth/Scale unlocks shared account-scope keys.
- QueueTransport ABC: interface defined in EDD §5.1 + InProcessTransport full impl EDD §5.2 + Redis skeleton EDD §5.3. Milestone 1 implements QueueTransport ABC, InProcessTransport fully, Redis skeleton comment-only placeholder.

## Functional Requirements
- **FR-1 Worker Scaffold**: Package `src/chmabapay/workers/` exports QueueTransport ABC, InProcessTransport, base Worker ABC, and 4 Worker classes: W1 PaymentDetectionWorker, W2 WebhookSenderWorker, W3 BillingInvoiceWorker (M1 stub only, no-op), W4 ExpirySweeperWorker.
- **FR-2 Refactor Existing Loops Into Workers**: `main.py:L26-L33` raw loop lines gone; lifespan instead instantiates 4 Worker instances with InProcessTransport; enqueues W4 ExpirySweeper 60s heartbeat job at startup.
- **FR-3 Enqueue-on-Write**: `create_payment()` commit → W1 dedup enqueue by payment_public_id (no duplicate pending jobs per payment). Event insert → W2 fan-out (most-specific-scope wins) per endpoint with `uq_event_endpoint` dedup OK.
- **FR-4 DB Migration Account New Cols**: `Account` gets `account_type` enum, all KYC cols per BRD §2 table, `saas_sub_merchants_enabled`, `whitelabel_enabled`, `is_platform_admin`. Default new account: `account_type="individual"`, `kyc_status="none"`, `kyc_live_blocked=True`.
- **FR-5 DB Plan Tables + Seed**: New tables `Plan`, `PlanSubscription`, `PlanInvoice`, `PlanLedgerEntry`, `AuditLog`. Seed 4 default plans: Starter ($0, Ind-only, max_stores=5, allow_account_scope_keys=False, allow_saas=False), Growth ($29/mo, Business, max_stores=50, allow_account_scope_keys=True, allow_saas=False, allow_whitelabel=False), Scale ($99/mo, allow_saas=True, max_sub_merchants=500, allow_whitelabel=True), Enterprise (custom). Default new signup auto-subscribed to Starter trial.
- **FR-6 Plan Enforcement**: `create_store()` enforces `Plan.max_stores` cap; `ApiKey` create blocks INDIVIDUAL users trying to create account_scope keys; PlanLedgerEntry incremented atomically in `mark_paid()` using `UPDATE col=col+1 WHERE id`.
- **FR-7 Auth Router**: New `routers/auth.py` with Google OAuth login/redirect, JWT session in httpOnly cookie (max-age 24h, Secure if not localhost, SameSite Lax), `/auth/signout` clears cookie. Uses session auth Depends function that returns Account row.
- **FR-8 Account Router**: New `routers/account.py` with `/v1/me` GET profile, `/v1/me` PATCH update name/email, `/v1/kyc` POST upload data (saves all KYC cols + kyc_status=submitted + writes AuditLog), `/v1/kyc` GET current status.
- **FR-9 Keys Router (Session)**: New `routers/keys.py` (session Depends) + works with Bearer key too for API. List/Create/Revoke/Rotate. Create shows account_scope radio disabled for Individual; enforces `Plan.max_keys_per_store`.
- **FR-10 Webhooks Router (Session)**: New `routers/webhooks.py`. List/Create/Update/Delete endpoints. Sign test event button that POSTs Stripe-style `t=<ts>,v1=<hmac>` signature to endpoint URL with `compare_digest` constant-time + 300s max age verification (documented).
- **FR-11 Billing Router**: New `routers/billing.py`. `GET /v1/billing/plans` returns public plans matrix (matches CutLuy). `POST /v1/billing/change-plan` → validates, changes PlanSubscription, auto-switches `account_type=business` if upgrading from Starter→Growth, writes AuditLog.
- **FR-12 Test-Mode Bypass**: Payment created with key.mode=test. Creates W1 dedup job → after 5s, W1 worker process() marks_paid (no Bakong/PayWay network call). Payment.attempt_history list contains one JSON attempt entry.
- **FR-13 Frontend Next.js User Portal**: App 2 (per EDD §11). pnpm workspace `web/user` + `web/admin` (admin M2 empty placeholder) + `web/shared` component library. Page list matches BRD §7.2: Login → Dashboard Overview → Stores (list/create wizard 3-step destination ABA/Paste PayWay Link/Bank account Bakong derivation Style B) → Payments (list + filter + detail KHQR card + timeline) → Keys → Webhooks → Settings (profile tab / KYC tab / Billing plan table + upgrade). All pages use Design Tokens `theme.ts` values.
- **FR-14 Khmer-First + Dark Mode Enforcement**: Khmer-first text on Individual pages and public `/pay/{id}`. Individual user pages → `showThemeToggle()` returns FALSE → dark switcher completely hidden. Business users see dark mode toggle.
- **FR-15 Sign-out + Session Expiry**: `/auth/signout` clears httpOnly cookie. Session JWT valid 24h. Impersonation session TTL (for M2 admin) 30m max allowed structure prepared in claims.
- **FR-16 mark_paid Idempotency + Dedup**: Calling mark_paid twice returns success without re-writing. paid_at set ONCE. Events not duplicated. webhook POST already has `uq_event_endpoint` unique.
- **FR-17 Attempt History JSON List**: W1.process append attempt JSON `{source, ts, aba_signals, matched_amount, status_reason}` to `Payment.attempt_history` NEW JSON column.
- **FR-18 KYC Soft/Hard Block Logic**: INDIVIDUAL accounts — 10th live txn → KYC banner shown, but payments still work (soft gate). BUSINESS accounts — KYC status=approved + kyc_live_blocked=False required for live payments; without it, API returns 402 "KYC required".

## Non-Functional Requirements
- **NFR-1 Zero Logic Rewrite for Scale**: Worker W1.process code identical running in-process monolith, Redis CLI worker, or k8s microservice pod. ONLY deployment changes (env var + CLI args).
- **NFR-2 No New `while True` Loops**: After refactor, `grep -R "while True" src/` → should return ONLY workers base.py transport loop (one canonical run loop), not scattered loops.
- **NFR-3 Design Token 100% Coverage**: Every CTA button uses radius=8, shadow=violet glow `0 7px 16px rgba(105,87,245,0.2)`. No custom hex constants in components; everything imported from theme.ts.
- **NFR-4 Observability Trace Header**: Every FastAPI response header includes `X-ChmabaPay-Trace: <uuid>`. Middleware sets it. W1/W2 attempts logged with same trace_id if originated from HTTP request.
- **NFR-5 SSR TTLCache**: ABA SSR page fetches cached 30s, maxsize 2000 keys per BRD reverse-engineering cache hierarchy (EDD §7.2).
- **NFR-6 Concurrency Cap**: W1 PaymentDetection loop uses Semaphore concurrency 20. W2 uses concurrency 30. Configurable as env vars WORKER_W1_CONCURRENCY etc.
- **NFR-7 Pytest Green After Changes**: Run `pytest -x` → existing tests still pass. Tests added for new code.
- **NFR-8 Ruff Lint Pass**: `ruff check src/chmabapay` clean exit (no new lint issues).

## Constraints
- **Technical**: Backend = FastAPI monolith (single deploy). Database = SQLAlchemy async ORM with current DB engine. Frontend = Next.js 13+ App Router + pnpm workspaces. No microservices for Phase 1.
- **Business**: Two account types ONLY. Individual vs Business. Private LTD + SaaS both = Business (different plan tier gate). User explicitly corrected twice; CANNOT introduce 3rd enum value.
- **Dependencies**: Google OAuth credentials in settings (env var). Auth lib choice: use httpx-based OAuth manually or `fastapi-sso` (confirm installed; install new package only if needed, document in pyproject).
- **Brand Compliance**: Brand palette, radii, fonts exact match chmaba.com extract. Design Tokens doc `theme.ts` copy-paste code used VERBATIM in frontend. No custom radius >12.

## Assumptions
1. Google OAuth credentials configured via env vars (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI). Assumed user provides after implementation (M1 code works with configured or falls back to dev login in enable_dev_gateway).
2. Frontend runs on separate port during development; CORS allow_origins * is kept for dev; production narrowed later.
3. pnpm is package manager choice (matches Next.js 13 workspaces standard). If not installed on developer machines, npm/pip install pnpm is acceptable one-time step.
4. Dev/test uses sqlite or current DATABASE_URL; production Postgres. Models migrations use alembic or use current `create_all()` pattern with explicit column add steps + seed.
5. KYC files stored locally first (disk); S3 signed URLs Phase 2. Code abstracts upload path behind `kyc_storage_upload(file)` helper so Phase 2 S3 swap is one function change.

## Open Questions
- [x] KYC file storage: Confirm S3 vs local disk for Phase 1 (currently assumed local, helper abstraction ready). → resolved: moot — KYC was dropped entirely, so no KYC file storage exists (supabase/migrations/6-drop-kyc.sql drops every identity-document column and the plan-level KYC gate).
- [x] Auth lib: Prefer `fastapi-sso` vs manual httpx OAuth2 flow (currently assumed minimal manual httpx for less deps, can install if desired). → resolved: the manual httpx OAuth2 flow shipped — `src/chmabapay/routers/auth.py` google_login/google_callback call Google's endpoints with `httpx.AsyncClient`; `fastapi-sso` was never adopted.
- [x] Frontend UI library: shadcn/ui + Tailwind vs custom styled-components (per DESIGN_TOKENS theme.ts, likely shadcn/ui + Tailwind most consistent). → resolved: Next.js App Router + Tailwind CSS with hand-built components in `web/shared` and `web/landing` — `web/landing/package.json` lists `tailwindcss` but no shadcn/ui and no styled-components.

---

## Acceptance Criteria

### AC-1: QueueTransport ABC + InProcessTransport full implementation
- **Type**: `rule`
- **Given**: Clean project with new workers/ package
- **When**: `from chmabapay.workers import QueueTransport, InProcessTransport, Worker; from chmabapay.workers.w1 import PaymentDetectionWorker` imports succeed
- **Then**: InProcessTransport instance has `enqueue(job)`, `dequeue(queue_name, n)`, `mark_done(job_id, success, result)`, `metrics()` methods. enqueue dedup check by dedup_key; duplicate enqueue returns silently (no error).
- **Pass Condition**: Python imports + unit test run: create 2 identical jobs with same dedup_key → len(dequeue 10) returns 1.
- **Evidence**: `pytest tests/test_workers_queue.py -v` passes dedup test.

### AC-2: All existing while True loops refactored into Worker.process(Job)
- **Type**: `rule`
- **Given**: Codebase after M1.4-M1.6 refactor
- **When**: Run `Get-ChildItem src -Recurse -Include *.py | Select-String -Pattern "while True" | Select-Object Path, LineNumber, Line` on Windows or equivalent
- **Then**: Only ONE occurrence remains: the canonical `transport.run()` loop in a single workers base.py runner file. Zero occurrences in webhooks.py; zero occurrences in bakong.py status_reconciler.py.
- **Pass Condition**: Scatter loops count == 0; lifespan tasks[] entries replaced with Worker.start(transport) calls
- **Evidence**: Grep command output ascii in task completion evidence.

### AC-3: Enqueue-on-write for create_payment + Event insert
- **Type**: `rule`
- **Given**: Running dev server with enable_dev_gateway=True, test mode API key
- **When**: `POST /v1/payments` creates payment → commits → no other trigger
- **Then**: Within 200ms, InProcessTransport queue `payments.detection` length becomes 1 with job dedup_key = payment_public_id. Second POST same idempotency → same dedup key → job count still 1 (not 2).
- **Pass Condition**: Test key payment → 5s later payment.status=paid via W1 job.
- **Evidence**: Dev rail sink test case; or test calling services.payments.create_payment then assert mark_paid event fired.

### AC-4: Account table new columns + default values
- **Type**: `rule`
- **Given**: Fresh DB with new models applied
- **When**: Sign up creates Account row
- **Then**: All BRD §2 columns exist; defaults: account_type='individual', kyc_status='none', kyc_live_blocked=True, saas_sub_merchants_enabled=False, whitelabel_enabled=False, is_platform_admin=False. KYC cols nullable where expected; required KYC Business cols NULL for Ind.
- **Pass Condition**: `pytest tests/test_models_account.py` asserts all columns present + defaults
- **Evidence**: DB inspect columns list + pytest pass

### AC-5: Plan table seed (4 plans with feature gates)
- **Type**: `rule`
- **Given**: Seed function called `seed_default_plans(session)`
- **When**: Query Plan count after seed
- **Then**: 4 rows with codes=starter/growth/scale/enterprise. Starter allowed_account_types=['individual']; Growth allow_account_scope_keys=True; Scale allow_saas_sub_merchants=True; Enterprise priority_support=True. Max_stores: Starter=5, Growth=50, Scale=500, Enterprise=null (unlimited).
- **Pass Condition**: Query after seed returns 4 correct rows.
- **Evidence**: Select * from plans query output.

### AC-6: Plan enforcement: max_stores cap + Individual account-scope key rejection
- **Type**: `rule`
- **Given**: Ind account with Starter plan. Already created 5 stores (at max_stores=5 cap).
- **When**: Attempt create 6th store
- **Then**: HTTP 400 {"detail": "Upgrade to Growth plan to add more than 5 stores."}. Same account tries create api_key scope=account → 400 {"detail": "Individual accounts can only create store-scoped keys. Upgrade to Growth plan to unlock shared keys."}
- **Pass Condition**: Two HTTP 400 responses as specified.
- **Evidence**: pytest httpx TestClient requests.

### AC-7: mark_paid increments PlanLedgerEntry atomically
- **Type**: `rule`
- **Given**: Account with PlanLedgerEntry row for current month
- **When**: Two concurrent async mark_paid calls for two different payments (100 + 200 cents)
- **Pass Condition**: Final total_volume_cents += 300; entry total_payments_count += 2. No race (UPDATE col = col + val SQL).
- **Evidence**: pytest test with asyncio.gather two concurrent mark_paid → assert counter final state.

### AC-8: Google OAuth + Session signin/signout
- **Type**: `rule`
- **Given**: Valid Google OAuth creds (or dev fake login if dev gateway enabled)
- **When**: User clicks Login with Google → redirected → callback verifies token → sets httpOnly JWT cookie session
- **Then**: `GET /v1/me` returns 200 with account.id matching. Cookie: Secure=True if not localhost, HttpOnly=True, SameSite=Lax, Max-Age 86400. POST /auth/signout returns Set-Cookie session deleted. Subsequent /v1/me returns 401.
- **Pass Condition**: 4 requests flow as described.
- **Evidence**: httpx TestClient test in tests/test_auth.py.

### AC-9: KYC submit (session) saves cols + writes AuditLog
- **Type**: `rule`
- **Given**: Logged-in session account
- **When**: POST /v1/kyc with Business KYC payload
- **Then**: Account.company_name_registered, company_registration_number, director_name etc saved; kyc_status='submitted'; kyc_live_blocked=True (still needs admin approve). AuditLog 1 row with action='kyc.submitted', target_type='Account', target_id=account.id, admin_account_id=account.id (self-submit).
- **Pass Condition**: row queries return correct values.
- **Evidence**: test file assertions.

### AC-10: Keys router works with BOTH Bearer key auth AND session auth
- **Type**: `rule`
- **Given**: Existing API key ck_xxx OR session cookie
- **When**: GET /v1/keys with either header Authorization: Bearer ck_xxx OR session cookie
- **Then**: Both return 200 list of keys visible within their scope (bearer store-scoped key sees only its store; account scoped sees all account; session sees all under their account).
- **Pass Condition**: Two auth modes, both 200.
- **Evidence**: pytest httpx tests.

### AC-11: Webhook test event signs + verifies
- **Type**: `rule`
- **Given**: Webhook endpoint created with secret_key = "test-secret"
- **When**: Click "Send test event" (POST /v1/webhooks/1/test)
- **Then**: Outbound POST contains header `ChmabaPay-Signature: t=<10-digit-ts>,v1=<64-hex-hmac>`. Server verification with same secret using constant-time compare + 300s clock skew → VALID. 301s old timestamp with modified payload → INVALID.
- **Pass Condition**: Signature verify unit tests 3 scenarios: valid, ts expired, payload tampered.
- **Evidence**: pytest tests/test_webhooks_signature.py.

### AC-12: Billing change-plan auto-switches Individual→Business
- **Type**: `rule`
- **Given**: account_type='individual' with Starter plan
- **When**: POST /v1/billing/change-plan {"plan_code": "growth"}
- **Then**: New subscription: plan_id=Growth, status='trial' (trial_days=14). Account.account_type='business' AUTOMATICALLY flipped. Keys page now shows account_scope radio enabled. AuditLog action='plan.changed'.
- **Pass Condition**: DB rows as described + type field flipped.
- **Evidence**: pytest DB assertions.

### AC-13: Test mode bypass mark_paid after 5s (no Bakong API needed)
- **Type**: `rule`
- **Given**: Key.mode='test'
- **When**: Create payment with that key
- **Then**: Immediately → status=pending. After 5.5s wait → status=paid, paid_at set (approx now). attempt_history list length 1, attempt[0].source='test-mode-fake-delay'
- **Pass Condition**: Two status calls 6s apart correct.
- **Evidence**: pytest async with 7s total timeout.

### AC-14: Payment attempt_history W1.process appends JSON attempts
- **Type**: `rule`
- **Given**: Payment pending status
- **When**: W1.process(job) runs reconcile + 3 failed before success
- **Then**: attempt_history length 4; each entry has keys {source, attempt_at, matched_amount_cents, signals, note}. Paid entry last {source='bakong_verify_receipt tier 3', matched_amount_cents=exact}
- **Pass Condition**: JSON keys present + length correct.
- **Evidence**: unit test with mock reconciler returning failures then success.

### AC-15: Next.js Portal page set exists + Design tokens applied
- **Type**: `rubric`
- **Dimension**: Dashboard UX conformance to BRD §7 + DESIGN_TOKENS
- **Scale**: 1-5
- **Anchors**: 1 = missing > 3 pages, hardcoded colors; 3 = all pages present but ~3 token violations (radius>12, custom hex, violet wrong hex); 5 = All 7 page groups exist + implemented via shared components + theme.ts 100%, no hardcoded hex colors.
- **Pass Threshold**: >= 4
- **Evidence**: Frontend TSX scan for hardcoded #hex values vs imported from theme; page route tree listing.

### AC-16: Individual pages: dark mode toggle HIDDEN (UX Law L6)
- **Type**: `rule`
- **Given**: account_type='individual' logged in session context
- **When**: Layout calls showThemeToggle(account_type='individual')
- **Then**: Returns FALSE. Theme toggle component (moon icon top-right or switcher) completely absent from rendered DOM. Not display:none — actually not rendered at all (so inspect element can't accidentally activate either). For account_type='business' same component returns TRUE and rendered.
- **Pass Condition**: Two renders snapshot diff. Business render has toggle HTML tag; Individual render missing it.
- **Evidence**: Jest/Vitest snapshot test output OR curl /dashboard with both sessions.

### AC-17: Khmer-first bilingual labels on trust pages (UX Law L1)
- **Type**: `rule`
- **Given**: Individual user, page = /stores/new wizard, locale default = km
- **When**: CTA button text inspected
- **Then**: First line ចុះបង្កើតហាងថ្មី + second line (muted gray, smaller) "Create a store" (English subtitle). Not the other way around. English-first allowed ONLY for Business account on keys/webhooks pages.
- **Pass Condition**: Wizard Khmer-first on trust pages; Business tech pages EN first allowed.
- **Evidence**: Screenshots of wizard + Business keys page side by side.

### AC-18: Exit Demo Checklist — 5 flows (BRD §13.0.1)
- **Type**: `rule`
- **Given**: Running backend + frontend with seeded starter plan, sink server running for webhook test, dev test key mode available.
- **When**: Run Milestone 1 demo checklist 5 flows in order.
- **Then**: Every sub-item in BRD demo works: (1) Google login → onboarding → picks Ind. (2) Creates store with PayWay link → destination valid. (3) Test key payment → 5s auto mark_paid → webhook POST arrives to sink. (4) Submit KYC → status 'submitted' (admin queue placeholder shown M1, approve M2). (5) Upgrade Starter→Growth → auto business_type → shared key option appears.
- **Pass Condition**: 5/5 flows complete in a single run-through recording.
- **Evidence**: Demo command output/video timestamp OR integration test for each of 5 flows.

### AC-19: Observability — Trace ID header every response
- **Type**: `rule`
- **Given**: ANY http endpoint
- **When**: GET /health (even!)
- **Then**: Response header X-ChmabaPay-Trace present, format = UUID4 (36 chars). Same value logged in structured log for that request.
- **Pass Condition**: curl -i /health shows header.
- **Evidence**: curl command output.

### AC-20: Pytest + Ruff clean
- **Type**: `rule`
- **Given**: End of implementation.
- **When**: Run `pytest -x` → exit 0; Run `ruff check src/chmabapay` (all python files) → exit 0. No new dependencies added without updating pyproject.toml.
- **Pass Condition**: Zero failing tests; zero ruff errors that are newly introduced.
- **Evidence**: Full terminal output lines.
