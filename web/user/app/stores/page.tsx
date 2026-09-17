"use client";

import Link from "next/link";
import { brandColors, radius, shadows } from "@shared/theme";
import { DataTable, type ColumnDef } from "@shared/components/DataTable";
import { StatusBadge } from "@shared/components/StatusBadge";

interface StoreRow {
  id: string;
  name: string;
  email: string;
  destination: string;
  destinationCode: "ABA" | "BAKONG" | "WING";
  status: "active" | "draft";
  createdAt: string;
  totalVolume: string;
}

const banks: Record<string, string> = {
  ABA: brandColors.bank.ABA,
  BAKONG: brandColors.bank.BAKONG,
  WING: brandColors.bank.WING,
};

export default function StoresListPage() {
  const rows: StoreRow[] = [
    {
      id: "st_sokha_noodles",
      name: "Sokha Noodles",
      email: "sokha.noodles@example.com",
      destination: "ABA PayWay — st_sokha_noodles",
      destinationCode: "ABA",
      status: "active",
      createdAt: "2026-09-01",
      totalVolume: "$12,480.50",
    },
  ];

  const columns: ColumnDef<StoreRow>[] = [
    {
      key: "name",
      header: "Store",
      accessor: (r) => (
        <div>
          <div style={{ fontWeight: 600, color: brandColors.brandInk }}>{r.name}</div>
          <div style={{ fontSize: "12px", color: brandColors.textBody }}>{r.email}</div>
        </div>
      ),
    },
    {
      key: "destination",
      header: "Destination",
      accessor: (r) => (
        <div style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
          <span style={{
            width: "6px", height: "6px", borderRadius: "50%",
            backgroundColor: banks[r.destinationCode],
          }} />
          <span style={{ fontSize: "12px", fontWeight: 500 }}>{r.destination}</span>
        </div>
      ),
    },
    {
      key: "totalVolume",
      header: "Volume",
      align: "right",
      accessor: (r) => (
        <span style={{ fontWeight: 600, color: brandColors.brandInk }}>{r.totalVolume}</span>
      ),
    },
    {
      key: "status",
      header: "Status",
      accessor: (r) => (
        r.status === "active"
          ? <StatusBadge kind="paid" label="Active" />
          : <StatusBadge kind="pending" label="Draft" />
      ),
    },
    {
      key: "createdAt",
      header: "Created",
      accessor: (r) => <span style={{ color: brandColors.subtleText, fontSize: "12px" }}>{r.createdAt}</span>,
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
            Stores
          </h1>
          <p style={{ marginTop: "4px", color: brandColors.textBody, fontSize: "14px" }}>
            Manage store profiles, bank destinations, and Bakong KHQR settings.
          </p>
        </div>
        <Link
          href="/stores/new"
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "12px 20px",
            borderRadius: `${radius.lg}px`,
            backgroundColor: brandColors.brandViolet,
            color: brandColors.white,
            fontWeight: 600,
            fontSize: "14px",
            textDecoration: "none",
            border: `1px solid rgba(255,255,255,0.08)`,
            boxShadow: shadows.primaryButton,
          }}
        >
          + New Store
        </Link>
      </div>

      <DataTable<StoreRow>
        columns={columns}
        data={rows}
        emptyState={
          <div>
            <div style={{ fontWeight: 600, marginBottom: "8px" }}>No stores yet.</div>
            <Link href="/stores/new" style={{ color: brandColors.brandViolet, textDecoration: "none", fontWeight: 600 }}>
              Create your first store &rarr;
            </Link>
          </div>
        }
      />
    </div>
  );
}
