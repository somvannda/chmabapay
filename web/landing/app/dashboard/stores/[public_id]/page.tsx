"use client";

import { useEffect } from "react";

// The store workspace lives at /dashboard/<store_id> (Overview / Payments / Settings).
export default function StoreDetailRedirect({
  params,
}: {
  params: { public_id: string };
}) {
  useEffect(() => {
    window.location.replace(`/dashboard/${params.public_id}`);
  }, [params.public_id]);

  return <div className="dash-info">Opening store…</div>;
}
