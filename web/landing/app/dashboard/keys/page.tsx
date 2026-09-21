"use client";

import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/components/portal/apiError";
import { useToast } from "@/components/portal/Toast";

type ApiKey = {
  id: string | number;
  name?: string | null;
  key_prefix?: string;
  status: "active" | "revoked";
  last_used_at?: string | null;
  created_at?: string | null;
  revoked_at?: string | null;
  raw_key?: string;
  [k: string]: unknown;
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatKey(prefix: string | undefined): string {
  const value = prefix || "";
  const match = /^(ck_(?:live|test))_?(.*)$/.exec(value);
  if (!match) return `${value.slice(0, 4)}…`;
  return `${match[1]}_${match[2].slice(0, 4)}…`;
}

function CopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
    }
  }, [value]);
  return (
    <div className="dash-copy-field">
      <div className="dash-copy-field-value">{value}</div>
      <button type="button" className="dash-copy-btn" onClick={onCopy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function RevealBanner({
  rawKey,
  onDismiss,
}: {
  rawKey: string;
  onDismiss: () => void;
}) {
  return (
    <div className="dash-key-reveal">
      <div className="dash-key-reveal-head">
        <div>
          <h2 className="dash-panel-title">Save your new key</h2>
          <p className="dash-key-reveal-copy">
            This is the only time it is shown. Copy it now and store it securely.
          </p>
        </div>
        <button
          type="button"
          className="dash-btn dash-btn-secondary dash-btn-sm"
          onClick={onDismiss}
        >
          Dismiss
        </button>
      </div>
      <CopyField value={rawKey} />
    </div>
  );
}

export default function DashboardKeysPage() {
  const { notify } = useToast();
  const [loading, setLoading] = useState(true);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [revealKey, setRevealKey] = useState<string | null>(null);

  const fetchKeys = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch("/v1/keys", { credentials: "include" });
      // A failed read used to leave `keys` empty, which the page then rendered as
      // "No API keys yet" — the same screen as a fresh account, and the one state in
      // which a merchant might create a duplicate key.
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json().catch(() => ({}));
      const items: ApiKey[] = Array.isArray(data)
        ? data
        : Array.isArray(data?.items)
          ? data.items
          : Array.isArray(data?.data)
            ? data.data
            : [];
      setKeys(items);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchKeys();
  }, [fetchKeys]);

  const handleCreate = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setCreating(true);
      try {
        const res = await fetch("/v1/keys", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.trim() }),
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const data = (await res.json()) as ApiKey;
        setName("");
        if (data.raw_key) setRevealKey(data.raw_key);
        notify("API key created");
        void fetchKeys();
      } catch (err) {
        notify(err instanceof Error ? err.message : String(err), "error");
      } finally {
        setCreating(false);
      }
    },
    [name, notify, fetchKeys],
  );

  const handleRevoke = useCallback(
    async (id: string | number) => {
      try {
        const res = await fetch(`/v1/keys/${id}/revoke`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) throw new Error(await readApiError(res));
        notify("API key revoked");
        void fetchKeys();
      } catch (err) {
        notify(err instanceof Error ? err.message : String(err), "error");
      }
    },
    [notify, fetchKeys],
  );

  const handleRotate = useCallback(
    async (id: string | number) => {
      try {
        const res = await fetch(`/v1/keys/${id}/rotate`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const data = (await res.json()) as ApiKey | { key?: ApiKey };
        const rawKey =
          (data as ApiKey)?.raw_key ?? (data as { key?: ApiKey }).key?.raw_key;
        if (rawKey) setRevealKey(rawKey);
        notify("API key rotated");
        void fetchKeys();
      } catch (err) {
        notify(err instanceof Error ? err.message : String(err), "error");
      }
    },
    [notify, fetchKeys],
  );

  const sortedKeys = [...keys].sort((a, b) => {
    const ta = new Date(a.created_at || 0).getTime();
    const tb = new Date(b.created_at || 0).getTime();
    return tb - ta;
  });

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">API keys</h1>
          <div className="dash-page-subtitle">
            Authenticate requests with <code>Authorization: Bearer</code>.
          </div>
        </div>
        <a
          className="dash-btn dash-btn-secondary dash-btn-sm"
          href="/api/docs"
          target="_blank"
          rel="noreferrer"
        >
          API docs
        </a>
      </div>

      {revealKey !== null && (
        <RevealBanner rawKey={revealKey} onDismiss={() => setRevealKey(null)} />
      )}

      <form className="dash-key-create" onSubmit={handleCreate}>
        <div className="dash-field">
          <label htmlFor="key-name">Key name</label>
          <input
            id="key-name"
            className="dash-input"
            type="text"
            placeholder="Production"
            autoComplete="off"
            maxLength={64}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <button
          type="submit"
          className="dash-btn dash-btn-primary"
          disabled={creating || name.trim() === ""}
        >
          {creating ? "Creating…" : "Create key"}
        </button>
      </form>

      {loading ? (
        <div className="dash-info">Loading keys…</div>
      ) : loadError ? (
        <div className="dash-warn">
          Your API keys could not be loaded, so this list is unknown rather than
          empty. Any key you already created is still working.
          <div className="dash-empty-cta-row">
            <button
              type="button"
              className="dash-btn dash-btn-secondary dash-btn-sm"
              onClick={() => void fetchKeys()}
            >
              Retry
            </button>
          </div>
        </div>
      ) : sortedKeys.length === 0 ? (
        <div className="dash-empty">
          No API keys yet.
          <div className="dash-empty-desc">
            Name one above to authenticate requests from your server.
          </div>
        </div>
      ) : (
        <div className="dash-key-list">
          {sortedKeys.map((k) => {
            const active = k.status === "active";
            return (
              <div className="dash-key-row" key={String(k.id)}>
                <div className="dash-key-row-main">
                  <div className="dash-key-row-title">
                    <span className="dash-key-name">
                      {k.name?.trim() || "Untitled key"}
                    </span>
                    {!active && (
                      <span className="dash-pill dash-pill-failed">
                        {k.status}
                      </span>
                    )}
                  </div>
                  <div className="dash-key-meta">
                    <code>{formatKey(k.key_prefix)}</code>
                    <span className="dash-key-sep">·</span>
                    <span>
                      {k.last_used_at
                        ? `last used ${formatDate(k.last_used_at)}`
                        : "never used"}
                    </span>
                  </div>
                </div>
                {active && (
                  <div className="dash-key-row-actions">
                    <button
                      type="button"
                      className="dash-btn dash-btn-secondary dash-btn-sm"
                      onClick={() => handleRotate(k.id)}
                    >
                      Rotate
                    </button>
                    <button
                      type="button"
                      className="dash-btn dash-btn-danger dash-btn-sm"
                      onClick={() => handleRevoke(k.id)}
                    >
                      Revoke
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
