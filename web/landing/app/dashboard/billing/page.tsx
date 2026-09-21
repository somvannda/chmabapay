"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { readApiError } from "@/components/portal/apiError";
import { useToast } from "@/components/portal/Toast";

/* ------------------------------------------------------------------ *
 * API types — everything rendered on this page comes from the API.
 * ------------------------------------------------------------------ */

type PlanOut = {
  name: string;
  code: string;
  monthly_fee_cents: number;
  monthly_fee_formatted: string;
  base_payments_included: number;
  max_stores: number | null;
  max_keys_per_account: number;
  max_webhooks_per_account: number;
  priority_support: boolean;
  is_public: boolean;
  tagline?: string | null;
  features?: string[] | null;
};

type SubscriptionInfo = {
  plan_code?: string;
  plan_name?: string;
  status?: string;
  next_billing_at?: string | null;
  trial_ends_at?: string | null;
};

type SubscriptionResponse = {
  subscription: SubscriptionInfo | null;
  plan: PlanOut | null;
};

type Invoice = {
  id: string | number;
  // The API's field is `period_month` (`YYYY-MM`). This read `period`, which the
  // endpoint never returns, so the Period column showed an em dash for every row
  // — invisible while nothing issued invoices, and wrong the moment something did.
  period_month?: string | null;
  period?: string | null;
  status?: string | null;
  base_fee_formatted?: string | null;
  overage_fee_formatted?: string | null;
  total_due_formatted?: string | null;
  paid_at?: string | null;
};

type ChangePlanResponse = {
  subscription?: SubscriptionInfo | null;
  // A paid plan is bought rather than clicked: the route answers with the invoice
  // it raised, and the plan only changes once that invoice is settled.
  payment_required?: boolean;
  invoice?: Invoice | null;
  detail?: string;
  error?: string;
};

/* ------------------------------------------------------------------ *
 * Formatting helpers
 * ------------------------------------------------------------------ */

const nf = new Intl.NumberFormat("en-US");

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function priceParts(cents: number): { amount: string; period: string } {
  if (!cents) return { amount: "$0", period: "/mo" };
  const dollars = cents / 100;
  return {
    amount: `$${dollars % 1 === 0 ? dollars.toFixed(0) : dollars.toFixed(2)}`,
    period: "/mo",
  };
}

function statusPill(status: string | undefined): { className: string; label: string } {
  switch (status) {
    case "trial":
      return { className: "dash-pill dash-pill-scanned", label: "Free trial" };
    case "active":
      return { className: "dash-pill dash-pill-paid", label: "Active" };
    case "canceled":
      return { className: "dash-pill dash-pill-failed", label: "Canceled" };
    default:
      return {
        className: "dash-pill dash-pill-pending",
        label: status ? status : "No subscription",
      };
  }
}

function invoicePill(status: string | undefined | null): { className: string; label: string } {
  switch (status) {
    case "paid":
      return { className: "dash-pill dash-pill-paid", label: "Paid" };
    case "draft":
      return { className: "dash-pill dash-pill-pending", label: "Draft" };
    case "issued":
      return { className: "dash-pill dash-pill-scanned", label: "Issued" };
    case "open":
      // What the billing worker writes when it raises an invoice. "Due" rather than
      // the raw status, which would render as a lowercase "open" in a pill.
      return { className: "dash-pill dash-pill-pending", label: "Due" };
    case "overdue":
      return { className: "dash-pill dash-pill-failed", label: "Overdue" };
    default:
      return { className: "dash-pill dash-pill-pending", label: status || "—" };
  }
}

type Feature = { label: string; on: boolean };

function featuresFor(plan: PlanOut): Feature[] {
  const rows: Feature[] = [];

  // The admin console owns the plan bullets; fall back to deriving them from the
  // numeric caps for plans that have no copy set yet.
  if (plan.features && plan.features.length > 0) {
    for (const label of plan.features) {
      rows.push({ label, on: true });
    }
    return rows;
  }

  rows.push({
    label: `${nf.format(plan.base_payments_included)} payments / month`,
    on: true,
  });

  if (plan.max_stores === null) {
    rows.push({ label: "Unlimited stores", on: true });
  } else {
    rows.push({
      label: plan.max_stores === 1 ? "1 store" : `Up to ${plan.max_stores} stores`,
      on: true,
    });
  }

  rows.push({
    label: `${plan.max_keys_per_account} API key${
      plan.max_keys_per_account === 1 ? "" : "s"
    }`,
    on: true,
  });

  rows.push({
    label: `${plan.max_webhooks_per_account} webhook endpoint${
      plan.max_webhooks_per_account === 1 ? "" : "s"
    }`,
    on: true,
  });

  // CSV export is deliberately absent: it is available on every plan (see the
  // launch-gap-closure spec, D6), so listing it as a ✓ in a per-plan comparison
  // would present a capability as a tier difference.
  rows.push({ label: "Priority support", on: plan.priority_support });

  return rows;
}

