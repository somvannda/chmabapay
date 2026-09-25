# Launch Gap Closure — Tasks

Ordered by severity, then by dependency. Each task names the gap IDs it closes
(see `spec.md`). Status values: `pending`, `in_progress`, `complete`, `blocked`.

Legend: **S1** blocker · **S2** major · **S3** minor · **S4** polish

---

## Wave 1 — Security and access control (no decisions needed)

> **Status: COMPLETE (T-01…T-05).** Verified at the time: `ruff check src tests` clean, 37
> tests pass against Postgres in Docker. Re-verified on 2026-09-21 against **production**:
> all four KHQR routes answer **401** without a credential from the public hostname, which
> is the live form of T-01's acceptance evidence.

### T-01 — Authenticate the four KHQR routes
- **Status**: `complete` · **Priority**: S1 · **Gaps**: G-10 · **Depends on**: none
- **Description**: `POST /api/v1/khqr/from-link`, `/probe-aba-status`, `/payway/checkout`,
  `/payway/status` in `src/chmabapay/routers/khqr.py` (lines 112, 281, 327, 371) carry
  no auth dependency. Add the account API-key dependency used elsewhere (the same
  `get_current_auth_context` / `require_key` pattern the payments router uses), so a
  `ck_live_` Bearer key is required. Leave `GET /api/v1/khqr/render.svg` public only if it
  is genuinely intended to be public — confirm against `docs/api.md:22`, which marks it
  "(no auth)". Do not add auth to `render.svg` if the docs are right.
- **Test**: pytest — each of the four returns 401 without a key and 200/4xx-with-key
  with a valid one. Live re-probe: `POST /api/v1/khqr/from-link` with an empty body must
  change from **422** to **401**.
- **Evidence**: `curl -s -o /dev/null -w "%{http_code}" -X POST https://pay.chmaba.com/api/v1/khqr/from-link -H "Content-Type: application/json" -d "{}"`
- **Shipped**: each of the four carries `dependencies=AUTH_SECURITY` individually rather
  than the router doing it, because `GET /api/v1/khqr/render.svg` **is** genuinely public — a
  QR image is what a merchant pastes into a page their customers load, and `docs/api.md`
  is right about it. So the router stays open and the four routes that drive an outbound
  ABA fetch (one of which spends a real checkout session) declare the credential
  themselves, with a comment in the router saying why the split exists. Confirmed live on
  2026-09-21: all four answer **401**, not the **422** the audit found. `tests/test_khqr.py`
  covers the refusal, and `tests/test_openapi_schema.py` (added later, in T-29) pins the
  declaration so a route that drifts out from under it fails the suite rather than the
  probe.

### T-02 — Enforce password sessions on `/api/v1/admin/*`
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-42 · **Depends on**: none
- **Description**: `get_hybrid_admin_context` in `src/chmabapay/routers/admin.py:33-51`
  only checks `is_platform_admin`. `session_auth_method(request)` exists
  (`routers/auth.py:141-153`) but is only called by `GET /api/v1/me`. Add a `Request`
  parameter and fail closed unless the session's `amr == "password"`. Also decide the
  Bearer path: an admin's own `ck_` key currently reaches every admin route. Recommended
  default is to drop the key path for admin (session + password only), which matches
  `web/admin/README.md` and closes the latent privilege-escalation surface noted in P1-2.
  Record the choice in the docstring.
- **Test**: pytest — Google-amr session → 403; password session → 200; API key alone → 401/403.
- **Shipped**: `get_hybrid_admin_context` now resolves the session's `amr` and refuses
  anything that is not `password` with **403 `password_session_required`**, failing
  *closed* — a token minted before the `amr` claim existed reports "unknown" and is
  refused rather than assumed to be a password session. The one exception is `amr == "dev"`,
  and it is conditional on `enable_dev_gateway`, the same flag that mounts `/_dev/*` and
  that production hardcodes to `false`, so the exception cannot exist there; without it a
  local console could not be opened before a password was hand-set. **The key path was
  kept**, against the task's recommended default, and the reasoning is in the docstring: a
  `ck_` value is a revocable, hashed, workspace-scoped credential rather than an SSO
  session, so the rule this guard exists to enforce (an SSO session must not substitute for
  the password) does not apply to it. The residual risk is real and worth naming: an admin
  who mints a key for a script has handed that script the power to assign plans, resolve
  invoices and suspend accounts — with nothing in the key's appearance to say so.
  Covered by `tests/test_admin_plans.py` (a password session gets 200, the *same* admin
  authenticated by Google gets 403 `password_session_required`) and by
  `tests/test_admin_actions.py::test_every_operator_action_is_admin_gated` (anonymous →
  401, a merchant's own session → 403, and the attempted writes verifiably did not
  happen).

### T-03 — Admin bootstrap command
- **Status**: `complete` · **Priority**: S1 · **Gaps**: G-40 · **Depends on**: T-02
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
- **Shipped**: `python -m chmabapay.cli grant-admin <email> [--password <pw>] [--name <name>]`
  creates the account if it does not exist, promotes it (`is_platform_admin`,
  `whitelabel_enabled`) and optionally sets a password hash, so day-0 no longer needs a
  Google sign-in followed by an SSH session to flip a flag. The flag parser is a small
  explicit splitter rather than `argparse`, because `--password` may legitimately be
  omitted and the password may be supplied through `CHMABAPAY_PASSWORD` instead of the
  command line — where it would land in the shell history. `web/admin/README.md` documents
  the exact invocation, what it does to an account that already exists (promotes it, does
  not replace the password), and the Google-first prerequisite for the older path;
  `deploy/.env.example` spells out the division of labour between the two mechanisms —
  `CHMABAPAY_ADMIN_EMAILS` only promotes on a Google sign-in, `CHMABAPAY_ADMIN_PASSWORD`
  only supplies a password to an account that already exists, and neither alone gets you
  a working console login on a fresh deployment. The module docstring and `cli.py`'s
  `main()` usage text carry it too, so the command is discoverable from the
  `unknown command` error rather than only from a doc.

### T-04 — Audit admin and merchant sign-ins
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-44 · **Depends on**: none
- **Description**: `password_login` (`routers/auth.py:493-539`) writes no audit row.
  Record `auth.login_succeeded` and `auth.login_failed` with the IP and the attempted
  email (never the password). Follow `audit.record`'s existing signature
  (`src/chmabapay/audit.py:20-38`). Do not log on the Google callback a second time —
  the success row belongs to the credential path.
