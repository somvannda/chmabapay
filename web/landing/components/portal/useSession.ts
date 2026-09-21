"use client";

import { useCallback, useEffect, useState } from "react";

export type Profile = {
  id?: number | string;
  email?: string;
  // The API returns the profile name as `name` (see /v1/me).
  name?: string | null;
  is_platform_admin?: boolean;
  whitelabel_enabled?: boolean;
  // Whether the account has a password at all — never the password. Google-created
  // accounts do not, and the security settings say so instead of offering a form
  // that would be refused.
  has_password?: boolean;
  status?: string;
  created_at?: string;
  // Merchant-agreement acceptance. `terms_required_version` is what the server
  // publishes right now, so "accepted" is `terms_accepted_version ===
  // terms_required_version` — the version is never hardcoded in the UI, or a
  // text change would leave every client asking for the old one.
  terms_accepted_at?: string | null;
  terms_accepted_version?: string | null;
  terms_required_version?: string | null;
  [k: string]: unknown;
};

/** Has this account accepted the terms the server is currently publishing? */
export function termsAccepted(profile: Profile | null): boolean {
  if (!profile) return false;
  const required = profile.terms_required_version;
  if (!required) return true; // server did not say; do not invent a gate
  return profile.terms_accepted_version === required;
}

export function useSession() {
  const [loading, setLoading] = useState<boolean>(true);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchProfile = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/v1/me", { credentials: "include" });
      if (!res.ok) {
        if (res.status === 401) {
          const next = encodeURIComponent(
            window.location.pathname + window.location.search,
          );
          window.location.replace(`/user/google/auth/login?next=${next}`);
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      }
      const data = (await res.json()) as Profile;
      setProfile(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch("/v1/me", { credentials: "include" });
        if (!res.ok) {
          if (res.status === 401) {
            const next = encodeURIComponent(
              window.location.pathname + window.location.search,
            );
            window.location.replace(`/user/google/auth/login?next=${next}`);
            return;
          }
          throw new Error(`HTTP ${res.status}`);
        }
        const data = (await res.json()) as Profile;
        if (alive) setProfile(data);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const refresh = useCallback(() => {
    void fetchProfile();
  }, [fetchProfile]);

  return { loading, profile, error, refresh };
}
