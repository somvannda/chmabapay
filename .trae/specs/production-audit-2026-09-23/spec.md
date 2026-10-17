# Production Audit — 2026-09-23

**Goal.** Record the third A-to-Z production audit of `pay.chmaba.com` and
`admin-pay.chmaba.com`, and close every gap that stands between ChmabaPay and a
first paying merchant integrating on their own.

**Supersedes the open tail of.** `.trae/specs/launch-gap-closure/` (2026-09-18,
Waves 1–8). That plan's 47 tasks are `complete`; this audit re-verified them and
found what regressed, what was missed, and what was invented after it closed.

**Method.** Production probed read-only: HTTP status, rendered pages, browser
console and network capture, plus a live end-to-end attempt at the one flow the
operator reported broken. Every claim was then re-verified against source. Items
that could not be reproduced from source or a live probe were dropped.

**Severity.**

| Level | Meaning |
| --- | --- |
| **S1 — blocker** | Shipping the first real customer without this creates legal, financial or security exposure, or breaks the documented onboarding flow. |
| **S2 — major** | A real customer or operator hits this in the first weeks; no clean workaround. |
| **S3 — minor** | Degrades trust or costs support time; a workaround exists. |
| **S4 — polish** | Cosmetic or consistency only. |

---

## What is already sound (do not rework)

Verified live or in source during this audit:

- **Public site.** All pages 200, real 404 page, no dead links or anchors, no
  placeholders, no site-attributable console errors, no horizontal overflow,
  complete breakpoint system, correct `robots.txt` / `sitemap.xml`, HSTS +
  `X-Frame-Options: DENY` + `frame-ancestors 'none'` + `nosniff` on both hosts.
- **API docs accuracy.** Every documented row matches a real route with the right
  method and path. Key caps, CSV-on-every-plan, delivery-log limits (`limit` max
  500), the 8-attempt retry cap, header names and the four advertised webhook
  events all match the code. `payment.scanned` is dev-gateway-only and
  `payment.failed` has no producer, so omitting both is *correct*.
- **Money path.** Proven live 2026-09-17: a real ABA wallet settled a real
  `POST /api/v1/payments`, and a signature-verified `payment.completed` left the
  platform 104 ms after the row was written.
- **Tenant isolation.** Every merchant-facing path filters `account_id`
  server-side (payments, stores, keys, webhooks, reports). Changing a `public_id`
  in a URL returns 404, never another tenant's row.
- **Admin authorisation.** All 25 `/api/v1/admin/*` handlers carry the admin guard and
  require `is_platform_admin`; audit rows are written for every admin mutation.
- **Production health.** `GET /health` answers `{"status":"ok"}`; a
  platform-admin account exists and password login works.

---

## A. Public site and legal text

