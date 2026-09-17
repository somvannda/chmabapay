"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type StoreOption = {
  id: string;
  name: string;
  status?: string;
  [k: string]: unknown;
};

type Payment = {
  id: string;
  status: string;
  amount: string;
  currency?: string;
  reference_id?: string | null;
  store?: string;
  store_name?: string;
  created_at?: string | null;
  [k: string]: unknown;
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatAmount(str: string | null | undefined): string {
  if (!str) return "$0.00";
  const n = parseFloat(str);
  if (Number.isNaN(n)) return str;
  return `$${n.toFixed(2)}`;
}

function pillClassForStatus(status: string): string {
  switch ((status || "").toLowerCase()) {
    case "paid":
      return "dash-pill dash-pill-paid";
    case "scanned":
      return "dash-pill dash-pill-scanned";
    case "expired":
      return "dash-pill dash-pill-expired";
    case "failed":
      return "dash-pill dash-pill-failed";
    default:
      return "dash-pill dash-pill-pending";
  }
}

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "pending", label: "Pending" },
  { value: "paid", label: "Paid" },
  { value: "scanned", label: "Scanned" },
  { value: "expired", label: "Expired" },
  { value: "failed", label: "Failed" },
];

export default function DashboardPaymentsPage() {
  const [loading, setLoading] = useState(true);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [stores, setStores] = useState<StoreOption[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [storeFilter, setStoreFilter] = useState("");

  const storeNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of stores) m.set(s.id, s.name);
    return m;
  }, [stores]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/stores", { credentials: "include" });
        if (res.ok) {
          const data = await res.json().catch(() => ({}));
          const items: StoreOption[] = Array.isArray(data)
            ? data
            : Array.isArray(data?.items)
              ? data.items
              : Array.isArray(data?.data)
                ? data.data
                : [];
          if (alive) setStores(items);
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
    setLoading(true);
    (async () => {
      try {
        const params = new URLSearchParams();
        params.set("limit", "50");
        if (statusFilter) params.set("status", statusFilter);
        if (storeFilter) params.set("store", storeFilter);
        const res = await fetch(`/v1/payments?${params.toString()}`, {
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
          if (alive) setPayments(items);
        }
      } catch {
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [statusFilter, storeFilter]);

  function storeNameFor(payment: Payment): string {
    if (payment.store_name) return payment.store_name;
    if (payment.store && storeNameMap.has(payment.store)) {
      return storeNameMap.get(payment.store)!;
    }
    if (storeFilter) {
      const s = stores.find((x) => x.id === storeFilter);
      if (s) return s.name;
    }
    return "-";
  }

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h2 className="dash-page-title">Payments</h2>
          <div className="dash-page-subtitle">
            All customer payments and their status
          </div>
        </div>
      </div>

      <div className="dash-toolbar">
        <div className="dash-toolbar-filters">
          <select
            className="dash-select"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value || "all"} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <select
            className="dash-select"
            value={storeFilter}
            onChange={(e) => setStoreFilter(e.target.value)}
          >
            <option value="">All stores</option>
            {stores.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div className="dash-toolbar-filters">
          <Link
            className="dash-btn dash-btn-primary"
            href="/dashboard/payments/new"
          >
            + Create payment
          </Link>
        </div>
      </div>

      <div className="dash-panel">
        {loading ? (
          <div className="dash-empty">Loading payments…</div>
        ) : payments.length === 0 ? (
          <div className="dash-empty">
            No payments yet.
            <div className="dash-empty-desc">
              Create a payment request to share with your customer. They scan
              the QR or open the checkout link to pay.
              <div className="dash-empty-cta-row">
                <Link
                  className="dash-btn dash-btn-primary dash-btn-sm"
                  href="/dashboard/payments/new"
                >
                  + Create payment
                </Link>
              </div>
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Amount</th>
                <th>Reference</th>
                <th>Status</th>
                <th>Store</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {payments.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link
                      className="dash-link-btn"
                      href={`/dashboard/payments/${p.id}`}
                    >
                      #{p.id.slice(0, 8)}
                    </Link>
                  </td>
                  <td>
                    <strong>{formatAmount(p.amount)}</strong>
                  </td>
                  <td>{p.reference_id || "-"}</td>
                  <td>
                    <span className={pillClassForStatus(p.status)}>
                      {(p.status || "pending").toLowerCase()}
                    </span>
                  </td>
                  <td>{storeNameFor(p)}</td>
                  <td>{formatDate(p.created_at)}</td>
                  <td>
                    <Link
                      className="dash-btn dash-btn-secondary dash-btn-sm"
                      href={`/dashboard/payments/${p.id}`}
                    >
                      View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
