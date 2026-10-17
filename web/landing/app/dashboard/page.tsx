"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
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
  // Per-currency paid totals. USD and KHR are different units, so a workspace
  // settling in both must not have them summed into one number.
  total_matching_paid_by_currency?: Record<
    string,
    { cents: number; formatted: string }
  >;
  // Refunds are counted separately because the paid totals above exclude them —
  // a reversed payment is no longer `paid`. Without these the "Settled" card can
  // only show a number that dropped for a reason it cannot name.
  total_matching_reversed_count?: number;
  total_matching_reversed_amount_cents?: number;
  total_matching_reversed_by_currency?: Record<
    string,
    { cents: number; formatted: string }
  >;
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

/**
 * Render a paid total across the currencies it was settled in. A workspace that
 * settles in both USD and KHR gets one figure per currency rather than a single
 * sum, which would be dollars added to riel. Falls back to `fallbackCents` (the
 * summed field) on older API responses that predate the breakdown.
 */
function formatSummaryMoney(
  summary: ReportSummary | null,
  fallbackCents: number,
): string {
  return formatByCurrency(summary?.total_matching_paid_by_currency, fallbackCents);
}

/** One figure per currency, never a sum of dollars and riel. */
function formatByCurrency(
  byCurrency: Record<string, { cents: number; formatted: string }> | undefined,
  fallbackCents: number,
): string {
  if (byCurrency && Object.keys(byCurrency).length > 0) {
    return Object.entries(byCurrency)
      .map(([cur, amount]) =>
        cur === "KHR" ? `៛${amount.formatted}` : `$${amount.formatted}`,
      )
      .join(" · ");
  }
  return formatDollars(fallbackCents);
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

function MetricCard({
  label,
  value,
  change,
  direction = "flat",
  tone = "violet",
  icon,
  detail,
}: {
  label: string;
  value: React.ReactNode;
  change: string;
  direction?: "up" | "down" | "alert" | "flat";
  tone?: "violet" | "lime" | "peach" | "yellow";
  icon: React.ReactNode;
  detail?: React.ReactNode;
}) {
  const tones: Record<string, string> = {
    violet: "bg-[#f0efff] text-[#6957f5] dark:bg-[#2b2850] dark:text-[#b3a7ff]",
    lime: "bg-[#eff9e5] text-[#67a53c] dark:bg-[#1f2f1c]",
    peach: "bg-[#fff1e9] text-[#cb784d] dark:bg-[#352920]",
    yellow: "bg-[#fff5dc] text-[#b18020] dark:bg-[#362c17]",
  };
  const trends: Record<string, string> = {
    up: "text-[#67a53c]",
    down: "text-[#c2564b]",
    alert: "text-[#bd8624]",
    flat: "text-[#92939d]",
  };
  const arrows: Record<string, string> = { up: "↗", down: "↘", alert: "!", flat: "–" };
  return (
    <div className="surface-shadow card-border block w-full rounded-2xl bg-white p-4 text-left dark:bg-[#1f2025] sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-[#858690] dark:text-[#a9aab3]">{label}</p>
          <p className="mt-2 truncate text-[25px] font-extrabold tracking-[-.055em] text-[#202128] dark:text-[#e4e4e8]">
            {value}
          </p>
        </div>
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${tones[tone]}`}
          aria-hidden="true"
        >
          {icon}
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className={`inline-flex items-center gap-1 text-[11px] font-bold ${trends[direction]}`}>
          <span aria-hidden="true">{arrows[direction]}</span>
          {change}
        </span>
        {detail ? <span className="truncate text-[10px] text-[#a4a5ad]">{detail}</span> : null}
      </div>
    </div>
  );
}

function paidAtOf(p: Payment): Date | null {
  const raw = p.paid_at;
  if (typeof raw !== "string") return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

function storeOf(p: Payment): string {
  const raw = p.store;
  return typeof raw === "string" ? raw : "";
}

/** Chmaba POS-style bar chart: thin violet bars with day labels. */
function BarChart({
  data,
  format,
}: {
  data: { label: string; cents: number }[];
  format: (cents: number) => string;
}) {
  const max = Math.max(1, ...data.map((d) => d.cents));
  return (
    <div className="flex h-40 items-end gap-1.5" role="img" aria-label="Payment volume chart">
      {data.map((d) => (
        <div key={d.label} className="flex min-w-0 flex-1 flex-col items-center gap-1.5">
          <div className="flex w-full flex-1 items-end">
            <div
              className="w-full rounded-t-md bg-[#6957f5]/75 transition-all hover:bg-[#6957f5] dark:bg-[#8b7cf7]/75 dark:hover:bg-[#8b7cf7]"
              style={{ height: `${Math.max(2, (d.cents / max) * 100)}%` }}
              title={`${d.label}: ${format(d.cents)}`}
            />
          </div>
          <span className="truncate text-[9px] text-[#92939d]">{d.label}</span>
        </div>
      ))}
    </div>
  );
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

  // — Chmaba POS-style panels: volume trend, status mix, top stores —
  const volumeSeries = useMemo(() => {
    const days = 14;
    const paid = paymentsList.filter((p) => (p.status || "").toLowerCase() === "paid");
    const now = new Date();
    const buckets: { label: string; cents: number }[] = [];
    for (let i = days - 1; i >= 0; i -= 1) {
      const start = new Date(now);
      start.setHours(0, 0, 0, 0);
      start.setDate(start.getDate() - i);
      const end = new Date(start);
      end.setDate(start.getDate() + 1);
      const cents = paid.reduce((sum, p) => {
        const at = paidAtOf(p);
        return at && at >= start && at < end ? sum + amountCents(p) : sum;
      }, 0);
      buckets.push({ label: `${start.getMonth() + 1}/${start.getDate()}`, cents });
    }
    return buckets;
  }, [paymentsList]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of paymentsList) {
      const key = (p.status || "unknown").toLowerCase();
      counts[key] = (counts[key] || 0) + 1;
    }
    return counts;
  }, [paymentsList]);

  const topStores = useMemo(() => {
    const byStore: Record<string, number> = {};
    for (const p of paymentsList) {
      if ((p.status || "").toLowerCase() !== "paid") continue;
      const id = storeOf(p);
      if (!id) continue;
      byStore[id] = (byStore[id] || 0) + amountCents(p);
    }
    return Object.entries(byStore)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([id, cents]) => ({
        id,
        cents,
        name:
          storesList.find((s) => String(s.id) === id)?.name || `Store ${id.slice(0, 8)}`,
      }));
  }, [paymentsList, storesList]);

  const dataReady = !paymentsLoading && !storesLoading && !summaryLoading;
  // `isFirstRun` is a claim about the account, so it has to be false when we could
  // not read the stores rather than when there are none.
  const isFirstRun = dataReady && storesError === null && storesList.length === 0;
  const figuresReady = dataReady && summaryError === null;
  const anythingFailed = Boolean(paymentsError || storesError || summaryError);

  const displayName = profile?.name || profile?.email?.split("@")[0] || "there";
  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const todayLabel = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });

  if (loading || !profile) {
    return <div className="dash-info">Loading ChmabaPay…</div>;
  }

  return (
    <>
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <div className="flex items-center gap-2 text-xs text-[#92939d]">
            <span className="h-2 w-2 rounded-full bg-[#76bc4b]" />
            {todayLabel}
          </div>
          <h1 className="mt-2 text-2xl font-extrabold tracking-[-.05em] text-[#202128] dark:text-[#e4e4e8]">
            {greeting}, {displayName}
          </h1>
          <p className="mt-1 text-sm text-[#898a95]">
            Here is what is happening across your stores today.
          </p>
        </div>
        <Link className="dash-btn dash-btn-primary" href="/dashboard/payments/new">
          <span aria-hidden="true">+</span> New payment
        </Link>
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

      <section
        aria-label="Metrics"
        className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
      >
        <MetricCard
          label="Paid today"
          tone="lime"
          direction="up"
          change={`${todayCount} payment${todayCount === 1 ? "" : "s"}`}
          detail="today"
          icon={
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 7v10M9.5 9.5c0-1 1.1-1.8 2.5-1.8s2.5.8 2.5 1.8-1.1 1.6-2.5 1.9-2.5.9-2.5 1.9 1.1 1.8 2.5 1.8 2.5-.8 2.5-1.8" />
            </svg>
          }
          value={figuresReady ? formatSummaryMoney(summaryToday, todayCents) : "—"}
        />
        <MetricCard
          label="Settled"
          tone="violet"
          direction="flat"
          change={`${allTimeCount} paid`}
          detail={refundedCount > 0 ? `${refundedCount} refunded` : "no refunds"}
          icon={
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 6 9 17l-5-5" />
            </svg>
          }
          value={figuresReady ? formatSummaryMoney(summaryAll, allTimeCents) : "—"}
        />
        <MetricCard
          label="Avg. payment"
          tone="peach"
          direction="flat"
          change="average"
          detail="all time"
          icon={
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 19V5M4 19h16M8 15l3-4 3 2 4-5" />
            </svg>
          }
          value={figuresReady ? formatDollars(avgCents) : "—"}
        />
        <MetricCard
          label="Active stores"
          tone="yellow"
          direction="flat"
          change={`${storesList.length} total`}
          detail={
            <Link className="font-semibold text-[#6957f5] hover:underline" href="/dashboard/stores">
              Manage
            </Link>
          }
          icon={
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 9h16l-1-4H5L4 9Z" />
              <path d="M5 9v10h14V9" />
              <path d="M9 19v-5h6v5" />
            </svg>
          }
          value={dataReady && storesError === null ? String(activeStores) : "—"}
        />
      </section>

      <section aria-label="Insights" className="dash-insights">
        <div className="dash-panel">
          <div>
            <div className="dash-panel-title">Payment volume</div>
            <div className="dash-page-subtitle">Paid volume over the last 14 days.</div>
          </div>
          {paymentsLoading ? (
            <div className="dash-empty">Loading chart…</div>
          ) : (
            <BarChart data={volumeSeries} format={(c) => formatDollars(c)} />
          )}
        </div>

        <div className="dash-panel">
          <div>
            <div className="dash-panel-title">Payment status</div>
            <div className="dash-page-subtitle">
              Across the {paymentsList.length} most recent payments.
            </div>
          </div>
          {paymentsLoading ? (
            <div className="dash-empty">Loading status…</div>
          ) : (
            <div className="flex flex-col gap-2.5">
              {[
                ["paid", "Paid"],
                ["pending", "Pending"],
                ["scanned", "Scanned"],
                ["expired", "Expired"],
                ["failed", "Failed"],
                ["reversed", "Reversed"],
              ].map(([key, label]) => {
                const count = statusCounts[key] || 0;
                const pct = paymentsList.length
                  ? Math.round((count / paymentsList.length) * 100)
                  : 0;
                return (
                  <div key={key} className="flex items-center gap-3">
                    <span className="w-16 shrink-0 text-[11px] font-semibold text-[#696a74] dark:text-[#9a9aa4]">
                      {label}
                    </span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[#f1f1f5] dark:bg-[#2a2b30]">
                      <div
                        className="h-full rounded-full bg-[#6957f5]"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="w-8 shrink-0 text-right text-[11px] font-semibold text-[#92939d]">
                      {count}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="dash-panel">
          <div>
            <div className="dash-panel-title">Top stores</div>
            <div className="dash-page-subtitle">Paid volume by store.</div>
          </div>
          {topStores.length === 0 ? (
            <div className="dash-empty">No paid volume yet.</div>
          ) : (
            <div className="flex flex-col gap-2.5">
              {topStores.map((s) => (
                <div key={s.id} className="flex items-center justify-between gap-3">
                  <span className="truncate text-[13px] text-[#34353d] dark:text-[#c4c5cd]">
                    {s.name}
                  </span>
                  <span className="shrink-0 text-[13px] font-semibold text-[#17181c] dark:text-[#e4e4e8]">
                    {formatDollars(s.cents)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section aria-label="Stores and activity" className="dash-panels">
        <div className="dash-panel">
          <div>
            <div className="dash-panel-title">Stores</div>
            <div className="dash-page-subtitle">Each store has its own payment link.</div>
          </div>
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
            <div className="flex flex-col">
              {storesList.map((store) => (
                <Link
                  key={String(store.id)}
                  href={`/dashboard/${store.id}`}
                  className="group flex items-center justify-between gap-3 border-b border-[#f0f0f4] py-3 last:border-0 dark:border-[#2a2b30]"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#f0efff] text-[11px] font-extrabold text-[#6957f5] dark:bg-[#2b2850] dark:text-[#b3a7ff]">
                      {(store.name || "S").slice(0, 2).toUpperCase()}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-[13px] font-bold text-[#202128] dark:text-[#e4e4e8]">
                        {store.name || `Store #${String(store.id).slice(0, 8)}`}
                      </p>
                      <p className="mt-0.5 text-[11px] text-[#92939d]">
                        Created {formatDate(store.created_at)}
                      </p>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <span className={storePillClassForStatus(store.status || "unknown")}>
                      {(store.status || "unknown").toLowerCase()}
                    </span>
                    <span
                      className="text-[#c2c2c9] transition group-hover:translate-x-0.5 group-hover:text-[#6957f5]"
                      aria-hidden="true"
                    >
                      ›
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="dash-panel">
          <div>
            <div className="dash-panel-title">Recent activity</div>
            <div className="dash-page-subtitle">Latest payments across your stores.</div>
          </div>
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
            <div>
              <div className="grid grid-cols-[auto_1fr_auto] items-center gap-3 border-b border-[#f0f0f3] pb-2 text-[10px] font-bold uppercase tracking-wide text-[#a1a2ab] dark:border-[#2a2b30]">
                <span>Status</span>
                <span>Reference</span>
                <span className="text-right">Amount</span>
              </div>
              {recentPayments.map((p) => (
                <Link
                  key={String(p.id)}
                  href={`/dashboard/payments/${p.id}`}
                  className="grid grid-cols-[auto_1fr_auto] items-center gap-3 border-b border-[#f2f2f5] py-3 last:border-0 hover:bg-[#faf9ff] dark:border-[#2a2b30] dark:hover:bg-[#202127]"
                >
                  <span className={pillClassForStatus(p.status || "pending")}>
                    {(p.status || "pending").toLowerCase()}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-[13px] font-semibold text-[#202128] dark:text-[#e4e4e8]">
                      {p.reference_id || `#${String(p.id).slice(0, 8)}`}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-[#92939d]">
                      {timeAgo(p.created_at)}
                    </span>
                  </span>
                  <span className="text-right text-[13px] font-bold tabular-nums text-[#202128] dark:text-[#e4e4e8]">
                    {formatDollars(amountCents(p))}
                  </span>
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
