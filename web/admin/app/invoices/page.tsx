"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/Toast";
import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

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

type Pagination = {
  page: number;
  per_page: number;
  total_rows: number;
  total_pages: number;
};

const nf = new Intl.NumberFormat("en-US");

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "paid", label: "Paid" },
  { value: "waived", label: "Waived" },
  { value: "credited", label: "Credited" },
];

type InvoiceAction = "mark-paid" | "waive" | "credit";

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

/** `services/billing.py` writes `open` / `paid` / `waived` / `credited`. */
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
      return {
        className: "dash-pill dash-pill-pending",
        label: status || "open",
      };
  }
}

export default function AdminInvoicesPage() {
  const { notify } = useToast();
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<AdminInvoice[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [period, setPeriod] = useState("");
  const [appliedPeriod, setAppliedPeriod] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  // Bumped after a resolution so the list refetches without a full reload.
  const [reloadKey, setReloadKey] = useState(0);

  const [invoice, setInvoice] = useState<AdminInvoice | null>(null);
  const [invoiceAction, setInvoiceAction] = useState<InvoiceAction>("mark-paid");
  const [invoiceReason, setInvoiceReason] = useState("");
  const [invoiceAmount, setInvoiceAmount] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErrorMsg(null);
    (async () => {
      try {
        const params = new URLSearchParams();
        if (appliedPeriod) params.set("period_month", appliedPeriod);
        if (status) params.set("status", status);
        params.set("page", String(page));
        params.set("per_page", "25");
        const res = await apiFetch(`/v1/admin/invoices?${params.toString()}`, {
          credentials: "include",
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const data = await res.json().catch(() => ({}));
        if (!alive) return;
        setRows(Array.isArray(data?.data) ? data.data : []);
        setPagination(data?.pagination ?? null);
      } catch (e) {
        if (alive) setErrorMsg(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [appliedPeriod, status, page, reloadKey]);

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setPage(1);
      setAppliedPeriod(period.trim());
    },
    [period],
  );

  const openInvoiceModal = useCallback((row: AdminInvoice) => {
    setInvoice(row);
    setInvoiceAction("mark-paid");
    setInvoiceReason("");
    setInvoiceAmount("");
  }, []);

  // The same resolution the account detail page offers, reachable from the
  // platform-wide list: finding the stuck invoice and settling it should not require
  // a detour through the account first.
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
    setBusy(true);
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
      setReloadKey((k) => k + 1);
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  }, [invoice, invoiceAction, invoiceReason, invoiceAmount, notify]);

  const showPagination = pagination !== null && pagination.total_pages > 1;

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Invoices</h1>
          <div className="dash-page-subtitle">
            Plan invoices across every account
          </div>
        </div>
      </div>

      <div className="dash-toolbar">
        <form className="dash-toolbar-filters" onSubmit={onSubmit}>
          <input
            className="dash-input"
            type="text"
            placeholder="YYYY-MM"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            aria-label="Filter by period month"
          />
          <select
            className="dash-select"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            aria-label="Filter by invoice status"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value || "all"} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <button type="submit" className="dash-btn dash-btn-secondary">
            Search
          </button>
        </form>
      </div>

      {errorMsg && <div className="dash-warn">{errorMsg}</div>}

      <div className="dash-panel">
        {loading ? (
          <div className="dash-info">Loading invoices…</div>
        ) : errorMsg ? (
          <div className="dash-empty">
            Could not load the invoices.
            <div className="dash-empty-desc">
              The request failed, so this is not an empty result. Reload the page
              to try again.
            </div>
          </div>
        ) : rows.length === 0 ? (
          <div className="dash-empty">
            No invoices found.
            <div className="dash-empty-desc">
              Try a different period (YYYY-MM) or clear the status filter.
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Account</th>
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
              {rows.map((inv) => {
                const pill = invoicePill(inv.status);
                // Resolving an already-resolved invoice is a 409, so the console does
                // not offer it: an action that can only fail is not an action.
                const open = !["paid", "waived", "credited"].includes(
                  inv.status.toLowerCase(),
                );
                return (
                  <tr key={inv.id}>
                    <td>
                      <Link
                        className="dash-link-btn"
                        href={`/accounts/${inv.account_id}`}
                      >
                        {inv.account_email || `Account #${inv.account_id}`}
                      </Link>
                    </td>
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
                          disabled={busy}
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

      {showPagination && pagination && (
        <div className="dash-toolbar">
          <div className="dash-info">
            Page {pagination.page} of {pagination.total_pages} ·{" "}
            {nf.format(pagination.total_rows)} invoices
          </div>
          <div className="dash-toolbar-filters">
            <button
              type="button"
              className="dash-btn dash-btn-secondary dash-btn-sm"
              disabled={loading || pagination.page <= 1}
              onClick={() => setPage(pagination.page - 1)}
            >
              Previous
            </button>
            <button
              type="button"
              className="dash-btn dash-btn-secondary dash-btn-sm"
              disabled={loading || pagination.page >= pagination.total_pages}
              onClick={() => setPage(pagination.page + 1)}
            >
              Next
            </button>
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
                {formatCents(invoice.total_due_cents)} due for{" "}
                {invoice.account_email || `account #${invoice.account_id}`}. Whichever
                you pick puts the pending subscription in force, so the merchant is
                not left on their old plan waiting for an invoice nobody will pay.
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
                  <label htmlFor="invoice-amount">Amount credited (USD)</label>
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
                  disabled={busy}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="dash-btn dash-btn-danger"
                  onClick={() => void resolveInvoice()}
                  disabled={busy || invoiceReason.trim().length < 3}
                >
                  {busy ? "Resolving…" : "Resolve"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
