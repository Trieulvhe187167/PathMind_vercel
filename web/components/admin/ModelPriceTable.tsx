"use client";

import { useState } from "react";
import { Check, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { inputClass } from "@/components/settings/shared";
import {
  createModelPrice,
  deleteModelPrice,
  updateModelPrice,
  type ModelPriceList,
  type ModelPriceRow,
} from "@/lib/admin-api";
import { formatMultiplier } from "@/lib/billing-api";

interface RowDraft {
  display_name: string;
  input_per_m: string;
  cached_input_per_m: string;
  output_per_m: string;
  aliases: string;
  is_active: boolean;
}

const toDraft = (r: ModelPriceRow): RowDraft => ({
  display_name: r.display_name,
  input_per_m: String(r.input_per_m),
  cached_input_per_m:
    r.cached_input_per_m == null ? "" : String(r.cached_input_per_m),
  output_per_m: String(r.output_per_m),
  aliases: r.aliases.join(", "),
  is_active: r.is_active,
});

const num = (v: string) => Math.max(0, Number(v) || 0);
const cell = `${inputClass} !px-2 !py-1 text-xs`;

/**
 * Admin editor for the API price list. Prices drive how many credits each
 * call costs and the cost/margin figures of the dashboard.
 */
export default function ModelPriceTable({
  data,
  onChanged,
  onError,
}: {
  data: ModelPriceList;
  onChanged: () => void;
  onError: (msg: string) => void;
}) {
  const { t } = useTranslation();
  const [drafts, setDrafts] = useState<Record<string, RowDraft>>(() =>
    Object.fromEntries(data.prices.map((r) => [r.id, toDraft(r)])),
  );
  const [busyId, setBusyId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newRow, setNewRow] = useState({
    id: "",
    name: "",
    input: "",
    output: "",
  });

  const patch = (id: string, p: Partial<RowDraft>) =>
    setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], ...p } }));

  async function save(row: ModelPriceRow) {
    const d = drafts[row.id];
    if (!d) return;
    setBusyId(row.id);
    try {
      await updateModelPrice(row.id, {
        display_name: d.display_name.trim() || row.id,
        input_per_m: num(d.input_per_m),
        cached_input_per_m:
          d.cached_input_per_m.trim() === "" ? null : num(d.cached_input_per_m),
        output_per_m: num(d.output_per_m),
        aliases: d.aliases
          .split(",")
          .map((a) => a.trim())
          .filter(Boolean),
        is_active: d.is_active,
      });
      onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : t("Save failed"));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(row: ModelPriceRow) {
    setBusyId(row.id);
    try {
      await deleteModelPrice(row.id);
      onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : t("Delete failed"));
    } finally {
      setBusyId(null);
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusyId("__new");
    try {
      await createModelPrice({
        id: newRow.id.trim(),
        display_name: newRow.name.trim() || newRow.id.trim(),
        input_per_m: num(newRow.input),
        output_per_m: num(newRow.output),
      });
      setNewRow({ id: "", name: "", input: "", output: "" });
      setAdding(false);
      onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : t("Failed to add model"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6">
      <div className="mb-4 flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <h2 className="font-serif text-lg font-semibold text-[var(--foreground)]">
            {t("Model API price list")}
          </h2>
          <p className="mt-1 max-w-[70ch] text-xs text-[var(--muted-foreground)]">
            <Trans
              i18nKey="Prices are in USD per 1M tokens. Each LLM call deducts credits based on its actual cost (input, cached and output tokens). 1 credit = the cost of 1 token at ${{base}}/1M. Multiplier = (80% input price + 20% output price) / base rate. Models not in the table use the price of the <code>*</code> row. Leave the cache price empty to use 25% of the input price."
              values={{ base: data.credit_base_usd_per_mtok }}
              components={{ code: <code /> }}
            />
          </p>
          {data.default_model && (
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              <Trans
                i18nKey="Default model in use <code>{{model}}</code> → priced as <b>{{priceId}}</b>"
                values={{
                  model: data.default_model,
                  priceId: data.default_model_price_id,
                }}
                components={{
                  code: <code className="text-[var(--foreground)]" />,
                  b: <b className="text-primary" />,
                }}
              />
              {data.default_model_price_id === "*" && (
                <span className="text-warning">
                  {" "}
                  {t("(not in the price list — please add its real price)")}
                </span>
              )}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => setAdding((v) => !v)}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-muted"
        >
          <Plus size={14} /> {t("Add model")}
        </button>
      </div>

      {adding && (
        <form
          onSubmit={add}
          className="mb-4 grid grid-cols-2 gap-2 rounded-xl border border-[var(--border)] bg-muted/40 p-3 sm:grid-cols-5"
        >
          <input
            required
            placeholder={t("id (e.g. {{example}})", { example: "gpt-6-sol" })}
            value={newRow.id}
            onChange={(e) => setNewRow({ ...newRow, id: e.target.value })}
            className={cell}
          />
          <input
            placeholder={t("Display name")}
            value={newRow.name}
            onChange={(e) => setNewRow({ ...newRow, name: e.target.value })}
            className={cell}
          />
          <input
            required
            type="number"
            step="0.001"
            min={0}
            placeholder={t("Input price $/1M")}
            value={newRow.input}
            onChange={(e) => setNewRow({ ...newRow, input: e.target.value })}
            className={cell}
          />
          <input
            required
            type="number"
            step="0.001"
            min={0}
            placeholder={t("Output price $/1M")}
            value={newRow.output}
            onChange={(e) => setNewRow({ ...newRow, output: e.target.value })}
            className={cell}
          />
          <button
            type="submit"
            disabled={busyId === "__new"}
            className="rounded-lg bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {t("Add")}
          </button>
        </form>
      )}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] text-left text-xs">
          <thead className="text-[var(--muted-foreground)]">
            <tr>
              <th className="py-2 pr-2">{t("Model ID")}</th>
              <th className="py-2 pr-2">{t("Name")}</th>
              <th className="py-2 pr-2">{t("Input")}</th>
              <th className="py-2 pr-2">{t("Cache")}</th>
              <th className="py-2 pr-2">{t("Output")}</th>
              <th className="py-2 pr-2">{t("Aliases")}</th>
              <th className="py-2 pr-2">{t("Multiplier")}</th>
              <th className="py-2 pr-2">{t("Enabled")}</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {data.prices.map((r) => {
              const d = drafts[r.id] ?? toDraft(r);
              const dirty = JSON.stringify(d) !== JSON.stringify(toDraft(r));
              const isFallback = r.id === "*";
              return (
                <tr
                  key={r.id}
                  className="border-t border-[var(--border)] align-middle"
                >
                  <td className="py-1.5 pr-2 font-mono">
                    {r.id}
                    {r.notes && (
                      <div className="font-sans text-[10px] text-warning">
                        {r.notes}
                      </div>
                    )}
                  </td>
                  <td className="py-1.5 pr-2">
                    <input
                      value={d.display_name}
                      onChange={(e) =>
                        patch(r.id, { display_name: e.target.value })
                      }
                      className={cell}
                    />
                  </td>
                  {(
                    [
                      "input_per_m",
                      "cached_input_per_m",
                      "output_per_m",
                    ] as const
                  ).map((k) => (
                    <td key={k} className="w-24 py-1.5 pr-2">
                      <input
                        type="number"
                        step="0.001"
                        min={0}
                        value={d[k]}
                        placeholder={
                          k === "cached_input_per_m" ? t("auto") : undefined
                        }
                        onChange={(e) => patch(r.id, { [k]: e.target.value })}
                        className={cell}
                      />
                    </td>
                  ))}
                  <td className="py-1.5 pr-2">
                    <input
                      value={d.aliases}
                      disabled={isFallback}
                      onChange={(e) => patch(r.id, { aliases: e.target.value })}
                      className={cell}
                    />
                  </td>
                  <td className="py-1.5 pr-2 font-semibold text-primary">
                    {formatMultiplier(r.multiplier)}
                  </td>
                  <td className="py-1.5 pr-2">
                    <input
                      type="checkbox"
                      className="accent-[var(--primary)]"
                      checked={d.is_active}
                      disabled={isFallback}
                      onChange={(e) =>
                        patch(r.id, { is_active: e.target.checked })
                      }
                    />
                  </td>
                  <td className="whitespace-nowrap py-1.5 text-right">
                    <button
                      type="button"
                      title={t("Save")}
                      disabled={!dirty || busyId === r.id}
                      onClick={() => void save(r)}
                      className="rounded-md p-1.5 text-primary hover:bg-primary/10 disabled:opacity-30"
                    >
                      {busyId === r.id ? (
                        <RefreshCw size={14} className="animate-spin" />
                      ) : dirty ? (
                        <Save size={14} />
                      ) : (
                        <Check size={14} />
                      )}
                    </button>
                    {dirty && (
                      <button
                        type="button"
                        title={t("Discard changes")}
                        onClick={() => patch(r.id, toDraft(r))}
                        className="rounded-md p-1.5 text-[var(--muted-foreground)] hover:bg-muted"
                      >
                        <X size={14} />
                      </button>
                    )}
                    {!isFallback && (
                      <button
                        type="button"
                        title={t("Delete")}
                        disabled={busyId === r.id}
                        onClick={() => void remove(r)}
                        className="rounded-md p-1.5 text-[var(--muted-foreground)] hover:bg-destructive/10 hover:text-destructive"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
