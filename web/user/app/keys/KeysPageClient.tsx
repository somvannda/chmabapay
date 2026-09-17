"use client";

import React, { useMemo, useState } from "react";
import { brandColors, radius, shadows, fonts } from "@shared/theme";
import { CopyField } from "@shared/components/CopyField";
import { DataTable, type ColumnDef } from "@shared/components/DataTable";
import { StatusBadge } from "@shared/components/StatusBadge";
import { useModal } from "@shared/components/ModalSystem";
import { shouldRenderDarkToggle, type AccountType } from "@shared/ux-laws";

interface KeyRow {
  id: string;
  name: string;
  scope: "account" | "store";
  storeName: string | null;
  keyPrefix: string;
  status: "active" | "revoked";
  created: string;
  lastUsed: string | null;
}

interface KeysPageClientProps {
  accountType: AccountType;
  isPlatformAdmin: boolean;
}

const DEFAULT_ROWS: KeyRow[] = [
  {
    id: "key_demo_01",
    name: "Store API — Sokha Noodles",
    scope: "store",
    storeName: "Sokha Noodles",
    keyPrefix: "sk_test_8f2k19",
    status: "active",
    created: "2026-09-01",
    lastUsed: "12m ago",
  },
  {
    id: "key_demo_02",
    name: "Store Webhooks",
    scope: "store",
    storeName: "Sokha Noodles",
    keyPrefix: "whsec_4j1h82",
    status: "active",
    created: "2026-09-02",
    lastUsed: "2h ago",
  },
];

export function KeysPageClient({ accountType, isPlatformAdmin }: KeysPageClientProps) {
  const { open } = useModal();
  const canCreateAccountKeys = useMemo(
    () => accountType === "business" || isPlatformAdmin,
    [accountType, isPlatformAdmin]
  );
  const showDark = useMemo(
    () => shouldRenderDarkToggle(accountType, isPlatformAdmin),
    [accountType, isPlatformAdmin]
  );

  const rows: KeyRow[] = canCreateAccountKeys
    ? [
        ...DEFAULT_ROWS,
        {
          id: "key_demo_03",
          name: "Account-wide Live Secret",
          scope: "account",
          storeName: null,
          keyPrefix: "sk_live_acct_19",
          status: "active",
          created: "2026-08-20",
          lastUsed: "3m ago",
        },
      ]
    : DEFAULT_ROWS;

  const columns: ColumnDef<KeyRow>[] = [
    {
      key: "name",
      header: "Name",
      accessor: (r) => (
        <div>
          <div style={{ fontWeight: 600, color: brandColors.brandInk, fontSize: "13px" }}>{r.name}</div>
          <div style={{
            display: "inline-flex",
            marginTop: "4px",
            fontFamily: fonts.mono,
            fontSize: "11px",
            color: brandColors.subtleText,
          }}>
            {r.id}
          </div>
        </div>
      ),
    },
    {
      key: "scope",
      header: "Scope",
      accessor: (r) => (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            padding: "4px 10px",
            borderRadius: `${radius.pill}px`,
            fontSize: "11px",
            fontWeight: 700,
            backgroundColor: r.scope === "account" ? brandColors.brandViolet + "18" : "#F0F0F4",
            color: r.scope === "account" ? brandColors.brandViolet : brandColors.subtleText,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          {r.scope}
        </span>
      ),
    },
    {
      key: "keyPrefix",
      header: "Key",
      accessor: (r) => (
        <div style={{ fontFamily: fonts.mono, fontSize: "12px", color: brandColors.brandInk }}>
          {r.keyPrefix}••••••
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      accessor: (r) => (
        r.status === "active"
          ? <StatusBadge kind="paid" label="Active" />
          : <StatusBadge kind="expired" label="Revoked" />
      ),
    },
    {
      key: "lastUsed",
      header: "Last used",
      accessor: (r) => (
        <span style={{ color: brandColors.subtleText, fontSize: "12px" }}>
          {r.lastUsed ?? "Never"}
        </span>
      ),
    },
  ];

  const openCreate = () => {
    open({
      id: "create-key-modal",
      title: "Create new API key",
      confirmText: "Generate key",
      variant: "default",
      content: (
        <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          <div>
            <label style={{
              display: "block", fontSize: "12px", fontWeight: 700,
              color: brandColors.brandInk, marginBottom: "6px",
            }}>Name</label>
            <input
              type="text"
              placeholder="E.g. Node.js backend integration"
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #DEDEE7`,
                fontSize: "13px",
                outline: "none",
              }}
            />
          </div>
          <div>
            <label style={{
              display: "block", fontSize: "12px", fontWeight: 700,
              color: brandColors.brandInk, marginBottom: "6px",
            }}>Scope</label>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <label style={{
                display: "flex", alignItems: "center", gap: "8px",
                padding: "10px 12px",
                border: `1px solid #E5E7EB`,
                borderRadius: `${radius.lg}px`,
                cursor: "pointer",
                fontSize: "13px",
                fontWeight: 500,
                color: brandColors.brandInk,
              }}>
                <input type="radio" name="scope" value="store" defaultChecked />
                <div>
                  <div>Store-scoped key</div>
                  <div style={{ fontSize: "11px", color: brandColors.textBody, fontWeight: 400 }}>
                    Restricted to one store (Individual & Business)
                  </div>
                </div>
              </label>
              <label
                style={{
                  display: "flex", alignItems: "center", gap: "8px",
                  padding: "10px 12px",
                  border: `1px solid ${canCreateAccountKeys ? "#E5E7EB" : "#F0F0F4"}`,
                  borderRadius: `${radius.lg}px`,
                  cursor: canCreateAccountKeys ? "pointer" : "not-allowed",
                  fontSize: "13px",
                  fontWeight: 500,
                  color: canCreateAccountKeys ? brandColors.brandInk : brandColors.subtleText,
                  opacity: canCreateAccountKeys ? 1 : 0.55,
                }}
              >
                <input
                  type="radio" name="scope" value="account"
                  disabled={!canCreateAccountKeys}
                />
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    Account-scoped key
                    {!canCreateAccountKeys && (
                      <span style={{
                        fontSize: "10px", fontWeight: 700,
                        padding: "2px 6px",
                        borderRadius: `${radius.pill}px`,
                        backgroundColor: brandColors.statusFailed + "18",
                        color: brandColors.statusFailed,
                        letterSpacing: "0.05em",
                        textTransform: "uppercase",
                      }}>
                        Business only
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: "11px", color: brandColors.textBody, fontWeight: 400 }}>
                    Access all stores & billing (Business only L6)
                  </div>
                </div>
              </label>
            </div>
          </div>
        </div>
      ),
    });
  };

  if (accountType === "individual") {
    return (
      <div style={{ maxWidth: "900px", margin: "0 auto" }}>
        <div style={{ marginBottom: "24px" }}>
          <h1 style={{
            margin: 0,
            fontSize: "30px",
            fontWeight: 800,
            color: brandColors.brandInk,
            letterSpacing: "-0.02em",
          }}>
            API Keys
          </h1>
          <p style={{ marginTop: "4px", color: brandColors.textBody, fontSize: "14px" }}>
            Manage store-scoped API keys and webhook signing secrets.
          </p>
        </div>

        <div style={{
          padding: "16px 18px",
          borderRadius: `${radius.card}px`,
          backgroundColor: "#FFFBEB",
          border: `1px solid #FDE68A`,
          marginBottom: "20px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "16px",
          flexWrap: "wrap",
        }}>
          <div>
            <div style={{ fontSize: "13px", fontWeight: 700, color: "#92400E" }}>
              Individual plan &mdash; no account-wide keys
            </div>
            <div style={{ fontSize: "12px", color: brandColors.textBody, marginTop: "2px" }}>
              Upgrade to Business to create keys that access all stores.
            </div>
          </div>
          <a
            href="/settings/billing"
            style={{
              padding: "8px 14px",
              borderRadius: `${radius.lg}px`,
              backgroundColor: brandColors.brandViolet,
              color: brandColors.white,
              fontSize: "12px", fontWeight: 700,
              textDecoration: "none",
              boxShadow: shadows.primaryButton,
            }}
          >
            Upgrade to Business
          </a>
        </div>

        <KeysTable rows={rows} columns={columns} onCreate={openCreate} />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto" }}>
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
            API Keys
          </h1>
          <p style={{ marginTop: "4px", color: brandColors.textBody, fontSize: "14px" }}>
            API keys, webhook signing secrets, and account-scope credentials.
          </p>
        </div>
        <button
          type="button"
          onClick={openCreate}
          style={{
            padding: "12px 20px",
            borderRadius: `${radius.lg}px`,
            backgroundColor: brandColors.brandViolet,
            color: brandColors.white,
            border: `1px solid rgba(255,255,255,0.08)`,
            fontSize: "14px",
            fontWeight: 600,
            cursor: "pointer",
            boxShadow: shadows.primaryButton,
          }}
        >
          + Create key
        </button>
      </div>

      <div style={{
        padding: "16px 18px",
        borderRadius: `${radius.card}px`,
        backgroundColor: brandColors.dark.terminalBg,
        border: `1px solid ${brandColors.dark.border}`,
        marginBottom: "22px",
      }}>
        <CopyField
          label="Publishable key (safe in browser / app)"
          value="pk_test_D1splayOnlyDemoKeyDoNotUse0000000"
          masked
        />
      </div>

      <KeysTable rows={rows} columns={columns} onCreate={openCreate} />
    </div>
  );
}

