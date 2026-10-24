"use client";

/**
 * The workspace feature list — the part of the sidebar a learner owns.
 *
 * Every learner uses a different half of PathMind, so the shipped list is a
 * starting point rather than a layout: rows can be dragged into the order the
 * work actually happens in. Every feature is always listed (there is no
 * "More" group). The order is a per-machine view preference
 * (``lib/sidebar-layout.ts``).
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { Lock, MoreHorizontal, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useCapabilityAccess } from "@/components/access/CapabilityAccessContext";
import { useEntitlements } from "@/components/access/EntitlementsContext";
import { isPlanLocked, usePlanLockedText } from "@/components/access/PlanGate";
import {
  NAV_BY_HREF,
  PRIMARY_NAV_HREFS,
  isNavActive,
} from "@/components/sidebar/nav-entries";
import { Tooltip } from "@/components/ui/Tooltip";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { useCommunityUnread } from "@/hooks/useCommunityUnread";
import { useDragSort, type DragSort } from "@/hooks/useDragSort";
import { PRICING_HREF } from "@/lib/entitlements-api";
import { requirementKey } from "@/lib/plan-routes";
import { placeMenu, type FloatingMenuPosition } from "@/lib/floating-menu";
import {
  readNavLayout,
  reorderNavSection,
  resolveNavLayout,
  writeNavLayout,
  type SidebarNavLayout,
} from "@/lib/sidebar-layout";

const MODULE_NAV_HREFS = PRIMARY_NAV_HREFS.filter((href) => href !== "/chat");

const MENU_WIDTH = 210;
const COMMUNITY_HREF = "/community";

/** Unread Community notifications, once the account is known. */
function useSignedInCommunityUnread(): number {
  const { enabled, authenticated, loading } = useAuthStatus();
  const ent = useEntitlements();
  // Free plans have no Community, so don't poll a route that answers 402.
  const entitled = !!ent.data && ent.has("community");
  return useCommunityUnread(entitled && !loading && (!enabled || authenticated));
}

/** Tooltip text when the plan (not the model grant) locks this entry. */
function usePlanLock(): (href: string) => string | null {
  const ent = useEntitlements();
  const text = usePlanLockedText();
  return (href: string) => {
    const req = NAV_BY_HREF.get(href)?.plan;
    return req && isPlanLocked(ent, req) ? text(requirementKey(req)) : null;
  };
}

interface RowMenu {
  href: string;
  position: FloatingMenuPosition;
}

interface SidebarNavProps {
  /** Icon-only rail. Rows there are not arrangeable — see the rail branch. */
  collapsed: boolean;
  scrollRef?: RefObject<HTMLElement | null>;
  /** Home resets to a fresh session rather than just navigating. */
  onHomeClick: (event: React.MouseEvent) => void;
  /** Dismisses the mobile drawer on in-place navigation. */
  onNavigate: (event: React.MouseEvent) => void;
}

