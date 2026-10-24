"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Trans, useTranslation } from "react-i18next";
import {
  AlertCircle,
  Check,
  Cpu,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { fetchAuthStatus } from "@/lib/auth";
import { inputClass as settingsInputClass } from "@/components/settings/shared";
import {
  createPlan,
  deletePlan,
  getAvailableModels,
  listAdminPlans,
  updatePlanDetails,
  type AdminSubscriptionPlan,
  type PlanPatch,
  listModelPrices,
  type ModelPriceList,
} from "@/lib/admin-api";
import {
  PLAN_FEATURES,
  PLAN_LIMITS,
  type AllowancePeriod,
  type PlanFeature,
  type PlanLimit,
} from "@/lib/entitlements-api";

const MB = 1024 * 1024;

const FEATURE_LABELS: Record<PlanFeature, string> = {
  community: "Community",
  space_extras: "Notebooks, Question Bank & Skills",
  courses: "Courses",
  memory: "Memory",
  reading: "Immersive Reading",
  watching: "Immersive Watching",
  contribute: "Recommend branches to R&D",
};
const LIMIT_LABELS: Record<PlanLimit, string> = {
  max_skills: "Max own skills",
  max_partners: "Max partners",
  mastery_per_month: "New mastery paths",
  books_per_month: "New books",
};

interface Draft {
  name: string;
  description: string;
  sort_order: number;
  price_monthly: number;
  price_yearly: number;
  max_tokens_per_day: number;
  max_tokens_per_month: number;
  max_storage_mb: number;
  max_upload_mb: number;
  allowed_models: string[];
  default_model: string;
  allow_custom_api_key: boolean;
  is_active: boolean;
  features_text: string;
  ent_features: Record<PlanFeature, boolean>;
  /** "" = unlimited. */
  ent_limits: Record<PlanLimit, string>;
  ent_period: AllowancePeriod;
}

function toDraft(p: AdminSubscriptionPlan): Draft {
  return {
    name: p.name,
    description: p.description ?? "",
    sort_order: p.sort_order ?? 0,
    price_monthly: p.price_monthly,
    price_yearly: p.price_yearly,
    max_tokens_per_day: p.max_tokens_per_day,
    max_tokens_per_month: p.max_tokens_per_month,
    max_storage_mb: Math.round(p.max_storage_bytes / MB),
    max_upload_mb: Math.round(p.max_upload_file_size_bytes / MB),
    allowed_models: [...p.allowed_models],
    default_model: p.default_model ?? "",
    allow_custom_api_key: !!p.allow_custom_api_key,
    is_active: p.is_active !== false,
    features_text: (p.custom_features ?? []).join("\n"),
    ent_features: Object.fromEntries(
      PLAN_FEATURES.map((k) => [k, !!p.entitlements?.[k]]),
    ) as Record<PlanFeature, boolean>,
    ent_limits: Object.fromEntries(
      PLAN_LIMITS.map((k) => {
        const v = p.entitlements?.[k];
        return [k, v === null || v === undefined ? "" : String(v)];
      }),
    ) as Record<PlanLimit, string>,
    ent_period: p.entitlements?.allowance_period === "lifetime" ? "lifetime" : "month",
  };
}

function toPatch(d: Draft): PlanPatch {
  return {
    name: d.name.trim(),
    description: d.description.trim(),
    sort_order: d.sort_order,
    price_monthly: d.price_monthly,
    price_yearly: d.price_yearly,
    max_tokens_per_day: d.max_tokens_per_day,
    max_tokens_per_month: d.max_tokens_per_month,
    max_storage_bytes: d.max_storage_mb * MB,
    max_upload_file_size_bytes: d.max_upload_mb * MB,
    allowed_models: d.allowed_models,
    default_model: d.default_model,
    allow_custom_api_key: d.allow_custom_api_key,
    is_active: d.is_active,
    features: d.features_text
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
    entitlements: {
      ...d.ent_features,
      ...(Object.fromEntries(
        PLAN_LIMITS.map((k) => {
          const raw = d.ent_limits[k].trim();
          return [k, raw === "" ? null : Math.max(0, Math.floor(Number(raw) || 0))];
        }),
      ) as Record<PlanLimit, number | null>),
      allowance_period: d.ent_period,
    },
  };
}

const inputCls = settingsInputClass;

function NumberField({
  label,
  value,
  step,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-[var(--muted-foreground)]">
        {label}
      </span>
      <input
        type="number"
        min={0}
        step={step ?? 1}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))}
        className={inputCls}
      />
    </label>
  );
}

