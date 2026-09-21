# Launch Gap Closure — Tasks

Ordered by severity, then by dependency. Each task names the gap IDs it closes
(see `spec.md`). Status values: `pending`, `in_progress`, `done`, `blocked`.

Legend: **S1** blocker · **S2** major · **S3** minor · **S4** polish

---

## Wave 1 — Security and access control (no decisions needed)

> **Status: COMPLETE (T-01…T-05).** Verified: `ruff check src tests` clean, 37 tests
> pass against Postgres in Docker. Individual task statuses below are flipped in the
> final bookkeeping pass.

### T-01 — Authenticate the four KHQR routes
- **Status**: `pending` · **Priority**: S1 · **Gaps**: G-10 · **Depends on**: none
- **Description**: `POST /v1/khqr/from-link`, `/probe-aba-status`, `/payway/checkout`,
  `/payway/status` in `src/chmabapay/routers/khqr.py` (lines 112, 281, 327, 371) carry
  no auth dependency. Add the account API-key dependency used elsewhere (the same
  `get_current_auth_context` / `require_key` pattern the payments router uses), so a
  `ck_live_` Bearer key is required. Leave `GET /v1/khqr/render.svg` public only if it
  is genuinely intended to be public — confirm against `docs/api.md:22`, which marks it
  "(no auth)". Do not add auth to `render.svg` if the docs are right.
- **Test**: pytest — each of the four returns 401 without a key and 200/4xx-with-key
  with a valid one. Live re-probe: `POST /v1/khqr/from-link` with an empty body must
  change from **422** to **401**.
- **Evidence**: `curl -s -o /dev/null -w "%{http_code}" -X POST https://pay.chmaba.com/v1/khqr/from-link -H "Content-Type: application/json" -d "{}"`

### T-02 — Enforce password sessions on `/v1/admin/*`
- **Status**: `pending` · **Priority**: S2 · **Gaps**: G-42 · **Depends on**: none
- **Description**: `get_hybrid_admin_context` in `src/chmabapay/routers/admin.py:33-51`
  only checks `is_platform_admin`. `session_auth_method(request)` exists
  (`routers/auth.py:141-153`) but is only called by `GET /v1/me`. Add a `Request`
  parameter and fail closed unless the session's `amr == "password"`. Also decide the
  Bearer path: an admin's own `ck_` key currently reaches every admin route. Recommended
  default is to drop the key path for admin (session + password only), which matches
  `web/admin/README.md` and closes the latent privilege-escalation surface noted in P1-2.
  Record the choice in the docstring.
- **Test**: pytest — Google-amr session → 403; password session → 200; API key alone → 401/403.

### T-03 — Admin bootstrap command
- **Status**: `pending` · **Priority**: S1 · **Gaps**: G-40 · **Depends on**: T-02
- **Description**: `cli.py` only has `bootstrap` (creates `admin@chmaba.test`, no admin
  flag, no password) and `set-password` (refuses when the account does not exist,
  `cli.py:141-142`, and never sets `is_platform_admin`). Add
  `grant-admin <email> [--password]` that creates the account if missing, promotes it
  (`is_platform_admin=True`, `whitelabel_enabled=True`), and optionally sets a password
  hash — so day-0 does not require a Google login followed by SSH. Update
  `web/admin/README.md` and `deploy/.env.example` with the exact path, and note the
  Google-first prerequisite for the existing route.
- **Test**: pytest/CLI run — grant-admin on a missing email produces a working admin
  login; `POST /auth/login` succeeds with the set password.

### T-04 — Audit admin and merchant sign-ins
- **Status**: `pending` · **Priority**: S2 · **Gaps**: G-44 · **Depends on**: none
- **Description**: `password_login` (`routers/auth.py:493-539`) writes no audit row.
  Record `auth.login_succeeded` and `auth.login_failed` with the IP and the attempted
  email (never the password). Follow `audit.record`'s existing signature
  (`src/chmabapay/audit.py:20-38`). Do not log on the Google callback a second time —
  the success row belongs to the credential path.
- **Test**: pytest — success and failure each write one row with the expected action.

### T-05 — Per-account login lockout
- **Status**: `pending` · **Priority**: S2 · **Gaps**: G-43 (partial) · **Depends on**: T-04
- **Description**: the only protection on `POST /auth/login` is a 20/min/IP in-process
  limiter keyed on `X-Forwarded-For` (`ratelimit.py:77-79,104-145`). Add per-email
  failure counting with exponential backoff and a lockout window, released on success.
  Keep it dependency-free (in-process) unless the Redis path is already active.
- **Test**: pytest — N failures locks the account; success resets the counter; a
  different email is unaffected.

---

## Wave 2 — Money (blocked on decision D2)

