const baseApiUrl =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "https://api.chmabapay.com");

const integrationSteps = [
  {
    index: "01",
    title: "Create your workspace and get an API key",
    body: "Sign up, create a workspace, then generate a live API key from the developer settings. Every request is authenticated with your Bearer key.",
  },
  {
    index: "02",
    title: "Create a payment intent or checkout link",
    body: "Use the payments API to create a KHQR or ABA PayWay intent. You get back a payment URL, KHQR payload, and a tracking ID.",
  },
  {
    index: "03",
    title: "Listen for signed webhooks",
    body: "Subscribe to payment events and verify signatures on your server. ChmabaPay sends payment.completed, payment.scanned, payment.expired and payment.failed, each signed and safe to retry.",
  },
  {
    index: "04",
    title: "Reconcile with reports and export",
    body: "Use the ledger, settlement, and reconciliation endpoints to match payments against your orders and export CSV or JSON for finance.",
  },
] as const;

const endpointGroups = [
  {
    title: "API Keys",
    summary: "Manage workspace API keys. Live keys are prefixed `ck_live_`. One key authenticates every store in your workspace.",
    items: [
      { method: "GET", path: "/v1/keys", description: "List the API keys for your account" },
      { method: "POST", path: "/v1/keys", description: "Create a live workspace key. Pass name to label it. Limit depends on your plan (Free 1, Starter 3, Pro 10)." },
      { method: "POST", path: "/v1/keys/{key_id}/revoke", description: "Revoke a key immediately. Any request using that key will fail after this call." },
      { method: "POST", path: "/v1/keys/{key_id}/rotate", description: "Create a replacement key, then mark the old one for a 7-day deprecation grace window." },
    ],
  },
  {
    title: "Stores",
    summary: "Create and manage merchant stores. A store is the merchant, and each store maps to one ABA PayWay payment link — the only supported destination.",
    items: [
      { method: "POST", path: "/v1/stores", description: "Create a store. Pass name (max 120 chars), optional external_id, owner_name/owner_phone/owner_email, city, support_email, telegram_chat_id, redirect URLs, and link={raw_link, merchant_account_id, merchant_name}. ABA PayWay is the only supported destination. The branding fields (brand_color, logo_image_url, whitelabel_css) require the white-label entitlement." },
      { method: "GET", path: "/v1/stores", description: "List your stores (paginated, supports ?status=active|inactive)." },
      { method: "PATCH", path: "/v1/stores/{public_id}", description: "Update a store. Pass any of name, external_id, support_email, telegram_chat_id, redirect URLs, or link={raw_link, merchant_account_id, merchant_name}. The branding fields (brand_color, logo_image_url, whitelabel_css) require the white-label entitlement — otherwise 403 whitelabel_not_enabled." },
      { method: "GET", path: "/v1/stores/{public_id}", description: "Get a store, its payment link, active keys count, and last payment timestamp." },
      { method: "POST", path: "/v1/stores/{public_id}/disable", description: "Disable a store. New payments to this store_id → fail with 400." },
    ],
  },
  {
    title: "Payments",
    summary: "Create payment intents and track status. Every payment returns a KHQR string plus a hosted checkout URL, and exposes external_id (the store's external_id).",
    items: [
      { method: "POST", path: "/v1/payments", description: "Create a payment intent. Pass amount (float USD), reference_id (optional), metadata (optional), store=store_public_id or merchant=<store external_id>. 201 returns qr_string + hosted checkout_url." },
      { method: "GET", path: "/v1/payments", description: "List own payments (paginated, ?store=, ?merchant=<external_id>, ?status=pending|scanned|paid|expired|failed)." },
      { method: "GET", path: "/v1/payments/{public_id}", description: "Get a single payment plus its signed event timeline and paid_at/approved_at. Responses include external_id (the store's external_id)." },
    ],
  },
  {
    title: "Transactions",
    summary: "Bakong transaction search and polling. Supports MD5, SHA-256, short-hash, instruction ref, external ref, and 7-tier receipt cascade lookup.",
    items: [
      { method: "POST", path: "/v1/transactions/search", description: "Bakong search by raw criteria (search_type=hash|md5|short_hash|instruction_ref|external_ref, value, optional amount filter)." },
      { method: "POST", path: "/v1/transactions/poll", description: "Long-poll mode: blocks until a matching Bakong transaction is found or timeout=30s hits. Preferred over busy-loop GET." },
      { method: "GET", path: "/v1/transactions/md5/{md5_value}", description: "Lookup by QR MD5 32-char hash (from KHQR payload)." },
      { method: "GET", path: "/v1/transactions/hash/{hash_value}", description: "Lookup by full 64-char SHA-256 hash." },
      { method: "GET", path: "/v1/transactions/short-hash/{short_hash}", description: "Lookup by 8-ch truncated short hash (amount strongly recommended)." },
      { method: "GET", path: "/v1/transactions/instruction-ref/{ref}", description: "Lookup by ISO20022 instruction reference ID (EndToEndId / InstrId)." },
      { method: "GET", path: "/v1/transactions/external-ref/{ref}", description: "Lookup by merchant-supplied external_ref (typically a KHQR bill_number field)." },
      { method: "POST", path: "/v1/transactions/bulk", description: "Batch search. Body: array of {search_type, value, amount?} entries. Max 50 per request." },
      { method: "POST", path: "/v1/transactions/verify-receipt", description: "7-tier Bakong receipt cascade lookup (tier1 md5 → tier2 short → … → tier7 bank statement). Most authoritative for dispute resolution." },
      { method: "POST", path: "/v1/transactions/token/renew", description: "Renew Bakong Open API access token (used internally; exposed for diagnostics)." },
    ],
  },
  {
    title: "KHQR Generation",
    summary: "Build KHQR payloads from ABA PayWay links.",
    items: [
      { method: "POST", path: "/v1/khqr/from-link", description: "Build KHQR payload from an ABA PayWay share link (auto SSR-crawls the link for amount details)." },
      { method: "POST", path: "/v1/khqr/probe-aba-status", description: "Check ABA PayWay SSR page status for a slug — first-priority health check for link-based payments." },
    ],
  },
  {
    title: "Webhooks",
    summary: "Signed webhook endpoints with HMAC signatures, replay protection, and delivery attempt logs. Subscribe to event types or use wildcard `*`. Each event's `data` block contains both `store` and `merchant: { external_id }`; external_id is null for stores created without one.",
    items: [
      { method: "GET", path: "/v1/webhooks", description: "List your workspace webhook endpoints." },
      { method: "POST", path: "/v1/webhooks", description: "Register a workspace webhook endpoint. Pass url and an optional events=[] array ([\"*\"] for all). Capped by your plan. A signing secret is returned once at creation." },
      { method: "PATCH", path: "/v1/webhooks/{endpoint_id}", description: "Edit endpoint URL, events, status (active|disabled), or set enabled=true|false." },
      { method: "DELETE", path: "/v1/webhooks/{endpoint_id}", description: "Remove an endpoint and its delivery log. Use PATCH enabled=false to pause deliveries without losing history." },
      { method: "GET", path: "/v1/webhooks/{endpoint_id}/deliveries", description: "List delivery attempts (last 200, pagination). Shows http_status, attempts count, error preview, created/completed times." },
      { method: "POST", path: "/v1/webhooks/{endpoint_id}/test", description: "Trigger a synthetic test event immediately. Signs the payload with your signing secret so you can validate the whole pipeline." },
    ],
  },
  {
    title: "Billing & Plans",
    summary: "Plan management, invoices, and self-serve upgrades. Upgrades take effect immediately; prorating handled automatically on mid-cycle changes.",
    items: [
      { method: "GET", path: "/v1/billing/plans", description: "Public plans matrix (name, monthly fee, feature gates including csv_export_enabled booleans). No auth needed." },
      { method: "POST", path: "/v1/billing/change-plan", description: "Change plan. Pass plan_code=free|starter|pro. Upgrades take effect immediately with prorate handled." },
      { method: "GET", path: "/v1/billing/invoices", description: "List your plan invoices filtered by period_month=YYYY-MM. Session-only." },
      { method: "GET", path: "/v1/billing/invoices/{id}/khqr", description: "Generate a live KHQR payment to settle a plan invoice (dog-foods our own create_payment API — the payment itself targets ChmabaPay's HQ store). 201 returns payment_id + qr_string + checkout_url." },
    ],
  },
  {
    title: "Account",
    summary: "Profile management. Session or ck_ key auth.",
    items: [
      { method: "GET", path: "/v1/me", description: "Get your profile (name, email, account_type, is_platform_admin, plan, feature gates). Session or ck_ key auth." },
      { method: "PATCH", path: "/v1/me", description: "Update profile: name and display email (login email is Google-locked)." },
    ],
  },
  {
    title: "Admin (Platform Owner)",
    summary: "Platform Owner / is_platform_admin=True gated endpoints. Every mutation writes an AuditLog row.",
    items: [
      { method: "GET", path: "/v1/admin/overview", description: "Platform totals: accounts, stores (total + active), payments paid (all-time + this month), and MRR in cents." },
      { method: "GET", path: "/v1/admin/accounts", description: "Every account with plan, subscription status and store/payment counts. Filters: ?q= (email or name), ?page=, ?per_page= (max 100)." },
      { method: "GET", path: "/v1/admin/accounts/{account_id}", description: "One account's profile, plan, counts, stores, and last 24 invoices." },
      { method: "PATCH", path: "/v1/admin/accounts/{account_id}", description: "Grant or revoke an account entitlement. Currently white-label checkout branding (whitelabel_enabled)." },
      { method: "GET", path: "/v1/admin/plans", description: "Every plan, including retired and hidden ones." },
      { method: "PATCH", path: "/v1/admin/plans/{plan_id}", description: "Partial plan update (fee, included payments, store/key/webhook caps, feature flags). Only changed fields are written; the diff lands in AuditLog." },
      { method: "GET", path: "/v1/admin/invoices", description: "Plan invoices across every account. Filters: ?period_month=YYYY-MM, ?status=." },
    ],
  },
  {
    title: "Reports & Reconciliation",
    summary: "CSV and JSON payment exports. Available on every plan. Same filters apply to both formats.",
    items: [
      { method: "GET", path: "/v1/reports/payments.csv", description: "CSV export (StreamingResponse). Filters: ?from=YYYY-MM-DD, ?to=YYYY-MM-DD, ?store_id=st_…, ?merchant=<external_id>, ?statuses=pending,paid,expired,failed,scanned comma-sep." },
      { method: "GET", path: "/v1/reports/payments.json", description: "JSON reports. Same filters plus ?page= &per_page= (default 20, max 100). Returns {data, summary: {total_matching_rows, total_matching_paid_count, total_matching_paid_amount_cents_formatted, filters_applied}, pagination}." },
    ],
  },
  {
    title: "Public Hosted Checkout",
    summary: "No-auth, customer-facing pages. Branded checkout with QR scan UI and countdown timer.",
    items: [
      { method: "GET", path: "/pay/{public_id}", description: "Hosted checkout page. Shows the KHQR QR, amount, store name, and countdown timer to expiry, then redirects to the store's success/failure URL. Store logo, brand_color, and whitelabel_css render only when the account has the white-label entitlement." },
      { method: "GET", path: "/pay/{public_id}/status", description: "JSON status poller used by the checkout page's client JS. Returns {id, status, amount, paid_at, scan_detected_at}." },
    ],
  },
  {
    title: "Dev Rail (Sandbox)",
    summary: "enable_dev_gateway flag ON only. Synthetic scan/pay endpoints for CI integration tests and local development. Disabled in production.",
    items: [
      { method: "POST", path: "/_dev/payments/{public_id}/scan", description: "Synthetically mark payment.scanned_at. Simulates ABA customer scanning the QR without the Bakong API call." },
      { method: "POST", path: "/_dev/payments/{public_id}/pay", description: "Synthetic mark_paid (no Bakong). Use this in CI integration tests." },
    ],
  },
];

