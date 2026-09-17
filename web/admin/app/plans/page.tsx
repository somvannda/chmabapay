"use client";

import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/lib/apiError";
import { useToast } from "@/components/Toast";

type Plan = {
  id: number;
  code: string;
  name: string;
  monthly_fee_cents: number;
  base_payments_included: number;
  max_stores: number | null;
  max_keys_per_account: number;
  max_webhooks_per_account: number;
  csv_export_enabled: boolean;
  priority_support: boolean;
  is_public: boolean;
  is_active: boolean;
  tagline: string | null;
  features: string[] | null;
  is_featured: boolean;
  subscriptions_count: number;
};

type Draft = {
  name: string;
  monthly_fee_cents: string;
  base_payments_included: string;
  max_stores: string;
  max_keys_per_account: string;
  max_webhooks_per_account: string;
  csv_export_enabled: boolean;
  priority_support: boolean;
  is_public: boolean;
  is_active: boolean;
  is_featured: boolean;
  tagline: string;
  features: string;
};

type CreateDraft = Draft & { code: string };

const nf = new Intl.NumberFormat("en-US");

const FEATURES_MAX = 12;
const FEATURE_MAX_LEN = 80;

function formatCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

/** Feature bullets are edited as one bullet per line. */
function featuresToText(features: string[] | null | undefined): string {
  return (features ?? []).join("\n");
}

