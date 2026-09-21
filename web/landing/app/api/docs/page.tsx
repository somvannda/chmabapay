import { resolveSiteUrl } from "@/lib/siteUrl";

const integrationSteps = [
  {
    index: "01",
    title: "Create your workspace and get an API key",
    body: "Create a workspace, then generate a live API key in the dashboard. Every request is authenticated with that key as a Bearer token. The raw key is shown once.",
  },
  {
    index: "02",
    title: "Register a store and its payment link",
    body: "A store is the merchant, and each store holds one ABA PayWay share link. Money moves from the payer straight into that link's own bank account — ChmabaPay never holds funds.",
  },
  {
    index: "03",
    title: "Create a payment and show the QR",
    body: "Call the payments API with an amount and a reference id. ABA issues the KHQR, you get a hosted checkout URL, and the code is good for the window ABA sets — about 180 seconds.",
  },
  {
    index: "04",
    title: "Confirm from the webhook, reconcile from reports",
    body: "Verify the signature on payment.completed, then match it to your order by reference_id. CSV and JSON exports cover finance and disputes.",
  },
] as const;

const endpointGroups = [
  {
    title: "API Keys",
    summary:
      "Manage account API keys. Live keys are prefixed `ck_live_`, and one key authenticates every store on the account.",
    items: [
      { method: "GET", path: "/v1/keys", description: "List the account's API keys. The raw key is never returned again after creation." },
      { method: "POST", path: "/v1/keys", description: "Create a key. name is required (1–64 chars) — it is how the key is told apart in the list. The response includes raw_key once. Capped by your plan: 1 / 3 / 10 keys on Free, Starter and Pro by default. Refused with 403 until the merchant agreement is accepted." },
      { method: "POST", path: "/v1/keys/{key_id}/revoke", description: "Revoke a key by its numeric id. Any request using it fails from this call on." },
      { method: "POST", path: "/v1/keys/{key_id}/rotate", description: "Create a replacement and suspend the old key in the same call — the old key stops working at once, so deploy the new one first. The response is the new key, including raw_key once." },
    ],
  },
  {
    title: "Stores",
    summary:
      "Create and manage merchant stores. A store is the merchant, and each store maps to one ABA PayWay payment link — the only supported destination.",
    items: [
      { method: "POST", path: "/v1/stores", description: "Create a store. name (max 120 chars) is the only required field. Optional: external_id, city (defaults to \"Phnom Penh\", max 15 chars), support_email, telegram_chat_id, redirect_success_url, redirect_failure_url. Branding fields (brand_color, logo_image_url, whitelabel_css) require the white-label entitlement. Pass link={raw_link, merchant_account_id, merchant_name} to attach the destination in the same call, where only merchant_name is optional; leave it out and the store is created as a draft. 201 returns the store with id st_… ." },
      { method: "GET", path: "/v1/stores", description: "List every store on the account, wrapped as {data: [...]}. Not paginated. Each store carries is_internal — true for the platform's own store, from which plan fees are collected; it is exempt from quota and its takings are not counted as merchant volume." },
      { method: "GET", path: "/v1/stores/{public_id}", description: "Get one store and its payment link." },
      { method: "PATCH", path: "/v1/stores/{public_id}", description: "Update a store. Pass any of name, external_id, city, support_email, telegram_chat_id, redirect URLs, or link={raw_link, merchant_account_id, merchant_name}. Branding fields require the white-label entitlement — otherwise 403 whitelabel_not_enabled." },
      { method: "PUT", path: "/v1/stores/{public_id}", description: "Alias of PATCH /v1/stores/{public_id}: update a store with a full-body PUT. Same fields and validation, with the same 403 whitelabel_not_enabled when branding fields are sent without the entitlement." },
      { method: "PUT", path: "/v1/stores/{public_id}/link", description: "Attach or replace the store's ABA PayWay link. Requires raw_link and merchant_account_id. Promotes a draft store to active." },
      { method: "POST", path: "/v1/stores/{public_id}/disable", description: "Disable a store. New payments against it fail with 400 store_disabled, and no other write will touch it — a PATCH and a link attach are both refused while it is disabled." },
      { method: "POST", path: "/v1/stores/{public_id}/enable", description: "Re-enable a disabled store. Answers status active, or draft when the store has no payment link left — attach one with PUT /v1/stores/{public_id}/link and it becomes active. A no-op on a store that is not disabled." },
      { method: "POST", path: "/v1/stores/{public_id}/telegram/test", description: "Send a test message to the store's configured Telegram chat, so you can confirm the chat id is right." },
    ],
  },
  {
    title: "Payments",
    summary:
      "Create payments and track their status. The create call returns the KHQR string and a hosted checkout URL; reading a payment back returns the QR string without the URL, and the list returns neither.",
    items: [
      { method: "POST", path: "/v1/payments", description: "Create a payment. Pass amount (a positive decimal with at most two places, in the store link's currency), optional reference_id, metadata and idempotency_key, plus store=<store public id> or merchant=<store external_id>. hosted_qr is left out by default, which means auto: ABA issues the code whenever the store's link is an ABA PayWay link, because a code we build ourselves for one carries no ABA transaction and can never be confirmed. hosted_qr=false builds a code offline and is refused on a live request unless the deployment can confirm one. 201 returns qr_string, checkout_url and expires_at." },
      { method: "GET", path: "/v1/payments", description: "List the account's payments, newest first. Filters: ?store=, ?merchant=<external_id>, ?status=, ?limit= (default 20, max 100) and ?offset= (default 0, skips that many newest rows so you can page). A negative offset is a 422. Older payments are still listed after their QR dies, so filter ?status=paid for a settlement feed. Statuses: pending, scanned, paid, expired, failed, superseded, reversed." },
      { method: "GET", path: "/v1/payments/{public_id}", description: "One payment: status, amount, currency, QR string, created/expires/approved/paid timestamps, the ABA reference once settled, and reversal state. checkout_url is null here — it is built by the create call, and the id inside it is this payment's own id, so /pay/{public_id} is the same page. Amounts are returned as decimal strings; summary totals elsewhere are integer *amount_cents." },
      { method: "POST", path: "/v1/payments/{public_id}/reissue", description: "Replace a dead code with a fresh one and keep the lineage. Only an expired or failed payment can be replaced: paid answers 409 payment_already_paid, reversed answers 409 payment_reversed, and anything still live — pending, scanned, or a superseded code that already has a replacement — answers 409 payment_not_expired. 201 mints a new payment, 200 returns the live replacement already created for this one." },
      { method: "POST", path: "/v1/payments/{public_id}/reverse", description: "Record that a paid payment was refunded, with an optional note and a payment.reversed event. This is bookkeeping only — we never hold your funds, so send the money back to the customer yourself and record it here so your reports and quota stop counting the sale. 409 payment_not_paid or payment_already_reversed otherwise." },
    ],
  },
  {
    title: "Payment Reconciliation",
    summary:
      "Re-check a payment you created, and have the platform act on what it finds. These are the endpoints to use for confirmation. The Bakong ledger lookups below are a different thing and are not switched on.",
    items: [
      { method: "GET", path: "/v1/transactions/check-status/{payment_public_id}", description: "Authoritative status for one of your payments. Query: prefer_aba_page (default true), aba_slug_hint, mark_paid (default true — when a source reports PAID, the payment row transitions too). Returns status (PAID/PENDING/FAILED/UNKNOWN), source (payway_hosted_checkout when an ABA-hosted session answered, bakong_open_api when the Bakong ledger matched, or null when no source could be reached), matched_amount, transitioned_to_paid, the signals behind the answer, and error when a source could not be reached. 404 payment_not_found if the id is not on your account. Works without Bakong credentials for a payment that has a hosted ABA session." },
      { method: "POST", path: "/v1/transactions/verify-payment/{payment_public_id}", description: "The same reconciliation, returning the underlying Bakong transaction shape instead of a status object. Query: use_hash (default false — forces the Bakong path), prefer_aba_page (default true), aba_slug_hint. When nothing confirms it yet it answers 200 with found:false rather than a 404 — there is simply no transaction to return yet. Bakong credentials are required only for a payment with no hosted session to ask." },
    ],
  },
  {
    title: "Bakong Ledger Lookup",
    unavailable: true,
    summary:
      "Look a transaction up in Bakong's own ledger, and renew the token those lookups need. These require platform Bakong Open API credentials, which are not configured, so the ledger lookups here answer 503 bakong_not_configured today. They are listed for completeness, not for use — reconcile with the two endpoints above instead.",
    items: [
      { method: "POST", path: "/v1/transactions/token/renew", description: "Ask NBC for a fresh short-lived Bakong JWT, used by the ledger lookups below. Body optional {email} — the registered Bakong developer email; 400 email_required when neither the body nor the deployment supplies one, and 400 bakong_error when NBC refuses. 200 returns {token, message}, with token null when one could not be issued." },
      { method: "POST", path: "/v1/transactions/search", description: "Bakong search by identifier (search_type=hash|md5|short_hash|instruction_ref|external_ref, value, optional amount filter)." },
      { method: "POST", path: "/v1/transactions/poll", description: "Poll Bakong until the transaction succeeds. Interval and attempts are set with interval_seconds (default 2) and max_attempts (default 60) — there is no timeout_seconds field." },
      { method: "GET", path: "/v1/transactions/md5/{md5_value}", description: "Lookup by 32-char QR MD5." },
      { method: "GET", path: "/v1/transactions/hash/{hash_value}", description: "Lookup by full 64-char SHA-256 hash." },
      { method: "GET", path: "/v1/transactions/short-hash/{short_hash}", description: "Lookup by 8-char truncated short hash. An amount filter is strongly recommended." },
      { method: "GET", path: "/v1/transactions/instruction-ref/{ref}", description: "Lookup by ISO 20022 instruction reference (EndToEndId / InstrId)." },
      { method: "GET", path: "/v1/transactions/external-ref/{ref}", description: "Lookup by merchant-supplied external_ref, typically a KHQR bill_number." },
      { method: "POST", path: "/v1/transactions/bulk", description: "Batch search. Body: {search_type, values: [<string>, …]} — one identifier type, up to 100 values." },
      { method: "POST", path: "/v1/transactions/verify-receipt", description: "Receipt cascade lookup (md5 → short hash → … → bank statement)." },
    ],
  },
  {
    title: "KHQR Generation",
    summary:
      "Build KHQR payloads from ABA PayWay links, and render them. Only the PayWay routes here can confirm a payment: a code we build ourselves has no ABA transaction behind it, so no wallet will settle it and there is nothing to poll.",
    items: [
      { method: "POST", path: "/v1/khqr/from-link", description: "Build a KHQR payload from an ABA PayWay share link, crawling the link for the merchant and amount details. Useful for display; the code it returns is not payable, because ABA has no record of it." },
      { method: "POST", path: "/v1/khqr/probe-aba-status", description: "Best-effort status probe on a PayWay page. Query params: slug_or_url (required), bill_number, reference_id, expected_amount_usd. It re-fetches ABA's public SSR page and looks for PAID indicators, so it is advisory only — a layout change or a cached page can make it wrong in either direction. POST /v1/khqr/payway/status is the authoritative answer for a hosted session." },
      { method: "POST", path: "/v1/khqr/payway/checkout", description: "Ask ABA to issue a hosted checkout session for a link, and return the code ABA will accept — that is what makes it payable as well as trackable." },
      { method: "POST", path: "/v1/khqr/payway/status", description: "Ask ABA for a hosted session's outcome — approved, paid, and ABA's own receipt URL. This is the only check that is authoritative on ABA's side." },
      { method: "GET", path: "/v1/khqr/render.svg", description: "Render a KHQR payload as SVG. Pass payload (the raw QR string, 8–1500 chars); optional scale (default 8) and ecc (default h). Public and stateless — it encodes what you give it and stores nothing." },
    ],
  },
  {
    title: "Webhooks",
    summary:
      "Signed webhook endpoints with HMAC signatures, replay protection, and delivery logs. Subscribe to specific events or use the wildcard `*`. Each event's data block carries both `store` and `merchant: { external_id }`; external_id is null for stores created without one.",
    items: [
      { method: "GET", path: "/v1/webhooks", description: "List the account's webhook endpoints." },
      { method: "POST", path: "/v1/webhooks", description: "Register an endpoint. Pass url and an optional events=[] array ([\"*\"] for all). The response includes signing_secret once — store it, it cannot be read back. Capped by your plan." },
      { method: "PATCH", path: "/v1/webhooks/{endpoint_id}", description: "Change the url, events, status (active|disabled), or set enabled=true|false." },
      { method: "DELETE", path: "/v1/webhooks/{endpoint_id}", description: "Remove an endpoint and its delivery log. Use PATCH enabled=false to pause deliveries without losing history." },
      { method: "POST", path: "/v1/webhooks/{endpoint_id}/rotate-secret", description: "Issue a new signing secret and return it once. Deliveries signed with the previous secret will fail verification." },
      { method: "GET", path: "/v1/webhooks/{endpoint_id}/deliveries", description: "Delivery attempt log, newest first: attempt_count, http_status, response_body_preview, and created/completed times. ?limit= (default 200, max 500) and ?page=." },
      { method: "POST", path: "/v1/webhooks/{endpoint_id}/test", description: "Send a synthetic signed event now, so you can validate the whole pipeline before going live." },
    ],
  },
  {
    title: "Billing & Plans",
    summary: "Plans, the current subscription, and invoices.",
    items: [
      { method: "GET", path: "/v1/billing/plans", description: "Public plans matrix (name, monthly fee, and per-plan limits such as max_stores and max_keys_per_account). No auth needed." },
      { method: "GET", path: "/v1/billing/subscription", description: "The account's current subscription and plan. Session only." },
      { method: "POST", path: "/v1/billing/change-plan", description: "Change plan with plan_code=free|starter|pro. A move onto a free tier applies at once. A paid tier is bought rather than granted: the response comes back with payment_required=true, a pending subscription and the invoice for the period, and the plan activates when that invoice is paid — the pending subscription grants nothing until then. One invoice per account per period, so a 409 period_already_invoiced means this month is already billed. 400 plan_unchanged if it is the plan you are on, 404 plan_not_found, 400 plan_not_available if it is retired. Session only." },
      { method: "GET", path: "/v1/billing/invoices", description: "List the account's plan invoices, filtered by period_month=YYYY-MM. Session only." },
      { method: "GET", path: "/v1/billing/invoices/{id}/khqr", description: "Mint a live KHQR to settle a plan invoice, through our own payments API. 201 returns payment_id, qr_string and checkout_url. 400 invoice_already_paid, 404 invoice_not_found, and 503 billing_not_open when the platform's own payment destination is not configured yet. Session only." },
    ],
  },
  {
    title: "Account",
    summary: "The signed-in account's own profile. Everything here is session-cookie only — an API key is not accepted.",
    items: [
      { method: "GET", path: "/v1/me", description: "Your profile: id, email, name, status, whitelabel_enabled, is_platform_admin, has_password, created_at, updated_at, terms acceptance (terms_accepted_at / terms_accepted_version) and the version of the agreement published right now (terms_required_version), plus auth_method (password vs Google). There is no plan field here." },
      { method: "PATCH", path: "/v1/me", description: "Update name. email is deliberately not accepted here — the schema forbids unknown fields, so sending it is a 422, and moving an address is a verified operation instead (POST /v1/me/email)." },
      { method: "PATCH", path: "/v1/account", description: "Alias of PATCH /v1/me: update the account name. Only name is accepted; any other field is a 422 because the schema forbids unknown fields." },
      { method: "POST", path: "/v1/me/password", description: "Rotate the account's password. Body {current_password, new_password}. 401 invalid_password when the current one is wrong, 400 password_unchanged when the new one matches the old, and 409 no_password_set for a Google-only account that has no password to rotate. Session only." },
      { method: "DELETE", path: "/v1/me", description: "Close and anonymise the account. Body {confirm_email, current_password} — the account's own address echoed back, plus the password when the account has one. 400 confirm_email_does_not_match, 401 invalid_password, and 409 platform_admin_cannot_self_delete for the console's own account. Irreversible: it suspends the account and revokes every key, store and webhook." },
      { method: "POST", path: "/v1/me/email", description: "Move the account's email. Requires current_password for an account that has one; an account created through Google has no password to prove ownership with, so it is refused and pointed at support. 400 email_already_taken if the address is in use." },
      { method: "POST", path: "/v1/me/terms", description: "Record acceptance of the merchant agreement. Send the version you displayed; a stale one is refused with 409 terms_version_superseded. Re-accepting the current version is a no-op." },
    ],
  },
  {
    title: "Reports & Reconciliation",
    summary:
      "CSV and JSON payment exports, available on every plan. CSV streams every matching payment; JSON adds summary totals and pagination.",
    items: [
      { method: "GET", path: "/v1/reports/payments.csv", description: "Streaming CSV export. Filters: ?from=YYYY-MM-DD, ?to=YYYY-MM-DD, ?store_id=st_…, ?merchant=<external_id>, ?statuses=comma,separated." },
      { method: "GET", path: "/v1/reports/payments.json", description: "JSON export. Same filters plus ?page= and ?per_page= (default 20, max 100). Returns {data, summary: {total_matching_rows, total_matching_paid_count, total_matching_paid_amount_cents, total_matching_paid_amount_formatted, total_matching_reversed_count, total_matching_reversed_amount_cents, filters_applied}, pagination}. The paid totals count only status=paid, so the reversed totals are what account for the difference." },
    ],
  },
  {
    title: "Public Hosted Checkout",
    summary: "No-auth, customer-facing pages. Branded checkout with a QR and a countdown to expiry.",
    items: [
      { method: "GET", path: "/pay/{public_id}", description: "Hosted checkout page: the QR, amount, store name, and a countdown to expiry, then a redirect to the store's success or failure URL. Store logo, brand_color and whitelabel_css render only when the account has the white-label entitlement." },
      { method: "GET", path: "/pay/{public_id}/status", description: "Status poller used by the checkout page. Returns {id, status, amount, currency, store_name, store, created_at, expires_at}." },
      { method: "GET", path: "/pay/{public_id}/qr.svg", description: "The QR as SVG. Returns 410 once the code is dead — expired, failed, superseded or reversed — so the page stops offering a code that cannot be paid." },
    ],
  },
] as const;

