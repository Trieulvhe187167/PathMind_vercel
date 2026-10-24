"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  Download,
  Flame,
  Link2,
  Loader2,
  Lock,
  MessageSquare,
  Pin,
  Search,
  Sparkles,
  Trophy,
  Wand2,
} from "lucide-react";

import {
  CategoryChip,
  FORUM_CATEGORY_DOT,
  Pager,
  UserInitial,
  VoteControl,
  useCommunityLabels,
  useTimeAgo,
} from "@/components/community/community-ui";
import { CommunityShell } from "@/components/community/CommunityShell";
import PostComposerModal from "@/components/community/PostComposerModal";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  FORUM_CATEGORIES,
  listCommunitySkills,
  listPosts,
  votePost,
  type CommunitySkill,
  type ForumPost,
  type PostListResponse,
} from "@/lib/community-api";
import { notify } from "@/lib/notifications";

type Sort = "hot" | "new" | "top";

function plainPreview(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+\.)\s+/gm, "")
    .replace(/[*_`~|]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export default function CommunityForumPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const labels = useCommunityLabels();
  const timeAgo = useTimeAgo();
  const { username } = useAuthStatus();
  const [data, setData] = useState<PostListResponse | null>(null);
  const [loadedKey, setLoadedKey] = useState("");
  const [error, setError] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState<Sort>("hot");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(1);
  const [composing, setComposing] = useState(false);
  const [topSkills, setTopSkills] = useState<CommunitySkill[]>([]);

  const requestKey = JSON.stringify([category, sort, search, mine, page]);
  const loading = loadedKey !== requestKey;

  const load = useCallback(async () => {
    const key = JSON.stringify([category, sort, search, mine, page]);
    try {
      const res = await listPosts({ category, sort, q: search, mine, page });
      setData(res);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not load posts"));
    } finally {
      setLoadedKey(key);
    }
  }, [category, mine, page, search, sort, t]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    listCommunitySkills({ sort: "popular" })
      .then((res) => setTopSkills(res.skills.slice(0, 5)))
      .catch(() => setTopSkills([]));
  }, []);

  async function vote(post: ForumPost, value: -1 | 0 | 1) {
    const before = post;
    const optimistic = { ...post, my_vote: value, score: post.score - post.my_vote + value };
    setData((prev) => (prev ? { ...prev, posts: prev.posts.map((p) => (p.id === post.id ? optimistic : p)) } : prev));
    try {
      const res = await votePost(post.id, value);
      setData((prev) =>
        prev
          ? { ...prev, posts: prev.posts.map((p) => (p.id === post.id ? { ...p, score: res.score, my_vote: res.my_vote } : p)) }
          : prev,
      );
    } catch (e) {
      setData((prev) => (prev ? { ...prev, posts: prev.posts.map((p) => (p.id === post.id ? before : p)) } : prev));
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  const copyLink = (post: ForumPost) => {
    void navigator.clipboard?.writeText(`${window.location.origin}/community/posts/${post.id}`);
    notify(t("Link copied."), { tone: "success" });
  };

  const sortTab = (id: Sort, label: string, Icon: typeof Flame) => (
    <button
      key={id}
      onClick={() => {
        setSort(id);
        setPage(1);
      }}
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium ${
        sort === id
          ? "bg-[var(--muted)] text-[var(--foreground)]"
          : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
      }`}
    >
      <Icon size={14} /> {label}
    </button>
  );

  return (
    <CommunityShell>
      <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
        <div className="min-w-0 space-y-3">
          {/* Create post bar */}
          <div className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3">
            <UserInitial name={username ?? "?"} size={32} />
            <button
              onClick={() => setComposing(true)}
              className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-left text-[13px] text-[var(--muted-foreground)] hover:border-[var(--ring)]"
            >
              {t("Create a post — ask a question or share a tip")}
            </button>
          </div>

          {/* Topics: chips on wide screens, a dropdown on narrow ones */}
          <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] px-2 py-1.5">
            <div className="hidden flex-wrap items-center gap-1 sm:flex">
              {["", ...FORUM_CATEGORIES].map((c) => {
                const active = category === c;
                return (
                  <button
                    key={c || "all"}
                    onClick={() => {
                      setCategory(c);
                      setPage(1);
                    }}
                    className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
                      active
                        ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                        : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
                    }`}
                  >
                    {c && !active && (
                      <span className={`h-2 w-2 rounded-full ${FORUM_CATEGORY_DOT[c] ?? ""}`} />
                    )}
                    {c ? labels.forumCategory(c) : t("All topics")}
                  </button>
                );
              })}
            </div>
            <label className="flex items-center gap-2 px-1 sm:hidden">
              <span className="text-[12.5px] font-medium text-[var(--muted-foreground)]">{t("Topic")}</span>
              <select
                value={category}
                onChange={(e) => {
                  setCategory(e.target.value);
                  setPage(1);
                }}
                className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-[13px] text-[var(--foreground)]"
              >
                <option value="">{t("All topics")}</option>
                {FORUM_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {labels.forumCategory(c)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {/* Sort + search */}
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--border)] bg-[var(--card)] px-2 py-1.5">
            <div className="flex items-center gap-1">
              {sortTab("hot", t("Hot"), Flame)}
              {sortTab("new", t("New"), Sparkles)}
              {sortTab("top", t("Top"), Trophy)}
            </div>
            <form
              className="relative"
              onSubmit={(e) => {
                e.preventDefault();
                setPage(1);
                setSearch(query.trim());
              }}
            >
              <Search
                size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)]"
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("Search posts")}
                className="w-48 rounded-full border border-[var(--border)] bg-[var(--background)] py-1.5 pl-8 pr-3 text-[12.5px] text-[var(--foreground)] outline-none focus:border-[var(--ring)]"
              />
            </form>
          </div>

          {(mine || search) && (
            <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-[var(--muted-foreground)]">
              {t("Filtered:")}
              {mine && <span>{t("My posts")}</span>}
              {search && <span>“{search}”</span>}
              <button
                onClick={() => {
                  setCategory("");
                  setMine(false);
                  setSearch("");
                  setQuery("");
                  setPage(1);
                }}
                className="text-[var(--primary)] hover:underline"
              >
                {t("Clear")}
              </button>
            </div>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}

          {loading && !data ? (
            <div className="flex min-h-[240px] items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
            </div>
          ) : data && data.posts.length === 0 ? (
            <div className="flex min-h-[220px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--border)] text-center">
              <MessageSquare className="text-[var(--muted-foreground)]" />
              <p className="text-sm text-[var(--muted-foreground)]">{t("No posts here yet. Start the conversation!")}</p>
            </div>
          ) : (
            <ul className={`space-y-2 ${loading ? "opacity-60" : ""}`}>
              {data?.posts.map((post) => (
                <li
                  key={post.id}
                  onClick={() => router.push(`/community/posts/${post.id}`)}
                  className="flex cursor-pointer gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3 transition-colors hover:border-[var(--foreground)]/30"
                >
                  <VoteControl score={post.score} myVote={post.my_vote} onVote={(v) => void vote(post, v)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-[var(--muted-foreground)]">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setCategory(post.category);
                          setPage(1);
                        }}
                      >
                        <CategoryChip id={post.category} label={labels.forumCategory(post.category)} />
                      </button>
                      <span>
                        {t("Posted by {{name}}", { name: post.author })} · {timeAgo(post.created_at)}
                      </span>
                      {post.pinned && (
                        <span className="inline-flex items-center gap-0.5 text-emerald-600">
                          <Pin size={11} /> {t("Pinned")}
                        </span>
                      )}
                      {post.locked && (
                        <span className="inline-flex items-center gap-0.5 text-amber-600">
                          <Lock size={11} /> {t("Locked")}
                        </span>
                      )}
                    </div>
                    <h3 className="mt-1 text-[15.5px] font-semibold leading-snug text-[var(--foreground)]">
                      {post.title}
                    </h3>
                    {post.body && (
                      <p className="mt-1 line-clamp-3 text-[13px] leading-relaxed text-[var(--muted-foreground)]">
                        {plainPreview(post.body)}
                      </p>
                    )}
                    {post.skill && (
                      <Link
                        href={`/community/skills/${encodeURIComponent(post.skill.slug)}`}
                        onClick={(e) => e.stopPropagation()}
                        className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2.5 py-1 text-[12px] text-[var(--foreground)] hover:border-[var(--primary)]"
                      >
                        <Wand2 size={12} className="text-[var(--primary)]" /> {post.skill.title}
                      </Link>
                    )}
                    <div className="mt-2 flex items-center gap-1 text-[12px] text-[var(--muted-foreground)]">
                      <span className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]">
                        <MessageSquare size={13} />
                        {t("{{count}} comments", { count: post.comment_count })}
                      </span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          copyLink(post);
                        }}
                        className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
                      >
                        <Link2 size={13} /> {t("Share")}
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {data && (
            <Pager page={data.page} total={data.total} pageSize={data.page_size} onPage={setPage} />
          )}
        </div>

        {/* Sidebar */}
        <aside className="space-y-3">
          <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)]">
            <div className="h-10 bg-gradient-to-r from-[var(--primary)]/60 to-[var(--primary)]/20" />
            <div className="p-4">
              <h3 className="text-[14px] font-semibold text-[var(--foreground)]">{t("PathMind Community")}</h3>
              <p className="mt-1 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
                {t("A place for learners and teachers to ask questions, share study tips and swap AI skills.")}
              </p>
              <button onClick={() => setComposing(true)} className="mt-3 w-full rounded-full bg-[var(--primary)] py-2 text-[13px] font-medium text-[var(--primary-foreground)] hover:opacity-90">
                {t("Create post")}
              </button>
              <button
                onClick={() => {
                  setMine(!mine);
                  setPage(1);
                }}
                className="mt-2 w-full rounded-full border border-[var(--border)] py-2 text-[13px] font-medium text-[var(--foreground)] hover:bg-[var(--muted)]"
              >
                {mine ? t("All posts") : t("My posts")}
              </button>
            </div>
          </div>

          <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-[12px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                {t("Popular skills")}
              </h3>
              <Link href="/community/skills" className="text-[12px] text-[var(--primary)] hover:underline">
                {t("See all")}
              </Link>
            </div>
            {topSkills.length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No skills shared yet.")}</p>
            ) : (
              <ol className="space-y-2">
                {topSkills.map((s, i) => (
                  <li key={s.slug} className="flex items-center gap-2">
                    <span className="w-4 text-[12px] font-semibold text-[var(--muted-foreground)]">{i + 1}</span>
                    <Link
                      href={`/community/skills/${encodeURIComponent(s.slug)}`}
                      className="min-w-0 flex-1 truncate text-[13px] text-[var(--foreground)] hover:underline"
                    >
                      {s.title}
                    </Link>
                    <span className="inline-flex items-center gap-0.5 text-[11.5px] text-[var(--muted-foreground)]">
                      <Download size={11} /> {s.install_count}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 text-[12.5px] text-[var(--muted-foreground)]">
            <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide">{t("Community rules")}</h3>
            <ol className="list-decimal space-y-1 pl-4">
              <li>{t("Be kind and help each other.")}</li>
              <li>{t("No spam, ads or personal data.")}</li>
              <li>{t("Don't share exam answers or copyrighted material.")}</li>
            </ol>
          </div>
        </aside>
      </div>

      {composing && (
        <PostComposerModal
          isAdmin={Boolean(data?.is_admin)}
          initial={{ category: category || "general" }}
          onClose={() => setComposing(false)}
          onDone={(post) => router.push(`/community/posts/${post.id}`)}
        />
      )}
    </CommunityShell>
  );
}
