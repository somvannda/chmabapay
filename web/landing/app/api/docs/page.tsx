import { resolveSiteUrl } from "@/lib/siteUrl";
import CodeBlock from "./CodeBlock";

// The guide. The exhaustive endpoint, convention, rate-limit and error tables live at
// `/api/docs/reference`; what stays here is the path to a first payment and the security
// model behind it. The split exists so a reader is not asked to scroll past 48 error rows
// to reach the quick start, and so the reference can be linked and indexed on its own.
function quickStartPanels(baseUrl: string) {
  return [
    {
      title: "Set up your key",
      lang: "bash",
      code: `# Dashboard → API keys → Create key. The raw value is shown once.
export CHMABA_KEY="ck_live_aB3cXyZ..."
export CHMABA_API="${baseUrl}"`,
    },
    {
      title: "Create a store",
      lang: "bash",
      code: `curl -X POST "$CHMABA_API/api/v1/stores" \\
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
      title: "Attach the PayWay link",
      lang: "bash",
      code: `curl -X PATCH "$CHMABA_API/api/v1/stores/st_your_store_id" \\
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
      title: "Create a payment",
      lang: "bash",
      code: `curl -X POST "$CHMABA_API/payments" \\
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
      title: "Wait for settlement",
      lang: "bash",
      code: `# Poll the payment, or simply wait for the payment.completed webhook:
curl "$CHMABA_API/api/v1/payments/kQ7mZx2VaRt9LpBnWc4YsH1u" \\
  -H "Authorization: Bearer $CHMABA_KEY"
# status: pending → paid, or expired / failed / superseded / reversed.

# If the code died before the customer paid, mint a replacement:
curl -X POST "$CHMABA_API/payments/kQ7mZx2VaRt9LpBnWc4YsH1u/reissue" \\
  -H "Authorization: Bearer $CHMABA_KEY"
# 201 mints a new payment; 200 returns the live replacement if one exists.`,
    },
    {
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
          <a className="landing-button-secondary" href="/api/docs/reference">
            Full API reference
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

function QuickStartPanels({ baseUrl }: { baseUrl: string }) {
  return (
    <section id="getting-started" className="docs-qs-section">
      <div className="docs-qs-head">
        <p className="docs-qs-eyebrow">Quick start</p>
        <h2 className="docs-qs-heading">Six copy-paste steps to a verified webhook.</h2>
        <p className="docs-qs-lede">
          From an empty store to a verified webhook. Every block is runnable as-is — replace
          your API key and the store id with your own values.
        </p>
        <div className="docs-qs-meta">
          <span className="docs-qs-chip">Copy-paste ready</span>
          <span className="docs-qs-chip">curl and Node.js</span>
          <span className="docs-qs-chip">No SDK required</span>
        </div>
      </div>
      <div className="docs-quick-steps">
        {quickStartPanels(baseUrl).map((panel) => (
          <CodeBlock
            key={panel.title}
            code={panel.code}
            lang={panel.lang}
            title={panel.title}
            langTag
            className="docs-quick-step"
          />
        ))}
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
            against the raw body, then process idempotently. The quick start above has a
            runnable Node.js verify function.
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
          <CodeBlock
            title="Request headers"
            lang="http"
            code={`Content-Type: application/json
X-ChmabaPay-Event: payment.completed
X-ChmabaPay-Signature: t=1757548800,v1=4f3…cd
User-Agent: ChmabaPay-Webhook/1.0`}
          />
        </div>
      </div>
    </section>
  );
}

function AuthenticationSection() {
  return (
    <section id="authentication" className="landing-section landing-surface">
      <div className="landing-shell">
        <div className="docs-section-head">
          <p className="landing-section-eyebrow">Before the first call</p>
          <h2 className="landing-section-title">Authentication and environments</h2>
        </div>
        <div className="docs-ref-grid">
          <article className="docs-ref-card">
            <div className="docs-endpoint-head">
              <div className="docs-endpoint-title-row">
                <h3 className="docs-endpoint-title">
                  One key authenticates every store
                </h3>
              </div>
              <p className="docs-endpoint-summary">
                Send the key as a Bearer token on every request. It covers every store on the
                account, so treat it as a password: the raw value is shown once at creation,
                only a hash is kept, and a key that leaks is revoked rather than edited.
              </p>
            </div>
            <CodeBlock
              title="Request headers"
              lang="http"
              code={`Authorization: Bearer ck_live_aB3cXyZ…
Content-Type: application/json`}
            />
            <p className="docs-ref-note">
              Create and revoke keys in the dashboard, under <strong>API keys</strong>. Key
              management, billing and the account profile are deliberately not part of this
              API: they are driven by a signed-in session, and an API key is refused there, so
              a leaked key cannot rewrite the account it was issued from.
            </p>
          </article>

          <article className="docs-ref-card">
            <div className="docs-endpoint-head">
              <div className="docs-endpoint-title-row">
                <h3 className="docs-endpoint-title">There is no sandbox</h3>
              </div>
              <p className="docs-endpoint-summary">
                Every key is a live key, and a payment minted with one moves real money into the
                store&apos;s own ABA PayWay account. There is no test mode to switch to yet.
              </p>
            </div>
            <ul className="docs-signature-list">
              <li>
                Point a store at your own ABA PayWay link before anything else, then mint a
                single payment of 0.01.
              </li>
              <li>
                Pay it from your own wallet and check the signature on the{" "}
                <code className="docs-ref-inline">payment.completed</code> delivery. That is the
                whole critical path, and it costs one cent.
              </li>
              <li>
                Only then point the store at the merchant&apos;s link. Until test-mode keys
                exist, keep anything you are still wiring up away from a real merchant&apos;s
                link.
              </li>
            </ul>
          </article>
        </div>
      </div>
    </section>
  );
}

function ReferenceTeaser() {
  return (
    <section id="endpoints" className="landing-section landing-surface">
      <div className="landing-shell">
        <div className="docs-section-head">
          <p className="landing-section-eyebrow">Beyond the quick start</p>
          <h2 className="landing-section-title">The rest of the API, in one reference.</h2>
        </div>
        <div className="docs-ref-grid">
          <article className="docs-ref-card docs-ref-card-wide">
            <p className="docs-endpoint-summary">
              Thirty-three endpoints across eight groups — stores, payments, reconciliation,
              KHQR, webhooks, reports, public stats and hosted checkout — plus the complete
              error catalogue, the rate limits and the conventions. The quick start above is the
              happy path; the reference is everything else.
            </p>
            <a className="landing-button-primary" href="/api/docs/reference">
              Browse the full reference
            </a>
          </article>
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
    "ChmabaPay API guide: authentication, a copy-paste quick start, and signed webhooks for KHQR payments over ABA PayWay.",
  // Without these the page inherits the landing page's card, so every shared link
  // to the API reference previewed as the marketing home page.
  alternates: { canonical: "/api/docs" },
  openGraph: {
    type: "article",
    title: "API Docs — ChmabaPay",
    description:
      "Get from an empty store to a verified webhook with a copy-paste quick start for KHQR payments over ABA PayWay.",
    url: "/api/docs",
    siteName: "ChmabaPay",
    images: [{ url: "/og-image.png", width: 1024, height: 1024, alt: "ChmabaPay" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "API Docs — ChmabaPay",
    description:
      "Get from an empty store to a verified webhook with a copy-paste quick start for KHQR payments over ABA PayWay.",
    images: ["/og-image.png"],
  },
};

export default function ApiDocsPage() {
  const baseUrl = resolveSiteUrl();

  return (
    <main>
      <DocsHeader baseUrl={baseUrl} />
      <AuthenticationSection />
      <QuickStartPanels baseUrl={baseUrl} />
      <SignatureSection />
      <ReferenceTeaser />
      <CtaSection />
    </main>
  );
}
