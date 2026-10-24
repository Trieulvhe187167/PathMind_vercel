"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { CheckCircle2, ExternalLink, Flag, Loader2, ShieldCheck, Trash2 } from "lucide-react";

import { CommunityShell } from "@/components/community/CommunityShell";
import { useTimeAgo } from "@/components/community/community-ui";
import { useReportReasonLabel } from "@/components/community/ReportDialog";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { listReports, resolveReports, type ReportGroup } from "@/lib/community-api";
import { confirmAction } from "@/lib/confirm";
import { notify } from "@/lib/notifications";

type Tab = "open" | "resolved";

export default function CommunityModerationPage() {
  const { t } = useTranslation();
  const timeAgo = useTimeAgo();
  const reasonLabel = useReportReasonLabel();
  const { isAdmin, loading: authLoading } = useAuthStatus();
  const [tab, setTab] = useState<Tab>("open");
  const [items, setItems] = useState<ReportGroup[] | null>(null);
  const [loadedFor, setLoadedFor] = useState("");
  const [reload, setReload] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    const key = `${tab}:${reload}`;
    void (async () => {
      try {
        const res = await listReports(tab);
        if (cancelled) return;
        setItems(res.items);
        setError("");
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : t("Could not load reports"));
      } finally {
        if (!cancelled) setLoadedFor(key);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isAdmin, reload, t, tab]);

  const loading = loadedFor !== `${tab}:${reload}`;

  async function resolve(group: ReportGroup, action: "dismiss" | "remove") {
    if (action === "remove") {
      const ok = await confirmAction(t("Remove this content for everyone? The author will be notified."), {
        tone: "danger",
        confirmLabel: t("Remove"),
      });
      if (!ok) return;
    }
    setBusy(`${group.target_type}:${group.target_id}`);
    try {
      await resolveReports({ target_type: group.target_type, target_id: group.target_id, action });
      notify(action === "remove" ? t("Content removed.") : t("Reports dismissed."), { tone: "success" });
      setItems((prev) => prev?.filter((g) => g !== group) ?? prev);
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    } finally {
      setBusy("");
    }
  }

  const typeLabel = (type: string) =>
    type === "post" ? t("Post") : type === "comment" ? t("Comment") : t("Skill");

  const href = (g: ReportGroup) =>
    g.skill_slug
      ? `/community/skills/${encodeURIComponent(g.skill_slug)}`
      : g.post_id
        ? `/community/posts/${g.post_id}`
        : null;

  if (!authLoading && !isAdmin) {
    return (
      <CommunityShell>
        <p className="text-sm text-[var(--muted-foreground)]">{t("Only administrators can do this.")}</p>
      </CommunityShell>
    );
  }

  return (
    <CommunityShell>
      <div className="mx-auto max-w-4xl">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-[15px] font-semibold text-[var(--foreground)]">
            <ShieldCheck size={16} className="text-[var(--primary)]" /> {t("Reported content")}
          </h2>
          <div className="flex rounded-lg bg-[var(--muted)] p-1 text-[12.5px]">
            {(["open", "resolved"] as Tab[]).map((id) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`rounded-md px-3 py-1 ${
                  tab === id ? "bg-[var(--card)] font-medium text-[var(--foreground)] shadow-sm" : "text-[var(--muted-foreground)]"
                }`}
              >
                {id === "open" ? t("Open") : t("Resolved")}
              </button>
            ))}
          </div>
        </div>

        {error && <p className="mb-3 text-sm text-destructive">{error}</p>}

        {loading && !items ? (
          <div className="flex min-h-[200px] items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
          </div>
        ) : items && items.length === 0 ? (
          <div className="flex min-h-[200px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--border)] text-center">
            <CheckCircle2 className="text-emerald-500" />
            <p className="text-sm text-[var(--muted-foreground)]">
              {tab === "open" ? t("Nothing to review. All clear!") : t("No resolved reports yet.")}
            </p>
          </div>
        ) : (
          <ul className={`space-y-3 ${loading ? "opacity-60" : ""}`}>
            {items?.map((g) => {
              const link = href(g);
              const key = `${g.target_type}:${g.target_id}`;
              return (
                <li key={key} className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
                  <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-[var(--muted-foreground)]">
                    <span className="rounded-md bg-[var(--muted)] px-1.5 py-0.5 font-medium text-[var(--foreground)]">
                      {typeLabel(g.target_type)}
                    </span>
                    <span className="inline-flex items-center gap-1 font-medium text-destructive">
                      <Flag size={11} /> {t("{{count}} reports", { count: g.count })}
                    </span>
                    {g.author && <span>· {t("by {{name}}", { name: g.author })}</span>}
                    {g.status !== "open" && (
                      <span>
                        ·{" "}
                        {g.status === "removed"
                          ? t("Removed by {{name}}", { name: g.resolved_by })
                          : t("Dismissed by {{name}}", { name: g.resolved_by })}{" "}
                        {timeAgo(g.resolved_at)}
                      </span>
                    )}
                  </div>
                  {g.missing ? (
                    <p className="mt-2 text-[13px] italic text-[var(--muted-foreground)]">
                      {t("This content no longer exists.")}
                    </p>
                  ) : (
                    <>
                      {g.title && (
                        <div className="mt-1.5 text-[14px] font-semibold text-[var(--foreground)]">{g.title}</div>
                      )}
                      {g.excerpt && (
                        <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-[12.5px] text-[var(--muted-foreground)]">
                          {g.excerpt}
                        </p>
                      )}
                    </>
                  )}
                  <ul className="mt-3 space-y-1 border-t border-[var(--border)] pt-2">
                    {g.reports.map((r, i) => (
                      <li key={i} className="text-[12px] text-[var(--muted-foreground)]">
                        <span className="font-medium text-[var(--foreground)]">{reasonLabel(r.reason)}</span>
                        {r.details && <> — {r.details}</>}
                        <span className="ml-1">
                          ({r.reporter}, {timeAgo(r.created_at)})
                        </span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {link && !g.removed && (
                      <Link
                        href={link}
                        className="inline-flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1 text-[12px] text-[var(--foreground)] hover:bg-[var(--muted)]"
                      >
                        <ExternalLink size={12} /> {t("View")}
                      </Link>
                    )}
                    {tab === "open" && (
                      <>
                        <button
                          disabled={busy === key}
                          onClick={() => void resolve(g, "dismiss")}
                          className="inline-flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1 text-[12px] text-[var(--foreground)] hover:bg-[var(--muted)] disabled:opacity-50"
                        >
                          <CheckCircle2 size={12} /> {t("Keep (dismiss)")}
                        </button>
                        <button
                          disabled={busy === key}
                          onClick={() => void resolve(g, "remove")}
                          className="inline-flex items-center gap-1 rounded-lg bg-destructive px-2.5 py-1 text-[12px] font-medium text-white hover:opacity-90 disabled:opacity-50"
                        >
                          <Trash2 size={12} /> {t("Remove")}
                        </button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {tab === "open" && items && items.length > 0 && (
          <div className="mt-4 text-center">
            <button onClick={() => setReload((n) => n + 1)} className="text-[12.5px] text-[var(--primary)] hover:underline">
              {t("Refresh")}
            </button>
          </div>
        )}
      </div>
    </CommunityShell>
  );
}