> **Status: COMPLETE (T-06, T-07, T-08).** D2 answered as "build invoicing + HQ store
> now (full fix)". Verified: full suite **204 passed** against Postgres, `alembic
> check` clean at revision `0009`, landing app built in Docker.
>
> **One production action is still required** and cannot be done in code: set
> `CHMABAPAY_HQ_PAYWAY_LINK` in `deploy/.env` to ChmabaPay's own ABA PayWay link, then
> sign in once as the platform admin. Without it the HQ store is never seeded and
> invoice payment answers `503 billing_not_open` (which is now a message a merchant can
> act on rather than a raw 500 code).

### T-06 — Stop the free self-upgrade
- **Status**: `pending` · **Priority**: S1 · **Gaps**: G-24 · **Depends on**: D2
- **Description**: `POST /v1/billing/change-plan` (`routers/billing.py:140-196`) collects
  no payment and applies no proration; the dashboard exposes it one click from
  `billing/page.tsx:396-457`, granting Pro ($59.99/mo, 50 stores, 1M payments) for $0.
  Per D2 (full fix): a move to a **paid** tier must create/reuse an open invoice and only
  apply the plan once that invoice is settled; a downgrade to Free applies immediately.
  The dashboard must say "an invoice is due" rather than "upgraded".
- **Test**: pytest — merchant upgrade without payment is refused; admin change succeeds.

### T-07 — Invoice generation
- **Status**: `pending` · **Priority**: S1 · **Gaps**: G-22 · **Depends on**: D2
- **Description**: `w3_billing.py` is an M1 no-op stub and `PlanInvoice(` is never
  instantiated, so the Invoices table is permanently empty and "Pay with KHQR" is
  unreachable. Implement monthly invoice generation from the active subscription
  (`PlanSubscription.next_billing_at`), one row per account per period, idempotent on
  `(account_id, period_month)` — check the existing unique key before adding one.
- **Test**: pytest — a due subscription produces exactly one invoice; a re-run produces none.

### T-08 — Make invoice payment actually work
- **Status**: `pending` · **Priority**: S1 · **Gaps**: G-23 · **Depends on**: D2
- **Description**: `_get_hq_store` (`routers/billing.py:199-234`) raises
  `500 platform_hq_store_not_configured` when no HQ store exists. In production
  `CHMABAPAY_HQ_STORE_ID` and `CHMABAPAY_HQ_PAYWAY_LINK` are **empty**, so the seed in
  `auth.py:245-283` never runs. Per D2: seed the HQ store + a real PayWay link, or let
  the invoice target a configurable link, and return a friendly error instead of a raw
  500 code. Whichever is chosen must be reflected in `deploy/.env.example`.
- **Test**: pytest — invoice KHQR mint returns 201 with a real link configured; a
  misconfigured deployment returns a human-readable 4xx/5xx, not a code string.

### T-08b — Set the collection link from the admin console (added 2026-09-18)
- **Status**: `pending` (implemented, pending bookkeeping) · **Priority**: S1 ·
  **Gaps**: G-23 · **Depends on**: T-08
- **Description**: T-08 left the HQ store reachable only through
  `CHMABAPAY_HQ_PAYWAY_LINK` in `deploy/.env` plus a sign-in, which means switching
  self-serve billing on still required a shell session — the operator had to edit a file
  on the server to fill in a link they already had in their hand. Added
  `GET /v1/admin/hq-store` and `PUT /v1/admin/hq-store/link`, plus a **Plan fee
  collection** panel on the admin overview, so the setting is a field. The merchant
  account id is derived from the pasted link; the host is validated (this field decides
  where the platform's own revenue lands, so a pasted account number or a foreign URL is
  refused); both writes are audit-logged (`hq_store.created`, `hq_store.link_set`).
  Resolution order moved into `services.billing.resolve_hq_store` so the console and the
  billing route cannot disagree, and the console reports *which* source won — a store
  pinned by `CHMABAPAY_HQ_STORE_ID` deliberately beats the console, and an operator needs
  to be told that rather than left wondering why their save changed nothing.
- **Test**: pytest — an operator can set the link and the billing route resolves it; a
  second save replaces rather than duplicates; a non-PayWay link is refused with nothing
  written; a merchant gets 403. Browser — the panel on the admin overview.
- **Note**: the environment variable is still honoured and still wins. It is now the
  override rather than the only way.

---

## Wave 3 — Legal gate (blocked on decision D1)

> **Status: COMPLETE (T-09, T-10).** D1 answered as "remove the draft banner now".
> Verified: `tests/test_compliance.py` + `tests/test_audit.py` = 45 passed against
> Postgres. The landing app still needs its Docker build check (Wave 7).

### T-09 — Terms acceptance gate
- **Status**: `pending` · **Priority**: S1 · **Gaps**: G-21 · **Depends on**: D1
- **Description**: `POST /v1/me/terms` works and validates the version, but no portal
  code calls it and nothing gates on `terms_accepted_at`. Add a blocking acceptance
  screen in the post-OAuth flow (before the dashboard renders) that calls the endpoint
  with the displayed version, and enforce server-side in `GET /v1/me`'s consumers so an
  API key cannot bypass it. Reuse `settings.terms_version`
  (`config.py:126-131`) — do not hardcode the version in the UI.
- **Test**: pytest — a new account is blocked until acceptance; a stale version returns
  409; an accepted account passes. Browser check on the deployed flow.

### T-10 — Remove the draft banner (D1)
- **Status**: `pending` · **Priority**: S1 · **Gaps**: G-01 · **Depends on**: none
- **Description**: the Terms and Privacy pages state in production that they "must be
  reviewed and replaced before ChmabaPay accepts real merchant traffic"
  (`terms/page.tsx:34-42`, `privacy/page.tsx:27-32`). Per **D1**, remove the draft banner
  from both pages and the now-unused `legal-draft-*` CSS, leaving the body text as-is.
  Pair this with T-09 so acceptance of the text means something the moment the banner
  goes. The lawyer review stays open as `P1-4` and must not be marked closed.
- **Test**: browser — neither page renders a draft notice; the acceptance gate still
  blocks an un-accepted account.

---

## Wave 4 — Customer portal correctness

> **Status: COMPLETE (2026-09-18).** T-11…T-18 shipped. One documented behaviour change:
> a store link that is not an ABA PayWay link is now **refused** rather than stored and
> normalised, and a link PayWay does not recognise is refused too — see T-11.
> `tests/test_store_links.py` and `tests/test_account_security.py` are new. Full suite
> 231 passed, ruff clean, landing builds in Docker.

### T-11 — Validate the ABA PayWay link on create and link-set
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-26 · **Depends on**: none
- **Shipped**: `services/stores.py:resolve_link` — a shape check (PayWay host or a 4–64
  char slug) that needs no network, then `payway_parser.verify_link` asking PayWay about
  the slug. `ok` → `verified` and promotes to `active`; `not_found` (PayWay 4xx) → 400
  `payway_link_not_found: …`; `inconclusive` (timeout, non-4xx, unreadable page) → stored
  as `LINK_UNVERIFIED` and the store stays `draft`, because an ABA outage must not block a
  signup and a draft store with a link still takes payments. `verification` is now a
  required kwarg on `attach_link_to_store`, so no caller can claim it by accident. Errors
  are prefixed so the wizard shows them under the link field
  (`components/portal/apiError.ts:describeApiError`). `_BARE_SLUG`/host check in the
  wizard's `isFormValid` too. Note: **`tests/test_payments.py` changed** — a `bakong://`
  link used to be accepted and normalised to `aba_payway`; it is now refused.
- **Description**: the client only checks `length >= 6`
  (`stores/new/page.tsx:24-26`) and the backend stores anything, hardcoding
  `verification=LINK_VERIFIED` (`services/stores.py:130-138`), so a typo produces an
  "active, verified" store that fails later with `502 payway_hosted_error`. Reuse the
  existing SSR probe (`services/payway_parser.py`, `_extract_slug`) to validate the slug
  and merchant on create and on `PUT /v1/stores/{id}/link`; set verification from the
  result and only promote `draft → active` when validation passes. Return a field-level
  error the wizard can render.
- **Test**: pytest — a bogus slug is rejected with a clear detail; a good slug activates
  the store; the wizard shows the error inline.

### T-12 — Make `external_id` discoverable
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-27 · **Depends on**: none
- **Shipped**: the store overview's detail card now shows the full public ID and the
  merchant ID (`external_id`) with a copy affordance, replacing a truncated public ID that
  could not be copied; the store settings page has an editable Merchant ID field (it is
  `StorePatch`-writable) with a note that changing it breaks integrations still sending the
  old value, plus the store ID as a copy field. New shared
  `components/portal/CopyField.tsx` (used by both pages).
- **Description**: `external_id` is the value required for `merchant=`, but it is shown
  nowhere after creation. Render it on the store detail and store settings pages, with a
  copy affordance, and allow editing where the API already supports it
  (`StorePatch`).
- **Test**: browser — the id created in the wizard is visible and copyable afterwards.

### T-13 — Fix the webhook enable/disable bug
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-28 · **Depends on**: none
- **Shipped**: `WebhookOut` now publishes `enabled` (derived from `status == "active"`) and
  the console type made it required, so the edit form can no longer default a disabled
  endpoint to enabled and silently re-enable it on save. `payment.superseded` and
  `payment.reversed` added to `KNOWN_EVENTS`.
  (`routers/webhooks.py`; `dashboard/webhooks/page.tsx`.)
- **Description**: `WebhookOut` (`routers/webhooks.py:39-45`) omits `enabled`, so the
  edit modal's `endpoint?.enabled !== false` init
  (`webhooks/page.tsx:489`) resolves to `true` and saving re-enables a disabled
  endpoint. Either add `enabled` to `WebhookOut` (derived from `status == "active"`) or
  initialise the checkbox from `status`. Prefer the schema fix so every consumer agrees.
- **Test**: pytest — `enabled` is returned and mirrors `status`. Browser — disabling,
  reopening the modal and saving leaves it disabled. Also add the two missing event
  types (`payment.superseded`, `payment.reversed`) to the checkbox list
  (`webhooks/page.tsx:18-23`).

### T-14 — Graceful limit/quota errors
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-29 · **Depends on**: none
- **Shipped**: `components/portal/apiError.ts` is now the one place a machine code becomes
  copy — `describeApiError`/`readApiErrorInfo`/`readApiError` map `quota_exceeded`,
  `amount_too_low`, `amount_too_high`, `store_disabled`, `merchant_store_disabled`,
  `whitelabel_not_enabled` and `offline_qr_requires_a_confirmation_source`, flag
  `quota_exceeded` (and the `Max stores (n)` prose the store cap answers with) as
  `upgrade: true`, and report `field: "link"`. Because the translation lives in
  `readApiError`, every consumer — keys, webhooks, payments, settings — gets prose without
  being touched. `payments/new` and `stores/new` render the upgrade CTA from the flag
  instead of the old `detail.includes("Upgrade")` / `includes("max")` substring guessing.
- **Description**: `quota_exceeded`, `amount_too_high`, `amount_too_low`, `store_disabled`
  and `offline_qr_requires_a_confirmation_source` reach the user as raw codes on
  payments/keys/webhooks, while `stores/new` maps them correctly
  (`stores/new/page.tsx:87-99`). Extract that mapping into one shared helper and use it
  everywhere, with an upgrade CTA for 402.
- **Test**: browser — triggering the quota produces human copy plus a working upgrade link.

### T-15 — Explicit error states instead of silent empty
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-30 · **Depends on**: none
- **Shipped**: per-panel error state plus a Retry in `dashboard` overview (metrics,
  stores, activity — with `figuresReady`/`isFirstRun` gated so a failed read shows "—" and
  "this is not an empty account" instead of `$0.00` and "No stores yet"), `billing`
  (plans, subscription, invoices), `keys` and store `settings` (which now refuses to render
  the form at all rather than pre-filling blanks a save would write over real values).
  `reports` already had per-action error states for both downloads; its one remaining
  `catch {}` is the `csv_export_enabled` probe, whose only effect is that the button stays
  enabled — the 403 it would then hit is handled with copy, so it was left alone.
- **Description**: blanket `catch {}` in `keys`, `dashboard` overview, `billing`,
  `reports` and store `settings` renders a fetch failure as an empty/zero success state.
  Add a per-panel error state with a retry, and keep the 401 path redirecting via
  `useSession.ts`.
- **Test**: browser — with the API forced to 500, each panel shows an error, not zeros.

### T-16 — Account security self-service
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-31 · **Depends on**: D4
- **Shipped**: three routes in `routers/account.py` plus a Security tab.
  `POST /me/email` requires the current password (401 `invalid_password`) and refuses
  outright with 409 `email_change_requires_password` for a Google-only account — there is no
  mail provider, so there is no way to verify a new address and a session alone must not be
  able to redirect an account's mail. `email` was removed from `AccountPatch` and
  `extra="forbid"` added, so `PATCH /v1/me {"email": …}` is now 422 rather than silently
  applied. `POST /me/password` rotates the password (checks the old one, refuses
  `password_unchanged`, 409 `no_password_set` for a password-less account — a session must
  not be able to mint a credential). `DELETE /me` anonymises the account
  (`erased-{id}@chmabapay.invalid`, name cleared, `google_sub`/`password_hash` nulled,
  suspended so the session dies), disables its stores, revokes its keys and stops its
  webhooks, and **keeps the payments** as the accounting record the privacy policy already
  promises to retain; it needs a typed confirmation and the password, and a platform admin
  cannot erase itself. `GET /v1/me` gained `has_password` (a bool, never the hash) so the UI
  can explain instead of offering a form that would be refused. 8 tests in
  `tests/test_account_security.py`. Audit rows store email *domains*, not addresses, so a
  log cannot outlive an erasure.
- **Description**: no erasure path and no password rotation exist, and `PATCH /v1/me`
  (`routers/account.py:75-96`) changes email with no verification. Per D4, add
  `DELETE /v1/me` (or a documented erasure request flow), a password-change endpoint +
  settings UI for accounts that have a password, and re-verification before an email
  change is applied.
- **Test**: pytest — erasure removes/anonymises per the retention policy; password
  change invalidates the old password; an unverified email change is refused.

### T-17 — Correct the help FAQ
- **Status**: `complete` · **Priority**: S3 · **Gaps**: G-32 · **Depends on**: D6
- **Shipped**: the "How do I switch plans?" answer now matches `routers/billing.py:change_plan`
  — a move to a paid plan takes effect when its invoice is paid (the invoice is raised on
  selection), and a move to Free applies immediately. The two "every plan includes CSV
  exports" claims were **removed** rather than restated: `Reports` is named as where exports
  live, and the plan answer defers to the Billing page's feature comparison. That is true
  today (where the claim depends on a per-plan `csv_export_enabled` flag an operator can
  switch off) and stays true after T-33 deletes the gate — restating the positive claim now
  would have to be rewritten again then.
- **Description**: `help/page.tsx:25-28` claims CSV export is in every plan and that
  downgrades apply from the next billing date; neither matches the backend. Correct both
  once D6 fixes the real CSV policy.
- **Test**: content review against `routers/reports.py` and `routers/billing.py`.

### T-18 — Fix the dead sign-out fallback
- **Status**: `complete` · **Priority**: S4 · **Gaps**: G-33 · **Depends on**: none
- **Shipped**: the dead `GET /auth/logout` fallback is gone from
  `dashboard/layout.tsx:handleSignOut`. There is only `POST /auth/signout`; a comment records
  why, and the existing final `window.location.href = "/"` remains the real fallback so a
  failed call still leaves the dashboard rather than stranding the user.
- **Description**: `dashboard/layout.tsx:149-160` falls back to `GET /auth/logout`, which
  does not exist. Remove the fallback or point it at `/auth/signout`.
- **Test**: browser — sign-out from both the menu and the fallback path lands on `/`.

---

## Wave 5 — Admin console gaps

> **Status: COMPLETE (2026-09-21).** T-19…T-24 shipped. The console can now act, not only
> report: a plan can be assigned without an invoice, an invoice settled/waived/credited,
> a leaked key revoked without taking the merchant offline, one store disabled, one
> webhook delivery retried, and a disputed payment reconciled or credited by hand. The
> overview gained the "needs attention" block and a paid-today volume, and the failure-rate
> alert now covers a rail that is moving and failing.
> `tests/test_admin_actions.py` and `tests/test_admin_overview.py` are new.
> Full suite **256 passed** (241 before the two new files), ruff clean, admin image builds
> in Docker with `next build` including type and lint checks.

### T-19 — Admin payment detail + dispute resolution
- **Status**: `complete` · **Priority**: S1 · **Gaps**: G-41 · **Depends on**: none
- **Description**: add `GET /v1/admin/payments/{public_id}` exposing `bakong_ref`,
  `gateway_status_raw`, `attempt_history`, `reissued_from`, `detection_closed_at`;
  `POST /v1/admin/payments/{id}/reconcile` calling the existing `reconcile_payment`;
  `POST /v1/admin/payments/{id}/mark-paid` with a mandatory reason and an audit row; and
  a webhook re-deliver action. Surface all of it on the payments page. This is the
  difference between "I can see the problem" and "I can fix the customer's problem".
- **Test**: pytest — each route is admin-gated, each mutation writes an audit row, and
  reconcile on a paid payment is a no-op. Browser — the dispute flow end to end.
- **Shipped**: four routes in `routers/admin.py` (`GET /payments/{public_id}`,
  `POST .../reconcile`, `POST .../mark-paid`, `POST .../redeliver`), each behind
  `get_hybrid_admin_context`; `mark-paid` requires a 3–255 char reason, refuses `paid`
  /`failed`/`reversed`, settles through `services.payments.mark_paid` (so the ledger,
  plan invoice, successor retirement and `payment.completed` webhook all still run) and
  merges a `manual_mark_paid` note into `gateway_status_raw` instead of wiping the
  hosted session it would otherwise replace; `redeliver` resets each of the payment's
  deliveries to due-now with a fresh attempt budget (the sender's own scan picks them
  up, so no queue enqueue is needed); `reconcile` short-circuits to `["already_paid"]`
  on a settled payment, so a no-op writes no audit row. Console: new
  `web/admin/app/payments/[public_id]/page.tsx` (rail evidence, raw payload, resolve
  actions, per-payment deliveries) and the payment id in the feed now links to it.
  Eight tests in `tests/test_admin_plans.py`.

### T-20 — Admin billing and access actions
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-46 · **Depends on**: T-06
- **Description**: add `PATCH /v1/admin/accounts/{id}/plan` (audited),
  `POST /v1/admin/invoices/{id}/mark-paid|waive|credit`, `POST /v1/admin/keys/{id}/revoke`
  and `POST /v1/admin/stores/{id}/disable`, with UI. Today a leaked key can only be
  killed by suspending the entire merchant, and billing corrections need DB access.
- **Test**: pytest — each route is gated, audited, and idempotent where it should be.
- **Shipped**: four routes in `routers/admin.py`.
  `PATCH /accounts/{id}/plan` cancels whatever the account is on and activates the named
  plan with **no invoice and no proration** — the one route that grants a paid tier with no
  money attached, so `reason` (3–500 chars) is mandatory and the audit row records
  `from_plan`, `to_plan`, `monthly_fee_cents` and the reason; re-selecting the current plan
  is a 400 `plan_unchanged` rather than a "granted" row for nothing. `POST
  /invoices/{id}/resolve` takes `action` = `mark-paid` | `waive` | `credit` + reason, and
  only `mark-paid` writes `paid_at` (a waiver is not income, and stamping a settlement date
  on one would make a revenue report count money that never arrived); it activates the
  subscription through `billing_svc.activate_subscription`, the same call the rail path
  makes, so an invoice settled by hand and one paid by QR leave the account identical. It
  was one route rather than three because the activation step and audit shape are identical
  and three copies would be three places to drift. `POST /keys/{id}/revoke` and `POST
  /stores/{public_id}/disable` are idempotent and **quiet on a repeat** — a second call
  changes nothing and writes no second audit row, so the trail reads "this key was revoked",
  not "revoked five times because an operator clicked twice".
  Console: the account detail page gained a plan picker (plans fetched from
  `/v1/admin/plans` so it cannot offer a code the API would reject), a per-invoice Resolve
  modal (action, reason, optional credited amount), a Keys panel with Revoke, and Disable
  on each store row; `lib/apiError.ts` translates the refusals an operator can realistically
  hit (`last_platform_admin`, `plan_unchanged`, `invoice_already_*`) because printing a
  machine code at an operator makes a working guard look like a bug. The invoices list
  page's status filter was also repaired — it offered `draft`/`issued`/`overdue`, none of
  which `services/billing.py` ever writes, so it silently filtered to nothing.
  Ten tests in `tests/test_admin_actions.py`.

### T-21 — Overview operational signals
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-45 · **Depends on**: none
- **Description**: extend `GET /v1/admin/overview` (`routers/admin.py:942-997`) with a
  "needs attention" block — payments pending past expiry, detection-closed-unpaid,
  webhook deliveries failed in the last 24 h, worker heartbeat age, queue depth, today's
  paid volume — sourced from existing models/counters, and render it above the stat
  cards with deep links to the filtered lists.
- **Test**: pytest — each counter returns a number and matches a seeded fixture.
- **Shipped**: `GET /v1/admin/overview` now returns `needs_attention` (a list of
  `{key, label, detail, count, severity, href}`), `ops`, `paid_today_count` and
  `paid_today_cents`. The payment counters are database queries in `_needs_attention`;
  `detection_closed_unpaid` excludes reversals because a refunded payment's detection is
  closed too and the money did arrive — it came back. Worker signals come from
  `_worker_signals`, which reads the shared heartbeat keys and the transport's own
  backlog. **It reports "not measurable" rather than zero under the in-process transport**:
  the API *is* the worker there, so there is no cross-process stamp and no shared backlog,
  and a reassuring `0` would be the one wrong answer — `watched: false` says so instead and
  the stale-queue card is simply absent. Redis being unreachable is reported as an error
  string, not raised: an operator opening the console because something is wrong should
  still get the payment counters.
  Deep links needed two filters that `status` cannot express — `pending` alone includes
  codes a customer is scanning right now — so `list_all_payments` gained
  `attention=pending_past_expiry|detection_closed_unpaid`, and the payments and deliveries
  pages now seed their filters from the query string (the deliveries page did not, so the
  failure link would have been silently ignored).
  `healthcheck.queue_heartbeat_ages` was extracted from `stale_queues` so the console and
  the probe read the same stamps through one code path. New
  `tests/test_admin_overview.py` (4 tests, including the count-to-rows link and the
  in-process "not measurable" case).

### T-22 — Admin 401 redirect
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-47 · **Depends on**: none
- **Description**: page-level fetches show the literal `invalid_session`
  (`web/admin/lib/apiError.ts:7-19`). Add a shared fetch helper that redirects to
  `/login?next=…` on 401, matching `useSession.ts` in the customer app.
- **Test**: browser — expiring the session mid-navigation lands on the login page.
- **Shipped**: `web/admin/lib/apiFetch.ts` wraps `fetch`, sending credentials and
  redirecting to `/login?next=<current>` on 401 or on the `403 password_session_required`
  a key-authenticated operator hits. A `redirecting` flag makes it fire once, so a page
  with several parallel loads does not queue several redirects. Every page-level call site
  was switched over (11 files, 20 calls); only `app/login/page.tsx` and
  `AdminShell.tsx`'s `/auth/signout` still call `fetch` directly, which is correct —
  the login page cannot redirect to itself, and a sign-out must run.

### T-23 — Webhook delivery retry + failure-rate alert
- **Status**: `complete` · **Priority**: S3 · **Gaps**: G-48 · **Depends on**: T-19
- **Description**: add `POST /v1/admin/deliveries/{id}/retry` (reset `next_attempt_at`,
  audit row) plus a Retry button on the deliveries page, and a `webhook_failure_rate`
  condition in `alerts.py` (>10 % failing over 15 min) — today only queue backlog is
  alerted, so a rail failing slowly never pages.
- **Test**: pytest — retry reschedules and audits; the alert fires on a seeded failure ratio.
- **Shipped**: `POST /v1/admin/deliveries/{id}/retry` resets the delivery to due-now with a
  **fresh attempt budget** (a delivery that exhausted `webhook_max_attempts` is terminal,
  and the merchant who just fixed their endpoint should get a real attempt rather than one
  refused as spent); it is quiet on a repeat, like the other operator actions. Retry button
  on every row of the deliveries page.
  `alerts.py` gained the `webhook_failure_rate` condition. It cannot come from the queue
  transport: the webhook sender is a sweep that scans `event_deliveries` for due rows and
  reports `ok` whatever an individual POST did, so the counters never see a delivery fail —
  it reads the database through a callable the watcher is *given*
  (`DeliveryOutcomes`, defaulting to `webhook_delivery_outcomes`), which is also what keeps
  the watcher testable without a database. Bounded by a sample floor
  (`ALERT_WEBHOOK_FAILURE_MIN_SAMPLE`, default 20) so one failed delivery on a quiet night
  cannot page, and the threshold is strict (`>`), so exactly 10 % is not a failure.
  **A measurement that cannot be taken does not clear a firing condition** — a database
  blip keeps the last verdict, or the channel would send "RESOLVED" about a rail that is
  still broken. New settings in `config.py` and `.env.example`; six tests in
  `tests/test_observability.py` plus the retry tests in `tests/test_admin_actions.py`.

### T-24 — Admin account detail polish
- **Status**: `complete` · **Priority**: S3 · **Gaps**: G-49 · **Depends on**: T-20
- **Description**: show per-account key/store state and plan usage against limits, state
  suspension's blast radius in the confirm copy, and add a last-admin guard so the final
  platform admin cannot suspend themselves.
- **Test**: browser + pytest.
- **Shipped**: `GET /v1/admin/accounts/{id}` now returns `keys` (name, prefix, mode,
  status, last used, revoked), `limits` (the plan's `max_stores`,
  `max_keys_per_account`, `payments_included`) and `usage` (`keys_active`,
  `payments_this_month`), so a decision to suspend, revoke or comp is made with the
  merchant's actual position visible. The console renders stores as "3 of 5", keys as
  "2 of 5" and this month's payments against the included allowance, and lists the account's
  keys with their revocation state. Suspension's blast radius is stated twice — as a hint
  under the Status row and in a confirm step before it happens: signs them out everywhere,
  refuses every key, stops new payment codes, pauses webhooks, and **does not recall codes
  already in a customer's hands**, with a pointer to per-store Disable for the narrower
  case. `PATCH /accounts/{id}` refuses to suspend the last active platform admin with 409
  `last_platform_admin`: suspension is enforced at sign-in and on every authenticated
  request, so doing it to the final admin locks the console for good with no route back in
  that does not involve a database session. `_active_admin_count` deliberately excludes
  suspended admins — they are not a way back in.

---

## Wave 6 — Documentation, OpenAPI and copy

> **Status: COMPLETE (2026-09-21).** T-25…T-34 all closed. The docs page now describes what
> the routers actually do (T-25, T-28), the advertised event list matches the events the
> platform emits (T-26), the webhook test proves the real delivery shape (T-27), the
> published OpenAPI declares its two credentials, its real error bodies and hides the
> operator surface (T-29), the landing page no longer contradicts the docs about where money
> goes (T-30), `/api/docs` no longer scrolls sideways on a phone (T-31), metadata and the
> orphaned onboarding route are fixed (T-32), the CSV gate is deleted rather than
> documented (T-33), and the support posture is stated instead of implied (T-34).
> Verification: pytest **265 passed, 2 deselected**; `ruff check` clean; `next build` for
> `web/landing` in Docker (type + lint checks included) succeeded; browser checks at
> 390/430/480/600/768px and `curl` checks of the 404 title and the `/api/docs` canonical
> all pass against the running stack.

### T-25 — Fix the docs-page misstatements
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-13, G-14, G-15, G-18, G-20, D5 · **Depends on**: D5
- **Description**: in `web/landing/app/api/docs/page.tsx` correct: `checkout_url` on
  `GET /v1/payments/{id}` (not returned), reissue-on-`superseded` (rejected), the
  `probe-aba-status` "first-priority health check" framing, `attempts` → `attempt_count`,
  the `hosted_qr` default, `KeyCreate.name` requiredness, `link` requiredness,
  `StoreCreate.city` default, and add a one-line note on decimal `amount` vs `*_cents`.
- **Test**: content diff against the routers; the page still builds in Docker.
- **Shipped** (2026-09-21): every listed item corrected against the routers, plus three
  more the same read turned up. Per D5 the page now describes what the code does:
  `checkout_url` is built by the create call only (`get_payment` passes `None`), so the
  Payments group no longer claims "every payment returns" one; reissue accepts **only**
  `expired`/`failed` and the 409s are named (`payment_already_paid`, `payment_reversed`,
  `payment_not_expired` — the last one covering `superseded`, which already has a
  replacement); `probe-aba-status` is described as the advisory page-scrape it is, with
  `payway/status` named as the authoritative check and its query params listed;
  `attempts` → `attempt_count` plus `response_body_preview` and the paging params;
  `hosted_qr` is *omitted by default, meaning auto* (not "on"); `KeyCreate.name` is
  required and the 403 `terms_not_accepted` is documented; `LinkCreate` requires
  `raw_link` + `merchant_account_id`; `city` defaults to "Phnom Penh"; and the
  decimal-`amount`-vs-integer-`*amount_cents` split is stated on the payment read route.
  Also fixed while in there, because the same diff exposed them: `change-plan` was still
  documented as applying immediately with no payment collected, which Wave 2 replaced with
  a pending subscription + invoice + `payment_required`; `PATCH /v1/me` was documented as
  accepting `email`, which `extra="forbid"` turns into a 422 — `POST /v1/me/email` is now
  listed instead; and the Signature section no longer names `payment.scanned`, leaving the
  four real events as the only list on the page. Verification: `tsc --noEmit` on
  `web/landing` exit 0.

### T-26 — Fix the advertised events
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-11 · **Depends on**: D5
- **Description**: `payment.scanned` (dev-only) and `payment.failed` (no producer) are
  advertised in the docs, the docs-page event list and the portal's webhook checkboxes.
  Per D5: remove them from every surface, or implement the producers in the reconciler.
- **Test**: pytest — every advertised event can be produced by a real code path.
- **Shipped** (2026-09-21): took D5's first branch — removed them everywhere rather than
  invent producers. The four events the platform actually emits are
  `payment.completed`, `payment.expired`, `payment.superseded`, `payment.reversed`, and
  that list is now the only one published: `web/landing/app/api/docs/page.tsx` (event
  list), `web/landing/app/dashboard/webhooks/page.tsx` (`KNOWN_EVENTS` — this is what
  drove the checkboxes), `web/landing/app/dashboard/[public_id]/page.tsx` (step copy),
  `docs/api.md` (`X-ChmabaPay-Event` values), `docs/data-model.md` (events table row) and
  `docs/pos-provider-integration.md`. `BUSINESS_REQUIREMENTS.md` carries a dated NOTE
  saying *why* `payment.scanned` and `payment.failed` are not offered (dev-gateway only /
  no producer), so the next person to look for them finds the reason instead of the gap.
  Verification: repo-wide grep for the two retired names returns only that NOTE.

### T-27 — Make the webhook test payload identical to a real one
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-12 · **Depends on**: none
- **Description**: `routers/webhooks.py:88-111` builds a different shape from a real
  delivery, omitting `financial` (the field the docs tell integrators to branch on),
  `data.merchant`, `paid_at`, `expires_at`. Build the synthetic event with
  `webhooks.py`'s real builder so "send test" validates the integration the customer
  actually has, and set the `User-Agent` the real sender sets.
- **Test**: pytest — the test payload's keys are a superset of the documented set and
  match a real delivery's shape.
- **Shipped** (2026-09-21): the "Send test" button no longer has a shape of its own.
  `routers/webhooks.py::_build_test_event_payload` now persists nothing and instead
  constructs transient `models.Payment` / `models.Store` rows and hands them to the real
  `webhooks.build_event_payload(...)`, so the test delivery is produced by the same code
  that produces every live one — including `financial`, `data.merchant`, `paid_at` and
  `expires_at`. The headers were the same class of problem: the test used to sign and
  frame its own request, so it proved the signature the test made rather than the
  signature a customer receives. New `webhooks.delivery_headers(payload, secret,
  event_type)` is now the single place the wire format is decided
  (`Content-Type`, `X-ChmabaPay-Event`, `X-ChmabaPay-Signature: t=…,v1=…`, and
  `WEBHOOK_USER_AGENT = "ChmabaPay-Webhook/1.0"`), and all three senders use it —
  `webhooks._deliver`, `workers/w2_webhook_sender.py` and `routers/webhooks.py::test_webhook`.
  Verification: `tests/test_admin_actions.py` + full suite (see T-35).

### T-28 — Mark the Bakong ledger group unavailable and document the two live endpoints
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-16, G-50 · **Depends on**: D8
- **Description**: move the 9 Bakong Ledger Lookup endpoints into a visibly unavailable
  group (or hide them) and document `GET /v1/transactions/check-status/{payment_public_id}`
  and `POST /v1/transactions/verify-payment/{payment_public_id}`, which work today
  without Bakong credentials for hosted-session payments.
- **Test**: content review; live probe of the two documented endpoints.
- **Shipped** (2026-09-21): the docs page now separates the two things it used to conflate.
  A new **Payment Reconciliation** group documents the pair that actually answers
  `GET /v1/transactions/check-status/{payment_public_id}` and
  `POST /v1/transactions/verify-payment/{payment_public_id}`, with their query parameters
  (`prefer_aba_page`, `aba_slug_hint`, `mark_paid` / `use_hash`), the `PAID/PENDING/FAILED/
  UNKNOWN` + `source` response, and the fact that neither needs Bakong credentials for a
  payment that has a hosted ABA session. The nine ledger lookups stay listed — an
  integrator who has heard of the Bakong ledger should find them and see why they are not
  usable — but the group is marked unavailable: `unavailable: true` in the data renders a
  "Not available" badge and an `is-unavailable` card (dashed border, muted rows, new
  `.docs-endpoint-title-row` / `.docs-endpoint-badge` / `.docs-endpoint-card.is-unavailable`
  rules in `globals.css`, no inline styles), and the summary states the 503
  `bakong_not_configured` outright rather than in passing. This follows D8's default of
  leaving Bakong credentials empty and marking the ledger endpoints unavailable.
  Verification: `tsc --noEmit` on `web/landing` exit 0; the live probe of the two
  documented endpoints is part of T-35, since it needs a deployed payment to point at.

### T-29 — OpenAPI hygiene
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-17 · **Depends on**: T-01
- **Description**: add `components.securitySchemes` (bearer + session cookie) and apply
  security per route; declare the real error responses (201/402/403/404/409/410/503) via
  `responses={}` on the main operations; and set `include_in_schema=False` on the admin
  router so the internal surface is not published in the public `openapi.json`. Confirm
  the docs page and FastAPI's own `/docs` still render.
- **Test**: pytest — fetch `/openapi.json`, assert the schemes exist, that the four
  KHQR routes declare security, and that no `/v1/admin/` path appears.
- **Shipped** (2026-09-21): new `src/chmabapay/openapi.py` is the one place the shared
  metadata lives. Two schemes — `ApiKey` (HTTP bearer, `ck_live_…`) and `SessionCookie`
  (`apiKey` in cookie, `chmabapay_session`) — both declared with `auto_error=False` so
  they *document* rather than enforce; enforcement stays with the real dependency, which
  also answers the right status (401, not `HTTPBearer`'s 403). `AUTH_SECURITY` /
  `SESSION_SECURITY` and the `ErrorOut` body plus `AUTH_ERRORS` (401/403), `QUOTA_ERROR`
  (402), `CONFLICT_ERROR` (409) and `UPSTREAM_ERROR` (502) ride at router level on
  payments, keys, stores, webhooks, reports and transactions, and on the individual
  billing and account routes that are session-only; khqr's router deliberately carries
  none because `GET /v1/khqr/render.svg` is public, so each of its four outbound routes
  declares `AUTH_SECURITY` by hand. `/pay/{id}/qr.svg` and `/pay/{id}/status` got the
  404/410 `ErrorOut` bodies they actually raise. `include_in_schema=False` on the admin
  and dev routers keeps the operator surface out of the published document while the
  routes keep dispatching. New `tests/test_openapi_schema.py` (7 tests) pins all of it:
  the schemes exist, every route under the credentialed prefixes declares security, the
  listed public paths declare none, the four KHQR routes allow exactly
  `{ApiKey, SessionCookie}` and document 401, `POST /v1/payments` documents 402, no
  `/v1/admin` or `/_dev` path is published, and both hidden routers still serve (401/403,
  never a 404). Verified: full suite **263 passed, 2 deselected**; `ruff check` clean.

### T-30 — Landing copy corrections
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-02, G-03, G-04 · **Depends on**: none
- **Description**: remove Bakong as a *destination* claim
  (`page.tsx:21,99,339`), reword the late-settlement line so "settle" is not used in two
  senses on one page (`:339-340` vs `:355`), and fix the `Customers` nav anchor so it
  points at content that exists.
- **Test**: content review against `page.tsx:41` of the docs page ("ABA PayWay is the
  only supported destination").
- **Shipped** (2026-09-21): G-02 — the three destination claims are gone. The setup card
  now says "connect your ABA PayWay link", the hero says "KHQR payments over ABA PayWay",
  and the pricing footnote says "straight to your own ABA PayWay account"; a grep for
  `Bakong` on `web/landing/app/page.tsx` returns nothing, so the landing page and
  `/api/docs` now agree that ABA PayWay is the only destination. G-03 — the two senses of
  "settle" are separated: the footnote is now "does not hold funds and does not move them
  on your behalf" (the banking sense is reserved for "money settles straight into your own
  ABA PayWay account" above it), and the late-payment quote reads "we keep confirming and
  record the payment as paid anyway". G-04 — the nav item no longer points at a section
  about something else: the section id is `late-payments` and both the header link
  (`layout.tsx`) and the phone menu (`MobileNav.tsx`, whose docstring listed the old label
  too) now read "Late payments", which is what the section is actually about.
  Verification: `tsc --noEmit` on `web/landing` exit 0; grep for `#customers` across
  `web/landing` returns no anchors.

### T-31 — API docs mobile overflow
- **Status**: `complete` · **Priority**: S3 · **Gaps**: G-06 · **Depends on**: none
- **Description**: `.docs-signature-left` / `.docs-signature-panel` force a 587px min
  width, so the page scrolls horizontally below ~611px. Stack the section at the mobile
  breakpoint. Respect the no-inline-styles rule — edit `globals.css`.
- **Test**: browser at 390/430/480/600px — `document.scrollWidth === viewport width`.
- **Shipped** (2026-09-21): two independent overflow sources, found by measuring rather
  than by reading the CSS. The reported one: `.docs-signature-left` and
  `.docs-signature-panel` had no rule at all, so as grid items they kept the default
  `min-width: auto` and could never shrink below the Node.js sample (~82 columns of 12px
  monospace). The `<pre>`'s own `overflow: auto` never got a chance to run, because its
  column was already wide enough to hold every line. `min-width: 0` on both items lets the
  code block scroll inside the page instead of the page scrolling around it. The second
  one only showed up once the first was fixed: the endpoint cards overflowed at ≤430px
  because `search_type=hash|md5|short_hash|instruction_ref|external_ref` is 62 characters
  with no space in it, and `.docs-description`'s min-content width is what sized the grid
  track. `overflow-wrap: anywhere` lets both the line and the min-content size break.
  Verified in the browser: `document.documentElement.scrollWidth` is within 1px of the
  viewport at 390, 430, 480, 600 and 768px (the 15px residual is the scrollbar), an
  offender scan at 390px returns zero elements, and the long token is still fully visible
  rather than clipped. No inline styles: both edits are in `web/landing/app/globals.css`.

### T-32 — Metadata and dead routes
- **Status**: `complete` · **Priority**: S4 · **Gaps**: G-05, G-07 · **Depends on**: none
- **Description**: give the 404 page its own `<title>`, give `/api/docs` its own
  `og:title`/`twitter:title` plus a canonical, and delete the orphaned
  `/onboarding/account-type` stub (its own migration `0008_drop_account_type.py` removed
  the concept). Verify the OAuth `next` value is preserved for a real destination.
- **Test**: browser + `curl` on the metadata tags.
- **Shipped** (2026-09-21): G-05 — `app/not-found.tsx` now exports its own `metadata`
  (title "Page not found — ChmabaPay" plus `robots: noindex, nofollow`), so a 404 no longer
  arrives wearing the home page's title and cannot be indexed as it; `app/api/docs/page.tsx`
  gained `alternates.canonical: /api/docs` and its own `openGraph`/`twitter` title,
  description and `url`, so a shared docs link previews as the API reference rather than the
  marketing home page. Verified live against the running stack: the 404 serves
  `<title>Page not found — ChmabaPay</title>` with `name="robots" content="noindex"`, and
  `/api/docs` serves `rel="canonical" href="https://pay.chmaba.com/api/docs"`, its own
  `og:title`/`og:url` and `twitter:title`. G-07 — the `/onboarding/account-type` stub is
  deleted (the concept went with migration `0008`), and the root layout's pre-hydration
  script dropped its `/onboarding` clause, which would otherwise have stripped the header
  and footer from the 404 that route now returns; `/onboarding/account-type` answers 404 and
  nothing in the URL space claims to be a dashboard. The OAuth `next` round-trip was
  verified rather than assumed: two new tests in `tests/test_account_security.py` assert a
  deep link (`/dashboard/reports?from=…&statuses=…`) survives
  `_encode_google_state`/`_decode_google_state` intact, and that `next` values which are not
  same-origin paths (`//evil.example/x`, `https://evil.example`, a bare host, empty, absent)
  are dropped rather than honoured. Also corrected the layout's `twitter.description`,
  which still advertised Bakong — the same claim T-30 removed from the landing page.

### T-33 — CSV export policy
- **Status**: `complete` · **Priority**: S3 · **Gaps**: G-19 · **Depends on**: D6
- **Description**: per D6, either make CSV genuinely paid (flip `csv_export_enabled` on
  Free and keep the 403) or delete the gate; either way align `routers/reports.py:40-48`,
  the live plan matrix, the docs page and the client-side store-catalog export in
  `reports/page.tsx:104-138`, which bypasses the gate entirely.
- **Test**: pytest + live `GET /v1/billing/plans` agreement.
- **Shipped** (2026-09-21): took D6's second branch — the gate was not a gate, so it is
  gone. `routers/reports.py` lost `_get_active_plan` / `_ensure_csv_allowed` (with a
  docstring recording that the 403 was unreachable: the client rendered its own catalog
  export with no server call), `models.Plan.csv_export_enabled` and its three `db.py`
  seeds were dropped, and the field left every surface that advertised it — `billing.py
  PlanOut`, `admin.py` (`AdminPlanOut`, `AdminPlanPatch`, `AdminPlanCreate`), the admin
  plans editor, the portal billing page, `web/landing/app/api/docs/page.tsx` (two
  places), `docs/api.md`'s error table and `tools/testplan.py` SMOKE-007. The portal
  reports page also lost its `/v1/billing/subscription` probe and `csvLocked` banner,
  which existed only to render the gate. Migration
  `alembic/versions/0010_drop_csv_export_gate.py` (`0010` ← `0009`) does
  `batch.drop_column("csv_export_enabled")`; the downgrade re-adds it with
  `server_default=sa.text("true")`, which is the truth for every existing plan and keeps
  the rollback safe. Verified: full suite **263 passed, 2 deselected**; `ruff check` clean;
  `alembic upgrade head` runs in the compose `migrate` service.

### T-34 — Contact, status page and support posture
- **Status**: `complete` · **Priority**: S3 · **Gaps**: G-08 · **Depends on**: D4
- **Description**: per D4, decide whether to add a real contact form, a lightweight status
  page and a stated response time. Today contact is `mailto:` only and terms disclaim any
  SLA.
- **Test**: manual.
- **Shipped** (2026-09-21): took D4's default — **deferred**, with the posture stated rather
  than left to be inferred. No contact form, no status page and no response-time
  commitment were added; building any of the three properly means an inbox integration, an
  incident feed and a support rota, which is a launch-readiness project of its own, not a
  gap-closing change. What was wrong with the current state was not the absence but the
  silence: `/contact` listed three addresses and said nothing about what to expect, which
  reads as an unstated promise. It now says plainly that support is email only, that there
  is no phone line, no live chat and no public status page yet, that no response time is
  promised, and points at section 7 of the terms where the no-SLA position is already
  written down — and it keeps the one promise that is real: a payment id is enough to trace
  what the rail reported and what was recorded. This is the honest version of "deferred":
  nothing added that we cannot operate, nothing implied that we do not do. Revisit when
  there is a support rota to publish behind it.

---

## Wave 7 — Verification and release

### T-35 — Full verification pass
- **Status**: `pending` · **Priority**: S1 · **Depends on**: all
- **Description**: `docker compose build api`, run the pytest suite against Postgres
  using the documented invocation, build both frontends in Docker
  (`--build-arg APP=landing|admin`), then redeploy production and re-probe every route
  touched in Waves 1–6. Re-run the acceptance criteria in `spec.md`.
- **Test**: the acceptance criteria list in `spec.md`.

### T-36 — Update the readiness doc
- **Status**: `pending` · **Priority**: S3 · **Depends on**: T-35
- **Description**: record the closure in `docs/production-readiness.md` — a new
  `P1-5 Launch gap closure` subsection referencing this spec — and leave `P1-4`'s
  lawyer item open with its current status. Do not mark the legal item closed.
- **Test**: doc review.

---

## Summary

| Wave | Tasks | S1 | S2 | S3–S4 |
| --- | --- | --- | --- | --- |
| 1 Security and access | T-01…T-05 | 2 | 3 | 0 |
| 2 Money | T-06…T-08 | 3 | 0 | 0 |
| 3 Legal gate | T-09…T-10 | 2 | 0 | 0 |
| 4 Customer portal | T-11…T-18 | 0 | 5 | 3 |
| 5 Admin console | T-19…T-24 | 1 | 3 | 2 |
| 6 Docs and copy | T-25…T-34 | 0 | 5 | 5 |
| 7 Verification | T-35…T-36 | 1 | 0 | 1 |

Waves 2 and 3 are gated on decisions D2 and D1 and cannot start until those are
answered. Waves 1, 4, 5 and 6 can start immediately; the tasks that depend on D5, D6 and
D8 assume the low-risk default until told otherwise.
