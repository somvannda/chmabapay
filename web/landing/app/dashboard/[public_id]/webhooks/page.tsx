"use client";

import { useEffect } from "react";

// Webhooks are managed at the workspace (account) level now.
// Store-scoped endpoints were removed; this route forwards to the account page.
export default function StoreWebhooksRedirect() {
  useEffect(() => {
    window.location.replace("/dashboard/webhooks");
  }, []);

  return <div className="dash-info">Redirecting to webhooks…</div>;
}
