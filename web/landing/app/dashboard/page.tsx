"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { readApiError } from "@/components/portal/apiError";
import { useSession, type Profile } from "@/components/portal/useSession";

type Payment = {
  id: string | number;
  // `GET /api/v1/payments` returns `amount` as a decimal string; the reports
  // summary returns integer cents. Both shapes are accepted.
  amount?: string | number | null;
  amount_cents?: number;
  status: string;
  reference_id?: string | null;
  created_at?: string | null;
  [k: string]: unknown;
};

type ReportSummary = {
  total_matching_paid_count: number;
  total_matching_paid_amount_cents: number;
  // Refunds are counted separately because the paid totals above exclude them —
  // a reversed payment is no longer `paid`. Without these the "Settled" card can
  // only show a number that dropped for a reason it cannot name.
  total_matching_reversed_count?: number;
  total_matching_reversed_amount_cents?: number;
};

type Store = {
  id: string | number;
  name?: string;
  status?: string;
  created_at?: string | null;
  [k: string]: unknown;
};

type PlanInfo = {
  name?: string;
  code?: string;
  [k: string]: unknown;
};

type Subscription = {
  plan?: PlanInfo | string | null;
  // Top-level on the API response, and deliberately so: a lapsed account can owe money with no
  // subscription row left to hang it on. `current_period_end` is when the paid coverage ends,
  // which is the date the plan card shows.
  current_period_end?: string | null;
  outstanding_invoice_id?: number | null;
  [k: string]: unknown;
};

function formatDollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function amountCents(p: Payment): number {
  if (typeof p.amount_cents === "number") return p.amount_cents;
  const n = Number.parseFloat(String(p.amount ?? ""));
  return Number.isFinite(n) ? Math.round(n * 100) : 0;
}