- **Test**: pytest — success and failure each write one row with the expected action.
- **Shipped**: a private `_audit_login` wrapper in `routers/auth.py` stages one row per
  attempt — `auth.login_succeeded`, `auth.login_failed` (with `reason`) and
  `auth.login_blocked` — carrying the email, the client IP and the reason, never the
  password. It goes through `audit.record`, so the row joins the request's transaction:
  the failure path therefore **commits before it raises**, because raising discards the
  transaction and with it the audit row, which would have made the interesting half of
  the trail silently empty. Sign-ins against an address with no account are recorded too —
  `actor_account_id` is nullable for exactly that, and the target becomes `("Email", 0)`
  rather than a fabricated account. The Google callback deliberately does not write a
  second success row: the credential path owns it.
  `tests/test_admin_plans.py::test_every_sign_in_attempt_is_audited` covers both halves.
  The attempted address is stored **in full**, and that is deliberate rather than an
  oversight: the whole point of the row is to say which address is being probed, and a
  domain-only value would hide a targeted attack on one known mailbox. It is the account's
  *own* email that T-16 reduces to a domain on erasure, because that row must not outlive
  the data it describes; a sign-in attempt is evidence about the attacker, not about the
  account.

### T-05 — Per-account login lockout
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-43 (partial) · **Depends on**: T-04
- **Description**: the only protection on `POST /auth/login` is a 20/min/IP in-process
  limiter keyed on `X-Forwarded-For` (`ratelimit.py:77-79,104-145`). Add per-email
  failure counting with exponential backoff and a lockout window, released on success.
  Keep it dependency-free (in-process) unless the Redis path is already active.
- **Test**: pytest — N failures locks the account; success resets the counter; a
  different email is unaffected.
- **Shipped**: `LoginLockout` in `ratelimit.py` — a per-**email** counter beside the
  existing per-**address** limiter, because the address limiter cannot bound guessing
  spread across many addresses and does not slow a targeted attack on one known mailbox at
  all beyond the shared 20/min. Five failures locks that email for 60s, doubling per
  further failure up to a 1h ceiling (`_LOGIN_FAILURE_THRESHOLD = 5`,
  `_LOGIN_LOCK_BASE_SECONDS = 60`, `_LOGIN_LOCK_MAX_SECONDS = 3600`), and a success clears
  the record. Three details are the ones that matter: the lock is checked **before the
  password**, so the window cannot be probed by timing; the refusal is the same generic
  answer as a wrong password, so the endpoint does not become an oracle for which emails
  exist; and re-inserting the entry on each failure **resets its TTL**, so a long attack
  cannot let its own record age out while it is still failing. In-process and honest about
  it, like `RateLimiter`: it bounds one replica, and it fails **open** on cache eviction —
  the right way round for something that must not lock a real merchant out of their own
  account because the cache was under pressure. `G-43` is marked partial because that
  per-replica limit is the remaining half.

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
- **Status**: `complete` · **Priority**: S1 · **Gaps**: G-24 · **Depends on**: D2
- **Description**: `POST /api/v1/billing/change-plan` (`routers/billing.py:140-196`) collects
  no payment and applies no proration; the dashboard exposes it one click from
  `billing/page.tsx:396-457`, granting Pro ($59.99/mo, 50 stores, 1M payments) for $0.
  Per D2 (full fix): a move to a **paid** tier must create/reuse an open invoice and only
  apply the plan once that invoice is settled; a downgrade to Free applies immediately.
  The dashboard must say "an invoice is due" rather than "upgraded".
- **Test**: pytest — merchant upgrade without payment is refused; admin change succeeds.
- **Shipped**: a paid tier is now **bought**. The route parks a `pending` subscription,
  raises the invoice for the period via `services.billing.issue_invoice`, and returns
  `payment_required=true` with that invoice; the plan activates when the invoice is paid
  (`services.payments.mark_paid` calls `activate_subscription`). A pending subscription
  grants nothing in the meantime, because `_get_active_sub` reads only `trial`/`active`.
  A move to a free tier still applies immediately — there is nothing to collect, and
  making a downgrade wait on a payment would trap a merchant on a plan they are trying to
  leave. Three refusals were added that the old code had no reason to think about:
  `400 plan_unchanged` (re-selecting your own plan is not a purchase, and without it a
  merchant on Pro could invoice themselves a second time), `409 period_already_invoiced`
  (one invoice per account per period is a database constraint, so this turns what would
  have been a 500 from a constraint violation into an answer the client can explain) and
  `400 plan_not_available` for a retired plan. The dashboard renders the invoice and
  `payment_required` state instead of claiming an upgrade happened.
  `tests/test_billing_invoices.py` covers it.

### T-07 — Invoice generation
- **Status**: `complete` · **Priority**: S1 · **Gaps**: G-22 · **Depends on**: D2
- **Description**: `w3_billing.py` is an M1 no-op stub and `PlanInvoice(` is never
  instantiated, so the Invoices table is permanently empty and "Pay with KHQR" is
  unreachable. Implement monthly invoice generation from the active subscription
  (`PlanSubscription.next_billing_at`), one row per account per period, idempotent on
  `(account_id, period_month)` — check the existing unique key before adding one.
- **Test**: pytest — a due subscription produces exactly one invoice; a re-run produces none.
- **Shipped**: the M1 stub is gone. `w3_billing.py` now calls
  `services.billing.issue_due_invoices`, which sweeps subscriptions whose
  `next_billing_at` has passed and raises one invoice per period, idempotent on
  `(account_id, period_month)` — the unique key added by migration `0009`, which had to
  exist first because "only one invoice per period" was otherwise a convention rather than
  a guarantee. Four decisions inside it are the ones that matter:
  **`next_billing_at` advances whether or not an invoice was written** — a period that
  produced nothing (a free plan, or a row that already existed) must still move the due
  date, or the same subscription would be re-examined on every sweep forever and never be
  billed for any later month; **the schedule advances from the due date, not from `now`**,
  so a worker that was down for two months catches those months up one at a time instead of
  forgiving them; **a re-run returns the existing row untouched** rather than restamping an
  invoice that may already have been sent; and the catch-up loop is **bounded**
  (`MAX_CATCH_UP_PERIODS = 36`) so a pathological due date from a bad import or a clock jump
  cannot spin inside a request-scoped worker. Usage is read from `plan_ledger_entries`
  rather than counted from `payments`, because a reversal carries a negative entry and a
  refunded sale must stop counting toward what the merchant is billed. Overage is **counted
  and recorded, never charged**: there is no overage price on `Plan`, and the product
  enforces a quota (`402`) rather than billing past it, so inventing a fee would be charging
  a number nobody agreed to. A free plan produces no invoice at all — a monthly $0 invoice
  is noise on a billing page, not information. `tests/test_billing_invoices.py` covers the
  due/not-due/re-run cases.

