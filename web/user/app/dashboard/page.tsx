import Link from "next/link";
import { brandColors, radius, shadows } from "@shared/theme";
import { StatusBadge } from "@shared/components/StatusBadge";
import { fetchMe } from "../../lib/api";
import { headers } from "next/headers";

interface StatCard {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}

export default async function DashboardPage() {
  const h = headers();
  const cookieHeader = h.get("cookie") ?? undefined;
  const me = await fetchMe(cookieHeader);

  const stats: StatCard[] = [
    { label: "Total Volume", value: "$12,480.50", sub: "+14.2% vs last 7d", accent: brandColors.brandViolet },
    { label: "24h Payments", value: "38", sub: "+6 since yesterday", accent: brandColors.statusPaid },
    { label: "Active Stores", value: `${me?.store_count ?? 1}`, sub: "Live on Bakong", accent: brandColors.trustBakongTeal },
    { label: "Success Rate", value: "98.4%", sub: "Last 30 days", accent: brandColors.brandLime },
  ];

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto" }}>
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: "20px",
        flexWrap: "wrap",
        marginBottom: "24px",
      }}>
        <div>
          <h1 style={{
            margin: 0,
            fontSize: "30px",
            fontWeight: 800,
            color: brandColors.brandInk,
            letterSpacing: "-0.02em",
          }}>
            Welcome back, {me?.full_name ?? me?.email?.split("@")[0] ?? "there"}
          </h1>
          <p style={{ marginTop: "4px", color: brandColors.textBody, fontSize: "14px" }}>
            Here is what is happening with your stores and payments today.
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
            transition: "background-color 0.15s ease",
          }}
        >
          + Create Store
        </Link>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
        gap: "16px",
        marginBottom: "28px",
      }}>
        {stats.map((s) => (
          <div
            key={s.label}
            style={{
              padding: "20px",
              backgroundColor: brandColors.surfaceCard,
              borderRadius: `${radius.card}px`,
              boxShadow: shadows.card,
              border: `1px solid rgba(15,23,42,0.05)`,
            }}
          >
            <div style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              marginBottom: "10px",
            }}>
              <span style={{
                width: "6px", height: "6px", borderRadius: "50%",
                backgroundColor: s.accent,
              }} />
              <span style={{ fontSize: "12px", fontWeight: 600, color: brandColors.subtleText, letterSpacing: "0.04em", textTransform: "uppercase" }}>
                {s.label}
              </span>
            </div>
            <div style={{
              fontSize: "28px",
              fontWeight: 800,
              color: brandColors.brandInk,
              letterSpacing: "-0.02em",
            }}>
              {s.value}
            </div>
            {s.sub && (
              <div style={{ fontSize: "12px", color: brandColors.textBody, marginTop: "4px" }}>
                {s.sub}
              </div>
            )}
          </div>
        ))}
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "2fr 1fr",
        gap: "20px",
      }}>
        <div style={{
          padding: "20px",
          backgroundColor: brandColors.surfaceCard,
          borderRadius: `${radius.card}px`,
          boxShadow: shadows.card,
          border: `1px solid rgba(15,23,42,0.05)`,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "16px" }}>
            <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: brandColors.brandInk }}>
              Recent Payments
            </h3>
            <Link href="/payments" style={{ fontSize: "12px", fontWeight: 600, color: brandColors.brandViolet, textDecoration: "none" }}>
              View all &rarr;
            </Link>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            {[
              { id: "tx_01", amt: "$48.00", store: "Sokha Noodles", status: "paid" as const, time: "2m ago" },
              { id: "tx_02", amt: "$12.50", store: "Sokha Noodles", status: "paid" as const, time: "14m ago" },
              { id: "tx_03", amt: "$5.00", store: "Sokha Noodles", status: "pending" as const, time: "32m ago" },
              { id: "tx_04", amt: "$3.20", store: "Sokha Noodles", status: "scanned" as const, time: "58m ago" },
            ].map((tx) => (
              <div
                key={tx.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "10px 4px",
                  borderBottom: `1px solid #F8FAFC`,
                }}
              >
                <div>
                  <div style={{ fontSize: "14px", fontWeight: 600, color: brandColors.brandInk }}>{tx.store}</div>
                  <div style={{ fontSize: "11px", color: brandColors.subtleText }}>{tx.id} &middot; {tx.time}</div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
                  <span style={{ fontSize: "14px", fontWeight: 700, color: brandColors.brandInk }}>{tx.amt}</span>
                  <StatusBadge kind={tx.status} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div style={{
          padding: "20px",
          backgroundColor: brandColors.surfaceCard,
          borderRadius: `${radius.card}px`,
          boxShadow: shadows.card,
          border: `1px solid rgba(15,23,42,0.05)`,
        }}>
          <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: brandColors.brandInk, marginBottom: "16px" }}>
            Your Stores
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            <div style={{
              padding: "14px",
              borderRadius: `${radius.lg}px`,
              backgroundColor: "#FAFBFC",
              border: `1px solid #F0F0F4`,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: "14px", fontWeight: 700, color: brandColors.brandInk }}>Sokha Noodles</div>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "4px" }}>
                    <span style={{ width: "6px", height: "6px", borderRadius: "50%", backgroundColor: brandColors.trustAbaGreen }} />
                    <span style={{ fontSize: "11px", fontWeight: 500, color: brandColors.subtleText }}>ABA PayWay linked</span>
                  </div>
                </div>
                <StatusBadge kind="plan-starter" label="LIVE" />
              </div>
            </div>
            <Link
              href="/stores/new"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: "6px",
                padding: "12px",
                borderRadius: `${radius.lg}px`,
                border: `1px dashed #CBD5E1`,
                color: brandColors.subtleText,
                fontSize: "13px",
                fontWeight: 600,
                textDecoration: "none",
                transition: "all 0.15s ease",
              }}
            >
              + Add another store
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