export function SidebarNav({
  collapsed,
  scrollRef,
  onHomeClick,
  onNavigate,
}: SidebarNavProps) {
  const pathname = usePathname();
  const { t } = useTranslation();
  const { has } = useCapabilityAccess();
  const communityUnread = useSignedInCommunityUnread();
  const planLock = usePlanLock();
  const auth = useAuthStatus();
  /** Role-gated entries (the main knowledge tree is hidden from learners). */
  const roleAllows = useCallback(
    (href: string) => {
      const roles = NAV_BY_HREF.get(href)?.roles;
      if (!roles) return true;
      if (!auth.enabled) return true;
      const learner = !auth.isAdmin && !auth.isRnd && !auth.isEducator;
      return (
        (auth.isAdmin && roles.includes('admin')) ||
        (auth.isRnd && roles.includes('rnd')) ||
        (auth.isEducator && roles.includes('educator')) ||
        (learner && roles.includes('user'))
      );
    },
    [auth.enabled, auth.isAdmin, auth.isRnd, auth.isEducator],
  );

  const [layout, setLayout] = useState<SidebarNavLayout | null>(null);
  const [menu, setMenu] = useState<RowMenu | null>(null);
  const [railMenu, setRailMenu] = useState<FloatingMenuPosition | null>(null);
  const menuRootRef = useRef<HTMLDivElement>(null);
  const menuAnchorRef = useRef<HTMLElement | null>(null);

  // Hydrate after first paint so the server and the client agree on the
  // shipped order, then settle into this machine's arrangement.
  useEffect(() => {
    if (typeof window === "undefined") return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLayout(readNavLayout());
  }, []);

  const resolved = useMemo(
    // Folding into "More" is gone: ignore any stored folded list so every
    // feature (Co-Writer included) is always visible.
    () =>
      resolveNavLayout(
        MODULE_NAV_HREFS,
        layout ? { order: layout.order, collapsed: [] } : null,
        [],
      ),
    [layout],
  );
  /** Always edit the resolved order: the stored one may still be empty. */
  const editable = useMemo<SidebarNavLayout>(
    () => ({ order: resolved.order, collapsed: resolved.collapsed }),
    [resolved],
  );

  const applyLayout = useCallback((next: SidebarNavLayout) => {
    setLayout(next);
    writeNavLayout(next);
  }, []);

  const closeMenus = useCallback(() => {
    setMenu(null);
    setRailMenu(null);
    menuAnchorRef.current = null;
  }, []);

  const visibleDrag = useDragSort({
    ids: resolved.visible,
    disabled: collapsed,
    scrollRef,
    onReorder: (next) =>
      applyLayout(reorderNavSection(editable, resolved.visible, next)),
  });
  useEffect(() => {
    if (!menu && !railMenu) return;
    const closeOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        !menuRootRef.current?.contains(target) &&
        !menuAnchorRef.current?.contains(target)
      ) {
        closeMenus();
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenus();
    };
    const closeOnViewportChange = (event: Event) => {
      const target = event.target;
      if (target instanceof Node && menuRootRef.current?.contains(target))
        return;
      closeMenus();
    };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeMenus);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("mousedown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeMenus);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [closeMenus, menu, railMenu]);

  const lockedTooltip = t("Locked — contact your administrator to get access.");
  const isLocked = (href: string) => {
    const requires = NAV_BY_HREF.get(href)?.requires;
    return requires ? !has(requires) : false;
  };

  /* ---- Icon-only rail ----
   * No arranging here: the rail is 60px of icons with no room for a menu or a
   * drop target. It honours the saved order and shows every feature. */
  if (collapsed) {
    return (
      <nav className="mt-1 flex w-full flex-col items-center gap-1 px-1.5">
        {resolved.visible.filter(roleAllows).map((href) => (
          <RailRow
            key={href}
            href={href}
            active={isNavActive(pathname, href)}
            locked={isLocked(href)}
            lockedTooltip={lockedTooltip}
            planLocked={planLock(href)}
            onHomeClick={onHomeClick}
          />
        ))}
      </nav>
    );
  }

  /* ---- Expanded list ---- */
  const openRowMenu =
    (href: string) => (event: React.MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (menu?.href === href) {
        closeMenus();
        return;
      }
      const anchor = event.currentTarget as HTMLElement;
      menuAnchorRef.current = anchor;
      setRailMenu(null);
      setMenu({
        href,
        position: placeMenu(anchor.getBoundingClientRect(), MENU_WIDTH),
      });
    };

  const renderRow = (href: string, drag: DragSort) => {
    const entry = NAV_BY_HREF.get(href);
    if (!entry) return null;
    const active = isNavActive(pathname, href);
    const locked = isLocked(href);
    const planLocked = locked ? null : planLock(href);
    const dragging = drag.draggingId === href;
    const menuOpen = menu?.href === href;
    const Icon = entry.icon;
    const { style, ...handlers } = drag.getItemProps(href);
    const label = t(entry.label);
    const body = (
      <Fragment key={`${href}-content`}>
        <Icon size={15} strokeWidth={active ? 1.9 : 1.6} className="shrink-0" />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {href === COMMUNITY_HREF && communityUnread > 0 ? (
          <span
            className="shrink-0 rounded-full bg-[var(--primary)] px-1.5 text-[10.5px] font-semibold leading-4 text-[var(--primary-foreground)]"
            aria-label={t("{{count}} unread notifications", { count: communityUnread })}
          >
            {communityUnread > 99 ? "99+" : communityUnread}
          </span>
        ) : null}
        {locked || planLocked ? (
          <Lock size={13} strokeWidth={1.8} className="shrink-0" />
        ) : null}
      </Fragment>
    );
    const rowClass =
      "flex min-h-8 items-center gap-2 rounded-md py-1.5 pl-2.5 pr-7 text-[13px] transition-colors";

    return (
      <div
        {...handlers}
        key={href}
        style={style as CSSProperties}
        className={`group/nav relative rounded-md ${
          dragging
            ? "bg-[var(--secondary)] shadow-lg ring-1 ring-border/70"
            : ""
        }`}
      >
        {locked ? (
          <Tooltip
            key={`${href}-destination`}
            label={label}
            description={lockedTooltip}
            side="right"
          >
            <div
              aria-label={`${label} — ${lockedTooltip}`}
              aria-disabled
              className={`${rowClass} cursor-not-allowed text-muted-foreground/40`}
            >
              {body}
            </div>
          </Tooltip>
        ) : planLocked ? (
          <Tooltip
            key={`${href}-destination`}
            label={label}
            description={planLocked}
            side="right"
          >
            <Link
              href={PRICING_HREF}
              draggable={false}
              onClick={onNavigate}
              aria-label={`${label} — ${planLocked}`}
              className={`${rowClass} text-muted-foreground/60 hover:bg-background/60 hover:text-[var(--foreground)]`}
            >
              {body}
            </Link>
          </Tooltip>
        ) : (
          <Link
            key={`${href}-destination`}
            href={href}
            draggable={false}
            onClick={href === "/chat" ? onHomeClick : onNavigate}
            className={`${rowClass} ${
              active
                ? "bg-[var(--accent)] font-medium text-[var(--foreground)]"
                : "text-foreground/85 hover:bg-background/60 hover:text-[var(--foreground)]"
            }`}
          >
            {body}
          </Link>
        )}
        {resolved.customized ? (
        <button
          key={`${href}-arrange`}
          type="button"
          data-no-drag
          onClick={openRowMenu(href)}
          aria-label={t("Arrange {{feature}}", { feature: label })}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          className={`absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-1 text-[var(--muted-foreground)] transition-colors hover:bg-background/70 hover:text-[var(--foreground)] ${
            menuOpen
              ? "opacity-100"
              : "opacity-0 focus-visible:opacity-100 group-hover/nav:opacity-100"
          }`}
        >
          <MoreHorizontal size={13} />
        </button>
        ) : null}
      </div>
    );
  };

  return (
    <nav className="px-2 pt-1">
      <div className="space-y-px">
        {resolved.visible.filter(roleAllows).map((href) => (
          <Fragment key={href}>{renderRow(href, visibleDrag)}</Fragment>
        ))}
      </div>

      {menu && typeof document !== "undefined"
        ? createPortal(
            <FloatingPanel
              ref={menuRootRef}
              position={menu.position}
              label={t("Arrange sidebar")}
            >
              <MenuRow
                icon={RotateCcw}
                label={t("Reset sidebar order")}
                onClick={() => {
                  applyLayout({ order: [...PRIMARY_NAV_HREFS], collapsed: [] });
                  closeMenus();
                }}
              />
            </FloatingPanel>,
            document.body,
          )
        : null}
    </nav>
  );
}

export function SidebarHome({
  collapsed = false,
  onHomeClick,
}: {
  collapsed?: boolean;
  onHomeClick: (event: React.MouseEvent) => void;
}) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const { has } = useCapabilityAccess();
  const entry = NAV_BY_HREF.get("/chat")!;
  const active = isNavActive(pathname, entry.href);
  const locked = entry.requires ? !has(entry.requires) : false;
  const lockedTooltip = t("Locked — contact your administrator to get access.");
  if (collapsed) {
    return (
      <div className="mt-1 flex shrink-0 justify-center">
        <RailRow
          href="/chat"
          active={active}
          locked={locked}
          lockedTooltip={lockedTooltip}
          onHomeClick={onHomeClick}
        />
      </div>
    );
  }
  const Icon = entry.icon;
  const content = (
    <>
      <Icon size={15} strokeWidth={active ? 1.9 : 1.6} className="shrink-0" />
      <span>{t(entry.label)}</span>
    </>
  );
  const className =
    "flex min-h-8 items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] transition-colors";
  return (
    <div className="shrink-0 px-2 pb-1 pt-1">
      {locked ? (
        <Tooltip
          label={t(entry.label)}
          description={lockedTooltip}
          side="right"
        >
          <div
            aria-disabled
            aria-label={`${t(entry.label)} — ${lockedTooltip}`}
            className={`${className} cursor-not-allowed text-muted-foreground/40`}
          >
            {content}
            <Lock size={13} className="ml-auto" />
          </div>
        </Tooltip>
      ) : (
        <Link
          href="/chat"
          onClick={onHomeClick}
          className={`${className} ${
            active
              ? "bg-[var(--accent)] font-medium text-[var(--foreground)]"
              : "text-foreground/85 hover:bg-background/60 hover:text-[var(--foreground)]"
          }`}
        >
          {content}
        </Link>
      )}
    </div>
  );
}