function quickStartPanels(baseUrl: string) {
  return [
    {
      num: 1,
      title: "Set up your key",
      lang: "bash",
      code: `# Dashboard → API keys → Create key. The raw value is shown once.
export CHMABA_KEY="ck_live_aB3cXyZ..."
export CHMABA_API="${baseUrl}"`,
    },
    {
      num: 2,
      title: "Create a store",
      lang: "bash",
      code: `curl -X POST "$CHMABA_API/v1/stores" \\
  -H "Authorization: Bearer $CHMABA_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Demo Store",
    "external_id": "demo-store",
    "city": "Phnom Penh"
  }'
# 201 returns the store id (st_…) and status "draft".`,
    },
    {
      num: 3,
      title: "Attach the PayWay link",
      lang: "bash",
      code: `curl -X PATCH "$CHMABA_API/v1/stores/st_your_store_id" \\
  -H "Authorization: Bearer $CHMABA_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "link": {
      "raw_link": "https://link.payway.com.kh/ABAPAYpe518710Y",
      "merchant_account_id": "ABAPAYpe518710Y",
      "merchant_name": "Demo Store"
    }
  }'
# The store becomes "active". ABA PayWay is the only supported destination.`,
    },
    {
      num: 4,
      title: "Create a payment",
      lang: "bash",
      code: `curl -X POST "$CHMABA_API/v1/payments" \\
  -H "Authorization: Bearer $CHMABA_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "amount": 5.50,
    "reference_id": "ticket_42",
    "idempotency_key": "ticket_42",
    "store": "st_your_store_id",
    "metadata": { "table": "A3" }
  }'
# 201: { "id": "kQ7mZx2VaRt9LpBnWc4YsH1u", "status": "pending",
#        "qr_string": "000201...", "checkout_url": ".../pay/kQ7mZx2VaRt9LpBnWc4YsH1u",
#        "expires_at": "..." }
# Payment ids carry no prefix — only stores are st_… — so do not strip one.
# idempotency_key goes in the BODY — retry with the same value to get the
# same payment back instead of minting a second one.`,
    },
    {
      num: 5,
      title: "Wait for settlement",
      lang: "bash",
      code: `# Poll the payment, or simply wait for the payment.completed webhook:
curl "$CHMABA_API/v1/payments/kQ7mZx2VaRt9LpBnWc4YsH1u" \\
  -H "Authorization: Bearer $CHMABA_KEY"
# status: pending → paid, or expired / failed / superseded / reversed.

# If the code died before the customer paid, mint a replacement:
curl -X POST "$CHMABA_API/v1/payments/kQ7mZx2VaRt9LpBnWc4YsH1u/reissue" \\
  -H "Authorization: Bearer $CHMABA_KEY"
# 201 mints a new payment; 200 returns the live replacement if one exists.`,
    },
    {
      num: 6,
      title: "Verify the webhook signature (Node.js)",
      lang: "javascript",
      code: `// Server-side only. Never expose the signing secret to browser code.
import { createHmac, timingSafeEqual } from "node:crypto";

const SECRET = process.env.CHMABA_WEBHOOK_SECRET || "whsec_your-signing-secret";

function verifyChmabaSignature(rawBody, signatureHeader) {
  const [tPart, sigPart] = signatureHeader.split(",");
  const t = tPart.split("=")[1];
  const v1 = sigPart.split("=")[1];
  // Reject replay attacks (5 minute drift max)
  if (Math.floor(Date.now() / 1000) - Number(t) > 300) {
    throw new Error("Signature timestamp expired");
  }
  const signedPayload = \`\${t}.\${rawBody}\`;
  const expected = createHmac("sha256", SECRET)
    .update(signedPayload, "utf8")
    .digest("hex");
  const a = Buffer.from(v1, "hex");
  const b = Buffer.from(expected, "hex");
  if (!timingSafeEqual(a, b)) {
    throw new Error("Invalid webhook signature");
  }
  return true;
}`,
    },
  ];
}

