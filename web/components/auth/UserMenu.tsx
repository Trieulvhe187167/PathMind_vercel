"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeftCircle, Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { UserAvatar } from "@/components/UserAvatar";
import { useAuthSnapshot } from "@/hooks/useAuthStatus";

/**
 * The only item in the sidebar footer: the account card. It opens Settings
 * (Profile page); Sign out and Admin live at the bottom of the Settings
 * navigation. Without multi-user auth there is no account, so it falls back
 * to a plain Settings link.
 */
export function UserMenu({ collapsed = false }: { collapsed?: boolean }) {
  const { t } = useTranslation();
  const pathname = usePathname();
  // Shared, persisted auth store: the account card renders immediately on
  // every page and follows login / logout (also from other tabs).
  const snapshot = useAuthSnapshot();
  const resolved = snapshot !== undefined;
  const status =
    snapshot?.enabled && snapshot?.authenticated ? snapshot : null;

  const active = pathname.startsWith("/settings");
  const expandedClass = `flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] transition-colors ${
    active
      ? "bg-[var(--accent)] text-[var(--foreground)]"
      : "text-[var(--muted-foreground)] hover:bg-[var(--background)]/50 hover:text-[var(--foreground)]"
  }`;
  const collapsedClass = `mx-auto flex h-9 w-9 items-center justify-center rounded-xl transition-colors ${
    active ? "bg-[var(--accent)]" : "hover:bg-background/60"
  }`;

  // Administrators and R&D who open the learner app get a way straight back
  // to their own console (without sign-in the single local user is the admin).
  const consoleHref =
    resolved && snapshot
      ? !snapshot.enabled || snapshot.role === "admin"
        ? "/admin"
        : snapshot.role === "rnd"
          ? "/rnd"
          : null
      : null;
  const consoleLink = consoleHref ? (
    <Link
      href={consoleHref}
      title={consoleHref === "/rnd" ? t("Back to R&D console") : t("Back to Admin console")}
      aria-label={consoleHref === "/rnd" ? t("Back to R&D console") : t("Back to Admin console")}
      className={
        collapsed
          ? "mx-auto mb-1 flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--primary)] text-[var(--primary-foreground)] transition-opacity hover:opacity-90"
          : "mb-1 flex w-full items-center gap-2.5 rounded-lg bg-[var(--primary)] px-3 py-2 text-[13px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90"
      }
    >
      <ArrowLeftCircle size={16} strokeWidth={1.8} />
      {!collapsed && <span>{consoleHref === "/rnd" ? t("Back to R&D console") : t("Back to Admin console")}</span>}
    </Link>
  ) : null;

  if (!status?.username) {
    if (!resolved) return null;
    return (
      <>
      {consoleLink}
      <Link
        href="/settings/general"
        title={t("Settings")}
        aria-label={t("Settings")}
        className={collapsed ? collapsedClass : expandedClass}
      >
        <Settings size={16} strokeWidth={1.6} />
        {!collapsed && <span>{t("Settings")}</span>}
      </Link>
      </>
    );
  }

  return (
    <>
    {consoleLink}
    <Link
      href="/settings/profile"
      title={`${status.username} — ${t("Settings")}`}
      aria-label={`${status.username} — ${t("Settings")}`}
      className={collapsed ? collapsedClass : expandedClass}
    >
      <UserAvatar
        username={status.username}
        userId={status.user_id}
        avatar={status.avatar}
        role={status.role}
        size={collapsed ? 18 : 16}
      />
      {!collapsed && <span className="min-w-0 flex-1 truncate">{status.username}</span>}
    </Link>
    </>
  );
}