### T-08 — Make invoice payment actually work
- **Status**: `complete` · **Priority**: S1 · **Gaps**: G-23 · **Depends on**: D2
- **Description**: `_get_hq_store` (`routers/billing.py:199-234`) raises
  `500 platform_hq_store_not_configured` when no HQ store exists. In production
  `CHMABAPAY_HQ_STORE_ID` and `CHMABAPAY_HQ_PAYWAY_LINK` are **empty**, so the seed in
  `auth.py:245-283` never runs. Per D2: seed the HQ store + a real PayWay link, or let
  the invoice target a configurable link, and return a friendly error instead of a raw
  500 code. Whichever is chosen must be reflected in `deploy/.env.example`.
- **Test**: pytest — invoice KHQR mint returns 201 with a real link configured; a
  misconfigured deployment returns a human-readable 4xx/5xx, not a code string.
- **Shipped**: the raw `500 platform_hq_store_not_configured` — whose body the dashboard
  passed straight into a toast, so the person who could not fix it was shown an instruction
  addressed to somebody else, in a code they would never read as "billing is not switched
  on" — is now a **503 `billing_not_open`** with a sentence a merchant can act on ("contact
  support and we will settle it with you"). Resolution of the destination moved into
  `services.billing.resolve_hq_store`, because the console and the billing route need the
  same answer and two copies of that order would eventually disagree about which store is
  in use; it returns the **source** as well as the store (`env` / `console` / `fallback` /
  `none`) so the console can tell an operator that an environment variable is overriding
  what they just saved. `deploy/.env.example` documents the variable and points at the
  console route as the supported alternative. **The production action this task identified
  cannot be done in code and is still outstanding**: `CHMABAPAY_HQ_PAYWAY_LINK` must be set
  (in `deploy/.env`, or now from the console — see T-08b), or a paid plan change correctly
  answers `503 billing_not_open`.

### T-08b — Set the collection link from the admin console (added 2026-09-18)
- **Status**: `complete` · **Priority**: S1 ·
  **Gaps**: G-23 · **Depends on**: T-08
- **Description**: T-08 left the HQ store reachable only through
  `CHMABAPAY_HQ_PAYWAY_LINK` in `deploy/.env` plus a sign-in, which means switching
  self-serve billing on still required a shell session — the operator had to edit a file
  on the server to fill in a link they already had in their hand. Added
  `GET /api/v1/admin/hq-store` and `PUT /api/v1/admin/hq-store/link`, plus a **Plan fee
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
- **Shipped**: shipped as described, and this is the path that removes the last shell step
  from switching billing on. Worth stating plainly, because it is the one place in this
  register where operator convenience and a security boundary point in opposite
  directions: this endpoint decides **where the platform's own revenue lands**, so it
  refuses a pasted account number or a foreign host rather than trusting the operator to
  paste the right thing, and both writes are audited. The console panel is
  `web/admin/components/HqStorePanel.tsx`, and it reports the resolution source so an
  operator whose save appears to do nothing is told that `CHMABAPAY_HQ_STORE_ID` is the
  reason instead of being left to guess. `tests/test_admin_actions.py` and
  `tests/test_admin_overview.py` cover the writes, the refusal and the admin gate.

---

## Wave 3 — Legal gate (blocked on decision D1)

> **Status: COMPLETE (T-09, T-10).** D1 answered as "remove the draft banner now".
> Verified: `tests/test_compliance.py` + `tests/test_audit.py` = 45 passed against
> Postgres, and the rendered notice is gone — `legal-draft|must be reviewed|
> draft-banner` returns **no match** under `web/landing`, and `globals.css` keeps only
> the `.legal-*` rules. The one surviving mention is the file-header comment on each
> legal page recording why the banner went. The landing Docker build check this wave
> was waiting on now passes: Wave 7 built `web/landing` (type + lint included) and it
> is what production serves.

### T-09 — Terms acceptance gate
- **Status**: `complete` · **Priority**: S1 · **Gaps**: G-21 · **Depends on**: D1
- **Description**: `POST /api/v1/me/terms` works and validates the version, but no portal
  code calls it and nothing gates on `terms_accepted_at`. Add a blocking acceptance
  screen in the post-OAuth flow (before the dashboard renders) that calls the endpoint
  with the displayed version, and enforce server-side in `GET /api/v1/me`'s consumers so an
  API key cannot bypass it. Reuse `settings.terms_version`
  (`config.py:126-131`) — do not hardcode the version in the UI.
- **Test**: pytest — a new account is blocked until acceptance; a stale version returns
  409; an accepted account passes. Browser check on the deployed flow.
