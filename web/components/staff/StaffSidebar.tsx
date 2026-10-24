"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  FileText,
  Boxes,
  Cpu,
  KeyRound,
  CalendarClock,
  ExternalLink,
  Gift,
  HardDrive,
  Inbox,
  LogOut,
  Network,
  Receipt,
  ScrollText,
  SlidersHorizontal,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useSidebarDrawer } from "@/components/layout/AppShell";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { getAdminStats } from "@/lib/admin-api";
import { logout } from "@/lib/auth";

import type { StaffConsole } from "@/components/admin/StaffGuard";
import { CONSOLE_CONFIG_PAGES } from "@/components/staff/pages/ConsoleSettingsPage";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

// Each console has its own map: R&D curates knowledge under /rnd,
// administrators run the business and the system under /admin (and only read
// the knowledge graph). Neither shares a navigation with learners or educators.
export const CONSOLE_NAV: Record<StaffConsole, NavGroup[]> = {
  rnd: [
    {
      label: "Knowledge",
      items: [
        { href: "/rnd/knowledge", label: "Main knowledge graph", icon: Network },
        { href: "/rnd/samples", label: "Samples", icon: FileText },
        { href: "/rnd/reviews", label: "Branch reviews", icon: Inbox },
        { href: "/rnd/rewards", label: "Rewards", icon: Gift },
      ],
    },
    {
      label: "System",
      items: [{ href: "/rnd/status", label: "System Status", icon: Activity }],
    },
  ],
  admin: [
    {
      label: "Business",
      items: [
        { href: "/admin/users", label: "Users", icon: Users },
        { href: "/admin/plans", label: "Plans", icon: Boxes },
        { href: "/admin/models", label: "Models", icon: Cpu },
        { href: "/admin/payments", label: "Payments", icon: Receipt },
        { href: "/admin/insights", label: "Insights", icon: BarChart3 },
        { href: "/admin/rewards", label: "Rewards", icon: Gift },
      ],
    },
    {
      label: "Knowledge (read-only)",
      items: [
        { href: "/admin/knowledge", label: "Main knowledge graph", icon: Network },
        { href: "/admin/reviews", label: "Branch reviews", icon: Inbox },
        { href: "/admin/samples", label: "Samples", icon: FileText },
      ],
    },
    {
      // System configuration that used to sit in the app's Settings.
      label: "Configuration",
      items: CONSOLE_CONFIG_PAGES.map((page) => ({
        href: `/admin/config/${page.key}`,
        label: page.label,
        icon: page.key === "connections" ? KeyRound : SlidersHorizontal,
      })),
    },
    {
      label: "System",
      items: [
        { href: "/admin/status", label: "System Status", icon: Activity },
        { href: "/admin/settings", label: "System settings", icon: SlidersHorizontal },
        { href: "/admin/jobs", label: "Jobs", icon: CalendarClock },
        { href: "/admin/storage", label: "Storage", icon: HardDrive },
        { href: "/admin/audit", label: "Audit log", icon: ScrollText },
      ],
    },
  ],
};

/** Navigation of one staff console (``/admin`` or ``/rnd``). */
export default function StaffSidebar({ console: owner }: { console: StaffConsole }) {
  const { t } = useTranslation();
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const drawer = useSidebarDrawer();
  const auth = useAuthStatus();
  const [pendingPayments, setPendingPayments] = useState(0);

  useEffect(() => {
    if (owner !== "admin") return;
    let alive = true;
    getAdminStats()
      .then((stats) => alive && setPendingPayments(stats.pending_payments || 0))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [owner, pathname]);

  const signOut = async () => {
    await logout();
    router.replace("/login");
  };

  return (
    <aside className="flex h-dvh w-[240px] shrink-0 flex-col border-r border-[var(--border)] bg-[var(--secondary)] max-md:max-w-[85vw]">
      <div className="flex h-[56px] shrink-0 items-center gap-2 px-4">
        <Image src="/logo.png" unoptimized alt="" width={22} height={22} className="h-[22px] w-[22px] object-contain" />
        <div className="min-w-0 leading-tight">
          <div className="text-[13px] font-semibold text-[var(--foreground)]">PathMind</div>
          <div className="text-[10px] uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
            {owner === "rnd" ? t("R&D console") : t("Admin console")}
          </div>
        </div>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-3" aria-label={owner === "rnd" ? t("R&D console") : t("Admin console")}>
        {CONSOLE_NAV[owner].map((group) => {
          const items = group.items;
          return (
            <section key={group.label} className="mt-3">
              <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                {t(group.label)}
              </div>
              {items.map(({ href, label, icon: Icon }) => {
                const active = pathname === href || pathname.startsWith(`${href}/`);
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={() => drawer?.close()}
                    aria-current={active ? "page" : undefined}
                    className={`relative flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] transition-colors ${
                      active
                        ? "bg-[var(--card)] font-semibold text-[var(--foreground)] shadow-sm"
                        : "text-[var(--muted-foreground)] hover:bg-[var(--card)]/60 hover:text-[var(--foreground)]"
                    }`}
                  >
                    {active && <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-[var(--primary)]" />}
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 flex-1 truncate">{t(label)}</span>
                    {href === "/admin/payments" && pendingPayments > 0 && (
                      <span className="rounded-full bg-[var(--primary)] px-1.5 text-[10px] font-bold text-[var(--primary-foreground)]">
                        {pendingPayments}
                      </span>
                    )}
                  </Link>
                );
              })}
            </section>
          );
        })}
      </nav>

      <div className="shrink-0 space-y-1 border-t border-[var(--border)] px-2 py-2">
        <div className="flex items-center gap-2 px-2 py-1.5">
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium text-[var(--foreground)]">{auth.username || t("Local")}</div>
          </div>
          <span className="shrink-0 rounded-md border border-[var(--border)] bg-[var(--card)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--primary)]">
            {owner === "rnd" ? t("R&D") : t("Admin")}
          </span>
        </div>
        <Link
          href="/chat"
          className="flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[12px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--card)]/60 hover:text-[var(--foreground)]"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          {t("Open learner app")}
        </Link>
        {auth.enabled && (
          <button
            type="button"
            onClick={() => void signOut()}
            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[12px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--card)]/60 hover:text-[var(--foreground)]"
          >
            <LogOut className="h-3.5 w-3.5" />
            {t("Sign out")}
          </button>
        )}
      </div>
    </aside>
  );
}
