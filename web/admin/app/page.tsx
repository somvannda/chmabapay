"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";

type AdminOverview = {
  accounts_total: number;
  stores_total: number;
  stores_active: number;
  payments_paid_total: number;
  payments_paid_this_month: number;
  mrr_cents: number;
};

const nf = new Intl.NumberFormat("en-US");

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export default function AdminOverviewPage() {
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await fetch("/v1/admin/overview", { credentials: "include" });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as AdminOverview;
      setOverview(data);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Platform admin</h1>
          <div className="dash-page-subtitle">
            Platform-wide accounts, stores, payments and billing
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
        <div className="dash-info">Loading platform overview…</div>
      ) : !overview ? (
        <div className="dash-empty">
          Platform overview is unavailable.
          <div className="dash-empty-desc">
            Refresh the page, or try again once the API is reachable.
          </div>
        </div>
      ) : (
        <section aria-label="Platform metrics" className="dash-metrics">
          <div className="dash-stat-card">
            <div className="dash-stat-label">Accounts</div>
            <div className="dash-stat-value">
              {nf.format(overview.accounts_total)}
            </div>
            <div className="dash-stat-trend">
              <Link className="dash-link-btn" href="/accounts">
                View accounts
              </Link>
            </div>
          </div>

          <div className="dash-stat-card">
            <div className="dash-stat-label">Stores</div>
            <div className="dash-stat-value">
              {nf.format(overview.stores_total)}
            </div>
            <div className="dash-stat-trend">
              {nf.format(overview.stores_active)} active
            </div>
          </div>

          <div className="dash-stat-card">
            <div className="dash-stat-label">Payments paid</div>
            <div className="dash-stat-value">
              {nf.format(overview.payments_paid_total)}
            </div>
            <div className="dash-stat-trend">
              {nf.format(overview.payments_paid_this_month)} this month
            </div>
          </div>

          <div className="dash-stat-card">
            <div className="dash-stat-label">MRR</div>
            <div className="dash-stat-value">
              {formatCents(overview.mrr_cents)}
            </div>
            <div className="dash-stat-trend">
              <Link className="dash-link-btn" href="/plans">
                View plans
              </Link>
            </div>
          </div>
        </section>
      )}
    </>
  );
}
