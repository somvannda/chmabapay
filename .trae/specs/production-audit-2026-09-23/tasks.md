# Production Audit — Tasks

Action plan for `.trae/specs/production-audit-2026-09-23/spec.md`.
Findings are referenced by their spec ID (e.g. `C-01`).

**How to read a task.** `Status` is `todo` until shipped. `Gaps` maps to spec IDs.
`Acceptance` is the test that decides it is done — an item is not complete because
the code changed, only because its test passes.

**Wave order is the recommendation.** Wave 1 closes the launch blocker and needs no
decision. Waves 2–3 make customer-facing surfaces true and bring the API reference
up to standard. Waves 4–7 are quality and legal work that can land after merchant #1.
Wave 8 verifies and releases. **Wave 9 is the new support-ticketing feature (D-4)** —
the one net-new build in this plan, and not launch-blocking on its own.

---

## Wave 1 — Unblock onboarding (P0, no decisions needed)

### PA-01 — Make webhook endpoint creation work
- **Status**: `complete` · **Priority**: **S1** · **Gaps**: C-01 · **Depends on**: —
- **Problem**: the create modal sends `enabled`, `WebhookCreate` forbids extra
  fields, so every create returns `422 Extra inputs are not permitted`. Reproduced
  live on production. This breaks onboarding step 3 and launch acceptance
  criterion 2.
- **Files**: `web/landing/app/dashboard/webhooks/page.tsx` (body build at
  `:579-587`, POST at `:598-603`), `src/chmabapay/routers/webhooks.py:30-35`
- **Action**: in the create branch, send only `url` and `events`. Do not send
  `enabled` on create; the endpoint is always created active
  (`WEBHOOK_ENDPOINT_ACTIVE`). Keep `enabled` on the PATCH branch, which is why
  edit already works.
- **Acceptance**: (a) live browser test — create an endpoint through the UI and it
  appears in the list with a signing secret; (b) a regression test pins the accepted
  payload and the rejection of `enabled`; (c) the created endpoint is deleted again
  so production state is unchanged.
- **Shipped (2026-09-23)**: `webhooks/page.tsx` now sends `url` + `events` only, and
  adds `enabled` on the PATCH branch alone. Pinned by
  `tests/test_audit.py::test_a_webhook_create_accepts_the_dashboard_payload`, which
  asserts 201 for exactly the payload the form sends and 422 when `enabled` is
  included. `pytest -k webhook` → 8 passed; `ruff check src/chmabapay tests` → clean.
  The production browser re-test is part of PA-34 (not yet run against the deployed
  build, which still carries the old bundle until release).
- **Correction (2026-09-23, during PA-34)**: **the fix above was not in the file.** The
  create body still carried `enabled`, and PA-34's live browser pass reproduced the
  original 422 exactly — `{"detail":[{"type":"extra_forbidden","loc":["body","enabled"],
  "msg":"Extra inputs are not permitted","input":true}]}`. Endpoint creation from the
  portal was still impossible, so C-01 was never actually closed.
  Two things were wrong. The edit was lost (the file is a working-tree modification with
  no commit to recover it from, so it was either never applied or overwritten by a later
  edit to the same file). And the regression test *could not* have caught it: it asserts
  the accepted payload against a literal written in `test_audit.py`, so it pinned what
  the API takes while the form went on sending something else. A green test over a
  hardcoded payload is not a guard.
  Now fixed properly: the create body is `{url, events}` and `body.enabled` is assigned
  under `if (isEditing)`. Pinned by
  `tests/test_audit.py::test_the_webhook_form_sends_the_payload_the_api_accepts`, which
  reads `web/landing/app/dashboard/webhooks/page.tsx` as source — the same approach
  `test_openapi_schema.py` takes to the docs page — and asserts the body literal carries
  no `enabled` while the PATCH assignment survives. `pytest tests/test_audit.py -k
  webhook` → 9 passed; the live lifecycle then verified end to end (below).
- **Verified live (2026-09-23, PA-34)**: signed in at
  `/auth/_dev/login?email=pa34.verify@chmaba.test`, then on `/dashboard/webhooks`:
  "+ Add endpoint" → URL `https://example.com/webhooks/chmabapay`, no events ticked →
  **201** and the "Webhook signing secret — save it now" modal appeared with a
  `whsec_…` secret. The row read `https://example.com/webhooks/chmabapay` /
  `All events` / `active` / `Sep 23, 2026`, with Edit, Send test, Deliveries, Rotate
  secret, Disable, Delete. "Delete" → the "Delete this endpoint?" confirmation →
  "Delete endpoint" → the page returned to "No webhook endpoints yet." No `/v1/*`
  request answered 4xx or 5xx during the run. The endpoint was created against the
  local database and deleted again, so no production state changed.

### PA-02 — Remove the no-op "Enabled" control from the create form
- **Status**: `complete` · **Priority**: S4 · **Gaps**: C-02 · **Depends on**: PA-01
- **Problem**: creating an endpoint always sets it active, so the create form's
  "Enabled" checkbox changes nothing — an unticked box still produces an enabled
  endpoint. After PA-01 the field is not sent on create at all.
- **Note**: an earlier live observation described the *event* checkboxes as
  read-only. Source (`:655-670`) shows they are editable, call `toggleEvent`, and the
  wildcard default matches the form's own note at `:671-673`. That claim was wrong
  and is withdrawn — do not "fix" the event checkboxes.
- **Files**: `web/landing/app/dashboard/webhooks/page.tsx:676-686`
- **Action**: show the Enabled control only when editing, where it works through
  `WebhookPatch`; or render it disabled on create with a note that new endpoints are
  always created active.
- **Acceptance**: the create form offers no control that has no effect; edit still
  toggles status as it does today.
- **Shipped (2026-09-23)**: the Enabled checkbox is rendered only under
  `{isEditing && …}` (`webhooks/page.tsx:676-690`), so the create form no longer offers
  a control that cannot change the outcome, and edit still round-trips `enabled` through
  `WebhookPatch`. The note above stands: the event checkboxes were wrongly reported as
  read-only and are untouched. Verified by reading the form source during PA-34.

### PA-03 — Stop showing raw validator text to merchants
- **Status**: `complete` · **Priority**: S2 · **Gaps**: C-03 · **Depends on**: PA-01
- **Problem**: FastAPI/Pydantic `detail` strings reach the merchant verbatim
  ("Extra inputs are not permitted", `quota_exceeded`, `store_disabled`).
- **Files**: `web/landing/components/portal/apiError.ts:63-71`,
  `src/chmabapay/services/payments.py:366-396`
- **Action**: one shared error→copy mapper with an upgrade CTA where the refusal is
  a plan limit, and a generic fallback for unmapped codes. A 422 must never render
  the validator's internal message.
- **Acceptance**: a unit test maps each documented `detail` code to non-empty copy;
  a live 422 renders human text, and the raw detail is available only in the
  console, not the UI.
- **Shipped (2026-09-23)**: `readApiErrorInfo` recognises FastAPI's validation array
  (`isValidationDetail`) and answers with form-level copy —
  "Some of the details in that request were not accepted. Check the values and try
  again." — instead of the validator's prose. The array branch was removed from
  `detailOf` so no path can re-expose it. Code-mapped `detail` strings, including
  the `payway_link_` prefix used for field-level placement, are untouched.

### PA-03b — Retire the dead error code and stop leaking machine tokens
- **Status**: `complete` · **Priority**: S3 · **Gaps**: C-12, C-13 · **Depends on**: PA-03, PA-10
- **Problem**: found while enumerating the error surface for the docs. `ERROR_COPY`
  defined `period_already_invoiced`, which no backend code raises, while the code that
  *is* raised — `open_invoice_unpaid` — was unmapped and rendered as
  `open_invoice_unpaid: invoice 12 for 2026-09 is still unpaid`. More generally every
  unmapped code reached the screen verbatim.
- **Files**: `web/landing/components/portal/apiError.ts`, `src/chmabapay/routers/billing.py:340`
- **Action**: map `open_invoice_unpaid` and `billing_not_open`; drop the dead entry;
  answer the general unmapped case with form-level copy instead of a raw token.
- **Acceptance**: no unmapped code renders as a token or as `code: upstream text`.
- **Shipped (2026-09-23)**: `open_invoice_unpaid`, `billing_not_open` and
  `password_too_long` are mapped; `period_already_invoiced` is deleted. The fallback
  answers any bare `snake_case` token or `code: …` shape with form-level copy, so an
  exception-carrying code such as `bakong_error: …`, `qr_render_failed: …` or
  `payway_hosted_error: …` can no longer surface upstream text. Prose is shown for
  exactly two curated codes (`email_change_requires_password`,
  `platform_admin_cannot_self_delete`) via a small `PROSE_CODES` set, plus a dedicated
  branch for `tx_not_found_yet`, which uses `;` rather than `:` and so never reached the
  code lookup at all.

### PA-03c — Refuse an over-long password instead of 500ing
- **Status**: `complete` · **Priority**: S2 · **Gaps**: E-04 · **Depends on**: —
- **Problem**: `PasswordChangeIn.new_password` is bounded in characters
  (`max_length=200`), but bcrypt hashes at most 72 **bytes** and `hash_password` raises
  a bare `ValueError` past that. A 73-character ASCII password, or 25 Khmer characters
  (75 bytes), produced an unhandled 500 on `POST /v1/me/password`.
- **Files**: `src/chmabapay/routers/account.py:88,253`, `src/chmabapay/security.py:37-46`
- **Action**: check the byte length against `MAX_PASSWORD_BYTES` and answer
  `400 password_too_long`. Checked in bytes, because a character bound cannot catch the
  multi-byte case — which is the case that actually matters for a Khmer-first product.
- **Acceptance**: a 73-byte password returns 400 `password_too_long`, not 500, and the
  account's existing password is unchanged.
- **Shipped (2026-09-23)**: the guard is in place, and pinned by
  `tests/test_account_security.py::test_a_new_password_longer_than_bcrypt_allows_is_refused_not_a_500`,
  which covers both a 73-character ASCII password and 25 Khmer characters, and asserts
  the stored hash is untouched. It was verified as a real defect before the fix.
  `pytest tests/test_account_security.py` → 15 passed; `ruff` clean.

---

## Wave 2 — Truth on customer-facing surfaces

### PA-04 — Remove the Bakong Ledger Lookup group from the API page
- **Status**: `complete` · **Priority**: S2 · **Gaps**: B-01 · **Depends on**: —
- **Problem**: 10 endpoints are published while their own text says they answer
  `503 bakong_not_configured` today. Internal operational state on a marketing page.
- **Files**: `web/landing/app/api/docs/page.tsx:76-93`
- **Action**: delete the group (decision D-3). Remove the "Not available" badge and
  the `503` disclosure with it. The routes stay mounted — they are only
  unadvertised.
- **Acceptance**: `grep` for `bakong_not_configured` and `token/renew` returns
  nothing in `web/landing/app/api/docs/page.tsx`.
- **Shipped (2026-09-23)**: group deleted, together with the `unavailable` field's
  JSX handling and the now-dead `.docs-endpoint-badge` and
  `.docs-endpoint-card.is-unavailable` CSS rules. The page lists 10 groups. The
  trailing "the Bakong ledger lookups below are not switched on" sentence was removed
  from the Payment Reconciliation summary, which would otherwise have pointed at a
  group that no longer exists. Verified: `grep` for the removed paths returns nothing.

### PA-05 — Remove the non-payable KHQR helpers from the integration surface
- **Status**: `complete` · **Priority**: S2 · **Gaps**: B-02 · **Depends on**: —
- **Problem**: `POST /v1/khqr/from-link` is documented as returning a code that "is
  not payable", and `POST /v1/khqr/probe-aba-status` as "advisory only" and able to
  be wrong in either direction. Neither is integration surface.
- **Files**: `web/landing/app/api/docs/page.tsx:95-105`
- **Action**: remove both rows from the public page. Keep
  `POST /v1/khqr/payway/checkout` and `/payway/status`, which are the payable and
  authoritative routes.
- **Acceptance**: the KHQR group contains only the payable/authoritative rows.
- **Shipped (2026-09-23)**: `from-link` and `probe-aba-status` removed, and the group
  summary rewritten — the old summary existed to explain why a self-built code is not
  payable, which only mattered while those rows were listed. Remaining rows:
  `payway/checkout`, `payway/status`, `render.svg`.
