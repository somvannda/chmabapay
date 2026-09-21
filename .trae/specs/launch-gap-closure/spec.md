# Launch Gap Closure — Spec

**Goal.** Close every gap found in the 2026-09-18 production audit of `pay.chmaba.com`
and `admin-pay.chmaba.com`, so a first external merchant can sign up and integrate
without an engineer in the loop.

**Audited revision.** `1d2826c` (production), schema at Alembic `0008`.

**Audit tracks.** (1) public site A–Z, (2) API docs vs live OpenAPI + routers,
(3) customer portal A–Z, (4) admin console A–Z, plus a production deploy/ops recon.

**Method.** Production was probed read-only (HTTP + rendered pages + `openapi.json`).
Every claim below was then re-verified against source. Findings that could not be
reproduced from source were dropped rather than carried.

---

## Severity definitions

| Level | Meaning |
| --- | --- |
| **S1 — blocker** | Shipping the first real customer without this creates legal, financial or security exposure. |
| **S2 — major** | A real customer or a real incident hits this in the first weeks; no clean workaround. |
| **S3 — minor** | Degrades trust or costs support time; workaround exists. |
| **S4 — polish** | Cosmetic or consistency only. |

## Out of scope (cannot be closed by code)

- **Legal review of the Terms, Privacy Policy and Merchant Agreement.** This is a
  lawyer task and is already tracked as `P1-4` in `docs/production-readiness.md`.
  Code can only gate acceptance and stop advertising the text as non-binding.
- **KYB capture at onboarding.** Deliberately descoped earlier (see `P1-4`). The
  register treats it as a decision, not a default.
- **NBC / Bakong on-behalf-of confirmation.** Documented in
  `docs/legal/on-behalf-of.md`; a regulatory question, not a code change.

---

## A. Public site (`pay.chmaba.com`)

| ID | Sev | Gap | Evidence | Fix |
| --- | --- | --- | --- | --- |
| G-01 | S1 | Terms and Privacy render a visible "this is a first draft, not yet binding, must be reviewed before ChmabaPay accepts real merchant traffic" banner **in production**. | `web/landing/app/terms/page.tsx:34-42`, `web/landing/app/privacy/page.tsx:27-32` | Decision D1. Code half: gate signup so the banner's own precondition is honoured. |
| G-02 | S2 | Landing advertises Bakong as a supported destination ("connect ABA PayWay **or Bakong**", "your own ABA **or Bakong** account") while `/api/docs` states ABA PayWay is the only supported destination, and Bakong credentials are disabled in production. | `web/landing/app/page.tsx:21,99,339` vs `web/landing/app/api/docs/page.tsx:41` | Remove Bakong as a *destination* claim; keep it only where a Bakong-issued code is genuinely involved. |
| G-03 | S2 | Same page contradicts itself on fund handling: "does not hold funds or **settle on your behalf**" then "we keep reconciling and **settle the payment anyway**". | `web/landing/app/page.tsx:339-340` vs `:355` | Reword the late-settlement line to "we keep confirming and record it as paid" — reserve "settle" for the banking sense. |
| G-04 | S3 | Nav item `Customers` anchors to `/#customers`, a section about late settlement. No customer content exists. | `web/landing/app/page.tsx` nav | Rename the anchor to match its section, or add real content. |
| G-05 | S4 | 404 page reuses the landing `<title>`; `/api/docs` `og:title` and `twitter:title` reuse the landing title; no canonical on `/api/docs`. | `web/landing/app/not-found.tsx`, `web/landing/app/api/docs/page.tsx:447-451` | Page-specific metadata. |
| G-06 | S3 | `/api/docs` scrolls horizontally below ~611px: `.docs-signature-left` / `.docs-signature-panel` render 587px wide. | `web/landing/app/globals.css` (`docs-signature-*`) | Stack the signature section below a breakpoint. |
| G-07 | S3 | `/onboarding/account-type` is a dead redirect stub, and the intended destination is lost on sign-in (`next=/dashboard`, not the requested path). | `web/landing/app/onboarding/account-type/page.tsx:1-21`; production redirect observed | Delete the orphan route; check the `next` construction. |
| G-08 | S3 | Contact page is `mailto:` only — no form, no monitored inbox assertion; no status page, no support SLA. | `web/landing/app/contact/page.tsx:39-48` | Decision D4. |

