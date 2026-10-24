"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  Flag,
  Link2,
  Loader2,
  Lock,
  MessageSquare,
  Pencil,
  Pin,
  Reply,
  Trash2,
  Unlock,
  Wand2,
} from "lucide-react";

import {
  CategoryChip,
  UserInitial,
  VoteControl,
  fieldClass,
  primaryButton,
  secondaryButton,
  useCommunityLabels,
  useTimeAgo,
} from "@/components/community/community-ui";
import { CommunityShell } from "@/components/community/CommunityShell";
import PostComposerModal from "@/components/community/PostComposerModal";
import ReportDialog from "@/components/community/ReportDialog";
import {
  createComment,
  deleteComment,
  deletePost,
  getPost,
  moderatePost,
  updateComment,
  voteComment,
  votePost,
  type ForumComment,
  type ForumPost,
} from "@/lib/community-api";
import { confirmAction } from "@/lib/confirm";
import { notify } from "@/lib/notifications";

const MarkdownRenderer = dynamic(() => import("@/components/common/MarkdownRenderer"), {
  ssr: false,
});

const MAX_INDENT = 6;

function CommentEditor({
  initial = "",
  placeholder,
  submitLabel,
  onSubmit,
  onCancel,
  autoFocus,
}: {
  initial?: string;
  placeholder: string;
  submitLabel: string;
  onSubmit: (body: string) => Promise<void>;
  onCancel?: () => void;
  autoFocus?: boolean;
}) {
  const { t } = useTranslation();
  const [body, setBody] = useState(initial);
  const [busy, setBusy] = useState(false);
  return (
    <div className="space-y-2">
      <textarea
        value={body}
        autoFocus={autoFocus}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        maxLength={10000}
        placeholder={placeholder}
        className={fieldClass}
      />
      <div className="flex justify-end gap-2">
        {onCancel && (
          <button type="button" onClick={onCancel} className={`${secondaryButton} py-1.5 text-[12.5px]`}>
            {t("Cancel")}
          </button>
        )}
        <button
          type="button"
          disabled={!body.trim() || busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onSubmit(body.trim());
              setBody("");
            } finally {
              setBusy(false);
            }
          }}
          className={`${primaryButton} py-1.5 text-[12.5px]`}
        >
          {busy && <Loader2 size={13} className="animate-spin" />}
          {submitLabel}
        </button>
      </div>
    </div>
  );
}

