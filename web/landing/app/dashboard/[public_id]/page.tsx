"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

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
  status?: string;
  created_at?: string | null;
  [k: string]: unknown;
};

function formatDollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatAmountFromStr(str: string | number | null | undefined): number {
  if (!str) return 0;
  if (typeof str === "number") return str;
  const n = parseFloat(str);
  if (Number.isNaN(n)) return 0;
  return Math.round(n * 100);
}

function isToday(iso: string | null | undefined): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  const start = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    0,
    0,
    0,
    0,
  );
  return d >= start;
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

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`/v1/stores/${publicId}`, {
          credentials: "include",
        });
        if (res.status === 404) {
          if (alive) setStoreMissing(true);
          return;
        }
        if (res.ok) {
          const data = await res.json();
          if (alive) setStore(data as StoreDetail);
        }
      } catch {
      } finally {
        if (alive) setStoreLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [publicId]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(
          `/v1/payments?store=${encodeURIComponent(publicId)}&limit=200`,
          { credentials: "include" },
        );
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
  }, [publicId]);

  const paidPayments = paymentsList.filter((p) => p.status === "paid");
  const paidToday = paidPayments.filter((p) => isToday(p.paid_at));
  const paidTodayCents = paidToday.reduce((sum, p) => sum + amountCents(p), 0);
  const settledCents = paidTodayCents;
  const avgCents =
    paidPayments.length > 0
      ? Math.round(
          paidPayments.reduce((s, p) => s + amountCents(p), 0) /
            paidPayments.length,
        )
      : 0;

  const recentPayments = [...paymentsList]
    .sort((a, b) => {
      const ta = new Date(a.created_at || 0).getTime();
      const tb = new Date(b.created_at || 0).getTime();
      return tb - ta;
    })
    .slice(0, 5);

  const dataReady = !paymentsLoading && !storeLoading;

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
      {!storeLoading && store && (
        <section className="dash-hero">
          <div className="dash-hero-left">
            <div className="dash-hello">This store at a glance</div>
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
                  Public ID:{" "}
                  <code className="dash-code-mono">
                    {String(store.id).slice(0, 10)}…
                  </code>
                </li>
                <li>
                  {paidPayments.length} total paid {paidPayments.length === 1 ? "payment" : "payments"}
                </li>
                <li>
                  {paymentsList.length} total {paymentsList.length === 1 ? "transaction" : "transactions"}
                </li>
              </ul>
            </div>
          </div>
        </section>
      )}

      <section aria-label="Store metrics" className="dash-metrics">
        <div className="dash-stat-card dash-stat-accent">
          <div className="dash-stat-label">Paid today</div>
          <div className="dash-stat-value">
            {dataReady ? formatDollars(paidTodayCents) : "—"}
          </div>
          <div className="dash-stat-trend">
            {paidToday.length} {paidToday.length === 1 ? "payment" : "payments"}
          </div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Settled</div>
          <div className="dash-stat-value">
            {dataReady ? formatDollars(settledCents) : "—"}
          </div>
          <div className="dash-stat-trend">No refunds (M1)</div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Avg. payment</div>
          <div className="dash-stat-value">
            {dataReady ? formatDollars(avgCents) : "—"}
          </div>
          <div className="dash-stat-trend">
            {paidPayments.length} total paid
          </div>
        </div>
        <div className="dash-stat-card">
          <div className="dash-stat-label">Transactions</div>
          <div className="dash-stat-value">
            {dataReady ? String(paymentsList.length) : "—"}
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

      <h2 className="dash-payments-section-head">
        Payments · last 14 days
      </h2>

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
                  payment.scanned and more across all your stores.
                </div>
              </div>
            </li>
          </ol>
        </div>

        <div className="dash-panel">
          <div className="dash-panel-title">Recent activity</div>
          {recentPayments.length === 0 && dataReady ? (
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
