"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { useCapabilityFilter } from "@/features/capabilities/useCapabilityCatalog";
import {
  ArrowUpRight,
  BookOpen,
  Brain,
  ClipboardList,
  Ear,
  Github,
  History,
  NotebookPen,
  UserRound,
  Wand2,
  type LucideIcon,
} from "lucide-react";

import { listSessions } from "@/lib/session-api";
import { listNotebooks, listNotebookEntries } from "@/lib/notebook-api";
import { listPersonas } from "@/lib/personas-api";
import { listKnowledgeBases } from "@/features/knowledge/api/catalog";
import { listSkills } from "@/lib/skills-api";
import { usePersonalizationAccess } from "@/components/common/AdminOnlyGuard";
import { useEntitlements } from "@/components/access/EntitlementsContext";
import {
  PlanLockBadge,
  UsageMeter,
  isPlanLocked,
  usePlanLockedText,
} from "@/components/access/PlanGate";
import { PRICING_HREF, type PlanFeature, type PlanLimit } from "@/lib/entitlements-api";
import { planRouteFor, requirementKey } from "@/lib/plan-routes";

/**
 * Learning Space dashboard — the hub of `/space`.
 *
 * Replaces the old "land directly in a section behind a side list" flow with a
 * single overview the learner enters from. Each tile is a real entry point that
 * shows a live count so the space feels inhabited, then routes into the full
 * section page (which keeps the mini-nav for lateral movement).
 */

type Lang = { zh: string; en: string };

type DashKey =
  | "knowledge"
  | "memory"
  | "chat_history"
  | "notebooks"
  | "question_bank"
  | "personas"
  | "skills"
  | "whisper";

interface DashboardItem {
  key: DashKey;
  href: string;
  icon: LucideIcon;
  title: Lang;
  blurb: Lang;
  /**
   * Unit shown after the live count, e.g. "168 conversations". Omitted
   * together with ``load`` for a tile that has nothing to count.
   */
  unit?: Lang;
  /** Icon-tile accent — full class strings so Tailwind keeps them. */
  tile: string;
  /**
   * Live count for the tile. Optional: a surface with no countable rows (an
   * ephemeral room, say) renders as title + blurb instead of showing a
   * permanently-loading number.
   */
  load?: () => Promise<number>;
  /** GitHub handle of the contributor this surface came from. */
  credit?: string;
  /**
   * Turn capability this surface needs, when it is not served by this
   * repository. The tile is withheld unless the backend registry actually
   * holds the name, so a stock install never offers a room whose capability
   * was never installed (#963).
   */
  requiresCapability?: string;
  /** Only administrators see this tile when sign-in is enabled. */
  adminOnly?: boolean;
}

interface DashboardGroup {
  label: Lang;
  items: DashboardItem[];
}

