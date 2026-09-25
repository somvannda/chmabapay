# ChmabaPay M1 — Implementation Task Queue

Priority order (EDD §15 M1.3 first — workers scaffold BEFORE writing loops/routers):
T1 → T2 → T3 → T4+T5 (parallel OK) → T6 → T7 → T8 (T8a b c parallel) → T9 → T10 → T11.

---

## Task 1: `src/chmabapay/workers/` package scaffold — QueueTransport ABC + InProcessTransport + Worker base + W3 stub
- **Status**: `complete`
- **Verified**: `src/chmabapay/workers/__init__.py` exports QueueTransport/InProcessTransport/Worker/Job/JobStatus, `workers/inprocess.py` InProcessTransport.enqueue dedups via TTLCache, and tests/test_queue_redis.py::test_a_duplicate_dedup_key_is_not_enqueued passes (redis.py is now a full RedisTransport and w3_billing.py a real BillingInvoiceWorker, i.e. past the M1 stubs).
- **Priority**: high
- **Depends On**: None (Milestone 1.3 — START HERE)
- **Description**:
  - Create `src/chmabapay/workers/__init__.py` — exports: `QueueTransport`, `InProcessTransport`, `Worker`, `Job`, `JobStatus`.
  - `base.py` — QueueTransport abstract base class with 4 methods + `metrics()` + `run(workers, stop_event)` canonical loop ONLY (so `while True` lives once here — meets AC-2 scatter loop count == 0).
  - `inprocess.py` — full InProcessTransport implementation per EDD §5.2 (asyncio.Queue per queue_name, in-memory dedup dict with TTLCache 1h).
  - `redis.py` — SKELETON ONLY (comments with EDD §5.3 transport placeholder structure — no actual Redis calls).
  - `job.py` — `Job` dataclass: `job_id: UUID`, `queue_name: str`, `dedup_key: Optional[str]`, `payload: dict`, `created_at`, `attempts: int`, `max_attempts: int = 10`.
  - `w3_billing.py` — W3 BillingInvoiceWorker stub (process() logs + returns success — M2 implements).
- **Acceptance Criteria Addressed**: AC-1 (full ABC impl), NFR-1 zero-rewrite scalability (same process() works in any transport), NFR-2 no new while True loops (only base.py canonical one)
- **Test Requirements**:
  - `rule` TR-1.1: `from chmabapay.workers import *` import succeeds clean → evidence: pytest import test passes.
  - `rule` TR-1.2: InProcessTransport.enqueue() dedup works → enqueue job A with dedup_key='X'; enqueue job B with same dedup_key='X' → len(dequeue n=10) returns 1 job (not 2). Evidence: tests/workers/test_inprocess_dedup.py pass.
  - `rule` TR-1.3: Worker base.run_forever(stop_event) works. Spin up 1 DummyWorker that appends to list; start, wait 100ms, set stop → run_forever returns cleanly. Evidence: pytest test.
  - `rubric` TR-1.4: ABC adherence; scale: 1-5. 1 = interface methods missing, 3 = all 4 methods present but params mismatch EDD, 5 = 100% signature match EDD §5.1 abstract signatures. Threshold >= 4. Evidence: code diff of base.py vs EDD snippet.
- **Notes**: M1.3 exact from EDD §15.

## Task 2: Implement W1 PaymentDetectionWorker + Refactor bakong_verify_loop → process(job)
- **Status**: `complete`
- **Verified**: `workers/w1_payment_detection.py` PaymentDetectionWorker.process uses asyncio.Semaphore(20) and a 30s/2000-key TTLCache and appends to Payment.attempt_history via _append_attempt, no `bakong_verify_loop` remains in `webhooks.py`, and tests/test_payments.py::test_every_detection_attempt_is_recorded_not_only_the_first covers it.
- **Priority**: high
- **Depends On**: T1 (workers scaffold in place)
- **Description**:
  - File: `src/chmabapay/workers/w1_payment_detection.py` class `PaymentDetectionWorker(Worker)`
  - W1.process(job): dedup on payment_public_id → extract payment → call `reconcile_payment(payment)` (existing reconciler in status_reconciler.py) → if transitioned to paid: `mark_paid()` + append attempt_history. If not paid, retry with exponential backoff 2^n capped 600s.
  - Add NEW JSON column to `Payment` model: `attempt_history: Mapped[list | None] = mapped_column(JSON)` per AC-14.
  - Wrap today's `bakong_verify_loop` logic flow into W1; delete the raw loop function in `webhooks.py`.
  - Use Semaphore concurrency 20 per NFR-6.
  - SSR TTLCache 30s / 2000 keys for ABA SSR fetch (per NFR-5) — decorate fetch_payment_status with cachetools TTLCache.
