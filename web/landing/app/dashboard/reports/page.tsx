"use client";

import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/components/portal/apiError";

function csvEscape(value: unknown): string {
  if (value === null || value === undefined) return "";
  const str = String(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

function yyyymmdd(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function defaultFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 30);
  return d.toISOString().slice(0, 10);
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function formatMoney(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

const nf = new Intl.NumberFormat("en-US");

// The same seven states the payments list offers, minus its "All statuses" row:
// here every box unticked *is* "all", and a box that says "all" beside the others
// would be a second way to say it.
const STATUS_OPTIONS = [
  { value: "pending", label: "Pending" },
  { value: "paid", label: "Paid" },
  { value: "scanned", label: "Scanned" },
  { value: "expired", label: "Expired" },
  { value: "failed", label: "Failed" },
  { value: "reversed", label: "Refunded" },
  { value: "superseded", label: "Superseded" },
];

type StoreOption = { id: string; name: string };

type ReportSummary = {
  total_matching_rows: number;
  total_matching_paid_count: number;
  total_matching_paid_amount_formatted: string;
  total_matching_reversed_count: number;
  total_matching_reversed_amount_cents: number;
};

export default function DashboardReportsPage() {
  const [from, setFrom] = useState<string>(defaultFrom());
  const [to, setTo] = useState<string>(today());
  const [storeId, setStoreId] = useState("");
  const [stores, setStores] = useState<StoreOption[]>([]);
  const [merchant, setMerchant] = useState("");
  // What the totals were counted with. Typing an external id must not fire a count per
  // keystroke, so the text applies on blur or Enter — the split the console's account
  // search uses. The download reads the field itself, so a click that arrives before
  // blur still exports what is on screen.
  const [appliedMerchant, setAppliedMerchant] = useState("");
  const [statuses, setStatuses] = useState<string[]>([]);
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [paymentsBusy, setPaymentsBusy] = useState(false);
  const [storesBusy, setStoresBusy] = useState(false);
  const [paymentsError, setPaymentsError] = useState<string | null>(null);
  const [storesError, setStoresError] = useState<string | null>(null);

  // One place builds the query, so the export and the totals can never disagree about
  // what is being filtered — the whole point of showing a count above a download.
  const filterParams = useCallback(
    (merchantValue: string) => {
      const params = new URLSearchParams();
      if (from) params.set("from", from);
      if (to) params.set("to", to);
      if (storeId) params.set("store_id", storeId);
      if (merchantValue) params.set("merchant", merchantValue);
      if (statuses.length > 0) params.set("statuses", statuses.join(","));
      return params;
    },
    [from, to, storeId, statuses],
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/stores", { credentials: "include" });
        if (!res.ok) return;
        const data = await res.json().catch(() => ({}));
        const items: StoreOption[] = Array.isArray(data)
          ? data
          : Array.isArray(data?.items)
            ? data.items
            : Array.isArray(data?.data)
              ? data.data
              : [];
        if (alive) setStores(items);
      } catch {
        // Not fatal: without the list the store filter is simply not offered, and the
        // export still works over the whole workspace.
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    setSummaryLoading(true);
    setSummaryError(null);
    (async () => {
      try {
        const params = filterParams(appliedMerchant);
        // `per_page=1`: the totals are computed over the whole match, not this page —
        // asking for one row keeps the payload to a single payment.
        params.set("page", "1");
        params.set("per_page", "1");
        const res = await fetch(`/v1/reports/payments.json?${params.toString()}`, {
          credentials: "include",
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const data = await res.json().catch(() => ({}));
        if (!alive) return;
        setSummary((data?.summary as ReportSummary) ?? null);
      } catch (e) {
        if (!alive) return;
        // A total that could not be read is not a total of zero, so the cards go back
        // to an em dash rather than showing a number nobody counted.
        setSummary(null);
        setSummaryError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setSummaryLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [filterParams, appliedMerchant]);

  const toggleStatus = (value: string) => {
    setStatuses((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value],
    );
  };

  async function handleDownloadPayments() {
    setPaymentsError(null);
    setPaymentsBusy(true);
    try {
      const params = filterParams(merchant.trim());
      const res = await fetch(`/v1/reports/payments.csv?${params.toString()}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const blob = await res.blob();
      triggerDownload(blob, `chmabapay-payments-${yyyymmdd(new Date())}.csv`);
    } catch (e) {
      setPaymentsError(e instanceof Error ? e.message : String(e));
    } finally {
      setPaymentsBusy(false);
    }
  }

  async function handleDownloadStores() {
    setStoresError(null);
    setStoresBusy(true);
    try {
      const res = await fetch("/v1/stores?limit=500", { credentials: "include" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json().catch(() => ({}));
      const items: Record<string, unknown>[] = Array.isArray(data)
        ? data
        : Array.isArray(data?.items)
          ? data.items
          : Array.isArray(data?.data)
            ? data.data
            : [];

      const header = ["ID", "Name", "Status", "City", "Created At"];
      const rows = items.map((s) => [
        s.id ?? "",
        s.name ?? "",
        s.status ?? "",
        s.city ?? "",
        s.created_at ?? "",
      ]);
      const csvText =
        [header, ...rows].map((row) => row.map(csvEscape).join(",")).join("\n") + "\n";
      triggerDownload(
        new Blob([csvText], { type: "text/csv;charset=utf-8;" }),
        `chmabapay-stores-${yyyymmdd(new Date())}.csv`,
      );
    } catch (e) {
      setStoresError(e instanceof Error ? e.message : String(e));
    } finally {
      setStoresBusy(false);
    }
  }

  // `null` while a recount is in flight, so the cards fall back to an em dash instead
  // of showing the previous filters' numbers under the new ones.
  const totals = summaryLoading ? null : summary;

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Reports</h1>
          <div className="dash-page-subtitle">
            Export transaction and store data for accounting
          </div>
        </div>
      </div>

      <section aria-label="Exports" className="dash-panels">
        <div className="dash-panel">
          <div className="dash-panel-title">Payments export</div>
          <div className="dash-info">
            Every payment in your workspace with its status, amount, currency,
            store, reference and the paid, created and approved timestamps.
          </div>

          <div className="dash-form-row">
            <div className="dash-field">
              <label htmlFor="rep-from">From</label>
              <input
                id="rep-from"
                className="dash-input"
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
              />
            </div>
            <div className="dash-field">
              <label htmlFor="rep-to">To</label>
              <input
                id="rep-to"
                className="dash-input"
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
              />
            </div>
            <div className="dash-field">
              <label htmlFor="rep-store">Store</label>
              <select
                id="rep-store"
                className="dash-select"
                value={storeId}
                onChange={(e) => setStoreId(e.target.value)}
              >
                <option value="">All stores</option>
                {stores.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="dash-field">
              <label htmlFor="rep-merchant">External ID</label>
              <input
                id="rep-merchant"
                className="dash-input"
                type="text"
                value={merchant}
                onChange={(e) => setMerchant(e.target.value)}
                onBlur={() => setAppliedMerchant(merchant.trim())}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    setAppliedMerchant(merchant.trim());
                  }
                }}
                placeholder="your-store-reference"
              />
              <div className="dash-hint">
                The store&rsquo;s external ID, matched exactly. Press Enter to
                recount.
              </div>
            </div>
          </div>

          <div className="dash-field">
            <label>Statuses</label>
            <div className="dash-form-row">
              {STATUS_OPTIONS.map((opt) => (
                <label key={opt.value} className="dash-field">
                  <div className="dash-toolbar-filters">
                    <input
                      type="checkbox"
                      checked={statuses.includes(opt.value)}
                      onChange={() => toggleStatus(opt.value)}
                    />
                    <span>{opt.label}</span>
                  </div>
                </label>
              ))}
            </div>
            <div className="dash-hint">
              Leave every box empty to export all statuses.
            </div>
          </div>

          {summaryError ? (
            <div className="dash-warn">
              Could not count the matching payments: {summaryError}. The export below
              still uses these filters.
            </div>
          ) : (
            <section aria-label="Matching totals" className="dash-metrics">
              <div className="dash-stat-card">
                <div className="dash-stat-label">Payments matched</div>
                <div className="dash-stat-value">
                  {totals ? nf.format(totals.total_matching_rows) : "—"}
                </div>
                <div className="dash-stat-trend">with these filters</div>
              </div>
              <div className="dash-stat-card dash-stat-accent">
                <div className="dash-stat-label">Paid</div>
                <div className="dash-stat-value">
                  {totals ? `$${totals.total_matching_paid_amount_formatted}` : "—"}
                </div>
                <div className="dash-stat-trend">
                  {totals
                    ? `${nf.format(totals.total_matching_paid_count)} payment${
                        totals.total_matching_paid_count === 1 ? "" : "s"
                      }`
                    : "—"}
                </div>
              </div>
              <div className="dash-stat-card">
                <div className="dash-stat-label">Refunded</div>
                <div className="dash-stat-value">
                  {totals
                    ? formatMoney(totals.total_matching_reversed_amount_cents)
                    : "—"}
                </div>
                <div className="dash-stat-trend">
                  {totals
                    ? `${nf.format(totals.total_matching_reversed_count)} payment${
                        totals.total_matching_reversed_count === 1 ? "" : "s"
                      }`
                    : "—"}
                </div>
              </div>
            </section>
          )}

          <div>
            <button
              type="button"
              className="dash-btn dash-btn-primary dash-btn-sm"
              onClick={handleDownloadPayments}
              disabled={paymentsBusy}
            >
              {paymentsBusy ? "Preparing…" : "Download CSV"}
            </button>
          </div>
          {paymentsError && <div className="dash-warn">{paymentsError}</div>}
        </div>

        <div className="dash-panel">
          <div className="dash-panel-title">Stores catalog</div>
          <div className="dash-info">
            Every store with its id, name, status, city and created date.
          </div>
          <div>
            <button
              type="button"
              className="dash-btn dash-btn-secondary dash-btn-sm"
              onClick={handleDownloadStores}
              disabled={storesBusy}
            >
              {storesBusy ? "Preparing…" : "Download CSV"}
            </button>
          </div>
          {storesError && <div className="dash-warn">{storesError}</div>}
        </div>
      </section>
    </>
  );
}
