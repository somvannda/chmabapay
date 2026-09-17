"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";

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
  { value: "draft", label: "Draft" },
  { value: "issued", label: "Issued" },
  { value: "paid", label: "Paid" },
  { value: "overdue", label: "Overdue" },
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

export default function AdminInvoicesPage() {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<AdminInvoice[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [period, setPeriod] = useState("");
  const [appliedPeriod, setAppliedPeriod] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);

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
        const res = await fetch(`/v1/admin/invoices?${params.toString()}`, {
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
  }, [appliedPeriod, status, page]);

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setPage(1);
      setAppliedPeriod(period.trim());
    },
    [period],
  );

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
              </tr>
            </thead>
            <tbody>
              {rows.map((inv) => {
                const pill = invoicePill(inv.status);
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
    </>
  );
}