export default function ForumPostPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const labels = useCommunityLabels();
  const timeAgo = useTimeAgo();
  const params = useParams<{ id: string }>();
  const postId = String(params?.id ?? "");
  const [post, setPost] = useState<ForumPost | null>(null);
  const [comments, setComments] = useState<ForumComment[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState("");
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [editingComment, setEditingComment] = useState<string | null>(null);
  const [editingPost, setEditingPost] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [commentSort, setCommentSort] = useState<"best" | "new" | "old">("best");
  const [reporting, setReporting] = useState<{ type: "post" | "comment"; id: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await getPost(postId);
        if (cancelled) return;
        setPost(res.post);
        setComments(res.comments);
        setIsAdmin(res.is_admin);
        setError("");
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : t("Could not load the post"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [postId, t]);

  const children = useMemo(() => {
    const map = new Map<string | null, ForumComment[]>();
    for (const c of comments) {
      const key = c.parent_id && comments.some((p) => p.id === c.parent_id) ? c.parent_id : null;
      map.set(key, [...(map.get(key) ?? []), c]);
    }
    const order = (a: ForumComment, b: ForumComment) =>
      commentSort === "new"
        ? b.created_at.localeCompare(a.created_at)
        : commentSort === "old"
          ? a.created_at.localeCompare(b.created_at)
          : b.score - a.score || a.created_at.localeCompare(b.created_at);
    for (const list of map.values()) list.sort(order);
    return map;
  }, [comments, commentSort]);

  const patchComment = (id: string, change: Partial<ForumComment>) =>
    setComments((prev) => prev.map((c) => (c.id === id ? { ...c, ...change } : c)));

  async function onVotePost(value: -1 | 0 | 1) {
    if (!post) return;
    try {
      const res = await votePost(post.id, value);
      setPost({ ...post, score: res.score, my_vote: res.my_vote });
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  async function onVoteComment(c: ForumComment, value: -1 | 0 | 1) {
    try {
      const res = await voteComment(c.id, value);
      patchComment(c.id, { score: res.score, my_vote: res.my_vote });
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  async function addComment(body: string, parentId: string | null) {
    if (!post) return;
    try {
      const created = await createComment(post.id, body, parentId);
      setComments((prev) => [...prev, created]);
      setPost({ ...post, comment_count: post.comment_count + 1 });
      setReplyTo(null);
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Could not post the comment"), { tone: "error" });
      throw e;
    }
  }

  async function removeComment(c: ForumComment) {
    if (!(await confirmAction(t("Delete this comment?"), { tone: "danger", confirmLabel: t("Delete") }))) return;
    try {
      await deleteComment(c.id);
      patchComment(c.id, { deleted: true, body: "", author: "", author_id: null, can_edit: false });
      if (post) setPost({ ...post, comment_count: Math.max(0, post.comment_count - 1) });
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  async function removePost() {
    if (!post) return;
    if (!(await confirmAction(t("Delete this post?"), { tone: "danger", confirmLabel: t("Delete") }))) return;
    try {
      await deletePost(post.id);
      notify(t("Post deleted."), { tone: "success" });
      router.push("/community");
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  async function moderate(change: { pinned?: boolean; locked?: boolean }) {
    if (!post) return;
    try {
      const res = await moderatePost(post.id, change);
      setPost({ ...post, pinned: res.pinned, locked: res.locked });
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  function renderThread(parentId: string | null, depth: number): ReactNode {
    const list = children.get(parentId) ?? [];
    if (list.length === 0) return null;
    return (
      <ul className={depth > 0 ? "mt-2 space-y-2" : "space-y-3"}>
        {list.map((c) => {
          const isCollapsed = collapsed.has(c.id);
          const replies = children.get(c.id)?.length ?? 0;
          return (
            <li key={c.id} className="flex gap-2">
              <div className="flex flex-col items-center">
                <UserInitial name={c.deleted ? "?" : c.author} size={26} />
                <button
                  aria-label={isCollapsed ? t("Expand") : t("Collapse")}
                  onClick={() =>
                    setCollapsed((prev) => {
                      const next = new Set(prev);
                      if (next.has(c.id)) next.delete(c.id);
                      else next.add(c.id);
                      return next;
                    })
                  }
                  className="mt-1 w-4 flex-1 cursor-pointer"
                >
                  <span className="mx-auto block h-full w-px bg-[var(--border)] hover:bg-[var(--primary)]" />
                </button>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 text-[12px] text-[var(--muted-foreground)]">
                  <span className="font-medium text-[var(--foreground)]">
                    {c.deleted ? t("[deleted]") : c.author}
                  </span>
                  {!c.deleted && post && c.author_id === post.author_id && (
                    <span className="rounded bg-[var(--primary)]/10 px-1 text-[10.5px] font-semibold text-[var(--primary)]">
                      {t("OP")}
                    </span>
                  )}
                  <span>· {timeAgo(c.created_at)}</span>
                  {c.edited_at && <span>· {t("edited")}</span>}
                  {isCollapsed && replies > 0 && (
                    <span>· {t("{{count}} replies hidden", { count: replies })}</span>
                  )}
                </div>
                {!isCollapsed && (
                  <>
                    {editingComment === c.id ? (
                      <div className="mt-1">
                        <CommentEditor
                          initial={c.body}
                          autoFocus
                          placeholder={t("Edit your comment")}
                          submitLabel={t("Save")}
                          onCancel={() => setEditingComment(null)}
                          onSubmit={async (body) => {
                            try {
                              const res = await updateComment(c.id, body);
                              patchComment(c.id, { body: res.body, edited_at: res.edited_at });
                              setEditingComment(null);
                            } catch (e) {
                              notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
                            }
                          }}
                        />
                      </div>
                    ) : (
                      <div className="mt-0.5 text-[13.5px] text-[var(--foreground)]">
                        {c.deleted ? (
                          <span className="italic text-[var(--muted-foreground)]">{t("This comment was deleted.")}</span>
                        ) : (
                          <MarkdownRenderer content={c.body} variant="compact" allowHtml={false} />
                        )}
                      </div>
                    )}
                    {!c.deleted && editingComment !== c.id && (
                      <div className="mt-1 flex items-center gap-1 text-[12px] text-[var(--muted-foreground)]">
                        <VoteControl
                          orientation="horizontal"
                          score={c.score}
                          myVote={c.my_vote}
                          onVote={(v) => void onVoteComment(c, v)}
                        />
                        {(!post?.locked || isAdmin) && (
                          <button
                            onClick={() => setReplyTo(replyTo === c.id ? null : c.id)}
                            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
                          >
                            <Reply size={13} /> {t("Reply")}
                          </button>
                        )}
                        {!c.can_edit && (
                          <button
                            onClick={() => setReporting({ type: "comment", id: c.id })}
                            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
                          >
                            <Flag size={12} /> {t("Report")}
                          </button>
                        )}
                        {c.can_edit && (
                          <>
                            <button
                              onClick={() => setEditingComment(c.id)}
                              className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
                            >
                              <Pencil size={12} /> {t("Edit")}
                            </button>
                            <button
                              onClick={() => void removeComment(c)}
                              className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)] hover:text-destructive"
                            >
                              <Trash2 size={12} /> {t("Delete")}
                            </button>
                          </>
                        )}
                      </div>
                    )}
                    {replyTo === c.id && (
                      <div className="mt-2">
                        <CommentEditor
                          autoFocus
                          placeholder={t("Write a reply")}
                          submitLabel={t("Reply")}
                          onCancel={() => setReplyTo(null)}
                          onSubmit={(body) => addComment(body, c.id)}
                        />
                      </div>
                    )}
                    {renderThread(c.id, Math.min(depth + 1, MAX_INDENT))}
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    );
  }

  const back = (
    <Link
      href="/community"
      className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
    >
      <ArrowLeft size={14} /> {t("Back to forum")}
    </Link>
  );

  if (error && !post) {
    return (
      <CommunityShell>
        {back}
        <p className="text-sm text-[var(--muted-foreground)]">{error}</p>
      </CommunityShell>
    );
  }
  if (!post) {
    return (
      <CommunityShell>
        <div className="flex min-h-[240px] items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
        </div>
      </CommunityShell>
    );
  }

  return (
    <CommunityShell>
      {back}
      <div className="mx-auto max-w-3xl space-y-4">
        <article className="flex gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
          <VoteControl score={post.score} myVote={post.my_vote} onVote={(v) => void onVotePost(v)} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-[var(--muted-foreground)]">
              <CategoryChip id={post.category} label={labels.forumCategory(post.category)} />
              <span>
                {t("Posted by {{name}}", { name: post.author })} · {timeAgo(post.created_at)}
              </span>
              {post.edited_at && <span>· {t("edited")}</span>}
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
            <h2 className="mt-1.5 text-[20px] font-semibold leading-snug text-[var(--foreground)]">{post.title}</h2>
            {post.skill && (
              <Link
                href={`/community/skills/${encodeURIComponent(post.skill.slug)}`}
                className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2.5 py-1 text-[12.5px] text-[var(--foreground)] hover:border-[var(--primary)]"
              >
                <Wand2 size={13} className="text-[var(--primary)]" /> {post.skill.title}
              </Link>
            )}
            {post.body && (
              <div className="mt-3 text-[14px] text-[var(--foreground)]">
                <MarkdownRenderer content={post.body} variant="prose" allowHtml={false} />
              </div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-1 text-[12px] text-[var(--muted-foreground)]">
              <span className="inline-flex items-center gap-1 px-1.5 py-1">
                <MessageSquare size={13} /> {t("{{count}} comments", { count: post.comment_count })}
              </span>
              <button
                onClick={() => {
                  void navigator.clipboard?.writeText(window.location.href);
                  notify(t("Link copied."), { tone: "success" });
                }}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
              >
                <Link2 size={13} /> {t("Share")}
              </button>
              {!post.can_edit && (
                <button
                  onClick={() => setReporting({ type: "post", id: post.id })}
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
                >
                  <Flag size={12} /> {t("Report")}
                </button>
              )}
              {post.can_edit && (
                <>
                  <button
                    onClick={() => setEditingPost(true)}
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
                  >
                    <Pencil size={12} /> {t("Edit")}
                  </button>
                  <button
                    onClick={() => void removePost()}
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)] hover:text-destructive"
                  >
                    <Trash2 size={12} /> {t("Delete")}
                  </button>
                </>
              )}
              {isAdmin && (
                <>
                  <button
                    onClick={() => void moderate({ pinned: !post.pinned })}
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
                  >
                    <Pin size={12} /> {post.pinned ? t("Unpin") : t("Pin")}
                  </button>
                  <button
                    onClick={() => void moderate({ locked: !post.locked })}
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-[var(--muted)]"
                  >
                    {post.locked ? <Unlock size={12} /> : <Lock size={12} />}
                    {post.locked ? t("Unlock") : t("Lock")}
                  </button>
                </>
              )}
            </div>
          </div>
        </article>

        <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
          {post.locked && !isAdmin ? (
            <p className="flex items-center gap-2 rounded-lg bg-[var(--muted)]/60 p-3 text-[12.5px] text-[var(--muted-foreground)]">
              <Lock size={13} /> {t("This post is locked; new comments are closed.")}
            </p>
          ) : (
            <CommentEditor
              placeholder={t("What are your thoughts?")}
              submitLabel={t("Comment")}
              onSubmit={(body) => addComment(body, null)}
            />
          )}

          <div className="mb-3 mt-4 flex items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
            {t("Sort by")}
            <select
              value={commentSort}
              onChange={(e) => setCommentSort(e.target.value as "best" | "new" | "old")}
              className="rounded-md border border-[var(--border)] bg-[var(--card)] px-2 py-1 text-[12px] text-[var(--foreground)]"
            >
              <option value="best">{t("Best")}</option>
              <option value="new">{t("Newest")}</option>
              <option value="old">{t("Oldest")}</option>
            </select>
          </div>
          {comments.length === 0 ? (
            <p className="py-6 text-center text-[12.5px] text-[var(--muted-foreground)]">
              {t("No comments yet. Be the first to help!")}
            </p>
          ) : (
            renderThread(null, 0)
          )}
        </section>
      </div>

      {reporting && (
        <ReportDialog
          targetType={reporting.type}
          targetId={reporting.id}
          onClose={() => setReporting(null)}
        />
      )}
      {editingPost && (
        <PostComposerModal
          existing={post}
          isAdmin={isAdmin}
          onClose={() => setEditingPost(false)}
          onDone={(updated) => {
            setEditingPost(false);
            setPost({ ...post, ...updated });
          }}
        />
      )}
    </CommunityShell>
  );
}