export default function AdminPlansPage() {
  const router = useRouter();
  const { t } = useTranslation();
  const [plans, setPlans] = useState<AdminSubscriptionPlan[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [priceList, setPriceList] = useState<ModelPriceList | null>(null);
  const [modelInput, setModelInput] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [deleteTarget, setDeleteTarget] =
    useState<AdminSubscriptionPlan | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [createBusy, setCreateBusy] = useState(false);

  const flash = (msg: string) => {
    setSuccess(msg);
    setTimeout(() => setSuccess(""), 3500);
  };

  const load = useCallback(async () => {
    setError("");
    try {
      const [fetchedPlans, models, prices] = await Promise.all([
        listAdminPlans(),
        getAvailableModels().catch(() => [] as string[]),
        listModelPrices().catch(() => null),
      ]);
      setPriceList(prices);
      setPlans(fetchedPlans);
      setDrafts(
        Object.fromEntries(fetchedPlans.map((p) => [p.id, toDraft(p)])),
      );
      setAvailableModels(models);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : t("Failed to load plans"),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchAuthStatus().then((auth) => {
      if (!auth?.authenticated)
        return router.replace("/login?next=/admin/plans");
      if (!auth.is_admin) return router.replace("/");
      void load();
    });
  }, [router, load]);

  const patchDraft = (id: string, patch: Partial<Draft>) =>
    setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const addModel = (id: string, raw: string) => {
    const model = raw.trim();
    if (!model) return;
    const d = drafts[id];
    if (d && !d.allowed_models.includes(model))
      patchDraft(id, { allowed_models: [...d.allowed_models, model] });
    setModelInput((prev) => ({ ...prev, [id]: "" }));
  };

  async function save(id: string) {
    const d = drafts[id];
    if (!d) return;
    if (!d.name.trim()) return setError(t("Plan name cannot be empty."));
    setSavingId(id);
    setError("");
    try {
      const updated = await updatePlanDetails(id, toPatch(d));
      setPlans((prev) => prev.map((p) => (p.id === id ? updated : p)));
      setDrafts((prev) => ({ ...prev, [id]: toDraft(updated) }));
      flash(t("Saved plan {{name}}.", { name: updated.name }));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("Save failed"));
    } finally {
      setSavingId(null);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreateBusy(true);
    setError("");
    try {
      const created = await createPlan({
        id: newId.trim().toLowerCase(),
        name: newName.trim(),
        is_active: false,
      });
      setShowCreate(false);
      setNewId("");
      setNewName("");
      await load();
      flash(
        t(
          "Created plan {{name}} (currently not on sale — finish configuring it, then turn it on).",
          { name: created.name },
        ),
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("Failed to create plan"));
    } finally {
      setCreateBusy(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    try {
      await deletePlan(deleteTarget.id);
      flash(t("Deleted plan {{name}}.", { name: deleteTarget.name }));
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("Failed to delete plan"));
    } finally {
      setDeleteBusy(false);
      setDeleteTarget(null);
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-[var(--background)] px-4 py-8 text-[var(--foreground)] sm:px-6 lg:px-8">
      <div className="mx-auto max-w-6xl">

        <div className="mb-6 flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
          <div>
            <h1 className="font-serif text-xl font-semibold text-[var(--foreground)]">
              {t("Plans")}
            </h1>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {t(
                "Prices, credit and storage quotas, features and the models each plan may use. Model prices and providers are on the Models page. Changes apply immediately to all users of the plan.",
              )}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--card)]"
            >
              <Plus size={14} /> {t("New plan")}
            </button>
            <button
              onClick={() => {
                setLoading(true);
                void load();
              }}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--muted-foreground)] hover:bg-[var(--card)]"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />{" "}
              {t("Reload")}
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError("")} aria-label={t("Close")}>
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        {success && (
          <div className="mb-4 flex items-center gap-2 rounded-xl border border-success/30 bg-success-surface p-3 text-xs text-success">
            <Check className="h-4 w-4 shrink-0" /> {success}
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-xs text-[var(--muted-foreground)]">
            <RefreshCw className="h-5 w-5 animate-spin text-primary" />{" "}
            {t("Loading...")}
          </div>
        ) : (
          <div className="space-y-6">
            {plans.map((p) => {
              const d = drafts[p.id];
              if (!d) return null;
              const isFree = p.id === "free";
              const dirty =
                JSON.stringify(toPatch(d)) !==
                JSON.stringify(toPatch(toDraft(p)));
              const suggestions = availableModels.filter(
                (m) => !d.allowed_models.includes(m),
              );
              return (
                <div
                  key={p.id}
                  className={`rounded-2xl border bg-[var(--card)] p-6 shadow-sm ${
                    d.is_active
                      ? "border-[var(--border)]"
                      : "border-dashed border-[var(--border)] opacity-80"
                  }`}
                >
                  {/* Header */}
                  <div className="flex flex-col justify-between gap-4 border-b border-[var(--border)] pb-5 sm:flex-row sm:items-start">
                    <div className="flex-1 space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          value={d.name}
                          onChange={(e) =>
                            patchDraft(p.id, { name: e.target.value })
                          }
                          className="rounded-lg border border-transparent bg-transparent px-1 text-lg font-semibold hover:border-[var(--border)] focus:border-primary focus:outline-none"
                          aria-label={t("Plan name")}
                        />
                        <span className="rounded-full bg-[var(--muted)] px-2 py-0.5 font-mono text-[10px] text-[var(--muted-foreground)]">
                          ID: {p.id}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                            d.is_active
                              ? "bg-success-surface text-success"
                              : "bg-[var(--muted)] text-[var(--muted-foreground)]"
                          }`}
                        >
                          {d.is_active ? t("On sale") : t("Not on sale")}
                        </span>
                        <span className="text-xs text-[var(--muted-foreground)]">
                          ·{" "}
                          {t("{{count}} active users", {
                            count: p.active_subscribers,
                          })}
                        </span>
                        {priceList &&
                          (() => {
                            const maxCost =
                              (d.max_tokens_per_month *
                                priceList.credit_base_usd_per_mtok) /
                              1_000_000;
                            const ratio =
                              d.price_monthly > 0
                                ? maxCost / d.price_monthly
                                : null;
                            const tone =
                              ratio == null
                                ? "bg-muted text-muted-foreground"
                                : ratio > 1
                                  ? "bg-destructive/10 text-destructive"
                                  : ratio > 0.7
                                    ? "bg-warning-surface text-warning"
                                    : "bg-success-surface text-success";
                            return (
                              <span
                                title={t(
                                  "API cost if one user uses up the monthly credits, compared with the monthly price",
                                )}
                                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${tone}`}
                              >
                                {t("Max API cost ${{cost}}/month", {
                                  cost: maxCost.toFixed(2),
                                })}
                                {ratio != null &&
                                  ` · ${t("{{percent}}% of price", {
                                    percent: Math.round(ratio * 100),
                                  })}`}
                              </span>
                            );
                          })()}
                      </div>
                      <input
                        value={d.description}
                        onChange={(e) =>
                          patchDraft(p.id, { description: e.target.value })
                        }
                        placeholder={t("Short description shown on the Pricing page")}
                        className={inputCls}
                      />
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {!isFree && (
                        <button
                          onClick={() => setDeleteTarget(p)}
                          title={t("Delete plan")}
                          className="rounded-xl border border-[var(--border)] p-2 text-[var(--muted-foreground)] hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
                      <button
                        disabled={savingId === p.id || !dirty}
                        onClick={() => void save(p.id)}
                        className="flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-40"
                      >
                        {savingId === p.id ? (
                          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Save className="h-3.5 w-3.5" />
                        )}
                        {dirty ? t("Save changes") : t("Saved")}
                      </button>
                    </div>
                  </div>

                  {/* Pricing & quotas */}
                  <div className="grid grid-cols-2 gap-4 border-b border-[var(--border)] py-5 sm:grid-cols-4">
                    <NumberField
                      label={t("Monthly price (USD)")}
                      step={0.01}
                      value={d.price_monthly}
                      onChange={(v) => patchDraft(p.id, { price_monthly: v })}
                    />
                    <NumberField
                      label={t("Yearly price (USD, 0 = 12× monthly)")}
                      step={0.01}
                      value={d.price_yearly}
                      onChange={(v) => patchDraft(p.id, { price_yearly: v })}
                    />
                    <NumberField
                      label={t("Credits / day")}
                      step={1000}
                      value={d.max_tokens_per_day}
                      onChange={(v) =>
                        patchDraft(p.id, { max_tokens_per_day: v })
                      }
                    />
                    <NumberField
                      label={t("Credits / month")}
                      step={10000}
                      value={d.max_tokens_per_month}
                      onChange={(v) =>
                        patchDraft(p.id, { max_tokens_per_month: v })
                      }
                    />
                    <NumberField
                      label={t("Total storage (MB)")}
                      step={10}
                      value={d.max_storage_mb}
                      onChange={(v) => patchDraft(p.id, { max_storage_mb: v })}
                    />
                    <NumberField
                      label={t("Max file size (MB)")}
                      step={5}
                      value={d.max_upload_mb}
                      onChange={(v) => patchDraft(p.id, { max_upload_mb: v })}
                    />
                    <NumberField
                      label={t("Display order")}
                      value={d.sort_order}
                      onChange={(v) => patchDraft(p.id, { sort_order: v })}
                    />
                    <div className="flex flex-col justify-end gap-2 text-xs">
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          className="accent-[var(--primary)]"
                          checked={d.is_active}
                          disabled={isFree}
                          onChange={(e) =>
                            patchDraft(p.id, { is_active: e.target.checked })
                          }
                        />
                        {t("On sale")}
                      </label>
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          className="accent-[var(--primary)]"
                          checked={d.allow_custom_api_key}
                          onChange={(e) =>
                            patchDraft(p.id, {
                              allow_custom_api_key: e.target.checked,
                            })
                          }
                        />
                        {t("Allow own API key")}
                      </label>
                    </div>
                  </div>

                  {/* Entitlements */}
                  <div className="border-b border-[var(--border)] py-5">
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider">
                      {t("What this plan unlocks")}
                    </h3>
                    <p className="mb-3 text-[11px] text-[var(--muted-foreground)]">
                      {t(
                        "Practice, chat history and the Knowledge Center are open on every plan. Leave a limit empty for unlimited; 0 means not included. Lowering a limit never deletes anything.",
                      )}
                    </p>
                    <div className="mb-3 flex flex-wrap gap-x-5 gap-y-2 text-[13px]">
                      {PLAN_FEATURES.map((k) => (
                        <label key={k} className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            className="accent-[var(--primary)]"
                            checked={d.ent_features[k]}
                            onChange={(e) =>
                              patchDraft(p.id, {
                                ent_features: { ...d.ent_features, [k]: e.target.checked },
                              })
                            }
                          />
                          {t(FEATURE_LABELS[k])}
                        </label>
                      ))}
                    </div>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                      {PLAN_LIMITS.map((k) => (
                        <label key={k} className="block">
                          <span className="mb-1 block text-[11px] font-medium text-[var(--muted-foreground)]">
                            {t(LIMIT_LABELS[k])}
                          </span>
                          <input
                            type="number"
                            min={0}
                            step={1}
                            value={d.ent_limits[k]}
                            placeholder={t("Unlimited")}
                            onChange={(e) =>
                              patchDraft(p.id, {
                                ent_limits: { ...d.ent_limits, [k]: e.target.value },
                              })
                            }
                            className={inputCls}
                          />
                        </label>
                      ))}
                      <label className="block">
                        <span className="mb-1 block text-[11px] font-medium text-[var(--muted-foreground)]">
                          {t("Path & book allowance")}
                        </span>
                        <select
                          value={d.ent_period}
                          onChange={(e) =>
                            patchDraft(p.id, {
                              ent_period: e.target.value as AllowancePeriod,
                            })
                          }
                          className={inputCls}
                        >
                          <option value="month">{t("Resets every month")}</option>
                          <option value="lifetime">{t("One-time trial")}</option>
                        </select>
                      </label>
                    </div>
                  </div>

                  {/* Features */}
                  <div className="border-b border-[var(--border)] py-5">
                    <label className="mb-1 block text-[11px] font-medium text-[var(--muted-foreground)]">
                      {t(
                        "Features shown on the Pricing page (one per line — leave empty to generate them from the actual quotas)",
                      )}
                    </label>
                    <textarea
                      rows={3}
                      value={d.features_text}
                      onChange={(e) =>
                        patchDraft(p.id, { features_text: e.target.value })
                      }
                      placeholder={p.features.join("\n")}
                      className={`${inputCls} font-sans`}
                    />
                  </div>

                  {/* Models */}
                  <div className="pt-5">
                    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider">
                        <Cpu className="h-4 w-4 text-primary" />
                        {t("Allowed AI models ({{count}})", {
                          count: d.allowed_models.length,
                        })}
                      </h3>
                      <span className="text-[11px] text-[var(--muted-foreground)]">
                        {t(
                          "Empty = no model restriction. Version suffixes (e.g. -20241022) are accepted automatically.",
                        )}
                      </span>
                    </div>
                    <div className="mb-4 flex min-h-[52px] flex-wrap items-center gap-2 rounded-2xl border border-[var(--border)] bg-muted/30 p-3">
                      {d.allowed_models.length === 0 ? (
                        <span className="text-xs italic text-[var(--muted-foreground)]">
                          {t(
                            "Unrestricted — users of this plan can use every configured model.",
                          )}
                        </span>
                      ) : (
                        d.allowed_models.map((m) => (
                          <span
                            key={m}
                            className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border)] bg-[var(--card)] px-3 py-1 font-mono text-xs"
                          >
                            {m}
                            <button
                              type="button"
                              onClick={() =>
                                patchDraft(p.id, {
                                  allowed_models: d.allowed_models.filter(
                                    (x) => x !== m,
                                  ),
                                })
                              }
                              className="rounded-full p-0.5 text-[var(--muted-foreground)] hover:text-destructive"
                              aria-label={t("Remove {{name}}", { name: m })}
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </span>
                        ))
                      )}
                    </div>
                    {suggestions.length > 0 && (
                      <div className="mb-4 flex flex-wrap items-center gap-1.5">
                        <Sparkles className="h-3 w-3 text-warning" />
                        {suggestions.map((m) => (
                          <button
                            key={m}
                            type="button"
                            onClick={() => addModel(p.id, m)}
                            className="inline-flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1 font-mono text-[11px] text-[var(--muted-foreground)] hover:border-primary/40 hover:text-primary"
                          >
                            <Plus className="h-3 w-3" />
                            {m}
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="flex max-w-md items-center gap-2">
                      <input
                        type="text"
                        placeholder={t("Other model name (e.g. {{example}})", {
                          example: "claude-3-7-sonnet",
                        })}
                        value={modelInput[p.id] ?? ""}
                        onChange={(e) =>
                          setModelInput((prev) => ({
                            ...prev,
                            [p.id]: e.target.value,
                          }))
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            addModel(p.id, modelInput[p.id] ?? "");
                          }
                        }}
                        className={inputCls}
                      />
                      <button
                        type="button"
                        onClick={() => addModel(p.id, modelInput[p.id] ?? "")}
                        disabled={!(modelInput[p.id] ?? "").trim()}
                        className="flex shrink-0 items-center gap-1 rounded-xl border border-[var(--border)] px-3.5 py-1.5 text-xs font-semibold hover:bg-[var(--muted)] disabled:opacity-40"
                      >
                        <Plus className="h-3.5 w-3.5" /> {t("Add")}
                      </button>
                    </div>
                    <div className="mt-4 max-w-md">
                      <label className="mb-1 block text-xs font-semibold">
                        {t("Default model")}
                      </label>
                      <select
                        value={d.default_model}
                        onChange={(e) => patchDraft(p.id, { default_model: e.target.value })}
                        className={inputCls}
                      >
                        <option value="">{t("Deployment default (Settings → Models)")}</option>
                        {(d.allowed_models.length > 0
                          ? d.allowed_models
                          : d.default_model
                            ? [d.default_model]
                            : []
                        ).map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                      <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                        {t(
                          "Used when a user of this plan has not chosen a model. Pick a cheap model for free plans so the quota goes further.",
                        )}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

      </div>

      {showCreate && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] px-4"
          role="dialog"
          aria-modal="true"
          onClick={() => !createBusy && setShowCreate(false)}
        >
          <form
            onClick={(e) => e.stopPropagation()}
            onSubmit={handleCreate}
            className="w-full max-w-sm space-y-3 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-xl"
          >
            <h2 className="text-base font-semibold">{t("Create new plan")}</h2>
            <label className="block text-xs">
              <span className="mb-1 block text-[var(--muted-foreground)]">
                {t("ID (lowercase letters, digits, - _)")}
              </span>
              <input
                required
                pattern="[a-z0-9][a-z0-9_\-]{1,48}"
                value={newId}
                onChange={(e) => setNewId(e.target.value)}
                className={inputCls}
                placeholder={t("e.g. {{example}}", { example: "student" })}
              />
            </label>
            <label className="block text-xs">
              <span className="mb-1 block text-[var(--muted-foreground)]">
                {t("Display name")}
              </span>
              <input
                required
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className={inputCls}
                placeholder={t("e.g. {{example}}", { example: "Student Plus" })}
              />
            </label>
            <p className="text-[11px] text-[var(--muted-foreground)]">
              {t(
                "New plans are created as not on sale with default quotas; configure the plan, then turn on “On sale”.",
              )}
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="rounded-lg px-3 py-1.5 text-sm text-[var(--muted-foreground)]"
              >
                {t("Cancel")}
              </button>
              <button
                type="submit"
                disabled={createBusy}
                className="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
              >
                {createBusy ? t("Creating...") : t("Create plan")}
              </button>
            </div>
          </form>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title={t("Delete plan?")}
        tone="danger"
        confirmLabel={t("Delete plan")}
        busy={deleteBusy}
        onConfirm={() => void handleDelete()}
        onCancel={() => setDeleteTarget(null)}
      >
        <Trans
          i18nKey="Plan <b>{{name}}</b> will be permanently deleted. If the plan has ever had subscriptions or payments, the system will refuse — in that case untick “On sale” instead of deleting it."
          values={{ name: deleteTarget?.name ?? "" }}
          components={{ b: <b /> }}
        />
      </ConfirmDialog>
    </div>
  );
}