- **Acceptance Criteria Addressed**: AC-2 (loops → Worker), AC-3 (enqueue-on-write), AC-14 (attempt_history JSON append), NFR-5 SSR cache, NFR-6 concurrency cap
- **Test Requirements**:
  - `rule` TR-2.1: Delete webhooks.bakong_verify_loop function; grep "bakong_verify_loop" should find only worker class (not a raw loop). Evidence: grep output.
  - `rule` TR-2.2: W1.process with mock reconciler returning 3 fail then success → Payment.attempt_history len == 4; each entry has keys {source, attempt_at, matched_amount_cents, signals, note}. Evidence: pytest mock test.
  - `rule` TR-2.3: Semaphore 20 cap. Monkey-patch reconcile to sleep(50ms); spawn 50 concurrent W1 jobs → max parallel observed at any instant ≤ 22 (with 2 flake buffer). Evidence: pytest concurrency counter test.
  - `rule` TR-2.4: TTLCache decorator present on fetch_payment_status or wrapper; 2 calls within 10s with same payway URL → 1 HTTP call (not 2). Evidence: mock httpx count test.

## Task 3: Implement W2 WebhookSenderWorker + Refactor webhook_loop → process(job)
- **Status**: `complete`
- **Verified**: `workers/w2_webhook_sender.py` WebhookSenderWorker.process signs and retries deliveries using `security.py` sign_payload/verify_signature (hmac.compare_digest) and `webhooks.py` delivery_headers, no `webhook_loop` remains in `webhooks.py`, and tests/test_payments.py asserts verify_signature on the emitted X-ChmabaPay-Signature.
- **Priority**: high
- **Depends On**: T1
- **Description**:
  - `src/chmabapay/workers/w2_webhook_sender.py` class `WebhookSenderWorker(Worker)`
  - process(job): pulls EventDelivery row, signs payload with Stripe-style `t=<ts>,v1=<hmac>` constant-time compare secret → POST → parse response → success = set status SUCCESS; fail = increment attempts, next_attempt_at = now + 2^n seconds capped 3600s, up to max 10 attempts.
  - Delete raw webhook_loop in webhooks.py. Keep sign_payload pure function (move to module security.py or keep in webhooks module if already there).
  - uq_event_endpoint dedup already present in model (AC-3 fan-out dedup OK).
- **Acceptance Criteria Addressed**: AC-2 (loop gone), AC-3 (fan-out enqueue), AC-11 (sign/verify signature)
- **Test Requirements**:
  - `rule` TR-3.1: grep "webhook_loop" → only Worker references, no raw while True function. Evidence: grep.
  - `rule` TR-3.2: Signature generate + verify 3 cases: (a) valid payload + valid ts (299s old) → VALID. (b) ts 301s old → INVALID (expired). (c) payload modified by 1 byte → INVALID. Uses hmac.compare_digest constant time (Python builtin). Not string ==. Evidence: tests/signature unit test.
  - `rule` TR-3.3: Retry backoff schedule. 0 failures → next_attempt_at = None (success). 1st fail → +2s. 5th fail → +64s. 10th fail → set status=final FAILED, no more retries. Evidence: pytest assertions on next_attempt_at.

## Task 4: Implement W4 ExpirySweeperWorker + Refactor expiry_loop
- **Status**: `complete`
- **Verified**: `workers/w4_expiry_sweeper.py` ExpirySweeperWorker sweeps via services/payments.py expire_due_payments, is enqueued every 60s with the minute-granularity key expiry_heartbeat_dedup_key in `workers/runtime.py` w4_tick, no `expiry_loop` remains, and tests/test_settlement_lifecycle.py asserts the EVENT_EXPIRED row.
- **Priority**: medium
- **Depends On**: T1
- **Description**:
  - `src/chmabapay/workers/w4_expiry_sweeper.py` class `ExpirySweeperWorker(Worker)`
  - process(job): batched SQL UPDATE payments SET status='expired' WHERE status='pending' AND expires_at < now() LIMIT 500; per payment emit event=payment.expired → enqueue W2 per endpoint.
  - Delete raw expiry_loop. Add 60s heartbeat recurring: every 60s enqueue W4 job with dedup_key='expiry-sweep-{YYYYMMDD-HH-MM}' so duplicates not run twice.
