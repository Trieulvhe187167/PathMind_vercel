"use client";

import { useId } from "react";
import { useTranslation } from "react-i18next";
import Link from "next/link";
import type { useEmbeddingModels } from "@/hooks/useEmbeddingModels";

export default function EmbeddingModelSelector({
  catalog,
  disabled = false,
}: {
  catalog: ReturnType<typeof useEmbeddingModels>;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const key = (selection: { profile_id: string; model_id: string }) =>
    JSON.stringify([selection.profile_id, selection.model_id]);
  if (catalog.locked) {
    // Regular users: the administrator's default model, shown read-only.
    const current = catalog.options.find(
      (option) =>
        catalog.selection && key(option) === key(catalog.selection),
    );
    return (
      <div className="space-y-2">
        <span className="block text-xs font-medium">
          {t("Embedding model")}
        </span>
        <div
          id={id}
          className="w-full rounded-lg border border-[var(--border)] bg-[var(--muted)] px-3 py-2 text-sm text-[var(--muted-foreground)]"
        >
          {catalog.loading
            ? t("Loading...")
            : current
              ? `${current.label}${current.detail ? ` · ${current.detail}` : ""}`
              : t("No embedding model is configured yet.")}
        </div>
        <p className="text-xs text-[var(--muted-foreground)]">
          {current || catalog.loading
            ? t(
                "Chosen by the administrator. Indexing documents uses credits from your plan.",
              )
            : t("Ask an administrator to set up an embedding model.")}
        </p>
        {catalog.error && (
          <p role="alert" className="text-xs text-red-600">
            {catalog.error}
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block text-xs font-medium">
        {t("Embedding model")}
      </label>
      <select
        id={id}
        value={catalog.selection ? key(catalog.selection) : ""}
        disabled={disabled || catalog.loading || !!catalog.error}
        onChange={(event) => {
          const selected = catalog.options.find(
            (option) => key(option) === event.target.value,
          );
          catalog.setSelection(
            selected
              ? { profile_id: selected.profile_id, model_id: selected.model_id }
              : null,
          );
        }}
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
      >
        <option value="">
          {catalog.loading ? t("Loading...") : t("Select an embedding model")}
        </option>
        {catalog.options.map((option) => (
          <option key={key(option)} value={key(option)}>
            {option.label} · {option.profile_name}
            {option.detail ? ` · ${option.detail}` : ""}
          </option>
        ))}
      </select>
      <p className="text-xs text-[var(--muted-foreground)]">
        {t(
          "This knowledge base keeps using the selected model when the global default changes.",
        )}
      </p>
      {catalog.error && (
        <p role="alert" className="text-xs text-red-600">
          {catalog.error}
        </p>
      )}
      {!catalog.loading && !catalog.error && catalog.options.length === 0 && (
        <Link
          href="/settings/embedding"
          className="text-xs text-[var(--primary)]"
        >
          {t("Configure an embedding model in Settings first.")}
        </Link>
      )}
    </div>
  );
}
