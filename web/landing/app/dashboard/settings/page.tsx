"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { readApiError } from "@/components/portal/apiError";
import { useSession } from "@/components/portal/useSession";

type TabKey = "profile" | "billing" | "security";

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
  const [profileSuccess, setProfileSuccess] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileSaving, setProfileSaving] = useState(false);

  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [billingLoading, setBillingLoading] = useState(true);

  // Security tab. The email and password forms are separate submissions on purpose:
  // each needs the current password, and neither should be able to happen as a side
  // effect of saving the other.
  const [newEmail, setNewEmail] = useState("");
  const [emailPassword, setEmailPassword] = useState("");
  const [emailSaving, setEmailSaving] = useState(false);
  const [emailMsg, setEmailMsg] = useState<string | null>(null);
  const [emailErr, setEmailErr] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [pwSaving, setPwSaving] = useState(false);
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [pwErr, setPwErr] = useState<string | null>(null);

  const [confirmEmail, setConfirmEmail] = useState("");
  const [erasePassword, setErasePassword] = useState("");
  const [erasing, setErasing] = useState(false);
  const [eraseErr, setEraseErr] = useState<string | null>(null);

  useEffect(() => {
    if (profile) {
      setProfileName(profile.name ?? "");
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

  const handleEmailSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setEmailSaving(true);
    setEmailErr(null);
    setEmailMsg(null);
    try {
      const res = await fetch("/v1/me/email", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: newEmail.trim(),
          current_password: emailPassword,
        }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      await refresh();
      setEmailPassword("");
      setNewEmail("");
      setEmailMsg("Email updated.");
    } catch (err) {
      setEmailErr(err instanceof Error ? err.message : String(err));
    } finally {
      setEmailSaving(false);
    }
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPwSaving(true);
    setPwErr(null);
    setPwMsg(null);
    try {
      const res = await fetch("/v1/me/password", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setCurrentPassword("");
      setNewPassword("");
      setPwMsg("Password changed. Use it the next time you sign in.");
    } catch (err) {
      setPwErr(err instanceof Error ? err.message : String(err));
    } finally {
      setPwSaving(false);
    }
  };

  const handleErase = async (e: React.FormEvent) => {
    e.preventDefault();
    setErasing(true);
    setEraseErr(null);
    try {
      const res = await fetch("/v1/me", {
        method: "DELETE",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirm_email: confirmEmail.trim(),
          current_password: erasePassword,
        }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      // The account is suspended by the time this returns, so every later request
      // answers 401 and the workspace could not render anyway.
      window.location.replace("/");
    } catch (err) {
      setEraseErr(err instanceof Error ? err.message : String(err));
      setErasing(false);
    }
  };

  const handleProfileSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setProfileSaving(true);
    setProfileError(null);
    setProfileSuccess(null);
    try {
      // The profile endpoint's field is `name`. Anything else is dropped by the
      // request schema, which used to make this save a silent no-op.
      const body = { name: profileName };
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
            Manage your profile, billing and account security.
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
        <button
          type="button"
          role="tab"
          className={`dash-tab ${activeTab === "security" ? "dash-tab-active" : ""}`}
          onClick={() => setActiveTab("security")}
        >
          Security
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

      {activeTab === "security" && profile && (
        <section className="dash-panels">
          <div className="dash-panel">
            <div className="dash-panel-title">Email address</div>
            <form className="dash-form" onSubmit={handleEmailSubmit}>
              {emailMsg && (
                <div className="dash-form-alert dash-form-alert-success">{emailMsg}</div>
              )}
              {emailErr && (
                <div className="dash-form-alert dash-form-alert-error">{emailErr}</div>
              )}

              <div className="dash-hint">
                Currently <strong>{profile.email}</strong>. This is where receipts and
                account notices go, so it takes your current password to move it —
                a stolen browser session must not be enough to redirect them.
              </div>

              {profile.has_password === false ? (
                <div className="dash-warn">
                  This account signs in with Google, so there is no password to confirm
                  a change with, and we have no way to verify a new address. Email
                  support@chmaba.com to move it.
                </div>
              ) : (
                <>
                  <div className="dash-field">
                    <label htmlFor="sec-new-email">New email address</label>
                    <input
                      id="sec-new-email"
                      type="email"
                      className="dash-input"
                      value={newEmail}
                      onChange={(e) => setNewEmail(e.target.value)}
                      placeholder="you@example.com"
                      required
                    />
                  </div>
                  <div className="dash-field">
                    <label htmlFor="sec-email-password">Current password</label>
                    <input
                      id="sec-email-password"
                      type="password"
                      className="dash-input"
                      value={emailPassword}
                      onChange={(e) => setEmailPassword(e.target.value)}
                      autoComplete="current-password"
                      required
                    />
                  </div>
                  <div>
                    <button
                      type="submit"
                      className="dash-btn dash-btn-primary"
                      disabled={emailSaving}
                    >
                      {emailSaving ? "Saving…" : "Change email"}
                    </button>
                  </div>
                </>
              )}
            </form>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Password</div>
            <form className="dash-form" onSubmit={handlePasswordSubmit}>
              {pwMsg && (
                <div className="dash-form-alert dash-form-alert-success">{pwMsg}</div>
              )}
              {pwErr && (
                <div className="dash-form-alert dash-form-alert-error">{pwErr}</div>
              )}

              {profile.has_password === false ? (
                <div className="dash-hint">
                  This account signs in with Google and has no password to change.
                </div>
              ) : (
                <>
                  <div className="dash-field">
                    <label htmlFor="sec-current-password">Current password</label>
                    <input
                      id="sec-current-password"
                      type="password"
                      className="dash-input"
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                      autoComplete="current-password"
                      required
                    />
                  </div>
                  <div className="dash-field">
                    <label htmlFor="sec-new-password">New password</label>
                    <input
                      id="sec-new-password"
                      type="password"
                      className="dash-input"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      autoComplete="new-password"
                      minLength={8}
                      required
                    />
                    <div className="dash-hint">At least 8 characters.</div>
                  </div>
                  <div>
                    <button
                      type="submit"
                      className="dash-btn dash-btn-primary"
                      disabled={pwSaving}
                    >
                      {pwSaving ? "Saving…" : "Change password"}
                    </button>
                  </div>
                </>
              )}
            </form>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Close this account</div>
            <form className="dash-form" onSubmit={handleErase}>
              {eraseErr && (
                <div className="dash-form-alert dash-form-alert-error">{eraseErr}</div>
              )}

              <div className="dash-hint">
                We anonymise your account and switch off every store, API key and
                webhook on it. Payments stay on record — they are the accounting
                evidence of money that really moved, and your own books depend on
                them. This cannot be undone.
              </div>

              {profile.is_platform_admin ? (
                <div className="dash-warn">
                  This account operates the platform console and cannot close itself.
                  Grant another platform admin first.
                </div>
              ) : (
                <>
                  <div className="dash-field">
                    <label htmlFor="sec-confirm-email">
                      Type <strong>{profile.email}</strong> to confirm
                    </label>
                    <input
                      id="sec-confirm-email"
                      type="text"
                      className="dash-input"
                      value={confirmEmail}
                      onChange={(e) => setConfirmEmail(e.target.value)}
                      autoComplete="off"
                      required
                    />
                  </div>
                  {profile.has_password !== false && (
                    <div className="dash-field">
                      <label htmlFor="sec-erase-password">Current password</label>
                      <input
                        id="sec-erase-password"
                        type="password"
                        className="dash-input"
                        value={erasePassword}
                        onChange={(e) => setErasePassword(e.target.value)}
                        autoComplete="current-password"
                        required
                      />
                    </div>
                  )}
                  <div>
                    <button
                      type="submit"
                      className="dash-btn dash-btn-danger"
                      disabled={erasing}
                    >
                      {erasing ? "Closing…" : "Close my account"}
                    </button>
                  </div>
                </>
              )}
            </form>
          </div>
        </section>
      )}
    </>
  );
}