## B. API documentation

| ID | Sev | Gap | Evidence | Fix |
| --- | --- | --- | --- | --- |
| G-10 | S1 | Four endpoints require **no authentication** while the docs present a single Bearer surface: `POST /v1/khqr/from-link`, `/probe-aba-status`, `/payway/checkout`, `/payway/status`. Verified live: `POST /v1/khqr/from-link` with an empty body returns **422, not 401**. `payway/checkout` spends a real ABA session. | `src/chmabapay/routers/khqr.py:112,281,327,371` | Add the standard hybrid auth dependency to all four. |
| G-11 | S2 | Docs advertise `payment.scanned` and `payment.failed`. `scanned` is only produced by the dev gateway (`ENABLE_DEV_GATEWAY=false` in prod); `failed` has **no producer at all** and the status is never persisted. | `src/chmabapay/models.py:79-81`; `services/payments.py:361` called only from `routers/dev.py:80` | Remove both from the docs' event list, or implement them. Decision D5. |
| G-12 | S2 | The synthetic `/v1/webhooks/{id}/test` payload omits `financial` — the one field the docs tell integrators to branch on — plus `data.merchant`, `paid_at`, `expires_at`. | `src/chmabapay/routers/webhooks.py:88-111` vs `docs/api.md:198-200` | Build the test payload from the same builder as a real delivery. |
| G-13 | S2 | `GET /v1/payments/{id}` is documented as returning `checkout_url`; the router hardcodes `None`. `docs/api.md` correctly says it is not returned, so the docs page contradicts the prose doc. | `src/chmabapay/routers/payments.py:410` vs `web/landing/app/api/docs/page.tsx:60` | Correct the docs page. |
| G-14 | S2 | Docs say reissue accepts `superseded`; the service rejects it with 409. | `src/chmabapay/services/payments.py:211-213` vs `page.tsx:61` | Correct the docs, or widen the guard. Decision D5. |
| G-15 | S2 | `probe-aba-status` is billed as "the first-priority health check"; the reconciler documents that page as a static template that can produce a **false paid** and refuses to trust it. | `src/chmabapay/services/status_reconciler.py:25-40` vs `page.tsx:86` | Reword to describe what it actually returns; drop the "authoritative" framing. |
| G-16 | S2 | 9 Bakong Ledger Lookup endpoints are listed for customers while the same group's own text says they answer `503 bakong_not_configured` in production. Meanwhile the two transaction endpoints that **do** work without Bakong creds (`GET /v1/transactions/check-status/{id}`, `POST /v1/transactions/verify-payment/{id}`) are undocumented. | `page.tsx:66-80`; `routers/transactions.py:369-371,420-472` | Mark the dead group as unavailable; document the two live endpoints. |
| G-17 | S2 | The live `openapi.json` is unusable as a contract: no `components.securitySchemes`, every `authorization` param `required=false`, and no 201/402/403/404/409/410/503 responses on any operation. It also publicly enumerates all 9 `/v1/admin/*` paths. | live `openapi.json`; `routers/admin.py:19` | Add security schemes + declared error responses; `include_in_schema=False` on the admin router. |
| G-18 | S3 | Field-level doc drift: `attempts` (actual `attempt_count`), `hosted_qr` documented default `true` (schema default `None`/auto), `KeyCreate.name` documented optional (it is required), `link.raw_link`/`merchant_account_id` requiredness unstated, `StoreCreate.city` default unstated, `secret_key` accepted but undocumented. | `routers/webhooks.py:342-351`, `schemas.py:33,141-151`, `routers/keys.py:26`, `schemas.py:22-27` | Correct each. |
| G-19 | S3 | Reports CSV gating is incoherent: the live plan matrix has `csv_export_enabled: true` on **Free**, so the 403 is unreachable, yet the 403 message and the docs both claim CSV needs Starter. The store-catalog CSV is generated client-side and bypasses the gate entirely. | `routers/reports.py:40-48`; live `GET /v1/billing/plans`; `web/landing/app/dashboard/reports/page.tsx:104-138` | Decide the real CSV policy and align plan matrix, message, docs and client-side export. Decision D6. |
| G-20 | S4 | Amount units are mixed in one surface: payments take a decimal `amount`, plans and reports return `*_cents`. | `page.tsx:58`, `routers/reports.py:311-328` | Document the rule explicitly at the top of the payments section. |

