"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { confirmAction } from "@/lib/confirm";
import {
  deleteGraphLabel,
  deleteGraphRelationType,
  getGraphSchema,
  saveGraphLabel,
  saveGraphRelationType,
  type GraphLabel,
  type GraphRelationType,
  type GraphSchema,
} from "@/lib/knowledge-tree-api";

const BTN =
  "inline-flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-[12px]";

/**
 * The graph's vocabulary: node labels (with their colour) and
 * relation types. R&D edits it (each change is in the edit history);
 * administrators read it.
 */
export default function GraphSchemaPanel({ canEdit }: { canEdit: boolean }) {
  const { t } = useTranslation();
  const [schema, setSchema] = useState<GraphSchema | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [label, setLabel] = useState({ name: "", color: "#7fb3d5", description: "" });
  const [rel, setRel] = useState({ name: "", description: "" });

  const load = useCallback(() => getGraphSchema().then(setSchema), []);
  useEffect(() => {
    let alive = true;
    getGraphSchema()
      .then((s) => alive && setSchema(s))
      .catch((e: Error) => alive && setMsg(e.message));
    return () => {
      alive = false;
    };
  }, []);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setMsg("");
    try {
      await fn();
      await load();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!schema) return <Loader2 className="h-4 w-4 animate-spin text-[var(--muted-foreground)]" />;

  const labelRow = (l: GraphLabel) => (
    <li key={l.id} className="flex items-start gap-2 border-b border-[var(--border)]/60 py-1.5">
      <span className="mt-1 inline-block h-3 w-3 shrink-0 rounded-full" style={{ background: l.color || "var(--muted-foreground)" }} />
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium">{l.name}</div>
        {l.description && <div className="text-[11px] text-[var(--muted-foreground)]">{l.description}</div>}
      </div>
      {canEdit && (
        <button
          type="button"
          className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
          aria-label={t("Remove label")}
          disabled={busy}
          onClick={async () => {
            if (await confirmAction(t("Remove the label {{name}}?", { name: l.name }), { tone: "danger" }))
              void act(() => deleteGraphLabel(l.id));
          }}
        >
          <Trash2 size={13} />
        </button>
      )}
    </li>
  );
  const relRow = (r: GraphRelationType) => (
    <li key={r.id} className="flex items-start gap-2 border-b border-[var(--border)]/60 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="font-mono text-[12px]">{r.name}</div>
        {r.description && <div className="text-[11px] text-[var(--muted-foreground)]">{r.description}</div>}
      </div>
      {canEdit && r.name !== schema.implicit_relation && (
        <button
          type="button"
          className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
          aria-label={t("Remove relation type")}
          disabled={busy}
          onClick={async () => {
            if (await confirmAction(t("Remove the relation type {{name}}?", { name: r.name }), { tone: "danger" }))
              void act(() => deleteGraphRelationType(r.id));
          }}
        >
          <Trash2 size={13} />
        </button>
      )}
    </li>
  );

  return (
    <div>
      {msg && (
        <p role="status" className="mb-3 text-[13px]">
          {msg}
        </p>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        <section className="rounded-xl border border-[var(--border)] p-4">
          <h2 className="mb-1 text-sm font-semibold">{t("Node labels")}</h2>
          <ul className="mt-2">{schema.labels.map(labelRow)}</ul>
          {canEdit && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              <input className={`${FIELD} w-32`} placeholder={t("Label")} value={label.name} onChange={(e) => setLabel({ ...label, name: e.target.value })} />
              <input type="color" className="h-7 w-9 rounded border border-[var(--border)]" value={label.color} onChange={(e) => setLabel({ ...label, color: e.target.value })} aria-label={t("Colour")} />
              <input className={`${FIELD} min-w-0 flex-1`} placeholder={t("Description")} value={label.description} onChange={(e) => setLabel({ ...label, description: e.target.value })} />
              <button
                type="button"
                className={BTN}
                disabled={busy || !label.name.trim()}
                onClick={() => void act(async () => { await saveGraphLabel(label); setLabel({ ...label, name: "", description: "" }); })}
              >
                {t("Add label")}
              </button>
            </div>
          )}
        </section>
        <section className="rounded-xl border border-[var(--border)] p-4">
          <h2 className="mb-1 text-sm font-semibold">{t("Relation types")}</h2>
          <p className="mb-2 text-[11px] text-[var(--muted-foreground)]">
            {t("{{name}} also comes from the outline: a heading inside another one is part of it.", { name: schema.implicit_relation })}
          </p>
          <ul>{schema.relations.map(relRow)}</ul>
          {canEdit && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              <input className={`${FIELD} w-40 font-mono`} placeholder="RELATION_NAME" value={rel.name} onChange={(e) => setRel({ ...rel, name: e.target.value })} />
              <input className={`${FIELD} min-w-0 flex-1`} placeholder={t("Description")} value={rel.description} onChange={(e) => setRel({ ...rel, description: e.target.value })} />
              <button
                type="button"
                className={BTN}
                disabled={busy || !rel.name.trim()}
                onClick={() => void act(async () => { await saveGraphRelationType(rel); setRel({ name: "", description: "" }); })}
              >
                {t("Add relation type")}
              </button>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
