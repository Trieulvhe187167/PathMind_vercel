"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslation } from "react-i18next";
import { MessagesSquare, ShieldCheck, Store } from "lucide-react";

import NotificationBell from "@/components/community/NotificationBell";
import { useAuthStatus } from "@/hooks/useAuthStatus";

/** Page frame: Forum / Skill Hub (/ Moderation) switch, notifications bell. */
export function CommunityShell({
  children,
  actions,
}: {
  children: ReactNode;
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
  const { isAdmin } = useAuthStatus();
  const pathname = usePathname() ?? "";
  const section = pathname.startsWith("/community/skills")
    ? "skills"
    : pathname.startsWith("/community/moderation")
      ? "moderation"
      : "forum";
  const tab = (active: boolean) =>
    `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors ${
      active
        ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
        : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
    }`;
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 py-6 md:px-6">
        <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-[19px] font-semibold tracking-tight text-[var(--foreground)]">
              {t("Community")}
            </h1>
            <p className="mt-0.5 text-[12.5px] text-[var(--muted-foreground)]">
              {t("Share skills, ask questions and help each other learn.")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <nav className="flex rounded-xl bg-[var(--muted)] p-1">
              <Link href="/community" className={tab(section === "forum")}>
                <MessagesSquare size={14} /> {t("Forum")}
              </Link>
              <Link href="/community/skills" className={tab(section === "skills")}>
                <Store size={14} /> {t("Skill Hub")}
              </Link>
              {isAdmin && (
                <Link href="/community/moderation" className={tab(section === "moderation")}>
                  <ShieldCheck size={14} /> {t("Moderation")}
                </Link>
              )}
            </nav>
            <NotificationBell />
            {actions}
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}