- **Acceptance Criteria Addressed**: AC-2 (loop gone), AC-3 (enqueue events)
- **Test Requirements**:
  - `rule` TR-4.1: grep "expiry_loop" raw reference count == 0. Evidence: grep.
  - `rule` TR-4.2: Insert 3 pending payments with expires in past; run W4 once → 3/3 status=expired. Events rows 3x payment.expired. Evidence: pytest DB test.

## Task 5: Wire main.py lifespan — Workers with InProcessTransport; Delete 3 raw loop lines
- **Status**: `complete`
- **Verified**: `src/chmabapay/main.py` lifespan calls `workers/runtime.py` start_workers (no raw asyncio.create_task loop lines remain) and trace_header_middleware sets X-ChmabaPay-Trace; tests/test_observability.py::test_a_request_binds_its_trace_id_for_the_logs_inside_it asserts the header.
- **Priority**: high
- **Depends On**: T1 (scaffold), T2 (W1), T3 (W2), T4 (W4)
- **Description**:
  - Rewrite `create_app` lifespan: transport=InProcessTransport(). workers=[PaymentDetectionWorker(), WebhookSenderWorker(), BillingInvoiceWorkerStub(), ExpirySweeperWorker()].
  - transport.start_all(). At yield shutdown: transport.stop_all(grace=5s).
  - Delete the 3 `asyncio.create_task(webhook_loop / expiry_loop / bakong_verify_loop)` lines completely.
  - Add middleware `X-ChmabaPay-Trace` UUID response header per AC-19 / NFR-4.
- **Acceptance Criteria Addressed**: AC-2 (all loops gone), AC-19 (trace header), NFR-2 (zero scattered loops)
- **Test Requirements**:
  - `rule` TR-5.1: `while True` grep across src/chmabapay/ (not tests) → at most ONE match: workers.base.py canonical transport.run() loop. Count == 1 passes. Count > 1 FAIL. Evidence: PowerShell grep output.
  - `rule` TR-5.2: `GET /health` → response header "X-ChmabaPay-Trace" present with valid UUID4 regex (8-4-4-4-12 hex). Evidence: curl output.

## Task 6: DB Schema — Account new cols + 5 new tables (Plan 4 models + AuditLog) + migrate/create_all + seed_default_plans
- **Status**: `partial`
- **Verified**: The 5 tables Plan/PlanSubscription/PlanInvoice/PlanLedgerEntry/AuditLog and Payment.attempt_history still exist in `models.py`, but the Account account_type/KYC/saas cols were removed (alembic/versions/0008_drop_account_type.py, supabase/migrations/6-drop-kyc.sql, 4-merge-sub-merchants-into-stores.sql) and the 4-plan matrix was replaced by free/starter/pro in `db.py` seed_default_plans (supabase/migrations/7-retire-dead-plan-and-store-config.sql), with create_all replaced by `db.py` ensure_schema + alembic.
- **Priority**: high
- **Depends On**: None (schema independent; can run parallel to T1 if team >1; alone run after T1-T5 since workers scale first per EDD)
- **Description**:
  - Edit Account class in `models.py` → add ALL cols from BRD §2 table: `account_type: Mapped[str] = mapped_column(String(16), default="individual")`; KYC cols (khmer_id_*, company_*, director_*, vat_tin_number), kyc_status, kyc_approved_at, kyc_reject_reason, kyc_live_blocked bool default True, saas_sub_merchants_enabled bool default False, whitelabel_enabled bool default False, is_platform_admin bool default False.
  - Add Payment.attempt_history JSON column (T2 added this; reconcile if done there first).
  - Create 5 NEW classes: Plan, PlanSubscription, PlanInvoice, PlanLedgerEntry, AuditLog per BRD §8.3 exact field names/types.
  - Create `db.seed_default_plans(session)` helper that UPSERTs 4 plans by code. Starter: max_stores=5, allow_account_scope_keys=False, allow_saas=False, allow_whitelabel=False. Growth: max_stores=50, allow_account=True, saas=False, whitelabel=False, price=$29/mo. Scale: max_stores=500, max_sub_merchants=500, saas=True, whitelabel=True, price=$99/mo. Enterprise: all nulls for maxes (unlimited), priority_support=True, custom price.
  - Modify db.create_all() so after tables created → runs seed_default_plans IF Starter row count == 0 (idempotent).
  - Modify auth flow NEW signups: auto-create PlanSubscription to Starter trial.