function isoDateParam(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
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

function storePillClassForStatus(status: string): string {
  const s = (status || "").toLowerCase();
  if (s === "active") return "dash-pill dash-pill-paid";
  return "dash-pill dash-pill-expired";
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function planName(profile: Profile | null, subscription: Subscription | null): string {
  const p = subscription?.plan;
  if (typeof p === "object" && p !== null) return p.name || "Free";
  if (typeof p === "string") return p;
  if (profile?.is_platform_admin) return "Pro (admin)";
  return "Free";
}

export default function DashboardOverviewPage() {
  const { profile, loading } = useSession();

  const [paymentsLoading, setPaymentsLoading] = useState(true);
  const [storesLoading, setStoresLoading] = useState(true);
  const [paymentsList, setPaymentsList] = useState<Payment[]>([]);
  const [storesList, setStoresList] = useState<Store[]>([]);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryAll, setSummaryAll] = useState<ReportSummary | null>(null);
  const [summaryToday, setSummaryToday] = useState<ReportSummary | null>(null);
  // A failed fetch used to be indistinguishable from an empty account: the cards
  // read $0.00 and "No stores yet" whether the account was new or the API was down.
  // These hold the reason so the page can say what it does not know.
  const [paymentsError, setPaymentsError] = useState<string | null>(null);
  const [storesError, setStoresError] = useState<string | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    setPaymentsLoading(true);
    setPaymentsError(null);
    (async () => {
      try {
        const res = await fetch("/api/v1/payments?limit=200", {
          credentials: "include",
        });
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
  }, [reloadKey]);

  useEffect(() => {
    let alive = true;
    setStoresLoading(true);
    setStoresError(null);
    (async () => {
      try {
        const res = await fetch("/api/v1/stores", { credentials: "include" });
        if (!res.ok) throw new Error(await readApiError(res));
        const data = await res.json().catch(() => ({}));
        const items: Store[] = Array.isArray(data)
          ? data
          : Array.isArray(data?.items)
            ? data.items
            : Array.isArray(data?.data)
              ? data.data
              : [];
        if (alive) setStoresList(items);
      } catch (e) {
        if (alive) setStoresError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setStoresLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/v1/billing/subscription", {
          credentials: "include",
        });
        if (res.ok && res.status !== 501) {
          const data = await res.json().catch(() => null);
          if (alive && data) setSubscription(data as Subscription);
        }
      } catch {
      }
    })();
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  useEffect(() => {
    let alive = true;
    setSummaryLoading(true);
    setSummaryError(null);
    (async () => {
      // The reports summary is the only place these totals exist: the payments
      // list returns `amount` without `amount_cents`/`paid_at` and is capped at
      // one page, so summing it silently undercounts.
      try {
        const today = isoDateParam(new Date());
        const [allRes, todayRes] = await Promise.all([
          fetch("/api/v1/reports/payments.json?per_page=1", {
            credentials: "include",
          }),
          fetch(
            `/api/v1/reports/payments.json?from=${today}&to=${today}&per_page=1`,
            { credentials: "include" },
          ),
        ]);
        if (!allRes.ok) throw new Error(await readApiError(allRes));
        const data = await allRes.json().catch(() => null);
        if (alive && data?.summary) setSummaryAll(data.summary);
        if (todayRes.ok) {
          const todayData = await todayRes.json().catch(() => null);
          if (alive && todayData?.summary) setSummaryToday(todayData.summary);
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
  }, [reloadKey]);

  const todayCents = summaryToday?.total_matching_paid_amount_cents ?? 0;
  const todayCount = summaryToday?.total_matching_paid_count ?? 0;
  const allTimeCents = summaryAll?.total_matching_paid_amount_cents ?? 0;
  const allTimeCount = summaryAll?.total_matching_paid_count ?? 0;
  const avgCents =
    allTimeCount > 0 ? Math.round(allTimeCents / allTimeCount) : 0;
  const refundedCount = summaryAll?.total_matching_reversed_count ?? 0;
  const refundedCents = summaryAll?.total_matching_reversed_amount_cents ?? 0;
  const activeStores = storesList.filter(
    (s) => (s.status || "").toLowerCase() === "active",
  ).length;

  const recentPayments = [...paymentsList]
    .sort((a, b) => {
      const ta = new Date(a.created_at || 0).getTime();
      const tb = new Date(b.created_at || 0).getTime();
      return tb - ta;
    })
    .slice(0, 5);

  const dataReady = !paymentsLoading && !storesLoading && !summaryLoading;
  // `isFirstRun` is a claim about the account, so it has to be false when we could
  // not read the stores rather than when there are none.
  const isFirstRun = dataReady && storesError === null && storesList.length === 0;
  const figuresReady = dataReady && summaryError === null;
  const anythingFailed = Boolean(paymentsError || storesError || summaryError);

  if (loading || !profile) {
    return <div className="dash-info">Loading ChmabaPay…</div>;
  }

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Your stores</h1>
          <div className="dash-page-subtitle">
            Each store has its own payment link. Webhooks and API keys belong to the
            whole workspace and serve every store &mdash; as do your plan and
            monthly quota.
          </div>
        </div>
        <div>
          <Link
            className="dash-btn dash-btn-primary"
            href="/dashboard/stores/new"
          >
            + New store
          </Link>
        </div>
      </div>

      {anythingFailed && (
        <div className="dash-warn">
          <strong>Some of this page could not be loaded.</strong> Where a figure is
          missing below it is because we could not read it, not because it is zero.
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

      {isFirstRun && (
        <section aria-label="Get started" className="dash-panels">
          <div className="dash-panel">
            <div className="dash-panel-title">Get started in 3 steps</div>
            <ol className="dash-steps">
              <li>
                <div className="dash-step-num">1</div>
                <div>
                  <div className="dash-step-title">
                    <Link href="/dashboard/keys" className="dash-link-btn">
                      Create your API key
                    </Link>
                  </div>
                  <div className="dash-step-desc">
                    Generate a secret key (ck_live_…) for your server-side
                    integration.
                  </div>
                </div>
              </li>
              <li>
                <div className="dash-step-num">2</div>
                <div>
                  <div className="dash-step-title">
                    <Link href="/dashboard/stores/new" className="dash-link-btn">
                      Add your ABA PayWay link
                    </Link>
                  </div>
                  <div className="dash-step-desc">
                    Paste your ABA PayWay share link to start generating KHQR
                    codes instantly.
                  </div>
                </div>
              </li>
              <li>
                <div className="dash-step-num">3</div>
                <div>
                  <div className="dash-step-title">
                    <Link href="/dashboard/webhooks" className="dash-link-btn">
                      Add a webhook endpoint
                    </Link>
                  </div>
                  <div className="dash-step-desc">
                    Get your signing secret to verify payment events in real
                    time.
                  </div>
                </div>
              </li>
            </ol>
          </div>
        </section>
      )}

      <section aria-label="Metrics" className="dash-metrics">
        <div className="dash-stat-card dash-stat-accent">
          <div className="dash-stat-label">Paid today</div>
          <div className="dash-stat-value">
            {figuresReady ? formatDollars(todayCents) : "—"}
          </div>
          <div className="dash-stat-trend">
            {todayCount} payment{todayCount === 1 ? "" : "s"} today
          </div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Settled</div>
          <div className="dash-stat-value">
            {figuresReady ? formatDollars(allTimeCents) : "—"}
          </div>
          <div className="dash-stat-trend">
            {refundedCount > 0
              ? `all time · ${refundedCount} refunded (${formatDollars(refundedCents)})`
              : "all time · no refunds"}
          </div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Avg. payment</div>
          <div className="dash-stat-value">
            {figuresReady ? formatDollars(avgCents) : "—"}
          </div>
          <div className="dash-stat-trend">{allTimeCount} total paid</div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Active stores</div>
          <div className="dash-stat-value">
            {dataReady && storesError === null ? String(activeStores) : "—"}
          </div>
          <div className="dash-stat-trend">
            <Link className="dash-link-btn" href="/dashboard/stores">
              Manage stores
            </Link>
          </div>
        </div>
      </section>

      <section aria-label="Stores and activity" className="dash-panels">
        <div className="dash-panel">
          <div className="dash-panel-title">Stores</div>
          {storesLoading ? (
            <div className="dash-empty">Loading stores…</div>
          ) : storesError ? (
            <div className="dash-warn">
              Your stores could not be loaded. This is not an empty account.
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
          ) : storesList.length === 0 ? (
            <div className="dash-empty">
              No stores yet.
              <div className="dash-empty-desc">
                Create your first store to connect a payment destination and
                start accepting payments.
                <div className="dash-empty-cta-row">
                  <Link
                    className="dash-btn dash-btn-primary dash-btn-sm"
                    href="/dashboard/stores/new"
                  >
                    + New store
                  </Link>
                </div>
              </div>
            </div>
          ) : (
            <div className="dash-store-grid">
              {storesList.map((store) => (
                <Link
                  key={String(store.id)}
                  href={`/dashboard/${store.id}`}
                  className="dash-panel dash-store-card"
                >
                  <div className="dash-store-card-head">
                    <div className="dash-panel-title">
                      {store.name || `Store #${String(store.id).slice(0, 8)}`}
                    </div>
                    <span
                      className={storePillClassForStatus(store.status || "unknown")}
                    >
                      {(store.status || "unknown").toLowerCase()}
                    </span>
                  </div>
                  <div className="dash-store-card-meta">
                    <div>
                      <span className="dash-store-card-meta-label">Created:</span>{" "}
                      {formatDate(store.created_at)}
                    </div>
                    <div className="dash-store-card-cta">View store →</div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="dash-panel">
          <div className="dash-panel-title">Recent activity</div>
          {paymentsLoading ? (
            <div className="dash-empty">Loading activity…</div>
          ) : paymentsError ? (
            <div className="dash-warn">
              Recent activity could not be loaded. This is not a quiet account.
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
              No payments yet.
              <div className="dash-empty-desc">
                Once you create a store and accept your first payment, it will
                show up here.
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
                    <span className={pillClassForStatus(p.status || "pending")}>
                      {(p.status || "pending").toLowerCase()}
                    </span>
                    <div className="dash-activity-meta">
                      <div className="dash-activity-ref">
                        {p.reference_id || `#${String(p.id).slice(0, 8)}`}
                      </div>
                      <div className="dash-activity-time">{timeAgo(p.created_at)}</div>
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

      <section className="dash-panels" aria-label="Current plan">
        <div className="dash-panel">
          <div className="dash-panel-title">Current plan</div>
          <div className="dash-plan-card">
            <div className="dash-plan-value">
              {planName(profile, subscription)}
            </div>
            {/* The renewal date, or that money is owed. "Payment due" rather than "overdue": an
                unpaid invoice is `outstanding` from the day it is raised — seven days before it is
                due — and the banner above carries the precise urgency copy. This line just names
                the state of the plan for the merchant who is not being warned about anything. */}
            {subscription?.outstanding_invoice_id ? (
              <div className="dash-plan-note dash-plan-note-warn">Payment due</div>
            ) : subscription?.current_period_end ? (
              <div className="dash-plan-note">
                Renews {formatDate(subscription.current_period_end)}
              </div>
            ) : null}
            <div className="dash-actions">
              <Link className="dash-btn dash-btn-secondary" href="/dashboard/billing">
                Manage plan →
              </Link>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
