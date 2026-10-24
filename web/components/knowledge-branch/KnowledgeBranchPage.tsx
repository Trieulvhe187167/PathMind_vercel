"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  addBranchNode,
  deleteBranchNode,
  enrichBranch,
  getMasteryPath,
  setMastered,
  setPrerequisites,
  type MasteryPath,
  resetBranch,
  getBranch,
  listBranchVersions,
  patchBranchNode,
  restoreBranchVersion,
  saveBranchVersion,
  type BranchData,
  type BranchNode,
  type BranchVersionInfo,
} from "@/lib/knowledge-branch-api";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { confirmAction } from "@/lib/confirm";
import {
  getMyRewards,
  listMyRecommendations,
  redeemPoints,
  submitRecommendation,
  type MyRewards,
  type Recommendation,
} from "@/lib/staff-api";

const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px]";

type Edit =
  | { kind: "add"; parent: BranchNode | null }
  | { kind: "edit"; node: BranchNode }
  | { kind: "recommend"; node: BranchNode }
  | { kind: "prereq"; node: BranchNode }
  | null;

export default function KnowledgeBranchPage() {
  // Only Educators recommend branches to R&D; everyone else just studies theirs.
  const canRecommend = useAuthStatus().isEducator;
  const { t } = useTranslation();
  const [branch, setBranch] = useState<BranchData | null>(null);
  const [versions, setVersions] = useState<BranchVersionInfo[]>([]);
  const [edit, setEdit] = useState<Edit>(null);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [reqs, setReqs] = useState<Set<string>>(new Set());
  const [pathVersion, setPathVersion] = useState(0);

  const fail = useCallback(
    (e: unknown) => setMessage(e instanceof Error ? e.message : t("Action failed")),
    [t],
  );

  const load = useCallback(async () => {
    try {
      const [data, vers] = await Promise.all([getBranch(), listBranchVersions()]);
      setBranch(data);
      setVersions(vers);
    } catch (e) {
      fail(e);
    }
  }, [fail]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      setEdit(null);
      await load();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  function open(next: Edit) {
    setEdit(next);
    if (next?.kind === "prereq") setReqs(new Set(next.node.prerequisites));
    setTitle(next?.kind === "edit" || next?.kind === "recommend" ? next.node.title : "");
    setSummary(next?.kind === "edit" ? next.node.summary : "");
  }

  function submit() {
    if (!edit || (edit.kind !== "prereq" && !title.trim())) return;
    if (edit.kind === "prereq") {
      const node = edit.node;
      void run(async () => {
        await setPrerequisites(node.id, [...reqs]);
        setPathVersion((v) => v + 1);
      });
      return;
    }
    if (edit.kind === "recommend") {
      const node = edit.node;
      void run(async () => {
        await submitRecommendation(node.id, title.trim(), summary);
        setMessage(t("Sent to R&D. You will earn points if it is merged."));
      });
      return;
    }
    void run(() =>
      edit.kind === "add"
        ? addBranchNode(edit.parent?.id ?? null, title.trim(), summary, branch?.version)
        : patchBranchNode(edit.node.id, { title: title.trim(), summary }, branch?.version),
    );
  }

  async function remove(node: BranchNode) {
    if (!(await confirmAction(t("Delete this node and everything below it?")))) return;
    void run(() => deleteBranchNode(node.id, branch?.version));
  }

  async function restore(v: BranchVersionInfo) {
    if (!(await confirmAction(t("Replace your branch with this saved version?")))) return;
    void run(() => restoreBranchVersion(v.id));
  }

  if (!branch) {
    return (
      <div className="flex h-full items-center justify-center">
        {message ? <p className="text-sm">{message}</p> : <Loader2 className="animate-spin" size={18} />}
      </div>
    );
  }

  return (
    <div className="mx-auto h-full max-w-4xl overflow-y-auto px-6 py-8">
      <h1 className="text-lg font-semibold">{t("My Knowledge")}</h1>
      <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">
        {t(
          "Your own branch of knowledge, private to you. The tutor uses it next to the main knowledge tree; if they disagree, the main tree wins.",
        )}
      </p>
      {message && (
        <p role="alert" className="mt-3 text-[13px] text-[var(--destructive)]">
          {message}
        </p>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <button className={BTN} disabled={busy} onClick={() => open({ kind: "add", parent: null })}>
          {t("Add top-level node")}
        </button>
        <button
          className={BTN}
          disabled={busy}
          onClick={() => void run(() => saveBranchVersion(""))}
        >
          {t("Save a version")}
        </button>
        <label className={`${BTN} cursor-pointer`}>
          {t("Add document")}
          <input
            type="file"
            hidden
            accept=".pdf,.docx,.md,.markdown,.txt"
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void run(() => enrichBranch(f));
            }}
          />
        </label>
        <button
          className={BTN}
          disabled={busy || !branch?.node_count}
          onClick={async () => {
            if (!(await confirmAction(t("Reset your personal knowledge? A restorable version is kept."), { tone: "danger" }))) return;
            void run(() => resetBranch());
          }}
        >
          {t("Reset")}
        </button>
      </div>

      {edit && (
        <div className="mt-4 space-y-2 rounded-lg border border-[var(--border)] p-3">
          <p className="text-[12px] font-medium">
            {edit.kind === "add"
              ? edit.parent
                ? t("New node under “{{title}}”", { title: edit.parent.title })
                : t("New top-level node")
              : edit.kind === "recommend"
                ? t("Recommend “{{title}}” and its documents to R&D", { title: edit.node.title })
                : edit.kind === "prereq"
                  ? t("What must be mastered before “{{title}}”?", { title: edit.node.title })
                  : t("Edit node")}
          </p>
          {edit.kind === "prereq" ? (
            <ul className="max-h-60 space-y-0.5 overflow-auto text-[13px]">
              {flatNodes(branch.nodes)
                .filter((n) => n.id !== edit.node.id)
                .map((n) => (
                  <li key={n.id}>
                    <label className="flex items-center gap-2 py-0.5" style={{ paddingLeft: n.depth * 14 }}>
                      <input
                        type="checkbox"
                        checked={reqs.has(n.id)}
                        onChange={() => {
                          const next = new Set(reqs);
                          if (next.has(n.id)) next.delete(n.id);
                          else next.add(n.id);
                          setReqs(next);
                        }}
                      />
                      {n.title}
                    </label>
                  </li>
                ))}
            </ul>
          ) : (
            <>
              <input
                className={FIELD}
                value={title}
                maxLength={200}
                placeholder={t("Title")}
                onChange={(e) => setTitle(e.target.value)}
              />
              <textarea
                className={FIELD}
                rows={4}
                value={summary}
                maxLength={4000}
                placeholder={t("Summary")}
                onChange={(e) => setSummary(e.target.value)}
              />
            </>
          )}
          <div className="flex gap-2">
            <button className={BTN} disabled={busy || (edit.kind !== "prereq" && !title.trim())} onClick={submit}>
              {t("Save")}
            </button>
            <button className={BTN} disabled={busy} onClick={() => setEdit(null)}>
              {t("Cancel")}
            </button>
          </div>
        </div>
      )}

      <div className="mt-5">
        {branch.nodes.length === 0 ? (
          <p className="text-[13px] text-[var(--muted-foreground)]">
            {t("Your branch is empty. Add a node to start.")}
          </p>
        ) : (
          <ul className="space-y-1">
            {branch.nodes.map((n) => (
              <NodeRow key={n.id} node={n} depth={0} onAdd={(p) => open({ kind: "add", parent: p })} onEdit={(x) => open({ kind: "edit", node: x })} onDelete={(x) => void remove(x)} onRecommend={canRecommend ? (x) => open({ kind: "recommend", node: x }) : undefined} onPrereq={(x) => open({ kind: "prereq", node: x })} />
            ))}
          </ul>
        )}
      </div>

      <MasteryPanel refreshKey={pathVersion + (branch?.version ?? 0)} />

      <RewardsPanel showRecommendations={canRecommend} />

      {versions.length > 0 && (
        <div className="mt-8">
          <h2 className="text-[13px] font-medium">{t("Saved versions")}</h2>
          <ul className="mt-2 space-y-1">
            {versions.map((v) => (
              <li key={v.id} className="flex items-center justify-between text-[12px]">
                <span>
                  {v.label} · {t("{{count}} nodes", { count: v.node_count })}
                </span>
                <button className={BTN} disabled={busy} onClick={() => void restore(v)}>
                  {t("Restore")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function NodeRow({
  node,
  depth,
  onAdd,
  onEdit,
  onDelete,
  onRecommend,
  onPrereq,
}: {
  node: BranchNode;
  depth: number;
  onRecommend?: (node: BranchNode) => void;
  onPrereq: (node: BranchNode) => void;
  onAdd: (parent: BranchNode) => void;
  onEdit: (node: BranchNode) => void;
  onDelete: (node: BranchNode) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const hasKids = node.children.length > 0;
  return (
    <li>
      <div
        className="flex items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-[var(--muted)]"
        style={{ marginLeft: depth * 16 }}
      >
        <button
          type="button"
          aria-label={open ? t("Collapse") : t("Expand")}
          className="mt-0.5 w-4 text-[var(--muted-foreground)]"
          onClick={() => setOpen((v) => !v)}
        >
          {hasKids ? open ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : null}
        </button>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium">{node.title}</p>
          {node.summary && (
            <p className="line-clamp-2 text-[12px] text-[var(--muted-foreground)]">{node.summary}</p>
          )}
        </div>
        <div className="flex shrink-0 gap-1 text-[11px]">
          <button className="underline" onClick={() => onAdd(node)}>{t("Add child")}</button>
          <button className="underline" onClick={() => onEdit(node)}>{t("Edit")}</button>
          <button className="underline" onClick={() => onDelete(node)}>{t("Delete")}</button>
          <button className="underline" onClick={() => onPrereq(node)}>
            {t("Prerequisites")}{node.prerequisites.length ? ` (${node.prerequisites.length})` : ""}
          </button>
          {depth === 0 && onRecommend && (
            <button className="underline" onClick={() => onRecommend(node)}>{t("Recommend to R&D")}</button>
          )}
        </div>
      </div>
      {hasKids && open && (
        <ul className="space-y-1">
          {node.children.map((c) => (
            <NodeRow key={c.id} node={c} depth={depth + 1} onAdd={onAdd} onEdit={onEdit} onDelete={onDelete} onRecommend={onRecommend} onPrereq={onPrereq} />
          ))}
        </ul>
      )}
    </li>
  );
}


function RewardsPanel({ showRecommendations }: { showRecommendations: boolean }) {
  const { t } = useTranslation();
  const [rw, setRw] = useState<MyRewards | null>(null);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [points, setPoints] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [r, l] = await Promise.all([
      getMyRewards(),
      showRecommendations ? listMyRecommendations() : Promise.resolve([] as Recommendation[]),
    ]);
    setRw(r);
    setRecs(l);
  }, [showRecommendations]);
  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  async function redeem(kind: "plan_days" | "ai_credits") {
    const n = Number(points);
    if (!n || n <= 0) return;
    setBusy(true);
    setMsg("");
    try {
      const r = await redeemPoints(kind, n);
      setMsg(
        kind === "plan_days"
          ? t("Added {{n}} plan days.", { n: String(r.days) })
          : t("Added {{n}} AI credits.", { n: Number(r.credits).toLocaleString() }),
      );
      setPoints("");
      await load();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!rw) return null;
  const perDay = rw.rules["rate.points_per_plan_day"];
  const perPoint = rw.rules["rate.credits_per_point"];
  return (
    <div className="mt-8 rounded-xl border border-[var(--border)] p-4">
      <h2 className="text-[13px] font-medium">
        {t("Reward points")}: <span className="font-semibold">{rw.balance}</span>
      </h2>
      <p className="mt-1 text-[12px] text-[var(--muted-foreground)]">
        {t("{{a}} points = 1 plan day · 1 point = {{b}} AI credits. Points are never paid out as cash.", { a: String(perDay), b: String(perPoint) })}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          type="number"
          min={1}
          className={`${FIELD} w-28`}
          placeholder={t("Points")}
          value={points}
          onChange={(e) => setPoints(e.target.value)}
        />
        <button className={BTN} disabled={busy || !Number(points)} onClick={() => void redeem("plan_days")}>
          {t("Redeem for plan days")}
        </button>
        <button className={BTN} disabled={busy || !Number(points)} onClick={() => void redeem("ai_credits")}>
          {t("Redeem for AI credits")}
        </button>
      </div>
      {msg && <p role="status" className="mt-2 text-[12px]">{msg}</p>}
      {recs.length > 0 && (
        <ul className="mt-3 space-y-1 text-[12px]">
          {recs.map((r) => (
            <li key={r.id} className="flex justify-between gap-2">
              <span className="truncate">{r.title}</span>
              <span className="text-[var(--muted-foreground)]">
                {r.status === "pending" ? t("Waiting") : r.status === "merged" ? t("Merged") : `${t("Rejected")}${r.review_note ? ` – ${r.review_note}` : ""}`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function flatNodes(nodes: BranchNode[], depth = 0): (BranchNode & { depth: number })[] {
  return nodes.flatMap((n) => [{ ...n, depth }, ...flatNodes(n.children, depth + 1)]);
}

function MasteryPanel({ refreshKey }: { refreshKey: number }) {
  const { t } = useTranslation();
  const [path, setPath] = useState<MasteryPath | null>(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    getMasteryPath().then(setPath).catch((e: Error) => setMsg(e.message));
  }, []);
  useEffect(load, [load, refreshKey]);

  if (!path || path.total === 0) return null;
  const dot = { mastered: "bg-emerald-500", ready: "bg-sky-500", locked: "bg-[var(--muted-foreground)]/40" } as const;
  return (
    <div className="mt-8 rounded-xl border border-[var(--border)] p-4">
      <h2 className="text-[13px] font-medium">
        {t("Mastery path")}: {path.mastered}/{path.total}
      </h2>
      <p className="mt-1 text-[12px] text-[var(--muted-foreground)]">
        {t("Study in this order. A node unlocks when everything it requires is mastered.")}
      </p>
      {msg && <p role="alert" className="mt-1 text-[12px] text-[var(--destructive)]">{msg}</p>}
      <ol className="mt-2 space-y-1">
        {path.items.map((i, idx) => (
          <li key={i.id} className={`flex items-start gap-2 text-[13px] ${i.status === "locked" ? "opacity-60" : ""}`}>
            <span className="mt-1.5 flex items-center gap-1.5">
              <span className="w-5 text-right text-[11px] text-[var(--muted-foreground)]">{idx + 1}</span>
              <span className={`h-2 w-2 rounded-full ${dot[i.status]}`} />
            </span>
            <span className="min-w-0 flex-1">
              <span className={i.id === path.next ? "font-semibold" : ""}>{i.title}</span>
              {i.id === path.next && <span className="ml-2 text-[11px] text-sky-600">{t("Next")}</span>}
              {i.blocked_by.length > 0 && (
                <span className="block text-[11px] text-[var(--muted-foreground)]">
                  {t("Needs")}: {i.blocked_by.map((b) => b.title).join(", ")}
                </span>
              )}
            </span>
            <label className="flex items-center gap-1 text-[12px]">
              <input
                type="checkbox"
                checked={i.status === "mastered"}
                disabled={i.status === "locked"}
                onChange={(e) => {
                  setMsg("");
                  setMastered(i.id, e.target.checked).then(load).catch((er: Error) => setMsg(er.message));
                }}
              />
              {t("Mastered")}
            </label>
          </li>
        ))}
      </ol>
    </div>
  );
}
