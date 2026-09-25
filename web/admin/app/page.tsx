"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

type AttentionItem = {
  key: string;
  label: string;
  detail: string;
  count: number;
  severity: string;
  href: string | null;
};

type OpsSignals = {
  transport: string;
  watched: boolean;
  heartbeat_ages: Record<string, number | null> | null;
  stale_queues: string[] | null;
  queue_depth: Record<string, number> | null;
  error: string | null;
};

type AdminOverview = {
  accounts_total: number;
  stores_total: number;
  stores_active: number;
  payments_paid_total: number;
  payments_paid_this_month: number;
  mrr_cents: number;
  paid_today_count: number;
  paid_today_cents: number;
  // Plan fees collected into the platform's own store. Kept apart from paid_today_*,
  // which is merchant volume only — the two are never summed.
  platform_revenue_today_cents: number;
  platform_revenue_this_month_cents: number;
  needs_attention: AttentionItem[];
  ops: OpsSignals;
};

const nf = new Intl.NumberFormat("en-US");

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.round(seconds / 60)}m`;
}

/**
 * Queue state, stated plainly. `watched: false` is not the same as "all clear": with
 * the in-process transport the API *is* the worker, so there is no cross-process
 * heartbeat to read and the console says so rather than showing a reassuring zero.
 */
function opsSummary(ops: OpsSignals): string {
  if (ops.error) return ops.error;
  if (!ops.watched) {
    return `Workers run in this process (${ops.transport}); queue depth and heartbeat age need WORKER_TRANSPORT=redis.`;
  }
  const ages = Object.entries(ops.heartbeat_ages ?? {})
    .map(([queue, age]) => `${queue} ${formatAge(age)}`)
    .join(" · ");
  const depth = Object.entries(ops.queue_depth ?? {})
    .map(([queue, pending]) => `${queue} ${nf.format(pending)}`)
    .join(" · ");
  return `Last drain: ${ages || "—"}. Queue depth: ${depth || "—"}.`;
}

export default function AdminOverviewPage() {
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch("/api/v1/admin/overview", { credentials: "include" });
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
        <>
          <div className="dash-panel">
            <div className="dash-panel-title">Needs attention</div>
            <ul className="dash-attention">
              {(overview.needs_attention ?? []).map((item) => {
                const clear = item.count === 0;
                const className = clear
                  ? "dash-attention-item is-clear"
                  : `dash-attention-item is-${item.severity}`;
                return (
                  <li key={item.key} className={className}>
                    <div className="dash-attention-count">
                      {nf.format(item.count)}
                    </div>
                    <div className="dash-attention-label">{item.label}</div>
                    <div className="dash-attention-detail">{item.detail}</div>
                    {item.href && !clear && (
                      <Link className="dash-link-btn" href={item.href}>
                        Show these
                      </Link>
                    )}
                  </li>
                );
              })}
            </ul>
            <div className="dash-ops-line">{opsSummary(overview.ops)}</div>
          </div>

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
              <div className="dash-stat-label">Paid today (merchants)</div>
              <div className="dash-stat-value">
                {formatCents(overview.paid_today_cents)}
              </div>
              <div className="dash-stat-trend">
                {nf.format(overview.paid_today_count)} payments
              </div>
            </div>

            <div className="dash-stat-card">
              <div className="dash-stat-label">Platform revenue</div>
              <div className="dash-stat-value">
                {formatCents(overview.platform_revenue_today_cents)}
              </div>
              <div className="dash-stat-trend">
                {formatCents(overview.platform_revenue_this_month_cents)} this month
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
        </>
      )}

    </>
  );
}
