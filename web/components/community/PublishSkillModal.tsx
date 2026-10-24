"use client";

import { useEntitlements } from "@/components/access/EntitlementsContext";
import { PlanLockedDialog } from "@/components/access/PlanGate";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { FilePlus2, FileUp, Loader2, PenLine, Wand2, Type } from "lucide-react";

import { Modal, fieldClass, primaryButton, secondaryButton, useCommunityLabels } from "@/components/community/community-ui";
import {
  createCoWriterDocument,
  listCoWriterDocuments,
  type CoWriterDocumentSummary,
} from "@/lib/co-writer-api";
import {
  SKILL_CATEGORIES,
  publishCommunitySkill,
  updateCommunitySkill,
  updateCommunitySkillUpload,
  uploadCommunitySkill,
  type CommunitySkillDetail,
  type SkillSource,
} from "@/lib/community-api";
import { notify } from "@/lib/notifications";
import { listSkills, type SkillInfo } from "@/lib/skills-api";

type Source = "keep" | "skill" | "file" | "cowriter" | "text";

export interface PublishInitial {
  source?: Exclude<Source, "keep">;
  skillName?: string;
  docId?: string;
  content?: string;
  title?: string;
  description?: string;
}

function humanize(slug: string): string {
  const text = slug.replace(/-/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const SKILL_TEMPLATE = `---
name: my-skill
description: One sentence — when should the AI use this skill?
---

# My skill

## When to use
Describe the situations where this playbook applies.

## How to help
1. First step the tutor should take.
2. Next step.

## Example
**Learner:** …
**Tutor:** …
`;

interface PublishSkillModalProps {
  onClose: () => void;
  onDone: (skill: CommunitySkillDetail) => void;
  initial?: PublishInitial;
  /** Update mode: the listing being edited. */
  existing?: CommunitySkillDetail;
}

/** Sharing needs the Community (paid plans); Free sees an upgrade note. */
export default function PublishSkillModal(props: PublishSkillModalProps) {
  const { has } = useEntitlements();
  if (!has("community")) {
    return (
      <PlanLockedDialog open onClose={props.onClose} title="Community" target="community" />
    );
  }
  return <PublishSkillDialog {...props} />;
}

function PublishSkillDialog({ onClose, onDone, initial, existing }: PublishSkillModalProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const labels = useCommunityLabels();
  const [source, setSource] = useState<Source>(existing ? "keep" : initial?.source ?? "skill");
  const [mySkills, setMySkills] = useState<SkillInfo[]>([]);
  const [docs, setDocs] = useState<CoWriterDocumentSummary[]>([]);
  const [skillName, setSkillName] = useState(initial?.skillName ?? "");
  const [docId, setDocId] = useState(initial?.docId ?? "");
  const [content, setContent] = useState(initial?.content ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState(existing?.title ?? initial?.title ?? "");
  const [titleTouched, setTitleTouched] = useState(false);
  const [description, setDescription] = useState(existing?.description ?? initial?.description ?? "");
  const [category, setCategory] = useState(existing?.category ?? "academics");
  const [tags, setTags] = useState((existing?.tags ?? []).join(", "));
  const [busy, setBusy] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [skills, documents] = await Promise.all([
        listSkills({ force: true }).catch(() => [] as SkillInfo[]),
        listCoWriterDocuments().catch(() => [] as CoWriterDocumentSummary[]),
      ]);
      if (cancelled) return;
      const own = skills.filter((s) => (s.source ?? "user") === "user");
      setMySkills(own);
      setDocs(documents);
      const firstSkill = own.find((s) => s.name === initial?.skillName) ?? own[0];
      const firstDoc = documents.find((d) => d.id === initial?.docId) ?? documents[0];
      setSkillName((prev) => prev || firstSkill?.name || "");
      setDocId((prev) => prev || firstDoc?.id || "");
      if (!existing && (initial?.source ?? "skill") === "skill" && firstSkill) {
        setTitle((prev) => prev || humanize(firstSkill.name));
        setDescription((prev) => prev || firstSkill.description);
      }
      if (!existing && initial?.source === "cowriter" && firstDoc) {
        setTitle((prev) => prev || firstDoc.title);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Runs once: later picks go through pickSkill / pickDoc.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pickSkill(name: string) {
    setSkillName(name);
    const chosen = mySkills.find((s) => s.name === name);
    if (!existing && chosen) {
      setTitle(humanize(chosen.name));
      setDescription(chosen.description);
    }
  }

  function pickDoc(id: string) {
    setDocId(id);
    const chosen = docs.find((d) => d.id === id);
    if (!existing && chosen) setTitle(chosen.title);
  }

  const tagList = tags
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);

  const sources: { id: Source; label: string; icon: typeof Wand2 }[] = [
    ...(existing ? [{ id: "keep" as Source, label: t("Keep current content"), icon: Wand2 }] : []),
    { id: "skill", label: t("My skills"), icon: Wand2 },
    { id: "file", label: t("Upload file"), icon: FileUp },
    { id: "cowriter", label: t("Co-Writer"), icon: PenLine },
    { id: "text", label: t("Write here"), icon: Type },
  ];

  const canSubmit =
    title.trim().length > 0 &&
    !busy &&
    (source === "keep" ||
      (source === "skill" && Boolean(skillName)) ||
      (source === "cowriter" && Boolean(docId)) ||
      (source === "file" && Boolean(file)) ||
      (source === "text" && content.trim().length > 0));

  async function startCoWriterDraft() {
    setDrafting(true);
    try {
      const doc = await createCoWriterDocument({ title: (titleTouched && title.trim()) || t("New skill"), content: SKILL_TEMPLATE });
      notify(t("Write your skill in Co-Writer, then share it from the Co-Writer toolbar."), { tone: "info" });
      router.push(`/co-writer/${doc.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Action failed"));
      setDrafting(false);
    }
  }

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError("");
    const meta = {
      title: title.trim(),
      description: description.trim(),
      category,
      tags: tagList,
    };
    try {
      let result: CommunitySkillDetail;
      if (existing) {
        if (source === "file" && file) {
          await updateCommunitySkillUpload(existing.slug, file);
          result = await updateCommunitySkill(existing.slug, meta);
        } else if (source === "keep") {
          result = await updateCommunitySkill(existing.slug, meta);
        } else {
          result = await updateCommunitySkill(existing.slug, {
            ...meta,
            source: source as SkillSource,
            skill_name: skillName,
            doc_id: docId,
            content,
          });
        }
      } else if (source === "file" && file) {
        result = await uploadCommunitySkill(file, meta);
      } else {
        result = await publishCommunitySkill({
          ...meta,
          source: source as "skill" | "cowriter" | "text",
          skill_name: source === "skill" ? skillName : undefined,
          doc_id: source === "cowriter" ? docId : undefined,
          content: source === "text" ? content : undefined,
        });
      }
      if (result.skipped_files && result.skipped_files.length > 0) {
        notify(
          t("Some files were not included: {{files}}", {
            files: result.skipped_files.slice(0, 5).join(", "),
          }),
          { tone: "info" },
        );
      }
      notify(existing ? t("Skill updated.") : t("Skill shared with the community."), { tone: "success" });
      onDone(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not share the skill"));
      setBusy(false);
    }
  }

  return (
    <Modal title={existing ? t("Update shared skill") : t("Share a skill")} onClose={onClose} wide>
      <div className="space-y-4">
        <div>
          <div className="mb-1.5 text-[12px] font-medium text-[var(--muted-foreground)]">
            {existing ? t("Content") : t("Share from")}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {sources.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => setSource(id)}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12.5px] transition-colors ${
                  source === id
                    ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--foreground)]"
                    : "border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                }`}
              >
                <Icon size={13} /> {label}
              </button>
            ))}
          </div>
        </div>

        {source === "skill" &&
          (mySkills.length === 0 ? (
            <p className="rounded-lg bg-[var(--muted)]/60 p-3 text-[12.5px] text-[var(--muted-foreground)]">
              {t("You have no skills of your own yet. Create one in Learning Space → Skills, or pick another source.")}
            </p>
          ) : (
            <select value={skillName} onChange={(e) => pickSkill(e.target.value)} className={fieldClass}>
              {mySkills.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
          ))}

        {source === "file" && (
          <div
            className="flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border border-dashed border-[var(--border)] p-5 text-center hover:bg-[var(--muted)]/40"
            onClick={() => fileInput.current?.click()}
          >
            <FileUp size={20} className="text-[var(--muted-foreground)]" />
            <div className="text-[13px] font-medium text-[var(--foreground)]">
              {file ? file.name : t("Choose SKILL.md, a .md file or a .zip skill package")}
            </div>
            <div className="text-[11.5px] text-[var(--muted-foreground)]">
              {t("Text files only; up to 5 MB.")}
            </div>
            <input
              ref={fileInput}
              type="file"
              accept=".md,.markdown,.txt,.zip"
              className="hidden"
              onChange={(e) => {
                const picked = e.target.files?.[0] ?? null;
                setFile(picked);
                if (picked && !title) setTitle(picked.name.replace(/\.(md|markdown|txt|zip)$/i, ""));
              }}
            />
          </div>
        )}

        {source === "cowriter" && (
          <div className="space-y-2">
            {docs.length === 0 ? (
              <p className="rounded-lg bg-[var(--muted)]/60 p-3 text-[12.5px] text-[var(--muted-foreground)]">
                {t("You have no Co-Writer documents yet.")}
              </p>
            ) : (
              <select value={docId} onChange={(e) => pickDoc(e.target.value)} className={fieldClass}>
                {docs.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title}
                  </option>
                ))}
              </select>
            )}
            <button
              type="button"
              disabled={drafting}
              onClick={() => void startCoWriterDraft()}
              className="inline-flex items-center gap-1.5 text-[12.5px] text-[var(--primary)] hover:underline disabled:opacity-50"
            >
              {drafting ? <Loader2 size={13} className="animate-spin" /> : <FilePlus2 size={13} />}
              {t("Start a new skill draft in Co-Writer")}
            </button>
          </div>
        )}

        {source === "text" && (
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={10}
            placeholder={SKILL_TEMPLATE}
            className={`${fieldClass} font-mono text-[12.5px] leading-relaxed`}
          />
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-[12px] font-medium text-[var(--muted-foreground)]">{t("Title")}</span>
            <input
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                setTitleTouched(true);
              }}
              maxLength={120}
              placeholder={t("e.g. Socratic Tutor")}
              className={fieldClass}
            />
          </label>
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-[12px] font-medium text-[var(--muted-foreground)]">
              {t("Description")}
            </span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              maxLength={500}
              placeholder={t("When should the AI use this skill? (leave empty to use the one in SKILL.md)")}
              className={fieldClass}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[12px] font-medium text-[var(--muted-foreground)]">{t("Category")}</span>
            <select value={category} onChange={(e) => setCategory(e.target.value)} className={fieldClass}>
              {SKILL_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {labels.skillCategory(c)}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-[12px] font-medium text-[var(--muted-foreground)]">{t("Tags")}</span>
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder={t("math, exam prep")}
              className={fieldClass}
            />
          </label>
        </div>

        <p className="text-[11.5px] text-[var(--muted-foreground)]">
          {t("Everyone on PathMind can see and add shared skills. Don't include personal data or API keys.")}
        </p>

        {error && <p className="text-[12.5px] text-destructive">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryButton}>
            {t("Cancel")}
          </button>
          <button type="button" disabled={!canSubmit} onClick={() => void submit()} className={primaryButton}>
            {busy && <Loader2 size={14} className="animate-spin" />}
            {existing ? t("Save changes") : t("Share")}
          </button>
        </div>
      </div>
    </Modal>
  );
}