- **Verified (2026-09-23, PA-34)**: `grep -n "from-link|probe-aba-status|
  bakong_not_configured" web/landing/app/api/docs/page.tsx` → no matches. The acceptance
  clause holds.

### PA-06 — Make the priority-support claim true
- **Status**: `complete` · **Priority**: S2 · **Gaps**: A-08 · **Depends on**: PA-36…PA-41
- **Problem**: Pro advertises priority support; nothing implements it, and
  `/contact` promises no response time. Decision D-4 is to build the feature, not
  remove the claim.
- **Files**: `web/landing/app/contact/page.tsx:69-75`, `src/chmabapay/db.py:112,123`
- **Action**: do **not** clear `priority_support` on Pro — the ticketing system in
  Wave 9 makes it true. This task is the copy alignment: `/contact` states the
  **24-hour calendar** first-response target for Pro and best-effort-with-no-target
  for Free and Starter; the portal support page states the account's own target.
- **Note**: ships with Wave 9; it depends on the ticketing tasks below.
- **Acceptance**: `/contact` states the Pro target and no longer contradicts the
  plan matrix; the target shown in the portal equals the one on `/contact`.

### PA-07 — Make the agreement the merchant accepts the agreement they were shown
- **Status**: `complete` · **Priority**: S2 · **Gaps**: A-03 · **Depends on**: decision **D-5**
- **Problem**: the gate offers "Read the merchant agreement" and links to `/terms`,
  while the API records acceptance of a document that is not published and not in
  force.
- **Files**: `web/landing/app/dashboard/layout.tsx:246-276`,
  `src/chmabapay/routers/keys.py:100-102`, `src/chmabapay/routers/account.py:371-403`
- **Action**: per D-5 (recommended **(b)**) align the labels and the recorded
  evidence to the Terms, or per (a) publish the agreement and gate on it. Either
  way the button label, the target document and the stored `terms_accepted_version`
  must name the same thing.
- **Acceptance**: label, linked document and recorded version agree; a merchant can
  read what they are asked to accept.
- **Shipped (2026-09-23)**: **the acceptance clause already held — no code change was
  needed, and none was made.** Read at the three call sites the finding named:
  1. the gate's button reads **"Read the Terms of Service"** and links to `/terms`
     (`web/landing/app/dashboard/layout.tsx:274-276`), not "Read the merchant agreement";
  2. the recorded evidence is `settings.terms_version`
     (`src/chmabapay/routers/account.py:395-403`, refused with `409
     terms_version_superseded` if a stale version is submitted), and `keys.py:110-112`
     compares against that same published version;
  3. `/terms` is public — 200 in the PA-34 production re-probe — so a merchant can
     actually read what they are asked to accept, which was the finding's real complaint.
  Label, linked document and stored version name the same thing, which is D-5(b).
- **Residual, recorded rather than actioned**: `docs/legal/merchant-agreement.md` still
  exists as an unpublished draft in the repo, and internal vocabulary still says "merchant
  agreement" in comments and docstrings (`keys.py:94`, `account.py:44,384`,
  `models.py:114`, `config.py:151`, `.env.example:209-211`,
  `dashboard/layout.tsx:246`). None of it reaches a merchant, so it is a naming
  inconsistency rather than the A-03 defect. Renaming it is a sweep with no behavioural
  effect and was deliberately not bundled into a verification wave; what the draft
  document itself should become (published, superseded by `/terms`, or deleted) is a legal
  decision that belongs with D-1 and PA-33b.

### PA-08 — Correct the legal text that has drifted
- **Status**: `complete` · **Priority**: S3 · **Gaps**: A-10, A-11, A-12 · **Depends on**: —
- **Files**: `web/landing/app/terms/page.tsx:228-231,63-64`,
  `web/landing/app/privacy/page.tsx:64-65`, `web/landing/app/contact/page.tsx:27-43`
- **Action**: (1) rewrite §6 billing to match `(subscription, period_start)`; (2)
  correct the district spelling to "Chbar Ampov" in all three documents; (3) add the
  registered entity and address to `/contact`.
- **Acceptance**: no page states the removed per-calendar-month rule; the district
  string is consistent across terms, privacy and contact.
- **Shipped (2026-09-23)**: §6 now describes the real refusal — an unpaid invoice for
  the period blocks a further change covering that period — instead of the removed
  calendar-month key. "Chmbar Ampov" corrected to "Chbar Ampov" in Terms, Privacy and
  Contact. `/contact` carries the entity and registered address.

### PA-09 — Metadata and dead routes
- **Status**: `complete` · **Priority**: S4 · **Gaps**: A-14, A-15, A-16 · **Depends on**: —
- **Files**: `web/landing/app/layout.tsx:30-70`, `web/landing/app/page.tsx:1`,
  `web/landing/app/robots.ts`
- **Action**: add `alternates.canonical` to the home page; drop the unused import;
  remove the stale `/onboarding` entry from `robots.txt`.
- **Acceptance**: `curl -s https://pay.chmaba.com/ | grep canonical` returns a tag;
  `GET /robots.txt` no longer lists a 404 route.
- **Shipped (2026-09-23)**: `page.tsx` exports `metadata` with
  `alternates: { canonical: "/" }` — declared on the home route rather than in the root
  layout, which would have leaked the home canonical onto every page without its own.
  The unused `brandColors` import is gone (it was the only reference in the file), and
  `/onboarding` is removed from `robots.txt`.
- **Verified (2026-09-23, PA-34)**: `page.tsx` carries `alternates: { canonical: "/" }`
  and no `brandColors` reference; `grep -n onboarding web/landing/app/robots.ts` → no
  matches. A-16's second half (`/.well-known/security.txt`) is not part of this task's
  acceptance and was not re-probed.

---

## Wave 3 — API documentation as a developer reference

The tables live in `web/landing/app/api/docs/reference.ts` as data, so
`tests/test_api_docs_errors.py` can read the codes and limits straight out of the page
and fail when the two drift apart.

### PA-10 — Add an error-code reference
- **Status**: `complete` · **Priority**: S2 · **Gaps**: B-03, C-12, C-13 · **Depends on**: PA-04
- **Files**: `web/landing/app/api/docs/reference.ts`, `page.tsx`,
  `tests/test_api_docs_errors.py`
- **Action**: a table of `detail` code → status → meaning, grouped by area, sourced from
  the routers so it is complete rather than illustrative.
- **Acceptance**: every `detail` string emitted by a public router appears in the table;
  a test enumerates the strings from source and fails on a missing row.
- **Shipped (2026-09-23)**: 11 groups — Access, Account, Stores, Payments, KHQR codes,
  Keys and webhooks, Billing, Reconciliation, Hosted checkout, Reports, Internal —
  carrying 60 codes. Both directions are asserted: every code the scanned modules raise
  must be documented (or named in `NOT_IN_REFERENCE` with a reason), and no documented
  code may be one nothing raises. That second test is the direct descendant of C-12,
  where a code was mapped for months while a different one was being raised.
- **Found while doing this**: `invalid_link`, `payload_too_long` and `invalid_payload`
  are reachable from documented `/v1/khqr` routes and were about to be excluded as
  withheld surface. Checked before excluding, and documented instead.
- **Note**: the regex reads `detail="token"` and the parenthesised multi-line f-string
  form. Two rows are deliberately not machine codes — the three `Max …` plan-limit
  messages and the two `Invalid 'from'|'to' date` messages are prose, and the tables say
  so rather than pretending a token-shape that does not exist.
- **Defect found by the render check**: a browser pass over the new page found literal
  backtick characters on screen. The inline-code convention (`RichText`) was applied to
  the new tables but not to the two endpoint summaries that already used backticks, nor
  to the rate-limit column — those rendered `` `ck_live_` `` and `` `POST /v1/payments` ``
  as raw text with visible ticks. Pre-existing for the summaries, introduced for the
  limits table, fixed for both by routing `summary`, `description` and `applies` through
  the same renderer. `tsc` cannot see this class of fault; only looking at the page did.

### PA-11 — Document rate limits
- **Status**: `complete` · **Priority**: S3 · **Gaps**: B-04 · **Depends on**: —
- **Files**: `web/landing/app/api/docs/reference.ts`, `page.tsx`,
  `src/chmabapay/config.py:89-94`, `src/chmabapay/ratelimit.py:243-256`
- **Action**: state each limit, the window, the scope (per key vs per IP), and the `429`
  response shape.
- **Acceptance**: documented values equal `config.py`.
- **Shipped (2026-09-23)**: the five rules (api 600/min, payment_create 60/min, khqr
  60/min, checkout 120/min, auth 20/min) with scope and what each covers, plus the 429
  body and the `Retry-After` / `X-RateLimit-*` headers as the middleware actually writes
  them. `test_the_documented_rate_limits_match_the_configuration` compares the page to
  `Settings` field by field, so changing a limit in `config.py` fails the suite until the
  page matches.

### PA-12 — Add Authentication and Environments sections
- **Status**: `complete` · **Priority**: S3 · **Gaps**: B-05, E-01 · **Depends on**: —
- **Problem**: nothing told an integrator that only live keys exist and a first
  integration moves real money.
- **Action**: an Authentication section and an Environments section stating plainly
  that there is no sandbox.
- **Acceptance**: the page states the live-only reality or documents the sandbox.
- **Shipped (2026-09-23)**: "One key authenticates every store" covers the Bearer form,
  the show-once/hash-at-rest property, and the routes that are session-only because they
  change the account itself (`/v1/me`, `/v1/account`, `/v1/billing/*`) — an API key is
  refused there, which is the kind of thing a reader otherwise discovers as a 401 they
  cannot explain. "There is no sandbox" states the live-only reality per D-2 and gives
  the safe path: point a store at your own PayWay link, mint one 0.01 payment, verify the
  `payment.completed` signature, and only then switch to the merchant's link. If test
  keys ever ship, this is the section they belong in.

### PA-13 — Document the conventions that already exist
- **Status**: `complete` · **Priority**: S4 · **Gaps**: B-06, B-08 · **Depends on**: —
- **Action**: idempotency, pagination, versioning, and the amount-unit rule.
- **Acceptance**: each convention has a section with a runnable example.
- **Shipped (2026-09-23)**: six conventions — Authentication, Idempotency, Amounts,
  Pagination, Versioning, Errors. Each states the rule and where it bites rather than
  restating the schema: `idempotency_key` must be **in the body** (a header is not read,
  which is the mistake worth preventing), `amount` is a decimal while every `*_cents` is
  an integer, `GET /v1/stores` is deliberately not paginated, and `/v1` takes additive
  changes while a break would arrive as `/v2`.

### PA-14 — Stop the page drifting from the runtime
- **Status**: `complete` · **Priority**: S3 · **Gaps**: B-07 · **Depends on**: PA-04
- **Files**: `web/landing/app/api/docs/page.tsx`, `tests/test_openapi_schema.py`
- **Action**: a test that diffs documented `method + path` against the runtime OpenAPI
  paths; link `/openapi.json` for machine consumers.
- **Acceptance**: deleting a real route or adding a documented path that does not exist
  fails the suite.
- **Shipped (2026-09-23)**: half of this already existed —
  `test_every_published_path_is_documented_or_declared_internal` covered *published but
  not documented*, which is what caught PA-04/PA-05 removing twelve documented rows and
  made the omission an explicit decision rather than a silent one. The missing direction
  is now added as `test_the_docs_page_documents_no_route_that_does_not_exist`: a
  documented route that the API does not serve fails the suite. The page links
  `/openapi.json`, which nginx routes at the edge (`deploy/nginx/api-locations.inc:25`);
  FastAPI's own `/docs` and `/redoc` stay unexposed by design.

---

## Wave 4 — Admin console safety and completeness

### PA-15 — Close the `amr: password` bypass
- **Status**: `complete` · **Priority**: S2 · **Gaps**: D-01 · **Depends on**: decision **D-6**
- **Problem**: the `Bearer ck_` branch precedes the amr check, so an admin API key
  plus any valid session cookie reaches every admin route without the password claim.
- **Files**: `src/chmabapay/routers/admin.py:87-108`
- **Action**: per D-6 (recommended **(a)**) reject `ck_` on `/v1/admin/*`. Also
  correct the claim in `web/admin/README.md` so the documentation matches the code.
