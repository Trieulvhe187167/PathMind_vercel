import { apiFetch, apiUrl } from "@/lib/api";

export type InsightKind = "topic" | "preference" | "activity";

export interface SharingStatus {
  enabled: boolean;
  last_shared_at: string | null;
  stale: boolean;
  min_group: number;
}

export interface InsightItem {
  kind: InsightKind;
  label: string;
  count: number;
}

export interface SharingPreview {
  period: string;
  items: InsightItem[];
}

export interface InsightGroup {
  kind: InsightKind;
  label: string;
  people: number;
  total: number;
}

export interface InsightStats {
  period: string;
  min_group: number;
  consenting_accounts: number | null;
  groups: InsightGroup[];
}

const BASE = "/api/memory-sharing";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

export const getSharingStatus = async () =>
  json<SharingStatus>(await apiFetch(apiUrl(BASE)));

export const setSharing = async (enabled: boolean) =>
  json<SharingStatus & { deleted: number }>(
    await apiFetch(apiUrl(BASE), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }),
  );

export const getSharingPreview = async () =>
  json<SharingPreview>(await apiFetch(apiUrl(`${BASE}/preview`)));

export const shareNow = async () =>
  json<{ period: string; shared: number }>(
    await apiFetch(apiUrl(`${BASE}/share`), { method: "POST" }),
  );

export const deleteSharedData = async () =>
  json<{ deleted: number }>(
    await apiFetch(apiUrl(`${BASE}/data`), { method: "DELETE" }),
  );

export const getInsightStats = async () =>
  json<InsightStats>(await apiFetch(apiUrl(`${BASE}/admin/stats`)));
