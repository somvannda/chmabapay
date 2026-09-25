"use client";

import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

/**
 * What the deployment can say about itself.
 *
 * `/health` is a compose liveness probe: it answers 200 while the API process is up.
 * That is not the question an operator has at 2am — the question is whether the
 * database is reachable, whether the worker is still draining, and how far behind the
 * queues are. The overview folds those signals into a single "needs attention" line;
 * this page is what sits behind that line, so the diagnosis does not need SSH.
 *
 * It refreshes on a button rather than a timer: a page that silently polls looks
 * current even when the tab has been asleep for an hour, and the "checked at" stamp
 * would then be a lie. The stamp is the contract.
 */

type WorkerSignals = {
  transport: string;
  // False when the deployment cannot report worker health at all — with the
  // in-process transport the API *is* the worker, so there is no cross-process
  // heartbeat and no shared backlog. Reporting zeros there would turn "not
  // measurable" into "all clear".
  watched: boolean;
  heartbeat_ages: Record<string, number | null> | null;
  stale_queues: string[] | null;
  queue_depth: Record<string, number> | null;
  error: string | null;
};

type HealthPayload = {
  app: string;
  database: string;
  worker_transport: string;
  dev_gateway: boolean;
  metrics_scrape_secured: boolean;
  expected_queues: string[];
  heartbeat_max_age_seconds: number;
  workers: WorkerSignals;
};

function formatAge(seconds: number | null): string {
  if (seconds === null) return "no stamp";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 90) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

function formatCheckedAt(at: Date | null): string {
  if (!at) return "—";
  return at.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function AdminHealthPage() {
  const [loading, setLoading] = useState(true);
  const [payload, setPayload] = useState<HealthPayload | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch("/api/v1/admin/health", { credentials: "include" });
      if (!res.ok) throw new Error(await readApiError(res));
      setPayload((await res.json()) as HealthPayload);
      setCheckedAt(new Date());
    } catch (e) {
      // The failure is itself a health signal, so it is kept separate from a
      // successful read that happens to report a problem.
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const workers = payload?.workers ?? null;
  const ages = workers?.heartbeat_ages ?? null;
  const depth = workers?.queue_depth ?? null;
  const queues = payload?.expected_queues ?? [];

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Health</h1>
          <div className="dash-page-subtitle">
            Database reachability, worker heartbeats and queue depth
          </div>
        </div>
      </div>

      <div className="dash-toolbar">
        <div className="dash-info">
          Checked at {formatCheckedAt(checkedAt)}
        </div>
        <div className="dash-toolbar-filters">
          <button
            type="button"
            className="dash-btn dash-btn-secondary dash-btn-sm"
            onClick={() => void load()}
            disabled={loading}
          >
            {loading ? "Checking…" : "Refresh"}
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="dash-warn">
          The console could not read the health endpoint: {errorMsg}
        </div>
      )}

      {payload && (
        <>
          <div className="dash-panel">
            <div className="dash-panel-title">API</div>
            <table className="dash-table">
              <tbody>
                <tr>
                  <td>
                    <div className="dash-stat-label">Process</div>
                  </td>
                  <td>
                    <span className="dash-pill dash-pill-paid">answering</span> —{" "}
                    {payload.app} served this page
                  </td>
                </tr>
                <tr>
                  <td>
                    <div className="dash-stat-label">Database</div>
                  </td>
                  <td>
                    {payload.database === "ok" ? (
                      <span className="dash-pill dash-pill-paid">reachable</span>
                    ) : (
                      <span className="dash-pill dash-pill-failed">
                        {payload.database}
                      </span>
                    )}
                  </td>
                </tr>
                <tr>
                  <td>
                    <div className="dash-stat-label">Worker transport</div>
                  </td>
                  <td>
                    <span className="dash-code-mono">
                      {payload.worker_transport}
                    </span>{" "}
                    <span className="dash-sub">
                      {workers?.watched
                        ? "separate worker process — heartbeats and queue depth are readable"
                        : "heartbeats are not measurable on this transport"}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>
                    <div className="dash-stat-label">Dev gateway</div>
                  </td>
                  <td>
                    {payload.dev_gateway ? (
                      <span className="dash-pill dash-pill-pending">
                        enabled — merchant payments can be settled by hand
                      </span>
                    ) : (
                      <span className="dash-pill dash-pill-paid">
                        off — nothing can settle a payment but the rail
                      </span>
                    )}
                  </td>
                </tr>
                <tr>
                  <td>
                    <div className="dash-stat-label">Metrics scrape</div>
                  </td>
                  <td>
                    {payload.metrics_scrape_secured
                      ? "Token required on /metrics"
                      : "Open — /metrics answers without a token, so it must be restricted at the proxy"}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Workers</div>
            {!workers?.watched ? (
              <div className="dash-empty">
                Worker health is not measurable here.
                <div className="dash-empty-desc">
                  {workers?.error
                    ? workers.error
                    : "With the in-process transport the API is the worker: there is no cross-process heartbeat to read and no shared backlog to measure, so this page shows nothing rather than zeros."}
                </div>
              </div>
            ) : (
              <>
                <table className="dash-table">
                  <thead>
                    <tr>
                      <th>Queue</th>
                      <th>Last drain</th>
                      <th>Pending</th>
                      <th>State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {queues.map((queue) => {
                      const age = ages ? (ages[queue] ?? null) : null;
                      const stale =
                        age === null ||
                        age > (payload.heartbeat_max_age_seconds ?? 60);
                      return (
                        <tr key={queue}>
                          <td>
                            <span className="dash-code-mono">{queue}</span>
                          </td>
                          <td>{formatAge(age)}</td>
                          <td>
                            {depth && depth[queue] !== undefined
                              ? new Intl.NumberFormat("en-US").format(depth[queue])
                              : "—"}
                          </td>
                          <td>
                            {stale ? (
                              <span className="dash-pill dash-pill-failed">
                                not draining
                              </span>
                            ) : (
                              <span className="dash-pill dash-pill-paid">
                                draining
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <div className="dash-hint">
                  A queue is stale when its last drain is older than{" "}
                  {payload.heartbeat_max_age_seconds}s or has no stamp at all. The API
                  keeps answering either way — detection and webhook delivery stop.
                  Pending counts come from the transport itself, so they are the
                  backlog the worker has yet to pick up.
                </div>
                {workers.error && <div className="dash-warn">{workers.error}</div>}
              </>
            )}
          </div>
        </>
      )}

      {loading && !payload && <div className="dash-info">Checking the deployment…</div>}
    </>
  );
}
