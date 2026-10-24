import { apiFetch, apiUrl } from "@/lib/api";

const BASE = "/api/knowledge-tree";

export interface Rights {
  role: string;
  can_edit: boolean;
  /** R&D and administrators see the main graph; only R&D edits it. */
  can_view?: boolean;
}
export interface DomainSummary {
  id: string;
  slug: string;
  name: string;
  description: string;
  kb_name: string | null;
  nodes: number;
}
export interface TreeNode {
  id: string;
  parent_id: string | null;
  title: string;
  summary: string;
  version: number;
  refs: { source_id: string; start: number; end: number }[];
  children: TreeNode[];
}
export interface TreeData {
  domain: { id: string; slug: string; name: string; description: string; kb_name: string | null };
  nodes: TreeNode[];
  sources: { id: string; filename: string; size: number }[];
}
export interface NodeDetail {
  id: string;
  domain_id: string;
  title: string;
  summary: string;
  version: number;
  excerpts: { source_id: string; filename: string; text: string }[];
  label: string;
  /** Optional free grouping ("" when unset). */
  layer: string;
  properties: Record<string, unknown>;
  relations: { outgoing: GraphEdge[]; incoming: GraphEdge[] };
  /** Readable id, unique inside the domain (like Q42 in Wikidata). */
  key: string;
  /** Display name per language: {"vi": "...", "en": "..."}. */
  names: Record<string, string>;
  description: string;
  provenance: { source?: string; confidence?: number; note?: string };
  created_by: string;
  created_at: string | null;
  updated_by: string;
  updated_at: string | null;
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

export const getRights = () => call<Rights>("/me");
export const listDomains = () =>
  call<{ domains: DomainSummary[] }>("/domains").then((r) => r.domains);
export const getTree = (id: string) => call<TreeData>(`/domains/${id}/tree`);
export const getNode = (id: string) => call<NodeDetail>(`/nodes/${id}`);
export const createDomain = (name: string, description: string) =>
  call<{ id: string }>("/domains", json("POST", { name, description }));
export const deleteDomain = (id: string) =>
  call<{ ok: boolean }>(`/domains/${id}`, { method: "DELETE" });
export const addNode = (
  domainId: string,
  parentId: string | null,
  title: string,
  summary: string,
) =>
  call<{ id: string }>(
    `/domains/${domainId}/nodes`,
    json("POST", { parent_id: parentId, title, summary }),
  );
export const patchNode = (
  id: string,
  patch: { title?: string; summary?: string },
) => call<{ id: string }>(`/nodes/${id}`, json("PATCH", patch));
export const deleteNode = (id: string) =>
  call<{ deleted: number }>(`/nodes/${id}`, { method: "DELETE" });

export function importFile(file: File, domainId: string | null, name = "") {
  const form = new FormData();
  form.append("file", file);
  if (!domainId) form.append("name", name);
  return call<{ domain_id: string; nodes_created: number }>(
    domainId ? `/domains/${domainId}/import` : "/domains/import",
    { method: "POST", body: form },
  );
}

/** A GraphRAG KB a domain may link: the admin library or an R&D account's own. */
export interface KbOption {
  ref: string;
  name: string;
  owner_id: string;
  owner: string;
}
export const listKbOptions = () =>
  call<{ knowledge_bases: string[]; options?: KbOption[] }>("/kb-options").then(
    (r) => r.options ?? r.knowledge_bases.map((ref) => ({ ref, name: ref, owner_id: "", owner: "admin" })),
  );
export const patchDomain = (
  id: string,
  patch: { name?: string; description?: string; kb_name?: string | null },
) => call<{ id: string; kb_name: string | null }>(`/domains/${id}`, json("PATCH", patch));

export interface AskResult {
  query: string;
  content: string;
  scope: { title: string; path: string; domain: string; score: number }[];
  sources: unknown[];
  notes: string[];
}
export const askKnowledge = (query: string, domainId?: string | null) =>
  call<AskResult>("/ask", json("POST", { query, domain_id: domainId ?? null }));

// ── R&D document store, tree export/import, audit trail ──
export interface StoreDocument {
  id: string;
  domain_id: string;
  domain_name: string;
  filename: string;
  size: number;
  downloadable: boolean;
  uploaded_by: string;
  created_at: string | null;
}
export interface AuditEntry {
  id?: string;
  actor_id: string;
  action: string;
  target_type: string;
  target_id: string;
  data: Record<string, unknown>;
  created_at: string | null;
}
export const listDocuments = (domainId?: string) =>
  call<{ documents: StoreDocument[] }>(
    `/documents${domainId ? `?domain_id=${encodeURIComponent(domainId)}` : ""}`,
  ).then((r) => r.documents);
export function uploadDocument(domainId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return call<StoreDocument>(`/domains/${domainId}/documents`, { method: "POST", body: form });
}
export const deleteDocument = (id: string) =>
  call<{ ok: boolean }>(`/documents/${id}`, { method: "DELETE" });
export const listAudit = (prefix = "store.") =>
  call<{ entries: AuditEntry[] }>(`/audit?action_prefix=${encodeURIComponent(prefix)}`).then(
    (r) => r.entries,
  );

async function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
export async function downloadDocument(doc: StoreDocument) {
  const res = await apiFetch(apiUrl(`${BASE}/documents/${doc.id}/download`));
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  await saveBlob(await res.blob(), doc.filename);
}
export async function exportTree(domainId: string, name: string) {
  const data = await call<unknown>(`/domains/${domainId}/export`);
  await saveBlob(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    `${name.replace(/[^\w.-]+/g, "_") || "tree"}.pathmind-graph.json`,
  );
}
export function importTree(file: File, name = "") {
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  return call<{ domain_id: string; name: string; nodes: number }>("/domains/import-tree", {
    method: "POST",
    body: form,
  });
}

// ── edit history of the main graph (R&D and administrators) ──────────────

export interface TreeChange {
  id: number;
  domain_id: string;
  domain_name: string;
  actor_id: string;
  actor_name: string;
  action: string;
  summary: string;
  reverts_id: number | null;
  reverted_by_id: number | null;
  created_at: string | null;
}

export interface TreeChangeItem {
  key: string;
  kind: "domain" | "node" | "source";
  change: "added" | "removed" | "changed";
  before: string;
  after: string;
  fields: string[];
}

export interface DeletedDomain {
  domain_id: string;
  domain_name: string;
  last_change_id: number;
}

export const listTreeHistory = (domainId?: string | null) =>
  call<{ changes: TreeChange[]; deleted_domains: DeletedDomain[] }>(
    `/history?limit=200${domainId ? `&domain_id=${encodeURIComponent(domainId)}` : ""}`,
  );
export const getTreeChange = (id: number) =>
  call<TreeChange & { items: TreeChangeItem[] }>(`/history/${id}`);
export const revertTreeChange = (id: number) =>
  call<{ reverted: number; change: TreeChange | null }>(`/history/${id}/revert`, { method: "POST" });

// ── samples and the tree as a document (Co-Writer) ───────────────────────

export type SampleFormat = "md" | "txt" | "docx" | "pdf";

export async function downloadSample(format: SampleFormat) {
  const res = await apiFetch(apiUrl(`${BASE}/sample?format=${format}`));
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  await saveBlob(await res.blob(), `knowledge-tree-sample.${format}`);
}
export const getSampleMarkdown = () => call<{ title: string; markdown: string }>("/sample/markdown");
export const getDomainMarkdown = (domainId: string) =>
  call<{ domain_id: string; name: string; markdown: string }>(`/domains/${domainId}/markdown`);

export interface SyncResult {
  domain_id: string;
  name: string;
  created: number;
  updated: number;
  removed: number;
  kept: number;
}
/** Update a domain (or create one when ``domainId`` is null) from a document's headings. */
export const syncTreeFromDocument = (body: {
  domain_id: string | null;
  name?: string;
  title: string;
  markdown: string;
}) => call<SyncResult>("/sync", json("POST", body));

// ── the knowledge graph: schema, typed nodes, relations ──────────────────


export interface GraphLabel {
  id: string;
  name: string;
  color: string;
  description: string;
}
export interface GraphRelationType {
  id: string;
  name: string;
  description: string;
}
export interface GraphSchema {
  labels: GraphLabel[];
  relations: GraphRelationType[];
  implicit_relation: string;
}
export interface GraphEdge {
  id: string;
  source_id: string;
  target_id: string;
  type: string;
  properties: Record<string, unknown>;
  /** Outline placement (child PART_OF parent); edit it by moving the node. */
  implicit: boolean;
  source_title?: string;
  target_title?: string;
}
export interface GraphNode {
  id: string;
  parent_id: string | null;
  title: string;
  summary: string;
  label: string;
  /** Optional free grouping ("" when unset). */
  layer: string;
  properties: Record<string, unknown>;
  position: number;
  /** Readable id, unique inside the domain (like Q42 in Wikidata). */
  key: string;
  /** Display name per language: {"vi": "...", "en": "..."}. */
  names: Record<string, string>;
  description: string;
  provenance: { source?: string; confidence?: number; note?: string };
  created_by: string;
  created_at: string | null;
  updated_by: string;
  updated_at: string | null;
}
export interface DomainGraph {
  domain: { id: string; name: string; description: string };
  nodes: GraphNode[];
  edges: GraphEdge[];
  schema: GraphSchema;
}

export const getGraphSchema = () => call<GraphSchema>("/schema");
export const getDomainGraph = (domainId: string) => call<DomainGraph>(`/domains/${domainId}/graph`);
export const saveGraphLabel = (
  body: { name: string; color?: string; description?: string },
  id?: string,
) => call<GraphLabel>(id ? `/schema/labels/${id}` : "/schema/labels", json(id ? "PATCH" : "POST", body));
export const deleteGraphLabel = (id: string) => call<{ ok: true }>(`/schema/labels/${id}`, { method: "DELETE" });
export const saveGraphRelationType = (body: { name: string; description?: string }, id?: string) =>
  call<GraphRelationType>(id ? `/schema/relations/${id}` : "/schema/relations", json(id ? "PATCH" : "POST", body));
export const deleteGraphRelationType = (id: string) =>
  call<{ ok: true }>(`/schema/relations/${id}`, { method: "DELETE" });
export const setNodeGraph = (
  nodeId: string,
  body: {
    label?: string | null;
    /** Optional free grouping; "" clears it. */
    layer?: string;
    properties?: Record<string, unknown>;
    key?: string;
    names?: Record<string, string>;
    description?: string;
    provenance?: { source?: string; confidence?: number | null; note?: string };
  },
) => call<{ id: string; label: string; layer: string; properties: Record<string, unknown> }>(
  `/nodes/${nodeId}/graph`,
  json("PUT", body),
);
export const addGraphEdge = (body: {
  source_id: string;
  target_id: string;
  type: string;
  properties?: Record<string, unknown>;
}) => call<GraphEdge>("/edges", json("POST", body));
export const deleteGraphEdge = (id: string) => call<{ ok: true }>(`/edges/${id}`, { method: "DELETE" });
