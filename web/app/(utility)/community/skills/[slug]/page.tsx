"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  Check,
  Download,
  FileText,
  Flag,
  Heart,
  Loader2,
  MessageSquarePlus,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";

import {
  UserInitial,
  primaryButton,
  secondaryButton,
  useCommunityLabels,
  useTimeAgo,
} from "@/components/community/community-ui";
import { CommunityShell } from "@/components/community/CommunityShell";
import PostComposerModal from "@/components/community/PostComposerModal";
import PublishSkillModal from "@/components/community/PublishSkillModal";
import ReportDialog from "@/components/community/ReportDialog";
import {
  CommunityApiError,
  deleteCommunitySkill,
  getCommunitySkill,
  installCommunitySkill,
  toggleSkillLike,
  type CommunitySkillDetail,
} from "@/lib/community-api";
import { confirmAction } from "@/lib/confirm";
import { notify } from "@/lib/notifications";
import { useEntitlements } from "@/components/access/EntitlementsContext";
import { useLimitReachedText } from "@/components/access/PlanGate";

const MarkdownRenderer = dynamic(() => import("@/components/common/MarkdownRenderer"), {
  ssr: false,
});

function stripFrontmatter(text: string): string {
  return text.replace(/^---\s*\n[\s\S]*?\n---\s*\n?/, "").trimStart();
}

