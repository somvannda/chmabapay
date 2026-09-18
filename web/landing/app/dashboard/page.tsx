"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useSession, type Profile } from "@/components/portal/useSession";

type Payment = {
  id: string | number;
  // `GET /v1/payments` returns `amount` as a decimal string; the reports
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

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/payments?limit=200", {
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json().catch(() => ({}));
          const items: Payment[] = Array.isArray(data)
            ? data
            : Array.isArray(data?.items)
              ? data.items
              : Array.isArray(data?.data)
                ? data.data
                : [];
          if (alive) setPaymentsList(items);
        }
      } catch {
      } finally {
        if (alive) setPaymentsLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/stores", { credentials: "include" });
        if (res.ok) {
          const data = await res.json().catch(() => ({}));
          const items: Store[] = Array.isArray(data)
            ? data
            : Array.isArray(data?.items)
              ? data.items
              : Array.isArray(data?.data)
                ? data.data
                : [];
          if (alive) setStoresList(items);
        }
      } catch {
      } finally {
        if (alive) setStoresLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/billing/subscription", {
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
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      // The reports summary is the only place these totals exist: the payments
      // list returns `amount` without `amount_cents`/`paid_at` and is capped at
      // one page, so summing it silently undercounts.
      try {
        const today = isoDateParam(new Date());
        const [allRes, todayRes] = await Promise.all([
          fetch("/v1/reports/payments.json?per_page=1", {
            credentials: "include",
          }),
          fetch(
            `/v1/reports/payments.json?from=${today}&to=${today}&per_page=1`,
            { credentials: "include" },
          ),
        ]);
        if (allRes.ok) {
          const data = await allRes.json().catch(() => null);
          if (alive && data?.summary) setSummaryAll(data.summary);
        }
        if (todayRes.ok) {
          const data = await todayRes.json().catch(() => null);
          if (alive && data?.summary) setSummaryToday(data.summary);
        }
      } catch {
      } finally {
        if (alive) setSummaryLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

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
  const isFirstRun = dataReady && storesList.length === 0;

  if (loading || !profile) {
    return <div className="dash-info">Loading ChmabaPay…</div>;
  }

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Your stores</h1>
          <div className="dash-page-subtitle">
            Each store has its own payment link, webhooks, and API keys. Your
            plan &amp; monthly quota are shared across all of them.
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
            {dataReady ? formatDollars(todayCents) : "—"}
          </div>
          <div className="dash-stat-trend">
            {todayCount} payment{todayCount === 1 ? "" : "s"} today
          </div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Settled</div>
          <div className="dash-stat-value">
            {dataReady ? formatDollars(allTimeCents) : "—"}
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
            {dataReady ? formatDollars(avgCents) : "—"}
          </div>
          <div className="dash-stat-trend">{allTimeCount} total paid</div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Active stores</div>
          <div className="dash-stat-value">
            {dataReady ? String(activeStores) : "—"}
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
          {recentPayments.length === 0 && dataReady ? (
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
