import Link from "next/link";

/**
 * The branded "not found" surface for the whole marketing + dashboard origin.
 *
 * Without this file Next.js serves its bare default — `<h1>404</h1>` in the
 * system font — which reads as a broken site rather than a wrong address. This
 * renders inside the root layout, so the address bar, header, footer and type
 * scale all stay the product's own.
 */
export default function NotFound() {
  return (
    <main className="nf-section">
      <div className="landing-shell">
        <div className="nf-card">
          <span className="nf-eyebrow">404</span>
          <h1 className="nf-title">We couldn&rsquo;t find that page.</h1>
          <p className="nf-copy">
            The link may be broken, or the page may have moved. Check the
            address, or start again from one of these.
          </p>

          <div className="nf-actions">
            <Link className="landing-button-primary" href="/">
              Back to home
            </Link>
            <Link className="landing-button-secondary" href="/api/docs">
              Read the API docs
            </Link>
          </div>

          <p className="nf-help">
            Still stuck?{" "}
            <a className="nf-inline-link" href="mailto:support@chmaba.com">
              support@chmaba.com
            </a>
          </p>
        </div>
      </div>
    </main>
  );
}
