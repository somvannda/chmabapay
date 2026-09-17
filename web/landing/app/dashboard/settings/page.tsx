"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useSession } from "@/components/portal/useSession";

type TabKey = "profile" | "billing";

type Subscription = {
  subscription?: {
    status?: string;
    next_billing_at?: string | null;
    trial_ends_at?: string | null;
  } | null;
  plan?: { name?: string; code?: string } | string | null;
  [k: string]: unknown;
};

function planDisplayName(sub: Subscription | null): string {
  if (!sub) return "Free";
  const p = sub.plan;
  if (typeof p === "object" && p !== null) return p.name || "Free";
  if (typeof p === "string" && p) return p;
  return "Free";
}

type Invoice = {
  id: string | number;
  amount_cents?: number;
  status?: string;
  created_at?: string | null;
  description?: string | null;
  [k: string]: unknown;
};

function formatDollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function DashboardSettingsPage() {
  const { profile, refresh } = useSession();

  const [activeTab, setActiveTab] = useState<TabKey>("profile");

  const [profileName, setProfileName] = useState("");
  const [profileDisplayName, setProfileDisplayName] = useState("");
  const [profileLocale, setProfileLocale] = useState("");
  const [profileSuccess, setProfileSuccess] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileSaving, setProfileSaving] = useState(false);

  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [billingLoading, setBillingLoading] = useState(true);

  useEffect(() => {
    if (profile) {
      setProfileName(profile.full_name ?? "");
    }
  }, [profile]);

  useEffect(() => {
    if (activeTab !== "billing") return;
    let alive = true;
    (async () => {
      setBillingLoading(true);
      try {
        const subRes = await fetch("/v1/billing/subscription", {
          credentials: "include",
        });
        if (subRes.ok && subRes.status !== 501) {
          const sub = await subRes.json().catch(() => null);
          if (alive && sub) setSubscription(sub);
        }
        const invRes = await fetch("/v1/billing/invoices?limit=5", {
          credentials: "include",
        });
        if (invRes.ok) {
          const data = await invRes.json().catch(() => ({}));
          const items: Invoice[] = Array.isArray(data)
            ? data
            : Array.isArray(data?.items)
              ? data.items
              : Array.isArray(data?.data)
                ? data.data
                : [];
          if (alive) setInvoices(items);
        }
      } catch {
      } finally {
        if (alive) setBillingLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, [activeTab]);

  const handleProfileSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setProfileSaving(true);
    setProfileError(null);
    setProfileSuccess(null);
    try {
      const body: { full_name: string; display_name?: string; locale?: string } = {
        full_name: profileName,
      };
      if (profileDisplayName) body.display_name = profileDisplayName;
      if (profileLocale) body.locale = profileLocale;
      const res = await fetch("/v1/me", {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setProfileSuccess("Profile saved");
        await refresh();
      } else {
        const err = await res.json().catch(() => null);
        setProfileError(err?.detail || err?.message || `HTTP ${res.status}`);
      }
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : String(e));
    } finally {
      setProfileSaving(false);
    }
  };

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h2 className="dash-page-title">Settings</h2>
          <div className="dash-page-subtitle">
            Manage your profile and billing.
          </div>
        </div>
      </div>

      <div className="dash-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          className={`dash-tab ${activeTab === "profile" ? "dash-tab-active" : ""}`}
          onClick={() => setActiveTab("profile")}
        >
          Profile
        </button>
        <button
          type="button"
          role="tab"
          className={`dash-tab ${activeTab === "billing" ? "dash-tab-active" : ""}`}
          onClick={() => setActiveTab("billing")}
        >
          Billing
        </button>
      </div>

      {activeTab === "profile" && profile && (
        <section className="dash-panels">
          <div className="dash-panel">
            <div className="dash-panel-title">Profile details</div>
            <form className="dash-form" onSubmit={handleProfileSubmit}>
              {profileSuccess && (
                <div className="dash-form-alert dash-form-alert-success">{profileSuccess}</div>
              )}
              {profileError && (
                <div className="dash-form-alert dash-form-alert-error">{profileError}</div>
              )}

              <div className="dash-field">
                <label htmlFor="profile-name">Full name</label>
                <input
                  id="profile-name"
                  type="text"
                  className="dash-input"
                  value={profileName}
                  onChange={(e) => setProfileName(e.target.value)}
                />
              </div>

              <div className="dash-field">
                <label htmlFor="profile-email">Email</label>
                <input
                  id="profile-email"
                  type="email"
                  className="dash-input"
                  value={profile.email ?? ""}
                  disabled
                />
              </div>

              <div className="dash-field">
                <label htmlFor="profile-display">Display name (optional)</label>
                <input
                  id="profile-display"
                  type="text"
                  className="dash-input"
                  value={profileDisplayName}
                  onChange={(e) => setProfileDisplayName(e.target.value)}
                  placeholder="How you appear on receipts"
                />
              </div>

              <div className="dash-field">
                <label htmlFor="profile-locale">Locale (optional)</label>
                <input
                  id="profile-locale"
                  type="text"
                  className="dash-input"
                  value={profileLocale}
                  onChange={(e) => setProfileLocale(e.target.value)}
                  placeholder="e.g. en-US, km-KH"
                />
              </div>

              <div className="dash-field">
                <label htmlFor="profile-created">Created at</label>
                <input
                  id="profile-created"
                  type="text"
                  className="dash-input"
                  value={formatDate(profile.created_at)}
                  disabled
                />
              </div>

              <div>
                <button
                  type="submit"
                  className="dash-btn dash-btn-primary"
                  disabled={profileSaving}
                >
                  {profileSaving ? "Saving…" : "Save changes"}
                </button>
              </div>
            </form>
          </div>
        </section>
      )}

      {activeTab === "billing" && (
        <section className="dash-panels">
          <div className="dash-panel">
            <div className="dash-panel-title">Subscription</div>
            {billingLoading ? (
              <div className="dash-info">Loading billing…</div>
            ) : (
              <div className="dash-form">
                <div className="dash-form-row">
                  <div className="dash-field">
                    <label>Current plan</label>
                    <input
                      type="text"
                      className="dash-input"
                      value={planDisplayName(subscription)}
                      disabled
                    />
                  </div>
                  <div className="dash-field">
                    <label>Status</label>
                    <div>
                      <span className="dash-pill dash-pill-paid">
                        {subscription?.subscription?.status || "active"}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="dash-form-row">
                  <div className="dash-field">
                    <label>Next billing date</label>
                    <input
                      type="text"
                      className="dash-input"
                      value={formatDate(subscription?.subscription?.next_billing_at)}
                      disabled
                    />
                  </div>
                </div>

                <div>
                  <Link className="dash-link-btn" href="/dashboard/billing">
                    Manage plan →
                  </Link>
                </div>
              </div>
            )}
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Recent invoices</div>
            {billingLoading ? (
              <div className="dash-info">Loading invoices…</div>
            ) : invoices.length === 0 ? (
              <div className="dash-empty">
                No invoices yet.
                <div className="dash-empty-desc">
                  Invoices will appear here after your first billing cycle.
                </div>
              </div>
            ) : (
              <table className="dash-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Description</th>
                    <th>Amount</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {invoices.slice(0, 5).map((inv) => (
                    <tr key={String(inv.id)}>
                      <td>{formatDate(inv.created_at)}</td>
                      <td>{inv.description || "—"}</td>
                      <td>
                        {typeof inv.amount_cents === "number"
                          ? formatDollars(inv.amount_cents)
                          : "—"}
                      </td>
                      <td>
                        <span className="dash-pill dash-pill-pending">
                          {(inv.status || "pending").toLowerCase()}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}
    </>
  );
}
