"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { readApiError } from "@/components/portal/apiError";
import NewPaymentForm, {
  currenciesOf,
  type LockedStore,
  type StoreOption,
} from "@/components/portal/NewPaymentForm";

/**
 * New payment **for one store**, reached from that store's own pages.
 *
 * The store comes from the URL, so the form does not ask for it again — which is the
 * whole point of this route existing beside the account-level one. The currency control
 * still appears, because a store can hold a link in each currency.
 */
export default function StoreNewPaymentPage({
  params,
}: {
  params: { public_id: string };
}) {
  const publicId = params.public_id;
  const [store, setStore] = useState<LockedStore | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`/api/v1/stores/${publicId}`, {
          credentials: "include",
        });
        if (res.status === 404) throw new Error("That store does not exist.");
        if (!res.ok) throw new Error(await readApiError(res));
        const data = (await res.json()) as StoreOption;
        if (alive) {
          setStore({
            id: data.id,
            name: data.name,
            currencies: currenciesOf(data),
          });
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [publicId]);

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h2 className="dash-page-title">New payment</h2>
          <div className="dash-page-subtitle">
            {store
              ? `For ${store.name}`
              : "Create a payment request for your customer"}
          </div>
        </div>
        <div>
          <Link
            className="dash-btn dash-btn-secondary dash-btn-sm"
            href={`/dashboard/${publicId}/payments`}
          >
            ← Back to this store
          </Link>
        </div>
      </div>

      {error ? (
        <div className="dash-warn">{error}</div>
      ) : store === null ? (
        <div className="dash-info">Loading store…</div>
      ) : (
        <NewPaymentForm lockedStore={store} />
      )}
    </>
  );
}
