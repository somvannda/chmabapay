"use client";

import { HqStorePanel } from "@/components/HqStorePanel";

/**
 * Platform settings.
 *
 * This page exists because the PayWay link panel used to sit at the bottom of the
 * overview under a heading nothing linked to, and the first operator to go looking
 * for it reported exactly that: "I don't see any settings page." It is the single
 * control that switches self-serve billing on, so it needs a home an operator can
 * find from the nav rather than a place they have to be told about.
 *
 * Everything here is platform-owner configuration, not merchant configuration:
 * merchant settings live in the merchant dashboard, where a store's own link and
 * appearance are edited. Keep the two separate.
 */
export default function SettingsPage() {
  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Settings</h1>
          <div className="dash-page-subtitle">
            Platform configuration — where ChmabaPay&apos;s own plan fees are collected
          </div>
        </div>
      </div>

      {/* Where plan fees land. Setting the link here also creates the platform's own
          store if none exists yet, and marks it internal so it is never metered as a
          merchant tenant. */}
      <HqStorePanel />
    </>
  );
}
