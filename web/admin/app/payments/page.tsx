"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

type AdminPaymentRow = {
  id: string;
  status: string;
  amount_cents: number;
  currency: string;
  reference_id: string | null;
  account_id: number;
  account_email: string | null;
  store_public_id: string;
  store_name: string;
  created_at: string;
  expires_at: string;
  paid_at: string | null;
  reversed_at: string | null;
  detection_closed_at: string | null;
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
  { value: "pending", label: "Pending" },
  { value: "scanned", label: "Scanned" },
  { value: "paid", label: "Paid" },
  { value: "reversed", label: "Refunded" },
  { value: "expired", label: "Expired" },
  { value: "superseded", label: "Superseded" },
  { value: "failed", label: "Failed" },
];

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatAmount(cents: number, currency: string): string {
  const symbol = (currency || "USD").toUpperCase() === "USD" ? "$" : "";
  return `${symbol}${(cents / 100).toFixed(2)}`;
}

function paymentPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "paid":
      return { className: "dash-pill dash-pill-paid", label: "paid" };
    case "scanned":
      return { className: "dash-pill dash-pill-scanned", label: "scanned" };
    case "reversed":
      return { className: "dash-pill dash-pill-reversed", label: "refunded" };
    case "superseded":
      return { className: "dash-pill dash-pill-superseded", label: "superseded" };
    case "expired":
      return { className: "dash-pill dash-pill-expired", label: "expired" };
    case "failed":
      return { className: "dash-pill dash-pill-failed", label: "failed" };
    default:
      return {
        className: "dash-pill dash-pill-pending",
        label: status || "pending",
      };
  }
}

/**
 * The reconciliation story for one row, which is the column an operator actually
 * reads. "Pending" alone cannot distinguish a code a customer is scanning right
 * now from one we gave up watching three days ago, and that difference decides
 * whether the answer to a merchant is "wait" or "the money never arrived".
 */
function settlementNote(row: AdminPaymentRow): React.ReactNode {
  if (row.paid_at && row.reversed_at) {
    return (
      <>
        {formatDateTime(row.paid_at)}
        <div className="dash-sub">refunded {formatDateTime(row.reversed_at)}</div>
      </>
    );
  }
  if (row.paid_at) return formatDateTime(row.paid_at);
  if (row.detection_closed_at) {
    return (
      <>
        {formatDateTime(row.detection_closed_at)}
        <div className="dash-sub">Stopped watching</div>
      </>
    );
  }
  return <span className="dash-sub">Still watching</span>;
}

export default function AdminPaymentsPage() {
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<AdminPaymentRow[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [appliedQ, setAppliedQ] = useState("");
  const [status, setStatus] = useState("");
  const [accountId, setAccountId] = useState("");
  const [attention, setAttention] = useState("");
  const [page, setPage] = useState(1);

  // Seeded from the URL so /accounts/{id} can link straight to one merchant's
  // payments, and so a filtered view can be pasted into a thread. `attention` comes
  // from the overview's "needs attention" cards — the two conditions a plain status
  // filter cannot express.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialQ = params.get("q") || "";
    const initialStatus = params.get("status") || "";
    const initialAccount = params.get("account_id") || "";
    const initialAttention = params.get("attention") || "";
    if (initialQ) {
      setQ(initialQ);
      setAppliedQ(initialQ);
    }
    if (initialStatus) setStatus(initialStatus);
    if (initialAccount) setAccountId(initialAccount);
    if (initialAttention) setAttention(initialAttention);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    let alive = true;
    setLoading(true);
    setErrorMsg(null);
    (async () => {
      try {
        const params = new URLSearchParams();
        if (appliedQ) params.set("q", appliedQ);
        if (status) params.set("status", status);
        if (accountId) params.set("account_id", accountId);
        if (attention) params.set("attention", attention);
        params.set("page", String(page));
        params.set("per_page", "25");
        const res = await apiFetch(`/api/v1/admin/payments?${params.toString()}`, {
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
  }, [ready, appliedQ, status, accountId, attention, page]);

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setPage(1);
      setAppliedQ(q.trim());
    },
    [q],
  );

  const clearFilters = useCallback(() => {
    setQ("");
    setAppliedQ("");
    setStatus("");
    setAccountId("");
    setAttention("");
    setPage(1);
  }, []);

  const filtered = Boolean(appliedQ || status || accountId || attention);
  const showPagination = pagination !== null && pagination.total_pages > 1;

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Payments</h1>
          <div className="dash-page-subtitle">
            Every payment across the platform, newest first
          </div>
        </div>
      </div>

      {attention && (
        <div className="dash-info">
          {attention === "pending_past_expiry"
            ? "Showing payments whose window closed while still pending."
            : "Showing payments we stopped watching that were never paid."}{" "}
          <button
            type="button"
            className="dash-btn dash-btn-secondary dash-btn-sm"
            onClick={() => {
              setAttention("");
              setPage(1);
            }}
          >
            Clear
          </button>
        </div>
      )}

      <div className="dash-toolbar">
        <form className="dash-toolbar-filters" onSubmit={onSubmit}>
          <input
            className="dash-input"
            type="search"
            placeholder="Payment ID or reference"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search by payment id or reference"
          />
          <select
            className="dash-select"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            aria-label="Filter by payment status"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value || "all"} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <input
            className="dash-input"
            type="text"
            inputMode="numeric"
            placeholder="Account ID"
            value={accountId}
            onChange={(e) => {
              setAccountId(e.target.value.replace(/[^0-9]/g, ""));
              setPage(1);
            }}
            aria-label="Filter by account id"
          />
          <button type="submit" className="dash-btn dash-btn-secondary">
            Search
          </button>
          {filtered && (
            <button
              type="button"
              className="dash-btn dash-btn-secondary"
              onClick={clearFilters}
            >
              Clear
            </button>
          )}
        </form>
      </div>

      <div className="dash-panel">
        {loading ? (
          <div className="dash-info">Loading payments…</div>
        ) : errorMsg ? (
          <div className="dash-empty">
            Could not load the payments.
            <div className="dash-empty-desc">
              {errorMsg}
              <br />
              The request failed, so this is not an empty result. Reload the page
              to try again.
            </div>
          </div>
        ) : rows.length === 0 ? (
          <div className="dash-empty">
            No payments match these filters.
            <div className="dash-empty-desc">
              Clear the filters to see the platform-wide feed, or search by the
              payment ID from the merchant&rsquo;s message.
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Payment</th>
                <th>Account</th>
                <th>Store</th>
                <th>Status</th>
                <th>Amount</th>
                <th>Reference</th>
                <th>Created</th>
                <th>Settled</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const pill = paymentPill(row.status);
                return (
                  <tr key={row.id}>
                    <td>
                      <Link className="dash-link-btn" href={`/payments/${row.id}`}>
                        <span className="dash-code-mono">{row.id}</span>
                      </Link>
                    </td>
                    <td>
                      <Link
                        className="dash-link-btn"
                        href={`/accounts/${row.account_id}`}
                      >
                        {row.account_email || `Account #${row.account_id}`}
                      </Link>
                    </td>
                    <td>{row.store_name}</td>
                    <td>
                      <span className={pill.className}>{pill.label}</span>
                    </td>
                    <td>{formatAmount(row.amount_cents, row.currency)}</td>
                    <td>{row.reference_id || "—"}</td>
                    <td>{formatDateTime(row.created_at)}</td>
                    <td>{settlementNote(row)}</td>
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
            {nf.format(pagination.total_rows)} payments
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