const GROUPS: DashboardGroup[] = [
  {
    label: { zh: "对话与资料", en: "Conversations & Materials" },
    items: [
      {
        key: "chat_history",
        href: "/space/chat-history",
        icon: History,
        title: { zh: "聊天历史", en: "Chat History" },
        blurb: {
          zh: "回顾并继续此前的对话。",
          en: "Review and reopen previous conversations.",
        },
        unit: { zh: "段对话", en: "conversations" },
        tile: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
        load: async () => (await listSessions(200, 0, { force: true })).length,
      },
      {
        key: "notebooks",
        href: "/notebooks",
        icon: NotebookPen,
        title: { zh: "笔记本", en: "Notebooks" },
        blurb: {
          zh: "整理来自对话、研究、智能写作等的产出。",
          en: "Organize saved outputs from chat, research, Co-Writer, and more.",
        },
        unit: { zh: "个笔记本", en: "notebooks" },
        tile: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
        load: async () => (await listNotebooks()).length,
      },
      {
        key: "question_bank",
        href: "/space/questions",
        icon: ClipboardList,
        title: { zh: "题库", en: "Question Bank" },
        blurb: {
          zh: "跨会话回顾和整理测验题目。",
          en: "Review and organize quiz questions across sessions.",
        },
        unit: { zh: "道题", en: "questions" },
        tile: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        load: async () => (await listNotebookEntries({ limit: 1 })).total,
      },
      {
        key: "knowledge",
        href: "/knowledge-bases",
        icon: BookOpen,
        title: { zh: "知识中心", en: "Knowledge Center" },
        blurb: {
          zh: "管理知识库与检索引擎。",
          en: "Manage knowledge bases and retrieval engines.",
        },
        unit: { zh: "个知识库", en: "knowledge bases" },
        tile: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
        load: async () => (await listKnowledgeBases({ library: true })).length,
      },
    ],
  },
  {
    label: { zh: "个性化", en: "Personalization" },
    items: [
      {
        key: "personas",
        adminOnly: true,
        href: "/space/personas",
        icon: UserRound,
        title: { zh: "Personas", en: "Personas" },
        blurb: {
          zh: "可在每轮对话中套用的行为预设。",
          en: "Behavior presets you can apply per chat turn.",
        },
        unit: { zh: "个预设", en: "personas" },
        tile: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
        load: async () => (await listPersonas()).length,
      },
      {
        key: "skills",
        href: "/space/skills",
        icon: Wand2,
        title: { zh: "技能", en: "Skills" },
        blurb: {
          zh: "模型按需读取的能力手册。",
          en: "Capability playbooks the model reads on demand.",
        },
        unit: { zh: "个技能", en: "skills" },
        tile: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
        load: async () => (await listSkills()).length,
      },
      {
        key: "memory",
        href: "/memory",
        icon: Brain,
        title: { zh: "记忆", en: "Memory" },
        blurb: {
          zh: "查看与整理导师记住的偏好和学习经历。",
          en: "Review what your tutor remembers about you.",
        },
        tile: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
      },
    ],
  },
  {
    label: { zh: "更多项目", en: "More Projects" },
    items: [
      {
        key: "whisper",
        href: "/whisper",
        icon: Ear,
        title: { zh: "密语", en: "Whisper" },
        blurb: {
          zh: "双席位咨询练习房间：督导只对受训者耳语。",
          en: "Dual-seat practice room — the supervisor whispers to the trainee only.",
        },
        tile: "bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400",
        credit: "alanguan73",
        // Served by the out-of-tree psych-academy plugin, not by this repo.
        requiresCapability: "whisper_visitor",
      },
    ],
  },
];

/**
 * The groups to render, given what the backend can actually serve.
 *
 * `isAvailable` is null while the probe is in flight: gated tiles stay hidden
 * until then, so a surface whose capability was never installed does not flash
 * into view and out again — an ungated tile is never affected. A group left
 * with no tiles is dropped along with its heading, or "More Projects" would
 * render as a title over nothing (#963).
 */
export function visibleGroups(
  groups: DashboardGroup[],
  isAvailable: ((name: string) => boolean) | null
): DashboardGroup[] {
  return groups
    .map(group => ({
      ...group,
      items: group.items.filter(
        item =>
          !item.requiresCapability ||
          (isAvailable?.(item.requiresCapability) ?? false)
      ),
    }))
    .filter(group => group.items.length > 0);
}

export { GROUPS as DASHBOARD_GROUPS };

