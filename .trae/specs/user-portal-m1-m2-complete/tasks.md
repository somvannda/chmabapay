# ChmabaPay User Portal M1+M2 - Implementation Task Queue

Ordering notes:
- T1 (backend unify auth) → runs first. All frontend tasks depend on it so cookie can call stores/payments/keys/webhooks etc.
- T2 (shell/layout shared) → runs after T1. Creates shared PortalLayout wrapping logic so each page doesn't re-implement guard.
- T3–T11 (page routes) → independent parallel OK; they just reuse PortalLayout + fetch individual API routes.
- T12 (tests/build verification) → last.
- T13 (Sokha demo walkthrough) → very last, evidence captured.

---

## Task 1: Backend - unify auth context (session cookie OR Bearer key) for CRUD routers
- **Status**: `complete`
- **Verified**: `src/chmabapay/auth.py` defines `AuthContext` and `get_current_auth_context` (session cookie OR Bearer key), consumed by `routers/stores.py`, `routers/payments.py`, `routers/keys.py`, `routers/webhooks.py`, `routers/reports.py` and `routers/transactions.py`; cookie auth on those CRUD routes is exercised by `tests/test_account_security.py::test_a_frozen_account_reads_and_pays_but_cannot_write` (GET `/api/v1/stores`, `/api/v1/payments`, `/api/v1/keys`, `/api/v1/webhooks` over `session_client`), `/auth/signout` POST lives in `src/chmabapay/routers/auth.py`, and the safe `?next=` redirect is covered by `tests/test_account_security.py::test_the_sign_in_redirect_survives_the_oauth_round_trip` (the spec's `tests/routers/test_auth_context.py` was never created).
- **Priority**: high
- **Depends On**: None (blocking prerequisite)
- **Description**:
  - New dependency `get_current_auth_context(request: Request, session: AsyncSession = Depends(get_session)) -> AuthContext` with fields `account: models.Account`, `scope: "account"|"store"`, `store_id: int | None`, `is_session: bool` (cookie origin true, key false).
  - AuthContext logic: try session cookie first via existing verify_jwt → if valid, account-wide scope return (is_session=true); else Bearer key → resolve via existing `require_store_manager` with its original per-store scope logic.
  - Update routers: `stores.py`, `payments.py`, `transactions.py`, `keys.py`, `webhooks.py`, `platform.py`, `reports.py` to all use new `AuthContext` dependency instead of only `KeyContext`. Keep `KeyContext` as alias typing for backward-compat (or deprecate clean).
  - Verify `_enforce_max_stores` in stores.py still works identically when ctx.account comes from cookie.
  - Verify Individual account scope key create guard in `keys.py` runs same rules whether session or key.
  - Add: `/auth/signout` POST form-accept endpoint (fastapi Form empty body OK) — today's dashboard page relies on HTML form submit. Verify it works without requiring JSON.
  - Optional: Google login accept `?next=/dashboard/stores` → after cookie set, 302 to safe same-origin next. If not provided, default to `settings.post_login_redirect_url`.
- **Acceptance Criteria Addressed**: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-8 (all cookie-auth CRUD + login redirect query)
- **Test Requirements**:
  - `rule` TR-1.1: New `pytest -xvs tests/routers/test_auth_context.py` cases: GET /api/v1/stores with Cookie header (no Bearer) → 200 if session JWT signed correctly; with no auth → 401.
  - `rule` TR-1.2: POST /api/v1/stores via session cookie returns 201 for 1st store; on 6th store for Starter user via cookie: returns 400 "Upgrade to Growth plan" same as key-based.
  - `rule` TR-1.3: Existing key-based tests (all pytest suite) still pass 6/6 green; regression zero.
  - `rubric` TR-1.4: AuthContext backward compat; scale 1-5. 1 = breaks old key consumers; 3 = mixed works but guards differ; 5 = identical behavior old routes + session works. Threshold >= 4. Evidence: pytest diff old/new.
- **Notes**: Existing `_enforce_max_stores` already operates on ctx.account — zero structural change, only wrap resolver into AuthContext.

## Task 2: Frontend - PortalLayout + useSession hook + 401 redirect logic + nav active sync
- **Status**: `complete`
- **Verified**: the portal shell is `web/landing/components/portal/DashboardShell.tsx` (the real filename; the spec's `PortalLayout.tsx` never existed), rendered with the session guard by `web/landing/app/dashboard/layout.tsx`, while `web/landing/components/portal/useSession.ts` performs the 401 → `/user/google/auth/login?next=` redirect and nav active state is derived by `DashboardShell.activeNavFromPath` (sign-out is wired in the layout, not as a `logout()` method on the hook).
- **Priority**: high
- **Depends On**: T1
- **Description**:
  - Extract layout + sidebar shell from existing [dashboard/page.tsx](file:///e:/Development/chmabapay/web/landing/app/dashboard/page.tsx) into `web/landing/components/portal/PortalLayout.tsx`.
  - Hook `useSession()` in `web/landing/components/portal/useSession.ts` → handles: fetch `/api/v1/me` on mount with credentials include; state `{ loading, profile, error }`; `logout()` method POST form submit hidden `/auth/signout` form; redirect 401 → location.replace with ?next=.
  - Each portal page (dashboard/stores/...) wraps children with `<PortalLayout activeNav="stores">{children}</PortalLayout>`; component:
    - useEffect → calls useSession.
    - loading skeleton.
    - error 401 redirect.
    - banner stack order: Platform admin, KYC status, kyc_live_blocked.
    - Plan gates: render lock badges on nav per profile fields + subscription fetch `/api/v1/billing/subscription` optional.
  - Append new CSS classes to globals.css only for anything introduced by PortalLayout reuse (should be minimal; mostly existing `.dash-*` classes).
  - Wire sign out in PortalLayout footer with same POST form as current dashboard page.
- **Acceptance Criteria Addressed**: AC-1 (session guard), AC-7 (banner display), AC-9 (Reports nav hidden Starter), AC-13 (plan gate UI), FR-P1..P7
- **Test Requirements**:
  - `rule` TR-2.1: `next build` passes with new components folder & PortalLayout import.
  - `rule` TR-2.2: HTTP GET `/dashboard` with no cookie → client-side on-load redirects location.replace to `/user/google/auth/login?next=%2Fdashboard` — evidence by browsing with browser (or E2E script — we can use browser MCP later).
  - `rule` TR-2.3: grep `style=` on new files = 0 matches; all in globals.css.
  - `rubric` TR-2.4: Layout reuse & responsiveness (scale 1-5). 1 = duplicated in each page; 3 = layout works desktop only; 5 = reused all pages, mobile sidebar hide (under 900px works). Threshold >= 4.

## Task 3: Dashboard Overview page (/dashboard) enhancement with real data-backed stats + today activity payments list
- **Status**: `complete`
- **Verified**: `web/landing/app/dashboard/page.tsx` renders the four metric cards (Paid today / Settled / Avg. payment / Active stores), the "Get started in 3 steps" panel and a last-5 "Recent activity" list, taking its real totals from server-side aggregates at `GET /api/v1/reports/payments.json` (`summary.total_matching_paid_amount_cents`) rather than a client-side sum of 200 payments.
- **Priority**: high
- **Depends On**: T2
- **Description**:
  - Keep existing design shell (it is good).
  - Add: fetch `/api/v1/payments?limit=200` (latest) via session cookie → client-aggregate:
    - Paid today sum → `Paid today` metric card
    - `Settled` sum = all paid today minus (if refunds exist M2, skip)
    - Average payment = avg of paid amounts
    - Active stores = fetch `/api/v1/stores` count
  - Today activity panel: show last 5 payments with colored status pills + link to payment detail `/dashboard/payments/[id]`. No payments → dashed empty state with Create payment CTA.
  - Open question resolution (in spec): use aggregate from last 200 payments client-side; server endpoint not needed M1.
  - CTA create first store button: link to `/dashboard/stores/new` (new subroute)
- **Acceptance Criteria Addressed**: AC-1, AC-12 (Sokha demo step 5c → see today activity)
- **Test Requirements**:
  - `rule` TR-3.1: Payments list renders 5 items if data via mock fetch — verify by manual injection in browser console.
  - `rule` TR-3.2: No-payment empty state renders dashed state per CSS (match classes on activity div).

## Task 4: Stores index + New store wizard + Store detail pages
- **Status**: `partial`
- **Verified**: `web/landing/app/dashboard/stores/page.tsx` (list with create/disable/enable), `web/landing/app/dashboard/stores/new/page.tsx` (form) and the real detail `web/landing/app/dashboard/[public_id]/page.tsx` plus its `/payments` `/settings` sub-pages exist; the described 10-row pagination / created_at sort / status filter, the 2-step wizard with destination-type tabs and the delete-store confirm do not — Bakong/bank destinations were retired by `supabase/migrations/4-merge-sub-merchants-into-stores.sql` and `src/chmabapay/routers/stores.py` exposes no DELETE route (only `/disable`, `/enable`, `/activate`).
- **Priority**: high
- **Depends On**: T2
- **Description**:
  - Pages:
    - `/dashboard/stores/page.tsx` (list)
    - `/dashboard/stores/new/page.tsx` (wizard 2-step)
    - `/dashboard/stores/[public_id]/page.tsx` (detail)
  - List: table with pagination 10/row, sortable by created_at desc default, filters status.
  - New wizard: step 1 name + destination type tabs (ABA PayWay link / Bakong ID / Bank code+acct); step 2 review → submit. Handles 400 plan gate error inline alert.
  - Detail: store fields, link info (mask numbers), recent payments tab showing table of last 20 for store; link attachment edit button; delete store confirm dialog with red warning.
- **Acceptance Criteria Addressed**: AC-2, AC-12 Sokha steps (paste ABA → create store ACTIVE)
- **Test Requirements**:
  - `rule` TR-4.1: Create store end-to-end browser with cookie (dev shell or walk-through) → 201 + new row in list.
  - `rule` TR-4.2: Plan gate 5 stores limit → on 6th POST, page shows inline red alert with message from backend response.detail verbatim.
  - `rubric` TR-4.3: Wizard UX flow (scale 1-5). 1 = single huge form; 3 = 2 steps but no tooltips; 5 = tabs by destination type, masked bakong/bank numbers preview, copy-to-clipboard store public_id. Threshold >= 4.

## Task 5: Payments index + New payment + Payment detail (QR copy + simulate paid button for admins)
- **Status**: `complete`
- **Verified**: `web/landing/app/dashboard/payments/page.tsx` (status + store filters, load-more), `web/landing/app/dashboard/payments/new/page.tsx` (amount / reference_id / store / metadata) and `web/landing/app/dashboard/payments/[public_id]/page.tsx` (QR `<img src="/pay/{id}/qr.svg">`, checkout-url copy button, paid_at + bakong_ref, and the admin-only "Test: Mark paid" button POSTing `/_dev/payments/{id}/pay`) all exist — note the QR route is `.svg`; the `.png` the spec names never existed on the backend.
- **Priority**: high
- **Depends On**: T2
- **Description**:
  - Pages:
    - `/dashboard/payments/page.tsx` (list, filters status/store/date)
    - `/dashboard/payments/new/page.tsx` (create)
    - `/dashboard/payments/[public_id]/page.tsx` (detail)
  - List table: status pills colored, amount USD formatted, copy public_id icon, detail link.
  - New wizard: amount + reference_id + store dropdown + metadata K/V pairs.
  - Detail: QR image (img src `/pay/{public_id}/qr.png` through rewrite), checkout_url copy button, status chip, paid_at + bakong_ref if set, attempts history collapsible JSON (if backend exposes attempt_history field in payment out; otherwise skip M1).
  - If `profile.is_platform_admin=true` → show "Test: Mark paid" button that POSTs to dev simulate (using cookie auth, we added in T1); OR fallback to `/_dev/payments/{id}/pay` endpoint via cookie auth context.
- **Acceptance Criteria Addressed**: AC-3, AC-12 (Sokha steps)
- **Test Requirements**:
  - `rule` TR-5.1: Create payment from UI → 201 → list includes new row pending → detail page renders QR <img> loaded successfully (200 network for PNG).
  - `rule` TR-5.2: Admin user can click simulate mark paid and UI re-renders status to "PAID" with a timestamp + bakong_ref non-empty.
  - `rule` TR-5.3: Checkout_url copy button writes to clipboard correctly (navigator.clipboard).

## Task 6: API Keys page (list + create with scope + reveal once modal + revoke)
- **Status**: `partial`
- **Verified**: `web/landing/app/dashboard/keys/page.tsx` implements the list, create form, one-time `RevealBanner` (never re-revealed), Revoke and Rotate; the described Store-scope vs Account-scope tabs with Individual-Starter gating do not exist because keys became workspace-scoped — `allow_account_scope_keys` was dropped in `supabase/migrations/7-retire-dead-plan-and-store-config.sql` and the account-type split in `alembic/versions/0008_drop_account_type.py`.
- **Priority**: high
- **Depends On**: T2
- **Description**:
  - Route `/dashboard/keys/page.tsx`
  - List: columns prefix/scope/created/last_used/revoke action.
  - Create key modal (or separate route): tabs Store-scope (stores dropdown) OR Account-scope (disabled/gated tooltip for Starter Individual).
  - Reveal once modal: after submit success, shows raw key string, with "I have copied my key" checkbox required before closing; once closed — key value destroyed in component state (never re-reveal).
  - Revoke: confirmation; after revoke, list grays the row.
- **Acceptance Criteria Addressed**: AC-4 (keys gate + reveal once)
- **Test Requirements**:
  - `rule` TR-6.1: Individual Starter → Account scope tab disabled with "Upgrade to Growth" tooltip (hover title text matches).
  - `rule` TR-6.2: After key create modal close, reopen or click refresh page → list table shows masked value only `ck_****last4`; no place in DOM contains full key (verify in browser search "ck_live_...").
  - `rubric` TR-6.3: Secret handling UX (scale 1-5). 1 = re-exposes; 3 = reveal once but no checkboxes; 5 = reveal once + warning text + checkbox + toast on copy. Threshold >= 4.

## Task 7: Webhooks endpoints page (list + add + signing secret reveal once + rotate secret + edit/disable/delete)
- **Status**: `complete`
- **Verified**: `web/landing/app/dashboard/webhooks/page.tsx` lists endpoints and implements add/edit-with-event-checkboxes, disable, delete, signing-secret reveal-once (`RevealSecretModal`) and rotate (`POST /api/v1/webhooks/{id}/rotate-secret`), plus test-send and a deliveries log — note the real event names are `payment.completed/expired/superseded/reversed`, not the spec's `payment.created/paid/expired/refunded`.
- **Priority**: high
- **Depends On**: T2
- **Description**:
  - Route `/dashboard/webhooks/page.tsx`
  - List: url, status (active/disabled), last delivery state chip, actions: edit URL / rotate signing secret / disable / delete.
  - Add endpoint modal: url input + event checkboxes (payment.created / payment.paid / payment.expired / payment.refunded — align with backend `Event.type` enum).
  - Signing secret reveal ONCE same pattern as keys (never re-reveal).
  - Rotate secret: new signing secret → again reveal once pattern.
- **Acceptance Criteria Addressed**: AC-5 (webhook create + signing secret shown once)
- **Test Requirements**:
  - `rule` TR-7.1: Create endpoint via UI → returns 201 → signing secret modal shows `whsec_` string.
  - `rule` TR-7.2: Refresh page → row present, signing secret not shown anywhere (only rotate secret action remains).
  - `rubric` TR-7.3: Event type coverage; scale 1-5. 1 = no event selection (all events only); 3 = static list of 4; 5 = searchable/selectable chips + "Select all" checkbox. Threshold >= 4.

## Task 8: Billing & Plans page (plan matrix + upgrade CTAs + subscription card)
- **Status**: `partial`
- **Verified**: `web/landing/app/dashboard/billing/page.tsx` renders the plan matrix, the subscription card and upgrade CTAs calling `POST /api/v1/billing/change-plan`; the described Starter→Growth "redirect to /dashboard/keys with Account scope pre-enabled" and Growth→Scale "Sub-Merchants nav unlocks" steps are absent, and the Starter/Growth/Scale/Enterprise plans themselves were deleted by `supabase/migrations/7-retire-dead-plan-and-store-config.sql` (plans are now Free/Starter/Pro).
- **Priority**: high
- **Depends On**: T2
- **Description**:
  - Route `/dashboard/billing/page.tsx`
  - Plans matrix 4 cols copy exact BRD §3.1 rows. Current plan highlighted violet card.
  - Subscription card: status (trial|active|canceled), trial_ends_at, next_billing_at, monthly fee, plan name/code.
  - Upgrade: call POST `/api/v1/billing/plan-change` with body plan_code. On 201 show success; refetch `/api/v1/me` & subscription.
  - On plan change from Starter→Growth success: client redirect to `/dashboard/keys/new` with Account scope tab pre-enabled (onboarding hint to make shared key).
  - On Growth→Scale success: next render Sub-Merchants nav unlocked.
- **Acceptance Criteria Addressed**: AC-6, AC-9 (Reports unlock), AC-13 plan gate consistency
- **Test Requirements**:
  - `rule` TR-8.1: Upgrade Growth via UI → 200/201 → refetch profile.account_type == "business" → Keys page Account scope tab active now.
  - `rule` TR-8.2: Upgrade Scale via UI → profile allow_saas_sub_merchants true → Sub-Merchants nav no lock.
  - `rubric` TR-8.3: Plan matrix fidelity to BRD §3.1; scale 1-5. 1 = missing rows; 3 = half features; 5 = all 12 feature rows present with exact values from BRD. Threshold >= 4.

## Task 9: Settings page (Profile / KYC / Billing tabs / Features admin-only)
- **Status**: `partial`
- **Verified**: `web/landing/app/dashboard/settings/page.tsx` ships Profile and Billing tabs (plus a new Security tab for email/password/account-erase); the KYC tab is superseded — `supabase/migrations/6-drop-kyc.sql` dropped every KYC column and `kyc_status` — and no Features/whitelabel tab exists there (whitelabel branding lives in the store settings page `web/landing/app/dashboard/[public_id]/settings/page.tsx`).
- **Priority**: high
- **Depends On**: T2
- **Description**:
  - Route `/dashboard/settings/page.tsx`
  - Tabs: Profile | KYC | Billing | Features (shown only for whitelabel_enabled=true OR is_platform_admin)
  - Profile: name edit, email read-only from /api/v1/me.
  - KYC: 2 radio Individual/Business. Show form per selection. Submit via POST /api/v1/kyc body match schemas KycIndividualIn/KycBusinessIn from backend.
  - Open question resolution (in spec): file URLs pasted as string inputs only (not file picker).
  - Billing tab: shortcut with subscription card info (reuse Billing page component) + link to plans matrix.
  - Features (admin only): brand logo domain, whitelabel domain fields (readonly M1 — placeholder input).
- **Acceptance Criteria Addressed**: AC-7 (KYC submit → kyc_status updated → banner at Overview top)
- **Test Requirements**:
  - `rule` TR-9.1: Settings KYC Individual submitted → /api/v1/kyc 200 → /api/v1/me kyc_status no longer "none". Overview shows KYC banner yellow reviewing.
  - `rule` TR-9.2: Profile PATCH name change → success & hero welcome text updates without full page reload.
  - `rubric` TR-9.3: Form UX quality (scale 1-5). 1 = unlabeled inputs; 3 = labels + basic placeholder; 5 = field-level error messages from backend 400 detail, submit disabled on invalid, server-validation toast success. Threshold >= 4.

## Task 10: Sub-Merchants page (gated) + Reports page (CSV export buttons, Starter hidden)
- **Status**: `superseded`
- **Verified**: no `/dashboard/sub-merchants` route exists today — `supabase/migrations/4-merge-sub-merchants-into-stores.sql` merged sub-merchants into stores (dropping the `sub_merchants` table and `allow_saas_sub_merchants`), and the "Reports nav hidden for Starter" gate was removed by `alembic/versions/0010_drop_csv_export_gate.py`; the page itself survives and works ungated at `web/landing/app/dashboard/reports/page.tsx`.
- **Priority**: medium
- **Depends On**: T2
- **Description**:
  - Sub-Merchants route `/dashboard/sub-merchants/page.tsx` → hidden & redirect to billing upgrade for users without allow_saas_sub_merchants. M1 skeleton: list + add wizard via POST `/api/v1/platform/sub-merchants` (session cookie enabled via T1).
  - Reports route `/dashboard/reports/page.tsx` → nav item hidden for Starter users via PortalLayout. Growth+ users see buttons: Payments CSV (30d) Export / Stores CSV Export. Download via fetch + Blob + hidden anchor click. Empty state if reports router returns 501 (handled gracefully).
- **Acceptance Criteria Addressed**: AC-9 (Reports hidden Starter), AC-6 (Sub-Merchants on Scale)
- **Test Requirements**:
  - `rule` TR-10.1: Starter user → Reports nav hidden. User grows → Reports appears.
  - `rule` TR-10.2: Sub-Merchants for users lacking flag → page redirect to /dashboard/billing with "Upgrade to Scale" toast. Admin (flag=true) → Sub-Merchant page loads list.
  - `rubric` TR-10.3: CSV export handling; 1-5. 1 = errors; 3 = downloads raw JSON not CSV; 5 = CSV content-type, correct filename, BOM if needed Excel. Threshold >= 4.

## Task 11: Help center / onboarding pages (low priority M1 nice-to-have)
- **Status**: `partial`
- **Verified**: `web/landing/app/dashboard/help/page.tsx` renders six collapsible FAQ accordions as specified; no `/dashboard/onboarding` route was ever created (the only trace left is a stale `/onboarding` entry in `web/landing/app/robots.ts`), and the Overview shows an inline "Get started in 3 steps" panel instead of a separate onboarding carousel.
- **Priority**: low
- **Depends On**: T2
- **Description**:
  - Help: `/dashboard/help/page.tsx` → 6 accordions (no backend call).
  - Optional Onboarding: `/dashboard/onboarding/page.tsx` — if account age <24h AND stores_count==0; Overview redirects here to run 3-step onboarding carousel (Create store → Create payment → Check webhook setup) with embedded modals.
- **Acceptance Criteria Addressed**: AC-12 (Sokha onboarding walkthrough)
- **Test Requirements**:
  - `rule` TR-11.1: `/dashboard/help` builds with no fetch errors → accordions expand/collapse click works.
  - `rubric` TR-11.2: Onboarding usefulness; 1-5. 1 = empty; 3 = generic steps; 5 = real links to create store/new payment/webhook pages with pre-filled data suggestions. Threshold >= 3 (low priority).

## Task 12: Verification — next build + CSS grep + pytest regression run
- **Status**: `complete`
- **Verified**: twelve of the named route pages exist under `web/landing/app/dashboard/` (`stores`, `stores/new`, `stores/[public_id]`, `payments`, `payments/new`, `payments/[public_id]`, `keys`, `webhooks`, `billing`, `settings`, `reports`, `help`, plus the `[public_id]` store workspace) and a grep for ` style=` across `web/landing/**/*.tsx` returns 0 matches, satisfying AC-10/NFR-1 (the spec's `/dashboard/sub-merchants` and `/dashboard/onboarding` rows are absent by design; `next build` and `pytest` were not re-executed during this audit).
- **Priority**: high
- **Depends On**: All of T1–T11
- **Description**:
  - `cd web/landing ; Remove-Item -Recurse -Force .next ; npm run build` — exit 0.
  - `grep -R " style=" web/landing/app web/landing/components` → 0 matches.
  - Backend: `cd e:\Development\chmabapay ; pytest -xvs` → all 6 tests green + any new tests (T1) pass.
  - New routes visible in build log: /dashboard/stores, /dashboard/stores/new, /dashboard/stores/[public_id], /dashboard/payments, /dashboard/payments/new, /dashboard/payments/[public_id], /dashboard/keys, /dashboard/webhooks, /dashboard/billing, /dashboard/settings, /dashboard/sub-merchants, /dashboard/reports, /dashboard/help, /dashboard/onboarding (if created).
- **Acceptance Criteria Addressed**: AC-10, AC-11
- **Test Requirements**:
  - `rule` TR-12.1: next build exit 0.
  - `rule` TR-12.2: grep count style= in tsx = 0.
  - `rule` TR-12.3: pytest exit 0.

## Task 13: End-to-end walkthrough evidence — BRD §5 Sokha demo
- **Status**: `complete`
- **Verified**: the full five-step walkthrough was captured as `.trae/specs/user-portal-m1-m2-complete/evidence/step1-signin.png`, `step2-store-create.png`, `step3-payment-create-qr.png`, `step4-mark-paid.png` and `step5-webhook.png`.
- **Priority**: high
- **Depends On**: T12
- **Description**:
  - Capture browser evidence for BRD §5 exit criteria:
    - Step 1: Sign in with Google → dashboard.
    - Step 2: Create store paste ABA PayWay link → store ACTIVE.
    - Step 3: Create test $5.50 payment with st_… key (via /dashboard/payments/new UI).
    - Step 4: Use admin simulate Mark paid button (or dev rail) → mark it PAID.
    - Step 5: Verify webhook delivery signature against sink if available; otherwise delivery attempt at least once in UI/Webhooks list showing state.
  - Write walkthrough notes (text file or screenshots path) in spec folder evidence.
- **Acceptance Criteria Addressed**: AC-12 (Sokha demo rubric >= 4), AC-13 (plan gates >= 4)
- **Test Requirements**:
  - `rule` TR-13.1: Every step (1-5) completed with screenshot or network payload evidence captured.
  - `rubric` TR-13.2: Full flow polish; 1-5. 1 = broken, 3 = flows work but ugly toast messages, 5 = no red errors, clear CTAs, plan gate tooltips on all locks. Threshold >= 4.
