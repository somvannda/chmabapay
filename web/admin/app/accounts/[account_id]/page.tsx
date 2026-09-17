"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { useToast } from "@/components/Toast";

type AccountProfile = {
  id: number;
  email: string;
  name: string;
  status: string;
  account_type: string;
  account_type_explicitly_set: boolean;
  whitelabel_enabled: boolean;
  is_platform_admin: boolean;
  created_at: string;
  updated_at: string;
};

type AccountPlan = {
  code: string | null;
  name: string | null;
  subscription_status: string | null;
};

type AccountStore = {
  id: string;
  name: string;
  external_id: string | null;
  status: string;
};

type AdminInvoice = {
  id: number;
  account_id: number;
  account_email: string | null;
  period_month: string;
  status: string;
  base_fee_cents: number;
  usage_payments_count: number;
  overage_fee_cents: number;
  total_due_cents: number;
  paid_at: string | null;
  created_at: string;
};

type AccountDetail = {
  account: AccountProfile;
  plan: AccountPlan;
  counts: { stores: number; payments: number };
  stores: AccountStore[];
  invoices: AdminInvoice[];
};

const nf = new Intl.NumberFormat("en-US");

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

function subscriptionPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "active":
      return { className: "dash-pill dash-pill-paid", label: "active" };
    case "trial":
      return { className: "dash-pill dash-pill-scanned", label: "trial" };
    case "canceled":
      return { className: "dash-pill dash-pill-failed", label: "canceled" };
    default:
      return { className: "dash-pill dash-pill-pending", label: "none" };
  }
}

function invoicePill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "paid":
      return { className: "dash-pill dash-pill-paid", label: "paid" };
    case "issued":
      return { className: "dash-pill dash-pill-scanned", label: "issued" };
    case "overdue":
      return { className: "dash-pill dash-pill-failed", label: "overdue" };
    case "draft":
      return { className: "dash-pill dash-pill-pending", label: "draft" };
    default:
      return {
        className: "dash-pill dash-pill-pending",
        label: status || "—",
      };
  }
}

function storePill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  return (status || "").toLowerCase() === "active"
    ? { className: "dash-pill dash-pill-paid", label: "active" }
    : { className: "dash-pill dash-pill-expired", label: status || "unknown" };
}

function TextOrDash({ value }: { value: string | null | undefined }) {
  return <>{value ? value : "—"}</>;
}

