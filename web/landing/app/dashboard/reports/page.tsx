"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type PlanInfo = {
  name?: string;
  code?: string;
  csv_export_enabled?: boolean;
};

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

export default function DashboardReportsPage() {
  const [from, setFrom] = useState<string>(defaultFrom());
  const [to, setTo] = useState<string>(today());
  const [csvEnabled, setCsvEnabled] = useState<boolean | null>(null);
  const [paymentsBusy, setPaymentsBusy] = useState(false);
  const [storesBusy, setStoresBusy] = useState(false);
  const [paymentsError, setPaymentsError] = useState<string | null>(null);
  const [storesError, setStoresError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/billing/subscription", {
          credentials: "include",
        });
        if (res.ok && res.status !== 501) {
          const data = await res.json().catch(() => null);
          const plan: PlanInfo | null =
            data && typeof data.plan === "object" ? data.plan : null;
          if (alive && plan) setCsvEnabled(Boolean(plan.csv_export_enabled));
        }
      } catch {
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function handleDownloadPayments() {
    setPaymentsError(null);
    setPaymentsBusy(true);
    try {
      const params = new URLSearchParams();
      if (from) params.set("from", from);
      if (to) params.set("to", to);
      const res = await fetch(`/v1/reports/payments.csv?${params.toString()}`, {
        credentials: "include",
      });
      if (res.status === 403) {
        setCsvEnabled(false);
        setPaymentsError("CSV exports are not available on your current plan.");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
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

  const csvLocked = csvEnabled === false;

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

      {csvLocked && (
        <div className="dash-warn">
          CSV exports are not available on your current plan.{" "}
          <Link className="dash-link-btn" href="/dashboard/billing">
            View plans
          </Link>
        </div>
      )}

      <section aria-label="Exports" className="dash-panels">
        <div className="dash-panel">
          <div className="dash-panel-title">Payments export</div>
          <div className="dash-info">
            Every payment in your workspace with status, amount, store, and
            Bakong references.
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
          </div>
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
            All store records with status, city, owner email, and created dates.
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
