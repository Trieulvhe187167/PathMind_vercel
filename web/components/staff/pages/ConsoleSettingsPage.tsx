"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";

import { SETTINGS_SECTIONS } from "@/components/settings/SettingsPageContent";
import { SettingsLoadStatusBanner } from "@/components/settings/SettingsLoadStatusBanner";
import { SettingsToolbar } from "@/components/settings/SettingsToolbar";
import { SettingsAccessProvider } from "@/features/settings/navigation/SettingsAccessProvider";
import { isSystemSettingsKey, isWideSettingsPage } from "@/features/settings/navigation/settings-pages";
import { resolveSettingsKey } from "@/features/settings/navigation/settings-nav";
import {
  ModelCatalogProvider,
  SettingsDraftProvider,
  SettingsProvider,
  UiSettingsProvider,
} from "@/features/settings/store";
import { useSettings } from "@/features/settings/store/SettingsStore";

/** System configuration pages of the admin console, in sidebar order. */
export const CONSOLE_CONFIG_PAGES: { key: string; label: string }[] = [
  { key: "connections", label: "Providers & API keys" },
  { key: "llm", label: "Language models" },
  { key: "task-models", label: "Task models" },
  { key: "embedding", label: "Embedding models" },
  { key: "search", label: "Web search" },
  { key: "voice", label: "Voice" },
  { key: "multimodal", label: "Multimodal generation" },
  { key: "capabilities", label: "Capabilities" },
  { key: "knowledge", label: "Knowledge & documents" },
  { key: "starters", label: "Starting points" },
  { key: "status", label: "Runtime status" },
];

function Body({ section }: { section: string }) {
  const { t } = useTranslation();
  const { setActiveSection, settingsLoading, saving, applying } = useSettings();
  const key = resolveSettingsKey(section);
  const Component = isSystemSettingsKey(key) || key === "starters" ? SETTINGS_SECTIONS[key] : undefined;
  const label = CONSOLE_CONFIG_PAGES.find((p) => p.key === key)?.label;

  useEffect(() => {
    setActiveSection(key);
  }, [key, setActiveSection]);

  return (
    <div
      className="flex h-full min-w-0 flex-col overflow-hidden"
      style={{ "--settings-content": isWideSettingsPage(key) ? "1280px" : "960px" } as React.CSSProperties}
    >
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden [scrollbar-gutter:stable]">
        <div className="mx-auto w-full max-w-[var(--settings-content,960px)] px-5 pb-20 pt-8 sm:px-10">
          <p className="mb-4 text-[11px] uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
            {t("Configuration")}{label ? ` · ${t(label)}` : ""}
          </p>
          <SettingsLoadStatusBanner />
          {!Component ? (
            <p className="text-sm">
              {t("This settings page is unavailable.")}{" "}
              <Link href="/admin/config/connections" className="underline">
                {t("Providers & API keys")}
              </Link>
            </p>
          ) : settingsLoading ? (
            <div className="min-h-48 animate-pulse rounded-xl bg-[var(--muted)]/30" aria-busy="true" />
          ) : (
            <fieldset disabled={saving || applying} aria-busy={saving || applying} className="min-w-0" data-settings-page={key}>
              <Component key={key} />
            </fieldset>
          )}
        </div>
      </div>
      <SettingsToolbar />
    </div>
  );
}

/**
 * System settings (providers, models, search, voice, capabilities...) in the
 * admin console. Same sections, drafts and apply flow as the app's Settings,
 * which now keeps only personal pages.
 */
export default function ConsoleSettingsPage({ section }: { section: string }) {
  return (
    <SettingsProvider>
      <SettingsAccessProvider mode="console">
        <UiSettingsProvider>
          <ModelCatalogProvider>
            <SettingsDraftProvider>
              <Body section={section} />
            </SettingsDraftProvider>
          </ModelCatalogProvider>
        </UiSettingsProvider>
      </SettingsAccessProvider>
    </SettingsProvider>
  );
}
