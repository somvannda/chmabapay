# ChmabaPay User Portal M1+M2 - Product Requirements Document

## Overview
- **Summary**: Build the complete User Portal (dashboard + all page-by-page flows from BRD §7) as Next.js 14 App Router routes inside the existing landing frontend (`web/landing/app/`), backed by the existing FastAPI v1 APIs. Every page is session-gated: read the httpOnly cookie `chmabapay_session`, fetch `/v1/me` (credentials:include) to render the current user, and redirect `/ → /dashboard` when logged in, or `/dashboard → /auth/google/login` when not logged in.
- **Purpose**: Turn the Google OAuth sign-in flow into a full working onboarding → dashboard → CRUD portal experience per BRD §5 (Individual Sokha) and §6 (Business KhmerPOS). The portal is the primary user-facing UI; every server action works with the existing FastAPI routers (stores/payments/keys/webhooks/billing/account/auth).
- **Target Users**: Individual accounts (Sokha, post-Google-sign in) + Business accounts (KhmerPOS, post plan upgrade to Growth) + whitelisted Platform Admin (duke@chmaba.com, first OAuth sign-in auto-promoted per admin_emails env list).

## Goals
1. Session auth UX end-to-end: no 404 or blank-screen flows. Logged-in users always land on `/dashboard` with session JWT cookie verified by `/v1/me`.
2. BRD §7 page-by-page spec built out as real Next routes (not placeholders): Overview, Stores list/create, Payments list/detail, Keys list/create/reveal-once, Webhooks list/create/signing-secret-copy, Billing & Plans matrix + change-plan, Settings (Profile/KYC/Billing tabs), Sub-merchants page shown/hidden via plan `allow_saas_sub_merchants`.
3. Onboarding UX per BRD §5 & §6: new Individual user → 3-step "Get started" on the overview with CTA to Create Store. New Business user (Growth upgrade) → switch account_type auto + prompt to create shared key + webhook.
4. Plan gates match BRD §3.1 matrix (Starter: 5 stores, no Sub-Merchants, no shared keys; Growth: 50 stores, shared keys unlocked; Scale/Enterprise: Sub-Merchants + white-label unlocked). UI shows lock badges when action denied.
5. Session lifecycle: `/auth/signout` clears cookie and lands on `/` landing page; on cookie expiry, any page-wide `/v1/me` 401 → redirect to login with `?next=...` return path.
6. Platform Admin (is_platform_admin=true) on any page sees a green banner per current Overview page; Sub-merchants tab is unlocked because admin has both saas_sub_merchants_enabled and whitelabel_enabled true (idempotent promote on every login via auth.py `_maybe_promote_admin_and_seed_hq`).

