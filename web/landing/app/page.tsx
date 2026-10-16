import type { Metadata } from "next";
import { SessionCta } from "@/components/SessionCta";

/**
 * The homepage canonical. Every other public page declares one; without this the
 * highest-value indexable URL on the site was the only one that did not, so a link
 * to a filtered or parameterised variant could be indexed as a separate page.
 */
export const metadata: Metadata = {
  alternates: { canonical: "/" },
};

/**
 * The pricing cards are the homepage's only API dependency, and the page is
 * prerendered — in the production image that happens at `docker build` time, before
 * any container is up, so `api:8000` cannot resolve and `fetchPublicPlans()` falls
 * back to `[]`. A failed fetch leaves no revalidate hint behind, which would make the
 * route plain static and serve that empty state until the next deploy. Declaring the
 * window on the route itself means the page instead regenerates in the background and
 * the real plans appear on the first request after it expires.
 */
export const revalidate = 60;

/**
 * Where the page's calls to action send an anonymous visitor. `SessionCta` sends a
 * signed-in one to the workspace instead of back through Google's consent screen.
 */
const signedOutCtaHref = "/auth/google/login";

const featureCards = [
  {
    index: "01",
    icon: "setup",
    title: "Set up in minutes",
    body: "Create your workspace, connect your ABA PayWay link, and go live without a heavy setup process.",
  },
  {
    index: "02",
    icon: "payment",
    title: "Accept payments without friction",
    body: "Give merchants a cleaner KHQR and ABA PayWay flow with less confusion and clearer payment status.",
  },
  {
    index: "03",
    icon: "insight",
    title: "Know what worked",
    body: "Track successful payments, retries, and delivery events in one honest view with less noise.",
  },
] as const;

/**
 * Pricing is owned by the admin console: the plans endpoint returns the name, fee,
 * tagline, feature bullets and the "featured" flag for every public+active plan.
 */
type PublicPlan = {
  code: string;
  name: string;
  monthly_fee_cents: number;
  monthly_fee_formatted: string;
  tagline: string | null;
  features: string[] | null;
  is_featured: boolean;
};

function backendBaseUrl(): string {
  return (
    process.env.NEXT_PUBLIC_API_URL ??
    process.env.BACKEND_URL ??
    "http://127.0.0.1:8000"
  );
}