function RailRow({
  href,
  active,
  locked,
  lockedTooltip,
  planLocked = null,
  onHomeClick,
}: {
  href: string;
  active: boolean;
  locked: boolean;
  lockedTooltip: string;
  /** Upgrade tooltip when the plan locks it (the row then opens pricing). */
  planLocked?: string | null;
  onHomeClick: (event: React.MouseEvent) => void;
}) {
  const { t } = useTranslation();
  const communityUnread = useSignedInCommunityUnread();
  const entry = NAV_BY_HREF.get(href);
  if (!entry) return null;
  const Icon = entry.icon;
  const label = t(entry.label);
  const description = locked
    ? lockedTooltip
    : entry.tooltipKey
      ? t(entry.tooltipKey)
      : undefined;

  if (locked) {
    return (
      <Tooltip label={label} description={description} side="right">
        <div
          aria-label={`${label} — ${lockedTooltip}`}
          aria-disabled
          className="relative flex h-9 w-9 cursor-not-allowed items-center justify-center rounded-xl text-muted-foreground/40"
        >
          <Icon size={18} strokeWidth={1.6} />
          <Lock
            size={10}
            strokeWidth={2}
            className="absolute bottom-1 right-1 text-muted-foreground/70"
          />
        </div>
      </Tooltip>
    );
  }

  if (planLocked) {
    return (
      <Tooltip label={label} description={planLocked} side="right">
        <Link
          href={PRICING_HREF}
          aria-label={`${label} — ${planLocked}`}
          className="relative flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground/50 transition-colors hover:bg-background/60 hover:text-[var(--foreground)]"
        >
          <Icon size={18} strokeWidth={1.6} />
          <Lock
            size={10}
            strokeWidth={2}
            className="absolute bottom-1 right-1 text-muted-foreground/70"
          />
        </Link>
      </Tooltip>
    );
  }

  return (
    <Tooltip label={label} description={description} side="right">
      <Link
        href={href}
        onClick={href === "/chat" ? onHomeClick : undefined}
        aria-label={label}
        className={`relative flex h-9 w-9 items-center justify-center rounded-xl transition-all duration-150 ${
          active
            ? "bg-[var(--accent)] text-[var(--foreground)] shadow-sm"
            : "text-foreground/85 hover:bg-background/60 hover:text-[var(--foreground)]"
        }`}
      >
        <Icon size={18} strokeWidth={active ? 2 : 1.6} />
        {href === COMMUNITY_HREF && communityUnread > 0 ? (
          <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-[var(--primary)] ring-2 ring-[var(--background)]" />
        ) : null}
      </Link>
    </Tooltip>
  );
}

function FloatingPanel({
  ref,
  position,
  label,
  children,
}: {
  ref: React.RefObject<HTMLDivElement | null>;
  position: FloatingMenuPosition;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      ref={ref}
      role="menu"
      aria-label={label}
      style={{
        left: position.left,
        top: position.top,
        maxHeight: position.maxHeight,
        transform: position.openUpward ? "translateY(-100%)" : undefined,
        transformOrigin: position.openUpward ? "bottom" : "top",
      }}
      className="fixed z-[100] overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--popover)] p-1.5 text-[12px] shadow-xl"
    >
      <div style={{ width: MENU_WIDTH - 12 }}>{children}</div>
    </div>
  );
}

function MenuRow({
  icon: Icon,
  label,
  onClick,
}: {
  icon: typeof RotateCcw;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
    >
      <Icon size={13} strokeWidth={1.7} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  );
}