- **Acceptance**: a pytest case sends a platform-admin `ck_` key with a valid
  password-less session cookie to `GET /v1/admin/overview` and receives 403.
- **Shipped (2026-09-23)**: the `Bearer ck_` branch is **deleted**, not merely
  unreachable — so the identity on `/v1/admin/*` is always a password session and the
  refusal is structural. `get_hybrid_admin_context` lost its `authorization` and
  `session` parameters; `HybridAuthContext` lost its two now-unusable fields
  (`key_ctx`, `is_session`, neither of which any handler read); the
  `KeyContext` / `resolve_key_context` import went with them. A key with no cookie is
  still refused by `get_current_session_account` (401 `invalid_session`), and one with a
  password-less cookie by the claim check (403 `password_session_required`) — both
  pinned by `test_an_api_key_cannot_skip_the_password_claim` in `test_admin_plans.py`.
  `web/admin/README.md` was rewritten in both places that described the old branch (the
  contract paragraph and the "unreachable / pending a decision" note), the stale comment
  in `test_audit.py` and the test-plan note in `tools/testplan.py` were corrected, and
  the P1-2 finding in `docs/production-readiness.md` now records the resolution instead
  of an open decision.

### PA-16 — Let an operator resolve a refund dispute
- **Status**: `complete` · **Priority**: S2 · **Gaps**: D-02 · **Depends on**: —
- **Files**: `src/chmabapay/routers/admin.py`, `web/admin/app/payments/[public_id]/page.tsx`
- **Action**: `POST /v1/admin/payments/{public_id}/reverse` reusing
  `services.payments.reverse_payment`, with a mandatory reason, the same terminal
  state guards, an audit row, and a UI action on the payment detail page behind a
  confirmation modal that echoes the payment id and amount.
- **Acceptance**: pytest — reverse a merchant's paid payment as an admin, assert
  `reversed_at` set, a `payment.reversed` event enqueued and an audit row written;
  re-reversing returns 409.
- **Shipped (2026-09-23)**: the route reuses `reverse_payment`, so the state guards
  (`409 payment_not_paid`, `409 payment_already_reversed`), the negative ledger row, the
  plan-ledger correction and the `payment.reversed` webhook are identical to the
  merchant path; the reason is mandatory (`PaymentReasonIn`, 3–255 chars, `extra="forbid"`)
  and the audit row is `admin.payment_reversed` with the amount, the account and the store.
  The audit row is written into the transaction `reverse_payment` commits, so a refused
  reversal leaves no trail — asserted, not assumed. UI: a **Record refund** button in the
  payment detail's Resolve panel, enabled only for a settled payment, behind a modal that
  echoes the payment id, the amount, the store, and the fact that it cannot be undone, with
  its confirm button labelled **Confirm and record refund** (distinct from the button that
  opens it, per the earlier duplicate-label lesson).
- **Tests**: `test_an_operator_can_reverse_a_merchants_paid_payment` (200, status
  `reversed`, ledger `[+1250, −1250]`, both events, audit row) and
  `test_reversing_a_payment_that_never_settled_is_refused_and_records_nothing` (409
  `payment_not_paid`, 422 without a reason, no audit row). The reverse route was added to
  `test_every_operator_action_is_admin_gated`.

### PA-17 — Wire the internal-store toggle
- **Status**: `complete` · **Priority**: S2 · **Gaps**: D-03 · **Depends on**: —
- **Files**: `src/chmabapay/routers/admin.py:1289-1348`,
  `web/admin/app/accounts/[account_id]/page.tsx` (Stores table)
- **Action**: add an "Internal" toggle on the account-detail Stores table calling the
  existing endpoint, with a confirmation modal echoing the store name and id.
- **Acceptance**: toggling a store to internal excludes it from merchant GMV and
  quota; toggling back restores it.
- **Shipped (2026-09-23)**: a **Mark as platform** / **Unmark platform** button on every
  store row, behind a `ConfirmAction` of kind `internal-store` that echoes the store name
  and `public_id` and spells out the consequence in each direction (exempt from merchant
  volume and quota, takings reported as platform revenue) plus the warning that an
  ordinary merchant store marked this way stops being billed. Sent as
  `PUT /v1/admin/stores/{id}/internal` with `{is_internal}`; the API's `changed` flag picks
  the message, so a repeat says so rather than claiming a change. The GMV/quota acceptance
  is the pre-existing `test_an_internal_store_is_not_metered_and_reports_as_platform_revenue`,
  which already drives the endpoint directly. `web/admin/README.md` now says the route is
  reached from that table.

### PA-18 — Fix the void-invoice dead end
- **Status**: `complete` · **Priority**: S3 · **Gaps**: D-04 · **Depends on**: —
- **Files**: `web/admin/app/accounts/[account_id]/page.tsx:93,959`,
  `web/admin/lib/apiError.ts:14-45`
- **Action**: add `void` to the account-detail action list and to the "open" check
  so a voided invoice shows no Resolve button; add `invoice_already_void` to
  `ERROR_COPY`.
- **Acceptance**: a voided invoice renders no action; every `invoice_already_*`
  code has copy.
