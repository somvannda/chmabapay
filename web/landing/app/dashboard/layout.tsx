"use client";

import { useEffect, useState } from "react";

import { DashboardShell } from "@/components/portal/DashboardShell";
import { useSession } from "@/components/portal/useSession";

type SubscriptionInfo = {
  subscription?: {
    status?: string;
    next_billing_at?: string | null;
    trial_ends_at?: string | null;
  } | null;
  plan?: {
    name?: string;
    code?: string;
    base_payments_included?: number;
  } | string | null;
  next_billing_at?: string | null;
  [k: string]: unknown;
};

function formatDateShort(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function planFromSubscription(sub: SubscriptionInfo | null): {
  name: string;
  code: string;
} {
  const p = sub?.plan;
  if (typeof p === "object" && p !== null) {
    return { name: p.name || "Free", code: (p.code || "free").toLowerCase() };
  }
  if (typeof p === "string" && p) {
    return { name: p, code: p.toLowerCase() };
  }
  return { name: "Free", code: "free" };
}

function monthStartDateParam(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return `${d.getFullYear()}-${m}-01`;
}

function firstOfNextMonthIso(): string {
  const d = new Date();
  d.setMonth(d.getMonth() + 1);
  d.setDate(1);
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { loading, profile, error } = useSession();

  const [planName, setPlanName] = useState("Free plan");
  const [planCode, setPlanCode] = useState("free");
  const [used, setUsed] = useState(0);
  const [limit, setLimit] = useState(3000);
  const [resetsLabel, setResetsLabel] = useState("");
  const [planLoaded, setPlanLoaded] = useState(false);
  const [signoutLoading, setSignoutLoading] = useState(false);

  useEffect(() => {
    document.body.classList.add("dash-hide-landing-chrome");
    return () => {
      document.body.classList.remove("dash-hide-landing-chrome");
    };
  }, []);

  useEffect(() => {
    let alive = true;

    const loadPlan = async () => {
      try {
        const subRes = await fetch("/v1/billing/subscription", {
          credentials: "include",
        });
        if (subRes.ok && subRes.status !== 501) {
          const sub = (await subRes.json().catch(() => null)) as SubscriptionInfo | null;
          if (!alive || !sub) return;
          const plan = planFromSubscription(sub);
          const planObj =
            typeof sub.plan === "object" && sub.plan !== null ? sub.plan : null;
          setPlanName(`${plan.name} plan`);
          setPlanCode(plan.code);
          setLimit(planObj?.base_payments_included ?? 100);
          const resetsIso =
            sub.subscription?.next_billing_at ||
            sub.next_billing_at ||
            firstOfNextMonthIso();
          setResetsLabel(formatDateShort(resetsIso));
        }
      } catch {
      }

      try {
        const url = `/v1/reports/payments.json?from=${monthStartDateParam()}&per_page=1`;
        const repRes = await fetch(url, { credentials: "include" });
        if (repRes.ok) {
          const data = await repRes.json().catch(() => ({}));
          const count = data?.summary?.total_matching_paid_count;
          if (alive && typeof count === "number") setUsed(count);
        }
      } catch {
      }

      if (alive) setPlanLoaded(true);
    };

    void loadPlan();

    // The billing page fires this after a plan change so the sidebar card
    // reflects the new plan without a full page reload.
    const onPlanChanged = () => {
      void loadPlan();
    };
    window.addEventListener("chmabapay:plan-changed", onPlanChanged);

    return () => {
      alive = false;
      window.removeEventListener("chmabapay:plan-changed", onPlanChanged);
    };
  }, []);

  async function handleSignOut() {
    if (signoutLoading) return;
    setSignoutLoading(true);
    try {
      const res = await fetch("/auth/signout", {
        method: "POST",
        credentials: "include",
      });
      if (res.ok || res.status === 302 || res.status === 303) {
        window.location.href = "/";
        return;
      }
    } catch {
    }
    try {
      const res = await fetch("/auth/logout", {
        method: "GET",
        credentials: "include",
      });
      if (res.ok || res.status === 302 || res.status === 303) {
        window.location.href = "/";
        return;
      }
    } catch {
    }
    window.location.href = "/";
  }

  if (loading) {
    return (
      <div className="cp-gate">
        <div className="cp-gate-card">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="cp-gate-mark" src="/logo.svg" alt="" width={40} height={40} />
          <div className="cp-gate-title">Loading your workspace…</div>
        </div>
      </div>
    );
  }

  if (error || !profile) {
    const nextParam =
      typeof window !== "undefined"
        ? encodeURIComponent(window.location.pathname + window.location.search)
        : "";
    const signInHref = `/user/google/auth/login?next=${nextParam}`;
    return (
      <div className="cp-gate">
        <div className="cp-gate-card">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="cp-gate-mark" src="/logo.svg" alt="" width={40} height={40} />
          <div className="cp-gate-title">You&apos;re signed out</div>
          <p className="cp-gate-text">
            {error || "Sign in to open your ChmabaPay workspace."}
          </p>
          <a className="dash-btn dash-btn-primary" href={signInHref}>
            Sign in with Google
          </a>
        </div>
      </div>
    );
  }

  return (
    <DashboardShell
      profile={profile}
      planName={planName}
      planCode={planCode}
      used={used}
      limit={limit}
      resetsLabel={resetsLabel}
      planLoaded={planLoaded}
      onSignOut={handleSignOut}
      signoutLoading={signoutLoading}
    >
      {children}
    </DashboardShell>
  );
}
