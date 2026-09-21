"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

type AdminAccountRow = {
  id: number;
  email: string;
  name: string;
  status: string;
  is_platform_admin: boolean;
  plan_code: string | null;
  plan_name: string | null;
  subscription_status: string | null;
  stores_count: number;
  payments_count: number;
  created_at: string;
};

type Pagination = {
  page: number;
  per_page: number;
  total_rows: number;
  total_pages: number;
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

export default function AdminAccountsPage() {
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<AdminAccountRow[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [appliedQ, setAppliedQ] = useState("");
  const [page, setPage] = useState(1);

  // Seed the search box from the URL so a shared /accounts?q=... link opens
  // pre-filtered.
  useEffect(() => {
    const initialQ = new URLSearchParams(window.location.search).get("q") || "";
    if (initialQ) {
      setQ(initialQ);
      setAppliedQ(initialQ);
    }
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
        params.set("page", String(page));
        params.set("per_page", "25");
        const res = await apiFetch(`/v1/admin/accounts?${params.toString()}`, {
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
  }, [ready, appliedQ, page]);

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      setPage(1);
      setAppliedQ(q.trim());
    },
    [q],
  );

  const showPagination = pagination !== null && pagination.total_pages > 1;

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Accounts</h1>
          <div className="dash-page-subtitle">
            Every account on the platform, with plan and usage counts
          </div>
        </div>
      </div>

      <div className="dash-toolbar">
        <form className="dash-toolbar-filters" onSubmit={onSubmit}>
          <input
            className="dash-input"
            type="search"
            placeholder="Search email or name"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search accounts"
          />
          <button type="submit" className="dash-btn dash-btn-secondary">
            Search
          </button>
        </form>
      </div>

      {errorMsg && <div className="dash-warn">{errorMsg}</div>}

      <div className="dash-panel">
        {loading ? (
          <div className="dash-info">Loading accounts…</div>
        ) : rows.length === 0 ? (
          <div className="dash-empty">
            No accounts match this search.
            <div className="dash-empty-desc">
              Clear the search box, or try a different query.
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Plan</th>
                <th>Subscription</th>
                <th>Status</th>
                <th>Stores</th>
                <th>Payments</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const sub = subscriptionPill(row.subscription_status);
                const standing = accountStatusPill(row.status);
                return (
                  <tr key={row.id}>
                    <td>
                      <Link
                        className="dash-link-btn"
                        href={`/accounts/${row.id}`}
                      >
                        {row.email}
                      </Link>
                      {row.is_platform_admin && (
                        <>
                          {" "}
                          <span className="dash-badge dash-badge-violet">
                            admin
                          </span>
                        </>
                      )}
                    </td>
                    <td>{row.name || "—"}</td>
                    <td>{row.plan_name || "—"}</td>
                    <td>
                      <span className={sub.className}>{sub.label}</span>
                    </td>
                    <td>
                      <span className={standing.className}>{standing.label}</span>
                    </td>
                    <td>{nf.format(row.stores_count)}</td>
                    <td>{nf.format(row.payments_count)}</td>
                    <td>{formatDate(row.created_at)}</td>
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
            {nf.format(pagination.total_rows)} accounts
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
