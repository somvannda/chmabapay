import Link from "next/link";

/**
 * The console's own not-found surface. It renders inside `AdminShell`, so the
 * operator stays signed in and the sidebar stays put — Next.js's bare default
 * would drop the whole console chrome.
 */
export default function NotFound() {
  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Page not found</h1>
          <div className="dash-page-subtitle">
            That route does not exist in the platform console.
          </div>
        </div>
      </div>

      <div className="dash-panel">
        <div className="dash-empty">
          Nothing is served at this address.
          <div className="dash-empty-desc">
            Check the link, or return to the overview.
            <div className="dash-empty-cta-row">
              <Link className="dash-btn dash-btn-primary dash-btn-sm" href="/">
                Back to overview
              </Link>
              <Link
                className="dash-btn dash-btn-secondary dash-btn-sm"
                href="/accounts"
              >
                Accounts
              </Link>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
