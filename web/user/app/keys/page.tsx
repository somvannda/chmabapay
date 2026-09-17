import { headers } from "next/headers";
import { fetchMe } from "../../lib/api";
import { KeysPageClient } from "./KeysPageClient";

export default async function KeysPage() {
  const h = headers();
  const cookieHeader = h.get("cookie") ?? undefined;
  const me = await fetchMe(cookieHeader);

  return (
    <KeysPageClient
      accountType={me?.account_type ?? "individual"}
      isPlatformAdmin={me?.is_platform_admin ?? false}
    />
  );
}
