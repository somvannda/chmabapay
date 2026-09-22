"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { readApiError } from "@/components/portal/apiError";
import { useToast } from "@/components/portal/Toast";
import { useSession } from "@/components/portal/useSession";

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
  // The invoice the account is frozen over, if any. Nullable on purpose: a frozen account can
  // owe nothing at all (an operator froze it by hand), and the hold copy has to be able to say
  // so instead of quoting $0.00.
  outstanding_invoice_id?: number | null;
};

/** A store as the API returns it. `billing_suspended_at` is the platform's hold, not a status. */
type StoreOut = {
  id: string;
  db_id: number;
  name: string;
  status: string;
  billing_suspended_at: string | null;
};

type StoreSlotResponse = {
  store: StoreOut;
  displaced: StoreOut | null;
  moved: boolean;
};

type SubscriptionResponse = {
  subscription: SubscriptionInfo | null;
  plan: PlanOut | null;
  // Set while the current plan's allowance is not yet enforced, because a paid plan was given
  // up this calendar month: the instant enforcement starts. A sibling of `subscription` rather
  // than a key inside it, like `current_period_end` and `outstanding_invoice_id` — it is a fact
  // about the account, and it reads the same whether or not a row accompanies it.
  //
  // The usage figure is counted against the plan *in force*, so a mid-month downgrade reads
  // "3,100 / 3,000" while every code still mints. The number is right and this is why, which is
  // what lets the card below say so instead of leaving the bar to contradict itself (§7.7).
  quota_deferred_until?: string | null;
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
  // The window the invoice is a claim for, and when it was expected. `is_overdue` is derived
  // server-side from `due_at` rather than stored, so this page never has to guess from a status
  // value — the worker writes `open` for both an invoice due next week and one a week late.
  due_at?: string | null;
  days_until_due?: number | null;
  is_overdue?: boolean;
  voided_at?: string | null;
  void_reason?: string | null;
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

/** The payable code the API mints for one invoice. */
type KhqrResponse = {
  payment_id: string;
  qr_string: string;
  checkout_url: string;
  expires_at: string;
  amount_cents: number;
  amount_formatted: string;
};

/** What `/pay/{id}/status` reports, polled until the code is terminal. */
type PayStatusResponse = {
  status?: string;
  amount?: string;
  currency?: string;
};

// Payment states that mean "this code will not take money any more", so polling can
// stop. Mirrors the backend's PAYMENT_DEAD_STATUSES, plus `failed`.
const PAY_TERMINAL = new Set(["paid", "expired", "failed", "superseded", "reversed"]);

function payStatusCopy(status: string): string {
  switch (status) {
    case "scanned":
      return "QR scanned — confirm the payment in your banking app.";
    case "paid":
      return "Payment received. Your plan is being activated.";
    case "expired":
      return "This code has expired. Close this and try again to get a fresh one.";
    case "failed":
      return "That payment did not go through. Close this and try again.";
    case "superseded":
    case "reversed":
      return "This code is no longer payable. Close this and try again.";
    default:
      return "Waiting for you to scan and pay…";
  }
}

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

/**
 * One invoice's badge.
 *
 * `Overdue Nd` comes from the API's derived `is_overdue` and `days_until_due`, never from a status
 * value: the worker writes `open` for an invoice due next week and for one a week late, and a
 * separate clock is what tells them apart. A status-only badge could therefore only ever say
 * "Open" for a debt that has already cost the merchant their payment codes.
 *
 * A `void` invoice says `Void` and is still payable — settling one is how a lapsed merchant buys
 * their plan back (§5.3) — so the Pay button appears beside it rather than the row reading as dead.
 */
function invoicePill(inv: Invoice): { className: string; label: string } {
  if (inv.status === "paid") {
    return { className: "dash-pill dash-pill-paid", label: "Paid" };
  }
  if (inv.status === "void" || inv.voided_at) {
    return { className: "dash-pill dash-pill-superseded", label: "Void" };
  }
  if (inv.is_overdue) {
    const days = inv.days_until_due;
    return {
      className: "dash-pill dash-pill-failed",
      label:
        typeof days === "number" && days < 0 ? `Overdue ${Math.abs(days)}d` : "Overdue",
    };
  }
  // `draft` and `issued` are the pre-migration spellings of "unpaid" and are still read as open
  // by the API, so they are labelled the same way here rather than with a status nobody uses.
  return { className: "dash-pill dash-pill-pending", label: "Open" };
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
 * Pay-an-invoice modal
 * ------------------------------------------------------------------ */

/**
 * The code a merchant scans to buy a plan.
 *
 * A paid plan is *bought*, so the click that asks for it has to end somewhere the
 * merchant can actually pay — the previous flow only raised the invoice and pointed at
 * a table row, and the row's button opened `/pay/{id}`, a page that carries no QR at
 * all. The code lives on `/pay/{id}/qr.svg`, the same route the payment detail page
 * uses, so what is rendered here is the code ABA issued rather than a redrawn one.
 */
function InvoicePaymentModal({
  invoice,
  khqr,
  loading,
  error,
  status,
  secondsLeft,
  onClose,
  onRetry,
}: {
  invoice: Invoice;
  khqr: KhqrResponse | null;
  loading: boolean;
  error: string | null;
  status: string;
  secondsLeft: number | null;
  onClose: () => void;
  onRetry: () => void;
}) {
  const paid = status === "paid";
  const pill =
    status === "paid"
      ? { className: "dash-pill dash-pill-paid", label: "Paid" }
      : status === "scanned"
        ? { className: "dash-pill dash-pill-scanned", label: "Scanned" }
        : PAY_TERMINAL.has(status)
          ? { className: "dash-pill dash-pill-failed", label: status }
          : { className: "dash-pill dash-pill-pending", label: "Awaiting payment" };

  return (
    <div className="dash-modal-backdrop" onClick={onClose}>
      <div className="dash-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dash-modal-head">
          <h3 className="dash-modal-title">
            {paid ? "Payment received" : "Pay with KHQR"}
          </h3>
          <button
            type="button"
            className="dash-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="dash-modal-body">
          {loading ? (
            <div className="dash-info">Preparing your payment code…</div>
          ) : error ? (
            <>
              <div className="dash-warn">{error}</div>
              <div className="dash-toolbar">
                <div />
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={onRetry}
                >
                  Try again
                </button>
              </div>
            </>
          ) : khqr ? (
            <>
              <div className="bill-pay-amount">{khqr.amount_formatted}</div>
              <div className="bill-pay-period">
                Invoice {invoice.period_month || invoice.period || "—"}
              </div>

              {paid ? (
                <div className="dash-note">
                  Your plan is active as soon as the invoice settles, which happens on
                  confirmation from the bank. Nothing else to do.
                </div>
              ) : (
                <>
                  <img
                    className="dash-qr-img"
                    src={`/pay/${khqr.payment_id}/qr.svg`}
                    alt="KHQR code for this invoice"
                  />
                  <div className="bill-pay-status-row">
                    <span className={pill.className}>{pill.label}</span>
                  </div>
                  <div className="bill-pay-status">{payStatusCopy(status)}</div>
                  {secondsLeft !== null && !PAY_TERMINAL.has(status) && (
                    <div className="bill-pay-countdown">
                      Expires in {Math.floor(secondsLeft / 60)}:
                      {String(secondsLeft % 60).padStart(2, "0")}
                    </div>
                  )}
                </>
              )}

              <div className="dash-toolbar">
                {!paid && khqr.checkout_url ? (
                  <a
                    className="dash-btn dash-btn-secondary"
                    href={khqr.checkout_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open checkout page ↗
                  </a>
                ) : (
                  <div />
                )}
                <button
                  type="button"
                  className="dash-btn dash-btn-primary"
                  onClick={onClose}
                >
                  {paid ? "Done" : "Close"}
                </button>
              </div>
            </>
          ) : (
            <div className="dash-info">No payment code was returned.</div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Page
 * ------------------------------------------------------------------ */

/**
 * Opens the payment modal for `?pay={invoice_id}`.
 *
 * That URL is the backend's own notice `action_url` — the banner's one link — so this has to work
 * both on a cold load and on a client-side navigation from another dashboard page.
 * `useSearchParams` is what makes the second case work: a `window.location` read in an effect
 * would never see the URL change, because Next's router does not reload the document. Next
 * requires a `useSearchParams` consumer to sit inside a Suspense boundary, which is why this is a
 * component of its own rather than a line in the page.
 */
function OpenInvoiceFromUrl({
  invoices,
  onOpen,
}: {
  invoices: Invoice[];
  onOpen: (invoice: Invoice) => void | Promise<void>;
}) {
  const searchParams = useSearchParams();
  const payId = searchParams.get("pay");
  const handled = useRef<string | null>(null);

  useEffect(() => {
    if (!payId || handled.current === payId) return;
    const match = invoices.find((inv) => String(inv.id) === payId);
    // No match yet means the list is still in flight; the effect runs again when it lands.
    if (!match) return;
    handled.current = payId;
    void onOpen(match);
  }, [payId, invoices, onOpen]);

  return null;
}

export default function BillingPage() {
  const { notify } = useToast();
  // The hold is account-level, so it is the profile that says whether the page is the
  // merchant's way out rather than a read-only view of it.
  const { profile } = useSession();
  const [plans, setPlans] = useState<PlanOut[]>([]);
  const [plansLoading, setPlansLoading] = useState(true);
  const [subscription, setSubscription] = useState<SubscriptionInfo | null>(null);
  // Held separately from `subscription` because the API sends it beside the subscription row
  // rather than inside it, and this page keeps only the row (`data.subscription`). Reading it as
  // `subscription?.quota_deferred_until` was the first attempt and it silently rendered nothing:
  // the type described the nested shape, the API answered at the top level, and no fallback
  // exists to paper over the difference the way `outstanding_invoice_id` has one.
  const [quotaDeferredUntil, setQuotaDeferredUntil] = useState<string | null>(null);
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
  // The invoice being paid. Non-null means the KHQR modal is open.
  const [payInvoice, setPayInvoice] = useState<Invoice | null>(null);
  const [khqr, setKhqr] = useState<KhqrResponse | null>(null);
  const [khqrLoading, setKhqrLoading] = useState(false);
  const [khqrError, setKhqrError] = useState<string | null>(null);
  const [payStatus, setPayStatus] = useState("pending");
  const [paySecondsLeft, setPaySecondsLeft] = useState<number | null>(null);
  // The store allowance, for the slot chooser. Only meaningful when something is held —
  // `GET /v1/stores` is a read, so it stays available while the account is frozen.
  const [stores, setStores] = useState<StoreOut[]>([]);
  const [storesError, setStoresError] = useState<string | null>(null);
  const [movingStoreId, setMovingStoreId] = useState<string | null>(null);

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
        setQuotaDeferredUntil(data.quota_deferred_until ?? null);
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

  const loadStores = useCallback(async () => {
    setStoresError(null);
    try {
      const res = await fetch("/v1/stores", { credentials: "include" });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json().catch(() => ({}));
      setStores(Array.isArray(data?.data) ? data.data : []);
    } catch (e) {
      setStoresError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void loadStores();
  }, [loadStores]);

  /**
   * Bring one suspended store back, moving whichever store makes room for it.
   *
   * The displacement is the server's to decide and it answers with the store it held, so
   * the merchant is told which one went offline rather than discovering it later. A store
   * the platform held is the only thing this can release; an operator's own disable is a
   * different refusal with its own message.
   */
  async function handleActivateStore(store: StoreOut) {
    if (movingStoreId) return;
    setMovingStoreId(store.id);
    try {
      const res = await fetch(`/v1/stores/${store.id}/activate`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as StoreSlotResponse;
      await loadStores();
      if (!data.moved) {
        notify(`${store.name} is already live.`);
      } else if (data.displaced) {
        notify(
          `${store.name} is live. ${data.displaced.name} was suspended to make room.`,
        );
      } else {
        notify(`${store.name} is live again.`);
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setMovingStoreId(null);
    }
  }

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
        // actually happened, and open the code that settles it — the click has to end
        // somewhere the merchant can pay, and a toast pointing at a table row does not.
        await loadInvoices();
        const planName = data.subscription?.plan_name ?? planCode;
        const amount = data.invoice?.total_due_formatted;
        notify(
          amount
            ? `Invoice for ${amount} raised — scan the code to move to ${planName}.`
            : `Invoice raised — scan the code to move to ${planName}.`,
        );
        if (data.invoice) {
          await openInvoicePayment(data.invoice);
        }
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

  /**
   * Open the invoice's payable code, minting it on the first ask.
   *
   * The route is idempotent per invoice, so reopening returns the code the merchant
   * already has rather than minting a second payable one — paying both would charge
   * them twice for one month against a single invoice.
   */
  const openInvoicePayment = useCallback(async (invoice: Invoice) => {
    setPayInvoice(invoice);
    setKhqr(null);
    setKhqrError(null);
    setPayStatus("pending");
    setPaySecondsLeft(null);
    setKhqrLoading(true);
    try {
      const res = await fetch(`/v1/billing/invoices/${invoice.id}/khqr`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setKhqr((await res.json()) as KhqrResponse);
    } catch (e) {
      setKhqrError(e instanceof Error ? e.message : String(e));
    } finally {
      setKhqrLoading(false);
    }
  }, []);

  const closePayment = useCallback(() => {
    setPayInvoice(null);
    setKhqr(null);
    setKhqrError(null);
    setPayStatus("pending");
    setPaySecondsLeft(null);
  }, []);

  // Primitive, so the countdown's once-a-second render cannot restart the poll loop
  // below: an unstable dependency there would tear the interval down and rebuild it
  // every second, and the 2.5s fetch would never fire.
  const payIsTerminal = PAY_TERMINAL.has(payStatus);

  // Poll the payment while the modal is open. This is what makes the invoice read
  // Paid without the merchant reloading: the backend flips the invoice and the plan in
  // the same transaction as the settlement, so once this reports paid the subscription
  // and the invoice list are stale by exactly one refetch.
  //
  // 1.5s, not the 2.5s this started at. The server is the slow half — it is the sweep
  // that asks ABA, and it now runs every 5s for a code still inside ABA's window — so
  // this only needs to be comfortably shorter than that to not be the term anyone
  // notices. The endpoint is one indexed read of the merchant's own row.
  useEffect(() => {
    if (!khqr || payIsTerminal) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch(`/pay/${khqr.payment_id}/status`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as PayStatusResponse;
        const next = (data.status || "").toLowerCase();
        if (!next) return;
        setPayStatus(next);
        if (next === "paid") {
          notify("Payment received — your plan is now active.");
          await loadSubscription();
          await loadInvoices();
          // Keep the sidebar plan card in sync (it is owned by the dashboard layout).
          window.dispatchEvent(new Event("chmabapay:plan-changed"));
        }
      } catch {
        // Transient. A failed poll is not a failed payment, so it is never shown as
        // one — the next tick retries.
      }
    }, 1500);
    return () => clearInterval(id);
  }, [khqr, payIsTerminal, notify, loadSubscription, loadInvoices]);

  // ABA's window is short (180s observed, and nothing extends it), so showing how much
  // is left is the difference between scanning now and coming back to a dead code.
  useEffect(() => {
    if (!khqr || payStatus === "paid") return;
    const deadline = new Date(khqr.expires_at).getTime();
    if (Number.isNaN(deadline)) return;
    const tick = () =>
      setPaySecondsLeft(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [khqr, payStatus]);

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

  /**
   * Whether the usage bar needs the deferral explained to it.
   *
   * Only when it changes what the bar *means*: the figure now comes from a plan whose allowance
   * is not being enforced, so "over" is a reading the merchant cannot act on. Under the
   * allowance the deferral is invisible and a note would be noise, and with no plan loaded there
   * is no limit to be over.
   */
  const deferredUntil = quotaDeferredUntil ? formatDate(quotaDeferredUntil) : null;
  const showDeferredNote =
    deferredUntil !== null &&
    limit > 0 &&
    usedThisMonth !== null &&
    usedThisMonth >= limit;

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

  /**
   * The invoice the hold is keyed on.
   *
   * The subscription response names it directly (`outstanding_invoice_id`), which is what
   * the server freezes on. The fallback covers a stale read: an unpaid invoice is the only
   * thing that can hold an account, so showing the oldest one is closer to true than
   * showing nothing.
   */
  const outstanding = useMemo(() => {
    const id = subscription?.outstanding_invoice_id;
    if (id !== null && id !== undefined) {
      return invoices.find((inv) => Number(inv.id) === Number(id)) ?? null;
    }
    return invoices.find((inv) => inv.status !== "paid" && !inv.voided_at) ?? null;
  }, [invoices, subscription]);

  const isFrozen = profile?.status === "restricted";
  const heldStores = useMemo(
    () => stores.filter((s) => s.billing_suspended_at !== null),
    [stores],
  );
  const liveStores = useMemo(
    () => stores.filter((s) => s.billing_suspended_at === null),
    [stores],
  );

  return (
    <>
      {/* The banner's CTA lands here as /dashboard/billing?pay={id}. Rendered above the page head
          so the modal opens as soon as the invoice list is in. */}
      <Suspense fallback={null}>
        <OpenInvoiceFromUrl invoices={invoices} onOpen={openInvoicePayment} />
      </Suspense>

      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Billing &amp; plans</h1>
          <div className="dash-page-subtitle">
            Your plan, monthly usage and invoices
          </div>
        </div>
      </div>

      {errorMsg && <div className="dash-warn">{errorMsg}</div>}

      {isFrozen && (
        <section className="dash-panel bill-hold" aria-label="Account on hold">
          <div className="dash-panel-title">Your account is on hold</div>
          <div className="dash-hint">
            {outstanding ? (
              <>
                A plan invoice for{" "}
                {outstanding.total_due_formatted || "the amount shown below"} is unpaid
                {outstanding.due_at
                  ? ` (due ${formatDate(outstanding.due_at)})`
                  : ""}
                . Until it is settled your stores cannot generate payment codes and the
                rest of the dashboard is read-only. Nothing has been deleted — your
                stores, keys, links and history are exactly as you left them.
              </>
            ) : (
              <>
                Your stores cannot generate payment codes and the rest of the dashboard
                is read-only. Nothing has been deleted — your stores, keys, links and
                history are exactly as you left them.
              </>
            )}
          </div>

          {/* The three ways out, in the order a merchant should consider them. Every one
              of them is reachable from this page and nowhere else while frozen, which is
              why this page is the one surface the read-only mode leaves writable. */}
          <ol className="bill-hold-ways">
            <li>
              <strong>Settle the invoice.</strong> Scan a code and everything comes back
              the moment the bank confirms.
              {outstanding && (
                <button
                  type="button"
                  className="dash-btn dash-btn-primary dash-btn-sm"
                  onClick={() => void openInvoicePayment(outstanding)}
                >
                  Pay{" "}
                  {outstanding.total_due_formatted
                    ? outstanding.total_due_formatted
                    : "with KHQR"}
                </button>
              )}
            </li>
            <li>
              <strong>Move to Free.</strong> The unpaid invoice is withdrawn, the debt
              goes with it, and the account is unfrozen straight away with the Free
              plan&apos;s allowance.{" "}
              <a href="#plans">Choose Free</a>
            </li>
            <li>
              <strong>Move to a smaller paid plan.</strong> The old invoice is withdrawn
              and the plan starts the moment its own invoice is paid.{" "}
              <a href="#plans">Choose a plan</a>
            </li>
          </ol>
        </section>
      )}

      {/* Hidden while the account is frozen, not merely disabled: a freeze stops every
          store regardless of the cap (§7.2), and the gate refuses this write while frozen
          by design — the allowlist is reads plus `change-plan`. Showing the chooser here
          would be a button that can only answer 403, and the hold panel above already
          names the three writes that do work. It reappears the moment the hold lifts. */}
      {!isFrozen && (heldStores.length > 0 || storesError) && (
        <section className="dash-panel" aria-label="Store allowance">
          <div className="dash-panel-title">Which stores stay live</div>
          <div className="dash-hint">
            {currentPlan?.max_stores
              ? `Your plan allows ${currentPlan.max_stores} store${
                  currentPlan.max_stores === 1 ? "" : "s"
                }. ${liveStores.length} ${
                  liveStores.length === 1 ? "is" : "are"
                } live, and the rest are suspended — they keep their links, keys and history, but they cannot generate payment codes. Bring one back and whichever store makes room for it is suspended in its place, so you never go over the allowance.`
              : "The stores below are suspended and cannot generate payment codes. They keep their links, keys and history."}
          </div>
          {storesError ? (
            <div className="dash-warn">
              Your stores could not be loaded, so this is not a statement that nothing is
              suspended.
              <div className="dash-empty-cta-row">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary dash-btn-sm"
                  onClick={() => void loadStores()}
                >
                  Retry
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="bill-slot-subhead">
                Live ({liveStores.length}
                {currentPlan?.max_stores ? ` of ${currentPlan.max_stores}` : ""})
              </div>
              <ul className="bill-slot-list">
                {liveStores.map((s) => (
                  <li key={s.id} className="bill-slot-row">
                    <span>{s.name}</span>
                    <span className="dash-pill dash-pill-paid">Live</span>
                  </li>
                ))}
              </ul>

              <div className="bill-slot-subhead">
                Suspended — plan limit ({heldStores.length})
              </div>
              <ul className="bill-slot-list">
                {heldStores.map((s) => (
                  <li key={s.id} className="bill-slot-row">
                    <span>{s.name}</span>
                    <button
                      type="button"
                      className="dash-btn dash-btn-secondary dash-btn-sm"
                      disabled={movingStoreId !== null}
                      onClick={() => void handleActivateStore(s)}
                    >
                      {movingStoreId === s.id ? "Bringing back…" : "Bring back"}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

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
                  <progress
                    className="cp-plan-fill"
                    max={100}
                    value={usagePct}
                    aria-label="Monthly payment quota used"
                  />
                </div>
                {showDeferredNote && (
                  <p className="bill-usage-note">
                    Nothing is blocked until {deferredUntil} — these payments were made
                    under your previous plan.
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </section>

      <section aria-label="Plan comparison" id="plans">
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
                <th>Due</th>
                <th>Base fee</th>
                <th>Overage</th>
                <th>Total due</th>
                <th>Paid at</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => {
                const pill = invoicePill(inv);
                // Everything except a settled invoice is payable. A `void` one included, on
                // purpose: settling it is the reinstatement path, and the API refuses only `paid`.
                const payable = inv.status !== "paid";
                return (
                  <tr key={String(inv.id)}>
                    <td>{inv.period_month || inv.period || "—"}</td>
                    <td>
                      <span className={pill.className}>{pill.label}</span>
                    </td>
                    <td>{formatDate(inv.due_at)}</td>
                    <td>{inv.base_fee_formatted || "$0.00"}</td>
                    <td>{inv.overage_fee_formatted || "$0.00"}</td>
                    <td>{inv.total_due_formatted || "$0.00"}</td>
                    <td>{formatDate(inv.paid_at)}</td>
                    <td>
                      {payable ? (
                        <button
                          type="button"
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          onClick={() => void openInvoicePayment(inv)}
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

      {payInvoice && (
        <InvoicePaymentModal
          invoice={payInvoice}
          khqr={khqr}
          loading={khqrLoading}
          error={khqrError}
          status={payStatus}
          secondsLeft={paySecondsLeft}
          onClose={closePayment}
          onRetry={() => void openInvoicePayment(payInvoice)}
        />
      )}
    </>
  );
}