- **Shipped**: the version is never hardcoded. `GET /api/v1/me` returns
  `terms_required_version` straight from `settings.terms_version`
  (`routers/account.py:49`), and the portal compares it to the account's accepted
  version in `termsAccepted(profile)` (`components/portal/useSession.ts:29`) — which
  returns `true` when the server says nothing, so a profile that predates the field
  cannot lock a merchant out of their own dashboard.
  The gate is `app/dashboard/layout.tsx:230`: it renders **instead of** the workspace
  shell, not beside it, and its button echoes the version the page actually displayed
  back to `POST /api/v1/me/terms` (line 165). A `409` is therefore surfaced as "the terms
  were updated while this page was open — reload", which is what `409
  terms_version_superseded` means, rather than as a generic save failure.
  A UI gate is a convention, so the same rule is enforced server-side at the point a
  merchant starts using the service programmatically: `_require_terms_accepted`
  (`routers/keys.py:83`) refuses key creation with `403 terms_not_accepted` when the
  accepted version is not the published one. It is checked at **creation**, so an
  account already integrated is never cut off mid-flight, and it compares the version
  rather than testing `terms_accepted_at` for null, so re-publishing asks for consent
  again. Operator tooling is unaffected — it holds an admin session, not a key.
  Pinned by `tests/test_compliance.py::test_a_key_cannot_be_minted_before_the_terms_are_accepted`,
  `::test_a_superseded_version_is_refused_rather_than_recorded` and
  `::test_republishing_the_agreement_asks_for_consent_again`, with the fixture default
  (`terms_accepted=True`) documented in `tests/conftest.py:174-191` so a test about
  the gate has to opt in explicitly.

### T-10 — Remove the draft banner (D1)
- **Status**: `complete` · **Priority**: S1 · **Gaps**: G-01 · **Depends on**: none
- **Description**: the Terms and Privacy pages state in production that they "must be
  reviewed and replaced before ChmabaPay accepts real merchant traffic"
  (`terms/page.tsx:34-42`, `privacy/page.tsx:27-32`). Per **D1**, remove the draft banner
  from both pages and the now-unused `legal-draft-*` CSS, leaving the body text as-is.
  Pair this with T-09 so acceptance of the text means something the moment the banner
  goes. The lawyer review stays open as `P1-4` and must not be marked closed.
- **Test**: browser — neither page renders a draft notice; the acceptance gate still
  blocks an un-accepted account.
- **Shipped**: the banner is gone from `/terms` and `/privacy`, and the CSS went with
  it — `globals.css` retains only the `.legal-page`/`.legal-shell`/`.legal-body` family
  (from `:4995`), with no `legal-draft` selector left to re-apply. The body text is
  untouched, including the line that keeps the review honest in the document itself
  (`terms/page.tsx:151`, "is subject to change following legal review").
  What T-09 adds is the other half: with the gate in place, accepting version 1 records
  agreement to text that describes what the system actually does, so the banner's
  removal is a statement about the product rather than an unexplained disappearance.
  **The lawyer review is deliberately not closed by this task.** It remains `P1-4` in
  `docs/production-readiness.md`, and the fact is now recorded where a developer will
  meet it — the file-header comment in `terms/page.tsx:8-19` and its counterpart in
  `privacy/page.tsx:11-16` state that the banner was removed on 2026-09-18 by an
  explicit product decision to open the service to real merchant traffic, *not* because
  the text was reviewed, and that its absence must not be read as approval. Those two
  comments are also the reminder that `terms_version` in `config.py` must move in the
  same change as any substantive edit to the text.

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
  and merchant on create and on `PUT /api/v1/stores/{id}/link`; set verification from the
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
  `extra="forbid"` added, so `PATCH /api/v1/me {"email": …}` is now 422 rather than silently
  applied. `POST /me/password` rotates the password (checks the old one, refuses
  `password_unchanged`, 409 `no_password_set` for a password-less account — a session must
  not be able to mint a credential). `DELETE /me` anonymises the account
  (`erased-{id}@chmabapay.invalid`, name cleared, `google_sub`/`password_hash` nulled,
  suspended so the session dies), disables its stores, revokes its keys and stops its
  webhooks, and **keeps the payments** as the accounting record the privacy policy already
  promises to retain; it needs a typed confirmation and the password, and a platform admin
  cannot erase itself. `GET /api/v1/me` gained `has_password` (a bool, never the hash) so the UI
  can explain instead of offering a form that would be refused. 8 tests in
  `tests/test_account_security.py`. Audit rows store email *domains*, not addresses, so a
  log cannot outlive an erasure.
- **Description**: no erasure path and no password rotation exist, and `PATCH /api/v1/me`
  (`routers/account.py:75-96`) changes email with no verification. Per D4, add
  `DELETE /api/v1/me` (or a documented erasure request flow), a password-change endpoint +
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
- **Description**: add `GET /api/v1/admin/payments/{public_id}` exposing `bakong_ref`,
  `gateway_status_raw`, `attempt_history`, `reissued_from`, `detection_closed_at`;
  `POST /api/v1/admin/payments/{id}/reconcile` calling the existing `reconcile_payment`;
  `POST /api/v1/admin/payments/{id}/mark-paid` with a mandatory reason and an audit row; and
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
- **Description**: add `PATCH /api/v1/admin/accounts/{id}/plan` (audited),
  `POST /api/v1/admin/invoices/{id}/mark-paid|waive|credit`, `POST /api/v1/admin/keys/{id}/revoke`
  and `POST /api/v1/admin/stores/{id}/disable`, with UI. Today a leaked key can only be
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
  `/api/v1/admin/plans` so it cannot offer a code the API would reject), a per-invoice Resolve
  modal (action, reason, optional credited amount), a Keys panel with Revoke, and Disable
  on each store row; `lib/apiError.ts` translates the refusals an operator can realistically
  hit (`last_platform_admin`, `plan_unchanged`, `invoice_already_*`) because printing a
  machine code at an operator makes a working guard look like a bug. The invoices list
  page's status filter was also repaired — it offered `draft`/`issued`/`overdue`, none of
  which `services/billing.py` ever writes, so it silently filtered to nothing.
  Ten tests in `tests/test_admin_actions.py`.

### T-21 — Overview operational signals
- **Status**: `complete` · **Priority**: S2 · **Gaps**: G-45 · **Depends on**: none
- **Description**: extend `GET /api/v1/admin/overview` (`routers/admin.py:942-997`) with a
  "needs attention" block — payments pending past expiry, detection-closed-unpaid,
  webhook deliveries failed in the last 24 h, worker heartbeat age, queue depth, today's
  paid volume — sourced from existing models/counters, and render it above the stat
  cards with deep links to the filtered lists.
- **Test**: pytest — each counter returns a number and matches a seeded fixture.
- **Shipped**: `GET /api/v1/admin/overview` now returns `needs_attention` (a list of
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
- **Description**: add `POST /api/v1/admin/deliveries/{id}/retry` (reset `next_attempt_at`,
  audit row) plus a Retry button on the deliveries page, and a `webhook_failure_rate`
  condition in `alerts.py` (>10 % failing over 15 min) — today only queue backlog is
  alerted, so a rail failing slowly never pages.
- **Test**: pytest — retry reschedules and audits; the alert fires on a seeded failure ratio.
- **Shipped**: `POST /api/v1/admin/deliveries/{id}/retry` resets the delivery to due-now with a
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
- **Shipped**: `GET /api/v1/admin/accounts/{id}` now returns `keys` (name, prefix, mode,
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
  `GET /api/v1/payments/{id}` (not returned), reissue-on-`superseded` (rejected), the
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
  a pending subscription + invoice + `payment_required`; `PATCH /api/v1/me` was documented as
  accepting `email`, which `extra="forbid"` turns into a 422 — `POST /api/v1/me/email` is now
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
  group (or hide them) and document `GET /api/v1/transactions/check-status/{payment_public_id}`
  and `POST /api/v1/transactions/verify-payment/{payment_public_id}`, which work today
  without Bakong credentials for hosted-session payments.
- **Test**: content review; live probe of the two documented endpoints.
- **Shipped** (2026-09-21): the docs page now separates the two things it used to conflate.
  A new **Payment Reconciliation** group documents the pair that actually answers
  `GET /api/v1/transactions/check-status/{payment_public_id}` and
  `POST /api/v1/transactions/verify-payment/{payment_public_id}`, with their query parameters
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
  KHQR routes declare security, and that no `/api/v1/admin/` path appears.
- **Shipped** (2026-09-21): new `src/chmabapay/openapi.py` is the one place the shared
  metadata lives. Two schemes — `ApiKey` (HTTP bearer, `ck_live_…`) and `SessionCookie`
  (`apiKey` in cookie, `chmabapay_session`) — both declared with `auto_error=False` so
  they *document* rather than enforce; enforcement stays with the real dependency, which
  also answers the right status (401, not `HTTPBearer`'s 403). `AUTH_SECURITY` /
  `SESSION_SECURITY` and the `ErrorOut` body plus `AUTH_ERRORS` (401/403), `QUOTA_ERROR`
  (402), `CONFLICT_ERROR` (409) and `UPSTREAM_ERROR` (502) ride at router level on
  payments, keys, stores, webhooks, reports and transactions, and on the individual
  billing and account routes that are session-only; khqr's router deliberately carries
  none because `GET /api/v1/khqr/render.svg` is public, so each of its four outbound routes
  declares `AUTH_SECURITY` by hand. `/pay/{id}/qr.svg` and `/pay/{id}/status` got the
  404/410 `ErrorOut` bodies they actually raise. `include_in_schema=False` on the admin
  and dev routers keeps the operator surface out of the published document while the
  routes keep dispatching. New `tests/test_openapi_schema.py` (7 tests) pins all of it:
  the schemes exist, every route under the credentialed prefixes declares security, the
  listed public paths declare none, the four KHQR routes allow exactly
  `{ApiKey, SessionCookie}` and document 401, `POST /api/v1/payments` documents 402, no
  `/api/v1/admin` or `/_dev` path is published, and both hidden routers still serve (401/403,
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
- **Test**: pytest + live `GET /api/v1/billing/plans` agreement.
- **Shipped** (2026-09-21): took D6's second branch — the gate was not a gate, so it is
  gone. `routers/reports.py` lost `_get_active_plan` / `_ensure_csv_allowed` (with a
  docstring recording that the 403 was unreachable: the client rendered its own catalog
  export with no server call), `models.Plan.csv_export_enabled` and its three `db.py`
  seeds were dropped, and the field left every surface that advertised it — `billing.py
  PlanOut`, `admin.py` (`AdminPlanOut`, `AdminPlanPatch`, `AdminPlanCreate`), the admin
  plans editor, the portal billing page, `web/landing/app/api/docs/page.tsx` (two
  places), `docs/api.md`'s error table and `tools/testplan.py` SMOKE-007. The portal
  reports page also lost its `/api/v1/billing/subscription` probe and `csvLocked` banner,
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

> **Status: COMPLETE (2026-09-21).** T-35 and T-36 closed. Local verification, the
> production redeploy to Alembic `0010`, the post-deploy probes and the readiness doc are
> all done. The single item this project did **not** close is P1-4's lawyer review of the
> merchant agreement — that is a legal task, not a code one, and it is deliberately left
> open.

### T-35 — Full verification pass
- **Status**: `complete` · **Priority**: S1 · **Depends on**: all
- **Description**: `docker compose build api`, run the pytest suite against Postgres
  using the documented invocation, build both frontends in Docker
  (`--build-arg APP=landing|admin`), then redeploy production and re-probe every route
  touched in Waves 1–6. Re-run the acceptance criteria in `spec.md`.
- **Test**: the acceptance criteria list in `spec.md`.
- **Shipped** (2026-09-21). Local, in Docker: `docker compose build api`; pytest against
  Postgres **265 passed, 2 deselected**; `ruff check src/chmabapay tests` clean;
  `next build` for **both** `web/landing` and `web/admin` (each runs its own type and lint
  checks inside the image). Deploy: committed as `4c4bffa` (96 files) and pushed, then
  fast-forwarded `main` — the VPS tracks `main`, and `1d2826c..4c4bffa` is a
  fast-forward, not a force. Production was at **`0008`**, so this deploy applied two
  migration groups, `0009` and `0010`, the second of which is destructive: a
  `pg_dump -Fc` was taken and **verified** (PGDMP magic, 14 `TABLE DATA` entries) before
  anything was pulled, and `0009`'s `DELETE FROM plan_invoices` was checked to be the
  no-op it claims to be *before* running it (`plan_invoices` held 0 rows) rather than
  after. Results: schema `0010`, `migrate` exit 0, all app containers healthy, `proxy`
  not recreated, 14 public tables with `plans` at 3 rows and `accounts` at 1, and the
  neighbouring POS stack (`deploy-front-1`, `deploy-api-1`, `deploy-db-1`) up 11 days
  throughout. Acceptance criteria, re-run against production: **AC-1** no S1 open except
  by decision; **AC-3** all four KHQR routes answer 401 without a credential from the
  public hostname; **AC-4** the operator actions exist and are admin-gated (covered by
  `test_admin_actions.py`/`test_admin_overview.py` and the admin image build);
  **AC-5** the suite and both frontend builds pass and the redeploy is verified. AC-2's
  merchant journey is proven by the suite rather than by hand in production — see the
  note below. Full probe output and the deploy record are in
  `docs/production-readiness.md` §P1-5.
  <br>**One approved item could not be carried out as written.** The user chose "probes
  plus one real test payment", described as driving a payment to paid through the dev
  rail. That premise does not hold in production: `ENABLE_DEV_GATEWAY` is hardcoded
  `"false"` in `deploy/docker-compose.prod.yml` (deliberately — it is the same flag that
  arms `/_dev/payments/{id}/pay`), so there is no rail to drive and `/_dev/integration-test`
  answers 404, confirmed by probe. Reaching *paid* therefore needs a human scanning a real
  QR with a real wallet, and creating the payment itself needs a `ck_live_` key, which
  needs a session — i.e. the platform admin's credentials. What was verified instead:
  every money-path endpoint exists, is routed, and refuses an unauthenticated caller with
  401 (`/api/v1/payments`, `/api/v1/stores`, `/api/v1/reports/payments.csv`,
  `/api/v1/transactions/check-status/…`), and `/pay/{id}` answers 404 for an unknown id while
  `/api/v1/billing/plans` answers 200. The end-to-end checkout is covered by the suite. Left
  for the operator, with the exact commands, rather than performed with credentials that
  are not this task's to spend.

### T-36 — Update the readiness doc
- **Status**: `complete` · **Priority**: S3 · **Depends on**: T-35
- **Description**: record the closure in `docs/production-readiness.md` — a new
  `P1-5 Launch gap closure` subsection referencing this spec — and leave `P1-4`'s
  lawyer item open with its current status. Do not mark the legal item closed.
- **Test**: doc review.
- **Shipped** (2026-09-21): added `### P1-5 Launch gap closure` to
  `docs/production-readiness.md`, immediately after P1-4 so the two read together. It
  records the audit being run against the deployed stack rather than the working tree, the
  50-gap register and where it lives, a table of the six gaps that actually mattered
  (access, money, legal, portal, console, docs), and the three decisions that closed a gap
  by *removing* a claim instead of building behind it (the CSV gate deleted, the Bakong
  ledger group marked unavailable, contact left email-only with the posture stated) — each
  recorded as a deliberate reduction in scope rather than a silent omission. Then the
  deploy: commit, the verified `pg_dump`, `0009`'s `DELETE` checked to be a no-op before it
  ran, `0010`, and the full post-deploy probe list including the POS stack staying up 11
  days. **P1-4's lawyer item is explicitly left open and said so in the closing paragraph**
  — the merchant agreement is still a draft and still needs a lawyer; this section records
  what the code closed and does not touch that item. Also verified while there: the
  deploy.md §12 caveats still hold (the `curl -f` footnote about an expected 404, and the
  `/metrics` token gate), so no correction was needed to them.

