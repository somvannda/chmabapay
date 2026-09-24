"use client";

import { useEffect, useState, type ReactNode } from "react";

import Link from "next/link";

/**
 * The numbers in this FAQ are the live plan payload, not prose.
 *
 * They used to be typed in, and they drifted: the copy named three payment
 * allowances and three key limits that only the plans in the database could confirm.
 * An FAQ that disagrees with the Billing page is worse than one that says less, so the
 * two answers that quote limits are built from `/v1/billing/plans`, and when that read
 * fails they drop the numbers instead of falling back to a remembered set.
 */

type PlanOut = {
  code: string;
  name: string;
  monthly_fee_cents: number;
  base_payments_included: number;
  max_stores: number | null;
  max_keys_per_account: number;
  priority_support: boolean;
};

type FaqItem = {
  q: string;
  a: ReactNode;
};

const nf = new Intl.NumberFormat("en-US");

function priceLabel(cents: number): string {
  const dollars = cents / 100;
  return `$${dollars % 1 === 0 ? dollars.toFixed(0) : dollars.toFixed(2)}/mo`;
}

/** The same wording the Billing page uses for the store cap. */
function storesLabel(maxStores: number | null): string {
  if (maxStores === null) return "unlimited stores";
  return maxStores === 1 ? "1 store" : `${nf.format(maxStores)} stores`;
}

function keysLabel(count: number): string {
  return count === 1 ? "1 key" : `${nf.format(count)} keys`;
}

/** "Free — 3,000 payments, 1 store, 1 key." / "Starter ($9.99/mo) — …" */
function planSummary(plan: PlanOut): string {
  const head =
    plan.monthly_fee_cents === 0
      ? plan.name
      : `${plan.name} (${priceLabel(plan.monthly_fee_cents)})`;
  const limits = [
    `${nf.format(plan.base_payments_included)} payments`,
    storesLabel(plan.max_stores),
    keysLabel(plan.max_keys_per_account),
  ].join(", ");
  return `${head} — ${limits}${plan.priority_support ? ", plus priority support" : ""}.`;
}

export default function DashboardHelpPage() {
  const [plans, setPlans] = useState<PlanOut[]>([]);
  const [plansLoaded, setPlansLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/billing/plans", { credentials: "include" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as PlanOut[];
        if (alive && Array.isArray(data)) {
          setPlans([...data].sort((a, b) => a.monthly_fee_cents - b.monthly_fee_cents));
          setPlansLoaded(true);
        }
      } catch {
        // Handled by the answers below, which say where the numbers live rather than
        // inventing them.
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Which plans carry priority support is a property of the plan rows, so the answer
  // names them from the payload too rather than assuming "Pro" still is the one.
  const priorityPlanNames = plans
    .filter((p) => p.priority_support)
    .map((p) => p.name);

  const faqs: FaqItem[] = [
    {
      q: "How do I create my first store and accept payments?",
      a: "Step 1: Go to API keys → + Create key and copy the live key (shown once). Step 2: Go to Stores → + New store and enter your ABA PayWay share link — the store becomes active as soon as the destination is attached. Step 3: Go to Payments → + New payment to generate a KHQR. Step 4: Open the QR and scan it with Bakong or ABA Pay to test the full flow.",
    },
    {
      q: "How do API keys work across my stores?",
      a: `Every API key is workspace-scoped: one key authenticates every store in your account. Pass a store id when creating a payment to choose the destination. ${
        plans.length > 0
          ? `Your plan sets how many active keys you can have (${plans
              .map((p) => `${p.name} ${p.max_keys_per_account}`)
              .join(", ")}).`
          : "Your plan sets how many active keys you can have — the Billing page shows the limit for the plan you are on."
      }`,
    },
    {
      q: "How do webhook signing secrets work?",
      a: "When you create a webhook endpoint we return a whsec_ signing secret once. Use it on your server to verify the X-ChmabaPay-Signature header on every POST. Rotate the secret at any time from the endpoint row — the new secret is also returned once only.",
    },
    {
      q: "How do I switch plans?",
      a: "Go to Billing → Choose your plan and select a plan. Moving to a paid plan takes effect once its invoice for the period is paid — the invoice is raised when you pick the plan and you can pay it from the Billing page, so the new plan starts with its first payment rather than before it. Moving to Free applies immediately, because there is nothing to collect. Your stores, keys and webhooks stay intact either way.",
    },
    {
      q: "What does each plan include?",
      a:
        plans.length > 0
          ? `Every plan includes hosted checkout and webhook signing. The plans differ in price, stores, payments per month and API keys: ${plans
              .map(planSummary)
              .join(" ")} The Billing page shows the full feature comparison for the plan you are on.`
          : "Every plan includes hosted checkout and webhook signing; price, stores, payments per month and API keys differ between them. The Billing page shows the live comparison.",
    },
    {
      q: "What support is available?",
      a: (
        <>
          Email support@chmaba.com on any plan, or{" "}
          <Link href="/dashboard/support">open a support request</Link> from your
          dashboard and follow our reply in the thread there.
          {priorityPlanNames.length > 0
            ? ` ${priorityPlanNames.join(" and ")} add${
                priorityPlanNames.length === 1 ? "s" : ""
              } priority support with a faster response time.`
            : ""}{" "}
          Payment exports for your finance team are on the Reports page.
        </>
      ),
    },
  ];

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Help center</h1>
          <div className="dash-page-subtitle">
            Common questions about ChmabaPay
          </div>
        </div>
      </div>

      {!plansLoaded && (
        <div className="dash-info">
          Loading your plan limits — the answers quote them once they are read.
        </div>
      )}

      {faqs.map((f) => (
        <div key={f.q} className="dash-panel">
          <details>
            <summary>{f.q}</summary>
            <p>{f.a}</p>
          </details>
        </div>
      ))}
    </>
  );
}
