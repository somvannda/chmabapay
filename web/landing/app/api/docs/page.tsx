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
      { method: "POST", path: "/v1/keys", description: "Create a live key. Pass name to label it. The response includes raw_key once. Capped by your plan (Free 1, Starter 3, Pro 10)." },
      { method: "POST", path: "/v1/keys/{key_id}/revoke", description: "Revoke a key. Any request using it fails from this call on." },
      { method: "POST", path: "/v1/keys/{key_id}/rotate", description: "Create a replacement and suspend the old key immediately — the old key stops working at once, so deploy the new one first." },
    ],
  },
  {
    title: "Stores",
    summary:
      "Create and manage merchant stores. A store is the merchant, and each store maps to one ABA PayWay payment link — the only supported destination.",
    items: [
      { method: "POST", path: "/v1/stores", description: "Create a store. Pass name (max 120 chars), optional external_id, city, support_email, telegram_chat_id, redirect URLs, and link={raw_link, merchant_account_id, merchant_name}. The branding fields (brand_color, logo_image_url, whitelabel_css) require the white-label entitlement." },
      { method: "GET", path: "/v1/stores", description: "List every store on the account. Not paginated." },
      { method: "GET", path: "/v1/stores/{public_id}", description: "Get one store and its payment link." },
      { method: "PATCH", path: "/v1/stores/{public_id}", description: "Update a store. Pass any of name, external_id, city, support_email, telegram_chat_id, redirect URLs, or link={raw_link, merchant_account_id, merchant_name}. Branding fields require the white-label entitlement — otherwise 403 whitelabel_not_enabled." },
      { method: "PUT", path: "/v1/stores/{public_id}/link", description: "Attach or replace the store's ABA PayWay link. Promotes a draft store to active." },
      { method: "POST", path: "/v1/stores/{public_id}/disable", description: "Disable a store. New payments against it fail with 400 store_disabled, and no other write will touch it — a PATCH and a link attach are both refused while it is disabled." },
      { method: "POST", path: "/v1/stores/{public_id}/enable", description: "Re-enable a disabled store. Answers status active, or draft when the store has no payment link left — attach one with PUT /v1/stores/{public_id}/link and it becomes active. A no-op on a store that is not disabled." },
      { method: "POST", path: "/v1/stores/{public_id}/telegram/test", description: "Send a test message to the store's configured Telegram chat, so you can confirm the chat id is right." },
    ],
  },
  {
    title: "Payments",
    summary:
      "Create payments and track their status. Every payment returns the KHQR string plus a hosted checkout URL.",
    items: [
      { method: "POST", path: "/v1/payments", description: "Create a payment. Pass amount (decimal, in the store link's currency), optional reference_id, metadata, idempotency_key, and store=<store public id> or merchant=<store external_id>. hosted_qr defaults to on, which is what makes ABA issue a payable code; hosted_qr=false builds a code offline and is refused unless the platform can confirm it. 201 returns qr_string, checkout_url and expires_at." },
      { method: "GET", path: "/v1/payments", description: "List the account's payments, newest first. Filters: ?store=, ?merchant=<external_id>, ?status=, ?limit= (default 20). Statuses: pending, scanned, paid, expired, failed, superseded, reversed." },
      { method: "GET", path: "/v1/payments/{public_id}", description: "One payment: status, amount, currency, QR, checkout URL, created/expires/approved/paid timestamps, and reversal state." },
      { method: "POST", path: "/v1/payments/{public_id}/reissue", description: "Replace a dead code (expired, failed or superseded) with a fresh one and keep the lineage. 201 mints a new payment, 200 returns the live replacement already created for this one." },
      { method: "POST", path: "/v1/payments/{public_id}/reverse", description: "Record that a paid payment was refunded, with an optional note and a payment.reversed event. This is bookkeeping only — we never hold your funds, so send the money back to the customer yourself and record it here so your reports and quota stop counting the sale. 409 payment_not_paid or payment_already_reversed otherwise." },
    ],
  },
  {
    title: "Bakong Ledger Lookup",
    summary:
      "Look a transaction up in Bakong's own ledger. These require platform Bakong Open API credentials, which are not enabled, so they answer 503 bakong_not_configured today. The ABA PayWay path does not need them — use GET /v1/payments/{public_id} and webhooks instead.",
    items: [
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
    summary: "Build KHQR payloads from ABA PayWay links, and render them.",
    items: [
      { method: "POST", path: "/v1/khqr/from-link", description: "Build a KHQR payload from an ABA PayWay share link (crawls the link for the merchant and amount details)." },
      { method: "POST", path: "/v1/khqr/probe-aba-status", description: "Check an ABA PayWay slug's status — the first-priority health check for link-based payments." },
      { method: "POST", path: "/v1/khqr/payway/checkout", description: "Ask ABA to issue a hosted checkout session for a link, and return the code ABA will accept." },
      { method: "POST", path: "/v1/khqr/payway/status", description: "Ask ABA for a hosted session's outcome — approved, paid, and ABA's own receipt URL. This is the check that is authoritative on ABA's side." },
      { method: "GET", path: "/v1/khqr/render.svg", description: "Render a KHQR payload as SVG. Takes ecc and scale parameters." },
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
      { method: "GET", path: "/v1/webhooks/{endpoint_id}/deliveries", description: "Delivery attempt log: http_status, attempts, error preview, created/completed times." },
      { method: "POST", path: "/v1/webhooks/{endpoint_id}/test", description: "Send a synthetic signed event now, so you can validate the whole pipeline before going live." },
    ],
  },
  {
    title: "Billing & Plans",
    summary: "Plans, the current subscription, and invoices.",
    items: [
      { method: "GET", path: "/v1/billing/plans", description: "Public plans matrix (name, monthly fee, and feature gates such as csv_export_enabled). No auth needed." },
      { method: "GET", path: "/v1/billing/subscription", description: "The account's current subscription and plan. Session only." },
      { method: "POST", path: "/v1/billing/change-plan", description: "Change plan: plan_code=free|starter|pro. Applied immediately. No payment is collected and no proration is calculated — settle the difference outside ChmabaPay for now. Session only." },
      { method: "GET", path: "/v1/billing/invoices", description: "List the account's plan invoices, filtered by period_month=YYYY-MM. Session only." },
      { method: "GET", path: "/v1/billing/invoices/{id}/khqr", description: "Mint a live KHQR to settle a plan invoice, through our own payments API. 201 returns payment_id, qr_string and checkout_url. Session only." },
    ],
  },
  {
    title: "Account",
    summary: "The signed-in account's own profile.",
    items: [
      { method: "GET", path: "/v1/me", description: "Your profile: name, email, status, plan, feature gates, and terms acceptance. Session cookie only — an API key is not accepted here." },
      { method: "PATCH", path: "/v1/me", description: "Update name or email. An email already in use is rejected with 400 email_already_taken. Session cookie only." },
      { method: "POST", path: "/v1/me/terms", description: "Record acceptance of the merchant agreement. Send the version you displayed; a stale version is refused with 409. Session cookie only." },
    ],
  },
  {
    title: "Reports & Reconciliation",
    summary:
      "CSV and JSON payment exports. JSON is available on every plan; CSV requires the plan's csv_export_enabled flag and returns 403 without it.",
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
# 201: { "id": "pay_abc123", "status": "pending",
#        "qr_string": "000201...", "checkout_url": ".../pay/pay_abc123",
#        "expires_at": "..." }
# idempotency_key goes in the BODY — retry with the same value to get the
# same payment back instead of minting a second one.`,
    },
    {
      num: 5,
      title: "Wait for settlement",
      lang: "bash",
      code: `# Poll the payment, or simply wait for the payment.completed webhook:
curl "$CHMABA_API/v1/payments/pay_abc123" \\
  -H "Authorization: Bearer $CHMABA_KEY"
# status: pending → scanned → paid, or expired / failed.

# If the code died before the customer paid, mint a replacement:
curl -X POST "$CHMABA_API/v1/payments/pay_abc123/reissue" \\
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
              event name alone. Events: payment.completed, payment.scanned, payment.expired,
              payment.failed, payment.superseded, payment.reversed.
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
