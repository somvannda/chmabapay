"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { brandColors, radius, shadows } from "@shared/theme";
import { StepProgress } from "@shared/components/StepProgress";
import { StatusBadge } from "@shared/components/StatusBadge";
import { bankBrandChips, type BankCode } from "@shared/ux-laws";

const DESTINATION_OPTIONS: { code: BankCode; hint: string; type: "payway" | "bakong" | "bank" }[] = [
  { code: "ABA", hint: "Paste ABA PayWay share link", type: "payway" },
  { code: "BAKONG", hint: "Derive from Bakong ID (Style-B)", type: "bakong" },
  { code: "WING", hint: "Wing account number", type: "bank" },
  { code: "ACLB", hint: "ACLEDA account", type: "bank" },
];

export default function NewStoreWizard() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [name, setName] = useState("");
  const [nameKm, setNameKm] = useState("");
  const [email, setEmail] = useState("");
  const [destCode, setDestCode] = useState<BankCode>("ABA");
  const [paywayLink, setPaywayLink] = useState("");
  const [bakongId, setBakongId] = useState("");
  const [accountNo, setAccountNo] = useState("");

  const canNext =
    step === 1 ? name.trim().length > 0 && email.includes("@") :
    step === 2 ? destCode === "ABA"
      ? paywayLink.includes("payway") || paywayLink.includes("aba")
      : destCode === "BAKONG"
      ? bakongId.length >= 6
      : accountNo.length >= 4
    : true;

  const submit = async () => {
    try {
      await fetch("/api/v1/stores", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          name,
          name_km: nameKm,
          email,
          destination_code: destCode,
          payway_link: paywayLink,
          bakong_id: bakongId,
          account_no: accountNo,
        }),
      });
    } catch {
      // Allow demo flow through
    }
    router.push("/stores");
  };

  const chip = bankBrandChips(destCode);

  return (
    <div style={{ maxWidth: "720px", margin: "0 auto" }}>
      <StepProgress steps={3} current={step} pageKey="stores-new" />

      <div style={{
        marginTop: "28px",
        backgroundColor: brandColors.surfaceCard,
        borderRadius: `${radius.card}px`,
        boxShadow: shadows.card,
        padding: "28px 32px",
        border: `1px solid rgba(15,23,42,0.05)`,
      }}>
        {step === 1 && (
          <div>
            <div style={{ marginBottom: "20px" }}>
              <h2 style={{ margin: 0, fontSize: "22px", fontWeight: 800, color: brandColors.brandInk }}>
                <span style={{ fontWeight: 800 }}>ព័ត៌មានអាជីវកម្ម</span>
              </h2>
              <div style={{ fontSize: "13px", color: brandColors.textBody, marginTop: "2px" }}>
                Business Information
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div>
                <label style={{
                  display: "block", fontSize: "13px", fontWeight: 700,
                  color: brandColors.brandInk, marginBottom: "6px",
                }}>
                  <span style={{ display: "block" }}>ឈ្មោះអាជីវកម្ម (ខ្មែរ)</span>
                  <span style={{ fontWeight: 400, fontSize: "11px", color: brandColors.textBody }}>Business Name (Khmer)</span>
                </label>
                <input
                  type="text"
                  value={nameKm}
                  onChange={(e) => setNameKm(e.target.value)}
                  placeholder="ហាងសុខា មី នូឌល"
                  style={{
                    width: "100%",
                    padding: "11px 14px",
                    borderRadius: `${radius.lg}px`,
                    border: `1px solid #DEDEE7`,
                    fontSize: "14px",
                    color: brandColors.brandInk,
                    outline: "none",
                    transition: "border-color 0.15s ease",
                  }}
                />
              </div>

              <div>
                <label style={{
                  display: "block", fontSize: "13px", fontWeight: 700,
                  color: brandColors.brandInk, marginBottom: "6px",
                }}>
                  <span style={{ display: "block" }}>ឈ្មោះអាជីវកម្ម (អង់គ្លេស)</span>
                  <span style={{ fontWeight: 400, fontSize: "11px", color: brandColors.textBody }}>Business Name (English)</span>
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Sokha Noodles"
                  style={{
                    width: "100%",
                    padding: "11px 14px",
                    borderRadius: `${radius.lg}px`,
                    border: `1px solid #DEDEE7`,
                    fontSize: "14px",
                    color: brandColors.brandInk,
                    outline: "none",
                  }}
                />
              </div>

              <div>
                <label style={{
                  display: "block", fontSize: "13px", fontWeight: 700,
                  color: brandColors.brandInk, marginBottom: "6px",
                }}>
                  <span style={{ display: "block" }}>អ៊ីមែលអាជីវកម្ម</span>
                  <span style={{ fontWeight: 400, fontSize: "11px", color: brandColors.textBody }}>Business Email</span>
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="shop@example.com"
                  style={{
                    width: "100%",
                    padding: "11px 14px",
                    borderRadius: `${radius.lg}px`,
                    border: `1px solid #DEDEE7`,
                    fontSize: "14px",
                    color: brandColors.brandInk,
                    outline: "none",
                  }}
                />
              </div>
            </div>
          </div>
        )}

        {step === 2 && (
          <div>
            <div style={{ marginBottom: "20px" }}>
              <h2 style={{ margin: 0, fontSize: "22px", fontWeight: 800, color: brandColors.brandInk }}>
                ជម្រើសការទូទាត់
              </h2>
              <div style={{ fontSize: "13px", color: brandColors.textBody, marginTop: "2px" }}>
                Choose where payments are settled
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "20px" }}>
              {DESTINATION_OPTIONS.map((opt) => {
                const c = bankBrandChips(opt.code);
                const selected = destCode === opt.code;
                return (
                  <button
                    key={opt.code}
                    type="button"
                    onClick={() => setDestCode(opt.code)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "12px",
                      padding: "14px 16px",
                      borderRadius: `${radius.lg}px`,
                      border: `2px solid ${selected ? brandColors.brandViolet : "#E5E7EB"}`,
                      backgroundColor: selected ? brandColors.brandViolet + "0A" : brandColors.white,
                      cursor: "pointer",
                      textAlign: "left",
                      transition: "all 0.15s ease",
                    }}
                  >
                    <span style={{
                      width: "10px", height: "10px", borderRadius: "50%",
                      backgroundColor: c.color, flexShrink: 0,
                    }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: "14px", fontWeight: 700, color: brandColors.brandInk }}>
                        {c.label}
                      </div>
                      <div style={{ fontSize: "12px", color: brandColors.textBody, marginTop: "2px" }}>
                        {opt.hint}
                      </div>
                    </div>
                    {selected && (
                      <span style={{
                        width: "20px", height: "20px", borderRadius: "50%",
                        backgroundColor: brandColors.brandViolet,
                        color: brandColors.white,
                        display: "inline-flex", alignItems: "center", justifyContent: "center",
                        fontSize: "12px", fontWeight: 800,
                      }}>&check;</span>
                    )}
                  </button>
                );
              })}
            </div>

            <div style={{ display: "inline-flex", alignItems: "center", gap: "6px", marginBottom: "14px" }}>
              <StatusBadge kind="paid" label={chip.label} />
              <span style={{ fontSize: "12px", color: brandColors.subtleText }}>selected destination</span>
            </div>

            {destCode === "ABA" && (
              <div>
                <label style={{
                  display: "block", fontSize: "13px", fontWeight: 700,
                  color: brandColors.brandInk, marginBottom: "6px",
                }}>
                  ABA PayWay share link
                </label>
                <input
                  type="text"
                  value={paywayLink}
                  onChange={(e) => setPaywayLink(e.target.value)}
                  placeholder="https://payway.ababank.com/..."
                  style={{
                    width: "100%",
                    padding: "11px 14px",
                    borderRadius: `${radius.lg}px`,
                    border: `1px solid #DEDEE7`,
                    fontSize: "14px",
                    color: brandColors.brandInk,
                    outline: "none",
                  }}
                />
                <p style={{ fontSize: "11px", color: brandColors.textBody, marginTop: "6px", lineHeight: 1.5 }}>
                  Open ABA mobile app &rarr; Payment &rarr; PayWay &rarr; Share link &rarr; paste here. We never store your password.
                </p>
              </div>
            )}

            {destCode === "BAKONG" && (
              <div>
                <label style={{
                  display: "block", fontSize: "13px", fontWeight: 700,
                  color: brandColors.brandInk, marginBottom: "6px",
                }}>
                  Bakong ID
                </label>
                <input
                  type="text"
                  value={bakongId}
                  onChange={(e) => setBakongId(e.target.value)}
                  placeholder="12607..."
                  style={{
                    width: "100%",
                    padding: "11px 14px",
                    borderRadius: `${radius.lg}px`,
                    border: `1px solid #DEDEE7`,
                    fontSize: "14px",
                    color: brandColors.brandInk,
                    outline: "none",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                />
              </div>
            )}

            {(destCode === "WING" || destCode === "ACLB") && (
              <div>
                <label style={{
                  display: "block", fontSize: "13px", fontWeight: 700,
                  color: brandColors.brandInk, marginBottom: "6px",
                }}>
                  {bankBrandChips(destCode).label} Account Number
                </label>
                <input
                  type="text"
                  value={accountNo}
                  onChange={(e) => setAccountNo(e.target.value)}
                  placeholder="Account number"
                  style={{
                    width: "100%",
                    padding: "11px 14px",
                    borderRadius: `${radius.lg}px`,
                    border: `1px solid #DEDEE7`,
                    fontSize: "14px",
                    color: brandColors.brandInk,
                    outline: "none",
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                />
              </div>
            )}
          </div>
        )}

        {step === 3 && (
          <div style={{ textAlign: "center", padding: "12px 0" }}>
            <div style={{
              width: "64px", height: "64px", borderRadius: "50%",
              backgroundColor: brandColors.statusPaid + "1A",
              color: brandColors.statusPaid,
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              fontSize: "32px", fontWeight: 800,
              marginBottom: "20px",
            }}>
              &check;
            </div>
            <h2 style={{ margin: 0, fontSize: "22px", fontWeight: 800, color: brandColors.brandInk }}>
              រួចរាល់
            </h2>
            <div style={{ fontSize: "13px", color: brandColors.textBody, marginTop: "2px", marginBottom: "20px" }}>
              You are ready to accept payments.
            </div>
            <div style={{
              display: "inline-flex",
              flexDirection: "column",
              gap: "8px",
              padding: "16px 20px",
              borderRadius: `${radius.card}px`,
              backgroundColor: "#FAFBFC",
              border: `1px solid #F0F0F4`,
              textAlign: "left",
            }}>
              <div style={{ fontSize: "12px", color: brandColors.subtleText, fontWeight: 600 }}>STORE PREVIEW</div>
              <div style={{ fontSize: "16px", fontWeight: 700, color: brandColors.brandInk }}>
                {name || nameKm || "Your new store"}
              </div>
              <div style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <span style={{
                  width: "6px", height: "6px", borderRadius: "50%", backgroundColor: chip.color,
                }} />
                <span style={{ fontSize: "12px", fontWeight: 600, color: chip.color }}>
                  {chip.label}
                </span>
              </div>
              {email && (
                <div style={{ fontSize: "12px", color: brandColors.textBody }}>{email}</div>
              )}
            </div>
          </div>
        )}

        <div style={{
          display: "flex",
          justifyContent: "space-between",
          marginTop: "28px",
          paddingTop: "20px",
          borderTop: `1px solid #F0F0F4`,
          gap: "10px",
        }}>
          {step > 1 ? (
            <button
              type="button"
              onClick={() => setStep((s) => (s - 1) as 1 | 2 | 3)}
              style={{
                padding: "11px 18px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #DEDEE7`,
                backgroundColor: brandColors.white,
                color: brandColors.brandInk,
                fontWeight: 600,
                fontSize: "14px",
                cursor: "pointer",
              }}
            >
              &larr; Back
            </button>
          ) : (
            <Link
              href="/stores"
              style={{
                padding: "11px 18px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #DEDEE7`,
                backgroundColor: brandColors.white,
                color: brandColors.brandInk,
                fontWeight: 600,
                fontSize: "14px",
                textDecoration: "none",
              }}
            >
              Cancel
            </Link>
          )}

          {step < 3 ? (
            <button
              type="button"
              onClick={() => setStep((s) => (s + 1) as 1 | 2 | 3)}
              disabled={!canNext}
              style={{
                padding: "11px 20px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid rgba(255,255,255,0.08)`,
                backgroundColor: canNext ? brandColors.brandViolet : "#C4B9FF",
                color: brandColors.white,
                fontWeight: 600,
                fontSize: "14px",
                cursor: canNext ? "pointer" : "not-allowed",
                boxShadow: canNext ? shadows.primaryButton : "none",
                transition: "background-color 0.15s ease",
              }}
            >
              Continue &rarr;
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              style={{
                padding: "11px 20px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid rgba(255,255,255,0.08)`,
                backgroundColor: brandColors.brandViolet,
                color: brandColors.white,
                fontWeight: 600,
                fontSize: "14px",
                cursor: "pointer",
                boxShadow: shadows.primaryButton,
              }}
            >
              Create Store &rarr;
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