## C. Customer portal

| ID | Sev | Gap | Evidence | Fix |
| --- | --- | --- | --- | --- |
| G-21 | S1 | Terms acceptance is never enforced. `POST /v1/me/terms` works, but **no portal code calls it** and nothing gates on `terms_accepted_at`. A merchant can transact forever without accepting. | `src/chmabapay/routers/account.py:117-153`; no caller anywhere in `web/` | Add a blocking acceptance screen in the post-OAuth flow; enforce server-side so API keys cannot bypass. |
| G-22 | S1 | A customer cannot pay. No `PlanInvoice` row is ever created — the billing worker is an M1 no-op stub and `PlanInvoice(` appears only as its class definition. | `src/chmabapay/workers/w3_billing.py:1-38`; `models.py:328` | Decision D2. |
| G-23 | S1 | Even with an invoice, `GET /v1/billing/invoices/{id}/khqr` raises `500 platform_hq_store_not_configured`: `CHMABAPAY_HQ_STORE_ID` and `CHMABAPAY_HQ_PAYWAY_LINK` are **empty in `deploy/.env`** (verified), so no HQ store is ever seeded. The customer sees the raw code in a toast. | `routers/billing.py:199-234`; `routers/auth.py:245-283`; prod env | Decision D2. |
| G-24 | S1 | `POST /v1/billing/change-plan` collects no payment and applies no proration, and is one click from the dashboard → Pro ($59.99/mo, 50 stores, 1M payments) for $0. | `routers/billing.py:140-196`; `web/landing/app/dashboard/billing/page.tsx:396-457` | Decision D2. |
| G-25 | S2 | No test/sandbox key exists at all — `create_key` hardcodes `mode="live"`. A first integration must move real money. | `routers/keys.py:120-127`; `docs/roadmap.md:68` (Phase 4) | Decision D3. |
| G-26 | S2 | No ABA PayWay link validation. Client-side check is `length >= 6`; the backend stores anything and hardcodes `verification=LINK_VERIFIED`, then the store goes `active` and fails later at QR mint with `502 payway_hosted_error`. | `web/landing/app/dashboard/stores/new/page.tsx:24-26`; `services/stores.py:130-138` | Validate on create/link-set; only promote to `active` after validation. |
| G-27 | S2 | `external_id` is collected at store creation then displayed **nowhere** — not the stores list, not the store detail, not store settings — yet it is the identifier required for `merchant=`. | `dashboard/stores/page.tsx:201-209`; `dashboard/[public_id]/page.tsx:255-261`; `[public_id]/settings/page.tsx:303-326` | Render it (and allow editing) on detail + settings. |
| G-28 | S2 | Editing a **disabled** webhook endpoint silently re-enables it: `WebhookOut` does not return `enabled`, and the modal initialises from `endpoint?.enabled !== false` → `true` when absent. | `routers/webhooks.py:39-45`; `dashboard/webhooks/page.tsx:489,509-517` | Return `enabled`, or init from `status === "active"`. |
| G-29 | S2 | Plan-limit errors surface as raw machine codes (`quota_exceeded`, `amount_too_high`, `store_disabled`) inconsistently across pages; `stores/new` maps them, everything else shows the string. | `services/payments.py:366-396`; `dashboard/payments/new/page.tsx:127-128` | One shared error→copy mapper with an upgrade CTA. |
| G-30 | S2 | Fetch failures are swallowed by blanket `catch {}` and render as empty/zero **success** states on nearly every dashboard panel. A 500 is indistinguishable from "no data". | `dashboard/keys/page.tsx:96-115`, `dashboard/page.tsx:136-162`, `dashboard/billing/page.tsx:179-243`, `dashboard/reports/page.tsx:58-77`, `[public_id]/settings/page.tsx:80-116` | Explicit per-panel error state with retry. |
| G-31 | S2 | No account deletion / erasure path (`DELETE /v1/me` absent) and no password rotation. `PATCH /v1/me` changes email with no verification. | `routers/account.py:75-96` | Add erasure + password change; require re-verification for email change. Decision D4. |
| G-32 | S3 | Help FAQ contradicts the product: claims CSV export is in every plan (backend says Starter) and that downgrades apply next cycle (`change-plan` applies immediately). | `dashboard/help/page.tsx:25-28` vs `routers/reports.py:43-48`, `routers/billing.py:155-177` | Correct the copy. |
| G-33 | S4 | Sign-out fallback calls `GET /auth/logout`, which does not exist; it 404s before the final redirect. | `dashboard/layout.tsx:149-160` | Remove the dead fallback. |

