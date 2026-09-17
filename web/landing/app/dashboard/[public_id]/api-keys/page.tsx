"use client";

import { useEffect } from "react";

// API keys are managed at the workspace (account) level now.
// Store-scoped keys were removed; this route forwards to the account page.
export default function StoreApiKeysRedirect() {
  useEffect(() => {
    window.location.replace("/dashboard/keys");
  }, []);

  return <div className="dash-info">Redirecting to API keys…</div>;
}
