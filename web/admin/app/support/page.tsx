"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

type AdminSupportRow = {
  id: string;
  subject: string;
  category: string;
  status: string;
  priority: string;
  account_id: number;
  account_email: string;
  account_name: string;
  assigned_admin_account_id: number | null;
  first_response_at: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
  response_target_hours: number | null;
  target_breached: boolean;
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
  { value: "pending", label: "Waiting on merchant" },
  { value: "resolved", label: "Resolved" },
];

const PRIORITY_OPTIONS = [
  { value: "", label: "All priorities" },
  { value: "priority", label: "Priority" },
  { value: "standard", label: "Standard" },
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

function statusPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "resolved":
      return { className: "dash-pill dash-pill-paid", label: "resolved" };
    case "pending":
      return { className: "dash-pill dash-pill-pending", label: "waiting" };
    case "open":
      return { className: "dash-pill dash-pill-scanned", label: "open" };
    default:
      return {
        className: "dash-pill dash-pill-pending",
        label: status || "open",
      };
  }
}

function priorityPill(priority: string | null | undefined): {
  className: string;
  label: string;
} {
  return (priority || "").toLowerCase() === "priority"
    ? { className: "dash-pill dash-pill-superseded", label: "priority" }
    : { className: "dash-pill dash-pill-pending", label: "standard" };
}

/**
 * The first-response target, as one cell. A breach is the single most important
 * thing this queue carries, so it is written out — "Breach" plus the target it
 * missed — and reinforced by the row tint, rather than signalled by a colour
 * alone. A thread with no target is best-effort and says so.
 */
function firstResponseCell(row: AdminSupportRow): React.ReactNode {
  if (row.target_breached) {
    return (
      <>
        <span className="dash-breach-flag">Breach</span>
        <div className="dash-sub">
          Past the {row.response_target_hours}h target, still unanswered
        </div>
      </>
    );
  }
  if (row.first_response_at) {
    return <span className="dash-sub">Answered</span>;
  }
  if (row.response_target_hours !== null) {
    return (
      <span className="dash-sub">Within the {row.response_target_hours}h target</span>
    );
  }
  return <span className="dash-sub">Best effort</span>;
}

export default function AdminSupportPage() {
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<AdminSupportRow[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [accountId, setAccountId] = useState("");
  const [page, setPage] = useState(1);

  // Seeded from the URL so an account page can link straight to one merchant's
  // requests, and so a filtered queue can be pasted into a thread.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialStatus = params.get("status") || "";
    const initialPriority = params.get("priority") || "";
    const initialAccount = params.get("account_id") || "";
    if (initialStatus) setStatus(initialStatus);
    if (initialPriority) setPriority(initialPriority);
    if (initialAccount) setAccountId(initialAccount);
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
        if (status) params.set("status", status);
        if (priority) params.set("priority", priority);
        if (accountId) params.set("account_id", accountId);
        params.set("page", String(page));
        params.set("per_page", "25");
        const res = await apiFetch(
          `/api/v1/admin/support/requests?${params.toString()}`,
          { credentials: "include" },
        );
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
  }, [ready, status, priority, accountId, page]);

  const clearFilters = useCallback(() => {
    setStatus("");
    setPriority("");
    setAccountId("");
    setPage(1);
  }, []);

  const filtered = Boolean(status || priority || accountId);
  const showPagination = pagination !== null && pagination.total_pages > 1;

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Support</h1>
          <div className="dash-page-subtitle">
            Merchant requests, priority first and unanswered oldest-first
          </div>
        </div>
      </div>

      <div className="dash-toolbar">
        <div className="dash-toolbar-filters">
          <select
            className="dash-select"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            aria-label="Filter by request status"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value || "all"} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <select
            className="dash-select"
            value={priority}
            onChange={(e) => {
              setPriority(e.target.value);
              setPage(1);
            }}
            aria-label="Filter by request priority"
          >
            {PRIORITY_OPTIONS.map((opt) => (
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
          {filtered && (
            <button
              type="button"
              className="dash-btn dash-btn-secondary"
              onClick={clearFilters}
            >
              Clear
            </button>
          )}
        </div>
      </div>

      <div className="dash-panel">
        {loading ? (
          <div className="dash-info">Loading support requests…</div>
        ) : errorMsg ? (
          <div className="dash-empty">
            Could not load the support queue.
            <div className="dash-empty-desc">
              {errorMsg}
              <br />
              The request failed, so this is not an empty result. Reload the page
              to try again.
            </div>
          </div>
        ) : rows.length === 0 ? (
          <div className="dash-empty">
            No support requests match these filters.
            <div className="dash-empty-desc">
              Clear the filters to see the whole queue. An account that never opened
              a request produces nothing here.
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Request</th>
                <th>Account</th>
                <th>Subject</th>
                <th>Category</th>
                <th>Status</th>
                <th>Priority</th>
                <th>Opened</th>
                <th>First response</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const sp = statusPill(row.status);
                const pp = priorityPill(row.priority);
                return (
                  <tr
                    key={row.id}
                    className={row.target_breached ? "is-breach" : undefined}
                  >
                    <td>
                      <Link
                        className="dash-link-btn"
                        href={`/support/${row.id}`}
                      >
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
                      {row.account_name && (
                        <div className="dash-sub">{row.account_name}</div>
                      )}
                    </td>
                    <td>
                      <Link className="dash-link-btn" href={`/support/${row.id}`}>
                        {row.subject}
                      </Link>
                    </td>
                    <td>
                      <span className="dash-badge dash-badge-muted">
                        {row.category}
                      </span>
                    </td>
                    <td>
                      <span className={sp.className}>{sp.label}</span>
                    </td>
                    <td>
                      <span className={pp.className}>{pp.label}</span>
                    </td>
                    <td>{formatDateTime(row.created_at)}</td>
                    <td>{firstResponseCell(row)}</td>
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
            {nf.format(pagination.total_rows)} requests
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
