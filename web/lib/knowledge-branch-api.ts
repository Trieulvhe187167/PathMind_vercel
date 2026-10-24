import { apiFetch, apiUrl } from "@/lib/api";

const BASE = "/api/knowledge-branch";

export interface BranchNode {
  id: string;
  parent_id: string | null;
  title: string;
  summary: string;
  position: number;
  source_kb: string | null;
  source_doc: string | null;
  prerequisites: string[];
  anchor_node_id?: string | null;
  origin?: "auto" | "manual";
  children: BranchNode[];
}
export interface BranchData {
  id: string;
  name: string;
  version: number;
  node_count: number;
  nodes: BranchNode[];
}
export interface BranchVersionInfo {
  id: string;
  label: string;
  node_count: number;
  created_at: string | null;
}

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

export const getBranch = () => call<BranchData>("");
export const addBranchNode = (
  parentId: string | null,
  title: string,
  summary: string,
  baseVersion?: number,
) =>
  call<{ id: string }>(
    "/nodes",
    json("POST", { parent_id: parentId, title, summary, base_version: baseVersion }),
  );
export const patchBranchNode = (
  id: string,
  fields: { title?: string; summary?: string },
  baseVersion?: number,
) => call<{ id: string }>(`/nodes/${id}`, json("PATCH", { ...fields, base_version: baseVersion }));
export const moveBranchNode = (id: string, parentId: string | null, baseVersion?: number) =>
  call<{ id: string }>(`/nodes/${id}/move`, json("POST", { parent_id: parentId, base_version: baseVersion }));
export const deleteBranchNode = (id: string, baseVersion?: number) =>
  call<{ deleted: number }>(
    `/nodes/${id}${baseVersion !== undefined ? `?base_version=${baseVersion}` : ""}`,
    { method: "DELETE" },
  );
export const setPrerequisites = (id: string, ids: string[]) =>
  call<{ id: string; prerequisites: string[] }>(
    `/nodes/${id}/prerequisites`,
    json("PUT", { prerequisite_ids: ids }),
  );
export const listBranchVersions = () =>
  call<{ versions: BranchVersionInfo[] }>("/versions").then((r) => r.versions);
export const saveBranchVersion = (label: string) =>
  call<{ id: string; label: string }>("/versions", json("POST", { label }));
export const restoreBranchVersion = (id: string) =>
  call<{ restored_nodes: number }>(`/versions/${id}/restore`, { method: "POST" });

export function enrichBranch(file: File) {
  const form = new FormData();
  form.append("file", file);
  return call<{ root_id: string; created: number }>("/enrich", { method: "POST", body: form });
}
export const resetBranch = () => call<{ removed: number }>("/reset", { method: "POST" });

export interface PathItem {
  id: string;
  title: string;
  summary: string;
  status: "mastered" | "ready" | "locked";
  prerequisites: { id: string; title: string }[];
  blocked_by: { id: string; title: string }[];
}
export interface MasteryPath {
  items: PathItem[];
  mastered: number;
  total: number;
  next: string | null;
}
export const getMasteryPath = () => call<MasteryPath>("/path");
export const setMastered = (id: string, mastered: boolean) =>
  call<{ node_id: string; mastered: boolean }>(`/nodes/${id}/mastered`, json("PUT", { mastered }));