## D. Admin console

| ID | Sev | Gap | Evidence | Fix |
| --- | --- | --- | --- | --- |
| G-40 | S1 | The first admin cannot sign in. `CHMABAPAY_ADMIN_PASSWORD` is empty (verified); `cli set-password` refuses when the account does not exist; only a Google sign-in creates **and** promotes the account; but the console refuses `amr=google` sessions. Bootstrap therefore requires SSH. | `cli.py:141-142`; `routers/auth.py:511-520`; `web/admin/components/AdminShell.tsx:237-243` | Add `cli grant-admin <email> [--password]` that creates/updates and promotes in one command; document the exact path. |
| G-41 | S1 | Payment disputes cannot be resolved. No `GET /v1/admin/payments/{id}`, so an operator cannot see `bakong_ref` or `gateway_status_raw` — the two fields that answer "did money move?". No force-reconcile, no mark-paid, no re-deliver. | `routers/admin.py:703-815`; `routers/transactions.py:439-448` | Admin payment detail + reconcile + re-deliver + audit-logged mark-paid. |
| G-42 | S2 | The "password-only console" is enforced **only in React**: `session_auth_method` is referenced solely by `GET /v1/me`, so an admin's Google session can call every `/v1/admin/*` endpoint directly. | `routers/account.py:72` (only caller); `routers/admin.py:33-51` | Enforce `amr == "password"` inside `get_hybrid_admin_context`. |
| G-43 | S2 | No MFA and no per-account lockout on `POST /auth/login`; the only defence is a 20/min/IP in-process limiter keyed on `X-Forwarded-For`. | `routers/ratelimit.py:77-79,104-145`; `routers/auth.py:493-539` | Decision D7. |
| G-44 | S2 | Admin sign-in is never audit-logged (mutations are). No record of successful or failed logins. | `routers/auth.py:493-539`; `audit.py:20-38` | Audit `auth.login_succeeded` / `auth.login_failed` with IP. |
| G-45 | S2 | Overview has no operational signals: no pending-too-long, no detection-closed-unpaid, no webhook failure rate, no worker liveness, no queue depth, no GMV, no error rate. | `routers/admin.py:942-997`; `web/admin/app/page.tsx:81-125` | Add an "needs attention" block sourced from existing counters. |
| G-46 | S2 | No operator plan change, no invoice mark-paid/waive/credit, no per-account key revoke, no store disable. Billing corrections need DB access; a leaked key cannot be killed without suspending the whole merchant. | `routers/admin.py:156-353,650-695`; `models.py:136,178,350-376` | Add these endpoints with audit rows and UI. |
| G-47 | S2 | 401 on any page-level fetch renders the literal string `invalid_session` in a warning banner instead of redirecting to `/login`. Client-side navigation does not remount the shell, so an operator is stranded mid-incident. | `web/admin/lib/apiError.ts:7-19`; `AdminShell.tsx:165-172` | Central fetch helper that redirects on 401. |
| G-48 | S3 | Failed webhook deliveries cannot be retried, and no alert exists for delivery **failure rate** (only queue backlog). | `routers/admin.py:823-934`; `alerts.py:134-178` | Add retry endpoint + button, and a failure-rate alert condition. |
| G-49 | S3 | `ExternalId`/key state not visible per account; suspension's blast radius unstated; self-suspension possible with no last-admin guard. | `web/admin/app/accounts/[account_id]/page.tsx:207-241` | Consequence copy + last-admin guard. |

## E. Operations / deployment

