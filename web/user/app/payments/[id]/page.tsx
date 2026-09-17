import Link from "next/link";
import { notFound } from "next/navigation";
import { brandColors, radius, shadows } from "@shared/theme";
import { StatusBadge } from "@shared/components/StatusBadge";
import { KHQR, type KHQRData } from "@shared/components/KHQR";
import { bankBrandChips, type BankCode } from "@shared/ux-laws";

interface PaymentDetail {
  id: string;
  amount: string;
  amountDisplay: string;
  currency: "KHR" | "USD";
  store: string;
  storeId: string;
  bank: BankCode;
  status: "paid" | "pending" | "scanned" | "failed" | "expired";
  method: string;
  customer: string | null;
  createdAt: string;
  settledAt: string | null;
  bakongId?: string;
  attemptHistory: Array<{
    id: string;
    status: string;
    message: string;
    timestamp: string;
  }>;
}

export default function PaymentDetailPage({ params }: { params: { id: string } }) {
  const id = params.id;

  const detail: PaymentDetail | null = id.startsWith("tx_") ? {
    id,
    amount: "$48.00",
    amountDisplay: "48.00 USD",
    currency: "USD",
    store: "Sokha Noodles",
    storeId: "st_sokha_noodles",
    bank: "ABA",
    status: "paid",
    method: "ABA PayWay",
    customer: "Dara",
    createdAt: "2026-09-10 09:12:04",
    settledAt: "2026-09-10 09:12:31",
    attemptHistory: [
      { id: "3", status: "settled", message: "ABA PayWay confirmed. Bakong DLT settlement complete.", timestamp: "09:12:31" },
      { id: "2", status: "paid", message: "ABA account credited.", timestamp: "09:12:14" },
      { id: "1", status: "created", message: "Payment link generated. Waiting for scan.", timestamp: "09:11:58" },
    ],
  } : null;

  if (!detail) notFound();

  const chip = bankBrandChips(detail.bank);
  const khqrData: KHQRData = {
    amount: detail.id,
    amountDisplay: detail.amountDisplay,
    merchantName: detail.store,
    bankCode: detail.bank,
    bakongId: detail.bakongId,
    currency: detail.currency,
  };

  const timelineColors: Record<string, string> = {
    settled: brandColors.statusPaid,
    paid: brandColors.statusPaid,
    created: brandColors.statusPending,
    failed: brandColors.statusFailed,
    scanned: brandColors.statusScanned,
  };

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto" }}>
      <div style={{ marginBottom: "20px" }}>
        <Link
          href="/payments"
          style={{
            display: "inline-flex", alignItems: "center", gap: "6px",
            fontSize: "13px", fontWeight: 600,
            color: brandColors.subtleText,
            textDecoration: "none",
            marginBottom: "14px",
          }}
        >
          &larr; Back to Payments
        </Link>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          gap: "16px", flexWrap: "wrap",
        }}>
          <div>
            <div style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: "13px", fontWeight: 500,
              color: brandColors.subtleText,
              marginBottom: "4px",
            }}>
              {detail.id}
            </div>
            <h1 style={{
              margin: 0,
              fontSize: "30px",
              fontWeight: 800,
              color: brandColors.brandInk,
              letterSpacing: "-0.02em",
              display: "inline-flex", alignItems: "center", gap: "14px",
            }}>
              {detail.amountDisplay}
              <StatusBadge kind={detail.status} />
            </h1>
          </div>
          <div style={{
            display: "inline-flex", alignItems: "center", gap: "8px",
            padding: "8px 12px",
            backgroundColor: brandColors.white,
            borderRadius: `${radius.lg}px`,
            border: `1px solid #E5E7EB`,
          }}>
            <span style={{
              width: "8px", height: "8px", borderRadius: "50%", backgroundColor: chip.color,
            }} />
            <span style={{ fontSize: "12px", fontWeight: 600, color: brandColors.brandInk }}>
              {detail.method}
            </span>
          </div>
        </div>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "1.2fr 1fr",
        gap: "20px",
        alignItems: "start",
      }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div style={{
            backgroundColor: brandColors.surfaceCard,
            borderRadius: `${radius.card}px`,
            boxShadow: shadows.card,
            padding: "22px",
            border: `1px solid rgba(15,23,42,0.05)`,
          }}>
            <h3 style={{
              margin: "0 0 16px 0",
              fontSize: "15px", fontWeight: 700, color: brandColors.brandInk,
            }}>
              Information
            </h3>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px 20px" }}>
              {[
                ["Store", detail.store],
                ["Method", detail.method],
                ["Customer", detail.customer ?? "Anonymous"],
                ["Created", detail.createdAt],
                ["Settled", detail.settledAt ?? "Pending"],
                ["Currency", detail.currency],
              ].map(([k, v]) => (
                <div key={k}>
                  <div style={{
                    fontSize: "11px", fontWeight: 600,
                    color: brandColors.subtleText,
                    letterSpacing: "0.05em",
                    textTransform: "uppercase",
                    marginBottom: "4px",
                  }}>{k}</div>
                  <div style={{
                    fontSize: "13px", fontWeight: 500, color: brandColors.brandInk,
                  }}>{v}</div>
                </div>
              ))}
            </div>
          </div>

          <div style={{
            backgroundColor: brandColors.surfaceCard,
            borderRadius: `${radius.card}px`,
            boxShadow: shadows.card,
            padding: "22px",
            border: `1px solid rgba(15,23,42,0.05)`,
          }}>
            <h3 style={{
              margin: "0 0 20px 0",
              fontSize: "15px", fontWeight: 700, color: brandColors.brandInk,
            }}>
              Attempt History
            </h3>
            <div style={{ position: "relative", paddingLeft: "20px" }}>
              <div
                aria-hidden
                style={{
                  position: "absolute",
                  left: "6px", top: "6px", bottom: "6px",
                  width: "2px",
                  backgroundColor: "#E5E7EB",
                }}
              />
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                {detail.attemptHistory.map((ev) => {
                  const dot = timelineColors[ev.status] ?? brandColors.subtleText;
                  return (
                    <div key={ev.id} style={{ position: "relative" }}>
                      <span
                        style={{
                          position: "absolute",
                          left: "-20px", top: "3px",
                          width: "14px", height: "14px",
                          borderRadius: "50%",
                          backgroundColor: dot,
                          border: `3px solid ${brandColors.white}`,
                          boxShadow: `0 0 0 2px ${dot}40`,
                        }}
                      />
                      <div style={{
                        fontSize: "13px", fontWeight: 600,
                        color: brandColors.brandInk,
                        textTransform: "capitalize",
                      }}>
                        {ev.status}
                        <span style={{
                          marginLeft: "10px",
                          fontSize: "11px", fontWeight: 500,
                          color: brandColors.subtleText,
                        }}>{ev.timestamp}</span>
                      </div>
                      <div style={{
                        marginTop: "3px",
                        fontSize: "12px", color: brandColors.textBody,
                        lineHeight: 1.5,
                      }}>{ev.message}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "16px", alignItems: "center" }}>
          <KHQR data={khqrData} size={200} />

          <div style={{
            width: "100%",
            display: "flex", flexDirection: "column", gap: "8px",
          }}>
            <button
              type="button"
              style={{
                width: "100%",
                padding: "11px 16px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #DEDEE7`,
                backgroundColor: brandColors.white,
                color: brandColors.brandInk,
                fontWeight: 600,
                fontSize: "13px",
                cursor: "pointer",
              }}
            >
              Download Receipt (ABA style)
            </button>
            <button
              type="button"
              style={{
                width: "100%",
                padding: "11px 16px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #DEDEE7`,
                backgroundColor: brandColors.white,
                color: brandColors.brandInk,
                fontWeight: 600,
                fontSize: "13px",
                cursor: "pointer",
              }}
            >
              Resend customer confirmation
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
