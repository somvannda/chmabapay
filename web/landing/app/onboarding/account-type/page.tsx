"use client";

import { useEffect } from "react";

// The account-type onboarding step has been removed (single account type).
// Any stale link to this route simply forwards to the dashboard.
export default function AccountTypePage() {
  useEffect(() => {
    window.location.replace("/dashboard");
  }, []);

  return (
    <div className="dash-root">
      <div className="dash-shell">
        <main className="dash-main">
          <div className="dash-info">Redirecting to your dashboard…</div>
        </main>
      </div>
    </div>
  );
}