/* ------------------------------------------------------------------ *
 * Page
 * ------------------------------------------------------------------ */

export default function BillingPage() {
  const { notify } = useToast();
  const [plans, setPlans] = useState<PlanOut[]>([]);
  const [plansLoading, setPlansLoading] = useState(true);
  const [subscription, setSubscription] = useState<SubscriptionInfo | null>(null);
  const [currentPlan, setCurrentPlan] = useState<PlanOut | null>(null);
  const [subLoading, setSubLoading] = useState(true);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [invoicesLoading, setInvoicesLoading] = useState(true);
  const [usedThisMonth, setUsedThisMonth] = useState<number | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [changingPlanCode, setChangingPlanCode] = useState<string | null>(null);
  // Each panel tracks its own read failure. Rendering one as empty is a claim — "no
  // plans", "no invoices" — and it was being made on behalf of a fetch that had
  // simply failed.
  const [plansError, setPlansError] = useState<string | null>(null);
  const [subError, setSubError] = useState<string | null>(null);
  const [invoicesError, setInvoicesError] = useState<string | null>(null);

  const loadPlans = useCallback(async () => {
    setPlansLoading(true);
    setPlansError(null);
    try {
      const res = await fetch("/v1/billing/plans", { credentials: "include" });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as PlanOut[];
      setPlans(Array.isArray(data) ? data : []);
    } catch (e) {
      setPlansError(e instanceof Error ? e.message : String(e));
    } finally {
      setPlansLoading(false);
    }
  }, []);

  const loadSubscription = useCallback(async () => {
    setSubLoading(true);
    setSubError(null);
    try {
      const res = await fetch("/v1/billing/subscription", {
        credentials: "include",
      });
      // 501 is the documented "no billing here" answer, not a failure.
      if (!res.ok && res.status !== 501) throw new Error(await readApiError(res));
      if (res.ok) {
        const data = (await res.json()) as SubscriptionResponse;
        setSubscription(data.subscription ?? null);
        setCurrentPlan(data.plan ?? null);
      }
    } catch (e) {
      setSubError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubLoading(false);
    }
  }, []);

  const loadUsage = useCallback(async () => {
    try {
      const d = new Date();
      const from = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
      const res = await fetch(
        `/v1/reports/payments.json?from=${from}&per_page=1`,
        { credentials: "include" },
      );
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const count = data?.summary?.total_matching_paid_count;
        if (typeof count === "number") setUsedThisMonth(count);
      }
    } catch {
      // Deliberately silent: this only fills in a usage figure that already renders
      // as "—" while unknown, so there is nothing for the page to misstate.
    }
  }, []);

  const loadInvoices = useCallback(async () => {
    setInvoicesLoading(true);
    setInvoicesError(null);
    try {
      const res = await fetch("/v1/billing/invoices", { credentials: "include" });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json().catch(() => ({}));
      const items: Invoice[] = Array.isArray(data)
        ? data
        : Array.isArray(data?.items)
          ? data.items
          : Array.isArray(data?.data)
            ? data.data
            : [];
      setInvoices(items);
    } catch (e) {
      setInvoicesError(e instanceof Error ? e.message : String(e));
    } finally {
      setInvoicesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPlans();
    void loadSubscription();
    void loadInvoices();
    void loadUsage();
  }, [loadPlans, loadSubscription, loadInvoices, loadUsage]);

  async function handleChangePlan(planCode: string) {
    if (changingPlanCode) return;
    setErrorMsg(null);
    setChangingPlanCode(planCode);
    try {
      const res = await fetch("/v1/billing/change-plan", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_code: planCode }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json().catch(() => ({}))) as ChangePlanResponse;
      await loadSubscription();
      await loadUsage();
      // Keep the sidebar plan card in sync (it is owned by the dashboard layout).
      window.dispatchEvent(new Event("chmabapay:plan-changed"));

      if (data.payment_required) {
        // The plan is parked until the invoice is settled, so "you are now on Pro"
        // would be a claim the API contradicts on the very next load. Say what
        // actually happened, and surface the invoice so it can be paid.
        await loadInvoices();
        const planName = data.subscription?.plan_name ?? planCode;
        const amount = data.invoice?.total_due_formatted;
        notify(
          amount
            ? `Invoice for ${amount} raised — pay it to move to ${planName}.`
            : `Invoice raised — pay it to move to ${planName}.`,
        );
        return;
      }

      notify(`You are now on the ${data.subscription?.plan_name ?? planCode} plan`);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setErrorMsg(message);
      notify(message, "error");
    } finally {
      setChangingPlanCode(null);
    }
  }

  async function handlePayInvoice(invoiceId: string | number) {
    try {
      const res = await fetch(`/v1/billing/invoices/${invoiceId}/khqr`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as { checkout_url?: string };
      if (data.checkout_url) {
        window.open(data.checkout_url, "_blank", "noopener,noreferrer");
      } else {
        notify("No checkout link was returned for this invoice.", "error");
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    }
  }

  const orderedPlans = useMemo(
    () => [...plans].sort((a, b) => a.monthly_fee_cents - b.monthly_fee_cents),
    [plans],
  );

  // Resolve the active plan purely from API data (no hardcoded plan order).
  const currentCode =
    currentPlan?.code ?? subscription?.plan_code ?? "";
  const freePlan = orderedPlans.find((p) => p.monthly_fee_cents === 0);
  const effectiveCurrentCode = currentCode || freePlan?.code || "";
  const currentPriceCents =
    currentPlan?.monthly_fee_cents ??
    orderedPlans.find((p) => p.code === effectiveCurrentCode)?.monthly_fee_cents ??
    0;

  const limit = currentPlan?.base_payments_included ?? 0;
  const usagePct =
    limit > 0 && usedThisMonth !== null
      ? Math.min(100, Math.round((usedThisMonth / limit) * 100))
      : 0;

  const renewalText = (() => {
    if (subscription?.status === "trial" && subscription.trial_ends_at) {
      return `Trial ends ${formatDate(subscription.trial_ends_at)}`;
    }
    if (subscription?.next_billing_at) {
      return `Renews ${formatDate(subscription.next_billing_at)}`;
    }
    return "No renewal scheduled";
  })();

  const subPill = statusPill(subscription?.status);

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Billing &amp; plans</h1>
          <div className="dash-page-subtitle">
            Your plan, monthly usage and invoices
          </div>
        </div>
      </div>

      {errorMsg && <div className="dash-warn">{errorMsg}</div>}

      <section className="dash-panel" aria-label="Current subscription">
        <div className="dash-panel-title">Current subscription</div>

        {subLoading ? (
          <div className="dash-info">Loading subscription…</div>
        ) : subError ? (
          <div className="dash-warn">
            Your subscription could not be loaded, so what is shown below may be
            out of date.
            <div className="dash-empty-cta-row">
              <button
                type="button"
                className="dash-btn dash-btn-secondary dash-btn-sm"
                onClick={() => void loadSubscription()}
              >
                Retry
              </button>
            </div>
          </div>
        ) : (
          <div className="bill-current">
            <div className="bill-current-plan">
              <div className="bill-current-name">
                {currentPlan?.name ?? "Free"}
              </div>
              <div className="bill-current-meta">
                <span className={subPill.className}>{subPill.label}</span>
                <span className="bill-current-renewal">{renewalText}</span>
              </div>
            </div>

            {limit > 0 && (
              <div className="bill-usage">
                <div className="bill-usage-head">
                  <span className="bill-usage-label">
                    Payments this month
                  </span>
                  <span className="bill-usage-value">
                    {usedThisMonth === null ? "—" : nf.format(usedThisMonth)} /{" "}
                    {nf.format(limit)}
                  </span>
                </div>
                <div className="cp-plan-bar">
                  <span
                    className="cp-plan-fill"
                    style={{ width: `${usagePct}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      <section aria-label="Plan comparison">
        <div className="dash-panel">
          <div className="dash-panel-title">Choose your plan</div>
          {plansLoading ? (
            <div className="dash-info">Loading plans…</div>
          ) : plansError ? (
            <div className="dash-warn">
              The plan list could not be loaded, so this is not a statement that no
              plans exist.
              <div className="dash-empty-cta-row">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary dash-btn-sm"
                  onClick={() => void loadPlans()}
                >
                  Retry
                </button>
              </div>
            </div>
          ) : orderedPlans.length === 0 ? (
            <div className="dash-empty">
              No plans available.
              <div className="dash-empty-desc">
                Please refresh, or contact support if this persists.
              </div>
            </div>
          ) : (
            <div className="bill-grid">
              {orderedPlans.map((plan) => {
                const isCurrent = plan.code === effectiveCurrentCode;
                const isUpgrade = plan.monthly_fee_cents > currentPriceCents;
                const busy = changingPlanCode === plan.code;
                const price = priceParts(plan.monthly_fee_cents);

                let ctaText = isUpgrade
                  ? `Upgrade to ${plan.name}`
                  : `Switch to ${plan.name}`;
                let ctaClass = isUpgrade
                  ? "dash-btn-primary"
                  : "dash-btn-secondary";
                if (isCurrent) {
                  ctaText = "Current plan";
                  ctaClass = "dash-btn-secondary";
                }

                return (
                  <article
                    key={plan.code}
                    className={`bill-card${isCurrent ? " bill-card-current" : ""}`}
                  >
                    {isCurrent && (
                      <span className="bill-card-badge">Current plan</span>
                    )}

                    <div className="bill-card-name">{plan.name}</div>
                    <div className="bill-card-price">
                      {price.amount}
                      <span>{price.period}</span>
                    </div>
                    <div className="bill-card-caption">
                      {plan.tagline ||
                        (plan.monthly_fee_cents === 0
                          ? "Free forever"
                          : "Billed monthly")}
                    </div>

                    <div className="bill-card-divider" />

                    <ul className="bill-card-features">
                      {featuresFor(plan).map((f) => (
                        <li
                          key={f.label}
                          className={f.on ? undefined : "bill-feature-off"}
                        >
                          <span className="bill-feature-mark">
                            {f.on ? "✓" : "✕"}
                          </span>
                          <span>{f.label}</span>
                        </li>
                      ))}
                    </ul>

                    <button
                      type="button"
                      className={`dash-btn ${ctaClass} bill-card-cta`}
                      disabled={isCurrent || busy}
                      onClick={() => handleChangePlan(plan.code)}
                    >
                      {busy ? "Updating…" : ctaText}
                    </button>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </section>

      <section className="dash-panel" aria-label="Invoice history">
        <div className="dash-panel-title">Invoices</div>
        {invoicesLoading ? (
          <div className="dash-info">Loading invoices…</div>
        ) : invoicesError ? (
          <div className="dash-warn">
            Your invoices could not be loaded, so this is not a statement that you
            have none. Any unpaid invoice is still payable from the link we emailed
            you.
            <div className="dash-empty-cta-row">
              <button
                type="button"
                className="dash-btn dash-btn-secondary dash-btn-sm"
                onClick={() => void loadInvoices()}
              >
                Retry
              </button>
            </div>
          </div>
        ) : invoices.length === 0 ? (
          <div className="dash-empty">
            No invoices yet.
            <div className="dash-empty-desc">
              Once you move to a paid plan, your invoices will appear here.
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Period</th>
                <th>Status</th>
                <th>Base fee</th>
                <th>Overage</th>
                <th>Total due</th>
                <th>Paid at</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => {
                const pill = invoicePill(inv.status);
                const unpaid = inv.status !== "paid" && inv.status !== "draft";
                return (
                  <tr key={String(inv.id)}>
                    <td>{inv.period_month || inv.period || "—"}</td>
                    <td>
                      <span className={pill.className}>{pill.label}</span>
                    </td>
                    <td>{inv.base_fee_formatted || "$0.00"}</td>
                    <td>{inv.overage_fee_formatted || "$0.00"}</td>
                    <td>{inv.total_due_formatted || "$0.00"}</td>
                    <td>{formatDate(inv.paid_at)}</td>
                    <td>
                      {unpaid ? (
                        <button
                          type="button"
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          onClick={() => handlePayInvoice(inv.id)}
                        >
                          Pay with KHQR
                        </button>
                      ) : (
                        <span className="dash-badge dash-badge-muted">
                          No action
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
