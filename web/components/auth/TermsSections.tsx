"use client";

import { useTranslation } from "react-i18next";

// English source text doubles as the i18n key (see locales/*/app.json).
export const TERMS_SECTIONS: { title: string; body: string }[] = [
  {
    title: "Your account",
    body: "Provide accurate information when you sign up and keep your password private. You are responsible for activity on your account.",
  },
  {
    title: "Acceptable use",
    body: "Use PathMind for learning. Do not upload unlawful content, try to disrupt the service, or access other people's data.",
  },
  {
    title: "AI-generated content",
    body: "Answers are produced by AI models and can be wrong. Check important information against reliable sources.",
  },
  {
    title: "Your data",
    body: "Your chats, notes and files are stored to provide the service. Administrators of this installation can manage accounts and usage limits. You can permanently delete your account and its data at any time in Settings → Profile.",
  },
  {
    title: "Plans and usage limits",
    body: "Each plan includes a usage quota. Paid features depend on your active subscription and may change with notice.",
  },
  {
    title: "Changes",
    body: "These terms may be updated. Continuing to use PathMind after an update means you accept the new terms.",
  },
];

/** The numbered Terms of Use sections, shared by /terms and the sign-up dialog. */
export function TermsSections() {
  const { t } = useTranslation();
  return (
    <ol className="space-y-6">
      {TERMS_SECTIONS.map((section, index) => (
        <li key={section.title}>
          <h2 className="text-base font-semibold">
            {index + 1}. {t(section.title)}
          </h2>
          <p className="mt-1.5 text-sm leading-relaxed text-[var(--muted-foreground)]">
            {t(section.body)}
          </p>
        </li>
      ))}
    </ol>
  );
}
