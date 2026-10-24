"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SettingRow, SettingSection, SettingsPageHeader } from "./shared";
import { useUiSettings } from "@/features/settings/store";
import { useSettings } from "@/features/settings/store/SettingsStore";
import { useSettingsAccess } from "@/features/settings/navigation/SettingsAccessProvider";

/** Shared control for interface and model output languages. */
function LanguageToggle({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: "en" | "zh" | "vi") => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex gap-0.5 rounded-lg bg-[var(--muted)] p-0.5">
      {(["en", "zh", "vi"] as const).map((option) => (
        <button
          key={option}
          aria-pressed={value === option}
          onClick={() => onChange(option)}
          className={`rounded-md px-2.5 py-1 text-[12px] transition-all ${
            value === option
              ? "bg-[var(--card)] font-medium text-[var(--foreground)] shadow-sm"
              : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          }`}
        >
          {t({ en: "language.english", zh: "language.chinese", vi: "language.vietnamese" }[option])}
        </button>
      ))}
    </div>
  );
}

export default function SettingsOverview() {
  const { t } = useTranslation();
  const { language, responseLanguage, updateLanguage, updateResponseLanguage } =
    useUiSettings();
  const { catalogEditable } = useSettings();
  const access = useSettingsAccess();
  // Provider/model setup is system configuration: it lives in the admin console
  // (/admin/config/...), so the app's General page only keeps personal options.
  const showSetup = catalogEditable && !access.hideAdminOnly;
  return (
    <div>
      <SettingsPageHeader
        title={t("General")}
        description={t(
          "Make PathMind feel at home. Apply your preferences using the bar below.",
        )}
      />
      <SettingSection title={t("Language")}>
        <SettingRow
          title={t("Interface language")}
          description={t(
            "Controls navigation, settings, and status text only.",
          )}
          control={
            <LanguageToggle value={language} onChange={updateLanguage} />
          }
        />
        <SettingRow
          title={t("Model output language")}
          description={t(
            "Sets the default language for chat and capability responses.",
          )}
          control={
            <LanguageToggle
              value={responseLanguage}
              onChange={updateResponseLanguage}
            />
          }
        />
      </SettingSection>
      {showSetup && <SettingSection
        title={t("Set up chat first")}
        description={t("Connect a provider and choose a language model. Other services are optional.")}
      >
        {[
          {
            href: "/admin/config/connections",
            title: t("1. Connect a provider"),
            description: t("Add your provider and credentials, then test the connection."),
          },
          {
            href: "/admin/config/llm",
            title: t("2. Choose a chat model"),
            description: t("Add a language model, test it, and select it as the default for conversations."),
          },
          {
            href: "/admin/config/status",
            title: t("3. Apply and check"),
            description: t("Apply your changes using the bottom bar, then check service status before starting a conversation."),
          },
        ].map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="group flex items-center justify-between gap-5 border-b border-[var(--border)]/50 py-4 last:border-0 rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--ring)]"
          >
            <div>
              <div className="text-[14px] font-medium">{item.title}</div>
              <p className="mt-1 text-[13px] leading-relaxed text-[var(--muted-foreground)]">
                {item.description}
              </p>
            </div>
            <ArrowRight size={17} className="shrink-0 text-[var(--muted-foreground)]" />
          </Link>
        ))}
      </SettingSection>}
      {showSetup && <SettingSection
        title={t("Add more when you need it")}
        description={t("These services are not required for basic chat.")}
      >
        {[
          { href: "/admin/config/embedding", title: t("Knowledge search"), description: t("Configure an embedding model before indexing a knowledge base.") },
          { href: "/admin/config/search", title: t("Web search"), description: t("Connect a search service to find information on the web.") },
          { href: "/admin/config/task-models", title: t("Background task models"), description: t("Choose models for automatic tasks such as titles and suggestions.") },
        ].map(item => (
          <Link key={item.href} href={item.href} className="flex items-center justify-between gap-5 border-b border-[var(--border)]/50 py-4 last:border-0 rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--ring)]">
            <div>
              <div className="text-[14px] font-medium">{item.title}</div>
              <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">{item.description}</p>
            </div>
            <ArrowRight size={17} className="shrink-0 text-[var(--muted-foreground)]" />
          </Link>
        ))}
      </SettingSection>}
    </div>
  );
}