---

## Wave 8 — Launch closure (from the 2026-09-21 second production audit)

> **Status: IN PROGRESS (2026-09-21).** The second A-to-Z audit was run against
> production at `02fd387`, on the same four tracks as the first (public site, API docs,
> portal, console) plus live probes of both hostnames: page metadata, the legal text as
> rendered, security and cache headers, `robots.txt`/`sitemap.xml`, a full internal-link
> crawl, and unauthenticated status codes on every money route. Findings were then
> re-verified against source. **Method note:** the portal and console were audited from
> source rather than rendered, because no merchant or admin credentials were used — every
> claim about them is code-verified, not browser-verified, and the two items that need a
> human are named in the Launch gate below.
>
> **Progress: 11 of 11 closed and deployed.** The last task, T-43, closed on 2026-09-21
> once the operator supplied the registered entity's details; its per-task status and the
> one clause deliberately left to the lawyer are recorded under it. The evidence for the
> wave is in `docs/production-readiness.md` §P1-6.
> Shipped in two deploys on 2026-09-21, both verified against production. `f51add1`
> carried the first ten tasks and left the schema at Alembic `0010`; `b390ca7` carried
> T-47 and migrated to `0011` (taking a verified `pg_dump -Fc` first — 51,844 bytes,
> `PGDMP`, 14 `TABLE DATA` entries — and proving the row counts unchanged at 2 accounts,
> 0 stores, 0 payments across the migration). After both: every public page 200, the
> security headers and `no-store` still live through Cloudflare on both hostnames, the
> money routes still 401 to an anonymous caller, `/api/v1/admin/overview` 401 and `/_dev` 404,
> `is_internal` published in the live OpenAPI, and the neighbouring POS stack untouched
> at 11–12 days up.