## Non-Goals
1. Admin portal pages (/admin/*) are M2 later work — out of scope today. We only link to `/api/docs` for admin today.
2. KYC document upload UI (file picker / preview / S3 storage) — M2 KYC only; today's Settings/KYC form accepts file URL strings only (matching current `/v1/kyc` POST schema).
3. Email notifications, CSV Reports full UI, Sub-Merchant CSV import wizard UI — M2/M3 later.
4. Dark-mode toggle, Khmer/English i18n, Invoice PDF viewer — deferred per M2.5/§12.5.
5. Redis transport or Phase 2/3 scale changes — deferred. Portal only uses existing backend routes.
6. Separate Next app for user portal — user explicitly chose "keep it with the website" today; portal routes share the Next app with landing.

## Background & Context
- Existing state (2026-09-11):
  - Backend routers live at `src/chmabapay/routers/`: [auth.py](file:///e:/Development/chmabapay/src/chmabapay/routers/auth.py#L1-L495) (Google OAuth, session cookies, `/auth/signout`), [account.py](file:///e:/Development/chmabapay/src/chmabapay/routers/account.py#L1-L160) (`/v1/me` GET/PATCH, `/v1/kyc` POST), [stores.py](file:///e:/Development/chmabapay/src/chmabapay/routers/stores.py#L1-L120), `payments.py`, `transactions.py`, `keys.py`, `webhooks.py`, [billing.py](file:///e:/Development/chmabapay/src/chmabapay/routers/billing.py#L1-L200) (plans list, change-plan, subscription), `platform.py`, `reports.py`, `admin.py`, `checkout.py`, `khqr.py`, `dev.py`.
  - Next app routes today: `/`, `/api/docs`, `/dashboard` new. Rewrites set in [next.config.js lines 7-61](file:///e:/Development/chmabapay/web/landing/next.config.js#L7-L61) proxy `/v1/*`, `/auth/*`, `/user/google/auth/*`, `/pay/*`, `/openapi.json`, `/health` straight to backend. No CORS issues. No separate origins.
  - Session auth dependency `get_current_session_account` [auth.py lines 290-312](file:///e:/Development/chmabapay/src/chmabapay/routers/auth.py#L290-L312) returns Account row or 401. Works for cookie. Currently used in account/billing; need to extend stores/payments/keys/webhooks to accept session (cookie) as auth in addition to Bearer keys so the browser UI can read/write them. That's a backend session middleware gap we need to close today.
  - Google OAuth callback → 302 to `POST_LOGIN_REDIRECT_URL=/dashboard` set in [.env line 17](file:///e:/Development/chmabapay/.env#L17). Auth router sets httpOnly SameSite=Lax cookie with Secure=HTTPS-or-non-localhost.
  - Design tokens & CSS centralization rule enforced (project memory CSS constraint): ZERO inline React `style={{}}` blocks. All styling lives in [globals.css](file:///e:/Development/chmabapay/web/landing/app/globals.css). Violet accent `#6957F5`, Inter/JetBrains Mono fonts, 8px radius.
  - BRD §5 exit criteria for Sokha demo: sign-in with Google → paste ABA link → create store ACTIVE → make test payment via `/v1/payments` → trigger `/_dev/payments/{id}/pay` → webhook delivered signed to sink. We need all of these to also be clickable UI steps in the portal (not just cURL).
  - BRD §7 Dashboard UX spec: 7 sidebar sections (Overview/Stores/Payments/Sub-Merchants/Reports/Settings/Keys/Webhooks/Help) page-by-page.
- Explicit user decisions captured this session:
  - Keep user portal with website (single Next app). Not split.
  - Strict localhost-only mode today (no Cloudflare tunnel, no pay.chmaba.com DNS yet).
  - Admin auto-promotion list = `CHMABAPAY_ADMIN_EMAILS=duke@chmaba.com`.
  - Session cookie strict 1:1 Google OAuth callback path shape `/user/google/auth/callback` matching [chmabapay.json](file:///e:/Development/chmabapay/chmabapay.json#L1-L30).
- Previous sessions: backend M1 + dashboard skeleton page.tsx created; `npm run build` green; next start 3001 ready; backend 8000 healthy; rewrite routes confirmed.

## Functional Requirements

### Portal-wide (apply to every page under /dashboard, /stores, /payments, etc.)
- **FR-P1 Session guard**: Every portal route (list below) is a client component that runs: useEffect → GET `/v1/me` with credentials include. 200 → render page + save profile to state + sidebar active-tab. 401 → `window.location.replace("/user/google/auth/login?next=" + encodeURIComponent(window.location.pathname))`. Loading state during fetch shows centered ChmabaPay skeleton.
- **FR-P2 Shared layout**: Every portal page shares the same sidebar shell we built for `/dashboard`: 280px left nav with brand, 6+ nav items list (Overview / Stores / Payments / Sub-Merchants [gated] / Keys / Webhooks / Billing / Reports [hidden Starter] / Settings / Sign out / API docs link / Landing link). Sidebar collapses under 900px.
- **FR-P3 Active tab sync**: URL pathname drives sidebar active state. `/stores` → active=Stores; `/payments/[id]` → active=Payments, etc.
- **FR-P4 Account type banners**: Top hero under profile avatar shows status banners (ordered by priority):
  - is_platform_admin → green "Platform admin pre-approved" banner (current design on Overview).
  - kyc_status=reviewing → yellow "KYC under review"; kyc_status=rejected → red + reject_reason text; kyc_status=none + Individual → blue "Submit KYC to unlock after 10 payments"; Business default kyc_live_blocked=true → red "Complete Business KYC to enable live payments".
- **FR-P5 Plan gates on nav + action buttons**:
  - Starter users → Sub-Merchants nav item shows "Locked" badge; Reports nav hidden per BRD §3.1 CSV export hidden Starter.
  - Individual + Starter → Key create wizard only offers per-store scope.
  - Business + Growth → unlocks Account scope shared keys.
  - Scale/Enterprise (or admin) → Sub-Merchants nav is active + unlocked. Whitelabel toggle in Settings appears.
- **FR-P6 Sign-out**: Every sidebar footer has a form (POST to `/auth/signout`) with a sign-out button. Form submit → backend clears cookie → 303 redirect → landing `/`.
- **FR-P7 Plan card**: Every portal page (or at minimum Overview + Billing) shows current plan with name, code, monthly fee, stores used/max, features unlocked, and a CTA "Upgrade plan" that routes to Billing.

### Page-by-page (BRD §7)
- **FR-Page-1 Overview /dashboard**:
  - Welcome hero + account banners + profile avatar/initials + date.
  - Plan card dark mode with 3 feature columns & status.
  - 4 metric cards (Paid today accent / Settled / Avg. payment / Active stores). Use real data when APIs return data; zero-state placeholder 0.00 otherwise (not breaking).
  - Get started 3-step panel with CTA to Create store.
  - Today activity: empty dashed state with link to Payments (with seeded test-payment link to `/_dev/payments/{id}/pay` UI).
- **FR-Page-2 Stores index /dashboard/stores**:
  - Table columns: name / public_id / status (pending_link|active) / link_type (aba_payway_link|bakong_id|bank) / created_at / actions (Open detail → /dashboard/stores/[id] | Edit link → attach link wizard | Delete store with confirmation dialog).
  - Top CTA "+ Create store" opens 2-step wizard (step1: name + ABA payway paste OR step2: Bakong ID paste OR bank dropdown + account). Submit → POST `/v1/stores` with session cookie auth → backend enforce max_stores plan gate via plan/subscription lookup → on 400 detail "Upgrade to Growth plan to add more than N stores" → render inline alert; on 201 → toast success & redirect to detail.
  - Store detail `/dashboard/stores/[id]`: store fields, current link info (mask bakong/bank numbers sensibly), recent payments for store tab (GET /v1/payments?store_id=id).
- **FR-Page-3 Payments index /dashboard/payments**:
  - Table: public_id / amount / status (pending|scanned|paid|expired|failed) / store / reference_id / paid_at / created_at.
  - Filter pill row: status filter + date range + store filter dropdown.
  - CTA "+ Create payment" opens wizard: amount, reference_id, store dropdown, metadata k/v → POST /v1/payments.
  - Row actions: detail page → /dashboard/payments/[id] with QR preview (GET /pay/{id}/qr.png → img src) + checkout_url copy button + instruction_ref + paid timestamp + bakong_ref if paid. If payment is pending/scanned AND user is admin → show a "Test: mark paid" button that POSTs dev rail simulate endpoint or `/_dev/payments/{id}/pay` with session cookie.
- **FR-Page-4 Keys /dashboard/keys**:
  - Table: key prefix (ck_live_* / ck_test_* / st_* / sk_*) / scope (account|store:id) / created_at / last used_at (null or date) / actions (revoke | reveal once).
  - Create key wizard: 2 tabs (store-scope OR account-scope); account-scope disabled (with "Upgrade to Growth" tooltip) for Individual Starter.
  - Reveal once modal: on Create → show full key string ONE time with "I have copied this key" checkbox → close destroys the string state. Never re-expose again after close.
- **FR-Page-5 Webhooks /dashboard/webhooks**:
  - Table: url / status / created_at / last delivery success count 7d / actions (rotate signing secret | edit URL | disable | delete).
  - Add endpoint wizard: url field + event type checkboxes (payment.created / payment.paid / payment.expired / payment.refunded — current event types defined in backend) → signing secret shown once (same reveal-once pattern).
  - Signing secret copy button on new endpoint (never shown later after dismiss; only rotate secret available).
- **FR-Page-6 Billing & Plans /dashboard/billing**:
  - Plans matrix (4 columns: Starter / Growth / Scale / Enterprise) copying exact BRD §3.1 feature rows. Current plan column highlighted violet.
  - CTA per plan: "Upgrade" → POST `/v1/billing/plan-change` body `{ plan_code: "growth" }`. On 201 show success toast + subscription row updated. If plan downgrade is blocked by backend → render alert.
  - Subscription card: status (trial|active|canceled), trial_ends_at, next_billing_at, plan name, monthly fee formatted.
  - Plan upgrade triggers:
    - Starter→Growth: Business account_type auto-switch = true (backend change-plan handles)
    - Growth→Scale: Sub-Merchants tab appears on sidebar next render after /v1/me refresh
- **FR-Page-7 Settings /dashboard/settings**:
  - Tabs: Profile | KYC | Billing | Features (admin only)
  - Profile: name editable (PATCH /v1/me body { name }) + email read-only.
  - KYC tab: 2 radio buttons (Individual / Business). Select Individual → show fields khmer_id_number + khmer_id_front_url + khmer_id_back_url. Select Business → show company fields (name / registration / MoC URL / VAT/TIN / director name / director ID). Submit → POST /v1/kyc. On success → toast & show kyc_status banner at top of page. After Business KYC submit, reloads profile shows account_type=business.
  - Billing tab: shortcut to Billing page plan card; next_billing_at; no invoice UI yet (M2).
  - Features tab (hidden when whitelabel_enabled is false): whitelabel domain field, brand text; today read-only (future).
- **FR-Page-8 Sub-Merchants /dashboard/sub-merchants** (only if allow_saas_sub_merchants):
  - Table: external_id / display_name / bakong hint / created_at / actions (edit | view payments).
  - CTA "+ Add Sub-merchant" → wizard: display_name + external_id + aba_link_or_bakong_id → POST /v1/platform/sub-merchants (session allowed for cookie).
  - M1 skeleton: read-only view list if platform endpoint works; empty state otherwise with banner "Coming soon, use POST /v1/platform/sub-merchants via API today".
- **FR-Page-9 Reports index /dashboard/reports** (hidden Starter):
  - M1 placeholder page with CSV export buttons "Payments (30d).csv", "Stores.csv". Button click → fetch `/v1/reports/...` (existing reports router) and trigger browser download via hidden anchor.
- **FR-Page-10 Help center /dashboard/help** (optional, low):
  - 6 accordions: common FAQs (how to create a store, where is ABA link, when do webhooks fire, plan limits, KYC timelines, contact support). No backend call.
- **FR-Page-11 Onboarding first-run /dashboard/onboarding** (optional):
  - If account is <24h old AND stores_count==0 → redirect Overview to onboarding 3-step carousel with Store creation embedded. Otherwise Overview loads normally.

### Backend integration gaps (needed so frontend cookie auth works for all CRUDs)
- **FR-BE-1**: Existing routers that currently only accept Bearer keys (`stores.py`, `payments.py`, `keys.py`, `webhooks.py`, `platform.py`, `reports.py`, `transactions.py`) MUST also accept session cookie via a new `get_current_auth_context(...)` dependency. Scope rules mirror the existing `require_store_manager` KeyContext but also work when a valid cookie session exists (account_id from cookie). Session is authoritative for User Portal requests; it always has full account scope (the user owns their own data) within plan gates. No admin impersonation yet.
- **FR-BE-2**: Cookie auth for `/v1/stores` POST/GET/PUT must correctly call `_enforce_max_stores` the same way Bearer does today.
- **FR-BE-3**: Cookie auth for `/v1/keys` create/rotate/revoke correctly enforces Individual = account_scope disabled.
- **FR-BE-4**: `/v1/payments` list + detail accepts session cookie with same filter/store scoping rules as Bearer.
- **FR-BE-5**: `/auth/signout` POST form endpoint must work over simple HTML form submit, not require JS fetch (for fallback). Currently only Form-style? Verify it exists or add.
- **FR-BE-6**: Google login accept optional `?next=/dashboard/stores` query, cookie set → 302 to that URL instead of static POST_LOGIN_REDIRECT_URL when present and safe (same origin).

## Non-Functional Requirements
- **NFR-1 CSS centralization (project memory rule)**: ZERO React inline `style=` in new portal pages. Any new styling added to the bottom of globals.css under existing `.dash-*` class hierarchy and design tokens.
- **NFR-2 No public credential exposure**: ckJWT and webhook signing secrets are shown once only on their creation modals. Subsequent detail views mask to `prefix_****last4`.
- **NFR-3 Session refresh tolerance**: Frontend `/v1/me` 401 → redirect and do NOT leave the page half-rendered with sensitive stale state.
- **NFR-4 Build performance**: `next build` must exit 0 after adding all routes. First-load JS per route <= 100kB.
- **NFR-5 Plan gate consistency**: All lock badges, disabled buttons, hidden nav items are derived server-side from `/v1/me` fields + subscription state from `/v1/billing/subscription`. Do NOT hardcode plan logic on client beyond `profile.allow_saas_sub_merchants`, etc.
- **NFR-6 Accessibility basics**: nav items aria-labels; buttons have names; form errors in span; inputs labelled. a11y color contrast violet on white passes AA.
- **NFR-7 Mobile usability**: Sidebar below 900px hides gracefully (we already have this CSS rule from dashboard globals.css dash-shell grid) — all new pages inherit same shell.

## Constraints
- **Technical**:
  - Portal MUST live inside existing single Next app `web/landing/app/` (user explicit decision today). No monorepo split now.
  - Rewrites & CORS: keep current next.config.js as source of truth — no extra backend CORS origins added.
  - Backend Postgres only, no SQLite. DSN postgres+asyncpg://postgres:postgres@localhost:5432/chmabapay.
  - Backend routers already exist; extend them with session auth. Do NOT create new REST surface area where existing routers already work.
  - No inline React styles per project memory. All design in globals.css.
  - Language / UX strings in ENGLISH only, right now (Khmer i18n M2 later per user profile).
- **Business**:
  - Strict BRD parity. User's earlier session message mandates this. Any deviation must be noted and justified with BRD line overrides approved by user.
  - Account_type enum is exactly `individual | business`. Business LTD vs PSP vs SaaS = plan tier + gates not new type.
  - Default on create: account_type=individual, plan=Starter, kyc_status=none, kyc_live_blocked=true, feature_gates=false (already coded in auth.py `_upsert_account`).
  - Growth upgrade auto-switch account_type business true (already in billing change-plan).
- **Dependencies**:
  - Existing dashboard design tokens & CSS: `.dash-shell/side/nav/main/hero/metric/plan-card/steps/activity` classes already in globals.css. Reuse.
  - Existing backend auth `get_current_session_account` [auth.py lines 290-312](file:///e:/Development/chmabapay/src/chmabapay/routers/auth.py#L290-L312).
  - Google OAuth client id/secret from [chmabapay.json](file:///e:/Development/chmabapay/chmabapay.json#L1-L30) copied verbatim to .env lines 9-11.
  - Rewrites cache behavior (next start does NOT hot-reload rewrites; any config change → delete .next, build, start). Today config is correct so no rebuild needed for new routes — only new code pages.

## Assumptions
1. User will keep `next start -p 3001` and backend `uvicorn 127.0.0.1:8000` running for today's end-to-end tests.
2. Next App Router client components "use client" are acceptable for dashboard UI (fetch after hydration). No SSR data prefetch today because session cookies httpOnly can't be read from RSC easily.
3. `POST /auth/signout` — form body submit with redirect works (current auth.py router: check endpoint exists for signout. Assume OK because dashboard page.tsx uses form action already).
4. No backend new tables needed for M1 portal; existing Account/Store/Payment/ApiKey/WebhookEndpoint/Plan/PlanSubscription models suffice.
5. Plans seed rows already exist in Postgres with codes: starter/growth/scale/enterprise.

## Open Questions
- [ ] **Q1**: KYC file uploads today → M1 accepts URLs pasted by user only. OK? (later M2 file picker + signed URL upload).
- [ ] **Q2**: On dashboard overview metric cards today, real aggregate endpoints (sum paid today) — backend has no route for totals yet. Options: (a) add quick `/v1/metrics/overview` JSON endpoint; (b) derive stats by aggregating the existing payments list results client-side with a `limit=200` fetch; (c) show 0 placeholder until backend builds reports later. Preference? Default assumption is (b) — client-side aggregation limited to last 200 payments so M1 works without new routes.
- [ ] **Q3**: Help center page (/dashboard/help) needed now, or skip for M1 and focus on Overview/Stores/Payments/Keys/Webhooks/Billing/Settings only? Default assumption: include a small Help center accordion page as low-effort, but mark priority=low so it doesn't block core flows.

## Acceptance Criteria

### AC-1: New user Individual sign-in flow lands in portal & has session
- **Type**: `rule`
- **Given**: Incognito window, cookie store empty
- **When**: Open `http://localhost:3001/` → click "Start free" → Google OAuth chooser → allow somvannda@gmail.com (normal test account; the admin console uses duke@chmaba.com)
- **Then**: 302 callback sets cookie `chmabapay_session` → redirects to `/dashboard` → dashboard overview loads, fetches `/v1/me` 200, hero welcome shows account name, plan card shows current plan, sidebar has Overview/Stores/Payments/Keys/Webhooks/Billing/Reports[hidden]/Settings/Sign out
- **Pass Condition**: Every check above observed. Cookie exists (inspect Application tab) and httpOnly=true.
- **Evidence**: Browser DevTools screenshot of (a) cookie tab, (b) `/dashboard` full rendered page, (c) `/v1/me` network 200 payload.

### AC-2: Session auth for stores CRUD (cookie works as well as Bearer)
- **Type**: `rule`
- **Given**: Signed-in Individual account on Starter plan
- **When**: User visits `/dashboard/stores`, clicks "+ Create store", fills name + ABA PayWay paste link, submits
- **Then**: Backend POST `/v1/stores` receives cookie, creates row in Postgres `stores` table, returns 201 StoreOut. Frontend redirects to `/dashboard/stores/[id]`. Table row shows new store.
- **Pass Condition**: 201 response + store row visible in list + max_stores plan gate alert works when attempting >5 stores (UI renders the 400 message from backend inline, not top of screen toast only).
- **Evidence**: cURL equivalent with Cookie header works (run by dev shell or browser network copy-as-cURL), or browser network 2xx log + DB select.

### AC-3: Payment creation via portal cookie works; dev rail simulate mark-paid paid state visible on detail
- **Type**: `rule`
- **Given**: Signed-in user with at least one ACTIVE store (link attached)
- **When**: Create a $5.50 payment with reference_id "ticket_42" via /dashboard/payments wizard.
- **Then**: 201 response with qr_string + checkout_url. Payments list shows the new row pending. Navigate to detail page, img src `checkout_url/qr.png` loads. Click dev "mark paid" (admin only OR allow via session for all in dev mode) → backend marks it paid. /v1/me session cookie auth for payment GET detail still works.
- **Pass Condition**: Status changes pending → paid on detail page + bakong_ref (or dev_simulated_ref) populated.
- **Evidence**: Browser detail screenshot with "paid" pill + network POST 200 simulate.

### AC-4: Keys create with scope restriction by plan type
- **Type**: `rule`
- **Given**: Signed-in Individual Starter account
- **When**: Open `/dashboard/keys` → click Create key
- **Then**: Account scope option is disabled; only store scope selectable. After create, full key string shown ONCE only. If user closes modal & later returns, key row shows `ck_live_****XXXX` masked only, no re-reveal button.
- **Pass Condition**: Attempt via PATCH or re-click cannot reveal original.
- **Evidence**: Key creation modal screenshot, closed return list masked.

### AC-5: Webhook endpoint create + signing secret shown once
- **Type**: `rule`
- **Given**: Signed-in user
- **When**: Add webhook URL `http://localhost:5000/hook` at `/dashboard/webhooks`, check payment.created + payment.paid boxes.
- **Then**: 201 create endpoint. Modal shows signing secret `whsec_*` ONCE. Row in list, action "Rotate signing secret" exists.
- **Pass Condition**: Second modal open no longer reveals the original secret.
- **Evidence**: Screenshot & network response.

### AC-6: Billing upgrade Growth → auto business account_type + shared key option appears
- **Type**: `rule`
- **Given**: Signed-in Starter Individual user
- **When**: `/dashboard/billing` → click Upgrade Growth → 201 change-plan success
- **Then**: Refresh `/v1/me` → account_type=business, allow_account_scope_keys=true. Return to `/dashboard/keys` → Create key wizard shows Account scope tab enabled. Sidebar Sub-Merchants nav item is still Locked. Upgrade Scale → Sub-Merchants unlocks.
- **Pass Condition**: Every flag change observed from /v1/me response & UI reflects it.
- **Evidence**: Network /v1/me diff before/after + sidebar lock state.

### AC-7: Settings KYC submit updates account KYC status + banner at top of Overview
- **Type**: `rule`
- **Given**: Signed-in Individual user kyc_status=none
- **When**: Submit KYC /dashboard/settings KYC tab Individual → fill 3 fields + submit
- **Then**: /v1/kyc 200; reload /v1/me → kyc_status=submitted (or reviewing depending on backend behavior, per current code). Overview hero shows yellow KYC reviewing banner.
- **Pass Condition**: kyc_status field reflects submission; banner visible.
- **Evidence**: Banner screenshot + network responses.

### AC-8: Session sign-out wipes cookie & routes to landing
- **Type**: `rule`
- **Given**: Signed-in user any page
- **When**: Sidebar footer → Sign out → POST form /auth/signout submit
- **Then**: 303 redirect → landing `/`. Cookie `chmabapay_session` is gone or empty. Navigate manually /dashboard → /v1/me 401 → redirect login.
- **Pass Condition**: Cookie gone + /dashboard 302 → login.
- **Evidence**: Application cookie screenshot empty + network 303/302.

### AC-9: Reports hidden for Starter; unlocked Growth+
- **Type**: `rule`
- **Given**: Signed-in Starter user
- **When**: View sidebar
- **Then**: Reports nav item is not rendered. Upgrade to Growth → refresh profile → Reports nav item appears. Click Reports → Payments CSV export button trigger downloads via /v1/reports route (or fallback empty CSV download M1 if reports router placeholder).
- **Pass Condition**: Starter no Reports; Growth+ has it.
- **Evidence**: Sidebar before/after screenshots.

### AC-10: CSS centralization compliance (zero inline React style)
- **Type**: `rule`
- **Given**: git diff of files changed in web/
- **When**: Grep `web/landing/app/**/*.tsx` for ` style=`
- **Then**: ZERO matches (unless user explicitly later requests a single override; today zero matches)
- **Pass Condition**: grep count = 0
- **Evidence**: PowerShell grep output.

### AC-11: next build succeeds with all new portal routes
- **Type**: `rule`
- **Given**: Code complete with all routes added
- **When**: `cd web/landing ; npm run build`
- **Then**: exit 0. route table shows /dashboard/stores, /dashboard/stores/[id], /dashboard/payments, /dashboard/payments/[id], /dashboard/keys, /dashboard/webhooks, /dashboard/billing, /dashboard/settings, /dashboard/sub-merchants, /dashboard/reports, /dashboard/help.
- **Pass Condition**: exit 0, routes present.
- **Evidence**: Build log tail.

### AC-12: BRD §5 Sokha demo exit criteria all have UI steps
- **Type**: `rubric`
- **Dimension**: BRD §5 exit criteria coverage
- **Scale**: 1-5
- **Anchors**: 1 = no UI exists only curl; 3 = half flows in UI + half cURL; 5 = all 4 exit steps clickable in portal.
- **Pass Threshold**: >= 4
- **Evidence**: Walkthrough checklist of each exit criteria item with UI screenshots + network logs.

### AC-13: Plan gate UI consistency & discoverability
- **Type**: `rubric`
- **Dimension**: Plan gate UI
- **Scale**: 1-5
- **Anchors**: 1 = plan violations possible in UI / hidden buttons without explanation; 3 = gates hidden only, no tooltips; 5 = every locked feature shows Lock badge with tooltip "Available on [plan]" and link to billing.
- **Pass Threshold**: >= 4
- **Evidence**: Walkthrough each gate (shared key Starter, Sub-merchants pre-Scale, Reports Starter, Business KYC for live mode) screenshots.
