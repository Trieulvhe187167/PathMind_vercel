import { apiFetch, apiUrl } from "@/lib/api";

const BASE = "/api/admin-ops";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(`${BASE}${path}`), init);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}
const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface SettingItem {
  type: "int" | "str" | "choice";
  value: string;
  source: "admin" | "default";
  is_set?: boolean;
  min?: number;
  max?: number;
  choices?: string[];
}
export const getSettings = () =>
  call<{ settings: Record<string, SettingItem> }>("/settings").then((r) => r.settings);
export const saveSettings = (values: Record<string, string>) =>
  call<{ settings: Record<string, SettingItem> }>("/settings", json("PUT", values)).then((r) => r.settings);
export const sendTestEmail = (to: string) => call<{ ok: boolean }>("/settings/test-email", json("POST", { to }));

export interface JobRow {
  id: string;
  name: string;
  owner: string;
  enabled: boolean;
  schedule: { kind: string; every_seconds: number | null; expr: string | null; tz: string | null; at_ms: number | null };
  next_run_at_ms: number | null;
  last_run_at_ms: number | null;
  last_status: string | null;
  last_error: string | null;
  recent: { run_at_ms: number; status: string; duration_ms: number; error: string | null }[];
}
export const listJobs = () => call<{ jobs: JobRow[] }>("/jobs").then((r) => r.jobs);
export const setJobEnabled = (id: string, enabled: boolean) => call<JobRow>(`/jobs/${id}/enabled`, json("PUT", { enabled }));
export const runJob = (id: string) => call<JobRow>(`/jobs/${id}/run`, { method: "POST" });

export interface AuditRow {
  id: number;
  actor_id: string;
  action: string;
  target_type: string;
  target_id: string;
  data: Record<string, unknown>;
  created_at: string | null;
}
export const listAuditLog = (actionPrefix = "", actorId = "") =>
  call<{ entries: AuditRow[] }>(
    `/audit?action_prefix=${encodeURIComponent(actionPrefix)}&actor_id=${encodeURIComponent(actorId)}&limit=300`,
  ).then((r) => r.entries);

export interface StorageRow {
  id: string;
  name: string;
  kind: "local" | "s3";
  config: Record<string, string | boolean>;
  is_default: boolean;
  enabled: boolean;
  last_test_ok: boolean | null;
  last_test_message: string;
  last_test_at: string | null;
  files: number;
}
export interface StorageList {
  backends: StorageRow[];
  implicit: { path: string; in_use: boolean; files: number };
}
export const listStorage = () => call<StorageList>("/storage");
export const addStorage = (body: { name: string; kind: string; config: Record<string, unknown>; enabled: boolean }) =>
  call<StorageRow>("/storage", json("POST", body));
export const editStorage = (id: string, body: { name: string; kind: string; config: Record<string, unknown>; enabled: boolean }) =>
  call<StorageRow>(`/storage/${id}`, json("PUT", body));
export const testStorage = (id: string) => call<StorageRow>(`/storage/${id}/test`, { method: "POST" });
export const defaultStorage = (id: string) => call<StorageRow>(`/storage/${id}/default`, { method: "POST" });
export const deleteStorage = (id: string) => call<{ ok: boolean }>(`/storage/${id}`, { method: "DELETE" });
