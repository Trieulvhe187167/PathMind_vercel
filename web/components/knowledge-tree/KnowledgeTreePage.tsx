"use client";

import NodeGraphPanel from "@/components/knowledge-tree/NodeGraphPanel";
import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  addNode,
  createDomain,
  deleteDomain,
  deleteNode,
  getNode,
  askKnowledge,
  getRights,
  listKbOptions,
  type KbOption,
  patchDomain,
  type AskResult,
  getTree,
  importFile,
  importTree,
  exportTree,
  listDomains,
  listDocuments,
  uploadDocument,
  deleteDocument,
  downloadDocument,
  listAudit,
  type StoreDocument,
  type AuditEntry,
  patchNode,
  type DomainSummary,
  type NodeDetail,
  type Rights,
  type TreeData,
  type TreeNode,
} from "@/lib/knowledge-tree-api";
import { confirmAction } from "@/lib/confirm";

const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px]";

type Draft =
  | { kind: "add"; parent: TreeNode | null }
  | { kind: "edit"; node: NodeDetail }
  | { kind: "delete"; node: NodeDetail }
  | { kind: "domain" };

export default function KnowledgeTreePage() {
  const { t } = useTranslation();
  const [rights, setRights] = useState<Rights | null>(null);
  const [domains, setDomains] = useState<DomainSummary[]>([]);
  const [domainId, setDomainId] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeData | null>(null);
  const [selected, setSelected] = useState<NodeDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const fail = useCallback(
    (e: unknown) => setMessage(e instanceof Error ? e.message : t("Action failed")),
    [t],
  );

  const loadDomains = useCallback(async () => {
    const list = await listDomains();
    setDomains(list);
    setDomainId((cur) => (cur && list.some((d) => d.id === cur) ? cur : list[0]?.id ?? null));
  }, []);

  useEffect(() => {
    void Promise.all([getRights().then(setRights), loadDomains()]).catch(fail);
  }, [loadDomains, fail]);

  useEffect(() => {
    setSelected(null);
    if (!domainId) {
      setTree(null);
      return;
    }
    getTree(domainId).then(setTree).catch(fail);
  }, [domainId, fail]);

  async function refreshTree() {
    await loadDomains();
    if (domainId) setTree(await getTree(domainId));
  }

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    setMessage("");
    try {
      await fn();
      setMessage(done);
      setDraft(null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  if (!rights)
    return (
      <Centered>
        {message ? <p className="text-xs text-[var(--muted-foreground)]">{message}</p> : <Loader2 className="h-4 w-4 animate-spin" />}
      </Centered>
    );

  return (
    <div className="h-full overflow-y-auto px-4 py-6 sm:px-8">
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-serif text-xl font-semibold">{t("Knowledge Graph")}</h1>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {rights.can_edit
                ? t("You curate the main graph: build it from documents and link it to a GraphRAG knowledge base.")
                : t("Read-only: only R&D changes the main graph. Its edits are listed under Edit history.")}
            </p>
          </div>
        </div>
        {message && (
          <p role="status" className="mb-3 text-xs text-[var(--muted-foreground)]">
            {message}
          </p>
        )}

        {(
          <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
            <aside className="space-y-2">
              {domains.length === 0 && (
                <p className="text-xs text-[var(--muted-foreground)]">{t("No domains yet.")}</p>
              )}
              {domains.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => setDomainId(d.id)}
                  className={`w-full rounded-xl border px-3 py-2 text-left text-sm ${d.id === domainId ? "border-[var(--primary)]" : "border-[var(--border)]"}`}
                >
                  <div className="font-medium">{d.name}</div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">
                    {t("{{n}} nodes", { n: d.nodes })}
                  </div>
                </button>
              ))}
              {rights.can_edit && (
                <ImportBox
                  busy={busy}
                  onImport={(file, name) =>
                    run(async () => {
                      const r = await importFile(file, null, name);
                      await loadDomains();
                      setDomainId(r.domain_id);
                    }, t("Imported."))
                  }
                  label={t("New domain from file")}
                />
              )}
              {rights.can_edit && (
                <button type="button" className={BTN} onClick={() => setDraft({ kind: "domain" })}>
                  {t("New empty domain")}
                </button>
              )}
            </aside>

            <section className="min-w-0 rounded-2xl border border-[var(--border)] p-4">
              {!tree ? (
                <p className="text-sm text-[var(--muted-foreground)]">{t("Pick a domain.")}</p>
              ) : (
                <div className="grid gap-4 md:grid-cols-[1fr_1fr]">
                  <div>
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <h2 className="text-sm font-semibold">{tree.domain.name}</h2>
                      <div className="flex flex-wrap gap-2">
                        {rights.can_edit && (
                          <>
                            <label className={`${BTN} cursor-pointer`}>
                              {t("Add file")}
                              <input
                                type="file"
                                hidden
                                accept=".pdf,.docx,.md,.markdown,.txt"
                                onChange={(e) => {
                                  const f = e.target.files?.[0];
                                  e.target.value = "";
                                  if (f)
                                    void run(async () => {
                                      await importFile(f, tree.domain.id);
                                      await refreshTree();
                                    }, t("Imported."));
                                }}
                              />
                            </label>
                            <button
                              type="button"
                              className={BTN}
                              onClick={async () => {
                                if (!(await confirmAction(`${t("Delete domain")}: ${tree.domain.name}?`, { tone: "danger" }))) return;
                                void run(async () => {
                                  await deleteDomain(tree.domain.id);
                                  await loadDomains();
                                }, t("Domain deleted."));
                              }}
                            >
                              {t("Delete domain")}
                            </button>
                          </>
                        )}
                        {rights.can_edit && (
                          <button type="button" className={BTN} onClick={() => setDraft({ kind: "add", parent: null })}>
                            {t("Add topic")}
                          </button>
                        )}
                      </div>
                    </div>
                    {(rights.can_edit || rights.can_view) && (
                      <StorePanel
                        key={`store-${tree.domain.id}`}
                        canEdit={rights.can_edit}
                        domainId={tree.domain.id}
                        domainName={tree.domain.name}
                        onError={fail}
                        onImported={() => void loadDomains()}
                      />
                    )}
                    {rights.can_edit && (
                      <KbPanel
                        key={tree.domain.id}
                        domainId={tree.domain.id}
                        current={tree.domain.kb_name}
                        onChanged={() => void refreshTree().catch(fail)}
                        onError={fail}
                      />
                    )}
                    {tree.nodes.length === 0 ? (
                      <p className="text-xs text-[var(--muted-foreground)]">{t("This domain is empty.")}</p>
                    ) : (
                      <ul className="space-y-0.5">
                        {tree.nodes.map((n) => (
                          <NodeRow key={n.id} node={n} depth={0} selectedId={selected?.id} onSelect={(id) => getNode(id).then(setSelected).catch(fail)} />
                        ))}
                      </ul>
                    )}
                    {tree.sources.length > 0 && (
                      <p className="mt-3 text-[11px] text-[var(--muted-foreground)]">
                        {t("Sources")}: {tree.sources.map((s) => s.filename).join(", ")}
                      </p>
                    )}
                  </div>
                  <div>
                    {selected ? (
                      <NodePanel
                        node={selected}
                        rights={rights}
                        onAdd={() => setDraft({ kind: "add", parent: findNode(tree.nodes, selected.id) })}
                        onEdit={() => setDraft({ kind: "edit", node: selected })}
                        onDelete={() => setDraft({ kind: "delete", node: selected })}
                        onGraphChanged={() => void getNode(selected.id).then(setSelected).catch(fail)}
                        onError={fail}
                      />
                    ) : (
                      <p className="text-sm text-[var(--muted-foreground)]">{t("Select a node to read it.")}</p>
                    )}
                  </div>
                </div>
              )}
            </section>
          </div>
        )}

        {draft && (
          <DraftDialog
            draft={draft}
            busy={busy}
            onClose={() => setDraft(null)}
            onSubmit={(v) =>
              run(async () => {
                await submitDraft(draft, v, tree?.domain.id ?? null);
                await refreshTree();
                setSelected(null);
              }, t("Saved."))
            }
          />
        )}
      </div>
    </div>
  );
}

