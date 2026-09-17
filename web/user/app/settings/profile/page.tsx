import { headers } from "next/headers";
import { fetchMe } from "../../../lib/api";
import ProfilePageClient from "./ProfilePageClient";

export default async function ProfilePage() {
  const h = headers();
  const cookieHeader = h.get("cookie") ?? undefined;
  const me = await fetchMe(cookieHeader);
  return <ProfilePageClient me={me} />;
}
