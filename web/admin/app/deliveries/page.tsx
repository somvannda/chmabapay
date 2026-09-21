"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/Toast";
import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

type AdminDeliveryRow = {
  id: number;
  event_id: string;
  event_type: string;
  status: string;
  attempts: number;
  last_response_status: number | null;
  last_error: string | null;
  next_attempt_at: string | null;
  account_id: number;
  account_email: string | null;
  endpoint_id: number;
  endpoint_url: string;
  created_at: string;
  updated_at: string;
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
  { value: "retrying", label: "Retrying" },
  { value: "success", label: "Success" },
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

function deliveryPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "success":
      return { className: "dash-pill dash-pill-paid", label: "success" };
    case "retrying":
      return { className: "dash-pill dash-pill-retrying", label: "retrying" };
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
 * A delivery still queued after the moment it was supposed to go out is the
 * signature of a sender that has stalled rather than a destination that is
 * refusing — a distinction that decides whether the fix is on our side.
 */
function isOverdue(row: AdminDeliveryRow): boolean {
  if (!row.next_attempt_at) return false;
  if (row.status !== "pending" && row.status !== "retrying") return false;
  const due = new Date(row.next_attempt_at);
  return !Number.isNaN(due.getTime()) && due.getTime() < Date.now();
}

export default function AdminDeliveriesPage() {
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<AdminDeliveryRow[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const { notify } = useToast();

  const [status, setStatus] = useState("");
  const [endpointId, setEndpointId] = useState("");
  const [accountId, setAccountId] = useState("");
  const [page, setPage] = useState(1);

  // Seeded from the URL so the overview's failure counter can link straight to the
  // rows it counted, and so a filtered view can be pasted into a thread.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialStatus = params.get("status") || "";
    const initialEndpoint = params.get("endpoint_id") || "";
    const initialAccount = params.get("account_id") || "";
    if (initialStatus) setStatus(initialStatus);
    if (initialEndpoint) setEndpointId(initialEndpoint);
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
        if (endpointId) params.set("endpoint_id", endpointId);
        if (accountId) params.set("account_id", accountId);
        params.set("page", String(page));
        params.set("per_page", "25");
        const res = await apiFetch(`/v1/admin/deliveries?${params.toString()}`, {
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
  }, [ready, status, endpointId, accountId, page]);

  // The row-level counterpart to the payment page's "re-deliver": one endpoint was
  // down, one event never arrived, and the merchant has already fixed their side.
  const retry = useCallback(
    async (row: AdminDeliveryRow) => {
      setBusy(row.id);
      try {
        const res = await apiFetch(`/v1/admin/deliveries/${row.id}/retry`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const data = (await res.json()) as { retried: boolean };
        notify(
          data.retried
            ? "Delivery queued — the sender picks it up on its next pass."
            : "That delivery was already queued to go.",
        );
        setRows((current) =>
          current.map((item) =>
            item.id === row.id
              ? { ...item, status: "retrying", attempts: 0 }
              : item,
          ),
        );
      } catch (e) {
        notify(e instanceof Error ? e.message : String(e), "error");
      } finally {
        setBusy(null);
      }
    },
    [notify],
  );

  const clearFilters = useCallback(() => {
    setStatus("");
    setEndpointId("");
    setAccountId("");
    setPage(1);
  }, []);

  const filtered = Boolean(status || endpointId || accountId);
  const showPagination = pagination !== null && pagination.total_pages > 1;

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Deliveries</h1>
          <div className="dash-page-subtitle">
            Webhook delivery attempts across every account, newest first
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
            aria-label="Filter by delivery status"
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
          <input
            className="dash-input"
            type="text"
            inputMode="numeric"
            placeholder="Endpoint ID"
            value={endpointId}
            onChange={(e) => {
              setEndpointId(e.target.value.replace(/[^0-9]/g, ""));
              setPage(1);
            }}
            aria-label="Filter by endpoint id"
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

      {errorMsg && <div className="dash-warn">{errorMsg}</div>}

      <div className="dash-panel">
        {loading ? (
          <div className="dash-info">Loading deliveries…</div>
        ) : rows.length === 0 ? (
          <div className="dash-empty">
            No delivery attempts match these filters.
            <div className="dash-empty-desc">
              With no filters this feed is every webhook we have tried to send. An
              account that has never had an endpoint configured produces nothing
              here, and neither does one whose events have all been delivered and
              aged out.
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Account</th>
                <th>Endpoint</th>
                <th>Status</th>
                <th>Tries</th>
                <th>Last response</th>
                <th>Next attempt</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const pill = deliveryPill(row.status);
                const overdue = isOverdue(row);
                return (
                  <tr key={row.id}>
                    <td>
                      {row.event_type}
                      <div className="dash-sub">
                        <span className="dash-code-mono">{row.event_id}</span>
                      </div>
                    </td>
                    <td>
                      <Link
                        className="dash-link-btn"
                        href={`/accounts/${row.account_id}`}
                      >
                        {row.account_email || `Account #${row.account_id}`}
                      </Link>
                    </td>
                    <td>
                      <span className="dash-code-mono">{row.endpoint_url}</span>
                    </td>
                    <td>
                      <span className={pill.className}>{pill.label}</span>
                    </td>
                    <td>{nf.format(row.attempts)}</td>
                    <td>
                      {row.last_response_status ?? "—"}
                      {row.last_error && (
                        <div className="dash-sub">
                          <span className="dash-code-mono">{row.last_error}</span>
                        </div>
                      )}
                    </td>
                    <td>
                      {formatDateTime(row.next_attempt_at)}
                      {overdue && <div className="dash-sub">overdue</div>}
                    </td>
                    <td>{formatDateTime(row.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="dash-btn dash-btn-secondary dash-btn-sm"
                        onClick={() => void retry(row)}
                        disabled={busy !== null}
                        title={
                          row.status === "success"
                            ? "Send it again even though it succeeded."
                            : "Queue this delivery to go out now."
                        }
                      >
                        {busy === row.id ? "Retrying…" : "Retry"}
                      </button>
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
            {nf.format(pagination.total_rows)} deliveries
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
