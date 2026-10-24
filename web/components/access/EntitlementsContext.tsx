"use client";

/**
 * What the signed-in user's plan unlocks (Community, Notebooks/Question
 * Bank/Skills, Reading, Watching) and how much of each allowance is used.
 *
 * The backend is the real boundary (paid-only routes answer 402); this only
 * decides what to show as locked and which plan to suggest. Until the first
 * read resolves everything reads as available so nothing flashes locked.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  ENTITLEMENTS_CHANGED_EVENT,
  LIMIT_USAGE,
  fetchEntitlements,
  type Entitlements,
  type PlanFeature,
  type PlanLimit,
} from "@/lib/entitlements-api";

export interface EntitlementsValue {
  loading: boolean;
  data: Entitlements | null;
  /** The plan unlocks this surface (true while loading / for admins). */
  has: (feature: PlanFeature) => boolean;
  /** ``null`` = unlimited. */
  limit: (key: PlanLimit) => number | null;
  used: (key: PlanLimit) => number;
  /** No room for one more (false while loading / when unlimited). */
  atLimit: (key: PlanLimit) => boolean;
  /** Name of the cheapest plan that unlocks / raises it ("" when none). */
  upgradeTo: (key: PlanFeature | PlanLimit) => string;
  /** Path/book allowances are a one-time trial rather than monthly. */
  trial: boolean;
  refresh: () => Promise<void>;
}

const DEFAULT_VALUE: EntitlementsValue = {
  loading: false,
  data: null,
  has: () => true,
  limit: () => null,
  used: () => 0,
  atLimit: () => false,
  upgradeTo: () => "",
  trial: false,
  refresh: async () => {},
};

const EntitlementsContext = createContext<EntitlementsValue>(DEFAULT_VALUE);

export function useEntitlements(): EntitlementsValue {
  return useContext(EntitlementsContext);
}

export function EntitlementsProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Entitlements | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchEntitlements();
      if (next) setData(next);
    } catch {
      // Keep the last known state; the backend still enforces.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onFocus = () => void refresh();
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", onFocus);
    window.addEventListener(ENTITLEMENTS_CHANGED_EVENT, onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.removeEventListener(ENTITLEMENTS_CHANGED_EVENT, onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  const value = useMemo<EntitlementsValue>(() => {
    const limit = (key: PlanLimit) => (data ? data.limits[key] ?? null : null);
    const used = (key: PlanLimit) => (data ? data.usage[LIMIT_USAGE[key]] ?? 0 : 0);
    return {
      loading,
      data,
      has: (feature) => (!data || data.is_admin ? true : !!data.features[feature]),
      limit,
      used,
      atLimit: (key) => {
        const max = limit(key);
        return max !== null && used(key) >= max;
      },
      upgradeTo: (key) => data?.upgrade_to[key] || "",
      trial: data?.allowance_period === "lifetime",
      refresh,
    };
  }, [data, loading, refresh]);

  return (
    <EntitlementsContext.Provider value={value}>{children}</EntitlementsContext.Provider>
  );
}
