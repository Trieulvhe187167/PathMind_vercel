"use client";

import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ArrowBigDown, ArrowBigUp, X } from "lucide-react";

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

export function useCommunityLabels() {
  const { t } = useTranslation();
  const skillCategories: Record<string, string> = {
    academics: t("Academics"),
    languages: t("Languages & Literature"),
    "skills-interests": t("Skills & Interests"),
    companions: t("Companions"),
    educators: t("For Educators"),
    productivity: t("Productivity"),
    other: t("Other"),
  };
  const forumCategories: Record<string, string> = {
    general: t("General"),
    help: t("Q&A / Help"),
    skills: t("Skill showcase"),
    study: t("Study tips"),
    feedback: t("Feedback"),
    announcements: t("Announcements"),
  };
  return {
    skillCategory: (id: string) => skillCategories[id] ?? id,
    forumCategory: (id: string) => forumCategories[id] ?? id,
  };
}

export const FORUM_CATEGORY_TONE: Record<string, string> = {
  general: "bg-[var(--muted)] text-[var(--foreground)]",
  help: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  skills: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  study: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  feedback: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  announcements: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
};

export const FORUM_CATEGORY_DOT: Record<string, string> = {
  general: "bg-slate-400",
  help: "bg-sky-500",
  skills: "bg-violet-500",
  study: "bg-emerald-500",
  feedback: "bg-amber-500",
  announcements: "bg-rose-500",
};

export function useTimeAgo() {
  const { t, i18n } = useTranslation();
  return (iso?: string) => {
    if (!iso) return "";
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return "";
    const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (seconds < 60) return t("just now");
    const rtf = new Intl.RelativeTimeFormat(i18n.language || "en", { numeric: "auto" });
    const steps: [number, Intl.RelativeTimeFormatUnit][] = [
      [60, "minute"],
      [3600, "hour"],
      [86400, "day"],
      [604800, "week"],
      [2629800, "month"],
      [31557600, "year"],
    ];
    let unit: Intl.RelativeTimeFormatUnit = "minute";
    let size = 60;
    for (const [secondsPer, name] of steps) {
      if (seconds >= secondsPer) {
        unit = name;
        size = secondsPer;
      }
    }
    return rtf.format(-Math.floor(seconds / size), unit);
  };
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

export function UserInitial({ name, size = 24 }: { name: string; size?: number }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--primary)]/15 font-semibold text-[var(--primary)]"
      style={{ width: size, height: size, fontSize: Math.round(size * 0.45) }}
    >
      {(name || "?")[0]?.toUpperCase()}
    </span>
  );
}

export function CategoryChip({ id, label }: { id: string; label: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${
        FORUM_CATEGORY_TONE[id] ?? FORUM_CATEGORY_TONE.general
      }`}
    >
      {label}
    </span>
  );
}

export function VoteControl({
  score,
  myVote,
  onVote,
  orientation = "vertical",
  disabled,
}: {
  score: number;
  myVote: number;
  onVote: (value: -1 | 0 | 1) => void;
  orientation?: "vertical" | "horizontal";
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const vertical = orientation === "vertical";
  return (
    <div
      className={`flex items-center ${vertical ? "flex-col gap-0.5" : "gap-1"} select-none`}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        disabled={disabled}
        aria-label={t("Upvote")}
        aria-pressed={myVote === 1}
        onClick={() => onVote(myVote === 1 ? 0 : 1)}
        className={`rounded-md p-0.5 transition-colors hover:bg-[var(--muted)] disabled:opacity-40 ${
          myVote === 1 ? "text-orange-500" : "text-[var(--muted-foreground)] hover:text-orange-500"
        }`}
      >
        <ArrowBigUp size={20} fill={myVote === 1 ? "currentColor" : "none"} strokeWidth={1.6} />
      </button>
      <span
        className={`min-w-[1.5rem] text-center text-[12.5px] font-semibold tabular-nums ${
          myVote === 1
            ? "text-orange-500"
            : myVote === -1
              ? "text-indigo-500"
              : "text-[var(--foreground)]"
        }`}
      >
        {score}
      </span>
      <button
        type="button"
        disabled={disabled}
        aria-label={t("Downvote")}
        aria-pressed={myVote === -1}
        onClick={() => onVote(myVote === -1 ? 0 : -1)}
        className={`rounded-md p-0.5 transition-colors hover:bg-[var(--muted)] disabled:opacity-40 ${
          myVote === -1 ? "text-indigo-500" : "text-[var(--muted-foreground)] hover:text-indigo-500"
        }`}
      >
        <ArrowBigDown size={20} fill={myVote === -1 ? "currentColor" : "none"} strokeWidth={1.6} />
      </button>
    </div>
  );
}

export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const { t } = useTranslation();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className={`surface-raised flex max-h-[90vh] w-full flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--card)] ${
          wide ? "max-w-2xl" : "max-w-lg"
        }`}
      >
        <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3.5">
          <h2 className="text-[15px] font-semibold text-[var(--foreground)]">{title}</h2>
          <button
            onClick={onClose}
            aria-label={t("Close")}
            className="rounded-md p-1 text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <X size={16} />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export const fieldClass =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]";
export const primaryButton =
  "inline-flex items-center justify-center gap-1.5 rounded-lg bg-[var(--primary)] px-3.5 py-2 text-[13px] font-medium text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50";
export const secondaryButton =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--card)] px-3.5 py-2 text-[13px] font-medium text-[var(--foreground)] hover:bg-[var(--muted)] disabled:opacity-50";

export function Pager({
  page,
  total,
  pageSize,
  onPage,
}: {
  page: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
}) {
  const { t } = useTranslation();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  const numbers: number[] = [];
  for (let n = Math.max(1, page - 2); n <= Math.min(pages, page + 2); n += 1) numbers.push(n);
  return (
    <div className="mt-6 flex items-center justify-center gap-1">
      <button
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
        className="rounded-lg px-2.5 py-1 text-[12.5px] text-[var(--muted-foreground)] hover:bg-[var(--muted)] disabled:opacity-40"
      >
        {t("Previous")}
      </button>
      {numbers.map((n) => (
        <button
          key={n}
          onClick={() => onPage(n)}
          className={`min-w-[2rem] rounded-lg px-2 py-1 text-[12.5px] ${
            n === page
              ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
              : "text-[var(--foreground)] hover:bg-[var(--muted)]"
          }`}
        >
          {n}
        </button>
      ))}
      <button
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
        className="rounded-lg px-2.5 py-1 text-[12.5px] text-[var(--muted-foreground)] hover:bg-[var(--muted)] disabled:opacity-40"
      >
        {t("Next")}
      </button>
    </div>
  );
}
