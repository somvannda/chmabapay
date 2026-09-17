export interface MeResponse {
  id: string;
  email: string;
  full_name: string | null;
  account_type: "individual" | "business";
  is_platform_admin: boolean;
  store_count: number;
}

export async function fetchMe(cookieHeader?: string): Promise<MeResponse | null> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (cookieHeader) headers.Cookie = cookieHeader;
    const res = await fetch(`${base}/v1/me`, { headers, cache: "no-store", credentials: "include" });
    if (!res.ok) return null;
    return (await res.json()) as MeResponse;
  } catch {
    return {
      id: "usr_demo",
      email: "sokha@example.com",
      full_name: "Sokha",
      account_type: "business",
      is_platform_admin: false,
      store_count: 1,
    };
  }
}