export default function SpaceDashboard() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const tr = useCallback((l: Lang) => (zh ? l.zh : l.en), [zh]);

  const [counts, setCounts] = useState<Partial<Record<DashKey, number>>>({});
  const ent = useEntitlements();
  const lockedText = usePlanLockedText();
  const planReady = !ent.loading || !!ent.data;
  /** Plan requirement key when the plan locks this tile, else null. */
  const lockOf = useCallback(
    (item: DashboardItem): PlanFeature | PlanLimit | null => {
      const route = planRouteFor(item.href);
      return route && isPlanLocked(ent, route.requires)
        ? requirementKey(route.requires)
        : null;
    },
    [ent]
  );

  const capabilityAvailable = useCapabilityFilter();
  const personalization = usePersonalizationAccess();
  // Skills are every user's own (per-account); personas and
  // memory stay admin-only when sign-in is enabled.
  const groups = useMemo(
    () =>
      visibleGroups(GROUPS, capabilityAvailable)
        .map(group => ({
          ...group,
          items: group.items.filter(
            item => !item.adminOnly || personalization.allowed
          ),
        }))
        .filter(group => group.items.length > 0),
    [capabilityAvailable, personalization.allowed]
  );

  useEffect(() => {
    let cancelled = false;
    // Each tile loads independently so one slow/failed endpoint never blanks
    // the whole dashboard.
    if (!personalization.ready || !planReady) return;
    // Locked tiles are not counted: their endpoints answer 402 on this plan.
    const items = GROUPS.flatMap(group => group.items).filter(
      item => (!item.adminOnly || personalization.allowed) && !lockOf(item)
    );
    for (const item of items) {
      if (!item.load) continue;
      item
        .load()
        .then(n => {
          if (!cancelled) setCounts(prev => ({ ...prev, [item.key]: n }));
        })
        .catch(() => {
          /* leave undefined → tile just omits the count */
        });
    }
    return () => {
      cancelled = true;
    };
  }, [personalization.ready, personalization.allowed, planReady, lockOf]);

  return (
    <div>
      <header className="mb-8">
        <h1 className="font-serif text-[24px] font-semibold leading-tight tracking-tight text-[var(--foreground)]">
          {tr({ zh: "学习空间", en: "Learning Space" })}
        </h1>
        <p className="mt-1.5 max-w-xl text-[13px] leading-relaxed text-[var(--muted-foreground)]">
          {tr({
            zh: "你的对话、智能体、笔记与题目，集中在一处 —— 从这里进入。",
            en: "Your conversations, agents, notebooks, and questions in one place — enter from here.",
          })}
        </p>
      </header>

      <div className="space-y-9">
        {groups.map(group => (
          <section key={group.label.en}>
            <h2 className="mb-3 px-0.5 font-serif text-[16px] font-semibold tracking-tight text-[var(--foreground)]">
              {tr(group.label)}
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {group.items.map(item => (
                <DashboardCard
                  key={item.key}
                  item={item}
                  count={counts[item.key]}
                  tr={tr}
                  locked={lockOf(item)}
                  lockedText={lockedText}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function DashboardCard({
  item,
  count,
  tr,
  locked = null,
  lockedText,
}: {
  item: DashboardItem;
  count: number | undefined;
  tr: (l: Lang) => string;
  /** Plan requirement locking this tile (it then links to pricing). */
  locked?: PlanFeature | PlanLimit | null;
  lockedText?: (target: PlanFeature | PlanLimit) => string;
}) {
  const Icon = item.icon;
  const loaded = count !== undefined;
  const formatted = useMemo(
    () => (loaded ? count.toLocaleString() : ""),
    [loaded, count]
  );

  return (
    <Link
      href={locked ? PRICING_HREF : item.href}
      aria-label={locked && lockedText ? `${tr(item.title)} — ${lockedText(locked)}` : undefined}
      className={`group relative flex flex-col rounded-xl border border-[var(--border)] p-4 transition-all duration-150 hover:-translate-y-0.5 hover:border-[var(--foreground)]/20 hover:shadow-[0_6px_20px_-12px_rgba(0,0,0,0.25)] ${
        locked ? "bg-[var(--secondary)]/40" : "bg-[var(--card)]"
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${item.tile}`}
        >
          <Icon size={18} strokeWidth={1.7} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[14.5px] font-medium leading-tight tracking-tight text-[var(--foreground)]">
            {tr(item.title)}
          </h3>
          {locked && lockedText ? (
            <p className="mt-1.5 text-[12px] text-[var(--muted-foreground)]">
              {lockedText(locked)}
            </p>
          ) : item.unit ? (
            <div className="mt-1 flex items-baseline gap-1.5">
              {loaded ? (
                <>
                  <span className="text-[20px] font-semibold leading-none tabular-nums text-[var(--foreground)]">
                    {formatted}
                  </span>
                  <span className="text-[12px] text-[var(--muted-foreground)]">
                    {tr(item.unit)}
                  </span>
                </>
              ) : (
                <span className="my-[3px] h-3.5 w-12 animate-pulse rounded bg-[var(--muted)]" />
              )}
            </div>
          ) : null}
        </div>
        {locked ? (
          <PlanLockBadge target={locked} />
        ) : (
          <ArrowUpRight
            size={16}
            className="shrink-0 text-[var(--muted-foreground)]/40 transition-colors group-hover:text-[var(--foreground)]"
          />
        )}
      </div>
      <p className="mt-3 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
        {tr(item.blurb)}
      </p>
      {!locked && item.key === "skills" ? (
        <UsageMeter target="max_skills" label="Your own skills" className="mt-2" inLink />
      ) : null}
      {item.credit ? (
        <span className="mt-2.5 inline-flex items-center gap-1 self-start text-[11px] leading-none text-[var(--muted-foreground)] opacity-60">
          <Github size={11} strokeWidth={1.8} aria-hidden />
          {item.credit}
        </span>
      ) : null}
    </Link>
  );
}
