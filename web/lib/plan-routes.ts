import {
  BOOKS_HOME,
  MASTERY_HOME,
  READING_HOME,
  WATCHING_HOME,
} from "@/lib/learning-routes";
import type { PlanFeature, PlanLimit } from "@/lib/entitlements-api";

/**
 * What a paid-only route needs: a plan feature, or a monthly allowance that is
 * not zero (Free has no Mastery Paths or Books at all). Read by the sidebar,
 * the Learning hub and ``PlanGate`` so a route is declared once.
 */
export type PlanRequirement =
  | { feature: PlanFeature }
  | { allowance: PlanLimit }
  /** Locked while the count limit is used up (e.g. creating one more partner). */
  | { count: PlanLimit };

export const ROUTE_PLANS: ReadonlyArray<{
  prefix: string;
  requires: PlanRequirement;
  /** Translation key naming the surface on the locked page. */
  title: string;
}> = [
  { prefix: "/community", requires: { feature: "community" }, title: "Community" },
  { prefix: "/courses", requires: { feature: "courses" }, title: "Courses" },
  { prefix: "/memory", requires: { feature: "memory" }, title: "Memory" },
  { prefix: "/notebooks", requires: { feature: "space_extras" }, title: "Notebooks" },
  { prefix: "/space/questions", requires: { feature: "space_extras" }, title: "Question Bank" },
  { prefix: "/space/skills", requires: { feature: "space_extras" }, title: "Skills" },
  { prefix: READING_HOME, requires: { feature: "reading" }, title: "Immersive Reading" },
  { prefix: WATCHING_HOME, requires: { feature: "watching" }, title: "Immersive Watching" },
  { prefix: MASTERY_HOME, requires: { allowance: "mastery_per_month" }, title: "Mastery Path" },
  { prefix: BOOKS_HOME, requires: { allowance: "books_per_month" }, title: "Books" },
  { prefix: "/partners/new", requires: { count: "max_partners" }, title: "New partner" },
];

export function planRouteFor(pathname: string) {
  return (
    ROUTE_PLANS.find(
      (r) => pathname === r.prefix || pathname.startsWith(`${r.prefix}/`),
    ) ?? null
  );
}

export function requirementKey(req: PlanRequirement): PlanFeature | PlanLimit {
  if ("feature" in req) return req.feature;
  return "allowance" in req ? req.allowance : req.count;
}
