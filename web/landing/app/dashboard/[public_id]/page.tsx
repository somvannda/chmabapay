"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { CopyField } from "@/components/portal/CopyField";
import { readApiError } from "@/components/portal/apiError";

type Payment = {
  id: string | number;
  amount_cents: number;
  amount?: string;
  status: string;
  reference_id?: string | null;
  paid_at?: string | null;
  created_at?: string | null;
  [k: string]: unknown;
};

type StoreDetail = {
  id: string;
  name?: string;
  external_id?: string | null;
  status?: string;
  created_at?: string | null;
  [k: string]: unknown;
};

type ReportSummary = {
  total_matching_rows: number;
  total_matching_paid_count: number;
  total_matching_paid_amount_cents: number;
  total_matching_reversed_count?: number;
  total_matching_reversed_amount_cents?: number;
};

function formatDollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function isoDateParam(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function formatAmountFromStr(str: string | number | null | undefined): number {
  if (!str) return 0;
  if (typeof str === "number") return str;
  const n = parseFloat(str);
  if (Number.isNaN(n)) return 0;
  return Math.round(n * 100);
}

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "";
  const diff = Date.now() - d;
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  return `${days}d ago`;
}

function pillClassForStatus(status: string): string {
  switch (status) {
    case "paid":
      return "dash-pill dash-pill-paid";
    case "scanned":
      return "dash-pill dash-pill-scanned";
    case "expired":
      return "dash-pill dash-pill-expired";
    case "failed":
      return "dash-pill dash-pill-failed";
    case "reversed":
      return "dash-pill dash-pill-reversed";
    case "superseded":
      return "dash-pill dash-pill-superseded";
    case "pending":
    default:
      return "dash-pill dash-pill-pending";
  }
}

function amountCents(p: Payment): number {
  if (typeof p.amount_cents === "number") return p.amount_cents;
  return formatAmountFromStr(p.amount);
}