export default function AdminAccountDetailPage({
  params,
}: {
  params: { account_id: string };
}) {
  const accountId = params.account_id;

  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<AccountDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    setNotFound(false);
    try {
      const res = await fetch(`/v1/admin/accounts/${accountId}`, {
        credentials: "include",
      });
      if (res.status === 404) {
        setNotFound(true);
        return;
      }
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as AccountDetail;
      setDetail(data);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  useEffect(() => {
    void load();
  }, [load]);

  const account = detail?.account ?? null;
  const sub = subscriptionPill(detail?.plan.subscription_status);
  const { notify } = useToast();
  const [entitlementSaving, setEntitlementSaving] = useState(false);

  // White-label checkout branding is an entitlement, not a plan field: the operator
  // grants it here and the store API refuses branding writes without it.
  const toggleWhitelabel = useCallback(async () => {
    if (!account) return;
    const next = !account.whitelabel_enabled;
    setEntitlementSaving(true);
    try {
      const res = await fetch(`/v1/admin/accounts/${accountId}`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ whitelabel_enabled: next }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setDetail((d) =>
        d ? { ...d, account: { ...d.account, whitelabel_enabled: next } } : d,
      );
      notify(next ? "White-label enabled" : "White-label disabled");
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setEntitlementSaving(false);
    }
  }, [account, accountId, notify]);

  return (
    <>
      <div className="dash-page-head">
        <div>
          <div className="dash-toolbar-filters">
            <Link className="dash-link-btn" href="/accounts">
              ← Back to accounts
            </Link>
          </div>
          <h1 className="dash-page-title">
            {account ? account.name || account.email : "Account"}
          </h1>
          <div className="dash-page-subtitle">
            {account ? account.email : "Account detail"}
          </div>
        </div>
      </div>

      {errorMsg && (
        <div className="dash-warn">
          {errorMsg}{" "}
          <button
            type="button"
            className="dash-btn dash-btn-secondary dash-btn-sm"
            onClick={() => void load()}
          >
            Retry
          </button>
        </div>
      )}

      {loading ? (
        <div className="dash-info">Loading account…</div>
      ) : notFound || !account || !detail ? (
        <div className="dash-empty">
          Account not found.
          <div className="dash-empty-desc">
            This account may have been removed.{" "}
            <Link className="dash-link-btn" href="/accounts">
              Return to accounts
            </Link>
          </div>
        </div>
      ) : (
        <>
          <div className="dash-panels">
            <div className="dash-panel">
              <div className="dash-panel-title">Plan &amp; usage</div>
              <table className="dash-table">
                <tbody>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Plan</div>
                    </td>
                    <td>
                      <TextOrDash value={detail.plan.name} />
                      {detail.plan.code && (
                        <span className="dash-badge dash-badge-muted">
                          {" "}
                          {detail.plan.code}
                        </span>
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Subscription</div>
                    </td>
                    <td>
                      <span className={sub.className}>{sub.label}</span>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Stores</div>
                    </td>
                    <td>{nf.format(detail.counts.stores)}</td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Payments</div>
                    </td>
                    <td>{nf.format(detail.counts.payments)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="dash-panel">
              <div className="dash-panel-title">Profile</div>
              <table className="dash-table">
                <tbody>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Account ID</div>
                    </td>
                    <td>{account.id}</td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Email</div>
                    </td>
                    <td>{account.email}</td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Name</div>
                    </td>
                    <td>
                      <TextOrDash value={account.name} />
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Status</div>
                    </td>
                    <td>
                      <span className="dash-pill dash-pill-pending">
                        {account.status}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Account type</div>
                    </td>
                    <td>
                      {account.account_type}
                      {account.account_type_explicitly_set
                        ? " (explicitly set)"
                        : " (default)"}
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Platform admin</div>
                    </td>
                    <td>{account.is_platform_admin ? "Yes" : "No"}</td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Whitelabel</div>
                    </td>
                    <td>
                      <span
                        className={
                          account.whitelabel_enabled
                            ? "dash-pill dash-pill-paid"
                            : "dash-pill dash-pill-pending"
                        }
                      >
                        {account.whitelabel_enabled ? "Enabled" : "Disabled"}
                      </span>{" "}
                      <button
                        type="button"
                        className="dash-btn dash-btn-secondary dash-btn-sm"
                        onClick={() => void toggleWhitelabel()}
                        disabled={entitlementSaving}
                      >
                        {entitlementSaving
                          ? "Saving…"
                          : account.whitelabel_enabled
                            ? "Disable"
                            : "Enable"}
                      </button>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Created</div>
                    </td>
                    <td>{formatDate(account.created_at)}</td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Updated</div>
                    </td>
                    <td>{formatDate(account.updated_at)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Stores</div>
            {detail.stores.length === 0 ? (
              <div className="dash-empty">
                No stores for this account.
                <div className="dash-empty-desc">
                  Stores appear once the account connects a payment
                  destination.
                </div>
              </div>
            ) : (
              <table className="dash-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Public ID</th>
                    <th>External ID</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.stores.map((store) => {
                    const pill = storePill(store.status);
                    return (
                      <tr key={store.id}>
                        <td>{store.name}</td>
                        <td>
                          <code>{store.id}</code>
                        </td>
                        <td>
                          <TextOrDash value={store.external_id} />
                        </td>
                        <td>
                          <span className={pill.className}>{pill.label}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Invoices</div>
            {detail.invoices.length === 0 ? (
              <div className="dash-empty">
                No invoices for this account.
                <div className="dash-empty-desc">
                  Invoices are generated when the account is on a paid plan.
                </div>
              </div>
            ) : (
              <table className="dash-table">
                <thead>
                  <tr>
                    <th>Period</th>
                    <th>Status</th>
                    <th>Base fee</th>
                    <th>Usage payments</th>
                    <th>Overage</th>
                    <th>Total due</th>
                    <th>Paid at</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.invoices.map((inv) => {
                    const pill = invoicePill(inv.status);
                    return (
                      <tr key={inv.id}>
                        <td>{inv.period_month}</td>
                        <td>
                          <span className={pill.className}>{pill.label}</span>
                        </td>
                        <td>{formatCents(inv.base_fee_cents)}</td>
                        <td>{nf.format(inv.usage_payments_count)}</td>
                        <td>{formatCents(inv.overage_fee_cents)}</td>
                        <td>{formatCents(inv.total_due_cents)}</td>
                        <td>{formatDate(inv.paid_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </>
  );
}