async function fetchPublicPlans(): Promise<PublicPlan[]> {
  try {
    const res = await fetch(`${backendBaseUrl()}/api/v1/billing/plans`, {
      next: { revalidate: 60 },
      // A hung API must not hold the marketing page hostage. Unreachable is already
      // fast (connection refused), but "accepting and silent" is not, so the wait
      // is bounded here and the section falls back to its empty state.
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return [];
    const data = (await res.json()) as PublicPlan[];
    return Array.isArray(data) ? data : [];
  } catch {
    // The marketing page must render even if the API is unreachable; the pricing
    // cards simply stay out until the next revalidation.
    return [];
  }
}

function planPrice(plan: PublicPlan): { amount: string; period: string } {
  if (plan.monthly_fee_cents === 0) return { amount: "$0", period: "" };
  return { amount: plan.monthly_fee_formatted, period: "/mo" };
}

/**
 * Platform totals shown on the homepage. Same shape as the API's
 * `/api/v1/public/stats` — counts are plain integers and volume is per currency
 * in minor units, because USD and KHR do not add up to one number.
 */
type PublicStats = {
  active_customers: number;
  active_stores: number;
  transactions_processed: number;
  volumes: Record<string, number>;
};

async function fetchPublicStats(): Promise<PublicStats | null> {
  try {
    const res = await fetch(`${backendBaseUrl()}/api/v1/public/stats`, {
      next: { revalidate: 60 },
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as PublicStats;
    if (data === null || typeof data !== "object") return null;
    return data;
  } catch {
    // The homepage must render even when the API is unreachable. The metrics
    // section falls back to its placeholder rather than blocking the page.
    return null;
  }
}

const compact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** USD volume, `amount_cents` is 2-decimal minor units. */
function formatUsd(cents: number): string {
  const dollars = cents / 100;
  if (dollars >= 1000) return `$${compact.format(dollars)}`;
  return `$${dollars.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

/** KHR volume, stored as 2-decimal minor units but riel has no fractional part. */
function formatKhr(cents: number): string {
  const riel = Math.round(cents / 100);
  if (riel >= 1000) return `៛${compact.format(riel)}`;
  return `៛${riel.toLocaleString("en-US")}`;
}


function HeroSection() {
  return (
    <section id="product" className="landing-hero">
      <div className="landing-section landing-shell landing-hero-shell">
        <div className="landing-hero-grid">
          <div className="landing-hero-copy">
            <div className="landing-hero-pill">Built for payment teams</div>
            <h1 className="landing-hero-title">
              <span className="landing-title-line">Your payments,</span>
              <span className="landing-title-line">
                in a better <span className="landing-hero-violet">flow.</span>
              </span>
            </h1>
            <p className="landing-hero-description">
              ChmabaPay brings checkouts, payment visibility, and cleaner operations into one calm
              workspace for KHQR payments over ABA PayWay.
            </p>

            <div className="landing-button-row landing-hero-actions">
              <SessionCta
                className="landing-button-primary"
                href={signedOutCtaHref}
                hrefWhenSignedIn="/dashboard"
              >
                Start free
              </SessionCta>
              <a className="landing-button-secondary" href="#how-it-works">
                See payment flow
              </a>
            </div>

            <div className="landing-proof-row">
              <div className="landing-proof-copy">
                Money settles straight into your own ABA PayWay account. ChmabaPay never holds
                your funds.
              </div>
            </div>
          </div>

          <div className="landing-dashboard-frame">
            <div className="landing-dashboard-window">
              <div className="landing-window-top">
                <div className="landing-window-dots">
                  <span />
                  <span />
                  <span />
                </div>
                <div className="landing-window-status">Dashboard preview</div>
              </div>

              <div className="landing-dashboard-shell">
                <div className="landing-dashboard-sidebar">
                  <div className="landing-sidebar-logo" />
                  <div className="landing-sidebar-stack">
                    <span className="landing-sidebar-dot landing-sidebar-dot-active" />
                    <span className="landing-sidebar-dot" />
                    <span className="landing-sidebar-dot" />
                    <span className="landing-sidebar-dot" />
                  </div>
                </div>

                <div className="landing-dashboard-main">
                  <div className="landing-dashboard-head">
                    <div className="landing-dashboard-date">Illustrative data</div>
                    <div className="landing-dashboard-user">CP</div>
                  </div>
                  <div className="landing-dashboard-greeting">Payment overview</div>

                  <div className="landing-dashboard-metrics">
                    <div className="landing-stat-card landing-stat-card-primary">
                      <div className="landing-stat-label">Paid today</div>
                      <div className="landing-stat-value">$1,284</div>
                      <div className="landing-stat-trend">+18.4%</div>
                    </div>
                    <div className="landing-stat-card">
                      <div className="landing-stat-label">Settled</div>
                      <div className="landing-stat-value">96</div>
                      <div className="landing-stat-trend">+12.6%</div>
                    </div>
                    <div className="landing-stat-card">
                      <div className="landing-stat-label">Avg. payment</div>
                      <div className="landing-stat-value">$10.04</div>
                      <div className="landing-stat-trend">+4.2%</div>
                    </div>
                  </div>

                  <div className="landing-dashboard-lower">
                    <div className="landing-chart-card">
                      <div className="landing-panel-title">Payment volume</div>
                      <div className="landing-panel-subtitle">Last 7 days</div>
                      <div className="landing-chart-bars">
                        {[34, 54, 44, 68, 78, 64, 86].map((height) => (
                          <div key={height} className="landing-chart-bar-wrap">
                            <div className="landing-chart-bar-track">
                              <div
                                className={`landing-chart-bar-fill landing-chart-bar-h${height}`}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="landing-items-card">
                      <div className="landing-panel-title">Your stores</div>
                      {[
                        ["MS", "Main Store", "$412"],
                        ["DT", "Downtown", "$355"],
                        ["ON", "Online shop", "$188"],
                      ].map(([initials, label, amount]) => (
                        <div key={label} className="landing-items-row">
                          <div className="landing-items-left">
                            <span className="landing-items-icon">{initials}</span>
                            <span className="landing-items-label">{label}</span>
                          </div>
                          <span className="landing-items-amount">{amount}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="landing-floating-toast">
              <div className="landing-floating-dot" />
              <div>
                <div className="landing-floating-title">Payment confirmed</div>
                <div className="landing-floating-copy">INV-1048 · $24.50</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function BandSection() {
  return (
    <section className="landing-band">
      <div className="landing-shell">
        <div className="landing-band-row">
          <div className="landing-band-title">One workspace. Every moving part.</div>
          <div className="landing-band-features">
            <span>Fast checkout setup</span>
            <span>Clear payment visibility</span>
            <span>Clear reports</span>
            <span>Safe by design</span>
          </div>
        </div>
      </div>
    </section>
  );
}

/**
 * Platform-wide traction, pulled live from `/api/v1/public/stats`. This is the
 * one section on the page that is a *claim about the platform* rather than a
 * description of the product, so it only renders numbers the API actually
 * returned — no inflated or illustrative figures in a "customers" counter. When
 * the API is unreachable the section is omitted entirely rather than showing a
 * row of zeros, which would read as "nobody uses this".
 */
function MetricsSection({ stats }: { stats: PublicStats }) {
  const usd = stats.volumes?.USD ?? 0;
  const khr = stats.volumes?.KHR ?? 0;
  const items = [
    { key: "customers", label: "Active customers", value: compact.format(stats.active_customers) },
    { key: "stores", label: "Active stores", value: compact.format(stats.active_stores) },
    {
      key: "transactions",
      label: "Transactions processed",
      value: compact.format(stats.transactions_processed),
    },
    {
      key: "volume",
      label: "Amount processed",
      // Both currencies shown side by side: USD and KHR settle in different
      // units, so one summed figure would be a meaningless addition.
      value: khr > 0 ? `${formatUsd(usd)} · ${formatKhr(khr)}` : formatUsd(usd),
    },
  ];

  return (
    <section className="landing-section landing-metrics">
      <div className="landing-shell">
        <div className="landing-metrics-head">
          <p className="landing-section-eyebrow">Live on the platform</p>
          <h2 className="landing-section-title">Real numbers, not marketing math.</h2>
        </div>
        <div className="landing-metrics-grid">
          {items.map((item) => (
            <div key={item.key} className="landing-metrics-card">
              <div className="landing-metrics-value">{item.value}</div>
              <div className="landing-metrics-label">{item.label}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function FeatureSection() {
  return (
    <section id="how-it-works" className="landing-section landing-surface">
      <div className="landing-shell">
        <div className="landing-feature-head">
          <div>
            <p className="landing-section-eyebrow">From first payment to full picture</p>
            <h2 className="landing-section-title">Less admin. More time on the work that matters.</h2>
          </div>
        </div>

        <div className="landing-feature-grid">
          {featureCards.map((item) => (
            <div key={item.index} className="landing-feature-card">
              <div className="landing-feature-top">
                <div className={`landing-feature-icon landing-feature-icon-${item.icon}`} />
                <div className="landing-feature-index">{item.index}</div>
              </div>
              <h3 className="landing-feature-title">{item.title}</h3>
              <p className="landing-feature-copy">{item.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

async function PricingSection() {
  const plans = await fetchPublicPlans();

  return (
    <section id="plans" className="landing-pricing-shell">
      <div className="landing-shell">
        <div className="landing-pricing-head">
          <div>
            <p className="landing-pricing-eyebrow">Plans that grow with you</p>
            <h2 className="landing-pricing-title">
              Start small.
              <br />
              Stay in control.
            </h2>
          </div>
          <p className="landing-pricing-copy">
            Every plan includes the essentials. Upgrade when your payment volume or store count grows.
          </p>
        </div>

        <div className="landing-pricing-grid">
          {plans.length === 0 ? (
            // Plans come from the API only — nothing is hardcoded here on purpose,
            // because the admin console owns them and a baked-in copy would drift.
            // A failed fetch must therefore degrade visibly instead of leaving the
            // grid silently empty under a "Start small" heading.
            <div className="landing-plan-card landing-plan-card-empty">
              <div className="landing-plan-name-dark">Plans aren’t loading right now</div>
              <div className="landing-plan-caption">
                The current plans come straight from our billing system, and that request just
                failed. Refresh in a moment, or contact us and we’ll send you the details.
              </div>
              <a className="landing-button-dark-secondary" href="/contact">
                Contact us
              </a>
            </div>
          ) : null}

          {plans.map((plan) => {
            const { amount, period } = planPrice(plan);
            const features = plan.features ?? [];
            return (
              <div
                key={plan.code}
                className={plan.is_featured ? "landing-plan-card landing-plan-card-featured" : "landing-plan-card"}
              >
                {plan.is_featured ? <div className="landing-plan-badge">Most popular</div> : null}
                <div className="landing-plan-name-dark">{plan.name}</div>
                <div className="landing-plan-price-dark">
                  {amount}
                  {period ? <span>{period}</span> : null}
                </div>
                <div className="landing-plan-caption">{plan.tagline ?? ""}</div>

                <div className="landing-plan-divider" />

                <div className="landing-plan-list-dark">
                  {features.map((item) => (
                    <div key={item} className="landing-plan-item-dark">
                      <span className="landing-plan-check-dark">✓</span>
                      <span>{item}</span>
                    </div>
                  ))}
                </div>

                <SessionCta
                  className={plan.is_featured ? "landing-button-dark-primary" : "landing-button-dark-secondary"}
                  href={signedOutCtaHref}
                  hrefWhenSignedIn="/dashboard/billing"
                >
                  {`Choose ${plan.name}`}
                </SessionCta>
              </div>
            );
          })}
        </div>

        <p className="landing-pricing-footnote">
          Money from each payment goes straight to your own ABA PayWay account. ChmabaPay does not
          hold funds and does not move them on your behalf.
        </p>
      </div>
    </section>
  );
}

function ClosingSection() {
  return (
    <section id="late-payments" className="landing-section landing-surface">
      <div className="landing-shell">
        <div className="landing-closing-grid">
          <div className="landing-testimonial-card">
            <p className="landing-testimonial-quote">
              An expired code is not a lost sale. If a customer pays after the window closes, we
              keep confirming and record the payment as paid anyway — so what you report matches what
              reached the bank.
            </p>
            <div className="landing-testimonial-author">
              <div>
                <div className="landing-testimonial-name">Late payments still settle</div>
                <div className="landing-testimonial-role">
                  Verified on a live ABA payment
                </div>
              </div>
            </div>
          </div>

          <div className="landing-cta-card">
            <div className="landing-cta-eyebrow">Ready when you are</div>
            <h3 className="landing-cta-title">Your next better payment day starts here.</h3>
            <p className="landing-cta-copy">Set up your first workspace free. No card needed.</p>
            <SessionCta
              className="landing-button-primary landing-cta-button"
              href={signedOutCtaHref}
              hrefWhenSignedIn="/dashboard"
            >
              Open your workspace
            </SessionCta>
          </div>
        </div>
      </div>
    </section>
  );
}

export default async function LandingPage() {
  // Fetched here rather than inside `MetricsSection` so one failed request can
  // omit the section cleanly instead of rendering an empty shell.
  const stats = await fetchPublicStats();
  return (
    <main>
      <HeroSection />
      <BandSection />
      {stats ? <MetricsSection stats={stats} /> : null}
      <FeatureSection />
      <PricingSection />
      <ClosingSection />
    </main>
  );
}
