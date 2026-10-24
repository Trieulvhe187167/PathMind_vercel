"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import StaffPage from "@/components/staff/StaffPage";
import { confirmAction } from "@/lib/confirm";
import {
  downloadRecommendationDocument,
  getRecommendation,
  listRecommendations,
  mergeRecommendation,
  rejectRecommendation,
  type Recommendation,
  type RecNode,
} from "@/lib/staff-api";
import { getTree, listDomains, type DomainSummary, type TreeNode } from "@/lib/knowledge-tree-api";

const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";

function flatten(nodes: TreeNode[], depth = 0): { id: string; label: string }[] {
  return nodes.flatMap((n) => [{ id: n.id, label: `${"— ".repeat(depth)}${n.title}` }, ...flatten(n.children, depth + 1)]);
}

export default function ReviewsPage() {
  // Merging changes the main graph, so only R&D decides; administrators watch.
  const canDecide = useAuthStatus().isRnd;
  const { t } = useTranslation();
  const [filter, setFilter] = useState<"pending" | "merged" | "rejected">("pending");
  const [items, setItems] = useState<Recommendation[]>([]);
  const [open, setOpen] = useState<Recommendation | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [domains, setDomains] = useState<DomainSummary[]>([]);
  const [domainId, setDomainId] = useState("");
  const [targets, setTargets] = useState<{ id: string; label: string }[]>([]);
  const [parentId, setParentId] = useState("");
  const [adopt, setAdopt] = useState(true);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setItems(await listRecommendations(filter));
  }, [filter]);
  useEffect(() => {
    load().catch((e: Error) => setMsg(e.message));
  }, [load]);
  useEffect(() => {
    listDomains().then((d) => {
      setDomains(d);
      if (d[0]) setDomainId((cur) => cur || d[0].id);
    }).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!domainId) return;
    getTree(domainId).then((tr) => setTargets(flatten(tr.nodes))).catch(() => setTargets([]));
    setParentId("");
  }, [domainId]);

  async function openItem(id: string) {
    const r = await getRecommendation(id);
    setOpen(r);
    setPicked(new Set((r.nodes ?? []).map((n) => n.id)));
    setNote("");
    setMsg("");
  }

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg("");
    try {
      await fn();
      setOpen(null);
      await load();
      setMsg(ok);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const byParent = new Map<string | null, RecNode[]>();
  (open?.nodes ?? []).forEach((n) => byParent.set(n.parent_id, [...(byParent.get(n.parent_id) ?? []), n]));
  const render = (pid: string | null, depth: number): React.ReactNode =>
    (byParent.get(pid) ?? []).sort((a, b) => a.position - b.position).map((n) => (
      <li key={n.id}>
        <label className="flex items-start gap-2 py-0.5" style={{ paddingLeft: depth * 16 }}>
          <input
            type="checkbox"
            disabled={open?.status !== "pending"}
            checked={picked.has(n.id)}
            onChange={() => {
              const next = new Set(picked);
              if (next.has(n.id)) next.delete(n.id);
              else next.add(n.id);
              setPicked(next);
            }}
          />
          <span>
            <span className="text-[13px] font-medium">{n.title}</span>
            {n.source_doc && <span className="ml-2 text-[11px] text-[var(--muted-foreground)]">{n.source_doc}</span>}
            {n.summary && <span className="block text-[11px] text-[var(--muted-foreground)]">{n.summary.slice(0, 160)}</span>}
          </span>
        </label>
        <ul>{render(n.id, depth + 1)}</ul>
      </li>
    ));

  return (
    <StaffPage
      wide
      title={t("Reviews")}
      subtitle={t("Branches that Educators recommend. Choose the nodes to merge into the main graph; their own branch stays untouched.")}
    >
      <div className="mb-3 flex gap-2">
        {(["pending", "merged", "rejected"] as const).map((s) => (
          <button key={s} className={`${BTN} ${filter === s ? "bg-[var(--muted)] font-semibold" : ""}`} onClick={() => { setFilter(s); setOpen(null); }}>
            {t(s === "pending" ? "Waiting" : s === "merged" ? "Merged" : "Rejected")}
          </button>
        ))}
      </div>
      {msg && <p role="status" className="mb-3 text-[13px]">{msg}</p>}
      <div className="grid gap-4 md:grid-cols-[320px_1fr]">
        <ul className="space-y-1.5">
          {items.length === 0 && <li className="text-[13px] text-[var(--muted-foreground)]">{t("Nothing here.")}</li>}
          {items.map((r) => (
            <li key={r.id}>
              <button className={`${BTN} w-full text-left ${open?.id === r.id ? "bg-[var(--muted)]" : ""}`} onClick={() => void openItem(r.id)}>
                <span className="block font-medium">{r.title}</span>
                <span className="text-[11px] text-[var(--muted-foreground)]">
                  {r.node_count} {t("nodes")} · {r.document_count} {t("documents")} · {r.created_at?.slice(0, 10)}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <section className="min-w-0 rounded-xl border border-[var(--border)] p-4">
          {!open ? (
            <p className="text-[13px] text-[var(--muted-foreground)]">{t("Pick a recommendation to review.")}</p>
          ) : (
            <>
              <h2 className="text-sm font-semibold">{open.title}</h2>
              {open.note && <p className="mt-1 whitespace-pre-wrap text-[13px]">{open.note}</p>}
              {(open.documents?.length ?? 0) > 0 && (
                <ul className="mt-3 space-y-1 text-[12px]">
                  {open.documents!.map((d, i) => (
                    <li key={d.sha256} className="flex items-center justify-between gap-2">
                      <span className="truncate">{d.filename}</span>
                      <button className={BTN} onClick={() => void downloadRecommendationDocument(open.id, i, d.filename).catch((e: Error) => setMsg(e.message))}>
                        {t("Download")}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <ul className="mt-3 max-h-80 overflow-auto rounded-lg border border-[var(--border)] p-2">{render(null, 0)}</ul>
              {open.status === "pending" && !canDecide ? (
                <p className="mt-3 text-[12px] text-[var(--muted-foreground)]">{t("Waiting for R&D to review. Administrators can read recommendations but not merge them.")}</p>
              ) : open.status === "pending" ? (
                <div className="mt-3 space-y-2">
                  <div className="grid gap-2 sm:grid-cols-2">
                    <select className={FIELD} value={domainId} onChange={(e) => setDomainId(e.target.value)}>
                      {domains.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                    <select className={FIELD} value={parentId} onChange={(e) => setParentId(e.target.value)}>
                      <option value="">{t("Top level of the domain")}</option>
                      {targets.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                    </select>
                  </div>
                  <label className="flex items-center gap-2 text-[12px]">
                    <input type="checkbox" checked={adopt} onChange={(e) => setAdopt(e.target.checked)} />
                    {t("Also add the documents to the store of this domain")}
                  </label>
                  <textarea className={FIELD} rows={2} placeholder={t("Note to the Educator (required to reject)")} value={note} onChange={(e) => setNote(e.target.value)} />
                  <div className="flex gap-2">
                    <button
                      className={BTN}
                      disabled={busy || !domainId || picked.size === 0}
                      onClick={async () => {
                        if (!(await confirmAction(`${t("Merge")} ${picked.size} ${t("nodes")}?`))) return;
                        void act(() => mergeRecommendation(open.id, { domain_id: domainId, parent_id: parentId || null, node_ids: [...picked], adopt_documents: adopt, note }), t("Merged. Points were granted."));
                      }}
                    >
                      {t("Merge selected")}
                    </button>
                    <button className={BTN} disabled={busy || !note.trim()} onClick={() => void act(() => rejectRecommendation(open.id, note), t("Rejected."))}>
                      {t("Reject")}
                    </button>
                  </div>
                </div>
              ) : (
                open.review_note && <p className="mt-3 text-[12px] text-[var(--muted-foreground)]">{t("Note")}: {open.review_note}</p>
              )}
            </>
          )}
        </section>
      </div>
    </StaffPage>
  );
}
