"use client";

import Link from "next/link";

import NewPaymentForm from "@/components/portal/NewPaymentForm";

/**
 * The account-level new-payment page.
 *
 * It asks which store because a payment always belongs to one and this account may have
 * several. `/dashboard/{store}/payments/new` is the same form with the store already
 * known, which is what a merchant wants when they start from a store's own page.
 */
export default function DashboardPaymentsNewPage() {
  return (
    <>
      <div className="dash-page-head">
        <div>
          <h2 className="dash-page-title">New payment</h2>
          <div className="dash-page-subtitle">
            Create a payment request for your customer
          </div>
        </div>
        <div>
          <Link
            className="dash-btn dash-btn-secondary dash-btn-sm"
            href="/dashboard/payments"
          >
            ← Back to payments
          </Link>
        </div>
      </div>

      <NewPaymentForm />
    </>
  );
}
