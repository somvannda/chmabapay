"use client";

import React, { useCallback, useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { brandColors, radius, shadows } from "@shared/theme";
import { StatusBadge } from "@shared/components/StatusBadge";
import { showKhmerFirst } from "@shared/ux-laws";

type PlanKey = string;

interface Plan {
  key: PlanKey;
  kind: "plan-starter" | "plan-growth" | "plan-scale" | "plan-enterprise";
  price: string;
  priceSubtitle: string;
  mostPopular?: boolean;
  badgeLabel: string;
  tagline: string | null;
  nameKm: string;
  nameEn: string;
  features: { km: string; en: string }[];
  ctaLabel: string;
  accountType: "individual" | "business";
  accentColor: string;
  perMonth: number;
}

/** Shape returned by GET /v1/billing/plans (admin-console owned). */
interface ApiPlan {
  code: string;
  name: string;
  monthly_fee_cents: number;
  monthly_fee_formatted: string;
  tagline: string | null;
  features: string[] | null;
  is_featured: boolean;
  base_payments_included: number;
  max_stores: number | null;
  max_keys_per_account: number;
  max_webhooks_per_account: number;
  csv_export_enabled: boolean;
  priority_support: boolean;
}

interface ApiSubscription {
  plan_code?: string;
  plan_name?: string;
  status?: string;
  next_billing_at?: string | null;
}

const ACCENTS = [
  brandColors.brandViolet,
  brandColors.brandLime,
  brandColors.trustBakongTeal,
  brandColors.goldVerified,
];

const KINDS = [
  "plan-starter",
  "plan-growth",
  "plan-scale",
  "plan-enterprise",
] as const;

/**
 * There is no Khmer plan copy in the database (a plan is a name + price + tagline +
 * bullets), so both keys carry the same string and the layout drops the duplicate.
 */
function toUiPlan(plan: ApiPlan, index: number): Plan {
  const bullets =
    plan.features && plan.features.length > 0
      ? plan.features
      : derivedFeatures(plan);
  return {
    key: plan.code,
    kind: KINDS[index % KINDS.length],
    price: plan.monthly_fee_cents === 0 ? "$0" : plan.monthly_fee_formatted,
    priceSubtitle: plan.monthly_fee_cents === 0 ? "" : "/month",
    mostPopular: plan.is_featured,
    badgeLabel: plan.is_featured ? "Most Popular" : plan.name,
    tagline: plan.tagline,
    nameKm: plan.name,
    nameEn: plan.name,
    features: bullets.map((label) => ({ km: label, en: label })),
    ctaLabel: `Choose ${plan.name}`,
    accountType: plan.monthly_fee_cents === 0 ? "individual" : "business",
    accentColor: ACCENTS[index % ACCENTS.length],
    perMonth: plan.monthly_fee_cents / 100,
  };
}

/** For plans that have no bullets of their own yet, read them off the caps. */
function derivedFeatures(plan: ApiPlan): string[] {
  const rows = [
    `${new Intl.NumberFormat("en-US").format(plan.base_payments_included)} payments / month`,
    plan.max_stores === null
      ? "Unlimited stores"
      : plan.max_stores === 1
        ? "1 store"
        : `Up to ${plan.max_stores} stores`,
    `${plan.max_keys_per_account} API key${plan.max_keys_per_account === 1 ? "" : "s"}`,
  ];
  if (plan.csv_export_enabled) rows.push("CSV reports export");
  if (plan.priority_support) rows.push("Priority support");
  return rows;
}

function formatRenewal(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 10);
}

async function readApiError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
  }
  return `HTTP ${res.status}`;
}

interface BillingPageClientProps {
  currentAccountType: "individual" | "business";
}