- **Acceptance Criteria Addressed**: AC-4 (Account cols + defaults), AC-5 (4 plans seed correct feature gates), AC-7 (PlanLedger counter)
- **Test Requirements**:
  - `rule` TR-6.1: Fresh DB create_all → inspect Account columns; all BRD §2 new cols present. Default: account_type='individual' for new row. Evidence: DB inspect test + pytest.
  - `rule` TR-6.2: seed_default_plans() called once → Plan count == 4; 4 unique codes starter/growth/scale/enterprise. Query for each feature gate correct values. Called twice → count still 4 (upsert idempotent). Evidence: DB query assertions.
  - `rule` TR-6.3: New account signup → PlanSubscription row created for plan_code=starter status=trial. Evidence: pytest auth.signup → subscription row.

## Task 7: mark_paid atomic counter + KYC gate enforcement + Plan gates
- **Status**: `partial`
- **Verified**: `services/payments.py` mark_paid records usage via _record_plan_ledger_entry (idempotent INSERT ... ON CONFLICT DO NOTHING, not the described atomic UPDATE) and `routers/stores.py` _enforce_max_stores caps stores by plan, but the KYC soft/hard gates and the Individual account-scope-key rejection no longer exist (supabase/migrations/6-drop-kyc.sql; account_type dropped by alembic/versions/0008_drop_account_type.py, so `routers/keys.py` create_key always writes KEY_ACCOUNT_SCOPE).
- **Priority**: high
- **Depends On**: T6 (tables exist)
- **Description**:
  - `mark_paid()` function → wrap in DB tx; execute: `UPDATE plan_ledger_entries SET total_payments_count = total_payments_count + 1, total_volume_cents = total_volume_cents + :amount WHERE account_id = :aid AND period_month = :month RETURNING *` (atomic +1 UPDATE, not SELECT-then-UPDATE for no race). Period month row auto-create if missing with INSERT ON CONFLICT DO NOTHING first.
  - KYC gate AC-18: Business account kyc_live_blocked=True AND mode=live → API create_payment returns 402 "KYC required to accept live payments. Upload KYC docs or use test keys." Individual account soft gate: 11th+ payment → dashboard warning banner, but still allows creation (store banner in UI BE returns warning header `X-ChmabaPay-Warning: kyc-gate-soft-reached`).
  - Plan gate enforcement AC-6 in routers/stores.py create store endpoint: check PlanFeature max_stores first; return 400 if over cap for current plan.
  - Enforce Individual cannot create account-scoped keys in routers/keys.py (AC-6): if account_type == 'individual' and scope=='account' → 400 upgrade required message.
- **Acceptance Criteria Addressed**: AC-6 (2 plan enforcements), AC-7 (atomic counter), AC-18 (KYC soft/hard gates)
- **Test Requirements**:
  - `rule` TR-7.1: Concurrent 2 mark_paid asyncio.gather with same account → counter final=2, amount final=sum(amounts). No 1+1=1 bug. Evidence: pytest concurrent test.
  - `rule` TR-7.2: Business live account, kyc_live_blocked=True → create_payment with mode=live key → HTTP 402 detail as specified. Same with test key → 200 OK. Individ live payment 1-10 → all 200 OK; 11th → response header X-ChmabaPay-Warning present as specified; body still 200. Evidence: httpx tests.
  - `rule` TR-7.3: Ind with 5 stores → create 6th → 400 upgrade message. Same for account_scope key → 400. Evidence: tests.