| ID | Sev | Gap | Evidence | Fix |
| --- | --- | --- | --- | --- |
| G-50 | S3 | `BAKONG_API_TOKEN` / `BAKONG_DEVELOPER_EMAIL` are empty in production. **The primary ABA PayWay path does not need them** (detection runs on ABA's hosted-session API), so this is not a launch blocker — but the deployment cannot answer the two Bakong ledger questions and the docs list them anyway. | prod env; `workers/w1_payment_detection.py:110-122`; `services/status_reconciler.py:185-236` | Decision D8: leave as-is and mark the docs, or supply credentials. |

## F. Verified as already sound (do not rework)

- Edge authorisation on `/v1/admin/*` (401 unauthenticated, 401 with a bare `ck_` key, `is_platform_admin` required on all 12 routes, account-active enforced on both auth paths).
- Plan limits enforced at create time for stores, keys, webhooks and CSV.
- Webhook signature scheme, header names, 300 s replay tolerance and 8-attempt cap all match the code.
- Audit trail is transactional, covers every admin mutation, and is surfaced in the UI.
- `/metrics` token-gated and not routed at the edge; alerts wired to Telegram for worker-stall, webhook backlog, double-charge and unknown outcome.
- Ledger/retention: 90-day raw-payload sweep enforced by the software.
- ABA PayWay detection genuinely works without Bakong credentials.

---

## Decisions taken (2026-09-18)

| ID | Question | **Decision** |
| --- | --- | --- |
| **D1** | Legal text is a self-declared draft that says it must be replaced before real traffic. | **Remove the draft banner now.** The Terms and Privacy pages stop advertising themselves as non-binding. The lawyer review stays open as `P1-4`; the content is unchanged apart from the banner. Because acceptance now means something, the acceptance gate (G-21 / T-09) is in scope. |
| **D2** | Billing cannot collect, and self-upgrade is free. | **Full fix.** Implement invoice generation (finish the W3 stub), seed the HQ store + PayWay link, and gate paid-tier upgrades on a settled invoice. No admin-only stopgap. |
| **D3** | No sandbox key exists. | **Defer.** Onboard the first merchant hand-held against live. Test-mode keys remain a Phase-4 item and are explicitly out of scope for this plan. |
| **D4** | Contact form, status page, account erasure, password rotation. | **Default:** build account erasure and password rotation (T-16, an S2 security gap); defer the contact form, status page and SLA (T-34, S3) unless asked. |
| **D5** | `payment.scanned` / `payment.failed` and reissue-on-superseded. | **Default: fix the docs to match reality** (T-25, T-26). Revisit implementing the producers if a merchant needs them. |
| **D6** | CSV export policy. | **Default: keep CSV available everywhere and delete the gate** (T-33), aligning the 403 message, the plan matrix, the docs and the client-side store export. |
| **D7** | Admin MFA. | **Defer TOTP; ship lockout.** The per-account lockout (T-05) closes the brute-force path. MFA becomes a fast-follow. |
| **D8** | Bakong platform credentials. | **Default: leave empty**, mark the ledger endpoints unavailable (T-28). The ABA PayWay rail does not depend on them. |

Deferrals (D3, D7-MFA, D4 contact/status) are recorded here so they are not silently
forgotten; each has a task that states what was deferred.

## Acceptance criteria

1. No `S1` item remains open except those explicitly deferred by decision.
2. A new merchant can: sign in → accept terms → create a store with a **validated**
   PayWay link → create a key → create a payment → receive a signature-verified
   webhook — with no engineer involved.
3. `POST /v1/khqr/from-link` and the other three KHQR routes return 401 without a
   credential (verified against production).
4. An operator can take a first admin seat, find a payment by reference, see its raw
   gateway payload, and resolve a "I paid but it says expired" dispute in-product.
5. `docker compose build` + the pytest suite pass; production redeploy verified.

## Verification protocol

- Backend: `docker compose build api` then the pytest suite against Postgres per the
  documented invocation in `docs/production-readiness.md`.
- Frontend: `docker build -f web/Dockerfile --build-arg APP=<landing|admin>` (host
  `next build` is unreliable here — see project memory).
- Production: read-only re-probe of every route touched, plus a redeploy.
