"use client";

import { useCallback, useEffect, useState } from "react";

export type Profile = {
  id?: number | string;
  email?: string;
  // The API returns the profile name as `name` (see /v1/me).
  name?: string | null;
  is_platform_admin?: boolean;
  whitelabel_enabled?: boolean;
  status?: string;
  created_at?: string;
  [k: string]: unknown;
};

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