function textToFeatures(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function toDraft(plan: Plan): Draft {
  return {
    name: plan.name,
    monthly_fee_cents: String(plan.monthly_fee_cents),
    base_payments_included: String(plan.base_payments_included),
    max_stores: plan.max_stores === null ? "" : String(plan.max_stores),
    max_keys_per_account: String(plan.max_keys_per_account),
    max_webhooks_per_account: String(plan.max_webhooks_per_account),
    csv_export_enabled: plan.csv_export_enabled,
    priority_support: plan.priority_support,
    is_public: plan.is_public,
    is_active: plan.is_active,
    is_featured: plan.is_featured,
    tagline: plan.tagline ?? "",
    features: featuresToText(plan.features),
  };
}

function validateCopy(
  tagline: string,
  features: string[],
): string | null {
  if (tagline.length > 160) {
    return "Tagline must be 160 characters or fewer.";
  }
  if (features.length > FEATURES_MAX) {
    return `Use at most ${FEATURES_MAX} feature bullets.`;
  }
  for (const item of features) {
    if (item.length > FEATURE_MAX_LEN) {
      return `Each feature bullet must be ${FEATURE_MAX_LEN} characters or fewer.`;
    }
  }
  return null;
}

/** Whole-number field, or null when the input is not a bare non-negative integer. */
function parseCount(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  return Number(trimmed);
}

/**
 * Compare the edited draft against the saved plan and return only the fields
 * that actually changed. An empty "Max stores" input means unlimited (null).
 */
function buildPatch(
  plan: Plan,
  draft: Draft,
): { patch: Record<string, unknown>; error: string | null } {
  const patch: Record<string, unknown> = {};

  const name = draft.name.trim();
  if (!name) return { patch: {}, error: "Plan name is required." };
  if (name !== plan.name) patch.name = name;

  const numericFields: { draft: keyof Draft; plan: keyof Plan; label: string }[] =
    [
      {
        draft: "monthly_fee_cents",
        plan: "monthly_fee_cents",
        label: "Monthly fee",
      },
      {
        draft: "base_payments_included",
        plan: "base_payments_included",
        label: "Included payments",
      },
      {
        draft: "max_keys_per_account",
        plan: "max_keys_per_account",
        label: "Max API keys",
      },
      {
        draft: "max_webhooks_per_account",
        plan: "max_webhooks_per_account",
        label: "Max webhooks",
      },
    ];

  for (const field of numericFields) {
    const raw = String(draft[field.draft]).trim();
    if (!/^\d+$/.test(raw)) {
      return {
        patch: {},
        error: `${field.label} must be a whole number of 0 or more.`,
      };
    }
    const value = Number(raw);
    if (value !== plan[field.plan]) patch[String(field.plan)] = value;
  }

  const maxStoresRaw = draft.max_stores.trim();
  if (maxStoresRaw !== "" && !/^\d+$/.test(maxStoresRaw)) {
    return {
      patch: {},
      error:
        "Max stores must be a whole number of 0 or more, or empty for unlimited.",
    };
  }
  const maxStores = maxStoresRaw === "" ? null : Number(maxStoresRaw);
  if (maxStores !== plan.max_stores) patch.max_stores = maxStores;

  const tagline = draft.tagline.trim();
  if (tagline !== (plan.tagline ?? "")) patch.tagline = tagline || null;

  const features = textToFeatures(draft.features);
  if (features.join("\n") !== (plan.features ?? []).join("\n")) {
    patch.features = features;
  }

  const copyError = validateCopy(tagline, features);
  if (copyError) return { patch: {}, error: copyError };

  const boolFields: { draft: keyof Draft; plan: keyof Plan }[] = [
    { draft: "csv_export_enabled", plan: "csv_export_enabled" },
    { draft: "priority_support", plan: "priority_support" },
    { draft: "is_public", plan: "is_public" },
    { draft: "is_active", plan: "is_active" },
    { draft: "is_featured", plan: "is_featured" },
  ];
  for (const field of boolFields) {
    if (draft[field.draft] !== plan[field.plan]) {
      patch[String(field.plan)] = draft[field.draft];
    }
  }

  return { patch, error: null };
}

function Flag({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={on ? "dash-badge" : "dash-badge dash-badge-muted"}>
      {label}
    </span>
  );
}

function PlanFields<T extends Draft>({
  draft,
  onChange,
  idPrefix,
}: {
  draft: T;
  onChange: <K extends keyof T>(key: K, value: T[K]) => void;
  idPrefix: string;
}) {
  return (
    <>
      <div className="dash-form-row">
        <div className="dash-field">
          <label htmlFor={`${idPrefix}-name`}>Name</label>
          <input
            id={`${idPrefix}-name`}
            className="dash-input"
            type="text"
            value={draft.name}
            onChange={(e) => onChange("name", e.target.value)}
          />
        </div>
        <div className="dash-field">
          <label htmlFor={`${idPrefix}-fee`}>Monthly fee (cents)</label>
          <input
            id={`${idPrefix}-fee`}
            className="dash-input"
            type="number"
            min={0}
            step={1}
            value={draft.monthly_fee_cents}
            onChange={(e) => onChange("monthly_fee_cents", e.target.value)}
          />
        </div>
      </div>

      <div className="dash-form-row">
        <div className="dash-field">
          <label htmlFor={`${idPrefix}-included`}>Included payments</label>
          <input
            id={`${idPrefix}-included`}
            className="dash-input"
            type="number"
            min={0}
            step={1}
            value={draft.base_payments_included}
            onChange={(e) => onChange("base_payments_included", e.target.value)}
          />
        </div>
        <div className="dash-field">
          <label htmlFor={`${idPrefix}-stores`}>Max stores</label>
          <input
            id={`${idPrefix}-stores`}
            className="dash-input"
            type="number"
            min={0}
            step={1}
            placeholder="Unlimited"
            value={draft.max_stores}
            onChange={(e) => onChange("max_stores", e.target.value)}
          />
        </div>
      </div>

      <div className="dash-form-row">
        <div className="dash-field">
          <label htmlFor={`${idPrefix}-keys`}>Max API keys</label>
          <input
            id={`${idPrefix}-keys`}
            className="dash-input"
            type="number"
            min={0}
            step={1}
            value={draft.max_keys_per_account}
            onChange={(e) => onChange("max_keys_per_account", e.target.value)}
          />
        </div>
        <div className="dash-field">
          <label htmlFor={`${idPrefix}-webhooks`}>Max webhooks</label>
          <input
            id={`${idPrefix}-webhooks`}
            className="dash-input"
            type="number"
            min={0}
            step={1}
            value={draft.max_webhooks_per_account}
            onChange={(e) =>
              onChange("max_webhooks_per_account", e.target.value)
            }
          />
        </div>
      </div>

      <div className="dash-field">
        <label htmlFor={`${idPrefix}-tagline`}>Tagline</label>
        <input
          id={`${idPrefix}-tagline`}
          className="dash-input"
          type="text"
          maxLength={160}
          placeholder="For teams making moves."
          value={draft.tagline}
          onChange={(e) => onChange("tagline", e.target.value)}
        />
        <div className="dash-hint">Shown under the plan name on the pricing page.</div>
      </div>

      <div className="dash-field">
        <label htmlFor={`${idPrefix}-features`}>Feature bullets</label>
        <textarea
          id={`${idPrefix}-features`}
          className="dash-textarea"
          rows={5}
          placeholder={"Up to 5 stores\n3 API keys\n15,000 payments / month"}
          value={draft.features}
          onChange={(e) => onChange("features", e.target.value)}
        />
        <div className="dash-hint">
          One bullet per line, max {FEATURES_MAX} bullets.
        </div>
      </div>

      <div className="dash-form-row">
        <div className="dash-field">
          <label>
            <input
              type="checkbox"
              checked={draft.csv_export_enabled}
              onChange={(e) => onChange("csv_export_enabled", e.target.checked)}
            />{" "}
            CSV export
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.priority_support}
              onChange={(e) => onChange("priority_support", e.target.checked)}
            />{" "}
            Priority support
          </label>
        </div>
        <div className="dash-field">
          <label>
            <input
              type="checkbox"
              checked={draft.is_public}
              onChange={(e) => onChange("is_public", e.target.checked)}
            />{" "}
            Public (on the website pricing page)
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.is_active}
              onChange={(e) => onChange("is_active", e.target.checked)}
            />{" "}
            Active (selectable)
          </label>
          <label>
            <input
              type="checkbox"
              checked={draft.is_featured}
              onChange={(e) => onChange("is_featured", e.target.checked)}
            />{" "}
            Featured (&ldquo;Most popular&rdquo; badge)
          </label>
        </div>
      </div>
    </>
  );
}

