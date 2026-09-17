import { brandColors } from "@shared/theme";

const primaryCtaHref = "/auth/google/login";

const featureCards = [
  {
    index: "01",
    icon: "setup",
    title: "Set up in minutes",
    body: "Create your workspace, connect ABA PayWay or Bakong, and go live without a heavy setup process.",
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
    const res = await fetch(`${backendBaseUrl()}/v1/billing/plans`, {
      next: { revalidate: 60 },
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
              workspace for KHQR, Bakong, and ABA PayWay.
            </p>

            <div className="landing-button-row landing-hero-actions">
              <a className="landing-button-primary" href={primaryCtaHref}>
                Start free
              </a>
              <a className="landing-button-secondary" href="#how-it-works">
                See payment flow
              </a>
            </div>

            <div className="landing-proof-row">
              <div className="landing-avatar-stack">
                <span className="landing-avatar-chip landing-avatar-green">A</span>
                <span className="landing-avatar-chip landing-avatar-gold">B</span>
                <span className="landing-avatar-chip landing-avatar-purple">C</span>
                <span className="landing-avatar-chip landing-avatar-pink">D</span>
              </div>
              <div className="landing-proof-copy">
                <strong>2,400+</strong> teams already trust the Chmaba product family
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
                <div className="landing-window-status">Payment rails healthy</div>
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
                    <div className="landing-dashboard-date">Friday, 11 September 2026</div>
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
                        {[34, 54, 44, 68, 78, 64, 86].map((height, index) => (
                          <div key={index} className="landing-chart-bar-wrap">
                            <div className="landing-chart-bar-track">
                              <div className="landing-chart-bar-fill" style={{ height: `${height}%` }} />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="landing-items-card">
                      <div className="landing-panel-title">Active rails</div>
                      {[
                        ["AB", "ABA PayWay", "$412"],
                        ["BK", "Bakong KHQR", "$355"],
                        ["DB", "Direct Bank", "$188"],
                        ["CL", "Checkout Link", "$96"],
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

                <a
                  className={plan.is_featured ? "landing-button-dark-primary" : "landing-button-dark-secondary"}
                  href={primaryCtaHref}
                >
                  {`Choose ${plan.name}`}
                </a>
              </div>
            );
          })}
        </div>

        <p className="landing-pricing-footnote">
          Money from each payment goes straight to your own ABA or Bakong account. ChmabaPay does not
          hold funds or settle on your behalf.
        </p>
      </div>
    </section>
  );
}

function ClosingSection() {
  return (
    <section id="customers" className="landing-section landing-surface">
      <div className="landing-shell">
        <div className="landing-closing-grid">
          <div className="landing-testimonial-card">
            <div className="landing-stars">*****</div>
            <p className="landing-testimonial-quote">
              “ChmabaPay gave us a cleaner start to each day. We know which payment links worked before
              the first support message arrives.”
            </p>
            <div className="landing-testimonial-author">
              <div className="landing-testimonial-avatar">ML</div>
              <div>
                <div className="landing-testimonial-name">Mina Lee</div>
                <div className="landing-testimonial-role">Owner, Little Fern Coffee</div>
              </div>
            </div>
          </div>

          <div className="landing-cta-card">
            <div className="landing-cta-eyebrow">Ready when you are</div>
            <h3 className="landing-cta-title">Your next better payment day starts here.</h3>
            <p className="landing-cta-copy">Set up your first workspace free. No card needed.</p>
            <a className="landing-button-primary landing-cta-button" href={primaryCtaHref}>
              Open your workspace
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}

export default function LandingPage() {
  return (
    <main>
      <HeroSection />
      <BandSection />
      <FeatureSection />
      <PricingSection />
      <ClosingSection />
    </main>
  );
}
