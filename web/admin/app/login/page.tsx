"use client";

import { useCallback, useEffect, useState } from "react";

function safeNext(raw: string | null): string {
  // Only same-origin paths, so `?next=` cannot be used as an open redirect.
  if (raw && raw.startsWith("/") && !raw.startsWith("//")) return raw;
  return "/";
}

export default function AdminLoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [next, setNext] = useState("/");

  useEffect(() => {
    setNext(safeNext(new URLSearchParams(window.location.search).get("next")));
    // Already signed in as an admin? Skip the form.
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/v1/me", { credentials: "include" });
        if (res.ok && alive) {
          const me = (await res.json()) as {
            is_platform_admin?: boolean;
            auth_method?: string;
          };
          // A Google session must not satisfy the console — leave the form up so the
          // visitor can sign in with a password.
          if (me.is_platform_admin === true && me.auth_method !== "google") {
            window.location.replace(next);
          }
        }
      } catch {
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setLoading(true);
      setError(null);
      try {
        const res = await fetch("/auth/login", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
        });
        if (!res.ok) {
          if (res.status === 401) throw new Error("Wrong email or password.");
          if (res.status === 403) throw new Error("That account is suspended.");
          throw new Error(`Sign-in failed (HTTP ${res.status}).`);
        }
        window.location.href = next;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [email, password, next],
  );

  return (
    <div className="cp-gate">
      <div className="cp-gate-card">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img className="cp-gate-mark" src="/logo.svg" alt="" width={40} height={40} />
        <div className="cp-gate-title">Platform console</div>
        <p className="cp-gate-text">Sign in with your platform admin password.</p>

        <form className="dash-form cp-gate-form" onSubmit={submit}>
          {error && (
            <div className="dash-form-alert dash-form-alert-error">{error}</div>
          )}

          <div className="dash-field">
            <label htmlFor="admin-email">Email</label>
            <input
              id="admin-email"
              type="email"
              className="dash-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
              autoFocus
            />
          </div>

          <div className="dash-field">
            <label htmlFor="admin-password">Password</label>
            <input
              id="admin-password"
              type="password"
              className="dash-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>

          <button
            type="submit"
            className="dash-btn dash-btn-primary"
            disabled={loading}
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