function DocsHeader({ baseUrl }: { baseUrl: string }) {
  return (
    <section className="landing-section landing-surface docs-hero">
      <div className="landing-shell">
        <p className="landing-section-eyebrow">API Reference</p>
        <h1 className="landing-section-title">
          Integrate ChmabaPay in one afternoon.
          <span className="landing-title-line">Ship it by coffee break.</span>
        </h1>
        <p className="docs-hero-copy">
          Payment APIs for KHQR and ABA PayWay. Signed webhooks, idempotent endpoints,
          and a flat response shape that is easy to test and easier to maintain.
        </p>
        <div className="landing-button-row docs-hero-actions">
          <a className="landing-button-primary" href="#getting-started">
            Get started
          </a>
          <a className="landing-button-secondary" href="#endpoints">
            Browse endpoints
          </a>
        </div>
        <div className="docs-quick-box">
          <div className="docs-quick-label">Base URL</div>
          <code className="docs-quick-value">{baseUrl}</code>
          <div className="docs-quick-label docs-quick-label-auth">Authorization</div>
          <code className="docs-quick-value">Authorization: Bearer ck_live_...</code>
        </div>
      </div>
    </section>
  );
}

function GettingStarted() {
  return (
    <section id="getting-started" className="landing-section">
      <div className="landing-shell">
        <div className="docs-section-head">
          <p className="landing-section-eyebrow">From zero to first payment</p>
          <h2 className="landing-section-title">Getting started</h2>
        </div>
        <div className="landing-feature-grid">
          {integrationSteps.map((item) => (
            <article key={item.index} className="landing-feature-card">
              <div className="landing-feature-top">
                <div className="landing-feature-icon landing-feature-icon-setup" />
                <div className="landing-feature-index">{item.index}</div>
              </div>
              <h3 className="landing-feature-title">{item.title}</h3>
              <p className="landing-feature-copy">{item.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function QuickStartPanels({ baseUrl }: { baseUrl: string }) {
  return (
    <section id="getting-started-panels" className="docs-qs-section">
      <h2 className="section-heading">Quick start: six copy-paste steps</h2>
      <p className="section-subheading">
        From an empty store to a verified webhook. Every block is runnable as-is — replace your
        API key and the store id with your own values.
      </p>
      <div className="docs-qs-grid">
        {quickStartPanels(baseUrl).map((panel) => (
          <div className="docs-qs-block" key={panel.num}>
            <div className="docs-signature-panel-head">
              <span className="docs-qs-num">({panel.num})</span>
              <h4 className="docs-label docs-qs-title">{panel.title}</h4>
            </div>
            <pre className="docs-signature-code docs-qs-code"><code className={panel.lang}>{panel.code}</code></pre>
          </div>
        ))}
      </div>
    </section>
  );
}

function EndpointSection() {
  return (
    <section id="endpoints" className="landing-section landing-surface">
      <div className="landing-shell">
        <div className="docs-section-head">
          <p className="landing-section-eyebrow">Core REST surface</p>
          <h2 className="landing-section-title">Endpoints you&rsquo;ll actually use.</h2>
        </div>
        <div className="docs-endpoint-grid">
          {endpointGroups.map((group) => {
            // Only one group can be unavailable today, and it is the one whose
            // routes cannot answer without platform credentials. Rendering it with
            // the same weight as the working ones would be the documentation
            // equivalent of the 503 it returns.
            const isUnavailable = "unavailable" in group && group.unavailable;
            return (
              <article
                key={group.title}
                className={`docs-endpoint-card${isUnavailable ? " is-unavailable" : ""}`}
              >
                <div className="docs-endpoint-head">
                  <div className="docs-endpoint-title-row">
                    <h3 className="docs-endpoint-title">{group.title}</h3>
                    {isUnavailable ? (
                      <span className="docs-endpoint-badge">Not available</span>
                    ) : null}
                  </div>
                  <p className="docs-endpoint-summary">{group.summary}</p>
                </div>
                <ul className="docs-endpoint-list">
                  {group.items.map((item) => (
                    <li key={`${item.method} ${item.path}`} className="docs-endpoint-row">
                      <span className={`docs-method docs-method-${item.method}`}>{item.method}</span>
                      <code className="docs-path">{item.path}</code>
                      <p className="docs-description">{item.description}</p>
                    </li>
                  ))}
                </ul>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function SignatureSection() {
  return (
    <section className="landing-section">
      <div className="landing-shell docs-signature-shell">
        <div className="docs-signature-left">
          <p className="landing-section-eyebrow">Webhook security</p>
          <h2 className="landing-section-title">Signed events, flat payloads, honest retries.</h2>
          <p className="docs-hero-copy">
            Every delivery carries a timestamp and an HMAC-SHA256 signature over
            {" "}<code>{"{timestamp}.{rawBody}"}</code> using your webhook secret, in the
            single <code>X-ChmabaPay-Signature</code> header. Reject stale timestamps, verify
            against the raw body, then process idempotently.
          </p>
          <ul className="docs-signature-list">
            <li>Verify in constant time, on the raw request body — not a re-serialised copy.</li>
            <li>Never process the same event id twice; ids are stable across retries.</li>
            <li>Return 2xx quickly. Anything else is retried with backoff, up to 8 attempts.</li>
            <li>
              Branch on <code>data.payment.status</code> and <code>financial</code>, not on the
              event name alone. Events: payment.completed, payment.expired,
              payment.superseded, payment.reversed.
            </li>
          </ul>
        </div>
        <div className="docs-signature-panel">
          <div className="docs-signature-panel-head">
            <span>Request headers</span>
          </div>
          <pre className="docs-signature-code">
{`Content-Type: application/json
X-ChmabaPay-Event: payment.completed
X-ChmabaPay-Signature: t=1757548800,v1=4f3…cd
User-Agent: ChmabaPay-Webhook/1.0`}
          </pre>
          <div className="docs-signature-panel-head">
            <span>Example: Node.js</span>
          </div>
          <pre className="docs-signature-code">
{`const { createHmac, timingSafeEqual } = require('crypto');

// rawBody must be the exact bytes received, not a re-serialised object.
function verify(rawBody, header, secret) {
  const [tPart, v1Part] = header.split(',');
  const ts = tPart.split('=')[1];
  const v1 = v1Part.split('=')[1];

  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false;

  const expected = createHmac('sha256', secret)
    .update(\`\${ts}.\${rawBody}\`, 'utf8')
    .digest('hex');

  return timingSafeEqual(Buffer.from(v1, 'hex'), Buffer.from(expected, 'hex'));
}`}
          </pre>
        </div>
      </div>
    </section>
  );
}

function CtaSection() {
  return (
    <section className="landing-section landing-surface">
      <div className="landing-shell">
        <div className="landing-cta-card docs-cta-card">
          <div className="landing-cta-eyebrow">Ready when you are</div>
          <h3 className="landing-cta-title">
            Your first payment integration starts here.
          </h3>
          <p className="landing-cta-copy">
            Create a workspace, generate an API key, and run the copy/paste flow from this page. You
            can go live the same day.
          </p>
          <a className="landing-button-primary landing-cta-button" href="/auth/google/login">
            Open your workspace
          </a>
        </div>
      </div>
    </section>
  );
}

export const metadata = {
  title: "API Docs — ChmabaPay",
  description:
    "ChmabaPay API reference for KHQR, ABA PayWay, signed webhooks, and reconciliation endpoints.",
  // Without these the page inherits the landing page's card, so every shared link
  // to the API reference previewed as the marketing home page.
  alternates: { canonical: "/api/docs" },
  openGraph: {
    type: "article",
    title: "API Docs — ChmabaPay",
    description:
      "Endpoints, signed webhooks and a copy-paste quick start for KHQR payments over ABA PayWay.",
    url: "/api/docs",
    siteName: "ChmabaPay",
    images: [{ url: "/og-image.png", width: 1024, height: 1024, alt: "ChmabaPay" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "API Docs — ChmabaPay",
    description:
      "Endpoints, signed webhooks and a copy-paste quick start for KHQR payments over ABA PayWay.",
    images: ["/og-image.png"],
  },
};

export default function ApiDocsPage() {
  const baseUrl = resolveSiteUrl();

  return (
    <main>
      <DocsHeader baseUrl={baseUrl} />
      <GettingStarted />
      <QuickStartPanels baseUrl={baseUrl} />
      <EndpointSection />
      <SignatureSection />
      <CtaSection />
    </main>
  );
}
