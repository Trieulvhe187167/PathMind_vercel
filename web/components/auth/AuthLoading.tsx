"use client";

import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

/** Full-screen fallback while the sign-in portal loads. */
export function AuthLoading() {
  const { t } = useTranslation();
  return (
    <div className="min-h-screen w-full bg-[var(--background)] flex items-center justify-center text-sm text-[var(--muted-foreground)]">
      <div className="flex items-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin text-[var(--primary)]" />
        <span>{t("Loading PathMind…")}</span>
      </div>
    </div>
  );
}