const NEW_PLAN: CreateDraft = {
  code: "",
  name: "",
  monthly_fee_cents: "0",
  base_payments_included: "3000",
  max_stores: "1",
  max_keys_per_account: "1",
  max_webhooks_per_account: "5",
  csv_export_enabled: true,
  priority_support: false,
  is_public: true,
  is_active: true,
  is_featured: false,
  tagline: "",
  features: "",
};

export default function AdminPlansPage() {
  const { notify } = useToast();

  const [loading, setLoading] = useState(true);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [newPlan, setNewPlan] = useState<CreateDraft>(NEW_PLAN);
  const [creating, setCreating] = useState(false);

  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await fetch("/v1/admin/plans", { credentials: "include" });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as Plan[];
      setPlans(Array.isArray(data) ? data : []);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const startEdit = useCallback((plan: Plan) => {
    setEditingId(plan.id);
    setDraft(toDraft(plan));
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setDraft(null);
  }, []);

  const setDraftField = useCallback(
    <K extends keyof Draft>(key: K, value: Draft[K]) => {
      setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
    },
    [],
  );

  const setNewPlanField = useCallback(
    <K extends keyof CreateDraft>(key: K, value: CreateDraft[K]) => {
      setNewPlan((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const save = useCallback(
    async (plan: Plan) => {
      if (!draft || savingId !== null) return;
      const { patch, error } = buildPatch(plan, draft);
      if (error) {
        notify(error, "error");
        return;
      }
      if (Object.keys(patch).length === 0) {
        notify("No changes to save.");
        cancelEdit();
        return;
      }

      setSavingId(plan.id);
      setErrorMsg(null);
      try {
        const res = await fetch(`/v1/admin/plans/${plan.id}`, {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const updated = (await res.json()) as Plan;
        setPlans((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
        cancelEdit();
        notify(`Plan ${updated.name} updated`);
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        setErrorMsg(message);
        notify(message, "error");
      } finally {
        setSavingId(null);
      }
    },
    [draft, savingId, notify, cancelEdit],
  );

  const create = useCallback(async () => {
    if (creating) return;
    const code = newPlan.code.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9_-]{1,31}$/.test(code)) {
      notify(
        "Code must be 2–32 characters: lowercase letters, digits, - or _.",
        "error",
      );
      return;
    }
    const name = newPlan.name.trim();
    if (!name) {
      notify("Plan name is required.", "error");
      return;
    }
    const tagline = newPlan.tagline.trim();
    const features = textToFeatures(newPlan.features);
    const copyError = validateCopy(tagline, features);
    if (copyError) {
      notify(copyError, "error");
      return;
    }

    const numeric: { key: keyof CreateDraft; label: string }[] = [
      { key: "monthly_fee_cents", label: "Monthly fee" },
      { key: "base_payments_included", label: "Included payments" },
      { key: "max_keys_per_account", label: "Max API keys" },
      { key: "max_webhooks_per_account", label: "Max webhooks" },
    ];
    const body: Record<string, unknown> = {
      code,
      name,
      tagline: tagline || null,
      features,
      csv_export_enabled: newPlan.csv_export_enabled,
      priority_support: newPlan.priority_support,
      is_public: newPlan.is_public,
      is_active: newPlan.is_active,
      is_featured: newPlan.is_featured,
    };
    for (const field of numeric) {
      const value = parseCount(String(newPlan[field.key]));
      if (value === null) {
        notify(`${field.label} must be a whole number of 0 or more.`, "error");
        return;
      }
      body[field.key] = value;
    }
    const storesRaw = newPlan.max_stores.trim();
    if (storesRaw !== "" && !/^\d+$/.test(storesRaw)) {
      notify(
        "Max stores must be a whole number of 0 or more, or empty for unlimited.",
        "error",
      );
      return;
    }
    body.max_stores = storesRaw === "" ? null : Number(storesRaw);

    setCreating(true);
    setErrorMsg(null);
    try {
      const res = await fetch("/v1/admin/plans", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const created = (await res.json()) as Plan;
      setPlans((prev) => [...prev, created]);
      setCreateOpen(false);
      setNewPlan(NEW_PLAN);
      notify(`Plan ${created.name} created`);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setErrorMsg(message);
      notify(message, "error");
    } finally {
      setCreating(false);
    }
  }, [creating, newPlan, notify]);

  const remove = useCallback(
    async (plan: Plan) => {
      const inUse = plan.subscriptions_count > 0;
      const prompt = inUse
        ? `${plan.name} is in use by ${plan.subscriptions_count} subscription(s), so it will be retired (hidden and deactivated) rather than deleted. Continue?`
        : `Delete ${plan.name}? This cannot be undone.`;
      if (!window.confirm(prompt)) return;

      setDeletingId(plan.id);
      setErrorMsg(null);
      try {
        const res = await fetch(`/v1/admin/plans/${plan.id}`, {
          method: "DELETE",
          credentials: "include",
        });
        if (!res.ok) throw new Error(await readApiError(res));
        const result = (await res.json()) as {
          retired: boolean;
          deleted: boolean;
        };
        await load();
        if (result.retired) notify(`Plan ${plan.name} retired`);
        else notify(`Plan ${plan.name} deleted`);
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        setErrorMsg(message);
        notify(message, "error");
      } finally {
        setDeletingId(null);
      }
    },
    [load, notify],
  );

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Plans</h1>
          <div className="dash-page-subtitle">
            Every plan on the platform, including retired and hidden ones
          </div>
        </div>
        <div className="dash-toolbar-filters">
          <button
            type="button"
            className="dash-btn dash-btn-primary"
            onClick={() => {
              setNewPlan(NEW_PLAN);
              setCreateOpen((v) => !v);
            }}
          >
            {createOpen ? "Close" : "New plan"}
          </button>
        </div>
      </div>

      <div className="dash-note">
        These rows are what the website pricing page and the user portal render —
        name, price, tagline and feature bullets all come from here. Leave Max
        stores empty for unlimited. The monthly fee is in cents.
      </div>

      {errorMsg && <div className="dash-warn">{errorMsg}</div>}

      {createOpen && (
        <div className="dash-panel">
          <div className="dash-panel-title">New plan</div>
          <div className="dash-form">
            <div className="dash-field">
              <label htmlFor="np-code">Code</label>
              <input
                id="np-code"
                className="dash-input"
                type="text"
                placeholder="growth"
                value={newPlan.code}
                onChange={(e) => setNewPlanField("code", e.target.value)}
              />
              <div className="dash-hint">
                Stable identifier used by the API and checkout. Lowercase letters,
                digits, - and _. Cannot be changed later.
              </div>
            </div>

            <PlanFields
              draft={newPlan}
              onChange={setNewPlanField}
              idPrefix="np"
            />

            <div className="dash-toolbar-filters">
              <button
                type="button"
                className="dash-btn dash-btn-primary"
                disabled={creating}
                onClick={() => void create()}
              >
                {creating ? "Creating…" : "Create plan"}
              </button>
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                disabled={creating}
                onClick={() => {
                  setCreateOpen(false);
                  setNewPlan(NEW_PLAN);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="dash-panel">
        {loading ? (
          <div className="dash-info">Loading plans…</div>
        ) : plans.length === 0 ? (
          <div className="dash-empty">
            No plans found.
            <div className="dash-empty-desc">
              Create one, or restart the backend to seed the defaults.
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Code</th>
                <th>Pricing copy</th>
                <th>Monthly fee</th>
                <th>Included payments</th>
                <th>Max stores</th>
                <th>Keys</th>
                <th>Webhooks</th>
                <th>Flags</th>
                <th>Subs</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {plans.map((plan) => {
                const isEditing = editingId === plan.id && draft !== null;
                const isSaving = savingId === plan.id;
                const isDeleting = deletingId === plan.id;
                const hidden = !plan.is_active || !plan.is_public;
                const featureCount = (plan.features ?? []).length;

                return (
                  <tr key={plan.id}>
                    {isEditing ? (
                      <>
                        <td>
                          <input
                            className="dash-input"
                            type="text"
                            value={draft.name}
                            aria-label="Plan name"
                            onChange={(e) => setDraftField("name", e.target.value)}
                          />
                        </td>
                        <td>
                          <code>{plan.code}</code>
                        </td>
                        <td>
                          <div className="dash-copy-stack">
                            <input
                              className="dash-input"
                              type="text"
                              maxLength={160}
                              placeholder="Tagline"
                              aria-label="Tagline"
                              value={draft.tagline}
                              onChange={(e) =>
                                setDraftField("tagline", e.target.value)
                              }
                            />
                            <textarea
                              className="dash-textarea"
                              rows={4}
                              placeholder="One feature bullet per line"
                              aria-label="Feature bullets"
                              value={draft.features}
                              onChange={(e) =>
                                setDraftField("features", e.target.value)
                              }
                            />
                          </div>
                        </td>
                        <td>
                          <input
                            className="dash-input"
                            type="number"
                            min={0}
                            step={1}
                            value={draft.monthly_fee_cents}
                            aria-label="Monthly fee in cents"
                            onChange={(e) =>
                              setDraftField("monthly_fee_cents", e.target.value)
                            }
                          />
                        </td>
                        <td>
                          <input
                            className="dash-input"
                            type="number"
                            min={0}
                            step={1}
                            value={draft.base_payments_included}
                            aria-label="Included payments"
                            onChange={(e) =>
                              setDraftField(
                                "base_payments_included",
                                e.target.value,
                              )
                            }
                          />
                        </td>
                        <td>
                          <input
                            className="dash-input"
                            type="number"
                            min={0}
                            step={1}
                            placeholder="Unlimited"
                            value={draft.max_stores}
                            aria-label="Max stores, empty for unlimited"
                            onChange={(e) =>
                              setDraftField("max_stores", e.target.value)
                            }
                          />
                        </td>
                        <td>
                          <input
                            className="dash-input"
                            type="number"
                            min={0}
                            step={1}
                            value={draft.max_keys_per_account}
                            aria-label="Max API keys per account"
                            onChange={(e) =>
                              setDraftField("max_keys_per_account", e.target.value)
                            }
                          />
                        </td>
                        <td>
                          <input
                            className="dash-input"
                            type="number"
                            min={0}
                            step={1}
                            value={draft.max_webhooks_per_account}
                            aria-label="Max webhooks per account"
                            onChange={(e) =>
                              setDraftField(
                                "max_webhooks_per_account",
                                e.target.value,
                              )
                            }
                          />
                        </td>
                        <td>
                          <label className="dash-check">
                            <input
                              type="checkbox"
                              checked={draft.csv_export_enabled}
                              onChange={(e) =>
                                setDraftField("csv_export_enabled", e.target.checked)
                              }
                            />{" "}
                            CSV
                          </label>
                          <label className="dash-check">
                            <input
                              type="checkbox"
                              checked={draft.priority_support}
                              onChange={(e) =>
                                setDraftField("priority_support", e.target.checked)
                              }
                            />{" "}
                            Priority
                          </label>
                          <label className="dash-check">
                            <input
                              type="checkbox"
                              checked={draft.is_public}
                              onChange={(e) =>
                                setDraftField("is_public", e.target.checked)
                              }
                            />{" "}
                            Public
                          </label>
                          <label className="dash-check">
                            <input
                              type="checkbox"
                              checked={draft.is_active}
                              onChange={(e) =>
                                setDraftField("is_active", e.target.checked)
                              }
                            />{" "}
                            Active
                          </label>
                        </td>
                        <td>{plan.subscriptions_count}</td>
                        <td>
                          <div className="dash-toolbar-filters">
                            <button
                              type="button"
                              className="dash-btn dash-btn-primary dash-btn-sm"
                              disabled={isSaving}
                              onClick={() => void save(plan)}
                            >
                              {isSaving ? "Saving…" : "Save"}
                            </button>
                            <button
                              type="button"
                              className="dash-btn dash-btn-secondary dash-btn-sm"
                              disabled={isSaving}
                              onClick={cancelEdit}
                            >
                              Cancel
                            </button>
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td>
                          {plan.name}
                          {hidden && (
                            <>
                              {" "}
                              <span className="dash-badge dash-badge-muted">
                                {plan.is_active ? "hidden" : "retired"}
                              </span>
                            </>
                          )}
                        </td>
                        <td>
                          <code>{plan.code}</code>
                        </td>
                        <td>
                          <div>{plan.tagline || "—"}</div>
                          <div className="dash-hint">
                            {featureCount > 0
                              ? `${featureCount} bullet${featureCount === 1 ? "" : "s"}`
                              : "no bullets"}
                          </div>
                        </td>
                        <td>{formatCents(plan.monthly_fee_cents)}</td>
                        <td>{nf.format(plan.base_payments_included)}</td>
                        <td>
                          {plan.max_stores === null
                            ? "Unlimited"
                            : nf.format(plan.max_stores)}
                        </td>
                        <td>{nf.format(plan.max_keys_per_account)}</td>
                        <td>{nf.format(plan.max_webhooks_per_account)}</td>
                        <td>
                          <div className="dash-flags">
                            <Flag on={plan.csv_export_enabled} label="CSV" />
                            <Flag on={plan.priority_support} label="Priority" />
                            <Flag on={plan.is_public} label="Public" />
                            <Flag on={plan.is_active} label="Active" />
                            {plan.is_featured && <Flag on label="Featured" />}
                          </div>
                        </td>
                        <td>{plan.subscriptions_count}</td>
                        <td>
                          <div className="dash-toolbar-filters">
                            <button
                              type="button"
                              className="dash-btn dash-btn-secondary dash-btn-sm"
                              onClick={() => startEdit(plan)}
                              disabled={editingId !== null || isDeleting}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="dash-btn dash-btn-secondary dash-btn-sm"
                              onClick={() => void remove(plan)}
                              disabled={isDeleting || editingId !== null}
                            >
                              {isDeleting
                                ? "…"
                                : plan.subscriptions_count > 0
                                  ? "Retire"
                                  : "Delete"}
                            </button>
                          </div>
                        </td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