### T-37 — Correct the API docs where they contradict the code
- **Status**: `complete` · **Priority**: S2 · **Gaps**: D1–D9 · **Depends on**: none
- **Description**: the second audit re-ran the docs-vs-code comparison and the first
  wave's fixes did not reach these. `check-status`'s documented `source` enum advertises
  `aba_payway_link_page`, which is **never assigned anywhere**, and omits
  `payway_hosted_checkout`, which is emitted for every ABA-hosted payment — the same dead
  enum is in `schemas.py`. `verify-payment` is documented to return `404
  tx_not_found_yet` but returns `200 {"found": false}`, so the documented branch is dead.
  `docs/api.md` still lists `superseded` as reissuable when the service refuses it with
  409, and still calls all of `/api/v1/billing/*` session-only when `GET /api/v1/billing/plans`
  is public (confirmed live: 200 anonymous). The docs page claims `GET /api/v1/me` returns
  `plan`; it does not. The `superseded` definition is inverted — it marks the
  *replacement* code when the original settles. `503 billing_not_open` is attributed to
  `change-plan`, which never raises it. The API-key security scheme still describes
  `ck_test_` keys, which the system never issues. Five real endpoints published in the
  OpenAPI appear in neither doc: `PUT /api/v1/stores/{public_id}`, `PATCH /api/v1/account`,
  `POST /api/v1/me/password`, `DELETE /api/v1/me`, `POST /api/v1/transactions/token/renew`.
- **Test**: pytest — the existing `tests/test_openapi_schema.py` stays green; a new
  assertion that every path in the live schema is either documented or explicitly listed
  as internal. Manual: re-read each corrected claim against the router.

### T-38 — Make the published OpenAPI match the runtime
- **Status**: `complete` · **Priority**: S3 · **Gaps**: D10 · **Depends on**: none
- **Description**: `POST /api/v1/payments` returns 201 and `POST .../reissue` returns 201 or
  200 depending on whether a code was minted, but neither decorator declares
  `status_code`, so the published schema advertises only 200 and omits reachable 400/404/502.
  A generated client is therefore wrong at runtime. Runtime behaviour is correct and must
  not change — only the declarations.
- **Test**: pytest — `tests/test_openapi_schema.py` asserts the declared status set for
  both operations includes what the handler can actually return.

### T-39 — Reach a merchant's older payments (pagination)
- **Status**: `complete` · **Priority**: S2 · **Gaps**: P3 · **Depends on**: none
- **Description**: `GET /api/v1/payments` accepts `limit` (default 20, clamped to 100) and no
  offset, and the portal asks for 50. In the first weeks a merchant crosses 50 payments
  and can no longer reach an older one anywhere in the portal — the only workaround is
  the Reports CSV. Add `offset` to the list endpoint (with the total already available
  from reports) and a "Load more" affordance on the payments pages.
- **Test**: pytest — `offset` returns the next slice, is clamped, and rejects a negative
  value; the first page is unchanged for an existing caller.

### T-40 — Never render "empty" for a failure
- **Status**: `complete` · **Priority**: S2 · **Gaps**: P1, P4, P5, P6, P7 · **Depends on**: none
- **Description**: five pages act only on `res.ok`, swallow the error and then assert the
  absence of data: the stores list, the payments list, the store-scoped payments list,
  the webhooks list, and the store overview — which renders a fully zeroed dashboard. A
  merchant with live stores sees "No stores yet." and may create duplicates. The keys
  page and the root overview were fixed for exactly this in Wave 4; port that pattern
  (`error` state + Retry + `—` placeholders). Same task, same files: the store-scoped
  payments list renders `reversed`/`superseded` in the grey "pending" pill; the settings
  billing tab always shows a green "active" pill regardless of the real status; the
  reports copy promises CSV columns that do not exist (owner email, Bakong references);
  and `describeApiError` falls through to raw machine codes for `period_already_invoiced`,
  `plan_not_available`, `payment_link_disabled` and friends, so a merchant literally reads
  `period_already_invoiced` on screen.
