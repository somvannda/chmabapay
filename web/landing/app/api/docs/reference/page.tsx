import { resolveSiteUrl } from "@/lib/siteUrl";
import CodeBlock from "../CodeBlock";
import { conventions, errorGroups, rateLimits } from "../reference";

// The complete surface, split off from the guide at `/api/docs`. The onboarding flow
// (quick start, auth) stays there; a reader who has shipped a first payment lands here
// for the endpoints they have not touched and the refusals they have not met.
//
// The rows below stay in this file rather than in `./reference.ts` for the same reason
// the guide keeps its own JSX: `./reference.ts` is read as data by
// `tests/test_api_docs_errors.py`, and the endpoint rows are read as JSX by
// `tests/test_openapi_schema.py`. Both tests follow the content, not the route.
const endpointGroups = [
  {
    title: "Stores",
    summary:
      "Create and manage merchant stores. A store is the merchant, and each store maps to one ABA PayWay payment link — the only supported destination.",
    items: [
      { method: "POST", path: "/api/v1/stores", description: "Create a store. name (max 120 chars) is the only required field. Optional: external_id, city (defaults to \"Phnom Penh\", max 15 chars), support_email, telegram_chat_id, redirect_success_url, redirect_failure_url. Branding fields (brand_color, logo_image_url, whitelabel_css) require the white-label entitlement. Pass link={raw_link, merchant_account_id, merchant_name} to attach the destination in the same call, where only merchant_name is optional; leave it out and the store is created as a draft. 201 returns the store with id st_… ." },
      { method: "GET", path: "/api/v1/stores", description: "List every store on the account, wrapped as {data: [...]}. Not paginated. Each store carries is_internal — true for the platform's own store, from which plan fees are collected; it is exempt from quota and its takings are not counted as merchant volume." },
      { method: "GET", path: "/api/v1/stores/{public_id}", description: "Get one store and its payment link." },
      { method: "PATCH", path: "/api/v1/stores/{public_id}", description: "Update a store. Pass any of name, external_id, city, support_email, telegram_chat_id, redirect URLs, or link={raw_link, merchant_account_id, merchant_name}. Branding fields require the white-label entitlement — otherwise 403 whitelabel_not_enabled." },
      { method: "PUT", path: "/api/v1/stores/{public_id}", description: "Alias of PATCH /api/v1/stores/{public_id}: update a store with a full-body PUT. Same fields and validation, with the same 403 whitelabel_not_enabled when branding fields are sent without the entitlement." },
      { method: "PUT", path: "/api/v1/stores/{public_id}/link", description: "Attach or replace the store's ABA PayWay link. Requires raw_link and merchant_account_id. Promotes a draft store to active." },
      { method: "POST", path: "/api/v1/stores/{public_id}/disable", description: "Disable a store. New payments against it fail with 400 store_disabled, and no other write will touch it — a PATCH and a link attach are both refused while it is disabled." },
      { method: "POST", path: "/api/v1/stores/{public_id}/enable", description: "Re-enable a disabled store. Answers status active, or draft when the store has no payment link left — attach one with PUT /api/v1/stores/{public_id}/link and it becomes active. A no-op on a store that is not disabled." },
      { method: "POST", path: "/api/v1/stores/{public_id}/activate", description: "Bring back a store the platform is holding for billing (billing_suspended_at, set when a downgrade leaves the account over its plan's store allowance). The store comes back and one currently-live store is held in its place, so the number of live stores is unchanged; on a plan with room, nothing is displaced. Answers {store, displaced, moved}. 409 store_disabled when an operator disabled the store — that is an abuse decision, not a billing hold, and POST /api/v1/stores/{public_id}/enable is the route that reverses it." },
      { method: "POST", path: "/api/v1/stores/{public_id}/telegram/test", description: "Send a test message to the store's configured Telegram chat, so you can confirm the chat id is right." },
    ],
  },
  {
    title: "Payments",
    summary:
      "Create payments and track their status. The create call returns the KHQR string and a hosted checkout URL; reading a payment back returns the QR string without the URL, and the list returns neither.",
    items: [
      { method: "POST", path: "/api/v1/payments", description: "Create a payment. Pass amount (a positive decimal with at most two places, in the store link's currency), optional reference_id, metadata and idempotency_key, plus store=<store public id> or merchant=<store external_id>. hosted_qr is left out by default, which means auto: ABA issues the code whenever the store's link is an ABA PayWay link, because a code we build ourselves for one carries no ABA transaction and can never be confirmed. hosted_qr=false builds a code offline and is refused on a live request unless the deployment can confirm one. 201 returns qr_string, checkout_url and expires_at." },
      { method: "GET", path: "/api/v1/payments", description: "List the account's payments, newest first. Filters: ?store=, ?merchant=<external_id>, ?status=, ?limit= (default 20, max 100) and ?offset= (default 0, skips that many newest rows so you can page). A negative offset is a 422. Older payments are still listed after their QR dies, so filter ?status=paid for a settlement feed. Statuses: pending, scanned, paid, expired, failed, superseded, reversed." },
      { method: "GET", path: "/api/v1/payments/{public_id}", description: "One payment: status, amount, currency, QR string, created/expires/approved/paid timestamps, the ABA reference once settled, and reversal state. checkout_url is null here — it is built by the create call, and the id inside it is this payment's own id, so /pay/{public_id} is the same page. Amounts are returned as decimal strings; summary totals elsewhere are integer *amount_cents." },
      { method: "POST", path: "/api/v1/payments/{public_id}/reissue", description: "Replace a dead code with a fresh one and keep the lineage. Only an expired or failed payment can be replaced: paid answers 409 payment_already_paid, reversed answers 409 payment_reversed, and anything still live — pending, scanned, or a superseded code that already has a replacement — answers 409 payment_not_expired. 201 mints a new payment, 200 returns the live replacement already created for this one." },
      { method: "POST", path: "/api/v1/payments/{public_id}/reverse", description: "Record that a paid payment was refunded, with an optional note and a payment.reversed event. This is bookkeeping only — we never hold your funds, so send the money back to the customer yourself and record it here so your reports and quota stop counting the sale. 409 payment_not_paid or payment_already_reversed otherwise." },
    ],
  },
  {
    title: "Payment Reconciliation",
    summary:
      "Re-check a payment you created, and have the platform act on what it finds. These are the endpoints to use for confirmation.",
    items: [
      { method: "GET", path: "/api/v1/transactions/check-status/{payment_public_id}", description: "Authoritative status for one of your payments. Query: prefer_aba_page (default true), aba_slug_hint, mark_paid (default true — when a source reports PAID, the payment row transitions too). Returns status (PAID/PENDING/FAILED/UNKNOWN), source (payway_hosted_checkout when an ABA-hosted session answered, bakong_open_api when the Bakong ledger matched, or null when no source could be reached), matched_amount, transitioned_to_paid, the signals behind the answer, and error when a source could not be reached. 404 payment_not_found if the id is not on your account. Works without Bakong credentials for a payment that has a hosted ABA session." },
      { method: "POST", path: "/api/v1/transactions/verify-payment/{payment_public_id}", description: "The same reconciliation, returning the underlying Bakong transaction shape instead of a status object. Query: use_hash (default false — forces the Bakong path), prefer_aba_page (default true), aba_slug_hint. When nothing confirms it yet it answers 200 with found:false rather than a 404 — there is simply no transaction to return yet. Bakong credentials are required only for a payment with no hosted session to ask." },
    ],
  },
  {
    title: "KHQR Generation",
    summary:
      "Mint an ABA-hosted code for a store's payment link, check whether it settled, and render a KHQR payload as SVG. Only the PayWay routes can confirm a payment.",
    items: [
      { method: "POST", path: "/api/v1/khqr/payway/checkout", description: "Ask ABA to issue a hosted checkout session for a link, and return the code ABA will accept — that is what makes it payable as well as trackable." },
      { method: "POST", path: "/api/v1/khqr/payway/status", description: "Ask ABA for a hosted session's outcome — approved, paid, and ABA's own receipt URL. This is the only check that is authoritative on ABA's side." },
      { method: "GET", path: "/api/v1/khqr/render.svg", description: "Render a KHQR payload as SVG. Pass payload (the raw QR string, 8–1500 chars); optional scale (default 8) and ecc (default h). Public and stateless — it encodes what you give it and stores nothing." },
    ],
  },
  {
    title: "Webhooks",
    summary:
      "Signed webhook endpoints with HMAC signatures, replay protection, and delivery logs. Subscribe to specific events or use the wildcard `*`. Each event's data block carries both `store` and `merchant: { external_id }`; external_id is null for stores created without one.",
    items: [
      { method: "GET", path: "/api/v1/webhooks", description: "List the account's webhook endpoints." },
      { method: "POST", path: "/api/v1/webhooks", description: "Register an endpoint. Pass url and an optional events=[] array ([\"*\"] for all). The response includes signing_secret once — store it, it cannot be read back. Capped by your plan." },
      { method: "PATCH", path: "/api/v1/webhooks/{endpoint_id}", description: "Change the url, events, status (active|disabled), or set enabled=true|false." },
      { method: "DELETE", path: "/api/v1/webhooks/{endpoint_id}", description: "Remove an endpoint and its delivery log. Use PATCH enabled=false to pause deliveries without losing history." },
      { method: "POST", path: "/api/v1/webhooks/{endpoint_id}/rotate-secret", description: "Issue a new signing secret and return it once. Deliveries signed with the previous secret will fail verification." },
      { method: "GET", path: "/api/v1/webhooks/{endpoint_id}/deliveries", description: "Delivery attempt log, newest first: attempt_count, http_status, response_body_preview, and created/completed times. ?limit= (default 200, max 500) and ?page=." },
      { method: "POST", path: "/api/v1/webhooks/{endpoint_id}/test", description: "Send a synthetic signed event now, so you can validate the whole pipeline before going live." },
    ],
  },
  {
    title: "Reports & Reconciliation",
    summary:
      "CSV and JSON payment exports, available on every plan. CSV streams every matching payment; JSON adds summary totals and pagination.",
    items: [
      { method: "GET", path: "/api/v1/reports/payments.csv", description: "Streaming CSV export. Filters: ?from=YYYY-MM-DD, ?to=YYYY-MM-DD, ?store_id=st_…, ?merchant=<external_id>, ?statuses=comma,separated." },
      { method: "GET", path: "/api/v1/reports/payments.json", description: "JSON export. Same filters plus ?page= and ?per_page= (default 20, max 100). Returns {data, summary: {total_matching_rows, total_matching_paid_count, total_matching_paid_amount_cents, total_matching_paid_amount_formatted, total_matching_paid_by_currency, total_matching_reversed_count, total_matching_reversed_amount_cents, filters_applied}, pagination}. The paid totals count only status=paid, so the reversed totals are what account for the difference. Read total_matching_paid_by_currency for accounts settling in both USD and KHR — the single amount_cents fields sum across currencies." },
    ],
  },
  {
    title: "Platform Stats (Public)",
    summary:
      "Aggregate, unauthenticated counters for the marketing site. Merchant activity only — the platform's own internal store is excluded.",
    items: [
      {
        method: "GET",
        path: "/api/v1/public/stats",
        description:
          "No auth. Returns {active_customers, active_stores, transactions_processed, volumes: {USD, KHR}, generated_at, app_name}. Counts and volumes cover paid, non-internal payments only; volumes are per currency in minor units. Cached 60s.",
      },
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

/**
 * Renders the catalogue's inline `code` spans.
 *
 * The rows live in `../reference.ts`, and a data file has no JSX to wrap a token in, so a
 * pair of backticks is the one convention: text between them renders as code and
 * everything else is plain text. There is deliberately no markdown support beyond this —
 * a row that needs more than inline code belongs in prose on the page, not in the table.
 */
function RichText({ text }: { text: string }) {
  const parts = text.split("`");
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <code key={index} className="docs-ref-inline">
            {part}
          </code>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

function ReferenceHeader({ baseUrl }: { baseUrl: string }) {
  return (
    <section className="landing-section landing-surface docs-hero">
      <div className="landing-shell">
        <p className="landing-section-eyebrow">API Reference</p>
        <h1 className="landing-section-title">
          Every endpoint, every refusal.
          <span className="landing-title-line">One page, machine-checked.</span>
        </h1>
        <p className="docs-hero-copy">
          The complete ChmabaPay surface: stores, payments, reconciliation, KHQR, webhooks,
          reports and hosted checkout, with the full error catalogue and rate limits. For the
          copy-paste onboarding flow, start with the guide.
        </p>
        <div className="landing-button-row docs-hero-actions">
          <a className="landing-button-primary" href="/api/docs">
            Getting started guide
          </a>
          <a className="landing-button-secondary" href="/openapi.json">
            OpenAPI schema
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
            return (
              <article key={group.title} className="docs-endpoint-card">
                <div className="docs-endpoint-head">
                  <div className="docs-endpoint-title-row">
                    <h3 className="docs-endpoint-title">{group.title}</h3>
                  </div>
                  <p className="docs-endpoint-summary">
                    <RichText text={group.summary} />
                  </p>
                </div>
                <ul className="docs-endpoint-list">
                  {group.items.map((item) => (
                    <li key={`${item.method} ${item.path}`} className="docs-endpoint-row">
                      <span className={`docs-method docs-method-${item.method}`}>{item.method}</span>
                      <code className="docs-path">{item.path}</code>
                      <p className="docs-description">
                        <RichText text={item.description} />
                      </p>
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

function ConventionsSection() {
  return (
    <section id="conventions" className="landing-section">
      <div className="landing-shell">
        <div className="docs-section-head">
          <p className="landing-section-eyebrow">How the API behaves</p>
          <h2 className="landing-section-title">Conventions worth knowing up front.</h2>
        </div>
        <div className="docs-ref-grid">
          <article className="docs-ref-card docs-ref-card-wide">
            <ul className="docs-ref-list">
              {conventions.map((item) => (
                <li key={item.title}>
                  <h4>{item.title}</h4>
                  <p>
                    <RichText text={item.body} />
                  </p>
                  {item.code ? (
                    <CodeBlock code={item.code} lang="bash" />
                  ) : null}
                </li>
              ))}
            </ul>
          </article>
        </div>
      </div>
    </section>
  );
}

function LimitsSection() {
  return (
    <section id="limits" className="landing-section landing-surface">
      <div className="landing-shell">
        <div className="docs-section-head">
          <p className="landing-section-eyebrow">Rate limits</p>
          <h2 className="landing-section-title">Limits, and what a 429 carries.</h2>
        </div>
        <div className="docs-ref-grid">
          <article className="docs-ref-card">
            <table className="docs-ref-table">
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Scope</th>
                  <th>Limit</th>
                </tr>
              </thead>
              <tbody>
                {rateLimits.map((row) => (
                  <tr key={row.rule}>
                    <td>
                      <span className="docs-ref-code">{row.rule}</span>
                      <span className="docs-ref-note">
                        <RichText text={row.applies} />
                      </span>
                    </td>
                    <td>{row.scope}</td>
                    <td>{row.limit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </article>

          <article className="docs-ref-card">
            <div className="docs-endpoint-head">
              <div className="docs-endpoint-title-row">
                <h3 className="docs-endpoint-title">When a limit is reached</h3>
              </div>
              <p className="docs-endpoint-summary">
                The request is refused before it is processed, so nothing was created. Wait the
                number of seconds the response names, then retry — the rule that fired is in
                the body, which is what tells you whether to slow down one endpoint or the
                whole client.
              </p>
            </div>
            <CodeBlock
              title="Response once a limit is hit"
              lang="http"
              code={`HTTP/1.1 429 Too Many Requests
Retry-After: 12
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0

{ "detail": "rate_limited: payment_create",
  "limit": 60, "window_seconds": 60, "retry_after": 12 }`}
            />
          </article>
        </div>
      </div>
    </section>
  );
}

function ErrorsSection() {
  return (
    <section id="errors" className="landing-section">
      <div className="landing-shell">
        <div className="docs-section-head">
          <p className="landing-section-eyebrow">Refusals</p>
          <h2 className="landing-section-title">Every error code, in one place.</h2>
          <p className="docs-ref-note">
            A refusal carries an HTTP status and a <code className="docs-ref-inline">detail</code>{" "}
            that is a stable machine token. Branch on the token, never on the prose around it.
            Request-validation failures are the one exception: they answer with FastAPI&apos;s
            array shape and carry no stable code, which is why the tables below list none.
          </p>
          <a className="docs-ref-link" href="/openapi.json">
            Machine-readable contract (OpenAPI)
          </a>
        </div>
        <div className="docs-ref-grid">
          {errorGroups.map((group) => (
            <article key={group.title} className="docs-ref-card">
              <div className="docs-endpoint-head">
                <div className="docs-endpoint-title-row">
                  <h3 className="docs-endpoint-title">{group.title}</h3>
                </div>
                <p className="docs-endpoint-summary">
                  <RichText text={group.summary} />
                </p>
              </div>
              <table className="docs-ref-table">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Code</th>
                    <th>What it means</th>
                  </tr>
                </thead>
                <tbody>
                  {group.rows.map((row) => (
                    <tr key={`${row.status}-${row.code}`}>
                      <td>
                        <span
                          className={
                            row.status >= 500
                              ? "docs-ref-status docs-ref-status-server"
                              : "docs-ref-status"
                          }
                        >
                          {row.status}
                        </span>
                      </td>
                      <td>
                        <span className="docs-ref-code">{row.code}</span>
                      </td>
                      <td>
                        <RichText text={row.meaning} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

export const metadata = {
  title: "API Reference — ChmabaPay",
  description:
    "The full ChmabaPay API surface: every endpoint, the complete error catalogue, rate limits and conventions.",
  // Its own canonical and social card, so a shared link to the reference does not
  // preview as the guide or the marketing home page.
  alternates: { canonical: "/api/docs/reference" },
  openGraph: {
    type: "article",
    title: "API Reference — ChmabaPay",
    description:
      "Every endpoint and every error code for KHQR payments over ABA PayWay.",
    url: "/api/docs/reference",
    siteName: "ChmabaPay",
    images: [{ url: "/og-image.png", width: 1024, height: 1024, alt: "ChmabaPay" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "API Reference — ChmabaPay",
    description:
      "Every endpoint and every error code for KHQR payments over ABA PayWay.",
    images: ["/og-image.png"],
  },
};

export default function ApiReferencePage() {
  const baseUrl = resolveSiteUrl();

  return (
    <main>
      <ReferenceHeader baseUrl={baseUrl} />
      <EndpointSection />
      <ConventionsSection />
      <LimitsSection />
      <ErrorsSection />
    </main>
  );
}