function KeysTable({
  rows, columns, onCreate,
}: { rows: KeyRow[]; columns: ColumnDef<KeyRow>[]; onCreate: () => void }) {
  return (
    <DataTable<KeyRow>
      columns={[
        ...columns,
        {
          key: "actions",
          header: "",
          align: "right",
          accessor: () => (
            <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
              <button
                type="button"
                onClick={onCreate}
                style={{
                  fontSize: "11px", fontWeight: 600,
                  padding: "6px 10px",
                  borderRadius: `${radius.sm}px`,
                  backgroundColor: "transparent",
                  border: `1px solid #E5E7EB`,
                  color: brandColors.brandInk,
                  cursor: "pointer",
                }}
              >
                Rotate
              </button>
              <button
                type="button"
                style={{
                  fontSize: "11px", fontWeight: 600,
                  padding: "6px 10px",
                  borderRadius: `${radius.sm}px`,
                  backgroundColor: "transparent",
                  border: `1px solid #FECACA`,
                  color: brandColors.statusFailed,
                  cursor: "pointer",
                }}
              >
                Revoke
              </button>
            </div>
          ),
        },
      ]}
      data={rows}
      emptyState={
        <div>
          <div style={{ fontWeight: 600, marginBottom: "8px" }}>No keys yet.</div>
          <button onClick={onCreate} style={{
            border: "none", background: "transparent",
            color: brandColors.brandViolet, fontWeight: 600, cursor: "pointer", padding: 0,
          }}>
            Create your first key &rarr;
          </button>
        </div>
      }
    />
  );
}

export default KeysPageClient;
