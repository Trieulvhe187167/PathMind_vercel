"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  addGraphEdge,
  deleteGraphEdge,
  getDomainGraph,
  setNodeGraph,
  type GraphEdge,
  type GraphNode,
  type GraphSchema,
  type NodeDetail,
} from "@/lib/knowledge-tree-api";

const BTN =
  "inline-flex items-center gap-1 rounded-lg border border-[var(--border)] px-2 py-1 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-[12px]";

function fmtProps(props: Record<string, unknown>) {
  const entries = Object.entries(props);
  return entries.length ? ` {${entries.map(([k, v]) => `${k}: ${String(v)}`).join(", ")}}` : "";
}

/** "a: 1, b: x" -> {a: 1, b: "x"} (numbers and true/false keep their type). */
function parseProps(text: string): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const part of text.split(",")) {
    const i = part.indexOf(":");
    if (i < 0) continue;
    const key = part.slice(0, i).trim();
    const raw = part.slice(i + 1).trim();
    if (!key) continue;
    out[key] = /^-?\d+(\.\d+)?$/.test(raw) ? Number(raw) : raw === "true" ? true : raw === "false" ? false : raw;
  }
  return out;
}

/**
 * Graph data of one node: its type, entity fields, properties and relations.
 * R&D edits them here; administrators only read.
 */