const qsPanels = [
  {
    num: 1,
    title: "Create Store",
    lang: "bash",
    code: `curl -X POST https://api.chmabapay.com/v1/stores \\
  -H "Authorization: Bearer <YOUR-SESSION-TOKEN>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Sokha Noodles",
    "external_id": "sokha-noodles",
    "city": "Phnom Penh",
    "owner_email": "sokha.noodles@example.kh",
    "support_email": "help@sokha-noodles.kh"
  }'`,
  },
  {
    num: 2,
    title: "Attach PayWay Link",
    lang: "bash",
    code: `curl -X PATCH https://api.chmabapay.com/v1/stores/st_sokha-store-xyz \\
  -H "Authorization: Bearer <YOUR-SESSION-TOKEN>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "link": {
      "raw_link": "https://link.payway.com.kh/ABAPAYpe518710Y",
      "merchant_account_id": "ABAPAYpe518710Y",
      "merchant_name": "Sokha Noodles"
    }
  }'
# ABA PayWay is the only supported destination.`,
  },
  {
    num: 3,
    title: "Create Workspace Key (shown ONCE!)",
    lang: "bash",
    code: `curl -X POST https://api.chmabapay.com/v1/keys \\
  -H "Authorization: Bearer <YOUR-SESSION-TOKEN>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Production"
  }'
# 201 response includes \`raw_key\` FIELD ONE TIME ONLY.
# One key authenticates every store in your workspace.
# Export it: export CHMABA_KEY="ck_live_aB3cXyZ..."`,
  },
  {
    num: 4,
    title: "Create Payment",
    lang: "bash",
    code: `curl -X POST https://api.chmabapay.com/v1/payments \\
  -H "Authorization: Bearer $CHMABA_KEY" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: ticket_42_$(date +%s)" \\
  -d '{
    "amount": 5.50,
    "reference_id": "ticket_42",
    "store": "st_sokha-store-xyz",
    "metadata": {
      "table": "A3",
      "server": "Dara"
    }
  }'
# 201 response: { "id": "pay_abc123", "external_id": "sokha-noodles",
#                 "qr_string": "000201...",
#                 "checkout_url": "https://chmabapay.com/pay/pay_abc123" }`,
  },
  {
    num: 5,
    title: "Poll Until Paid (long-poll, no busy-loop!)",
    lang: "bash",
    code: `# First, get the qr_md5_8 prefix from the KHQR payload or payments list:
export QR_MD5="8f3c1a98"

# Then block up to 30 seconds until Bakong matches:
curl -X POST https://api.chmabapay.com/v1/transactions/poll \\
  -H "Authorization: Bearer $CHMABA_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "search_type": "md5",
    "value": "'$QR_MD5'",
    "amount": 5.50,
    "timeout_seconds": 30
  }'
# 200 = match found, now call GET /v1/payments/pay_abc123 to confirm final status.`,
  },
  {
    num: 6,
    title: "Verify Webhook Signature (Node.js)",
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

function DocsHeader() {
  return (
    <section className="landing-section landing-surface docs-hero">
      <div className="landing-shell">
        <p className="landing-section-eyebrow">API Reference</p>
        <h1 className="landing-section-title">
          Integrate ChmabaPay in one afternoon.
          <span className="landing-title-line">Ship it by coffee break.</span>
        </h1>
        <p className="docs-hero-copy">
          Modern payment APIs for KHQR and ABA PayWay. Signed webhooks, idempotent endpoints,
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
          <code className="docs-quick-value">{baseApiUrl}</code>
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

function QuickStartPanels() {
  return (
    <section id="getting-started-panels" className="docs-qs-section">
      <h2 className="section-heading">Quick start: six copy-paste steps</h2>
      <p className="section-subheading">
        Individual vendor onboarding flow from first store to a paid webhook that Sokha the noodle vendor actually runs.
        Every snippet works verbatim — replace tokens (your-session-key, store id, and so on) with the values from your dashboard.
      </p>
      <div className="docs-qs-grid">
        {qsPanels.map((panel) => (
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
          {endpointGroups.map((group) => (
            <article key={group.title} className="docs-endpoint-card">
              <div className="docs-endpoint-head">
                <h3 className="docs-endpoint-title">{group.title}</h3>
                <p className="docs-endpoint-summary">{group.summary}</p>
              </div>
              <ul className="docs-endpoint-list">
                {group.items.map((item) => (
                  <li key={item.path} className="docs-endpoint-row">
                    <span className={`docs-method docs-method-${item.method}`}>{item.method}</span>
                    <code className="docs-path">{item.path}</code>
                    <p className="docs-description">{item.description}</p>
                  </li>
                ))}
              </ul>
            </article>
          ))}
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
            Every delivery includes a timestamp and an HMAC signature using your webhook secret. Reject
            stale timestamps, verify the payload, then process the event idempotently.
          </p>
          <ul className="docs-signature-list">
            <li>Verify signatures in constant time using the raw request body.</li>
            <li>Never process the same event ID twice.</li>
            <li>Return a 2xx status quickly; ChmabaPay retries on any other result.</li>
          </ul>
        </div>
        <div className="docs-signature-panel">
          <div className="docs-signature-panel-head">
            <span>Request headers</span>
          </div>
          <pre className="docs-signature-code">
{`X-ChmabaPay-Event: payment.approved
X-ChmabaPay-Timestamp: 1757548800
X-ChmabaPay-Signature: v1=4f3...cd
X-ChmabaPay-Delivery: delivery_28aF9pK`}
          </pre>
          <div className="docs-signature-panel-head">
            <span>Example: Node.js</span>
          </div>
          <pre className="docs-signature-code">
{`const { timingSafeEqual, createHmac } = require('crypto');
const rawBody = request.rawBody;
const { 'v1': signature } = parseSignatures(header);
const expected = createHmac('sha256', secret)
  .update(\`\${ts}.\${rawBody}\`)
  .digest('hex');
if (!timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) {
  throw new Error('bad signature');
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
};

export default function ApiDocsPage() {
  return (
    <main>
      <DocsHeader />
      <GettingStarted />
      <QuickStartPanels />
      <EndpointSection />
      <SignatureSection />
      <CtaSection />
    </main>
  );
}
