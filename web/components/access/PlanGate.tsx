"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowUpRight, Lock, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PRICING_HREF, type PlanFeature, type PlanLimit } from "@/lib/entitlements-api";
import { planRouteFor, requirementKey, type PlanRequirement } from "@/lib/plan-routes";

import { useEntitlements, type EntitlementsValue } from "./EntitlementsContext";

/** True when the user's plan does not allow this requirement at all. */
export function isPlanLocked(ent: EntitlementsValue, req: PlanRequirement): boolean {
  if ("feature" in req) return !ent.has(req.feature);
  if ("count" in req) return ent.atLimit(req.count);
  return ent.limit(req.allowance) === 0;
}

/** "Upgrade to Plus" / "See plans" — always a link to the pricing page. */
export function UpgradeLink({
  target,
  compact = false,
  className = "",
}: {
  target: PlanFeature | PlanLimit;
  compact?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const { upgradeTo } = useEntitlements();
  const plan = upgradeTo(target);
  const label = plan ? t("Upgrade to {{plan}}", { plan }) : t("See plans");
  return (
    <Link
      href={PRICING_HREF}
      className={
        className ||
        (compact
          ? "inline-flex items-center gap-1 text-[12px] font-medium text-[var(--primary)] hover:underline"
          : "inline-flex items-center gap-1.5 rounded-xl bg-[var(--primary)] px-4 py-2 text-[13px] font-medium text-[var(--primary-foreground)] shadow-sm transition hover:brightness-110")
      }
    >
      <Sparkles size={compact ? 12 : 14} aria-hidden="true" />
      {label}
    </Link>
  );
}

/** Small "🔒 Plus" chip for locked cards and rows. */
export function PlanLockBadge({ target }: { target: PlanFeature | PlanLimit }) {
  const { t } = useTranslation();
  const { upgradeTo } = useEntitlements();
  const plan = upgradeTo(target);
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--background)] px-2 py-0.5 text-[10.5px] font-medium text-[var(--muted-foreground)]">
      <Lock size={10} strokeWidth={2} aria-hidden="true" />
      {plan || t("Paid plans")}
    </span>
  );
}

/** Sentence for a tooltip or notice: "Available on Plus — upgrade to unlock." */
export function usePlanLockedText() {
  const { t } = useTranslation();
  const { upgradeTo } = useEntitlements();
  return (target: PlanFeature | PlanLimit) => {
    const plan = upgradeTo(target);
    return plan
      ? t("Available on {{plan}} — upgrade to unlock.", { plan })
      : t("Available on paid plans — upgrade to unlock.");
  };
}

/** Full-surface notice shown in place of a page the plan does not include. */
export function PlanLockedNotice({
  title,
  target,
}: {
  title: string;
  target: PlanFeature | PlanLimit;
}) {
  const { t } = useTranslation();
  const { upgradeTo, data, limit, used } = useEntitlements();
  const plan = upgradeTo(target);
  const feature = t(title);
  const max = target.startsWith("max_") ? limit(target as PlanLimit) : null;
  const reachedCount = max !== null && max > 0 && used(target as PlanLimit) >= max;
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-center gap-4 rounded-2xl border border-[var(--border)] bg-[var(--secondary)]/40 px-8 py-10 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--background)] text-[var(--muted-foreground)]">
          <Lock size={20} strokeWidth={1.8} />
        </div>
        <h2 className="text-base font-semibold text-[var(--foreground)]">{feature}</h2>
        <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
          {reachedCount
            ? `${t("You've reached your plan's limit ({{limit}}).", { limit: max })} ${
                plan
                  ? t("Upgrade to {{plan}} for more.", { plan })
                  : t("Remove one you no longer need to make room.")
              }`
            : plan
              ? t("{{feature}} is included from the {{plan}} plan. Upgrade to unlock it.", {
                  feature,
                  plan,
                })
              : t("{{feature}} is not included in your plan.", { feature })}
        </p>
        {data?.plan_name ? (
          <p className="text-[12px] text-[var(--muted-foreground)]">
            {t("Current plan: {{plan}}", { plan: data.plan_name })} ·{" "}
            {t("Anything you already created is kept.")}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-center gap-2">
          <UpgradeLink target={target} />
          <Link
            href={PRICING_HREF}
            className="inline-flex items-center gap-1 rounded-xl px-3 py-2 text-[13px] text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            {t("Compare plans")}
            <ArrowUpRight size={13} aria-hidden="true" />
          </Link>
        </div>
      </div>
    </div>
  );
}