export default function StoreOverviewPage({
  params,
}: {
  params: { public_id: string };
}) {
  const publicId = params.public_id;

  const [storeLoading, setStoreLoading] = useState(true);
  const [paymentsLoading, setPaymentsLoading] = useState(true);
  const [store, setStore] = useState<StoreDetail | null>(null);
  const [storeMissing, setStoreMissing] = useState(false);
  const [paymentsList, setPaymentsList] = useState<Payment[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryAll, setSummaryAll] = useState<ReportSummary | null>(null);
  const [summaryToday, setSummaryToday] = useState<ReportSummary | null>(null);
  // A failed fetch used to leave these lists empty and the figures at their zero
  // defaults, so the page rendered a complete but entirely zeroed dashboard whether
  // the store was new or the API was down — the outcome the "store not found" note
  // below exists to avoid. Each read now carries its own reason.
  const [storeError, setStoreError] = useState<string | null>(null);
  const [paymentsError, setPaymentsError] = useState<string | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    setStoreLoading(true);
    setStoreError(null);
    (async () => {
      try {
        const res = await fetch(`/v1/stores/${publicId}`, {
          credentials: "include",
        });
        if (res.status === 404) {
          if (alive) setStoreMissing(true);
          return;
        }
        if (!res.ok) throw new Error(await readApiError(res));
        const data = await res.json();
        if (alive) setStore(data as StoreDetail);
      } catch (e) {
        if (alive) setStoreError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setStoreLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [publicId, reloadKey]);

  useEffect(() => {
    let alive = true;
    setPaymentsLoading(true);
    setPaymentsError(null);
    (async () => {
      try {
        const res = await fetch(
          `/v1/payments?store=${encodeURIComponent(publicId)}&limit=20`,
          { credentials: "include" },
        );
        if (!res.ok) throw new Error(await readApiError(res));
        const data = await res.json().catch(() => ({}));
        const items: Payment[] = Array.isArray(data)
          ? data
          : Array.isArray(data?.items)
            ? data.items
            : Array.isArray(data?.data)
              ? data.data
              : [];
        if (alive) setPaymentsList(items);
      } catch (e) {
        if (alive) setPaymentsError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setPaymentsLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [publicId, reloadKey]);

  useEffect(() => {
    let alive = true;
    setSummaryLoading(true);
    setSummaryError(null);
    (async () => {
      // The store's totals come from the reports summary, not from adding up the
      // payments list. That list is one page — the API clamps `limit` to 100 — so
      // arithmetic over it stops counting at 100 payments while the cards carry on
      // describing the store as a whole. `per_page=1` because only `summary` is
      // read; the rows themselves are not wanted here.
      try {
        const today = isoDateParam(new Date());
        const scope = `store_id=${encodeURIComponent(publicId)}`;
        const [allRes, todayRes] = await Promise.all([
          fetch(`/v1/reports/payments.json?${scope}&per_page=1`, {
            credentials: "include",
          }),
          fetch(
            `/v1/reports/payments.json?${scope}&from=${today}&to=${today}&per_page=1`,
            { credentials: "include" },
          ),
        ]);
        if (!allRes.ok) throw new Error(await readApiError(allRes));
        // `paid today` is a separate request; if it fails while the all-time totals
        // succeed, rendering the card as $0.00 would be a claim the request never
        // made. Treat either failure as "this row is unknown".
        if (!todayRes.ok) throw new Error(await readApiError(todayRes));
        const data = await allRes.json().catch(() => null);
        if (alive && data?.summary) setSummaryAll(data.summary as ReportSummary);
        const todayData = await todayRes.json().catch(() => null);
        if (alive && todayData?.summary) {
          setSummaryToday(todayData.summary as ReportSummary);
        }
      } catch (e) {
        if (alive) setSummaryError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setSummaryLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [publicId, reloadKey]);

  // `total_matching_paid_*` counts only `status == paid`, so refunds are already
  // excluded from it. They are shown beside it rather than folded in, because a
  // total that quietly dropped is the thing a merchant queries.
  const settledCents = summaryAll?.total_matching_paid_amount_cents ?? 0;
  const totalPaidCount = summaryAll?.total_matching_paid_count ?? 0;
  const transactionsCount = summaryAll?.total_matching_rows ?? 0;
  const refundedCount = summaryAll?.total_matching_reversed_count ?? 0;
  const refundedCents = summaryAll?.total_matching_reversed_amount_cents ?? 0;
  const paidTodayCents = summaryToday?.total_matching_paid_amount_cents ?? 0;
  const paidTodayCount = summaryToday?.total_matching_paid_count ?? 0;
  const avgCents =
    totalPaidCount > 0 ? Math.round(settledCents / totalPaidCount) : 0;

  const recentPayments = [...paymentsList]
    .sort((a, b) => {
      const ta = new Date(a.created_at || 0).getTime();
      const tb = new Date(b.created_at || 0).getTime();
      return tb - ta;
    })
    .slice(0, 5);

  const dataReady = !paymentsLoading && !storeLoading && !summaryLoading;
  const figuresReady = dataReady && summaryError === null;
  const anythingFailed = Boolean(storeError || paymentsError || summaryError);

  // Any unknown first segment under /dashboard resolves to this route, so a stale
  // or mistyped store id has to say so instead of rendering an all-zero dashboard.
  if (storeMissing) {
    return (
      <div className="dash-empty">
        Store not found.
        <div className="dash-empty-desc">
          Nothing matches <code className="dash-code-mono">{publicId}</code>. It may
          have been removed.{" "}
          <Link className="dash-link-btn" href="/dashboard/stores">
            Back to stores
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div>
      {anythingFailed && (
        <div className="dash-warn">
          <strong>Some of this store could not be loaded.</strong> Where a figure
          is missing below it is because we could not read it, not because it is
          zero.
          <div className="dash-empty-cta-row">
            <button
              type="button"
              className="dash-btn dash-btn-secondary dash-btn-sm"
              onClick={() => setReloadKey((k) => k + 1)}
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {!storeLoading && store && (
        <section className="dash-hero">
          <div className="dash-hero-left">
            <h1 className="dash-hello">This store at a glance</h1>
            <div className="dash-sub">
              Per-store metrics and recent activity, scoped to this ChmabaPay
              store.
            </div>
          </div>
          <div className="dash-hero-right">
            <div className="dash-plan-card">
              <div className="dash-plan-label">Store details</div>
              <ul className="dash-plan-list">
                <li>
                  {figuresReady
                    ? `${totalPaidCount} total paid ${
                        totalPaidCount === 1 ? "payment" : "payments"
                      }`
                    : "Total paid: —"}
                </li>
                <li>
                  {figuresReady
                    ? `${transactionsCount} total ${
                        transactionsCount === 1 ? "transaction" : "transactions"
                      }`
                    : "Transactions: —"}
                </li>
              </ul>
              {/* Both identifiers are here rather than only the store id: `merchant=`
                  is the friendlier of the two to pass from an integration, and it
                  used to exist only inside the create wizard — set once, then
                  unreadable anywhere afterwards. */}
              <div className="dash-plan-label">Store ID</div>
              <CopyField value={store.id} />
              {store.external_id ? (
                <>
                  <div className="dash-plan-label">Merchant ID</div>
                  <CopyField value={store.external_id} />
                  <div className="dash-hint">
                    Pass this as <code>merchant=</code> instead of the store ID.
                  </div>
                </>
              ) : (
                <div className="dash-hint">
                  No merchant ID set. Add one in Settings to create payments with{" "}
                  <code>merchant=</code> instead of the store ID.
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      <section aria-label="Store metrics" className="dash-metrics">
        <div className="dash-stat-card dash-stat-accent">
          <div className="dash-stat-label">Paid today</div>
          <div className="dash-stat-value">
            {figuresReady ? formatDollars(paidTodayCents) : "—"}
          </div>
          <div className="dash-stat-trend">
            {figuresReady
              ? `${paidTodayCount} ${
                  paidTodayCount === 1 ? "payment" : "payments"
                }`
              : "—"}
          </div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Settled</div>
          <div className="dash-stat-value">
            {figuresReady ? formatDollars(settledCents) : "—"}
          </div>
          <div className="dash-stat-trend">
            {!figuresReady
              ? "—"
              : refundedCount > 0
                ? `all time · ${refundedCount} refunded (${formatDollars(refundedCents)})`
                : "all time · no refunds"}
          </div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Avg. payment</div>
          <div className="dash-stat-value">
            {figuresReady ? formatDollars(avgCents) : "—"}
          </div>
          <div className="dash-stat-trend">
            {figuresReady ? `${totalPaidCount} total paid` : "—"}
          </div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Transactions</div>
          <div className="dash-stat-value">
            {figuresReady ? String(transactionsCount) : "—"}
          </div>
          <div className="dash-stat-trend">
            <Link
              className="dash-link-btn"
              href={`/dashboard/${publicId}/payments`}
            >
              All payments
            </Link>
          </div>
        </div>
      </section>

      <section
        aria-label="Getting started and activity"
        className="dash-panels"
      >
        <div className="dash-panel">
          <div className="dash-panel-title">Quick setup for this store</div>
          <ol className="dash-steps">
            <li>
              <div className="dash-step-num">1</div>
              <div>
                <div className="dash-step-title">
                  <Link
                    href={`/dashboard/${publicId}/settings`}
                    className="dash-link-btn"
                  >
                    Configure store settings
                  </Link>
                </div>
                <div className="dash-step-desc">
                  Set brand color, success and failure redirect URLs, and
                  Telegram notifications in Settings.
                </div>
              </div>
            </li>
            <li>
              <div className="dash-step-num">2</div>
              <div>
                <div className="dash-step-title">
                  <Link
                    href="/dashboard/keys"
                    className="dash-link-btn"
                  >
                    Create a workspace API key
                  </Link>
                </div>
                <div className="dash-step-desc">
                  One key covers every store in your workspace. Pass this
                  store&apos;s id when creating a payment to target it.
                </div>
              </div>
            </li>
            <li>
              <div className="dash-step-num">3</div>
              <div>
                <div className="dash-step-title">
                  <Link
                    href="/dashboard/webhooks"
                    className="dash-link-btn"
                  >
                    Add a webhook endpoint
                  </Link>
                </div>
                <div className="dash-step-desc">
                  Receive real-time events for payment.completed,
                  payment.expired, payment.reversed and more across all your
                  stores.
                </div>
              </div>
            </li>
          </ol>
        </div>

        <div className="dash-panel">
          <div className="dash-panel-title">Recent activity</div>
          {paymentsLoading ? (
            <div className="dash-empty">Loading activity…</div>
          ) : paymentsError ? (
            <div className="dash-warn">
              Recent activity could not be loaded. This is not a quiet store.
              <div className="dash-empty-cta-row">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary dash-btn-sm"
                  onClick={() => setReloadKey((k) => k + 1)}
                >
                  Retry
                </button>
              </div>
            </div>
          ) : recentPayments.length === 0 && dataReady ? (
            <div className="dash-empty">
              No payments yet for this store.
              <div className="dash-empty-desc">
                Once you create a payment using this store&apos;s API key or
                checkout link, it will appear here with status updates.
                <div className="dash-empty-cta-row">
                  <Link
                    className="dash-btn dash-btn-primary dash-btn-sm"
                    href={`/dashboard/${publicId}/payments`}
                  >
                    Go to Payments
                  </Link>
                </div>
              </div>
            </div>
          ) : (
            <div className="dash-activity-list">
              {recentPayments.map((p) => (
                <Link
                  key={String(p.id)}
                  href={`/dashboard/payments/${p.id}`}
                  className="dash-activity-row"
                >
                  <div className="dash-activity-left">
                    <span
                      className={pillClassForStatus(p.status || "pending")}
                    >
                      {(p.status || "pending").toLowerCase()}
                    </span>
                    <div className="dash-activity-meta">
                      <div className="dash-activity-ref">
                        {p.reference_id ||
                          `#${String(p.id).slice(0, 8)}`}
                      </div>
                      <div className="dash-activity-time">
                        {timeAgo(p.created_at)}
                      </div>
                    </div>
                  </div>
                  <div className="dash-activity-right">
                    <div className="dash-activity-amount">
                      {formatDollars(amountCents(p))}
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