| ID | Sev | Finding | Evidence |
| --- | --- | --- | --- |
| A-01 | **S1** | Terms and Privacy are **not reviewed by counsel**, and the "draft / not binding" banner was removed by product decision. The code comments still warn that absence of the banner is not approval. | `web/landing/app/terms/page.tsx:8-19`, `web/landing/app/privacy/page.tsx:10-16`, `docs/production-readiness.md` (P1-4) |
| A-02 | **S1** | The **NBC/Bakong "on-behalf-of"** question is unresolved and self-described as the biggest legal/technical risk: whether the platform may query other merchants' transaction status. | `docs/legal/on-behalf-of.md:3-6,50-68`, `docs/legal/merchant-agreement.md:120-129` |
| A-03 | **S2** | Merchants must accept a "merchant agreement" that is **never published and not in force**; the gate's own link points at `/terms`, a different document. | `web/landing/app/dashboard/layout.tsx:246-276`, `src/chmabapay/routers/keys.py:100-102`, `docs/legal/merchant-agreement.md:1-11` |
| A-04 | **S2** | Terms §8 excludes indirect loss but says nothing about the ceiling. Per D-1 it must state that liability is limited to the extent of the ABA PayWay and Bakong/NBC rail limitations, with no liability beyond them. | `web/landing/app/terms/page.tsx:248-260` |
| A-05 | **S2** | **No refund / cancellation / price-change-notice policy** for plan fees (payer refunds are covered; subscription fees are not). | `web/landing/app/terms/page.tsx:215-234` |
| A-06 | **S2** | **No DPA / sub-processor schedule** offered to business merchants, though the platform is a processor of merchant and payer data. | absent repo-wide |
| A-07 | **S2** | **Resend** receives merchant email addresses and invoice data but is **not listed** in the privacy processor list. | `src/chmabapay/services/resend.py:33`, `web/landing/app/privacy/page.tsx:241-279` |
| A-08 | **S2** | Pro advertises **"Priority support"** which currently has no implementation anywhere, and `/contact` explicitly promises no response time. Per D-4 this becomes a real feature — see §F. | `src/chmabapay/db.py:112,123`, `web/landing/app/contact/page.tsx:69-75` |
| A-09 | **S2** | **Signup is Google-only.** If Google OAuth is unconfigured the CTA returns HTTP 400 `google_oauth_not_configured`, and there is no alternative self-serve path for merchants. | `web/landing/app/page.tsx:14`, `src/chmabapay/routers/auth.py:502-509` |
| A-10 | **S3** | Terms §6 billing prose drifted from the code: it describes a per-calendar-month invoice key that was deliberately replaced by `(subscription, period_start)`. | `web/landing/app/terms/page.tsx:228-231` vs `src/chmabapay/services/billing.py:11-19` |
| A-11 | **S3** | `/contact` omits the registered entity and address that `/terms` and `/privacy` now carry. | `web/landing/app/contact/page.tsx:27-43` |
| A-12 | **S3** | Address typo: "Khan **Chmbar** Ampov" (conventional: "Chbar Ampov") inside legally-operative text. | `terms:63-64`, `privacy:64-65`, `merchant-agreement.md:30-31` |
| A-13 | **S3** | No merchant notification when the agreement version changes; §9 promises to ask for re-acceptance, but only an in-app gate exists. | `web/landing/app/terms/page.tsx:264-271` |
| A-14 | **S4** | Home page has no `alternates.canonical` (every other page does). | `web/landing/app/layout.tsx:30-70` |
| A-15 | **S4** | Unused `brandColors` import on the home page. | `web/landing/app/page.tsx:1` |
| A-16 | **S4** | `/onboarding` is listed in `robots.txt` but returns 404; `/.well-known/security.txt` is a 404. | `web/landing/app/robots.ts`, live probe |

---

## B. API documentation surface

Every documented endpoint exists. The work here is **what is advertised**, and
bringing the page up to the standard a developer expects from an integration
reference.

| ID | Sev | Finding | Evidence |
| --- | --- | --- | --- |
| B-01 | **S2** | The **"Bakong Ledger Lookup" group (10 endpoints)** is published on the marketing page. Its own text admits they answer `503 bakong_not_configured` today. This is internal operational state on a customer-facing page and pure noise for an integrator. | `web/landing/app/api/docs/page.tsx:76-93` |
| B-02 | **S2** | Non-payable / advisory-only helpers are presented as integration surface: `POST /api/v1/khqr/from-link` ("the code it returns is not payable") and `POST /api/v1/khqr/probe-aba-status` ("advisory only"). | `web/landing/app/api/docs/page.tsx:99-100` |
| B-03 | **S2** | No **error-code reference**. The API returns structured `detail` codes everywhere; integrators have no table to code against. | `src/chmabapay/errors.py` |
| B-04 | **S3** | No **rate-limit documentation**, although limits are enforced (60/min payment-create, 600/min API, 120/min checkout, 20/min auth). | `src/chmabapay/config.py:89-94`, `src/chmabapay/ratelimit.py:77-96` |
| B-05 | **S3** | No **authentication / environments section**, so nothing states that only live keys exist and that a first integration moves real money. | `src/chmabapay/security.py:20`, `src/chmabapay/routers/keys.py:120-127` |
| B-06 | **S3** | No **idempotency**, **pagination**, or **versioning** conventions section, though all three exist (`idempotency_key`, `limit`/`offset`, `/api/v1`). | `src/chmabapay/schemas.py:143-163`, `src/chmabapay/routers/payments.py:403` |
| B-07 | **S3** | The page is hand-written, so it drifts from `openapi.json` with no test to catch it. | `web/landing/app/api/docs/page.tsx` |
| B-08 | **S4** | Amount units are mixed in one surface (`amount` decimal vs `*_cents` integer) with no explicit rule stated. | `web/landing/app/api/docs/page.tsx:58` |