## Task 8a: Router Auth — Google OAuth + Session Cookie JWT + /auth/signout + `/api/v1/me` endpoints
- **Status**: `complete`
- **Verified**: `routers/auth.py` google_login/google_callback implement the Google OAuth flow, _make_session_jwt/_verify_jwt carry the httpOnly session cookie and get_current_session_account gates routes, /auth/signout and /_dev/login exist, and `/api/v1/me` is served by `routers/account.py` (the account_type JWT claim is gone per alembic/versions/0008_drop_account_type.py; TTL is settings.jwt_ttl_seconds).
- **Priority**: high
- **Depends On**: T6 (Account cols + subscription auto-created)
- **Description**:
  - New file: `routers/auth.py`. Google OAuth flow: `/auth/google/login` redirects, `/auth/google/callback` returns JWT in httpOnly cookie.
  - JWT claims: `sub=account.id`, `account_type`, `is_platform_admin`, `exp=now+86400`. Session JWT signed secret from settings JWT_SECRET_KEY (new config field).
  - Session Depends function: `get_current_session_account(request)` → reads cookie, verifies JWT, returns Account.
  - `/auth/signout POST` → Set-Cookie empty, expires epoch.
  - Include dev fake login if enable_dev_gateway=True: simple `/auth/_dev/login?email=x@gmail.com` signs in without Google for local dev.
  - Mount router in main.py include_router.
- **Acceptance Criteria Addressed**: AC-8 (sign/in out + session), AC-15 (TTL), AC-6/KYC later
- **Test Requirements**:
  - `rule` TR-8a.1: Dev login enabled → POST /auth/_dev/login → Set-Cookie header JWT session; Max-Age near 86400; HttpOnly flag present; SameSite=Lax present. GET /api/v1/me with that cookie → 200 account JSON. POST /auth/signout → cookie cleared. Next /api/v1/me → 401. Evidence: httpx TestClient test.
  - `rule` TR-8a.2: Manually forged JWT with other secret → 401 (verification works). Expired JWT → 401. Evidence: pytest.

## Task 8b: Routers Account + Keys + Webhooks (Session/Key Authed CRUD)
- **Status**: `partial`
- **Verified**: `routers/account.py` /api/v1/me GET+PATCH, `routers/keys.py` list/create/revoke/rotate and `routers/webhooks.py` CRUD + POST /{id}/test all exist, but the POST/GET /api/v1/kyc endpoints were removed by supabase/migrations/6-drop-kyc.sql.
- **Priority**: high
- **Depends On**: T8a (get_current_session_account defined), T6 (AuditLog table ready), T7 (plan gates in place)
- **Description**:
  - `routers/account.py`:
    - GET /api/v1/me → profile.
    - PATCH /api/v1/me → update name/email.
    - POST /api/v1/kyc → KYC payload → save ALL company/director/kyc cols; kyc_status='submitted'; AuditLog.write(action='kyc.submitted').
    - GET /api/v1/kyc → return current status + reject reason if any.
  - `routers/keys.py`:
    - GET /api/v1/keys (works for BOTH session auth AND Bearer key via existing resolve_key_context — merge both Depends with OR).
    - POST /api/v1/keys → create with scope. Plan enforcement from T7: Indiv scope=account 400.
    - POST /api/v1/keys/{id}/rotate → new prefix + revoke old; sets revoked_at.
    - POST /api/v1/keys/{id}/revoke → revoke now.
  - `routers/webhooks.py`:
    - GET / POST / PATCH / DELETE /api/v1/webhooks.
    - POST /api/v1/webhooks/{id}/test → builds payment.completed test event, signs with endpoint secret, POSTS to url; returns success/fail status + response body snippet to caller (max 500 chars in response).
  - Mount all 3 new routers in main.py.
- **Acceptance Criteria Addressed**: AC-9 (KYC submit + AuditLog), AC-10 (Dual auth modes keys), AC-11 (test sign+send)
- **Test Requirements**:
  - `rule` TR-8b.1: POST /api/v1/kyc business payload → db Account cols populated; kyc_status='submitted'. AuditLog count +1 with correct action/type/id. Evidence: pytest assertions.
  - `rule` TR-8b.2: GET /api/v1/keys with session cookie → 200 list; same with Bearer ck_xxx header → 200 filtered to scope of that key. Bad token → 401. Evidence: httpx tests.
  - `rule` TR-8b.3: POST /api/v1/webhooks/1/test with httpx mock POST intercept → request header ChmabaPay-Signature matches t=<ts>,v1=<hmac> format. Verify signature locally (secret=endpoint.secret_key) → VALID. Evidence: test mock httpx transport verify locally.

