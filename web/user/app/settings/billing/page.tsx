import { headers } from "next/headers";
import { fetchMe } from "../../../lib/api";
import BillingPageClient from "./BillingPageClient";

export default async function BillingPage() {
  const h = headers();
  const cookieHeader = h.get("cookie") ?? undefined;
  const me = await fetchMe(cookieHeader);
  const accountType = me?.account_type ?? "individual";

  // The current plan and the plan catalogue are both fetched by the client from
  // /v1/billing — the admin console is the single source of truth for plans.
  return <BillingPageClient currentAccountType={accountType} />;
}