- **Shipped (2026-09-23)**: `void` added to `InvoiceAction` and to `INVOICE_ACTIONS`
  (with the "withdraws the claim, grants nothing, the next sweep may bill this window
  again" help text), and to the terminal-status list that gates the Resolve button — the
  list now matches `_RESOLVED_INVOICE_STATUS` in `admin.py`, whose `void` value is
  `billing_svc.INVOICE_VOID`. `invoice_already_void` added to `ERROR_COPY`, and
  `invoice_not_found` with it: it is the same class of unmapped code, and both were
  rendering as raw machine tokens.

### PA-19 — Support the `restricted` standing
- **Status**: `complete` · **Priority**: S3 · **Gaps**: D-05 · **Depends on**: —
- **Files**: `src/chmabapay/routers/admin.py:449-451`,
  `web/admin/app/accounts/[account_id]/page.tsx:167-176`
- **Action**: allow `restricted` in `AdminAccountPatch.status` and render it as a
  third standing state rather than as "active".
- **Acceptance**: an operator can freeze and unfreeze a restricted account from the
  console and the pill reflects it.
- **Shipped (2026-09-23)**: `AdminAccountPatch.status` accepts
  `active|suspended|restricted`, and `_STATUS_ACTIONS` names the new transition
  `account.restricted` (so the trail says what happened rather than "updated"). The
  console's Status row now offers **Freeze** and **Suspend** on an active account, and
  **Activate** (plus **Suspend**) on a frozen or suspended one, each behind a modal that
  echoes the account id and email and demands a reason for either restriction. The pill
  is its own state — `dash-pill-pending` labelled "restricted" — in both the detail page
  and the accounts list, where it previously rendered as the green "active".
  `openStatusConfirm` now takes the target standing as an argument: with three states a
  toggle has no single "other state".
- **Test**: `test_an_operator_can_freeze_an_account_without_locking_it_out` — freeze,
  assert a merchant key is refused `403 account_restricted` (the freeze is real, not a
  label), release, and assert exactly one `account.restricted` row naming the operator
  with the from/to and the reason.

### PA-20 — Bound the account-detail payload
- **Status**: `complete` · **Priority**: S3 · **Gaps**: D-06 · **Depends on**: —
- **Files**: `src/chmabapay/routers/admin.py:316-439`
- **Action**: cap or paginate the stores and keys collections the way invoices
  already are, and return counts so the UI can say "showing N of M".
- **Acceptance**: an account with many stores/keys returns a bounded payload.
- **Shipped (2026-09-23)**: both queries gained `.limit(ACCOUNT_DETAIL_ROW_CAP)` (50) on
  top of their existing deterministic order, following the invoice list's precedent at 24.
  The response gained `truncated: {stores, keys}` so the console knows the list was cut
  rather than the account having exactly 50, and `usage.keys_total` joins the existing
  `counts.stores` as the denominator for the "showing N of M" note now rendered under each
  table. `keys_active` / `keys_total` are counted in the database — the previous
  `keys_active` was a Python sum over the list, which the cap would have silently
  under-reported. (Note: `sum` over no rows is NULL, hence the `or 0`.)

### PA-21 — Make the deliveries retry opt-in real
- **Status**: `complete` · **Priority**: S3 · **Gaps**: D-07 · **Depends on**: —
- **Files**: `src/chmabapay/routers/admin.py:2037-2083`,
  `web/admin/app/deliveries/page.tsx:433-468`
- **Action**: give the row retry route the same `include_successes` opt-in the
  payment-level route has, and send it from the modal.
- **Acceptance**: retrying without the flag leaves a `success` row untouched.
- **Shipped (2026-09-23)**: `POST /v1/admin/deliveries/{id}/retry` takes
  `include_successes` (default false) and returns `retried: false` without touching a
  `success` row — the route's own "quiet on a repeat" convention rather than a new error
  code. The modal's checkbox is now what sends it (`retry(row, includeSuccesses)`), its
  copy no longer claims the delivery "is queued" before it is, the row stops being
  optimistically flipped to `retrying` on a no-op, and a success row left alone says so
  instead of "already queued to go".
- **Test**: `test_retrying_a_delivered_webhook_needs_the_opt_in`.
- **Fixed in passing, found by the suite**: the no-op branch compared
  `delivery.next_attempt_at` to an aware `now`. SQLite returns a naive value for that
  column where Postgres returns an aware one, so `TypeError` — a 500 — on the *second*
  retry of the same row; `test_retrying_a_delivery_reschedules_it_and_records_why`
  exercised exactly that path and was failing. A naive value is now read as UTC, the
  convention `webhooks.py` and `routers/reports.py` already use.

### PA-22 — Echo the resolved target in the remaining modals
- **Status**: `complete` · **Priority**: S4 · **Gaps**: D-09 · **Depends on**: —
- **Files**: `web/admin/app/accounts/[account_id]/page.tsx:1020-1055,1140-1168`,
  `web/admin/app/payments/[public_id]/page.tsx:584-600`
- **Action**: account suspend/activate echoes the account id and email; assign-plan
  echoes the plan and the account; mark-paid echoes the payment id and amount.
- **Acceptance**: each modal shows the resolved identifier before the confirm click.
- **Shipped (2026-09-23)**: the standing modal (now covering suspend, freeze and
  activate) opens with "This applies to `#12` sokha@…"; the assign-plan modal reads "This
  puts `pro` in force for `#12` sokha@…", so the plan code it echoes is the one currently
  selected in the form; and the mark-paid modal reads "This credits `pay_…` — $12.50 for
  Sokha Cafe". The refund modal built under PA-16 follows the same shape. `dash-code-mono`
  is used for the identifiers, matching the deliveries modal.

### PA-23 — Operator day-one tools
- **Status**: `complete` · **Priority**: S3 · **Gaps**: D-08, D-10 · **Depends on**: —
- **Scope**: pick from this list rather than doing all of it — decide at kickoff.
  - audit-log date range and CSV/JSON export (`admin.py:2418-2473`)
  - a health/metrics page surfacing `/health`, queue depth and worker heartbeat
  - key create/rotate on a merchant's behalf
  - search by store `public_id`, key prefix, or external reference across stores
  - deduplicate the double error banner on the five list pages
  - remove or repoint the admin OpenAPI link, which documents no admin route
- **Acceptance**: per sub-item chosen.
- **Decision (2026-09-23)**: all four tools selected, plus both freebies. External
  reference search is *not* included: a store id and a key prefix are the two things
  support is actually handed, and `external_id` is the merchant's own identifier, which
  the payments feed already searches.
- **Shipped (2026-09-23)**:
  - **Audit range + export** — `_audit_filters` is now the one filter builder for the list
    route and the new `GET /v1/admin/audit-logs/export` (`?format=csv|json`), so the export
    cannot disagree with the view it came from; `?from`/`?to` reuse `reports.py`'s
    day-boundary helpers. `AUDIT_EXPORT_CAP = 10_000` with `X-Total-Rows` /
    `X-Rows-Returned` headers, and `Content-Disposition: inline; filename="audit-log-<stamp>.<fmt>"`.
    The page grew the two date inputs and the two export buttons; a truncated export says so
    in the toast.
  - **Health page** — `GET /v1/admin/health` (app, database probe, worker transport, dev
    gateway, metrics-scrape state, expected queues, heartbeat max age, per-queue worker
    signals) and `/health` in the console. No connection strings: a console page may report
    *whether* the scrape is secured, not how to reach anything.
  - **Key mint/rotate on a merchant's behalf** — `mint_key` / `rotate_key_instance` moved into
    `routers/keys.py` and shared with the merchant routes rather than copied, because a second
    copy is a second place for the hash-at-rest rule, the scope, the prefix and the trail to
    drift. `POST /v1/admin/accounts/{id}/keys` and `POST /v1/admin/keys/{id}/rotate`; the
    console has **Mint a key** and per-row **Rotate**, and the raw key is shown once in a
    copy-and-close dialog.
  - **Search by store id / key prefix** — `GET /v1/admin/accounts?q=` matches email, name, a
    store's `public_id` and a key prefix. The query is sliced to twelve characters before it is
    compared, so a whole key pasted from a merchant's message resolves while the raw key and
    its hash are never read or stored.
  - **Freebie 1** — the five list pages showed the API error twice (a warn banner above a panel
    that repeated it). One surface now: the panel carries the message and what it means.
  - **Freebie 2** — the console's top link said "OpenAPI" and opened a schema that documents no
    console route (`routers/admin.py` sets `include_in_schema=False`). It now reads **Merchant
    API schema** and says so in its title, so it stops promising a contract it does not carry.
  - **Tests** — `test_an_operator_can_mint_a_key_for_a_merchant_who_cannot_sign_in`,
    `test_an_operator_can_rotate_a_key_and_the_old_one_dies_at_once`,
    `test_the_health_report_answers_for_the_deployment_without_secrets`,
    `test_an_account_can_be_found_by_store_id_or_key_prefix`, plus the four new route pairs in
    `test_every_operator_action_is_admin_gated` (which also pins that a refused mint leaves no
    key behind). `test_admin_actions.py` 30 passed; the six-file admin/audit/schema set 102
    passed; `ruff check` clean; `pnpm --filter admin typecheck` clean.

---

## Wave 5 — Portal correctness

### PA-24 — Correct the stale dashboard copy
- **Status**: `complete` · **Priority**: S3 · **Gaps**: C-04 · **Depends on**: —
- **Files**: `web/landing/app/dashboard/page.tsx:299-302`
- **Action**: webhooks and API keys are workspace-scoped; the store-scoped pages are
  redirect stubs. Reword so a new merchant does not hunt for per-store webhooks.
- **Acceptance**: no page claims per-store webhooks or keys.
- **Shipped (2026-09-23)**: the subtitle now reads "Each store has its own payment link.
  Webhooks and API keys belong to the whole workspace and serve every store — as do your
  plan and monthly quota." The two other places that describe the workspace shape were
  already correct and were left alone: the store detail page's onboarding list says "One
  key covers every store in your workspace" and "across all your stores". Verified in the
  browser on `/dashboard`.

### PA-25 — Expose the report filters the backend already supports
- **Status**: `complete` · **Priority**: S3 · **Gaps**: C-05 · **Depends on**: —
- **Files**: `web/landing/app/dashboard/reports/page.tsx:57`,
  `src/chmabapay/routers/reports.py:131-152`
- **Action**: add store, merchant and status filters, and render the JSON summary
  totals the endpoint already returns.
- **Acceptance**: a merchant can export one store's paid payments without
  post-filtering a spreadsheet.
- **Shipped (2026-09-23)**: the payments export grew a store select (from `/v1/stores`),
  an external-ID field, seven status checkboxes and three totals cards — matched rows,
  paid amount and count, refunded amount and count — read from
  `/v1/reports/payments.json` with `per_page=1`. One `filterParams` builder feeds both the
  totals and the CSV, so the count above the button cannot disagree with the file below
  it. The external ID applies on blur or Enter rather than per keystroke (the split the
  console's account search uses), and the totals blank to an em dash while a recount is in
  flight rather than showing the previous filters' numbers. A failed count says so and
  leaves the export usable; a failed *export* is a toast, not a page-level error state.
  Verified live: ticking "Paid" issues
  `GET /v1/reports/payments.json?from=…&to=…&statuses=paid&page=1&per_page=1`, the matched
  count moves from 1 to 0, and Download CSV fetches the same filters and saves the file.

### PA-26 — Expose `idempotency_key`, `merchant` and `hosted_qr` on payment create
- **Status**: `complete` · **Priority**: S3 · **Gaps**: C-06 · **Depends on**: —
- **Files**: `web/landing/app/dashboard/payments/new/page.tsx:108-119`
- **Action**: add the three fields with the same defaults the API applies.
- **Acceptance**: creating a payment with an idempotency key twice returns the same
  payment.
- **Shipped (2026-09-23)**: "Store external ID", "Idempotency key" and a "QR source"
  select (Automatic / ABA PayWay issues / Build it ourselves). All three are omitted from
  the body when untouched, so the request is exactly what the API's documented defaults
  describe. `hosted_qr: false` carries the reason it may be refused
  (`offline_qr_requires_a_confirmation_source`), and the external ID is a way to name the
  store that the API resolves *before* the public id — so the form accepts it on its own
  instead of leaving the submit button dead. Verified live: two submissions of
  `wave5-dup-key` landed on the same payment id
  (`etBDCduAh_7JDMIT0jT8lh-6`) with one row in the list.

### PA-27 — Let a store's city be corrected
- **Status**: `complete` · **Priority**: S3 · **Gaps**: C-07, C-08 · **Depends on**: —
- **Files**: `web/landing/app/dashboard/[public_id]/settings/page.tsx:150`
- **Action**: add a city field; validate the name inline instead of letting a raw
  422 escape when it is cleared.
- **Acceptance**: city is editable and appears in the CSV export; an empty name
  shows a field-level error.
- **Shipped (2026-09-23)**: the Store panel is now Name | City, then Support email as its
  own field. Both are checked before the request: a cleared name shows "A store needs a
  name." and a cleared city "A store needs a city.", each under its input, with
  `aria-invalid` set and the save button disabled — `StorePatch.name` is `min_length=1`
  and the city column is `NOT NULL`, and the API's answer for either was a 422 whose body
  is a list of validator objects, which reads as a broken form. `maxLength` matches the
  schema (120 / 15). Verified live: clearing each field shows its inline error and disables
  Save, "Siem Reap" saves and survives a reload, and the value is the one the stores
  export prints. The new-store form still does not ask for a city, so a portal-created
  store takes the column default and is corrected here.

### PA-28 — Remove or gate the dead "Test: Mark paid" control
- **Status**: `complete` · **Priority**: S3 · **Gaps**: C-09 · **Depends on**: —
- **Files**: `web/landing/app/dashboard/payments/[public_id]/page.tsx:176`
- **Action**: hide the button unless the dev gateway is mounted, or delete it.
- **Acceptance**: no control in production leads to a 404.
- **Shipped (2026-09-23)**: deleted rather than gated. Hiding it needs a signal the portal
  does not have — `/_dev/*` is mounted by a backend setting and `/v1/me` does not report it,
  so any client-side check would be a guess (and a probe request to find out). It is also
  no longer needed: the console carries a supported `POST /v1/admin/payments/{public_id}/mark-paid`
  that settles through the same `mark_paid` service, requires a reason and writes
  `admin.payment_mark_paid`. Removed with it: the handler, its `markingPaid` state, and the
  `useSession` read that existed only to decide whether to show the button.

### PA-29 — Correct the help FAQ
- **Status**: `complete` · **Priority**: S4 · **Gaps**: C-10 · **Depends on**: PA-06
- **Files**: `web/landing/app/dashboard/help/page.tsx:27`
- **Action**: render plan numbers from the plan payload, or correct the hardcoded
  copy to match live plans.
- **Acceptance**: help numbers equal the live plan payload.
- **Shipped (2026-09-23)**: the page reads `/v1/billing/plans` and builds the three answers
  that quote plans from it — the key limits, the per-plan allowances, and which plans carry
  priority support. When the read fails the answers drop the numbers and point at Billing
  instead of falling back to a remembered set, and a "Loading your plan limits" line says
  why the numbers are absent until they arrive. Copy that named plans by hand ("select Free,
  Starter or Pro") no longer does. Verified live against the payload: Free 3,000/1/1,
  Starter $9.99 15,000/5/3, Pro $59.99 1,000,000/50/10 + priority support, key limits
  Free 1 / Starter 3 / Pro 10 — all exactly the API's numbers.

### PA-30 — Remove the redirect flash on store-scoped pages
- **Status**: `complete` · **Priority**: S4 · **Gaps**: C-11 · **Depends on**: —
- **Files**: `web/landing/app/dashboard/[public_id]/webhooks/page.tsx:9`,
  `web/landing/app/dashboard/[public_id]/api-keys/page.tsx:9`
- **Action**: redirect server-side (or from the layout) instead of after paint.
- **Acceptance**: no store chrome is painted before the redirect.
- **Shipped (2026-09-23)**: both stub pages are deleted and the two URLs are entries in
  `next.config.js`'s `redirects()` next to the store-scoped *payment* URL that already
  worked this way, at the same `permanent: false` (307) the file records a reason for — a
  308 is cached hard and cannot be withdrawn if the route returns. A server component
  calling `redirect()` was tried first and does not work here: the dashboard layouts above
  these routes are client components, so the document shell streams before the page renders
  and the redirect arrives too late to be a status code — Next answers **500** with
  `NEXT_REDIRECT` embedded in the payload. Verified: both URLs answer `307` with the right
  `Location` before any body, an unrelated store route still answers `200`, and in the
  browser the first paint is already the destination with no store chrome and no
  "Redirecting…" notice. Two stale generated files under `.next/types` for the deleted
  routes had to be cleared or `tsc` failed on phantom modules.

---

## Wave 6 — Tooling and operations

### PA-31 — Stop referencing a key type that cannot exist
- **Status**: `complete` · **Priority**: S2 · **Gaps**: E-02 · **Depends on**: —
- **Files**: `src/chmabapay/tools/testplan.py:469`,
  `src/chmabapay/tools/integration_test.html:122-123,169,1092`
- **Action**: `new_api_key` mints `ck_live_` only, and decision D-2 keeps it that
  way. Remove the `ck_test_` instruction from the dev tooling so nothing points at a
  credential that cannot be created.
- **Acceptance**: `grep -r "ck_test_" src/` returns nothing outside negative test
  fixtures that assert such a key is rejected.
- **Shipped (2026-09-23)**: five edits, none of them behaviour.
  1. `testplan.py` SETUP-004's note now reads "This mints a real payment, so it spends
     one of the plan's monthly allowance — run the plan against a scratch account."
     — the caution the old text spent on the sandbox prefix instead.
  2. `integration_test.html` four edits: the key field's `placeholder` is `ck_live_…`
     with a hint saying every key is live and the suite's payments count against the
     plan; the "safe to run against" warnbox answers "none. Point it at a scratch
     account."; the `#liveWarn` line says a `ck_live_` key is in use and destructive
     cases stay blocked unless ticked; and the empty-field error asks for `ck_live_…`.
  3. `docs/data-model.md` — the `key_prefix` row states `ck_live_` + `raw[:12]`, and
     the `mode` row records `live` as the only mintable mode with the column kept for
     the Phase-4 sandbox (D-2).
  4. `docs/architecture.md` — the API-key line names `ck_live_…` and points at D-2.
  5. `docs/api.md` and `docs/production-readiness.md` already said no test prefix
     exists; left as they are — they describe the absence, which is still true.
- **Verification**: `grep -n "ck_test_" src/` → 8 hits, every one in
  `testplan.py` (634, 635, 639, 640, 644, 645, 646, 750) and every one a *negative*
  fixture asserting the key is refused: the `bad_credentials` list (`lowercase_scheme`,
  `no_scheme`, `tab_after_scheme`, `unknown_key`, `empty_key_body`, `double_space`,
  `very_long_key`) and one AUTH case whose `expect` is `401 unauthorized` for
  `POST /v1/khqr/from-link`. That is the acceptance clause exactly. `grep -rn "ck_test_"
  web/` → none, so no prompt or hint in either app still implies a sandbox.
  `tests/test_khqr.py` mentions the AUTH cases in a comment only and holds its own 401
  assertions, so the SETUP-004 text change cannot break it. `ruff check src/chmabapay
  tests` clean; `.venv\Scripts\python.exe -m pytest -q` → see PA-32.

### PA-32 — Document where the HQ collection link actually comes from
- **Status**: `complete` · **Priority**: S4 · **Gaps**: E-03 · **Depends on**: —
- **Files**: `deploy/.env.example`, `src/chmabapay/services/billing.py:204`
- **Action**: note in `deploy/.env.example` that `CHMABAPAY_HQ_STORE_ID` is
  optional and the console-resolved `ChmabaPay HQ` store wins when it is empty.
- **Acceptance**: the env file explains the resolution order.
- **Shipped (2026-09-23)**: the comment block above `CHMABAPAY_HQ_STORE_ID` and
  `CHMABAPAY_HQ_PAYWAY_LINK` now states that both are optional, that the supported way
  to set them is the admin console (**Settings → "Plan fee collection"** — creates the
  store, marks it internal, no restart), and gives the three-step resolution order read
  off `services/billing.resolve_hq_store`: (1) `CHMABAPAY_HQ_STORE_ID` (a `st_…` or a
  row id) wins over the console, which is why the console reports which source is in
  use; (2) empty → the console-resolved store, the one named **`ChmabaPay HQ`** owned by
  a platform admin, named rather than "the admin's own store" so plan fees cannot land
  in an operator's unrelated business; (3) neither → a platform admin's first active
  store, for deployments configured before the field existed. It also records that an
  unresolvable pin is not an error (the search falls through to (2)) and that with none
  of the three found, paying an invoice answers **503 `billing_not_open`**. The
  `CHMABAPAY_HQ_PAYWAY_LINK` comment notes it seeds that store on the admin's next
  sign-in and takes precedence over the console's saved link — the same kind of pin as
  the id.

---

## Wave 7 — Legal and decisions

### PA-33 — Put the legal position into the documents
- **Status**: `complete` · **Priority**: **S2** · **Gaps**: A-01, A-04, A-05, A-06, A-07 · **Depends on**: —
- **Decision D-1**: the published Terms and Privacy are held as reviewed, with no
  standalone liability cap.
- **Files**: `web/landing/app/terms/page.tsx:8-19,248-260`,
  `web/landing/app/privacy/page.tsx:10-16,241-279`,
  `docs/production-readiness.md`
- **Action**:
  1. Remove the "not reviewed by a lawyer / do not read as approval" code comments
     from Terms and Privacy, and record the D-1 position in
     `docs/production-readiness.md`.
  2. Rewrite §8's ceiling: liability is limited to the extent of the limitations
     imposed by the underlying rails (ABA PayWay and Bakong/NBC), and ChmabaPay
     assumes no liability beyond them. State it as a pass-through, not a cap we set.
  3. Add a short refund and cancellation statement for plan fees so §6 is complete
     (payer refunds stay as they are — the platform holds no funds).
  4. Add **Resend** to the privacy processor list (A-07) — a factual omission, not a
     legal opinion.
  5. Cover support-thread data in the privacy data inventory (see F-11).
- **Acceptance**: no page or code comment asserts the text is unreviewed; §8 names
  the rail limitations; §6 states the refund/cancellation position; the processor
  list includes every processor the code calls.
- **Shipped (2026-09-23)**: the "not reviewed by a lawyer" comments are gone from both
  pages; §8 carries the rail pass-through clause with no ceiling of our own; §6 carries
  the fee position (no pro-rata refunds, 30 days' notice); Resend is listed as a
  processor; the district spelling is fixed. `terms_version` moved `2 → 3` with the
  reason recorded in `config.py`, and the P1-7 section of `docs/production-readiness.md`
  records the D-1 position and what it does not settle. `pytest
  tests/test_compliance.py tests/test_config.py tests/test_audit.py` → 58 passed;
  `ruff` clean.
- **Deferred inside this task**: item 5, the support-thread data inventory (F-11),
  cannot be written until the ticketing feature creates that data — it is folded into
  Wave 9 rather than described ahead of the schema.

### PA-33b — Review the on-behalf-of position
- **Status**: `complete` · **Priority**: **S2** · **Gaps**: A-02 · **Depends on**: —
- **Files**: `docs/legal/on-behalf-of.md`, `docs/legal/merchant-agreement.md:120-129`
- **Action**: this is the one item D-1 does not settle. Either obtain the written
  NBC/Bakong position, or record explicitly that the platform accepts the risk of
  querying hosted-session status for merchant payments without it. Whichever is
  chosen, `docs/legal/on-behalf-of.md` must stop saying the question is open.
- **Acceptance**: the document states a resolution or a recorded risk acceptance,
  not an open question.
- **Shipped (2026-09-23)**: the **risk acceptance** branch, with the research behind
  it on the record. The written NBC/Bakong answer was not obtained — there is no
  route to one that does not cost weeks and a lawyer, and the operator asked for the
  position to be closed rather than left hanging, so the document now says exactly
  what is being accepted and when that stops being acceptable.
  `docs/legal/on-behalf-of.md` was rewritten from "Status: OPEN" to "Status: RISK
  ACCEPTED"; the old text was kept where it was still true (the architecture already
  designs the question out for the ABA path; the list of questions to put to the NBC
  survives as the letter to send when there is a reason to send it).
- **What the research found**, recorded as five questions rather than one, because
  they have different answers:
  1. **Polling is documented and third-party software is an expected reader.** The
     NBC's own *QR Payment Integration* (KHQR) document, v1.0.3 of 06.08.2021,
     names its audience as "third-party technical, POS service provider, software
     developers", and puts the MD5 status poll in the merchant's back end. What it
     never addresses is whether one integrator's credential may serve many
     merchants.
  2. **A Bakong credential does not scope to one merchant, mechanically.**
     Registration is a single developer email → `POST /v1/renew_token` → a bearer
     token, and `/v1/check_transaction_by_md5` takes the MD5 of any QR. So the API
     does not forbid the pattern; it does not distinguish one merchant from another
     at all. Recorded plainly: absence of a prohibition is not permission.
  3. **Whether a licence is needed at all is open, and that is the bigger question.**
     Two routes exist in the secondary literature — a PSI under the 1999 Banking Law
     and the 2017 Prakas, or a third-party processor under Prakas B9-010-151 as
     amended by Prakas B7-019-420. We hold nothing and settle nothing, which is the
     widely-recognised "technology layer" shape, but we do generate a QR, poll its
     status and report it, and `docs/legal/merchant-agreement.md` already asks
     whether reporting makes us a party. Unanswered, and named as unanswered.
  4. **Data protection has no omnibus regime yet**, so nothing bites today. Two
     conditions would change that: enactment of the draft PDP Law's Articles 22–24
     as circulated (a cross-border transfer prohibition and a localisation mandate),
     or the platform becoming NBC-licensed, which would pull in the Technology Risk
     Management Guidelines. Both are recorded as watch items, not as current risk.
  5. **An independent operational blocker.** Community SDK maintainers report HTTP
     403 from Bakong's API for hosts outside Cambodia, to the point that a paid
     relay service exists to work around it. So the Bakong path may stay unusable
     even on a favourable legal answer.
- **Position adopted**: proceed on the ABA hosted-checkout path, which requires no
  on-behalf-of credential; treat the Bakong developer token as blocked. Four risks
  accepted with their intolerable-conditions named (R1–R4 in the document), and one
  thing **explicitly not accepted**: acting as a payment institution without a
  licence. If the model ever holds, nets, settles or aggregates merchant funds, or
  charges payers, the document says the question goes to the NBC *before* that change
  ships.
- **Not done, and honestly so**: no lawyer has seen this, and the document says so in
  its own header. Every source is labelled primary (the NBC's own documents) or
  secondary (law-firm and community material), because the difference between "the
  NBC published this" and "a consultancy website says this" is the point. The one
  claim worth re-checking before anyone relies on it: the 2026-09-15 note that the
  NBC declined to register the developer email, which is why `verify_receipt` answers
  `401` — it may be a matter of how the request was made rather than a policy, and
  the document says so.

---

## Wave 8 — Verification and release

### PA-34 — Full verification pass
- **Status**: `complete` · **Priority**: **S1** · **Depends on**: PA-01…PA-33 (shipped subset)
- **Action**:
  1. `ruff check src/chmabapay` → exit 0.
  2. `pytest` against Postgres 16 + Redis 7 per the documented invocation in
     `docs/production-readiness.md`, plus `alembic upgrade head` and `alembic check`.
  3. `docker build -f web/Dockerfile --build-arg APP=<landing|admin>` for both apps.
  4. Live browser pass: create a webhook end to end (PA-01), then delete it; confirm
     no console errors on the public pages and no 4xx/5xx from the portal.
  5. Read-only production re-probe of every route touched.
- **Acceptance**: the launch gate in `spec.md` is satisfied and the evidence is
  recorded in the task.
- **Shipped (2026-09-23)** — every step, with its evidence.
  1. `ruff check src/chmabapay` → **exit 0**. (`ruff check src/chmabapay tests` → all
     checks passed.)
  2. Migrations, against a **fresh empty `chmabapay_migrate` database on the
     `postgres:16-alpine` service in this repo's compose stack** (host port 55432), not
     against a database that already had a schema: `alembic upgrade head` → exit 0,
     leaving `alembic_version = 0012` and **14 model tables**. `alembic check` → "No new
     upgrade operations detected.", exit 0 — so the revisions are the complete and only
     path to the models, with no drift.
     `pytest` with `CHMABAPAY_TEST_DATABASE_URL` on a fresh `chmabapay_test` and
     `CHMABAPAY_TEST_REDIS_URL` on the `redis:7-alpine` service (DB 15) → **372 passed,
     2 deselected, in 599s, exit 0**. The two deselected are the `live` ABA tests that
     `addopts` always excludes. (The same suite also passes on its default SQLite
     fallback, which is the slower run and not the one the gate names.)
  3. `docker build -f web/Dockerfile --build-arg APP=landing` and `--build-arg APP=admin`,
     both with `--build-arg NEXT_PUBLIC_API_URL=http://api:8000` to match
     `docker-compose.yml`, tagged `chmabapay-landing:verify` / `chmabapay-admin:verify`
     so the running containers' images were untouched → **both exit 0**. Run three times in
     all, because the source kept moving under it: the first pair predated the PA-01 and
     hydration fixes, and the landing image was rebuilt a third time after OP-01's city
     field. The final landing and admin builds both cover the current source.
  4. Live browser pass — see PA-01's "Verified live" block, and the two findings below.
     Full lifecycle green: create → 201 → show-once secret modal → row rendered → delete
     → empty state, with **no `/v1/*` request answering 4xx or 5xx** and no app-level
     console error on `/`, `/api/docs`, `/terms`, `/privacy` or `/dashboard/webhooks`.
  5. Read-only production re-probe, `GET` only, against `https://pay.chmaba.com`:

     | route | status | destination |
     | --- | --- | --- |
     | `/`, `/api/docs`, `/terms`, `/privacy`, `/contact`, `/health` | 200 | — |
     | `/login` | 307 | `/auth/google/login` |
     | `/pricing` | 307 | `/#plans` |
     | `/dashboard/st_probe/payments/pay_probe` | 307 | `/dashboard/payments/pay_probe` |
     | `/dashboard/st_probe/settings` | 200 | — |
     | `/dashboard/st_probe/webhooks` | 200 | *expected 307 → PA-30, unreleased* |
     | `/dashboard/st_probe/api-keys` | 200 | *expected 307 → PA-30, unreleased* |
     | `https://admin-pay.chmaba.com/` | 200 | — |

     The two 200s are not a regression: production serves the released bundle, which
     predates PA-30's router redirects, and the `/login`, `/pricing` and payment-route
     307s prove the mechanism itself is live in production. The PA-30 routes can only be
     re-probed after release.
- **Two findings from step 4, both fixed inside this task.**
  - **PA-01 was not actually shipped.** The create form still posted `enabled` and the
    422 reproduced exactly. Fixed, and the regression test rewritten so it reads the form
    rather than a literal — see PA-01's Correction block. This is the single most
    important result of the pass: the gate's first criterion was still open while the
    task claimed otherwise.
  - **A React hydration mismatch on every dashboard route.** `layout.tsx` renders
    `<body className="font-sans landing-english landing-body">` and the inline script
    immediately below adds `dash-hide-landing-chrome dash-path-dashboard`, so the DOM
    React hydrated into never matched what it had rendered — "Prop `className` did not
    match" on every dashboard load. `suppressHydrationWarning` is on the body element
    now, the documented opt-out for a deliberate pre-hydration difference; it applies one
    level deep, so a real mismatch elsewhere still reports. Confirmed gone live, on a
    hard reload, with the class list still applied as before.
- **The launch gate in `spec.md`: four of five criteria are satisfied, one is not.**
  1. **C-01** — closed and verified live in a browser. ✅
  2. **PA-33** — Terms and Privacy match D-1; §8 states the rail pass-through, §6 the fee
     position. ✅
  3. **B-01 and B-02 closed** — PA-04's ten Bakong ledger lookups and PA-05's two
     non-payable KHQR helpers are off the public page, both re-verified by `grep` against
     the page source. ✅ · **A-08 is NOT closed.** Pro still advertises priority support
     (`priority_support: True` in `db.py` for Pro, served by `/v1/billing/plans` and shown
     on the plan matrix) and Wave 9 does not exist, so nothing tracks the promise. The
     gate allows either closing it by shipping Wave 9 or by withholding the claim until
     it ships. **This is a decision for the operator, not a task** — until it is taken,
     the launch gate is open on this criterion alone. ❌
  4. **D-02** — closed. `POST /v1/admin/payments/{public_id}/reverse` exists in
     `admin.py`, calls the same `reverse_payment` service as the merchant route with the
     same `409` guards, and records `admin.payment_reversed` with the operator and a
     required reason. ✅
  5. **The verification protocol** — `ruff`, the pytest suite against Postgres, both
     `next build`s and the read-only production re-probe: all four pass, above. ✅
- **Not verified by this pass, and why.** The on-behalf-of position (PA-33b) is a legal
  decision, not a check. Wave 9 (PA-36…PA-43) does not exist to verify. PA-30's two
  redirects cannot be re-probed in production until the build is released. No live money
  path was exercised: the `live` ABA tests are opt-in and were not opted into, and no
  payment was created against the real rail during this pass.

### PA-35 — Update the readiness record
- **Status**: `complete` · **Priority**: S3 · **Depends on**: PA-34
- **Files**: `docs/production-readiness.md`
- **Action**: add a P1-7 section recording this audit, what it closed, what was
  deferred and why (including D-2's live-only sandbox and the Wave 9 deferral).
- **Acceptance**: the doc reflects the shipped state and the open decisions.
- **Shipped (2026-09-23)**: P1-7 already existed and was **wrong in three places**, which
  is the thing worth recording.
  1. It stated the webhook `enabled` bug was "Fixed, and pinned by
     `test_a_webhook_create_accepts_the_dashboard_payload`". Neither half was true — the
     fix was not in the file and that test cannot detect its absence. Rewritten with the
     correction, the reason the test was not a guard, and the lesson: *a test that
     restates a payload is not a guard on the code that sends it*.
  2. It listed D-02 ("no admin refund path, PA-16") as still open. The route has existed
     since PA-16; moved to a "closed after the findings above" block, with the other
     operator tools and the D-6/D-7/D-8 hardening beside it.
  3. "Where we are" quoted a stale suite count — "184 selected, 2 `live` deselected" —
     and an unverified "~35 min → ~2.5 min" claim for the SQLite-to-Postgres switch.
     Now **372 selected** and the figures actually measured on 2026-09-23 (~28 min on
     SQLite, ~10 min on Postgres locally), with the reason the suite is slow written
     down: every test drops and recreates the schema because each gets its own event
     loop.
  Added: the PA-34 verification evidence, and an explicit **launch gate: four of five
  criteria met** statement naming A-08 as the one open criterion and framing it as the
  operator's decision rather than a task.
  The deferrals D-1 does not settle (on-behalf-of, no sandbox per D-2, Wave 9 ticketing)
  were already recorded correctly and were left as they are.

---

## Wave 9 — Support ticketing (decision D-4)

The only net-new feature in this plan. Requirements are §F of `spec.md`.
This wave is **not launch-blocking** — it exists so the Pro "priority support"
claim becomes true. Until it ships, PA-06's copy change must not promise a target
that no system tracks.

### PA-36 — Support models and migration
- **Status**: `complete` · **Priority**: S2 · **Gaps**: F-01, F-02 · **Depends on**: —
- **Files**: `src/chmabapay/models.py`, `alembic/versions/`, `src/chmabapay/schemas.py`
- **Action**: add `SupportRequest` and `SupportMessage` exactly as specified in F-01
  and F-02, following the existing `public_id` conventions (`sup_…`). One Alembic
  revision after the current head. The opening message is stored as message #1.
- **Acceptance**: `alembic upgrade head` then `alembic check` reports no drift; a
  test creates a request with its first message and reads both back.

### PA-37 — Merchant support API
- **Status**: `complete` · **Priority**: S2 · **Gaps**: F-03 · **Depends on**: PA-36
- **Files**: `src/chmabapay/routers/` (new `support.py`), `src/chmabapay/main.py`
- **Action**: the five merchant routes from F-03, every one scoped to
  `ctx.account.id` the way `payments.py` and `webhooks.py` are. A request belonging
  to another account is a 404, never another tenant's thread.
- **Acceptance**: pytest — open, list, read, reply, close; a cross-account read by
  `public_id` returns 404; a `restricted` account can read but not open (consistent
  with the existing freeze rules).

### PA-38 — Operator support API
- **Status**: `complete` · **Priority**: S2 · **Gaps**: F-04 · **Depends on**: PA-36
- **Files**: `src/chmabapay/routers/admin.py`, `src/chmabapay/audit.py`
- **Action**: the queue and detail routes from F-04 behind the existing
  `get_hybrid_admin_context`, with paging via `_clamp_paging`. Every operator write
  (reply, status change, assignment) writes an audit row with the admin account id.
- **Acceptance**: pytest — queue filters by status and priority; paging clamps;
  reply/status/assign each produce an audit row; an unauthenticated call is 401.

### PA-39 — Priority derivation and first-response tracking
- **Status**: `complete` · **Priority**: S2 · **Gaps**: F-05, F-06 · **Depends on**: PA-36
- **Files**: `src/chmabapay/services/` (new `support.py`), `src/chmabapay/routers/support.py`
- **Action**: set `priority` from the account's active plan `priority_support` at
  open time; sort the operator queue priority-first then oldest-unanswered; set
  `first_response_at` on the first *operator* reply only, and never overwrite it.
- **Acceptance**: pytest — a Pro account's request opens as `priority` and sorts
  above a Free account's older request; a merchant reply does not set
  `first_response_at`; a second operator reply does not move it.

### PA-40 — Merchant portal support page
- **Status**: `complete` · **Priority**: S2 · **Gaps**: F-07 · **Depends on**: PA-37, PA-39
- **Files**: `web/landing/app/dashboard/support/page.tsx`,
  `web/landing/components/portal/DashboardShell.tsx`, `web/shared/`
- **Action**: open a request, list own requests, read the thread, reply, close, and
  state the account's response-time target inline. Follow the existing portal
  patterns: explicit loading / empty / error states with retry (never a silent
  empty), and the shared styling conventions — no inline React styles.
- **Acceptance**: live browser pass — open a request, see it listed, reply, close;
  a forced failure shows an error state with retry, not an empty list.

### PA-41 — Admin console support queue
- **Status**: `complete` · **Priority**: S2 · **Gaps**: F-08 · **Depends on**: PA-38, PA-39
- **Files**: `web/admin/app/support/page.tsx`,
  `web/admin/app/support/[public_id]/page.tsx`, `web/admin/components/AdminShell.tsx`
- **Action**: the queue (status/priority filters, paging) and detail (thread, reply,
  status, assignment). Requests past the first-response target are highlighted.
  Closing a request uses a confirmation modal that echoes the request id and
  subject, per the existing destructive-action convention.
- **Acceptance**: live browser pass as an operator — take the oldest priority
  request, reply, assign, close; the target breach is visible before reply.

### PA-42 — Support notifications
- **Status**: `complete` · **Priority**: S3 · **Gaps**: F-09 · **Depends on**: PA-37, PA-38
- **Files**: `src/chmabapay/services/resend.py`, `src/chmabapay/services/telegram.py`,
  `src/chmabapay/services/support.py`
- **Action**: email the merchant when an operator replies (reuse the existing Resend
  path and its from-address), and alert the operator channel when a `priority`
  request is opened. Notification failure must not fail the reply.
- **Acceptance**: pytest with the mailer stubbed — a reply sends one email; a
  priority open fires one alert; a mailer exception leaves the reply committed.

### PA-43 — Verification for Wave 9
- **Status**: `complete` · **Priority**: S2 · **Depends on**: PA-36…PA-42
- **Action**: `ruff`, the pytest suite against Postgres, both `next build`s, and a
  live browser pass across portal and console. Re-run the PA-34 checks so the suite
  covers the new code.
- **Acceptance**: same bar as PA-34, plus the F-01…F-11 acceptance criteria above.
- **Shipped (2026-09-23)** — Wave 9, what landed where.
  - **PA-36 / F-01, F-02.** `SupportRequest` and `SupportMessage` in `models.py`, with the
    statuses (`open`/`pending`/`resolved`), priorities (`standard`/`priority`), the two
    author kinds and the five categories as model constants. Migration
    `0013_support_tickets.py` creates both tables; `alembic upgrade head` onto a fresh
    Postgres 16 database and `alembic check` both exit 0 with no drift.
    Two deliberate shapes: **the opening message is message #1**, not a `body` column on the
    header, so a thread has one order and one shape and no reader special-cases the first
    message; and **`priority` is copied from the plan at open time** rather than derived on
    read, so an account that downgrades later keeps the queue position it was promised.
  - **PA-37 / F-03.** `routers/support.py`, five routes under `/v1/support`. Every lookup
    goes through `_load_request`, which scopes on `ctx.account.id` and answers **404** for
    another tenant's request. Verified independently: all five routes carry the scope and
    the 404 is raised in one place.
  - **PA-38 / F-04.** The operator queue in `routers/admin.py` behind the existing
    `get_hybrid_admin_context`, paging via `_clamp_paging`, with `support.replied`,
    `support.status_changed` and `support.assigned` audit rows. A no-op PATCH writes no
    audit row — the same rule `webhooks` already follows.
  - **PA-39 / F-05, F-06.** `services/support.py` holds the rules both routers share:
    `priority_for_account`, `response_target_hours`, `target_breached`, the queue ordering,
    and `record_operator_reply`, which **sets `first_response_at` only if it is None**. A
    merchant reply cannot set it, and a second operator reply cannot move it — both pinned
    by tests. The queue orders high-priority first via a `case` rank, not by the literal
    string, because `"priority" < "standard"` alphabetically and ordering on the text would
    have sorted the Pro requests *last*.
  - **PA-40 / F-07.** `/dashboard/support`, a Support nav entry, and a link from the help
    FAQ's "what support is available?" answer, which previously promised a channel that did
    not exist. The response-time target is rendered from the API's own number in all three
    places it appears.
  - **PA-41 / F-08.** `/support` and `/support/[public_id]` in the console, with status and
    priority filters, paging, a breach flag carried by **words as well as colour**, and a
    close confirmation echoing the request id and subject.
  - **PA-42 / F-09.** A merchant email on operator reply through `services/resend.py`, and
    an operator alert over the existing activity channel when a `priority` request opens.
    The reply is committed **before** the email is attempted and a mailer failure is logged
    rather than raised, so the thread — which is the record — cannot be lost to a courtesy
    notification. No new channel was added.
  - **F-10 / PA-06.** `/contact` now states Pro's 24-hour calendar target and that Free and
    Starter are best-effort with no target, and no longer says "support is email only" now
    that a support page exists. One number lives in one place
    (`services.support.PRIORITY_RESPONSE_TARGET_HOURS`); the portal and the console read it
    from the API rather than repeating it.
  - **F-11.** The privacy policy's data inventory has an "About support requests" section:
    the subject, category and every message, the administrative record, and the reply
    notification. `terms_version` is **not** bumped, and deliberately: what a merchant
    accepts is the Terms, not the Privacy Policy, so an additive privacy disclosure is not
    a re-acceptance event. The existing §4 retention wording ("everything else: for as long
    as your account exists") already covers these rows, and no new sweep was written.
- **One change made after the first pass, because the portal could not read the target it
  was required to state.** `response_target_hours` was published only on each request, so a
  Pro account that had never opened one had nothing to read it from and would have shown
  the best-effort copy — the platform's own promise invisible to exactly the merchants who
  had not needed it yet. It is now also on the list response (`SupportRequestListOut`), so
  it is readable with zero requests. Found by the portal implementer, not by a test.
- **Known residual, recorded rather than papered over**: the console's assignment control is
  a numeric admin-account-id input, because **no endpoint lists platform admins** — there
  is nothing to populate a picker with. It is functional and says so (the current assignee
  links to `/accounts/{id}`), but it is the weakest control on the page. Building an admin
  directory is a new surface and was not in scope for this wave.
- **Verification**: `ruff check src/chmabapay tests` → exit 0. `pytest tests/test_support.py`
  → **15 passed**. The **full suite against Postgres 16 + Redis 7 → 387 passed, 2
  `live` deselected, exit 0** (651s), which covers the new code and the changed list
  response. `pnpm --filter @chmabapay/landing build` and `pnpm --filter admin build` → both
  exit 0, with `/dashboard/support`, `/support` and `/support/[public_id]` emitted as routes.
- **Verified live — merchant portal (F-07)**: signed in, accepted the Terms gate, opened
  `/dashboard/support`. Heading `Support`, subtitle "Open a request, follow our replies and
  close the thread"; the Support nav entry sits between Settings and Help. The target banner
  read **"We answer every request as soon as we can. Your plan does not state a first-response
  time."** — the correct copy for an account whose plan publishes `response_target_hours:
  null`, which is the proof it is read from the API and not hardcoded. Opened a request
  (subject/category/message, five categories offered) → it appeared as row
  `sup_kTfynPbzTlAIq9x6xdGxLy4f` with `Waiting on: Platform`; the thread showed the message
  attributed to `You` with a timestamp; `Close request` asked **"Close this request?"** and
  explained the consequence before confirming; afterwards the row read `RESOLVED`, `Waiting
  on: —`, and the thread became read-only with no reply box. **No console errors and no
  `/v1/*` 4xx or 5xx.**
- **Verified live — operator console (F-08)**: signed in at `/login` with a password (two
  fields, no Google button), then `/support`. Columns `Request · Account · Subject · Category
  · Status · Priority · Opened · First response`, with status / priority / account-id filters
  and a `Clear` that appears only when a filter is active. The breached row carried the
  words **`BREACH`** and **"Past the 24h target, still unanswered"** in the list *before*
  anything was opened, and sorted above the resolved one — so the signal is text, not colour.
  Status and priority filters each narrowed to the correct single row server-side. The detail
  page raised the same breach as a `role="alert"` banner. Replying moved `First response`
  from the breach flag to a concrete timestamp and moved status `open` → `waiting`, which is
  F-06 working. Assigning `11` produced `Account #11` linking to `/accounts/11`. Closing
  showed a dialog that **echoed both the request id and its subject** in the body before
  confirming. **No `/v1/*` 4xx or 5xx.**
- **One gap the live pass found, and it was mine**: the console's navigation had no Support
  entry, so `/support` was reachable only by typing the URL — the shell had the section label
  for the breadcrumb but the nav list was never given the item. Added, with its own icon;
  `tsc` and the admin build are both green again. Worth recording because it is the class of
  thing a green typecheck and a green build cannot see: the page existed, built, and was
  unreachable.
- **A false positive the live pass also produced**, recorded so nobody "fixes" it: the operator
  reported no pagination on the queue. There is pagination — it renders only when
  `total_pages > 1`, exactly as `accounts/page.tsx` does, and the seeded queue had two rows.
  Not a defect; the check was.
- **An environment lesson, since it cost a verification run.** Running `next build` for the
  landing app while its `next dev` server was live left `web/landing/.next` holding a hashed
  production build beside a half-written dev build. The dev server then answered 500 for its
  own chunks and no dashboard route hydrated — a page that had already been verified once
  looked completely broken. The fix is to stop the dev server before building, or to run the
  build in a separate checkout. Nothing about the app was wrong; the build directory was.
- **The gate line moved**: with A-08 closed by this wave, `spec.md`'s launch gate is now five
  of five rather than four of five. PA-34's block above still records the state at the time it
  ran, deliberately — it is a record of what was true then, not a live dashboard.

---

## Wave 10 — API surface scope and presentation (decisions D-7…D-10)

Raised by the operator while reviewing the rebuilt reference: the page was advertising
the platform's own dashboard surface as if it were integration surface, and one of those
groups was reachable with an API key.

### PA-44 — Make key management session-only
- **Status**: `complete` · **Priority**: **S2** · **Gaps**: D-8 · **Depends on**: —
- **Problem**: `/v1/keys` used `AUTH_SECURITY`, so a merchant's API key could create,
  revoke and rotate keys. A leaked key could therefore mint itself a replacement and
  outlive its own revocation, and could revoke every other key on the account — locking
  the merchant out of the automation the key was stolen from. `/v1/me`, `/v1/account`
  and `/v1/billing/*` were already session-only for the same class of reason.
- **Files**: `src/chmabapay/routers/keys.py`, `tests/conftest.py`,
  `tests/test_account_security.py`, `tests/test_audit.py`
- **Action**: switch the router to `SESSION_SECURITY` and the handlers to
  `get_current_session_account`; add a test asserting an API key is refused; update the
  tests that minted keys with a key.
- **Acceptance**: an API key gets `401 invalid_session` on every `/v1/keys` route, and
  key creation still works from a session.
- **Shipped (2026-09-23)**: router and handlers converted. `session_client` moved into
  `conftest.py` rather than being re-derived per file, which is what the change needed —
  three test files now drive session-only routes. New test
  `test_an_api_key_cannot_manage_api_keys` asserts 401 `invalid_session` on list, create,
  revoke and rotate, and that the same key still authenticates `/v1/stores`, so the
  change is scoped to key management and not to the credential.
- **Note**: the refusal is `401 invalid_session`, not 403. That is the ordinary
  missing-session answer the account and billing routes already give, rather than a
  special case for keys.

### PA-45 — Take the dashboard surface off the public reference
- **Status**: `complete` · **Priority**: S2 · **Gaps**: D-7 · **Depends on**: PA-44
- **Problem**: the page listed **API Keys**, **Billing & Plans** and **Account** beside
  the integration API. Billing and Account are session-only and Billing is how we charge
  the merchant — none of it is something an integrator calls with a key. Listing it
  inflated the perceived API and invited questions about a surface they cannot use.
- **Files**: `web/landing/app/api/docs/page.tsx`, `reference.ts`, `docs/api.md`,
  `tests/test_openapi_schema.py`, `tests/test_api_docs_errors.py`
- **Action**: remove the three groups; move their content into `docs/api.md`; record the
  paths and codes as withheld; reword the Authentication card.
- **Acceptance**: the page documents only the key-authenticated integration surface plus
  the public hosted-checkout pages; both drift tests pass with the dashboard paths
  recorded as withheld.
- **Shipped (2026-09-23)**: the page is down to **7 endpoint groups** (Stores, Payments,
  Payment Reconciliation, KHQR Generation, Webhooks, Reports & Reconciliation, Public
  Hosted Checkout) from 11 — three removed here and one earlier by PA-04 — and 9 error
  groups from 11. The Authentication card now says where to create a key (the dashboard)
  and states that key management, billing and the profile are deliberately not part of
  this API, which is the guidance the removed API Keys group used to carry.
  `DASHBOARD_PATHS` in `test_openapi_schema.py` and the matching block in
  `NOT_IN_REFERENCE` name all fourteen routes and twenty codes, so the omission is a
  decision. `docs/api.md` gained a **Two surfaces, and why they are separate** section
  carrying the dashboard endpoints and their refusals, and its header no longer claims
  they are documented on the public page.
- **Note**: the hero still shows the base URL and `Authorization` in dark chips. They are
  not code blocks and were left alone, but they are now the only dark elements on the
  page — worth a look if the light treatment is the direction.

### PA-46 — Restyle the code blocks
- **Status**: `complete` · **Priority**: S3 · **Gaps**: D-10 · **Depends on**: —
- **Problem**: `.docs-signature-code` was `#111217` near-black, so the "six copy-paste
  steps" read as terminal output rather than as code a reader is meant to copy.
- **Files**: `web/landing/app/api/docs/CodeBlock.tsx` (new), `globals.css`, `page.tsx`
- **Action**: a light panel with a header carrying the title and a Copy button, applied
  to every code panel on the page.
- **Acceptance**: no code panel is dark; every panel has a working Copy button; copying
  yields the exact snippet including newlines.
- **Shipped (2026-09-23)**: `CodeBlock` is a small client component — the only one on the
  page — with `CopyButton` beside it. Twelve panels render (six quick-start steps, request
  headers, the 429 response, two webhook snippets, two convention examples), all light
  `#fbfbfd` on a `#e9e9ef` border with a `#f5f6fa` header. The dark rules
  (`.docs-signature-code`, `.docs-ref-list pre`) were deleted rather than left unused, and
  the two spacing rules they provided moved onto the new block. A refused clipboard write
  says **Copy failed** rather than appearing to succeed.
- **Not verified here**: the Copy button's behaviour. The local dev server serves stale
  client chunks that 404, so the page never hydrates — the Copy handler is client-side and
  could not run. Structure, styling and the absence of the dark class are verified in the
  served HTML and CSS; the click is a PA-34 check after a real build.

### PA-47 — Give the Quick-start steps distinct icons
- **Status**: `complete` · **Priority**: S4 · **Gaps**: D-9 · **Depends on**: —
- **Problem**: all four Quick-start cards used `.landing-feature-icon-setup`, so four
  different steps carried the same glyph.
- **Files**: `web/landing/app/api/docs/page.tsx`, `globals.css`
- **Action**: keep four steps, one icon each; add the missing variant.
- **Acceptance**: four different icon classes render, one per step, and none is repeated.
- **Shipped (2026-09-23)**: added `.landing-feature-icon-link` (two joined rings, teal) so
  the four are `setup`, `link`, `payment`, `insight` — target, connection, card, chart.
  Verified in the served HTML: four distinct classes, in step order.
- **Note**: four steps, not three or six. They map 1:1 to what a merchant must do, and the
  fourth — confirm from the webhook — is the one integrators get wrong, so it is the last
  candidate for merging away.

### PA-48 — Rebuild the Quick-start section as a timeline
- **Status**: `complete` · **Priority**: S4 · **Gaps**: D-11 · **Depends on**: PA-46, PA-47
- **Problem**: PA-46 lightened the code panels but not their shells. `.docs-qs-block` was
  `#0c0d10` and `.docs-signature-panel` `#0f1015`, so every card held a light snippet inside
  a black frame — the mismatch the operator spotted. Separately, a two-column grid made a
  six-step sequence read left-right-left-right, and the header was a plain heading with a
  paragraph under it.
- **Files**: `web/landing/app/globals.css`, `web/landing/app/api/docs/page.tsx`
- **Action**: replace the card grid with a numbered vertical timeline; rebuild the header as
  an eyebrow, title and meta chips; light every remaining dark shell.
- **Acceptance**: no dark shells remain on the page; the six steps read top-to-bottom in
  order; the header carries an eyebrow and chips.
- **Shipped (2026-09-23)**: `.docs-qs-steps` is an ordered list with a violet gradient rail
  down the left and a 32px numbered node per step, the snippet hanging off it in the step's
  own light `CodeBlock`. The header is `.docs-qs-eyebrow` + `.docs-qs-heading` +
  `.docs-qs-lede` + three chips (Copy-paste ready · curl and Node.js · No SDK required —
  all facts about the page, no time estimate invented). `.docs-qs-block`, `.docs-qs-grid`,
  `.docs-qs-num`, `.docs-qs-title` and the two grid media queries were deleted, and
  `.docs-signature-panel` was lightened so it is no longer the last black frame. Two latent
  faults fixed while in there: `.docs-code-pre` had `overflow-x` only, so the `max-height`
  on long snippets would have spilled rather than scrolled; and the Quick-start
  `min-height` existed purely to even out the two-column grid, so it was removed with it.
  Verified in the served HTML: six step nodes, three chips, twelve code panels, and none of
  the removed classes present. `tsc` clean, the drift suites pass, `ruff` clean.
- **Note**: the hero's base-URL and `Authorization` chips are still dark and were left
  alone — they are inline value pills, not code panels, and were outside what was asked.
- **Revised (2026-09-23, same day)**: the operator rejected the numbering — "don't have to
  mention 1 or 2 like that". The rail, the `.docs-qs-step-mark` node and the step's separate
  `h4` are gone; each step is now one card (`CodeBlock` with `className="docs-quick-step"`)
  whose header bar carries the step title, with the language moved to a small uppercase tag
  beside Copy. `langTag` is opt-in on `CodeBlock` for exactly this reason: a panel whose title
  already names the language ("Example: Node.js", "Request headers") would only repeat itself,
  so the signature column is untouched. `num` was dropped from the panel data — it existed to
  be printed. Verified in the served HTML: six cards, exactly six `docs-code-lang` tags across
  twelve header bars, no `docs-qs-step*` or `docs-qs-code` references left in either app's
  source. `tsc` clean.

---

## Outside the plan — items found while working, not in any wave

### OP-01 — Ask a new store for its city
- **Status**: `complete` · **Found**: during Wave 5 · **Flagged to the operator, never
  numbered**
- **Problem**: `StoreCreate.city` defaults to `"Phnom Penh"` and the portal's "New store"
  form never asked, so **every** store created through the portal claimed to be in Phnom
  Penh — including the ones that are not. The settings page then required a non-blank city,
  so the wrong value was also the one a merchant had to notice and correct by hand. `city`
  is not decoration: it is in the stores CSV the reports page generates from
  `GET /v1/stores?limit=500` and downloads as `chmabapay-stores-<date>.csv`, and it is named
  in the privacy policy's data inventory.
- **Files**: `web/landing/app/dashboard/stores/new/page.tsx`
- **Fix**: a required `City` field between Store name and Merchant ID, `maxLength={15}` to
  match the schema's own bound, sending `city` on create. The error is held back until the
  field has been visited (`cityTouched`, set on blur), because the create form starts empty
  and an error under an untouched required input on first paint is noise rather than help.
- **Verified live (2026-09-23)**: created "PA34 Siem Reap Store" with city "Siem Reap"
  through the form. `POST /v1/stores` → **201**, and the value was confirmed two ways that
  do not share a code path: the generated `chmabapay-stores-20260923.csv` reads
  `st_rNN_A5JVDfbnkzFRw3ldyHCd,PA34 Siem Reap Store,active,Siem Reap,…`, and
  `GET /v1/stores?limit=500` returns `city: "Siem Reap"`. Also checked: the submit button is
  disabled while the form is blank, no error shows before the field is visited, and the
  error "A store needs a city." appears once a real `focusout` is delivered. No `/v1/*`
  request answered 4xx or 5xx. `tsc` clean.

### OP-02 — 57 dead `docs-*` CSS rules in the admin console
- **Status**: `complete`
- **Problem**: `web/admin/app/globals.css` carried the API-docs stylesheet from the
  landing app — `.docs-hero`, `.docs-endpoint-*`, `.docs-method-*`, `.docs-signature-*`,
  the legacy `.docs-qs-*` set, and four rules inside two media queries. `grep -rn
  "docs-" web/admin --glob '*.tsx'` returned nothing: no console component used any of
  them. Cosmetic bloat, no user impact, which is why it sat unnumbered.
- **Removed**: two contiguous blocks — 1367–1661 (`.docs-hero` through the closing brace
  of the `@media (max-width: 768px)` rule) and 1829–1932 (the `/* API Docs — Quick Start
  panels */` header through the `.docs-qs-code …::selection` rule). 401 lines, in the
  tail of the shared stylesheet after the landing rules and before the dashboard rules.
- **Method, because it matters for a deletion this size in a 4,770-line shared
  stylesheet**: a line-range cut anchored on exact line content, not a hand transcription
  of ~400 lines. Guards asserted both seams before the cut (the rule above each block and
  the rule below it), then the result was checked five ways — `grep -c "docs-"` → **0**;
  brace count 696 open / 696 close; `git diff --numstat` → **0 insertions, 401
  deletions**, which is the proof that no line ending or encoding was rewritten along the
  way (the file is CRLF and the first naive attempt matched nothing because of it); both
  seams read back correctly in place; and `pnpm --filter @chmabapay/admin build` → exit 0.
- **A note for next time**: the guard used PowerShell `throw`, which is non-terminating at
  the top level of a session — execution continued past it and the write still happened.
  The guards were right and the output was verified correct, but a `throw` that does not
  stop the script is not a guard. Any future destructive edit of this shape should run the
  check and the write in separate steps, or use `exit 1`.

### OP-03 — `/.well-known/security.txt` is a 404
- **Status**: `complete`
- **Problem**: the path a security researcher is told to check did not exist. PA-09's
  acceptance covered the canonical and `robots.txt` only, so A-16's second half was never
  actioned.
- **Shipped**: `web/landing/public/.well-known/security.txt` — `Contact`, `Expires`
  (2027-09-23), `Preferred-Languages: en, km`, `Canonical`.
- **Two deliberate choices, both recorded in the file itself.**
  1. **The contact is `duke@chmaba.com`, not `security@chmaba.com`.** The role address is
     the right long-term answer and the file carries an `OPERATOR:` note to switch to it —
     but no such mailbox exists today, and a security contact that silently swallows
     reports is worse than no `security.txt` at all, because a researcher who gets no reply
     cannot tell a quiet inbox from a decision to ignore them. `duke@chmaba.com` is the one
     address this deployment is known to receive mail at (`CHMABAPAY_ADMIN_EMAILS`,
     `BILLING_EMAIL_FROM` is `billing@chmaba.com`).
  2. **No `Policy:` line.** That field is for a published vulnerability-disclosure policy
     and none exists; pointing it at `/terms` would claim a commitment the platform has not
     made. Adding it is the operator's call, and it is the natural companion to the role
     address.

### OP-04 — There is no database backup
- **Status**: `complete` — the job is built, **installed on the host, and proven by a real
  cron fire**; only the off-host destination is outstanding · **Found**: 2026-09-24, while
  answering the question "can we announce our launch and accept customers?"
- **Problem**: the audit's five launch-gate criteria were all met, and none of them asked
  this. `grep -rn "pg_dump\|backup\|cron\|systemd" docs/` finds exactly two dumps —
  `pg_dump -Fc > /root/chmabapay-pre-0008.dump` (deploy.md §12) and
  `pg_dump -Fc > /root/chmabapay-pre-0010.dump` (production-readiness.md §P1-5) — both taken
  by hand before a destructive migration, both left in `/root`, on the **same disk as the
  database they came from**. (A third deploy, `0011`–`0013`, is recorded as taking *none*.)
  So the only mechanism that existed required a person to
  remember, protected one point in time, and did not survive the failure it was protecting
  against. The production database is a single Docker volume; losing it loses every account,
  store, API key and payment the platform has ever recorded.
- **Why this is a launch blocker and not polish**: with no merchant, a lost database costs a
  re-seed. With merchant #1's real money in it, it costs the merchant's transaction history,
  and there is nothing to restore from.
- **Shipped**: `deploy/backup.sh` — POSIX `sh`, no dependency beyond the Docker CLI, cron-
  safe. One run: `pg_dump -Fc` to a hidden name, three verifications before it is published
  (`PGDMP` magic, `pg_restore -l` can read it, the listing contains `TABLE DATA`), a `.meta`
  sidecar with the Alembic revision, a mode-`600` archive of `deploy/.env` + `deploy/certs/`,
  an optional `rclone` copy to `BACKUP_REMOTE`, prune to `RETENTION_DAYS`, non-zero exit and
  an operator-chat alert on failure. Plus `--drill` (restore the newest dump **twice** —
  empty, then `--clean --if-exists` over the populated copy — and compare five row counts
  against live) and `--restore FILE` behind `I_MEAN_IT=yes`.
- **Docs**: `docs/deploy.md` §14 (the operator document), with pointers added from §9
  (redeploy) and §12 (install), and the §12 hand-rolled dump annotated as superseded.
- **Verified (2026-09-24, development stack, schema `0013` — not the VPS)**: a full run
  produced a **72,981-byte** dump, **17 tables with data**; the `.meta` read
  `alembic_revision=0013`; the config archive listed `deploy/.env` and
  `deploy/certs/{chmabapay.csr,fullchain.pem,privkey.pem}`; `--drill` reported
  `9 accounts, 13 stores, 5 payments, 1 webhook endpoint, 1 schema revision` on **both**
  sides; the drill database was dropped and the live database untouched. The `--restore`
  guard and the usage path both exited 1 **without alerting**; a deliberately broken run
  exited 1 and the alert **was delivered** (Telegram answered `"ok":true`, read from the
  response body rather than the status code, because Telegram answers 200 with `ok:false`
  when the bot has not been started).
- **Two bugs found by running it, not by reading it**: GNU tar refuses to append to a
  compressed archive, so the first version of the config archive silently contained **no
  certificates**; and a second `trap … EXIT` inside `backup()` replaced the top-level one,
  so the failure alert would never have fired.
- **Installed and verified on the host (2026-09-24, `163.245.204.122`)**: the script is at
  `/opt/chmabapay/deploy/backup.sh`, mode `755`, md5 `82333eade98846529a6378c093d3065d` —
  identical to the repository copy. The cron entry was **proven by letting it fire** rather
  than by reasoning about cron's PATH: a temporary `22 5` line produced, at **05:22:02 UTC**,
  a **58,645-byte** dump with **15 tables with data at schema `0012`**, the config archive,
  and the `BACKUP_REMOTE is unset` warnings, all in `/var/log/chmabapay-backup.log`. `--drill`
  then restored that dump twice against the **production** cluster in **5.2s** and reported
  `2 accounts, 1 store, 1 payment, 0 webhook endpoints, 1 schema revision` on both sides;
  `chmabapay_restore_drill` was dropped and the live database still reads `0012` / `2|1|1`.
  Available memory went 915 MB → 930 MB across the drill. **The POS stack was untouched**:
  `deploy-front-1`, `deploy-api-1`, `deploy-db-1` all still up 2 weeks. The final schedule is
  `15 2 * * *` at `/etc/cron.d/chmabapay-backup`.
- **Found on the host, and it matters to someone**: `/etc/cron.d/chmabapos-backup` already
  runs `/opt/chmabapos/backup.sh` nightly at 03:00 UTC, 7-day retention — so the POS is
  backed up, but to `/opt/chmabapos/backups/`, **on the same disk**, with its output sent to
  `/dev/null`, which means a failed night leaves no trace. The schedule for this job is
  02:15 so the two dumps never share the host's single core. The POS project is not this
  audit's to change and is revenue-generating, so nothing was touched; recorded as a
  decision for whoever owns it.
- **Off-host, and verified by checksum (2026-09-24)**: `rclone` v1.60.1 reaches
  Cloudflare R2 through the `s3` backend with `provider = Cloudflare` (no native `r2`
  backend at this version), scoped by a bucket-only token to `chmabapay-backups`. A
  **cron-fired** run at **09:30:02 UTC** produced `chmabapay-2026-09-24T093002Z.dump` and
  the bucket holds a byte-identical copy — md5 `85d478ba5d5f3a4bfaa5b87d2dc510b1` on both
  sides — with its `.meta` and the config archive beside it. The fetch had to be proven,
  not assumed: the previous token was **read-only**, which lists and reads fine and fails
  only on `PutObject` with a `403` that names no permission; and every upload logs a
  `501 Not Implemented` on attempt 1 (rclone HEADs back the version id R2 returns from
  `PutObject`, and R2 answers 501 to `?versionId=`) before attempt 2 succeeds, so
  **`--retries` must not be reduced** or a written object is reported as a failure.
- **Not done**: the `15 2` schedule has not fired on its own (two real fires were watched,
  at 09:19 and 09:30, proving the command and the environment rather than the time); the
  remote prune has never run because nothing in the bucket is 14 days old; the bucket has
  no lifecycle rule, so retention rests entirely on this script; `--restore` has had only
  its guard exercised; and the local prune cannot have fired yet. The tokens pasted into
  the chat during setup were to be revoked and reissued.
- **Also deployed**: this work went to production on 2026-09-24 with the rest of the audit
  (`e7b0f6f`); the migration `0013` that accompanied it is the first to have used the §14
  ritual, and `migrate` exited 0 with the schema moving `0012` → `0013`. See the deploy
  record in `docs/production-readiness.md`.
- **Note**: the alert-path test sent real messages to the operator Telegram chat, which is
  shared with the application's own alerts.