/**
 * Route-level plan gate (mounted once per layout group, next to the
 * capability gate): direct URLs to a locked surface show the notice.
 */
export default function PlanGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "";
  const ent = useEntitlements();
  const route = planRouteFor(pathname);
  if (!route) return <>{children}</>;
  // Don't mount a paid page (and fire its requests) before the plan is known.
  if (ent.loading && !ent.data) return null;
  if (isPlanLocked(ent, route.requires)) {
    return <PlanLockedNotice title={route.title} target={requirementKey(route.requires)} />;
  }
  return <>{children}</>;
}

/**
 * "Skills · 3 / 5" with an upgrade nudge once the allowance is used up.
 * Renders nothing when unlimited.
 */
export function UsageMeter({
  target,
  label,
  monthly = false,
  className = "",
  inLink = false,
}: {
  target: PlanLimit;
  /** Rendered inside another link: name the plan instead of linking to it. */
  inLink?: boolean;
  /** Translation key naming what is counted, e.g. "Skills". */
  label: string;
  monthly?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const ent = useEntitlements();
  const max = ent.limit(target);
  if (max === null || ent.loading) return null;
  const used = ent.used(target);
  const full = used >= max;
  return (
    <div
      className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] ${
        full ? "text-amber-600 dark:text-amber-400" : "text-[var(--muted-foreground)]"
      } ${className}`}
    >
      <span>
        {t(label)} ·{" "}
        {monthly && ent.trial
          ? t("{{used}} / {{limit}} free trial", { used, limit: max })
          : monthly
            ? t("{{used}} / {{limit}} this month", { used, limit: max })
            : t("{{used}} / {{limit}} used", { used, limit: max })}
      </span>
      {full ? (
        inLink ? (
          ent.upgradeTo(target) ? (
            <span className="font-medium">
              {t("Upgrade to {{plan}} for more.", { plan: ent.upgradeTo(target) })}
            </span>
          ) : null
        ) : (
          <UpgradeLink target={target} compact />
        )
      ) : null}
    </div>
  );
}

/** Message for a button that is disabled because the allowance is used up. */
export function useLimitReachedText() {
  const { t } = useTranslation();
  const { upgradeTo, limit, trial } = useEntitlements();
  return (target: PlanLimit) => {
    const plan = upgradeTo(target);
    const max = limit(target);
    const allowance = target === "books_per_month" || target === "mastery_per_month";
    const head =
      max === 0
        ? t("Not included in your plan.")
        : allowance && trial
          ? t("You've used your free trial ({{limit}}).", { limit: max ?? 0 })
          : t("You've reached your plan's limit ({{limit}}).", { limit: max ?? 0 });
    return plan ? `${head} ${t("Upgrade to {{plan}} for more.", { plan })}` : head;
  };
}

/**
 * Stand-in for a dialog whose feature the plan does not include (e.g. "Save
 * to Notebook" on Free): says so and offers the upgrade.
 */
export function PlanLockedDialog({
  open,
  onClose,
  title,
  target,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  target: PlanFeature | PlanLimit;
}) {
  const { t } = useTranslation();
  const text = usePlanLockedText();
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-[var(--background)]/65 p-4 backdrop-blur-md"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t(title)}
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-sm rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 text-center shadow-[0_22px_70px_rgba(0,0,0,0.18)]"
      >
        <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-[var(--secondary)] text-[var(--muted-foreground)]">
          <Lock size={18} strokeWidth={1.8} />
        </div>
        <h2 className="text-[15px] font-semibold text-[var(--foreground)]">{t(title)}</h2>
        <p className="mt-2 text-[13px] leading-relaxed text-[var(--muted-foreground)]">
          {text(target)}
        </p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl px-3 py-2 text-[13px] text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
          >
            {t("Not now")}
          </button>
          <UpgradeLink target={target} />
        </div>
      </div>
    </div>
  );
}
