"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { confirmAction } from "@/lib/confirm";
import {
  getTreeChange,
  listDomains,
  listTreeHistory,
  revertTreeChange,
  type DeletedDomain,
  type DomainSummary,
  type TreeChange,
  type TreeChangeItem,
} from "@/lib/knowledge-tree-api";

const BTN =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-2.5 py-1 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";

const ACTION_LABELS: Record<string, string> = {
  "domain.create": "Created domain",
  "domain.update": "Edited domain",
  "domain.delete": "Deleted domain",
  "node.create": "Added node",
  "node.update": "Edited node",
  "node.move": "Moved node",
  "node.delete": "Deleted node",
  import: "Imported file",
  "tree.import": "Imported graph",
  "document.upload": "Uploaded document",
  "document.delete": "Deleted document",
  "recommendation.merge": "Merged recommendation",
  revert: "Revert",
};

/**
 * Edit history of the main knowledge graph. Any edit can be reverted; when a
 * later edit touched the same nodes the server refuses and names the change
 * to revert first. A revert is itself an entry, so it can be undone (redo).
 */
export default function TreeHistoryPanel({
  onReverted,
  canRevert,
}: {
  onReverted: () => void;
  /** Only R&D reverts; administrators read the history. */
  canRevert: boolean;
}) {
  const { t } = useTranslation();
  const [domains, setDomains] = useState<DomainSummary[]>([]);
  const [deleted, setDeleted] = useState<DeletedDomain[]>([]);
  const [domainId, setDomainId] = useState("");
  const [changes, setChanges] = useState<TreeChange[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [items, setItems] = useState<Record<number, TreeChangeItem[]>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(
    () =>
      Promise.all([listTreeHistory(domainId || null), listDomains()]).then(([h, d]) => {
        setChanges(h.changes);
        setDeleted(h.deleted_domains);
        setDomains(d);
      }),
    [domainId],
  );

  useEffect(() => {
    let alive = true;
    Promise.all([listTreeHistory(domainId || null), listDomains()])
      .then(([h, d]) => {
        if (!alive) return;
        setChanges(h.changes);
        setDeleted(h.deleted_domains);
        setDomains(d);
      })
      .catch((e: Error) => alive && setMsg(e.message));
    return () => {
      alive = false;
    };
  }, [domainId]);

  async function toggle(id: number) {
    if (open === id) {
      setOpen(null);
      return;
    }
    setOpen(id);
    if (!items[id]) {
      try {
        const detail = await getTreeChange(id);
        setItems((prev) => ({ ...prev, [id]: detail.items }));
      } catch (e) {
        setMsg((e as Error).message);
      }
    }
  }

  async function revert(change: TreeChange) {
    const question =
      change.action === "revert"
        ? t("Undo this revert and re-apply change #{{id}}?", { id: String(change.reverts_id) })
        : t("Revert change #{{id}} ({{summary}})?", { id: String(change.id), summary: change.summary });
    if (!(await confirmAction(question))) return;
    setBusy(change.id);
    setMsg("");
    try {
      await revertTreeChange(change.id);
      setMsg(t("Change #{{id}} was reverted.", { id: String(change.id) }));
      await load();
      onReverted();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <select className={FIELD} value={domainId} onChange={(e) => setDomainId(e.target.value)}>
          <option value="">{t("All domains")}</option>
          {domains.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
          {deleted.map((d) => (
            <option key={d.domain_id} value={d.domain_id}>
              {t("{{name}} (deleted)", { name: d.domain_name || d.domain_id.slice(0, 8) })}
            </option>
          ))}
        </select>
        <p className="text-[12px] text-[var(--muted-foreground)]">
          {t("Every edit of the main graph is kept here. Revert the newest edits first when they touch the same nodes.")}
        </p>
      </div>
      {msg && (
        <p role="status" className="mt-3 text-[13px]">
          {msg}
        </p>
      )}
      {changes === null ? (
        <Loader2 className="mt-4 h-4 w-4 animate-spin text-[var(--muted-foreground)]" />
      ) : changes.length === 0 ? (
        <p className="mt-4 text-[13px] text-[var(--muted-foreground)]">{t("No edits yet.")}</p>
      ) : (
        <ul className="mt-3 divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {changes.map((c) => {
            const reverted = !!c.reverted_by_id;
            return (
              <li key={c.id} className="px-3 py-2">
                <div className="flex items-start gap-2">
                  <button
                    type="button"
                    className="mt-0.5 text-[var(--muted-foreground)]"
                    aria-label={open === c.id ? t("Hide details") : t("Show details")}
                    onClick={() => void toggle(c.id)}
                  >
                    {open === c.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2 text-[13px]">
                      <span className="font-mono text-[11px] text-[var(--muted-foreground)]">#{c.id}</span>
                      <span className={`font-medium ${reverted ? "line-through opacity-60" : ""}`}>
                        {t(ACTION_LABELS[c.action] ?? c.action)}
                      </span>
                      <span className="truncate text-[var(--muted-foreground)]">{c.summary}</span>
                    </div>
                    <div className="text-[11px] text-[var(--muted-foreground)]">
                      {c.domain_name} · {c.actor_name || c.actor_id.slice(0, 10)} ·{" "}
                      {c.created_at?.replace("T", " ").slice(0, 16)}
                      {reverted && ` · ${t("reverted by #{{id}}", { id: String(c.reverted_by_id) })}`}
                    </div>
                  </div>
                  {canRevert && !reverted && (
                    <button type="button" className={BTN} disabled={busy !== null} onClick={() => void revert(c)}>
                      {busy === c.id ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
                      {c.action === "revert" ? t("Undo revert") : t("Revert")}
                    </button>
                  )}
                </div>
                {open === c.id && items[c.id] && (
                  <ul className="mt-2 space-y-0.5 pl-6 text-[12px]">
                    {items[c.id].map((it) => (
                      <li key={it.key} className="flex flex-wrap gap-x-2">
                        <span className="w-28 text-[var(--muted-foreground)]">
                          {t(`${it.kind} ${it.change}`)}
                        </span>
                        {it.change === "changed" && it.before !== it.after ? (
                          <span>
                            <span className="line-through opacity-60">{it.before}</span> → {it.after}
                          </span>
                        ) : (
                          <span>{it.after || it.before}</span>
                        )}
                        {it.fields.length > 0 && (
                          <span className="text-[var(--muted-foreground)]">({it.fields.join(", ")})</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