export default function CommunitySkillPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const labels = useCommunityLabels();
  const timeAgo = useTimeAgo();
  const params = useParams<{ slug: string }>();
  const slug = decodeURIComponent(String(params?.slug ?? ""));
  const [skill, setSkill] = useState<CommunitySkillDetail | null>(null);
  const [error, setError] = useState("");
  const [installing, setInstalling] = useState(false);
  const ent = useEntitlements();
  const limitText = useLimitReachedText();
  const [editing, setEditing] = useState(false);
  const [discussing, setDiscussing] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [reporting, setReporting] = useState(false);

  const load = useCallback(async () => {
    try {
      setSkill(await getCommunitySkill(slug));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not load the skill"));
    }
  }, [slug, t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function install() {
    if (!skill) return;
    // Updating a copy you already have adds nothing to the count.
    if (!skill.installed_as && ent.atLimit("max_skills")) {
      notify(limitText("max_skills"), { tone: "error" });
      return;
    }
    setInstalling(true);
    try {
      let res;
      try {
        res = await installCommunitySkill(skill.slug, { force: Boolean(skill.installed_as) });
      } catch (e) {
        if (!(e instanceof CommunityApiError) || e.status !== 409) throw e;
        const replace = await confirmAction(
          t("You already have a skill named “{{name}}”. Replace it with this one?", { name: skill.slug }),
          { tone: "danger", confirmLabel: t("Replace") },
        );
        if (!replace) return;
        res = await installCommunitySkill(skill.slug, { force: true });
      }
      setSkill({ ...skill, installed_as: res.name, install_count: res.install_count });
      notify(t("Added “{{name}}” to your Skills.", { name: res.name }), { tone: "success" });
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Could not add the skill"), { tone: "error" });
    } finally {
      setInstalling(false);
    }
  }

  async function like() {
    if (!skill) return;
    try {
      const res = await toggleSkillLike(skill.slug);
      setSkill({ ...skill, liked: res.liked, like_count: res.like_count });
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  async function remove() {
    if (!skill) return;
    const ok = await confirmAction(t("Remove this skill from the community? People who added it keep their copy."), {
      tone: "danger",
      confirmLabel: t("Remove"),
    });
    if (!ok) return;
    try {
      await deleteCommunitySkill(skill.slug);
      notify(t("Skill removed."), { tone: "success" });
      router.push("/community/skills");
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Action failed"), { tone: "error" });
    }
  }

  const back = (
    <Link
      href="/community/skills"
      className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
    >
      <ArrowLeft size={14} /> {t("All skills")}
    </Link>
  );

  if (error && !skill) {
    return (
      <CommunityShell>
        {back}
        <p className="text-sm text-[var(--muted-foreground)]">{error}</p>
      </CommunityShell>
    );
  }
  if (!skill) {
    return (
      <CommunityShell>
        <div className="flex min-h-[240px] items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
        </div>
      </CommunityShell>
    );
  }

  const installed = Boolean(skill.installed_as);

  return (
    <CommunityShell>
      {back}
      <div className="grid gap-5 lg:grid-cols-[1fr_280px]">
        <article className="min-w-0 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6">
          <div className="mb-2 flex flex-wrap gap-1">
            <span className="rounded-md bg-[var(--primary)]/10 px-1.5 py-0.5 text-[11px] font-medium text-[var(--primary)]">
              {labels.skillCategory(skill.category)}
            </span>
            {skill.tags.map((tag) => (
              <span key={tag} className="rounded-md bg-[var(--muted)] px-1.5 py-0.5 text-[11px] text-[var(--muted-foreground)]">
                {tag}
              </span>
            ))}
            {skill.hidden && (
              <span className="rounded-md bg-destructive/10 px-1.5 py-0.5 text-[11px] text-destructive">
                {t("Hidden by a moderator")}
              </span>
            )}
          </div>
          <h2 className="text-[22px] font-semibold tracking-tight text-[var(--foreground)]">{skill.title}</h2>
          <p className="mt-1.5 text-[13.5px] leading-relaxed text-[var(--muted-foreground)]">{skill.description}</p>

          <div className="mt-5 flex items-center justify-between border-b border-[var(--border)] pb-2">
            <h3 className="text-[13px] font-semibold text-[var(--foreground)]">{t("Skill instructions")}</h3>
            <button
              onClick={() => setShowSource(!showSource)}
              className="text-[12px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            >
              {showSource ? t("Rendered") : t("SKILL.md source")}
            </button>
          </div>
          <div className="mt-4">
            {showSource ? (
              <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-[var(--muted)]/50 p-4 font-mono text-[12px] leading-relaxed text-[var(--foreground)]">
                {skill.content}
              </pre>
            ) : (
              <MarkdownRenderer content={stripFrontmatter(skill.content)} variant="prose" allowHtml={false} />
            )}
          </div>

          {skill.files.length > 0 && (
            <div className="mt-6">
              <h3 className="mb-2 text-[13px] font-semibold text-[var(--foreground)]">
                {t("Included files ({{count}})", { count: skill.files.length })}
              </h3>
              <ul className="space-y-1">
                {skill.files.map((f) => (
                  <li key={f} className="flex items-center gap-2 font-mono text-[12px] text-[var(--muted-foreground)]">
                    <FileText size={12} /> {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </article>

        <aside className="space-y-3">
          <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4">
            <button
              onClick={() => void install()}
              disabled={installing}
              className={`${installed ? secondaryButton : primaryButton} w-full`}
            >
              {installing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : installed ? (
                <Check size={14} />
              ) : (
                <Plus size={14} />
              )}
              {installed ? t("Added — update my copy") : t("Add to my Skills")}
            </button>
            {installed && (
              <Link
                href="/space/skills"
                className="mt-2 block text-center text-[12px] text-[var(--primary)] hover:underline"
              >
                {t("Open in Learning Space → Skills")}
              </Link>
            )}
            <div className="mt-4 grid grid-cols-2 gap-2 text-center">
              <button
                onClick={() => void like()}
                className={`rounded-lg border border-[var(--border)] py-2 text-[12.5px] hover:bg-[var(--muted)] ${
                  skill.liked ? "text-rose-500" : "text-[var(--foreground)]"
                }`}
              >
                <Heart size={14} className="mx-auto mb-0.5" fill={skill.liked ? "currentColor" : "none"} />
                {skill.like_count} {t("likes")}
              </button>
              <div className="rounded-lg border border-[var(--border)] py-2 text-[12.5px] text-[var(--foreground)]">
                <Download size={14} className="mx-auto mb-0.5" />
                {skill.install_count} {t("added")}
              </div>
            </div>
            <button onClick={() => setDiscussing(true)} className={`${secondaryButton} mt-2 w-full`}>
              <MessageSquarePlus size={14} /> {t("Discuss in the forum")}
            </button>
          </div>

          <div className="space-y-2 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4 text-[12.5px]">
            <div className="flex items-center gap-2">
              <UserInitial name={skill.author} size={28} />
              <div>
                <div className="font-medium text-[var(--foreground)]">{skill.author}</div>
                <div className="text-[11.5px] text-[var(--muted-foreground)]">{t("Author")}</div>
              </div>
            </div>
            <div className="flex justify-between text-[var(--muted-foreground)]">
              <span>{t("Version")}</span>
              <span className="text-[var(--foreground)]">v{skill.version}</span>
            </div>
            <div className="flex justify-between text-[var(--muted-foreground)]">
              <span>{t("Updated")}</span>
              <span className="text-[var(--foreground)]">{timeAgo(skill.updated_at)}</span>
            </div>
            <div className="flex justify-between text-[var(--muted-foreground)]">
              <span>{t("Shared")}</span>
              <span className="text-[var(--foreground)]">{timeAgo(skill.created_at)}</span>
            </div>
            {!skill.can_edit && (
              <button
                onClick={() => setReporting(true)}
                className="inline-flex items-center gap-1 pt-1 text-[12px] text-[var(--muted-foreground)] hover:text-destructive"
              >
                <Flag size={12} /> {t("Report this skill")}
              </button>
            )}
            {skill.can_edit && (
              <div className="flex gap-2 pt-2">
                <button onClick={() => setEditing(true)} className={`${secondaryButton} flex-1`}>
                  <Pencil size={13} /> {t("Edit")}
                </button>
                <button
                  onClick={() => void remove()}
                  className={`${secondaryButton} flex-1 text-destructive`}
                >
                  <Trash2 size={13} /> {t("Remove")}
                </button>
              </div>
            )}
          </div>
        </aside>
      </div>

      {reporting && (
        <ReportDialog targetType="skill" targetId={skill.id} onClose={() => setReporting(false)} />
      )}
      {editing && (
        <PublishSkillModal
          existing={skill}
          onClose={() => setEditing(false)}
          onDone={(updated) => {
            setEditing(false);
            setSkill({ ...skill, ...updated });
          }}
        />
      )}
      {discussing && (
        <PostComposerModal
          initial={{ category: "skills", skillSlug: skill.slug, title: skill.title }}
          onClose={() => setDiscussing(false)}
          onDone={(post) => router.push(`/community/posts/${post.id}`)}
        />
      )}
    </CommunityShell>
  );
}
