"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useTranslation } from "react-i18next";
import { Eye, Loader2, Pencil, Wand2 } from "lucide-react";

import { Modal, fieldClass, primaryButton, secondaryButton, useCommunityLabels } from "@/components/community/community-ui";
import { FORUM_CATEGORIES, createPost, updatePost, type ForumPost } from "@/lib/community-api";
import { notify } from "@/lib/notifications";

const MarkdownRenderer = dynamic(() => import("@/components/common/MarkdownRenderer"), {
  ssr: false,
});

export default function PostComposerModal({
  onClose,
  onDone,
  initial,
  existing,
  isAdmin = false,
}: {
  onClose: () => void;
  onDone: (post: ForumPost) => void;
  initial?: { category?: string; skillSlug?: string; title?: string; body?: string };
  existing?: ForumPost;
  isAdmin?: boolean;
}) {
  const { t } = useTranslation();
  const labels = useCommunityLabels();
  const [title, setTitle] = useState(existing?.title ?? "");
  const [body, setBody] = useState(existing?.body ?? initial?.body ?? "");
  const [category, setCategory] = useState(existing?.category ?? initial?.category ?? "general");
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const skillSlug = existing ? undefined : initial?.skillSlug;

  const categories = FORUM_CATEGORIES.filter(
    (c) => c !== "announcements" || isAdmin || existing?.category === "announcements",
  );

  async function submit() {
    if (title.trim().length < 3 || busy) return;
    setBusy(true);
    setError("");
    try {
      const post = existing
        ? await updatePost(existing.id, { title: title.trim(), body, category })
        : await createPost({ title: title.trim(), body, category, skill_slug: skillSlug });
      notify(existing ? t("Post updated.") : t("Post published."), { tone: "success" });
      onDone(post);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not publish the post"));
      setBusy(false);
    }
  }

  return (
    <Modal title={existing ? t("Edit post") : t("Create a post")} onClose={onClose} wide>
      <div className="space-y-3">
        <div className="flex flex-wrap gap-1.5">
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              className={`rounded-full border px-3 py-1 text-[12.5px] ${
                category === c
                  ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--foreground)]"
                  : "border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              }`}
            >
              {labels.forumCategory(c)}
            </button>
          ))}
        </div>
        {skillSlug && (
          <div className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--primary)]/10 px-2.5 py-1 text-[12px] text-[var(--primary)]">
            <Wand2 size={12} /> {t("About the skill: {{name}}", { name: initial?.title ?? skillSlug })}
          </div>
        )}
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={300}
          placeholder={t("Title")}
          className={`${fieldClass} text-[15px] font-medium`}
        />
        <div className="flex gap-1 border-b border-[var(--border)]">
          {[
            { on: false, label: t("Write"), icon: Pencil },
            { on: true, label: t("Preview"), icon: Eye },
          ].map(({ on, label, icon: Icon }) => (
            <button
              key={label}
              type="button"
              onClick={() => setPreview(on)}
              className={`-mb-px inline-flex items-center gap-1 border-b-2 px-3 py-1.5 text-[12.5px] ${
                preview === on
                  ? "border-[var(--primary)] text-[var(--foreground)]"
                  : "border-transparent text-[var(--muted-foreground)]"
              }`}
            >
              <Icon size={12} /> {label}
            </button>
          ))}
        </div>
        {preview ? (
          <div className="min-h-[180px] rounded-lg border border-[var(--border)] p-3">
            {body.trim() ? (
              <MarkdownRenderer content={body} variant="compact" allowHtml={false} />
            ) : (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nothing to preview.")}</p>
            )}
          </div>
        ) : (
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={9}
            maxLength={40000}
            placeholder={t("Share details, what you tried, or tips (Markdown supported)")}
            className={fieldClass}
          />
        )}
        {error && <p className="text-[12.5px] text-destructive">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryButton}>
            {t("Cancel")}
          </button>
          <button
            type="button"
            disabled={title.trim().length < 3 || busy}
            onClick={() => void submit()}
            className={primaryButton}
          >
            {busy && <Loader2 size={14} className="animate-spin" />}
            {existing ? t("Save changes") : t("Post")}
          </button>
        </div>
      </div>
    </Modal>
  );
}