- **Test**: pytest is not enough here — each page's failure path is a UI state. Verified
  by reading each page against its fetch, plus the landing Docker build (type + lint).

### T-41 — Confirm, and guard, the actions that destroy a live credential
- **Status**: `complete` · **Priority**: S2 · **Gaps**: P2, P9 · **Depends on**: none
- **Description**: Revoke/Rotate on an API key and Rotate-secret/Delete on a webhook
  endpoint have no confirmation and no in-flight disabled state. One mis-click revokes
  the secret a merchant has already deployed; a double-click on Rotate mints two keys and
  the second reveal banner overwrites the first, so one raw key is lost permanently.
  The stores list already implements the pattern to copy (`disablingId`). Also here: the
  inline `style={{width}}` progress bars in `DashboardShell`/billing violate the
  zero-inline-styles rule and move to `globals.css`.
- **Test**: browser — each guard blocks the action until confirmed and cannot be
  double-submitted.

### T-42 — Security headers, cache policy and per-page metadata
- **Status**: `complete` · **Priority**: S2 · **Gaps**: L1, L2, L3 · **Depends on**: none
- **Description**: neither hostname sends `Strict-Transport-Security`,
  `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy` or `Permissions-Policy` — verified live. The dashboard and the console
  are session-authenticated and framable, and the first request of a session is
  downgrade-able. Every app HTML response also carries `Cache-Control:
  s-maxage=31536000` (Next's static default) — one year of shared caching on
  authenticated pages; Cloudflare answers `cf-cache-status: DYNAMIC` today, so this is a
  latent risk rather than an active leak, and it is closed by a `no-store` app-route
  policy. Separately, `/terms`, `/privacy` and `/contact` have no canonical and inherit
  the landing `og:title`/`twitter:title`, so sharing a legal page shows the marketing
  title; `/api/docs` and the 404 were fixed in Wave 6 and are the template.
- **Test**: `curl -D -` on both hostnames shows the headers; `curl` shows each page's
  canonical and its own `og:title`; app routes answer `no-store`.

### T-43 — Remove the last trace of the draft state from the legal text
- **Status**: `complete` · **Priority**: S2 · **Gaps**: L4, L5, L6, L7, L8 · **Depends on**: operator input for L5 — supplied 2026-09-21
- **Description**: five things in the legal text itself, all found by reading it as
  rendered. (1) Terms §3 still ends "This list is a starting point and is subject to
  change following legal review" — the residue of the draft state whose banner D1 removed;
  it tells the customer the document has not been reviewed. (2) Terms §6 says "Changes to
  a paid plan take effect at your next billing period", which **contradicts the shipped
  behaviour**: a paid-plan change is a purchase — a `pending` subscription and an invoice,
  activating when the invoice is paid. The clause also never states the currency. (3) The
  Terms never identify the contracting entity — no registered name, number or address —
  and §8 excludes indirect loss but caps nothing. (4) Privacy §5 enumerates processors and
  omits **Cloudflare**, which terminates TLS for every request. (5) Privacy §4 says raw
  rail responses are kept "at most 90 days" while the sweep is daily, so the true bound is
  90 days plus one sweep.
- **Test**: read both pages as rendered; each corrected sentence checked against the code
  it describes. **L5's entity clause ships only with the operator's real details** —
  inventing a registration number is not an option this task has.
- **Closed 2026-09-21.** L4, L6, L7 and L8 were fixed with the Wave 8 deploy. L5 landed
  once the operator supplied the entity: the counterparty is **Chmaba**, registered at
  #62, Street P-10D, Sangkat Veal Sbov, Khan Chmbar Ampov, Phnom Penh, Cambodia, with
  **no company number** stated — because none was given, and this task does not invent
  one. The placeholder "ChmabaPay Technologies" (a company that does not exist) is gone
  from `/terms`, `/privacy`, the merchant-agreement draft, and both footers.
  `TERMS_VERSION` moved `1` → `2` in the same change, which is the coupling the page
  comment and `config.py` both warn about: 2 accounts hold a version-1 acceptance and
  are now asked again.
- **Deliberately NOT done here:** §8 excludes indirect loss but sets no ceiling. A
  liability cap is a legal decision, so it stays open in the Launch gate under P1-4
  rather than being drafted by us. L5 is thereby closed on the *identification* half
  only, and the register should be read that way.

### T-44 — Make a disabled store reversible, and stop revenue moving silently
- **Status**: `complete` · **Priority**: S2 · **Gaps**: A1, A2 · **Depends on**: none
- **Description**: two admin gaps with teeth. (1) There is no admin enable route: an
  operator can disable a store from the console but only the *merchant* can re-enable it
  (`POST /api/v1/stores/{id}/enable` is merchant-authed), so disabling during an incident is a
  one-way door. Add `POST /api/v1/admin/stores/{public_id}/enable` mirroring disable, audited
  as `store.enabled`, 404 `store_not_found`. (2) The HQ-store panel changes where **all**
  plan-fee revenue is collected with no confirmation, and the resolved
  `merchant_account_id` is only shown *after* the write — a shape-valid typo reroutes
  platform revenue with no friction.
- **Test**: pytest — the enable route requires a password session, is idempotent, 404s an
  unknown store, and writes one audit row; browser — the link change prompts first.

### T-45 — Console: distinguish failure from empty, and close the operator dead ends
- **Status**: `complete` · **Priority**: S3 · **Gaps**: A3–A12 · **Depends on**: T-44
- **Description**: the console's remaining rough edges, all operator-facing. List pages
  render the error banner *and* the "no results" empty state together, so a failed fetch
  reads as "there are none"; detail pages report any non-404 failure as "not found"; the
  global invoices list has no Resolve action; `is_featured` can be set at creation but
  never toggled; re-deliver resets successful deliveries too, with no confirmation
  (duplicate `payment.completed` at the merchant); the **critical** "worker queues not
  draining" alert has `href: None` and so renders with no next step; "Webhooks failed
  (24h)" links to a list that applies no time filter; eight reachable refusal codes print
  raw machine strings; `pending` subscriptions are invisible in the Plan panel while
  their open invoice is visible; and self-suspension is allowed whenever more than one
  admin exists.
- **Test**: pytest for the code-side changes; browser for the failure-vs-empty states.

### T-46 — Verification, release, and the launch gate
- **Status**: `complete` · **Priority**: S1 · **Depends on**: T-37…T-45
- **Description**: `ruff check`, the full suite against Postgres in Docker, `next build`
  for both apps in Docker, browser checks of the new guards and states, then mirror to the
  VPS and re-probe production. Update `docs/production-readiness.md` with a P1-6 section
  recording this audit and what it closed.
- **Test**: the probes, re-run against production.

### T-47 — Give the platform its own separate context, so it is not its own tenant
- **Status**: `complete` · **Priority**: S1 · **Gaps**: new (found 2026-09-21, "should we subscribe ourselves to our own plan?") · **Depends on**: T-44
- **Description**: asked whether the platform should be on its own plan, the answer
  turned out to be that the code currently makes it a tenant **and meters it**. Verified
  in source: `check_plan_quota` (`services/payments.py:380-410`) has no exemption, and
  `count_paid_payments_this_month` (`:413-426`) counts every paid payment on the
  account's stores; `_record_plan_ledger_entry(store.account_id, payment)` is called from
  `mark_paid` (`:635`) — and the HQ store **belongs to the platform-admin account**, so
  **every plan fee a merchant pays writes a usage and volume row against the platform's
  own account**; and the console's paid-today figure (`routers/admin.py:2034-2041`) sums
  every paid payment platform-wide with no exclusion, so platform revenue would appear as
  platform GMV. `grant-admin` attaching a free subscription is defensive and harmless in
  itself (the account needs a billing row to hold an invoice), but it is what makes the
  platform read as a tenant.
  **The decision is to model this as a separate kind, not a discount**: a store that
  belongs to the platform is an *internal store*, a first-class category rather than an
  identity check inside the money path. That way the platform owner gets a real working
  context — its own storefront receiving plan fees by KHQR through its own ABA PayWay
  link — that is instrumented apart from merchant tenancy instead of being metered by it.
- **Test**: pytest — an internal store's paid payment does not move the owning account's
  month count, does not append a usage-ledger row, and is excluded from the console's
  merchant volume while appearing in platform revenue; a merchant payment is unaffected
  in every one of those four places.

> **Launch gate.** When Wave 8 closes, the only open items are the ones no code can close,
> and each is stated rather than implied:
> 1. **P1-4 — the lawyer review.** T-43 removes the last code-visible trace of the draft
>    state; it cannot substitute for the review. Unchanged and still open.
> 2. **The liability cap in Terms §8.** The identification half of L5 is closed on
>    2026-09-21: `/terms` names **Chmaba** and its registered address at #62, Street
>    P-10D, Sangkat Veal Sbov, Khan Chmbar Ampov, Phnom Penh, Cambodia, and states no
>    company number because none was supplied. What remains is that §8 excludes indirect
>    loss but sets no ceiling, and a liability cap is a decision for the lawyer — it goes
>    to P1-4 rather than being drafted here.
> 3. **The platform's own collection store and link** (Wave 2's operator action, made
>    concrete by T-47). Nothing has been collected and nothing *can* be yet: production
>    has **zero stores**, `CHMABAPAY_HQ_PAYWAY_LINK` is empty, and `resolve_hq_store` has
>    nothing to return, so a paid plan change correctly answers `503 billing_not_open`.
>    The order matters — create a store for `duke@chmaba.com` named **`ChmabaPay HQ`**
>    (the name the resolver looks for), then set the ABA PayWay link in the console's
>    "Plan fee collection" panel, which also marks that store internal. Setting
>    `CHMABAPAY_HQ_PAYWAY_LINK` in `deploy/.env` is the alternative if the panel is
>    unreachable.
> 4. **Console access.** Found while answering "what is the admin username and password?":
>    there was **no platform-admin account at all**, so `admin-pay.chmaba.com` could not be
>    opened by anyone, and the whole of T-44/T-45 plus the HQ-store panel was unreachable
>    in production. The second audit missed it because the console was audited from source
>    without credentials and "the console is password-only" was verified as a *code* claim,
>    never as "an operator can actually get in". Fixed on 2026-09-21 by
>    `uv run python -m chmabapay.cli grant-admin duke@chmaba.com --name Duke` on the VPS
>    (`CHMABAPAY_PASSWORD` supplied through `docker compose run -e` so it never entered
>    shell history), which created account id=2 with `is_platform_admin`, white-label and a
>    free subscription, then verified end to end: `POST /auth/login` returns 200 with
>    `is_platform_admin: true`, the session JWT carries `amr: "password"`, and
>    `GET /api/v1/admin/overview` answers **200** with that cookie and **401** without it.
>    `CHMABAPAY_ADMIN_EMAILS` is `duke@chmaba.com`, so the env and the account now agree;
>    `CHMABAPAY_ADMIN_PASSWORD` is deliberately left empty, and the account's password
>    lives only as a hash.
> Everything else the second audit found is closed here. That is what makes this the last
> wave before the launch announcement.

