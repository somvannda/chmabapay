"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

type AuditEntry = {
  id: number;
  action: string;
  target_type: string;
  target_id: number;
  actor_account_id: number | null;
  actor_email: string | null;
  details: Record<string, unknown> | null;
  created_at: string;
};

type Pagination = {
  page: number;
  per_page: number;
  total_rows: number;
  total_pages: number;
};

const nf = new Intl.NumberFormat("en-US");

const TARGET_OPTIONS = [
  { value: "", label: "Any target" },
  { value: "ApiKey", label: "API key" },
  { value: "WebhookEndpoint", label: "Webhook endpoint" },
  { value: "Store", label: "Store" },
  { value: "Payment", label: "Payment" },
  { value: "Account", label: "Account" },
  { value: "Plan", label: "Plan" },
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
    second: "2-digit",
  });
}

/** A one-line reading of the details blob, with the raw JSON kept in the tooltip. */
function summarise(details: Record<string, unknown> | null): string {
  if (!details) return "—";
  return Object.entries(details)
    .map(([key, value]) => {
      const text =
        value !== null && typeof value === "object"
          ? JSON.stringify(value)
          : String(value);
      return `${key}=${text}`;
    })
    .join(" · ");
}

export default function AdminAuditPage() {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [action, setAction] = useState("");
  const [appliedAction, setAppliedAction] = useState("");
  const [targetType, setTargetType] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErrorMsg(null);
    (async () => {
      try {
        const params = new URLSearchParams();
        if (appliedAction) params.set("action", appliedAction);
        if (targetType) params.set("target_type", targetType);
        params.set("page", String(page));
        params.set("per_page", "50");
        const res = await apiFetch(`/v1/admin/audit-logs?${params.toString()}`, {
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
  }, [appliedAction, targetType, page]);

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setPage(1);
      setAppliedAction(action.trim());
    },
    [action],
  );

  const showPagination = pagination !== null && pagination.total_pages > 1;

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Audit trail</h1>
          <div className="dash-page-subtitle">
            Who changed a credential, a payout destination or an account&apos;s
            standing — newest first
          </div>
        </div>
      </div>

      <div className="dash-toolbar">
        <form className="dash-toolbar-filters" onSubmit={onSubmit}>
          <input
            className="dash-input"
            type="text"
            placeholder="key.revoked"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            aria-label="Filter by action"
          />
          <select
            className="dash-select"
            value={targetType}
            onChange={(e) => {
              setTargetType(e.target.value);
              setPage(1);
            }}
            aria-label="Filter by target type"
          >
            {TARGET_OPTIONS.map((opt) => (
              <option key={opt.value || "any"} value={opt.value}>
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
          <div className="dash-info">Loading the trail…</div>
        ) : errorMsg ? (
          <div className="dash-empty">
            Could not load the audit trail.
            <div className="dash-empty-desc">
              The request failed, so this is not an empty result. Reload the page
              to try again.
            </div>
          </div>
        ) : rows.length === 0 ? (
          <div className="dash-empty">
            Nothing recorded.
            <div className="dash-empty-desc">
              Try a different action, for example <code>key.revoked</code>, or clear
              the target filter.
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Target</th>
                <th>Actor</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((entry) => {
                const summary = summarise(entry.details);
                const clipped =
                  summary.length > 90 ? `${summary.slice(0, 89)}…` : summary;
                return (
                  <tr key={entry.id}>
                    <td>{formatDateTime(entry.created_at)}</td>
                    <td>
                      <span className="dash-code-mono">{entry.action}</span>
                    </td>
                    <td>
                      <span className="dash-code-mono">
                        {entry.target_type} #{entry.target_id}
                      </span>
                    </td>
                    <td>
                      {entry.actor_account_id === null ? (
                        "—"
                      ) : (
                        <Link
                          className="dash-link-btn"
                          href={`/accounts/${entry.actor_account_id}`}
                        >
                          {entry.actor_email || `Account #${entry.actor_account_id}`}
                        </Link>
                      )}
                    </td>
                    <td className="dash-sub" title={summary}>
                      {clipped}
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
            {nf.format(pagination.total_rows)} entries
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