## Task 8c: Router Billing CRUD (plans list + change plan) + Test mode 5s delay bypass
- **Status**: `partial`
- **Verified**: `routers/billing.py` GET /api/v1/billing/plans and POST /api/v1/billing/change-plan exist and `workers/w1_payment_detection.py` _test_mode_bypass does the 5s test-mode mark_paid (source='test-mode-fake-delay'), but the Starter→Growth account_type='business' auto-switch was removed (alembic/versions/0008_drop_account_type.py; docs/production-readiness.md records the field was hardcoded False before the drop).
- **Priority**: high
- **Depends On**: T6 (Plan tables + seed), T7 (plan logic), T1-T5 (W1 ready so enqueue test mode job works)
- **Description**:
  - `routers/billing.py`:
    - GET /api/v1/billing/plans → public list, 4 rows; include feature gates.
    - POST /api/v1/billing/change-plan body: {plan_code}. Validates allowed transition (downgrade allowed immediately, refunds later M2). Creates new PlanSubscription row (or updates existing). New plan = Growth or higher AND account_type='individual' → auto-switch account_type='business' (AC-12). Writes AuditLog action='plan.changed'.
  - Test mode bypass AC-13: In W1 PaymentDetectionWorker.process(job), first check if payment.api_key.mode == 'test' (resolve key from payment store → owner account → keys → get mode from key used; OR simpler: add `api_key_mode` payload field to create_payment enqueue so W1 can see it directly without DB lookup). If mode='test', after 5 second asyncio.sleep → call mark_paid directly without Bakong/PayWay external fetch. attempt_history[0] = {source='test-mode-fake-delay'}.
- **Acceptance Criteria Addressed**: AC-12 (auto business type switch), AC-13 (test mode 5s delay), NFR-7 tests green
- **Test Requirements**:
  - `rule` TR-8c.1: Ind account POST change-plan plan_code=growth → PlanSubscription new plan_id=Growth; account_type='business' flipped; AuditLog row exists. Evidence: pytest assertions.
  - `rule` TR-8c.2: Create payment with test key. At t=+1s → status pending; at t=+6s → status paid; attempt_history[0].source == 'test-mode-fake-delay'. paid_at set. Event row payment.completed 1 row. Evidence: pytest async 8s window test.

## Task 9: Enqueue-on-write triggers in create_payment(), create_event(), insert Event
- **Status**: `complete`
- **Verified**: `routers/payments.py` _enqueue_detection enqueues payments.detection with dedup_key `detect:{public_id}` and `webhooks.py` enqueue_event fans out webhooks.send jobs with dedup_key `send:{event_id}:{endpoint_id}`; tests/test_queue_redis.py::test_a_duplicate_dedup_key_is_not_enqueued proves the dedup behaviour.
- **Priority**: high
- **Depends On**: T2 (W1), T3 (W2), T5 (transport accessible from app.state)
- **Description**:
  - Store transport on app.state.worker_transport at lifespan startup.
  - In services/payments.py create_payment() after commit → `app.state.worker_transport.enqueue(queue_name='payments.detection', dedup_key=f"detect:{payment.public_id}", payload={'payment_public_id': payment.public_id})`.
  - In event creation (mark_paid emits payment.completed, expiry emits expired, scanned event → after insert Event row → fan-out to all applicable endpoints (account + store scope with Golden Resolution Rule most-specific-wins); per endpoint call transport.enqueue queue='webhooks.send' job dedup_key=f"send:{event.id}:{endpoint.id}"; dedup OK because uq_event_endpoint UniqueConstraint exists so duplicate insert blocked at DB layer anyway — dedup_key matches that pattern).
