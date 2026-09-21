"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";
import { useToast } from "@/components/Toast";

type AccountProfile = {
  id: number;
  email: string;
  name: string;
  status: string;
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

type AccountKey = {
  id: number;
  name: string;
  key_prefix: string;
  mode: string;
  status: string;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
};

type PlanLimits = {
  plan_code: string;
  max_stores: number | null;
  max_keys_per_account: number;
  payments_included: number;
} | null;

type PlanUsage = {
  keys_active: number;
  payments_this_month: number;
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
  limits: PlanLimits;
  usage: PlanUsage;
  keys: AccountKey[];
  stores: AccountStore[];
  invoices: AdminInvoice[];
};

type PlanOption = {
  id: number;
  code: string;
  name: string;
  monthly_fee_cents: number;
  is_active: boolean;
};

type InvoiceAction = "mark-paid" | "waive" | "credit";

/**
 * The destructive/standing actions that used to run behind `window.prompt` and
 * `window.confirm`. They all go through the styled modal now, so the confirmation
 * style — and the warning it carries — is the same as every other mutation.
 */
type ConfirmAction =
  | { kind: "status"; next: "active" | "suspended" }
  | { kind: "revoke-key"; key: AccountKey }
  | { kind: "disable-store"; store: AccountStore }
  | { kind: "enable-store"; store: AccountStore };

const nf = new Intl.NumberFormat("en-US");

const INVOICE_ACTIONS: { value: InvoiceAction; label: string; help: string }[] = [
  {
    value: "mark-paid",
    label: "Mark paid",
    help: "Settles it as income and puts the plan in force. Use it when the money arrived outside the platform.",
  },
  {
    value: "waive",
    label: "Waive",
    help: "Closes it with no income and puts the plan in force. Use it for goodwill.",
  },
  {
    value: "credit",
    label: "Credit",
    help: "Closes it as credited and puts the plan in force. The amount below is what was forgiven.",
  },
];

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

function formatLimit(value: number | null | undefined): string {
  if (value === null || value === undefined) return "unlimited";
  return nf.format(value);
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
    case "pending":
      // A purchase waiting on its invoice. Without this it read as "none", which
      // hid the one plan the account is actually trying to move to.
      return { className: "dash-pill dash-pill-pending", label: "pending" };
    case "canceled":
      return { className: "dash-pill dash-pill-failed", label: "canceled" };
    default:
      return { className: "dash-pill dash-pill-pending", label: "none" };
  }
}

function accountStatusPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  const s = (status || "").toLowerCase();
  if (s === "suspended") {
    return { className: "dash-pill dash-pill-failed", label: "suspended" };
  }
  return { className: "dash-pill dash-pill-paid", label: s || "active" };
}

/**
 * Invoice statuses are `open` / `paid` / `waived` / `credited`, which is what
 * `services/billing.py` writes and what the resolve route refuses on.
 */
function invoicePill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "paid":
      return { className: "dash-pill dash-pill-paid", label: "paid" };
    case "waived":
      return { className: "dash-pill dash-pill-scanned", label: "waived" };
    case "credited":
      return { className: "dash-pill dash-pill-reversed", label: "credited" };
    default:
      return { className: "dash-pill dash-pill-pending", label: status || "open" };
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

function keyPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  return (status || "").toLowerCase() === "active"
    ? { className: "dash-pill dash-pill-paid", label: "active" }
    : { className: "dash-pill dash-pill-failed", label: status || "revoked" };
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
  const [busy, setBusy] = useState<string | null>(null);
  const { notify } = useToast();

  const [entitlementSaving, setEntitlementSaving] = useState(false);
  const [statusSaving, setStatusSaving] = useState(false);

  const [plans, setPlans] = useState<PlanOption[] | null>(null);
  const [planOpen, setPlanOpen] = useState(false);
  const [planCode, setPlanCode] = useState("");
  const [planReason, setPlanReason] = useState("");

  const [invoice, setInvoice] = useState<AdminInvoice | null>(null);
  const [invoiceAction, setInvoiceAction] = useState<InvoiceAction>("mark-paid");
  const [invoiceReason, setInvoiceReason] = useState("");
  const [invoiceAmount, setInvoiceAmount] = useState("");

  // The signed-in operator's own account id, so suspending *yourself* can be called
  // out before it signs you out. `/v1/me` is the only place the console learns who
  // it is acting as.
  const [selfId, setSelfId] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<ConfirmAction | null>(null);
  const [confirmReason, setConfirmReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    setNotFound(false);
    try {
      const res = await apiFetch(`/v1/admin/accounts/${accountId}`, {
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

  // Who is signed in. This is the only way the page can tell that the account it is
  // about to suspend is the operator's own — the API protects the *last* admin but
  // not an admin from themselves while another admin exists.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await apiFetch("/v1/me", { credentials: "include" });
        if (!res.ok) return;
        const me = (await res.json()) as { id?: number };
        if (alive && typeof me.id === "number") setSelfId(me.id);
      } catch {
        // Not fatal: without it the self-suspension warning simply does not fire.
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const account = detail?.account ?? null;
  const sub = subscriptionPill(detail?.plan.subscription_status);

  // White-label checkout branding is an entitlement, not a plan field: the operator
  // grants it here and the store API refuses branding writes without it.
  const toggleWhitelabel = useCallback(async () => {
    if (!account) return;
    const next = !account.whitelabel_enabled;
    setEntitlementSaving(true);
    try {
      const res = await apiFetch(`/v1/admin/accounts/${accountId}`, {
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

  // Standing was writable only by hand before this: the API has supported
  // `{status, reason}` (with `account.suspended`/`account.activated` audit
  // actions) but nothing in the console called it, so an abuse report had no
  // in-product response. The confirmation and the reason are captured in the styled
  // modal now, like every other mutation here.
  const openStatusConfirm = useCallback(() => {
    if (!account) return;
    const next = account.status === "suspended" ? "active" : "suspended";
    setConfirmReason("");
    setConfirm({ kind: "status", next });
  }, [account]);

  // The merchant route *sells* a plan; this puts one in force with no invoice, which
  // is how a comp or an off-platform settlement is recorded. The plan list is fetched
  // on open so the console never offers a code the API would reject.
  const openPlanModal = useCallback(async () => {
    setPlanCode(detail?.plan.code ?? "");
    setPlanReason("");
    setPlanOpen(true);
    if (plans !== null) return;
    try {
      const res = await apiFetch("/v1/admin/plans", { credentials: "include" });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as PlanOption[];
      setPlans(data.filter((p) => p.is_active));
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    }
  }, [detail?.plan.code, plans, notify]);

  const assignPlan = useCallback(async () => {
    const reason = planReason.trim();
    if (!planCode) {
      notify("Pick a plan first.", "error");
      return;
    }
    if (reason.length < 3) {
      notify("A reason is required to assign a plan by hand.", "error");
      return;
    }
    setBusy("plan");
    try {
      const res = await apiFetch(`/v1/admin/accounts/${accountId}/plan`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_code: planCode, reason }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setPlanOpen(false);
      notify("Plan assigned. No invoice was raised for it.");
      await load();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [accountId, planCode, planReason, notify, load]);

  const openInvoiceModal = useCallback((row: AdminInvoice) => {
    setInvoice(row);
    setInvoiceAction("mark-paid");
    setInvoiceReason("");
    setInvoiceAmount("");
  }, []);

  const resolveInvoice = useCallback(async () => {
    if (!invoice) return;
    const reason = invoiceReason.trim();
    if (reason.length < 3) {
      notify("A reason is required to resolve an invoice.", "error");
      return;
    }
    const body: Record<string, unknown> = {
      action: invoiceAction,
      reason,
    };
    if (invoiceAction === "credit" && invoiceAmount.trim()) {
      const cents = Math.round(Number(invoiceAmount) * 100);
      if (!Number.isFinite(cents) || cents < 0) {
        notify("The credited amount must be a positive number.", "error");
        return;
      }
      body.amount_cents = cents;
    }
    setBusy("invoice");
    try {
      const res = await apiFetch(`/v1/admin/invoices/${invoice.id}/resolve`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setInvoice(null);
      notify("Invoice resolved. Recorded in the audit trail.");
      await load();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [invoice, invoiceAction, invoiceReason, invoiceAmount, notify, load]);

  // Killing one credential is the point: before this route existed the only way to
  // stop a leaked key was suspending the merchant, which took their store offline.
  const openRevokeConfirm = useCallback((key: AccountKey) => {
    setConfirmReason("");
    setConfirm({ kind: "revoke-key", key });
  }, []);

  const openDisableStoreConfirm = useCallback((store: AccountStore) => {
    setConfirmReason("");
    setConfirm({ kind: "disable-store", store });
  }, []);

  const openEnableStoreConfirm = useCallback((store: AccountStore) => {
    setConfirmReason("");
    setConfirm({ kind: "enable-store", store });
  }, []);

  // One handler for every confirm-modal action, so the busy/notify/refresh shape is
  // identical to the plan and invoice modals above.
  const runConfirm = useCallback(async () => {
    if (!confirm) return;

    if (confirm.kind === "status") {
      const reason = confirmReason.trim();
      if (confirm.next === "suspended" && reason.length < 3) {
        notify("A reason is required to suspend an account.", "error");
        return;
      }
      setStatusSaving(true);
      try {
        const res = await apiFetch(`/v1/admin/accounts/${accountId}`, {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status: confirm.next,
            reason: reason || undefined,
          }),
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const updated = (await res.json()) as AccountProfile;
        setDetail((d) => (d ? { ...d, account: { ...d.account, ...updated } } : d));
        notify(
          confirm.next === "suspended" ? "Account suspended" : "Account activated",
        );
        setConfirm(null);
        setConfirmReason("");
      } catch (e) {
        notify(e instanceof Error ? e.message : String(e), "error");
      } finally {
        setStatusSaving(false);
      }
      return;
    }

    if (confirm.kind === "revoke-key") {
      const key = confirm.key;
      setBusy(`key-${key.id}`);
      try {
        const res = await apiFetch(`/v1/admin/keys/${key.id}/revoke`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const data = (await res.json()) as { revoked: boolean };
        notify(data.revoked ? "Key revoked." : "That key was already revoked.");
        setConfirm(null);
        await load();
      } catch (e) {
        notify(e instanceof Error ? e.message : String(e), "error");
      } finally {
        setBusy(null);
      }
      return;
    }

    const store = confirm.store;
    const enabling = confirm.kind === "enable-store";
    setBusy(`store-${store.id}`);
    try {
      const res = await apiFetch(
        `/v1/admin/stores/${store.id}/${enabling ? "enable" : "disable"}`,
        { method: "POST", credentials: "include" },
      );
      if (!res.ok) throw new Error(await readApiError(res));
      if (enabling) {
        const data = (await res.json()) as { enabled: boolean };
        notify(data.enabled ? "Store enabled." : "That store was already enabled.");
      } else {
        const data = (await res.json()) as { disabled: boolean };
        notify(
          data.disabled ? "Store disabled." : "That store was already disabled.",
        );
      }
      setConfirm(null);
      await load();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [confirm, confirmReason, accountId, notify, load]);

  const standing = accountStatusPill(account?.status);
  const limits = detail?.limits ?? null;
  const usage = detail?.usage ?? null;

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
      ) : errorMsg && !detail ? (
        <div className="dash-empty">
          Could not load this account.
          <div className="dash-empty-desc">
            The request failed, so this is not a missing account. Use Retry above,
            or reload the page.
          </div>
        </div>
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
                      )}{" "}
                      <button
                        type="button"
                        className="dash-btn dash-btn-secondary dash-btn-sm"
                        onClick={() => void openPlanModal()}
                      >
                        Change plan
                      </button>
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
                    <td>
                      {nf.format(detail.counts.stores)}
                      {limits && (
                        <span className="dash-sub">
                          {" "}
                          of {formatLimit(limits.max_stores)}
                        </span>
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Active keys</div>
                    </td>
                    <td>
                      {usage ? nf.format(usage.keys_active) : "—"}
                      {limits && (
                        <span className="dash-sub">
                          {" "}
                          of {formatLimit(limits.max_keys_per_account)}
                        </span>
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Payments this month</div>
                    </td>
                    <td>
                      {usage ? nf.format(usage.payments_this_month) : "—"}
                      {limits && (
                        <span className="dash-sub">
                          {" "}
                          of {formatLimit(limits.payments_included)} included
                        </span>
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Payments (all time)</div>
                    </td>
                    <td>
                      <Link
                        className="dash-link-btn"
                        href={`/payments?account_id=${accountId}`}
                      >
                        {nf.format(detail.counts.payments)}
                      </Link>
                    </td>
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
                      <span className={standing.className}>{standing.label}</span>{" "}
                      <button
                        type="button"
                        className="dash-btn dash-btn-secondary dash-btn-sm"
                        onClick={openStatusConfirm}
                        disabled={statusSaving}
                      >
                        {statusSaving
                          ? "Saving…"
                          : account.status === "suspended"
                            ? "Activate"
                            : "Suspend"}
                      </button>
                      {account.status !== "suspended" && (
                        <div className="dash-hint">
                          Suspending signs the account out everywhere, refuses every
                          key, stops new payment codes and pauses webhooks. To stop one
                          store instead, use Disable in the Stores panel below.
                        </div>
                      )}
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
            <div className="dash-panel-title">API keys</div>
            {detail.keys.length === 0 ? (
              <div className="dash-empty">
                No API keys for this account.
                <div className="dash-empty-desc">
                  Payments cannot be created without one.
                </div>
              </div>
            ) : (
              <table className="dash-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Prefix</th>
                    <th>Mode</th>
                    <th>Status</th>
                    <th>Last used</th>
                    <th>Created</th>
                    <th>Revoked</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {detail.keys.map((key) => {
                    const pill = keyPill(key.status);
                    const active = key.status.toLowerCase() === "active";
                    return (
                      <tr key={key.id}>
                        <td>{key.name}</td>
                        <td>
                          <span className="dash-code-mono">{key.key_prefix}…</span>
                        </td>
                        <td>{key.mode}</td>
                        <td>
                          <span className={pill.className}>{pill.label}</span>
                        </td>
                        <td>{formatDate(key.last_used_at)}</td>
                        <td>{formatDate(key.created_at)}</td>
                        <td>{formatDate(key.revoked_at)}</td>
                        <td>
                          <button
                            type="button"
                            className="dash-btn dash-btn-danger dash-btn-sm"
                            onClick={() => openRevokeConfirm(key)}
                            disabled={busy !== null || !active}
                            title={
                              active
                                ? undefined
                                : "This key is already revoked."
                            }
                          >
                            {busy === `key-${key.id}` ? "Revoking…" : "Revoke"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
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
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {detail.stores.map((store) => {
                    const pill = storePill(store.status);
                    const active = store.status.toLowerCase() === "active";
                    const disabled = store.status.toLowerCase() === "disabled";
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
                        <td>
                          {active && (
                            <button
                              type="button"
                              className="dash-btn dash-btn-danger dash-btn-sm"
                              onClick={() => openDisableStoreConfirm(store)}
                              disabled={busy !== null}
                            >
                              {busy === `store-${store.id}`
                                ? "Disabling…"
                                : "Disable"}
                            </button>
                          )}
                          {disabled && (
                            <button
                              type="button"
                              className="dash-btn dash-btn-secondary dash-btn-sm"
                              onClick={() => openEnableStoreConfirm(store)}
                              disabled={busy !== null}
                              title="Stop blocking this store's new payments and webhooks."
                            >
                              {busy === `store-${store.id}`
                                ? "Enabling…"
                                : "Enable"}
                            </button>
                          )}
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
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {detail.invoices.map((inv) => {
                    const pill = invoicePill(inv.status);
                    // Resolving an already-resolved invoice is a 409, so the console
                    // does not offer it: an action that can only fail is not an action.
                    const open = !["paid", "waived", "credited"].includes(
                      inv.status.toLowerCase(),
                    );
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
                        <td>
                          {open && (
                            <button
                              type="button"
                              className="dash-btn dash-btn-secondary dash-btn-sm"
                              onClick={() => openInvoiceModal(inv)}
                              disabled={busy !== null}
                            >
                              Resolve
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {confirm && (
        <div className="dash-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dash-modal">
            <div className="dash-modal-head">
              <h2 className="dash-modal-title">
                {confirm.kind === "status"
                  ? confirm.next === "suspended"
                    ? "Suspend account"
                    : "Activate account"
                  : confirm.kind === "revoke-key"
                    ? "Revoke API key"
                    : confirm.kind === "disable-store"
                      ? "Disable store"
                      : "Enable store"}
              </h2>
              <button
                type="button"
                className="dash-modal-close"
                onClick={() => setConfirm(null)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="dash-modal-body">
              {confirm.kind === "status" ? (
                <>
                  <div className="dash-hint">
                    {confirm.next === "suspended"
                      ? "This signs the account out of every session, refuses every API key, stops new payment codes and pauses webhooks. QR codes already issued stay payable. Use Disable on a single store when only that store is the problem."
                      : "This clears the suspension: the account can sign in again, its keys work, and new payment codes and webhooks resume."}
                  </div>
                  {confirm.next === "suspended" &&
                    selfId !== null &&
                    account?.id === selfId && (
                      <div className="dash-warn">
                        This is your own account. Suspending it signs you out
                        immediately, and you will not be able to undo it from here —
                        another active admin would have to reactivate you.
                      </div>
                    )}
                  <div className="dash-field">
                    <label htmlFor="status-reason">
                      Reason
                      {confirm.next === "suspended" ? "" : " (optional)"}
                    </label>
                    <textarea
                      id="status-reason"
                      className="dash-textarea"
                      value={confirmReason}
                      onChange={(e) => setConfirmReason(e.target.value)}
                      placeholder={
                        confirm.next === "suspended"
                          ? "e.g. Payment-destination abuse reported by three customers."
                          : "e.g. Abuse report was a false positive; cleared."
                      }
                      rows={4}
                      maxLength={500}
                    />
                  </div>
                </>
              ) : confirm.kind === "revoke-key" ? (
                <div className="dash-hint">
                  Requests using <strong>{confirm.key.name}</strong> (
                  {confirm.key.key_prefix}…) stop immediately. This cannot be
                  undone — the merchant has to create a new key.
                </div>
              ) : confirm.kind === "disable-store" ? (
                <div className="dash-hint">
                  <strong>{confirm.store.name}</strong> stops accepting new payments
                  immediately. Codes already issued stay payable, and the account&rsquo;s
                  other stores are unaffected.
                </div>
              ) : (
                <div className="dash-hint">
                  <strong>{confirm.store.name}</strong> starts accepting new payments
                  and webhooks again. It returns to <strong>active</strong> only when it
                  still has a payment link; otherwise it stays a draft until a
                  destination is set.
                </div>
              )}
              <div className="dash-modal-foot">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={() => setConfirm(null)}
                  disabled={busy !== null || statusSaving}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className={
                    confirm.kind === "enable-store" ||
                    (confirm.kind === "status" && confirm.next === "active")
                      ? "dash-btn dash-btn-primary"
                      : "dash-btn dash-btn-danger"
                  }
                  onClick={() => void runConfirm()}
                  disabled={
                    busy !== null ||
                    statusSaving ||
                    (confirm.kind === "status" &&
                      confirm.next === "suspended" &&
                      confirmReason.trim().length < 3)
                  }
                >
                  {confirm.kind === "status"
                    ? statusSaving
                      ? "Saving…"
                      : confirm.next === "suspended"
                        ? "Suspend"
                        : "Activate"
                    : confirm.kind === "revoke-key"
                      ? busy === `key-${confirm.key.id}`
                        ? "Revoking…"
                        : "Revoke key"
                      : confirm.kind === "disable-store"
                        ? busy === `store-${confirm.store.id}`
                          ? "Disabling…"
                          : "Disable store"
                        : busy === `store-${confirm.store.id}`
                          ? "Enabling…"
                          : "Enable store"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {planOpen && (
        <div className="dash-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dash-modal">
            <div className="dash-modal-head">
              <h2 className="dash-modal-title">Assign a plan by hand</h2>
              <button
                type="button"
                className="dash-modal-close"
                onClick={() => setPlanOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="dash-modal-body">
              <div className="dash-hint">
                This puts the plan in force immediately, with no invoice and no
                proration — the account&rsquo;s current subscription is canceled. The
                reason below is stored in the audit trail against your account, along
                with the monthly fee that was given up.
              </div>
              <div className="dash-field">
                <label htmlFor="plan-code">Plan</label>
                <select
                  id="plan-code"
                  className="dash-select"
                  value={planCode}
                  onChange={(e) => setPlanCode(e.target.value)}
                >
                  <option value="">Select a plan…</option>
                  {(plans ?? []).map((p) => (
                    <option key={p.code} value={p.code}>
                      {p.name} ({p.code}) — {formatCents(p.monthly_fee_cents)}/mo
                    </option>
                  ))}
                </select>
                {detail?.plan.code === planCode && (
                  <div className="dash-hint">
                    The account is already on this plan; the API will refuse it as
                    unchanged.
                  </div>
                )}
              </div>
              <div className="dash-field">
                <label htmlFor="plan-reason">Reason</label>
                <textarea
                  id="plan-reason"
                  className="dash-textarea"
                  value={planReason}
                  onChange={(e) => setPlanReason(e.target.value)}
                  placeholder="e.g. Comped for the pilot until the POS integration ships."
                  rows={4}
                  maxLength={500}
                />
              </div>
              <div className="dash-modal-foot">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={() => setPlanOpen(false)}
                  disabled={busy !== null}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="dash-btn dash-btn-danger"
                  onClick={() => void assignPlan()}
                  disabled={
                    busy !== null ||
                    !planCode ||
                    planReason.trim().length < 3 ||
                    detail?.plan.code === planCode
                  }
                >
                  {busy === "plan" ? "Assigning…" : "Assign plan"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {invoice && (
        <div className="dash-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dash-modal">
            <div className="dash-modal-head">
              <h2 className="dash-modal-title">
                Resolve invoice {invoice.period_month}
              </h2>
              <button
                type="button"
                className="dash-modal-close"
                onClick={() => setInvoice(null)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="dash-modal-body">
              <div className="dash-hint">
                {formatCents(invoice.total_due_cents)} due. Whichever you pick puts
                the pending subscription in force, so the merchant is not left on
                their old plan waiting for an invoice nobody will pay.
              </div>
              <div className="dash-field">
                <label htmlFor="invoice-action">Action</label>
                <select
                  id="invoice-action"
                  className="dash-select"
                  value={invoiceAction}
                  onChange={(e) =>
                    setInvoiceAction(e.target.value as InvoiceAction)
                  }
                >
                  {INVOICE_ACTIONS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>
                <div className="dash-hint">
                  {INVOICE_ACTIONS.find((a) => a.value === invoiceAction)?.help}
                </div>
              </div>
              {invoiceAction === "credit" && (
                <div className="dash-field">
                  <label htmlFor="invoice-amount">
                    Amount credited (USD)
                  </label>
                  <input
                    id="invoice-amount"
                    className="dash-input"
                    type="number"
                    min="0"
                    step="0.01"
                    value={invoiceAmount}
                    onChange={(e) => setInvoiceAmount(e.target.value)}
                    placeholder={(invoice.total_due_cents / 100).toFixed(2)}
                  />
                  <div className="dash-hint">
                    Leave blank to credit the full invoice. The invoice keeps saying
                    what was billed; the audit row records what was forgiven.
                  </div>
                </div>
              )}
              <div className="dash-field">
                <label htmlFor="invoice-reason">Reason</label>
                <textarea
                  id="invoice-reason"
                  className="dash-textarea"
                  value={invoiceReason}
                  onChange={(e) => setInvoiceReason(e.target.value)}
                  placeholder="e.g. Bank transfer received; ABA ref 12345678."
                  rows={4}
                  maxLength={500}
                />
              </div>
              <div className="dash-modal-foot">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={() => setInvoice(null)}
                  disabled={busy !== null}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="dash-btn dash-btn-danger"
                  onClick={() => void resolveInvoice()}
                  disabled={busy !== null || invoiceReason.trim().length < 3}
                >
                  {busy === "invoice" ? "Resolving…" : "Resolve"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
