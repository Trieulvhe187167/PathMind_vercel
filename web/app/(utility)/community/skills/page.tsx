"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { Check, Download, Heart, Loader2, Plus, Search, Share2, Sparkles } from "lucide-react";

import {
  Pager,
  UserInitial,
  primaryButton,
  useCommunityLabels,
} from "@/components/community/community-ui";
import { CommunityShell } from "@/components/community/CommunityShell";
import PublishSkillModal from "@/components/community/PublishSkillModal";
import {
  CommunityApiError,
  installCommunitySkill,
  listCommunitySkills,
  toggleSkillLike,
  type CommunitySkill,
  type SkillListResponse,
} from "@/lib/community-api";
import { confirmAction } from "@/lib/confirm";
import { notify } from "@/lib/notifications";
import { useEntitlements } from "@/components/access/EntitlementsContext";
import { UsageMeter, useLimitReachedText } from "@/components/access/PlanGate";

type Sort = "popular" | "new" | "likes";

export default function SkillHubPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const labels = useCommunityLabels();
  const [data, setData] = useState<SkillListResponse | null>(null);
  const [loadedKey, setLoadedKey] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState<Sort>("popular");
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(1);
  const [publishing, setPublishing] = useState(false);
  const [installing, setInstalling] = useState("");
  const ent = useEntitlements();
  const limitText = useLimitReachedText();

  const requestKey = JSON.stringify([search, category, sort, mine, page]);
  const loading = loadedKey !== requestKey;

  const load = useCallback(async () => {
    const key = JSON.stringify([search, category, sort, mine, page]);
    try {
      const res = await listCommunitySkills({ q: search, category, sort, mine, page });
      setData(res);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not load skills"));
    } finally {
      setLoadedKey(key);
    }
  }, [category, mine, page, search, sort, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const patch = (slug: string, change: Partial<CommunitySkill>) =>
    setData((prev) =>
      prev
        ? { ...prev, skills: prev.skills.map((s) => (s.slug === slug ? { ...s, ...change } : s)) }
        : prev,
    );

  async function install(skill: CommunitySkill) {
    if (ent.atLimit("max_skills")) {
      notify(limitText("max_skills"), { tone: "error" });
      return;
    }
    setInstalling(skill.slug);
    try {
      let res;
      try {
        res = await installCommunitySkill(skill.slug);
      } catch (e) {
        if (!(e instanceof CommunityApiError) || e.status !== 409) throw e;
        const replace = await confirmAction(
          t("You already have a skill named “{{name}}”. Replace it with this one?", { name: skill.slug }),
          { tone: "danger", confirmLabel: t("Replace") },
        );
        if (!replace) return;
        res = await installCommunitySkill(skill.slug, { force: true });
      }
      patch(skill.slug, { installed_as: res.name, install_count: res.install_count });
      notify(t("Added “{{name}}” to your Skills.", { name: res.name }), { tone: "success" });
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Could not add the skill"), { tone: "error" });
    } finally {
      setInstalling("");
    }
  }

  async function like(skill: CommunitySkill) {
    try {
      const res = await toggleSkillLike(skill.slug);
      patch(skill.slug, { liked: res.liked, like_count: res.like_count });
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  const categories = data?.categories ?? [];
  const totalAll = categories.reduce((sum, c) => sum + c.count, 0);
  const chip = (active: boolean) =>
    `inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12.5px] transition-colors ${
      active
        ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--foreground)]"
        : "border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
    }`;

  return (
    <CommunityShell
      actions={
        <button onClick={() => setPublishing(true)} className={primaryButton}>
          <Share2 size={14} /> {t("Share a skill")}
        </button>
      }
    >
      {/* Hero + search */}
      <section className="mb-5 rounded-2xl border border-[var(--border)] bg-gradient-to-br from-[var(--primary)]/10 via-[var(--card)] to-[var(--card)] p-6">
        <div className="flex items-center gap-2 text-[12px] font-medium text-[var(--primary)]">
          <Sparkles size={14} /> {t("Community Skill Hub")}
        </div>
        <h2 className="mt-1 text-[22px] font-semibold tracking-tight text-[var(--foreground)]">
          {t("Teach your AI tutor new tricks")}
        </h2>
        <p className="mt-1 max-w-2xl text-[13px] text-[var(--muted-foreground)]">
          {t(
            "Skills are playbooks the AI reads when they fit your question. Add one with a click — no CLI, no download — or share your own from a file, your Skills or Co-Writer.",
          )}
        </p>
        <UsageMeter target="max_skills" label="Your own skills" className="mt-2" />
        <form
          className="mt-4 flex max-w-xl gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(1);
            setSearch(query.trim());
          }}
        >
          <div className="relative flex-1">
            <Search
              size={15}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)]"
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("Search skills")}
              className="w-full rounded-xl border border-[var(--border)] bg-[var(--background)] py-2.5 pl-9 pr-3 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]"
            />
          </div>
          <button type="submit" className={primaryButton}>
            {t("Search")}
          </button>
        </form>
      </section>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5">
          <button
            className={chip(!category && !mine)}
            onClick={() => {
              setCategory("");
              setMine(false);
              setPage(1);
            }}
          >
            {t("All")} <span className="text-[11px] opacity-70">{totalAll}</span>
          </button>
          {categories.map((c) => (
            <button
              key={c.id}
              className={chip(category === c.id)}
              onClick={() => {
                setCategory(category === c.id ? "" : c.id);
                setPage(1);
              }}
            >
              {labels.skillCategory(c.id)} <span className="text-[11px] opacity-70">{c.count}</span>
            </button>
          ))}
          <button
            className={chip(mine)}
            onClick={() => {
              setMine(!mine);
              setPage(1);
            }}
          >
            {t("Shared by me")}
          </button>
        </div>
        <select
          value={sort}
          onChange={(e) => {
            setSort(e.target.value as Sort);
            setPage(1);
          }}
          className="rounded-lg border border-[var(--border)] bg-[var(--card)] px-2.5 py-1.5 text-[12.5px] text-[var(--foreground)]"
          aria-label={t("Sort")}
        >
          <option value="popular">{t("Most added")}</option>
          <option value="likes">{t("Most liked")}</option>
          <option value="new">{t("Newest")}</option>
        </select>
      </div>

      {error && <p className="mb-3 text-sm text-destructive">{error}</p>}

      {loading && !data ? (
        <div className="flex min-h-[240px] items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
        </div>
      ) : data && data.skills.length === 0 ? (
        <div className="flex min-h-[240px] flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-[var(--border)] text-center">
          <p className="text-sm text-[var(--muted-foreground)]">
            {search || category || mine
              ? t("No skills match these filters.")
              : t("No skills shared yet — be the first!")}
          </p>
          <button onClick={() => setPublishing(true)} className={primaryButton}>
            <Plus size={14} /> {t("Share a skill")}
          </button>
        </div>
      ) : (
        <ul className={`grid gap-3 sm:grid-cols-2 lg:grid-cols-3 ${loading ? "opacity-60" : ""}`}>
          {data?.skills.map((skill) => (
            <li
              key={skill.slug}
              className="group flex cursor-pointer flex-col rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-sm transition-all hover:border-[var(--foreground)]/30 hover:shadow-md"
              onClick={() => router.push(`/community/skills/${encodeURIComponent(skill.slug)}`)}
            >
              <div className="mb-2 flex flex-wrap gap-1">
                <span className="rounded-md bg-[var(--primary)]/10 px-1.5 py-0.5 text-[10.5px] font-medium text-[var(--primary)]">
                  {labels.skillCategory(skill.category)}
                </span>
                {skill.tags.slice(0, 3).map((tag) => (
                  <span
                    key={tag}
                    className="rounded-md bg-[var(--muted)] px-1.5 py-0.5 text-[10.5px] text-[var(--muted-foreground)]"
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <Link
                href={`/community/skills/${encodeURIComponent(skill.slug)}`}
                onClick={(e) => e.stopPropagation()}
                className="text-[15px] font-semibold tracking-tight text-[var(--foreground)] hover:underline"
              >
                {skill.title}
              </Link>
              <p className="mt-1 line-clamp-3 flex-1 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
                {skill.description}
              </p>
              <div className="mt-3 flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]">
                  <UserInitial name={skill.author} size={20} />
                  <span className="truncate">{skill.author}</span>
                </span>
                <div className="flex shrink-0 items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void like(skill);
                    }}
                    className={`inline-flex items-center gap-1 rounded-md px-1 py-0.5 hover:bg-[var(--muted)] ${
                      skill.liked ? "text-rose-500" : ""
                    }`}
                    aria-label={t("Like")}
                  >
                    <Heart size={13} fill={skill.liked ? "currentColor" : "none"} /> {skill.like_count}
                  </button>
                  <span className="inline-flex items-center gap-1" title={t("Added by")}>
                    <Download size={13} /> {skill.install_count}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!skill.installed_as) void install(skill);
                    }}
                    disabled={installing === skill.slug}
                    className={`inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-[12px] font-medium ${
                      skill.installed_as
                        ? "bg-[var(--muted)] text-[var(--muted-foreground)]"
                        : "bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90"
                    }`}
                  >
                    {installing === skill.slug ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : skill.installed_as ? (
                      <Check size={12} />
                    ) : (
                      <Plus size={12} />
                    )}
                    {skill.installed_as ? t("Added") : t("Add")}
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {data && (
        <Pager
          page={data.page}
          total={data.total}
          pageSize={data.page_size}
          onPage={(n) => {
            setPage(n);
            window.scrollTo({ top: 0 });
          }}
        />
      )}

      {publishing && (
        <PublishSkillModal
          onClose={() => setPublishing(false)}
          onDone={(skill) => {
            setPublishing(false);
            router.push(`/community/skills/${encodeURIComponent(skill.slug)}`);
          }}
        />
      )}
    </CommunityShell>
  );
}