- **Acceptance Criteria Addressed**: AC-3 (enqueue-on-write), NFR-1 scalability (zero polling-based loops)
- **Test Requirements**:
  - `rule` TR-9.1: Create payment → within 200ms transport.dequeue('payments.detection') len ≥ 1; dedup_key == detect:{public_id}. Second create same idempotency → dequeue len unchanged (dedup works). Evidence: test with InProcessTransport.dequeue.
  - `rule` TR-9.2: mark_paid → 2 webhook endpoints configured (account, store) → 2 jobs enqueued in 'webhooks.send'; distinct endpoint_id payload. uq_event_endpoint guarantee honored. Evidence: test.

## Task 10: Frontend — Next.js 13+ App Router scaffold + shared components library + 7 page groups
- **Status**: `partial`
- **Verified**: The App Router portal ships as `web/landing` with the dashboard/stores/payments/keys/webhooks/settings/billing page groups plus `web/shared` components (StatusBadge/DataTable/StepProgress/ModalSystem/KHQR/CopyField) and theme.ts/ux-laws.ts, but the spec's `web/user`+`web/admin` split changed (admin is a full app, not an empty placeholder), KYCUpload/the KYC settings tab are gone (supabase/migrations/6-drop-kyc.sql) and the account_type-conditional dark-toggle/Khmer rules are moot (alembic/versions/0008_drop_account_type.py).
- **Priority**: high
- **Depends On**: T8a (auth endpoints exist, can call API from frontend), AC-15 Khmer-first applied, AC-16 dark toggle logic, Design Tokens theme.ts from doc
- **Description**:
  - `web/` directory: pnpm workspaces three packages (per EDD §11): `web/user` (App 2 User Portal), `web/admin` (empty M1 placeholder for App 3 Admin), `web/shared` (shared components library + theme.ts + i18n + hooks).
  - `web/shared/styles/theme.ts`: COPY-PASTE VERBATIM from DESIGN_TOKENS_AND_UX_LAWS.md §10.1 TypeScript theme.ts code block (every hex constant, radius tokens, shadow tokens, fonts, showThemeToggle(), khmerFirst(), showTechyPanels() type guards included verbatim).
  - `web/shared/components/`: ChmabaLayout (sidebar nav, header with logo, dark mode toggle conditionally rendered), StatusBadge, KHQRDisplay, CopyField, DataTable, StepProgress (UX Law L4 explicit steps), ModalSystem, KYCUpload (drag-drop).
  - Pages in `web/user/app/`:
    - (auth) `app/login` → redirects to Google OR dev login if enable_dev_gateway.
    - (protected) `app/dashboard/page.tsx` Overview: 4 summary cards (This Month Paid, Success Rate %, Average time to paid, Total stores) + recent payments table.
    - `app/stores/` list, `app/stores/new` 3-step wizard (1 Basic info, 2 Destination pick ABA/bank/PayWay link with bank color chips UX Law L5, 3 Review+Create with explicit progress bar StepProgress).
    - `app/payments/` list with filters (date range, status). `app/payments/[id]` detail: KHQR card with Bakong teal glow shadow, amount big, status timeline (created → scanned → paid explicit L4), raw JSON gateway_status collapsed by default (techy panel hidden for Ind unless click expand).
    - `app/keys/` page: list, create modal. scope radio with account radio disabled + tooltip "Upgrade to Growth plan to unlock shared account-wide keys" if account_type=individual. Copy key reveal flow like Stripe (dots → full on reveal). curl example below create form; techy terminal dark bg if dark mode or Business.
    - `app/webhooks/` list, create modal. Signature playground tab: docs with code snippets 3 languages (Python/PHP/JS) as H3 tabs.
    - `app/settings/page.tsx` with 3 tabs: Profile (name/email), KYC (upload form 4 file fields), Billing (plans 4 cards, most popular lime card with lime green popularPlan halo shadow, choose CTA).
    - Public: keep existing hosted checkout `/pay/[id]` if exists, or create — trust-only design (UX L1/L2/L3/L4/L5 all).
  - Apply design tokens everywhere: Primary buttons all radius=8, violet shadow #6957F5 0 7px 16px. Cards radius=12. No radius >12 anywhere (L3 law).
  - On EVERY trust-page component for Individual: Khmer-first text via `khmerFirst(true)` wrapper helper. On techy pages (keys/webhooks) Business: English-first allowed, dark toggle rendered via showThemeToggle(account_type).