async function submitDraft(
  draft: Draft,
  v: { title: string; summary: string },
  domainId: string | null,
) {
  if (draft.kind === "domain") await createDomain(v.title, v.summary);
  else if (draft.kind === "add" && domainId)
    await addNode(domainId, draft.parent?.id ?? null, v.title, v.summary);
  else if (draft.kind === "edit") await patchNode(draft.node.id, { title: v.title, summary: v.summary });
  else if (draft.kind === "delete") await deleteNode(draft.node.id);
}

function findNode(nodes: TreeNode[], id: string): TreeNode | null {
  for (const n of nodes) {
    if (n.id === id) return n;
    const hit = findNode(n.children, id);
    if (hit) return hit;
  }
  return null;
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="grid h-full place-items-center">{children}</div>;
}

function NodeRow({
  node, depth, selectedId, onSelect,
}: { node: TreeNode; depth: number; selectedId?: string; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(depth === 0);
  const has = node.children.length > 0;
  return (
    <li>
      <div className="flex items-center gap-1" style={{ paddingLeft: depth * 14 }}>
        <button type="button" className="w-4 text-[var(--muted-foreground)]" onClick={() => setOpen(!open)} aria-label={node.title}>
          {has ? open ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : null}
        </button>
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          className={`min-w-0 flex-1 truncate rounded px-1.5 py-0.5 text-left text-[13px] ${node.id === selectedId ? "bg-[var(--muted)] font-medium" : ""} ${depth === 0 ? "font-semibold" : ""}`}
        >
          {node.title}
        </button>
      </div>
      {has && open && (
        <ul>
          {node.children.map((c) => (
            <NodeRow key={c.id} node={c} depth={depth + 1} selectedId={selectedId} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </li>
  );
}

function NodePanel({
  node, rights, onAdd, onEdit, onDelete, onGraphChanged, onError,
}: {
  node: NodeDetail; rights: Rights; onAdd: () => void; onEdit: () => void; onDelete: () => void;
  onGraphChanged: () => void; onError: (e: unknown) => void;
}) {
  const { t } = useTranslation();
  const can = rights.can_edit;
  return (
    <div>
      <h3 className="text-sm font-semibold">{node.title}</h3>
      {node.summary && <p className="mt-2 whitespace-pre-wrap text-[13px]">{node.summary}</p>}
      <NodeGraphPanel
        key={`${node.id}-${node.version}`}
        node={node}
        canEdit={can}
        onChanged={onGraphChanged}
        onError={onError}
      />
      {node.excerpts.map((ex) => (
        <details key={ex.source_id} className="mt-3 text-[12px]">
          <summary className="cursor-pointer text-[var(--muted-foreground)]">{ex.filename}</summary>
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-[var(--muted)] p-2">{ex.text}</pre>
        </details>
      ))}
      {can && (
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={BTN} onClick={onAdd}>{t("Add child")}</button>
          <button type="button" className={BTN} onClick={onEdit}>{t("Edit")}</button>
          <button type="button" className={BTN} onClick={onDelete}>{t("Delete")}</button>
        </div>
      )}
    </div>
  );
}

function ImportBox({
  onImport, busy, label,
}: { onImport: (file: File, name: string) => void; busy: boolean; label: string }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  return (
    <div className="space-y-1.5 rounded-xl border border-dashed border-[var(--border)] p-2">
      <input className={FIELD} value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Domain name (optional)")} />
      <label className={`${BTN} block cursor-pointer text-center`}>
        {busy ? t("Working…") : label}
        <input
          type="file"
          hidden
          accept=".pdf,.docx,.md,.markdown,.txt"
          disabled={busy}
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) onImport(f, name);
          }}
        />
      </label>
      <p className="text-[10px] text-[var(--muted-foreground)]">{t("PDF, Word (.docx), Markdown or text. Headings become topics and nodes.")}</p>
    </div>
  );
}

function DraftDialog({
  draft, busy, onClose, onSubmit,
}: {
  draft: Draft;
  busy: boolean;
  onClose: () => void;
  onSubmit: (v: { title: string; summary: string }) => void;
}) {
  const { t } = useTranslation();
  const initial = draft.kind === "edit" ? draft.node : null;
  const [title, setTitle] = useState(initial?.title ?? "");
  const [summary, setSummary] = useState(initial?.summary ?? "");
  const deleting = draft.kind === "delete";
  const ok = deleting || title.trim();
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-md space-y-3 rounded-2xl bg-[var(--background)] p-5">
        <h3 className="text-sm font-semibold">
          {deleting ? t("Delete this node and everything below it?") : draft.kind === "edit" ? t("Edit node") : draft.kind === "domain" ? t("Domain") : t("New node")}
        </h3>
        {deleting ? (
          <p className="text-[13px]">{draft.node.title}</p>
        ) : (
          <>
            <input className={FIELD} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("Title")} />
            <textarea className={FIELD} rows={4} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder={t("Summary")} />
          </>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className={BTN} onClick={onClose}>{t("Cancel")}</button>
          <button type="button" className={BTN} disabled={busy || !ok} onClick={() => onSubmit({ title, summary })}>
            {t("Save")}
          </button>
        </div>
      </div>
    </div>
  );
}

function KbPanel({
  domainId, current, onChanged, onError,
}: { domainId: string; current: string | null; onChanged: () => void; onError: (e: unknown) => void }) {
  const { t } = useTranslation();
  const [options, setOptions] = useState<KbOption[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);

  useEffect(() => {
    listKbOptions().then(setOptions).catch(onError);
  }, [onError]);

  async function link(kb: string) {
    setBusy(true);
    try {
      await patchDomain(domainId, { kb_name: kb || null });
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  async function ask() {
    setBusy(true);
    setResult(null);
    try {
      setResult(await askKnowledge(q, domainId));
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-3 space-y-2 rounded-xl border border-[var(--border)] p-3 text-[12px]">
      <label className="block font-medium" htmlFor="kt-kb">{t("GraphRAG knowledge base")}</label>
      <select
        id="kt-kb"
        className={FIELD}
        disabled={busy}
        value={current ?? ""}
        onChange={(e) => void link(e.target.value)}
      >
        <option value="">{t("None")}</option>
        {options.map((o) => (
          <option key={o.ref} value={o.ref}>{o.name} — {o.owner}</option>
        ))}
      </select>
      <p className="text-[11px] text-[var(--muted-foreground)]">
        {t("Linking publishes this knowledge base to every user through the tutor.")}
      </p>
      <div className="flex gap-2">
        <input className={FIELD} value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Try a question")} />
        <button type="button" className={BTN} disabled={busy || !q.trim()} onClick={() => void ask()}>
          {t("Ask")}
        </button>
      </div>
      {result && (
        <div>
          {result.notes.map((n) => (
            <p key={n} className="text-[var(--muted-foreground)]">{n}</p>
          ))}
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-[var(--muted)] p-2">{result.content}</pre>
        </div>
      )}
    </div>
  );
}


function fmtSize(n: number) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

function StorePanel({
  domainId,
  domainName,
  onError,
  onImported,
  canEdit,
}: {
  domainId: string;
  domainName: string;
  onError: (e: unknown) => void;
  onImported: () => void;
  /** False for administrators: they read and download, R&D changes. */
  canEdit: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [docs, setDocs] = useState<StoreDocument[]>([]);
  const [log, setLog] = useState<AuditEntry[] | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setDocs(await listDocuments(domainId));
    setLog(null);
  }, [domainId]);

  useEffect(() => {
    if (open) void reload().catch(onError);
  }, [open, reload, onError]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-3 rounded-xl border border-[var(--border)] p-3">
      <button type="button" className="text-[12px] font-medium" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {t("Document store")}
      </button>
      {open && (
        <div className="mt-2 space-y-2 text-[12px]">
          <div className="flex flex-wrap gap-2">
            {canEdit && (
            <label className={`${BTN} cursor-pointer`}>
              {t("Upload document")}
              <input
                type="file"
                hidden
                accept=".pdf,.docx,.md,.markdown,.txt"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) void act(async () => { await uploadDocument(domainId, f); await reload(); });
                }}
              />
            </label>
            )}
            <button type="button" className={BTN} disabled={busy} onClick={() => void act(() => exportTree(domainId, domainName))}>
              {t("Export graph")}
            </button>
            {canEdit && (
            <label className={`${BTN} cursor-pointer`}>
              {t("Import graph")}
              <input
                type="file"
                hidden
                accept=".json,application/json"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) void act(async () => { await importTree(f); onImported(); });
                }}
              />
            </label>
            )}
            <button type="button" className={BTN} disabled={busy} onClick={() => void act(async () => setLog(await listAudit()))}>
              {t("Activity log")}
            </button>
          </div>
          {docs.length === 0 ? (
            <p className="text-[var(--muted-foreground)]">{t("No documents yet.")}</p>
          ) : (
            <ul className="space-y-1">
              {docs.map((d) => (
                <li key={d.id} className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate">{d.filename} <span className="text-[var(--muted-foreground)]">· {fmtSize(d.size)}</span></span>
                  <span className="flex shrink-0 gap-1">
                    <button type="button" className={BTN} disabled={busy} onClick={() => void act(() => downloadDocument(d))}>{t("Download")}</button>
                    {canEdit && (
                    <button
                      type="button"
                      className={BTN}
                      disabled={busy}
                      onClick={async () => {
                        if (!(await confirmAction(`${t("Delete")}: ${d.filename}?`, { tone: "danger" }))) return;
                        void act(async () => { await deleteDocument(d.id); await reload(); });
                      }}
                    >
                      {t("Delete")}
                    </button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {log && (
            <ul className="max-h-48 space-y-0.5 overflow-auto text-[11px] text-[var(--muted-foreground)]">
              {log.length === 0 && <li>{t("No activity yet.")}</li>}
              {log.map((l, i) => (
                <li key={l.id ?? i}>
                  {l.created_at?.replace("T", " ").slice(0, 16)} · {l.action} · {String(l.data?.filename ?? l.data?.name ?? l.target_id)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