export const BillingPageClient: React.FC<BillingPageClientProps> = ({
  currentAccountType,
}) => {
  const router = useRouter();
  const kmFirst = showKhmerFirst("settings-billing");

  const [plans, setPlans] = useState<Plan[]>([]);
  const [currentPlan, setCurrentPlan] = useState<PlanKey | null>(null);
  const [renewsAt, setRenewsAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<PlanKey | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [plansRes, subRes] = await Promise.all([
        fetch("/api/v1/billing/plans", { credentials: "include" }),
        fetch("/api/v1/billing/subscription", { credentials: "include" }),
      ]);
      if (!plansRes.ok) throw new Error(await readApiError(plansRes));
      const apiPlans = (await plansRes.json()) as ApiPlan[];
      const uiPlans = apiPlans.map(toUiPlan);
      setPlans(uiPlans);

      let code: string | null = null;
      if (subRes.ok) {
        const sub = (await subRes.json()) as {
          subscription: ApiSubscription | null;
        };
        code = sub.subscription?.plan_code ?? null;
        setRenewsAt(formatRenewal(sub.subscription?.next_billing_at));
      }
      setCurrentPlan(code);
      setSelected(code ?? uiPlans[0]?.key ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const chosen = useMemo(
    () => plans.find((p) => p.key === selected) ?? null,
    [plans, selected]
  );
  const current = useMemo(
    () => plans.find((p) => p.key === currentPlan) ?? null,
    [plans, currentPlan]
  );
  const isCurrentPlan = selected !== null && selected === currentPlan;

  const handleChange = async () => {
    if (!selected || selected === currentPlan || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/v1/billing/change-plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ plan_code: selected }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      await load();
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
      <div style={{
        backgroundColor: brandColors.surfaceCard,
        borderRadius: `${radius.card}px`,
        boxShadow: shadows.card,
        padding: "20px 24px",
        border: `1px solid rgba(15,23,42,0.05)`,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "16px",
        flexWrap: "wrap",
      }}>
        <div>
          <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: brandColors.subtleText, marginBottom: "4px" }}>
            Current Plan
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            <div style={{
              fontSize: "22px", fontWeight: 800,
              color: brandColors.brandInk,
              letterSpacing: "-0.01em",
            }}>
              {kmFirst ? current?.nameKm : current?.nameEn}
              {!current && "—"}
            </div>
            <StatusBadge
              kind={(current?.kind ?? "plan-starter") as any}
              label={current?.nameEn ?? "No plan"}
            />
            <div style={{
              fontSize: "12px", color: brandColors.textBody,
            }}>
              {currentAccountType === "business" ? "Business account" : "Individual account"}
            </div>
          </div>
        </div>
        {renewsAt && (
          <div style={{
            padding: "10px 14px",
            borderRadius: `${radius.lg}px`,
            backgroundColor: "#F0F0F4",
            fontSize: "13px", fontWeight: 600, color: brandColors.brandInk,
          }}>
            Renews: <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{renewsAt}</span>
          </div>
        )}
      </div>

      {error && (
        <div style={{
          padding: "12px 16px",
          borderRadius: `${radius.card}px`,
          backgroundColor: "#FEF2F2",
          border: "1px solid #FECACA",
          color: "#B91C1C",
          fontSize: "13px",
          fontWeight: 600,
        }}>
          {error}
        </div>
      )}

      <div>
        <h2 style={{
          margin: "0 0 14px 0",
          fontSize: "18px", fontWeight: 800, color: brandColors.brandInk,
        }}>
          {kmFirst ? "ជ្រើសរើសផែនការរបស់អ្នក" : "Choose your plan"}
        </h2>
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
          gap: "16px",
          alignItems: "stretch",
        }}>
          {loading ? (
            <div style={{ gridColumn: "1 / -1", fontSize: "13px", color: brandColors.textBody }}>
              Loading plans…
            </div>
          ) : plans.length === 0 ? (
            <div style={{ gridColumn: "1 / -1", fontSize: "13px", color: brandColors.textBody }}>
              No plans are available right now.
            </div>
          ) : plans.map((plan) => {
            const isSelected = selected === plan.key;
            const isCurrent = currentPlan === plan.key;
            return (
              <button
                key={plan.key}
                type="button"
                onClick={() => setSelected(plan.key)}
                style={{
                  textAlign: "left",
                  display: "flex",
                  flexDirection: "column",
                  position: "relative",
                  padding: "22px 20px",
                  borderRadius: `${radius.card}px`,
                  backgroundColor: plan.mostPopular ? "#F6FFE5" : brandColors.white,
                  border: `2px solid ${
                    isSelected
                      ? plan.accentColor
                      : plan.mostPopular
                      ? brandColors.brandLime
                      : "#E5E7EB"
                  }`,
                  cursor: "pointer",
                  boxShadow: plan.mostPopular
                    ? shadows.popularPlan
                    : isSelected
                    ? `0 0 0 4px ${plan.accentColor}1A`
                    : "none",
                  transition: "all 0.15s ease",
                  flex: 1,
                  minHeight: "100%",
                  paddingTop: plan.mostPopular ? "32px" : "22px",
                }}
              >
                {plan.mostPopular && (
                  <span style={{
                    position: "absolute",
                    top: "12px", right: "12px",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "4px",
                    padding: "4px 10px",
                    borderRadius: `${radius.pill}px`,
                    backgroundColor: brandColors.brandLime,
                    color: brandColors.brandInk,
                    fontSize: "10px",
                    fontWeight: 800,
                    letterSpacing: "0.06em",
                    textTransform: "uppercase",
                  }}>
                    Most Popular
                  </span>
                )}
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: "14px",
                }}>
                  <div style={{
                    fontSize: "16px",
                    fontWeight: 800,
                    color: brandColors.brandInk,
                  }}>
                    {kmFirst ? plan.nameKm : plan.nameEn}
                    {kmFirst && plan.nameEn !== plan.nameKm && (
                      <div style={{ fontSize: "11px", fontWeight: 500, color: brandColors.textBody, marginTop: "2px" }}>
                        {plan.nameEn}
                      </div>
                    )}
                  </div>
                  <div style={{
                    width: "20px", height: "20px",
                    borderRadius: "50%",
                    border: `2px solid ${isSelected ? plan.accentColor : "#CBD5E1"}`,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}>
                    {isSelected && (
                      <span style={{
                        width: "10px", height: "10px", borderRadius: "50%",
                        backgroundColor: plan.accentColor,
                      }} />
                    )}
                  </div>
                </div>

                <div style={{ marginBottom: "16px" }}>
                  <span style={{
                    fontSize: "30px",
                    fontWeight: 800,
                    color: brandColors.brandInk,
                    letterSpacing: "-0.02em",
                  }}>
                    {plan.price}
                  </span>
                  <span style={{
                    fontSize: "12px",
                    color: brandColors.textBody,
                    fontWeight: 500,
                    marginLeft: "2px",
                  }}>
                    {plan.priceSubtitle}
                  </span>
                </div>

                {plan.tagline && (
                  <div style={{
                    fontSize: "12px",
                    color: brandColors.textBody,
                    marginBottom: "14px",
                  }}>
                    {plan.tagline}
                  </div>
                )}

                <ul style={{
                  listStyle: "none",
                  padding: 0,
                  margin: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: "8px",
                  flex: 1,
                  marginBottom: "18px",
                }}>
                  {plan.features.map((f, i) => (
                    <li key={i} style={{
                      display: "flex", alignItems: "flex-start", gap: "8px",
                      fontSize: "12px",
                      color: brandColors.brandInk,
                      lineHeight: 1.45,
                    }}>
                      <span style={{
                        flexShrink: 0,
                        color: plan.accentColor,
                        fontWeight: 800,
                        marginTop: "1px",
                      }}>&check;</span>
                      <div>
                        <span style={{ fontWeight: 600 }}>{kmFirst ? f.km : f.en}</span>
                        {kmFirst && f.en !== f.km && (
                          <div style={{ fontSize: "10px", color: brandColors.textBody, fontWeight: 400, marginTop: "1px" }}>
                            {f.en}
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>

                {isCurrent && (
                  <StatusBadge kind={plan.kind as any} label="Current" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {!loading && plans.length > 0 && (
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: "16px",
        flexWrap: "wrap",
        padding: "18px 22px",
        borderRadius: `${radius.card}px`,
        backgroundColor: brandColors.surfaceCard,
        boxShadow: shadows.card,
        border: `1px solid rgba(15,23,42,0.05)`,
      }}>
        <div>
          <div style={{ fontSize: "12px", fontWeight: 600, color: brandColors.subtleText }}>
            {kmFirst ? "ផែនការបានជ្រើសរើស" : "Selected plan"}
          </div>
          <div style={{
            fontSize: "16px", fontWeight: 700,
            color: brandColors.brandInk,
            marginTop: "2px",
          }}>
            {chosen
              ? `${chosen.price} ${chosen.priceSubtitle} — ${kmFirst ? chosen.nameKm : chosen.nameEn}`
              : "—"}
          </div>
          {isCurrentPlan && (
            <div style={{
              marginTop: "4px",
              fontSize: "11px",
              fontWeight: 600,
              color: brandColors.textBody,
            }}>
              {kmFirst
                ? "ផែនការនេះកំពុងដំណើរការ"
                : "This is your current plan."}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={handleChange}
          disabled={isCurrentPlan || !chosen || submitting}
          style={{
            padding: "12px 26px",
            borderRadius: `${radius.lg}px`,
            border: `1px solid rgba(255,255,255,0.08)`,
            backgroundColor: isCurrentPlan || !chosen
              ? "#D1D5DB"
              : chosen.mostPopular
              ? brandColors.brandInk
              : brandColors.brandViolet,
            color: brandColors.white,
            fontWeight: 700,
            fontSize: "14px",
            cursor: isCurrentPlan || !chosen || submitting ? "default" : "pointer",
            boxShadow: !isCurrentPlan && chosen && !submitting
              ? chosen.mostPopular
                ? "none"
                : shadows.primaryButton
              : "none",
          }}
        >
          {submitting ? "Updating..." :
            isCurrentPlan ? "\u2713 Current Plan" :
            chosen ? `Change to ${chosen.nameEn}` : "Select a plan"
          }
        </button>
      </div>
      )}
    </div>
  );
};

export default BillingPageClient;