export default function NodeGraphPanel({
  node,
  canEdit,
  onChanged,
  onError,
}: {
  node: NodeDetail;
  canEdit: boolean;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const { t } = useTranslation();
  const [schema, setSchema] = useState<GraphSchema | null>(null);
  const [others, setOthers] = useState<GraphNode[]>([]);
  const [props, setProps] = useState(() => fmtProps(node.properties).slice(2, -1));
  const [entity, setEntity] = useState(() => ({
    key: node.key,
    names: Object.entries(node.names).map(([lang, name]) => `${lang}: ${name}`).join(", "),
    description: node.description,
    source: node.provenance.source ?? "",
    confidence: node.provenance.confidence === undefined ? "" : String(node.provenance.confidence),
  }));
  const [relType, setRelType] = useState("");
  const [target, setTarget] = useState("");
  const [relProps, setRelProps] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    getDomainGraph(node.domain_id)
      .then((g) => {
        if (!alive) return;
        setSchema(g.schema);
        setOthers(g.nodes.filter((n) => n.id !== node.id).sort((a, b) => a.title.localeCompare(b.title)));
      })
      .catch(onError);
    return () => {
      alive = false;
    };
  }, [node.domain_id, node.id, onError]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  const color = schema?.labels.find((l) => l.name === node.label)?.color || "var(--muted-foreground)";
  const relation = (e: GraphEdge, out: boolean) => (
    <li key={e.id} className="flex items-center gap-1.5">
      {out ? <ArrowRight size={11} className="shrink-0" /> : <ArrowLeft size={11} className="shrink-0" />}
      <span className={`font-mono text-[11px] ${e.implicit ? "text-[var(--muted-foreground)]" : ""}`}>{e.type}</span>
      <span className="min-w-0 truncate">
        {out ? e.target_title : e.source_title}
        <span className="text-[var(--muted-foreground)]">{fmtProps(e.properties)}</span>
      </span>
      {canEdit && !e.implicit && (
        <button
          type="button"
          className="ml-auto text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
          aria-label={t("Remove relation")}
          disabled={busy}
          onClick={() => void act(() => deleteGraphEdge(e.id))}
        >
          <Trash2 size={12} />
        </button>
      )}
    </li>
  );

  return (
    <div className="mt-3 space-y-3 rounded-xl border border-[var(--border)] p-3 text-[12px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />
        {canEdit && schema ? (
          <>
            <select
              className={FIELD}
              value={node.label}
              disabled={busy}
              onChange={(e) => void act(() => setNodeGraph(node.id, { label: e.target.value }))}
            >
              {schema.labels.map((l) => (
                <option key={l.id} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
            <input
              className={`${FIELD} w-28`}
              defaultValue={node.layer}
              maxLength={12}
              disabled={busy}
              placeholder={t("Layer (optional)")}
              aria-label={t("Layer (optional)")}
              onBlur={(e) => {
                if (e.target.value.trim() !== node.layer) void act(() => setNodeGraph(node.id, { layer: e.target.value.trim() }));
              }}
            />
          </>
        ) : (
          <span>
            <b>{node.label}</b>
            {node.layer && <> · {t("Layer")}: {node.layer}</>}
          </span>
        )}
      </div>

      <div className="space-y-1.5">
        <div className="font-medium">{t("Entity")}</div>
        {canEdit ? (
          <>
            <div className="flex gap-1.5">
              <input
                className={`${FIELD} w-48 font-mono`}
                value={entity.key}
                placeholder="linear-equations"
                aria-label={t("ID")}
                onChange={(e) => setEntity({ ...entity, key: e.target.value })}
              />
              <input
                className={`${FIELD} min-w-0 flex-1`}
                value={entity.names}
                placeholder={t("vi: Tên tiếng Việt, en: English name")}
                aria-label={t("Names by language")}
                onChange={(e) => setEntity({ ...entity, names: e.target.value })}
              />
            </div>
            <textarea
              className={`${FIELD} w-full`}
              rows={2}
              maxLength={1000}
              value={entity.description}
              placeholder={t("Short description")}
              onChange={(e) => setEntity({ ...entity, description: e.target.value })}
            />
            <div className="flex gap-1.5">
              <input
                className={`${FIELD} min-w-0 flex-1`}
                value={entity.source}
                placeholder={t("Source (citation, URL, page)")}
                onChange={(e) => setEntity({ ...entity, source: e.target.value })}
              />
              <input
                className={`${FIELD} w-24`}
                value={entity.confidence}
                inputMode="decimal"
                placeholder={t("Confidence 0–1")}
                onChange={(e) => setEntity({ ...entity, confidence: e.target.value })}
              />
              <button
                type="button"
                className={BTN}
                disabled={busy || !entity.key.trim()}
                onClick={() =>
                  void act(() =>
                    setNodeGraph(node.id, {
                      key: entity.key.trim(),
                      names: Object.fromEntries(
                        Object.entries(parseProps(entity.names)).map(([k, v]) => [k, String(v)]),
                      ),
                      description: entity.description,
                      provenance: {
                        ...node.provenance,
                        source: entity.source,
                        confidence: entity.confidence.trim() === "" ? null : Number(entity.confidence),
                      },
                    }),
                  )
                }
              >
                {t("Save")}
              </button>
            </div>
          </>
        ) : (
          <dl className="grid grid-cols-[90px_1fr] gap-x-2 gap-y-0.5">
            <dt className="text-[var(--muted-foreground)]">{t("ID")}</dt>
            <dd className="font-mono">{node.key}</dd>
            {Object.keys(node.names).length > 0 && (
              <>
                <dt className="text-[var(--muted-foreground)]">{t("Names")}</dt>
                <dd>{Object.entries(node.names).map(([lang, name]) => `${lang}: ${name}`).join(" · ")}</dd>
              </>
            )}
            {node.description && (
              <>
                <dt className="text-[var(--muted-foreground)]">{t("Description")}</dt>
                <dd>{node.description}</dd>
              </>
            )}
          </dl>
        )}
        <p className="text-[11px] text-[var(--muted-foreground)]">
          {[
            node.provenance.source && `${t("Source")}: ${node.provenance.source}`,
            node.excerpts.length > 0 && `${t("Documents")}: ${node.excerpts.map((x) => x.filename).join(", ")}`,
            node.provenance.confidence !== undefined && `${t("Confidence")}: ${node.provenance.confidence}`,
            node.updated_at && `${t("Updated")}: ${node.updated_at.slice(0, 10)}`,
          ]
            .filter(Boolean)
            .join(" · ") || t("No provenance recorded.")}
        </p>
      </div>

      <div>
        <div className="mb-1 font-medium">{t("Properties")}</div>
        {canEdit ? (
          <div className="flex gap-1.5">
            <input
              className={`${FIELD} min-w-0 flex-1`}
              value={props}
              placeholder={t("key: value, key: value")}
              onChange={(e) => setProps(e.target.value)}
            />
            <button
              type="button"
              className={BTN}
              disabled={busy}
              onClick={() => void act(() => setNodeGraph(node.id, { properties: parseProps(props) }))}
            >
              {t("Save")}
            </button>
          </div>
        ) : (
          <p className="text-[var(--muted-foreground)]">{fmtProps(node.properties).slice(2, -1) || t("None")}</p>
        )}
      </div>

      <div>
        <div className="mb-1 font-medium">{t("Relations")}</div>
        <ul className="space-y-1">
          {node.relations.outgoing.map((e) => relation(e, true))}
          {node.relations.incoming.map((e) => relation(e, false))}
          {node.relations.outgoing.length + node.relations.incoming.length === 0 && (
            <li className="text-[var(--muted-foreground)]">{t("No relations yet.")}</li>
          )}
        </ul>
        {canEdit && schema && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            <select className={FIELD} value={relType} onChange={(e) => setRelType(e.target.value)}>
              <option value="">{t("Relation…")}</option>
              {schema.relations.map((r) => (
                <option key={r.id} value={r.name} title={r.description}>
                  {r.name}
                </option>
              ))}
            </select>
            <select className={`${FIELD} min-w-0 flex-1`} value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">{t("Target node…")}</option>
              {others.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.title} ({n.label})
                </option>
              ))}
            </select>
            <input
              className={`${FIELD} w-32`}
              value={relProps}
              placeholder={t("qty: 2")}
              onChange={(e) => setRelProps(e.target.value)}
            />
            <button
              type="button"
              className={BTN}
              disabled={busy || !relType || !target}
              onClick={() =>
                void act(async () => {
                  await addGraphEdge({ source_id: node.id, target_id: target, type: relType, properties: parseProps(relProps) });
                  setTarget("");
                  setRelProps("");
                })
              }
            >
              <Plus size={12} />
              {t("Add relation")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
