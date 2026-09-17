import { redirect } from "next/navigation";
import { headers } from "next/headers";
import { fetchMe } from "../lib/api";

export default async function RootPage() {
  const h = headers();
  const cookieHeader = h.get("cookie") ?? undefined;
  const me = await fetchMe(cookieHeader);

  if (me) {
    redirect("/dashboard");
  } else {
    redirect("/login");
  }
}