- **Acceptance Criteria Addressed**: AC-15 (pages + tokens), AC-16 (no dark toggle for Ind — L6), AC-17 (Khmer-first L1), FR-13/14 (page list + Khmer rules)
- **Test Requirements**:
  - `rule` TR-10.1: TypeScript compile `pnpm tsc --noEmit web/*` exit 0. theme.ts applied verbatim; grep for `0 7px 16px 0 rgba(105,87,245,0.20)` (primary button shadow) string count ≥ 1 in shared components code (present in button class). No hardcoded hex #6957F5 anywhere except theme.ts. Evidence: ts build output + grep.
  - `rule` TR-10.2: Radius L3 law: all component className + style values scan → max value of 'rounded-X' or pixel radius = 12 (card). No component has 14/16/20/24 pixel radius. Evidence: grep scan.
  - `rule` TR-10.3: HTML output of `/stores/new` wizard with Individual session → contains Khmer text ON FIRST LINE of heading/CTAs. `/settings/kyc` page header Khmer-first. /keys page heading English-first (techy page allowed). Evidence: curl output HTML.
  - `rule` TR-10.4: `/dashboard` page layout HTML with Individual session → `<button class*="theme"`, `data-theme-toggle`, or equivalent dark-mode component DOM count == 0. Same with Business → count ≥ 1. Evidence: snapshot or grep output.
  - `rubric` TR-10.5: Overall UX conformance to BRD §7.2 / DESIGN_TOKENS doc. 1=missing 3+ pages; 3=all pages there but 3+ token violations; 5=all pages perfect. Threshold >= 4. Evidence: screenshot audit with checklist of pages + tokens.

## Task 11: Final — Exit Demo 5 flows run-through + Pytest Ruff full run verification
- **Status**: `complete`
- **Verified**: .github/workflows/ci.yml runs `ruff check .`, `alembic upgrade head`+`alembic check` and `pytest` on every push (docs/production-readiness.md P0-4 gate); `uv run ruff check src/chmabapay` passes locally, and the exit-demo screenshots live at .trae/specs/user-portal-m1-m2-complete/evidence/step1..step5.
- **Priority**: high
- **Depends On**: T1-T10 all.
- **Description**:
  - Run 5 BRD §13.0.1 Milestone exit demo flows manually or with integration test.
  - Run full pytest suite `pytest -x` once.
  - Run `ruff check src/chmabapay`.
  - Confirm no deprecated references to raw loops remain.
  - Record trace header curl output once.
- **Acceptance Criteria Addressed**: AC-18 (5 demo flows), AC-19 (trace), AC-20 (pytest+ruff)
- **Test Requirements**:
  - `rule` TR-11.1: Demo flow 1-5 complete. 1 sign in, 2 create store, 3 pay+webhook, 4 KYC submit, 5 upgrade+business toggle. Each with evidence.
  - `rule` TR-11.2: `pytest -x` exit 0; `ruff check src` exit 0. Evidence: terminal output.
  - `rule` TR-11.3: curl -i /health → X-ChmabaPay-Trace header. Evidence: curl output.
  - `rubric` TR-11.4: Demo quality narrative. 1=failures 2+, 3=small UI issues, 5=walkthrough smooth with zero manual errors. Threshold >= 4. Evidence: final command/video output.

---

## Dependency Graph (Read this if running tasks parallel)

```
Phase 1 (WORKERS FIRST — EDD rule):
  T1 (scaffold) ─→ T2 (W1) ─┐
                    T3 (W2) ─┤
                    T4 (W4) ─┴→ T5 (wire lifespan + trace middleware)
  (T6 schema runs IN PARALLEL here — independent from workers)

Phase 2 (LOGIC + DB ENFORCEMENT):
  T6 (schema + seed) ─→ T7 (plan/KYC/mark_paid atomic enforcement)

Phase 3 (ROUTERS):
  T6 ─→ T8a (Auth) ─→ T8b (Account/Keys/Webhooks)
  T1+T6 ─→ T8c (Billing + TestMode)
  T2+T3+T5 ─→ T9 (Enqueue-on-write triggers)

Phase 4 (UI):
  T8a ─→ T10 (Frontend all pages)

Phase 5 (VERIFY):
  T1..T10 ALL done → T11 (Final demo + pytest ruff)
```
