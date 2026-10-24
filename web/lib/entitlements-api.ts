import { apiFetch, apiUrl } from "@/lib/api";

/** Paid-only surfaces (see ``pathmind/services/entitlements.py``). */
export type PlanFeature =
  | "community"
  | "space_extras"
  | "courses"
  | "memory"
  | "reading"
  | "watching"
  | "contribute";
/** Numeric allowances; ``null`` = unlimited, ``0`` = not on this plan. */
export type PlanLimit =
  | "max_skills"
  | "max_partners"
  | "mastery_per_month"
  | "books_per_month";

export interface Entitlements {
  plan_id: string;
  plan_name: string;
  price_monthly: number;
  is_admin: boolean;
  features: Record<PlanFeature, boolean>;
  limits: Record<PlanLimit, number | null>;
  /** "lifetime" = the path/book allowances are a one-time trial (Free). */
  allowance_period: AllowancePeriod;
  usage: {
    skills: number;
    partners: number;
    mastery_this_month: number;
    books_this_month: number;
  };
  /** Cheapest plan that unlocks / raises each entitlement ("" = none). */
  upgrade_to: Partial<Record<PlanFeature | PlanLimit, string>>;
}

export type AllowancePeriod = "month" | "lifetime";

/** A plan's stored switches and limits (admin editor, pricing). */
export type PlanEntitlements = Record<PlanFeature, boolean> &
  Record<PlanLimit, number | null> & { allowance_period?: AllowancePeriod };

export const PLAN_FEATURES: PlanFeature[] = [
  "community",
  "space_extras",
  "courses",
  "memory",
  "reading",
  "watching",
  "contribute",
];
export const PLAN_LIMITS: PlanLimit[] = [
  "max_skills",
  "max_partners",
  "mastery_per_month",
  "books_per_month",
];

/** Where each limit's current usage lives in ``Entitlements.usage``. */
export const LIMIT_USAGE: Record<PlanLimit, keyof Entitlements["usage"]> = {
  max_skills: "skills",
  max_partners: "partners",
  mastery_per_month: "mastery_this_month",
  books_per_month: "books_this_month",
};

/** Returns null when signed out. */
export async function fetchEntitlements(): Promise<Entitlements | null> {
  const res = await apiFetch(apiUrl("/api/billing/entitlements"), {
    skipAuthRedirect: true,
  });
  if (!res.ok) return null;
  return (await res.json()) as Entitlements;
}

export const ENTITLEMENTS_CHANGED_EVENT = "pathmind:entitlements-changed";

/** Ask every mounted provider to re-read (after creating/deleting things). */
export function notifyEntitlementsChanged(): void {
  if (typeof window !== "undefined")
    window.dispatchEvent(new Event(ENTITLEMENTS_CHANGED_EVENT));
}

export const PRICING_HREF = "/pricing";