**Addendum, 2026-09-22 — the two items that were still open when this wave closed.** Kept
as an addendum rather than edited into the block above, so the record of what Wave 8 left
open stays intact:

1. **P1-4's lawyer review** was closed by the operator's own decision, not by a review, and
   §8's cap was accepted as drafted — indirect loss excluded, no ceiling. The limits of that
   decision are recorded against P1-4 in `docs/production-readiness.md`. Nothing in this repo
   has been read by a lawyer, and that file says so.
2. **The HQ PayWay link** is set and verified in production: `ChmabaPay HQ` (store 1) is
   `is_internal` and carries the console-saved `aba_payway` link, with
   `CHMABAPAY_HQ_STORE_ID` empty so `resolve_hq_store` answers from the console rather than
   the environment.

`docs/production-readiness.md` is the live record and carries both closures; this section
stays as the history of how the wave closed. Nothing is left open on either: P1-4's KYB
capture is **out of scope permanently by the same operator decision**, not deferred — this
platform does no identity or business verification, now or later, so there is no item here
waiting to be picked up.

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
| 8 Launch closure | T-37…T-46 | 1 | 7 | 2 |

Waves 2 and 3 were gated on decisions D2 and D1 and could not start until those were
answered. Waves 1, 4, 5 and 6 could start immediately; the tasks that depended on D5, D6
and D8 took the low-risk default, and each default is recorded in the task's `Shipped:`
block so the choice is visible rather than implied.

**Closing state (2026-09-21):** Waves 1–7 are closed — all 37 registered tasks (T-01…T-36
plus T-08b) are `complete` — and **Wave 8 (T-37…T-46) is the launch wave, in progress.**
Production runs `02fd387` at Alembic `0010`. Three items no code can close are listed in
Wave 8's Launch gate: P1-4's lawyer review of the merchant agreement, the registered
entity's details that the Terms must state, and the HQ PayWay link without which a paid
plan change correctly answers `503 billing_not_open`. Everything else the 2026-09-21
audit found is closed inside Wave 8, which is what makes it the last wave before the
launch announcement.