---

## C. Customer portal

| ID | Sev | Finding | Evidence |
| --- | --- | --- | --- |
| C-01 | **S1** | **Webhook endpoint creation always fails.** The modal sends `enabled`, but `WebhookCreate` is `extra="forbid"` and has no such field → HTTP **422 `Extra inputs are not permitted`**. Reproduced live in a browser against production; the raw validator message is what the merchant sees. This breaks the documented onboarding step 3 and launch acceptance criterion 2. | `web/landing/app/dashboard/webhooks/page.tsx:579-603` vs `src/chmabapay/routers/webhooks.py:30-35` |
| C-02 | **S4** | The create form offers an **"Enabled" checkbox that has no effect**: `create_webhook` always sets `WEBHOOK_ENDPOINT_ACTIVE`, so an unticked box still produces an active endpoint. After C-01 the field is not sent at all. (Corrected during implementation: an earlier live observation described the *event* checkboxes as read-only — source shows they are editable and functional, and the wildcard default matches the form's own note. That claim is withdrawn.) | `web/landing/app/dashboard/webhooks/page.tsx:676-686`, `src/chmabapay/routers/webhooks.py:198-211` |
| C-03 | **S2** | Backend `detail` codes surface to merchants as **raw machine strings** instead of mapped copy, with no consistent upgrade CTA. | `web/landing/components/portal/apiError.ts:63-71`, `src/chmabapay/services/payments.py:366-396` |
| C-04 | **S3** | Dashboard copy says each store has its own "webhooks, and API keys" — both are account-scoped now, and the store-scoped routes are redirect stubs. | `web/landing/app/dashboard/page.tsx:299-302` |
| C-05 | **S3** | Reports UI sends only `from`/`to`; the backend supports `store_id`, `merchant`, `statuses`. Reconciling by store means exporting everything. | `web/landing/app/dashboard/reports/page.tsx:57`, `src/chmabapay/routers/reports.py:131-152` |
| C-06 | **S3** | Payment-create UI cannot set `idempotency_key`, `merchant`, or `hosted_qr`, all of which the API accepts. | `web/landing/app/dashboard/payments/new/page.tsx:108-119`, `src/chmabapay/schemas.py:143-163` |
| C-07 | **S3** | Store settings cannot edit `city`; new stores are stuck at the default "Phnom Penh" while the CSV export carries a City column. | `web/landing/app/dashboard/[public_id]/settings/page.tsx:150`, `src/chmabapay/schemas.py:33,119` |
| C-08 | **S3** | Clearing the store name and saving returns a raw 422 rather than an inline field error. | `web/landing/app/dashboard/[public_id]/settings/page.tsx:150`, `src/chmabapay/schemas.py:117` |
| C-09 | **S3** | "Test: Mark paid" calls `/_dev/payments/{id}/pay`, which is unmounted in production → 404 shown to platform admins. | `web/landing/app/dashboard/payments/[public_id]/page.tsx:176`, `src/chmabapay/main.py:258-261` |
| C-10 | **S4** | Help FAQ hardcodes plan numbers and can drift from the live plan payload the billing page renders. | `web/landing/app/dashboard/help/page.tsx:27` |
| C-11 | **S4** | Store-scoped webhook/key pages flash the store chrome before the client-side redirect. | `web/landing/app/dashboard/[public_id]/webhooks/page.tsx:9` |
| C-12 | **S3** | The error mapper had a **dead entry**: `period_already_invoiced` is defined in `ERROR_COPY` but no backend code raises it, while the code that *is* raised for that condition (`open_invoice_unpaid`) was unmapped — so it fell through and rendered as `open_invoice_unpaid: invoice 12 for 2026-09 is still unpaid`. Found while enumerating the error surface for the docs (PA-10). | `web/landing/components/portal/apiError.ts` (former `period_already_invoiced` entry), `src/chmabapay/routers/billing.py:340` |
| C-13 | **S3** | Any unmapped code reached the screen verbatim, including prefix codes whose suffix is upstream exception text (`bakong_error: …`, `qr_render_failed: …`, `payway_hosted_error: …`). A blanket "show the prose" fix would have leaked exception text to merchants, so the fallback is curated and the general case is answered with form-level copy. | `web/landing/components/portal/apiError.ts`, `src/chmabapay/routers/transactions.py:89` |

---

## D. Admin console

| ID | Sev | Finding | Evidence |
| --- | --- | --- | --- |
| D-01 | **S2** | **`amr: password` is bypassable.** The `Bearer ck_` branch is evaluated before the amr check, so a platform-admin API key **plus any valid session cookie** reaches every admin route without the password claim. The key alone is insufficient (no cookie → 401), but the documented guarantee ("SSO sessions cannot access `/api/v1/admin/*`") does not hold. | `src/chmabapay/routers/admin.py:87-108` |
| D-02 | **S2** | **No payment refund/reversal in the console.** Operators can reconcile, mark-paid and redeliver but cannot resolve a "I was refunded" dispute in-product; the merchant-facing route is scoped to the merchant's own account. | `src/chmabapay/routers/admin.py` (no reverse route), `src/chmabapay/routers/payments.py:344-375` |
| D-03 | **S2** | `PUT /api/v1/admin/stores/{public_id}/internal` is implemented and described in the console README but **wired to no UI**, so a wrongly-internal store cannot be corrected. | `src/chmabapay/routers/admin.py:1289-1348`, `web/admin/README.md:87-94` |
| D-04 | **S3** | Account-detail invoice panel omits `void`, so a voided invoice still renders **Resolve** and always returns 409; `invoice_already_void` is untranslated. | `web/admin/app/accounts/[account_id]/page.tsx:93,959`, `web/admin/lib/apiError.ts:14-45` |
| D-05 | **S3** | Account standing cannot be set to `restricted` (billing freeze), and a restricted account renders as "active". | `src/chmabapay/routers/admin.py:449-451`, `src/chmabapay/models.py:43` |
| D-06 | **S3** | Account detail loads **all** stores and keys unbounded; only invoices are capped. | `src/chmabapay/routers/admin.py:316-439` |
| D-07 | **S3** | Deliveries retry modal offers "also re-send successes" but the row endpoint accepts no such parameter, so a direct caller silently re-sends successes. | `web/admin/app/deliveries/page.tsx:433-468`, `src/chmabapay/routers/admin.py:2037-2083` |
| D-08 | **S3** | Missing day-one operator tools: impersonation, audit-log date range and export, health/metrics page, key create/rotate on a merchant's behalf, search by store id or key prefix. | `src/chmabapay/routers/admin.py`, `web/admin/app/audit/page.tsx:73-76` |
| D-09 | **S4** | Three destructive modals do not echo the resolved target: account suspend/activate, assign-plan, and manual mark-paid. | `web/admin/app/accounts/[account_id]/page.tsx:1020-1055,1140-1168`, `web/admin/app/payments/[public_id]/page.tsx:584-600` |
| D-10 | **S4** | Error banners render twice on all list pages; the admin OpenAPI link documents no admin route. | `web/admin/app/accounts/page.tsx:162,167`, `web/admin/components/AdminShell.tsx:340-342` |

---

## E. Operations and launch posture

| ID | Sev | Finding | Evidence |
| --- | --- | --- | --- |
| E-01 | **S2** | **No sandbox / test key.** `create_key` hardcodes `mode="live"`, so a first integration must move real money, and the docs never say so. Decision D3 deferred test keys. | `src/chmabapay/routers/keys.py:120-127` |
| E-02 | **S2** | Internal dev tooling instructs the operator to use a `ck_test_` key that **cannot be minted**; `new_api_key` produces `ck_live_` only. | `src/chmabapay/security.py:20`, `src/chmabapay/tools/testplan.py:469`, `src/chmabapay/tools/integration_test.html:122-123` |
| E-03 | **S3** | `deploy/.env` ships empty `CHMABAPAY_HQ_STORE_ID`; the HQ link is console-resolved instead. Correct, but undocumented where an operator would look. | `deploy/.env.example`, `src/chmabapay/services/billing.py:204` |
| E-04 | **S2** | **`POST /api/v1/me/password` returned an unhandled 500 for a long password.** The schema bounds the password in *characters* (`max_length=200`) but bcrypt hashes at most 72 **bytes** and `hash_password` raises past that. A 73-character ASCII password, or any password whose UTF-8 form passes 72 bytes — 25 Khmer characters is 75 bytes — reached the hash call and 500'd. Found while enumerating the error surface for the docs (PA-10). | `src/chmabapay/routers/account.py:88,253`, `src/chmabapay/security.py:37-46` |

---

## F. Support ticketing (new — decision D-4)

"Priority support" is currently a boolean on the plan with no mechanism behind it.
D-4 is to **build it**, so the Pro claim becomes true instead of being removed.
This is the only net-new feature in this plan; everything else is correction.

**Definition.** A support request is a recorded, threaded conversation between a
merchant and the platform, visible to the merchant in the portal and to operators
in the admin console, with a response-time target attached to the merchant's plan.

| ID | Sev | Requirement |
| --- | --- | --- |
| F-01 | S2 | **`SupportRequest` model + migration.** `public_id` (`sup_…`), `account_id` FK, `subject`, `category`, `status`, `priority`, `assigned_admin_account_id`, `first_response_at`, `resolved_at`, `created_at`, `updated_at`. Alembic revision after the current head; `alembic check` must stay clean. |
| F-02 | S2 | **`SupportMessage` model.** Threaded replies: `request_id` FK, `author_account_id`, `author_kind` (`merchant` \| `operator`), `body`, `created_at`. The opening message is message #1 so a request is never headerless. |
| F-03 | S2 | **Merchant API.** `POST /api/v1/support/requests` (open), `GET /api/v1/support/requests` (own account only), `GET /api/v1/support/requests/{public_id}`, `POST /api/v1/support/requests/{public_id}/reply`, `POST /api/v1/support/requests/{public_id}/close`. Session or API key, scoped to `account_id` exactly like every other merchant route — no cross-tenant read. |
| F-04 | S2 | **Operator API.** `GET /api/v1/admin/support/requests` (queue with status/priority/account filters and paging), `GET /api/v1/admin/support/requests/{public_id}`, `POST /api/v1/admin/support/requests/{public_id}/reply`, `PATCH /api/v1/admin/support/requests/{public_id}` (status and assignment). Every operator write writes an audit row. |
| F-05 | S2 | **Priority derives from the plan, and orders the queue.** `priority` is set from `Plan.priority_support` at open time (Pro → `priority`, others → `standard`). The operator queue sorts priority first, then oldest-unanswered. This is what makes the Pro promise real rather than decorative. |
| F-06 | S2 | **First-response tracking.** `first_response_at` is set on the first operator reply only. The portal shows the target for the account's plan and whether it was met; the console shows requests approaching or past target, highlighted. |
| F-07 | S2 | **Merchant portal surface.** A support page under `/dashboard` with: open a request, the thread, reply, close, and the account's response-time target stated inline. Reachable from the help page. |
| F-08 | S2 | **Admin console surface.** A support queue page: list with status/priority filters and paging, detail with the full thread, reply box, status change, and assignment. Consistent with the existing console patterns (loading/empty/error states, confirmation on close). |
| F-09 | S3 | **Notifications.** Resend email to the merchant when an operator replies; operator alert (existing Telegram channel) when a `priority` request is opened. Reuse `services/resend.py` and `services/telegram.py` rather than adding a channel. |
| F-10 | S3 | **Copy alignment (closes A-08).** `/contact` states the **24-hour calendar** first-response target for Pro and that Free/Starter are best-effort email with no target. The portal support page states the account's own target. The plan matrix keeps `priority_support` and it is now true. |
| F-11 | S3 | **Retention.** Support threads contain merchant-entered content; they follow the same retention posture as the rest of the platform and must be covered explicitly in the privacy policy's data inventory. |

**Out of scope for this feature.** Attachments, CSAT scoring, canned responses,
SLA breach escalation automation, live chat, and KB article suggestions. Tickets
are text + status + assignment, nothing more, until there is a reason.

---

## Decisions

### Resolved — 2026-09-23

| ID | Question | **Decision** |
| --- | --- | --- |
| **D-1** | Legal text (A-01…A-06) | **Treat the published Terms and Privacy as reviewed.** Remove the "not reviewed" code comments and record the position in `docs/production-readiness.md`. **No standalone liability cap**: §8 states that ChmabaPay's aggregate liability is limited to the same extent as the limitations imposed by the underlying rails — ABA PayWay and Bakong/NBC — and that ChmabaPay assumes no liability beyond them. A short refund/cancellation statement for plan fees is still required so the document is internally complete, and A-07 (Resend in the processor list) is a factual omission that must still be fixed. |
| **D-2** | Sandbox keys (E-01, E-02) | **Live-only for merchant #1.** Keep no sandbox; onboard hand-held against live. Fix E-02 so no tooling instructs anyone to use a `ck_test_` key that cannot be minted. Sandbox keys stay a Phase-4 item. |
| **D-3** | Bakong ledger group (B-01, B-02) | **Delete from the public page.** Remove the 10 Bakong Ledger Lookup rows and the two non-payable KHQR helper rows. Routes stay mounted, unadvertised. |
| **D-4** | "Priority support" (A-08) | **Build it.** Ship a full support ticketing system: support request records, status tracking in the merchant portal, and an operator queue in the admin console. Pro carries a **24-hour calendar** first-response target, stated on `/contact` and in the portal. See §F. |
| **D-5** | Merchant agreement (A-03) | **Align the labels and the recorded evidence to the Terms** (recommended option b) so the button label, the linked document and the stored `terms_accepted_version` name the same thing. |
| **D-6** | Admin API-key access (D-01) | **Reject `ck_` on `/api/v1/admin/*` outright** (recommended option a). The console is password-only by design; correct the README claim to match. |
| **D-7** | The public reference listed the platform's own dashboard surface (API Keys, Billing & Plans, Account) beside the integration API. | **Hide it.** The public reference keeps only what an integrator calls with an API key — Stores, Payments, Reconciliation, Webhooks, Reports, Hosted checkout, KHQR. API Keys, Billing and Account come off the page and into `docs/api.md` for internal use. Routes stay mounted; the drift test records the paths as withheld, so the omission is a decision. |
| **D-8** | `/api/v1/keys` accepted an API key, so a leaked key could mint a replacement for itself and outlive its own revocation, or revoke every other key on the account. | **Make key management session-only**, matching `/api/v1/me`, `/api/v1/account` and `/api/v1/billing/*`. No credential may extend or destroy itself. |
| **D-9** | The Quick-start cards all used the same icon (`-setup`). | **Keep four steps and give each its own icon.** Four is the number of things a merchant must actually do; three would drop the webhook step, which is the one integrators get wrong. |
| **D-10** | Code panels were `#111217` near-black, reading as a terminal rather than a code block. | **Light panel with a Copy button** — `#fbfbfd` on a `#e9e9ef` border, a slim header carrying the language, muted comments. Applied to every code panel on the page so the signature section matches. |
| **D-11** | PA-46 lightened the code panels but left their shells dark — `.docs-qs-block` was `#0c0d10` and `.docs-signature-panel` `#0f1015` — so each card held a light snippet inside a black frame. The Quick-start steps were also a two-column grid, which made a six-step sequence read left-right-left-right. | **Rebuild the section as a numbered vertical timeline**, and light every remaining shell. A violet rail with a numbered node per step, the snippet hanging off it; the section header becomes an eyebrow, the title and chips, replacing the plain heading and paragraph. |

---

## Launch gate

The first paying merchant can be onboarded when:

1. **C-01 is closed** and verified live (create a webhook through the UI, end to end).
2. **PA-33 has landed** — the published Terms and Privacy match decision D-1, with
   §8 stating the rail pass-through limitation and §6 stating the fee position.
3. **B-01 and B-02 are closed** so nothing on a customer-facing page admits to being
   switched off. **A-08 is closed either by shipping Wave 9, or by withholding the
   Pro priority-support claim until it ships** — a promise must not be advertised
   before a system tracks it.
4. **D-02 is closed** (the console can resolve a refund dispute) — or you accept
   that disputes are handled by hand for merchant #1.
5. The verification protocol in `tasks.md` passes: `ruff`, the pytest suite against
   Postgres, both `next build`s, and a read-only production re-probe.

Everything else is quality work that can land after merchant #1 and is tracked
in `tasks.md`.
