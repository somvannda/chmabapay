"use client";

import Link from "next/link";
import { brandColors, radius, shadows } from "@shared/theme";
import { DataTable, type ColumnDef } from "@shared/components/DataTable";
import { StatusBadge } from "@shared/components/StatusBadge";
import { bankBrandChips, type BankCode } from "@shared/ux-laws";

interface PaymentRow {
  id: string;
  amount: string;
  currency: "KHR" | "USD";
  store: string;
  bank: BankCode;
  status: "paid" | "pending" | "scanned" | "failed" | "expired";
  method: string;
  customer: string | null;
  createdAt: string;
}

const rows: PaymentRow[] = [
  { id: "tx_8h2j9k1m", amount: "$48.00", currency: "USD", store: "Sokha Noodles", bank: "ABA", status: "paid", method: "ABA PayWay", customer: "Dara", createdAt: "2026-09-10 09:12" },
  { id: "tx_7g1f8d3c", amount: "12,500 KHR", currency: "KHR", store: "Sokha Noodles", bank: "BAKONG", status: "paid", method: "Bakong KHQR", customer: null, createdAt: "2026-09-10 08:58" },
  { id: "tx_5e6w7q4r", amount: "$5.00", currency: "USD", store: "Sokha Noodles", bank: "ABA", status: "pending", method: "ABA PayWay", customer: "Srey Mao", createdAt: "2026-09-10 08:40" },
  { id: "tx_3a2s1d0f", amount: "$3.20", currency: "USD", store: "Sokha Noodles", bank: "BAKONG", status: "scanned", method: "Bakong KHQR", customer: null, createdAt: "2026-09-10 08:14" },
  { id: "tx_9z0x2c5v", amount: "$22.75", currency: "USD", store: "Sokha Noodles", bank: "WING", status: "paid", method: "Wing Bank", customer: "Bopha", createdAt: "2026-09-09 19:42" },
  { id: "tx_4r5t6y7u", amount: "$1.50", currency: "USD", store: "Sokha Noodles", bank: "BAKONG", status: "expired", method: "Bakong KHQR", customer: null, createdAt: "2026-09-09 18:05" },
  { id: "tx_8i9o0p1q", amount: "$84.00", currency: "USD", store: "Sokha Noodles", bank: "ABA", status: "failed", method: "ABA PayWay", customer: "Raksmey", createdAt: "2026-09-09 15:28" },
];

export default function PaymentsListPage() {
  const columns: ColumnDef<PaymentRow>[] = [
    {
      key: "id",
      header: "ID & Date",
      width: "180px",
      accessor: (r) => (
        <div>
          <div style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: "12px", fontWeight: 600, color: brandColors.brandInk,
          }}>
            {r.id}
          </div>
          <div style={{ fontSize: "11px", color: brandColors.subtleText, marginTop: "2px" }}>{r.createdAt}</div>
        </div>
      ),
    },
    {
      key: "store",
      header: "Store",
      accessor: (r) => (
        <div>
          <div style={{ fontWeight: 600, color: brandColors.brandInk, fontSize: "13px" }}>{r.store}</div>
          <div style={{
            display: "inline-flex", alignItems: "center", gap: "6px",
            marginTop: "3px",
          }}>
            <span style={{
              width: "6px", height: "6px", borderRadius: "50%",
              backgroundColor: bankBrandChips(r.bank).color,
            }} />
            <span style={{ fontSize: "11px", color: brandColors.subtleText, fontWeight: 500 }}>
              {r.method}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "customer",
      header: "Customer",
      accessor: (r) => (
        <span style={{ color: r.customer ? brandColors.brandInk : brandColors.subtleText, fontSize: "13px" }}>
          {r.customer ?? "Anonymous"}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      accessor: (r) => <StatusBadge kind={r.status} />,
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      accessor: (r) => (
        <div style={{ textAlign: "right" }}>
          <div style={{
            fontSize: "14px", fontWeight: 700, color: brandColors.brandInk,
          }}>
            {r.amount}
          </div>
        </div>
      ),
    },
  ];

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto" }}>
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: "16px",
        marginBottom: "24px",
        flexWrap: "wrap",
      }}>
        <div>
          <h1 style={{
            margin: 0,
            fontSize: "30px",
            fontWeight: 800,
            color: brandColors.brandInk,
            letterSpacing: "-0.02em",
          }}>
            Payments
          </h1>
          <p style={{ marginTop: "4px", color: brandColors.textBody, fontSize: "14px" }}>
            Bakong KHQR and ABA PayWay payment history across all stores.
          </p>
        </div>
        <div style={{
          display: "flex", alignItems: "center", gap: "8px",
          padding: "8px 12px",
          backgroundColor: brandColors.white,
          borderRadius: `${radius.lg}px`,
          border: `1px solid #E5E7EB`,
        }}>
          <select
            defaultValue="all"
            style={{
              background: "transparent",
              border: "none",
              fontSize: "13px",
              fontWeight: 500,
              color: brandColors.brandInk,
              outline: "none",
              cursor: "pointer",
            }}
          >
            <option value="all">All stores</option>
            <option value="st_sokha_noodles">Sokha Noodles</option>
          </select>
          <span style={{ width: "1px", height: "16px", backgroundColor: "#E5E7EB" }} />
          <select
            defaultValue="all"
            style={{
              background: "transparent",
              border: "none",
              fontSize: "13px",
              fontWeight: 500,
              color: brandColors.brandInk,
              outline: "none",
              cursor: "pointer",
            }}
          >
            <option value="all">All statuses</option>
            <option value="paid">Paid</option>
            <option value="pending">Pending</option>
            <option value="scanned">Scanned</option>
            <option value="failed">Failed</option>
          </select>
        </div>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
        gap: "12px",
        marginBottom: "24px",
      }}>
        {[
          { label: "Total", value: rows.length, accent: brandColors.brandViolet },
          { label: "Paid", value: rows.filter(r => r.status === "paid").length, accent: brandColors.statusPaid },
          { label: "Pending", value: rows.filter(r => ["pending", "scanned"].includes(r.status)).length, accent: brandColors.statusPending },
          { label: "Failed", value: rows.filter(r => r.status === "failed").length, accent: brandColors.statusFailed },
        ].map((s) => (
          <div
            key={s.label}
            style={{
              padding: "14px 16px",
              backgroundColor: brandColors.white,
              borderRadius: `${radius.card}px`,
              boxShadow: shadows.card,
              border: `1px solid rgba(15,23,42,0.04)`,
            }}
          >
            <div style={{
              display: "flex", alignItems: "center", gap: "6px",
              marginBottom: "6px",
            }}>
              <span style={{
                width: "6px", height: "6px", borderRadius: "50%", backgroundColor: s.accent,
              }} />
              <span style={{
                fontSize: "11px", fontWeight: 600,
                color: brandColors.subtleText,
                letterSpacing: "0.05em",
                textTransform: "uppercase",
              }}>
                {s.label}
              </span>
            </div>
            <div style={{
              fontSize: "22px", fontWeight: 800,
              color: brandColors.brandInk,
            }}>{s.value}</div>
          </div>
        ))}
      </div>

      <DataTable<PaymentRow>
        columns={columns}
        data={rows}
        onRowClick={(r) => {
          if (typeof window !== "undefined") {
            window.location.href = `/payments/${r.id}`;
          }
        }}
      />
    </div>
  );
}
