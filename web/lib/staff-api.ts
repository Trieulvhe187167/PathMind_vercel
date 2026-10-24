import { apiFetch, apiUrl } from "@/lib/api";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(path), init);
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

// ── system status ──
export interface SystemStatus {
  generated_at: string;
  uptime_seconds: number;
  checks: { name: string; ok: boolean; detail: string }[];
  accounts: { total: number; by_role: Record<string, number> };
  subscriptions_active: number;
  payments_pending: number;
  recommendations_pending: number;
  knowledge: { domains: number; nodes: number; documents: number; document_bytes: number };
  answers_wrong_7d: number;
  activity_24h: { store_actions: number; credits_used_today: number };
  disk: { free_bytes?: number; total_bytes?: number };
}
export const getSystemStatus = () => call<SystemStatus>("/api/system-status");

// ── recommendations ──
export interface RecNode {
  id: string;
  parent_id: string | null;
  title: string;
  summary: string;
  source_doc: string | null;
  position: number;
}
export interface Recommendation {
  id: string;
  educator_id: string;
  title: string;
  note: string;
  status: "pending" | "merged" | "rejected";
  node_count: number;
  document_count: number;
  review_note: string;
  merged: Record<string, unknown>;
  created_at: string | null;
  reviewed_at: string | null;
  nodes?: RecNode[];
  documents?: { filename: string; size: number; sha256: string }[];
}
export const listRecommendations = (status?: string) =>
  call<{ items: Recommendation[] }>(`/api/recommendations${status ? `?status=${status}` : ""}`).then((r) => r.items);
export const getRecommendation = (id: string) => call<Recommendation>(`/api/recommendations/${id}`);
export const mergeRecommendation = (
  id: string,
  body: { domain_id: string; parent_id: string | null; node_ids: string[]; adopt_documents: boolean; note: string },
) => call<Recommendation>(`/api/recommendations/${id}/merge`, json("POST", body));
export const rejectRecommendation = (id: string, note: string) =>
  call<Recommendation>(`/api/recommendations/${id}/reject`, json("POST", { note }));
export async function downloadRecommendationDocument(id: string, index: number, filename: string) {
  const res = await apiFetch(apiUrl(`/api/recommendations/${id}/documents/${index}`));
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
export const submitRecommendation = (node_id: string, title: string, note: string) =>
  call<Recommendation>("/api/recommendations", json("POST", { node_id, title, note }));
export const listMyRecommendations = () =>
  call<{ items: Recommendation[] }>("/api/recommendations/mine").then((r) => r.items);

// ── rewards ──
export interface LedgerEntry {
  id: number;
  user_id: string;
  points: number;
  reason: string;
  note: string;
  created_at: string | null;
}
export interface MyRewards {
  balance: number;
  ledger: LedgerEntry[];
  rules: Record<string, number>;
  bonus_credits: number;
}
export const getMyRewards = () => call<MyRewards>("/api/rewards/me");
export const redeemPoints = (kind: "plan_days" | "ai_credits", points: number) =>
  call<Record<string, unknown>>("/api/rewards/redeem", json("POST", { kind, points }));
export const getRewardRules = () => call<{ rules: Record<string, number> }>("/api/rewards/rules").then((r) => r.rules);
export const saveRewardRules = (rules: Record<string, number>) =>
  call<{ rules: Record<string, number> }>("/api/rewards/rules", json("PUT", rules)).then((r) => r.rules);
export const grantPoints = (user_id: string, points: number, note: string) =>
  call<{ balance: number }>("/api/rewards/grant", json("POST", { user_id, points, note }));
export const listRewardLedger = (user_id?: string) =>
  call<{ entries: LedgerEntry[] }>(`/api/rewards/ledger${user_id ? `?user_id=${encodeURIComponent(user_id)}` : ""}`).then(
    (r) => r.entries,
  );

// ── answer ratings ──
export interface WrongAnswer {
  id: number;
  reason: string;
  excerpt: string;
  created_at: string | null;
}
export const listWrongAnswers = () =>
  call<{ items: WrongAnswer[] }>("/api/answer-ratings/wrong").then((r) => r.items);
export const rateAnswer = (body: {
  session_id: string;
  message_id: string;
  verdict: "right" | "wrong";
  reason?: string;
  excerpt?: string;
}) => call<{ verdict: string }>("/api/answer-ratings", json("PUT", body));
export const clearAnswerRating = (messageId: string) =>
  call<{ ok: boolean }>(`/api/answer-ratings/${encodeURIComponent(messageId)}`, { method: "DELETE" });
export const getSessionRatings = (sessionId: string) =>
  call<{ ratings: Record<string, { verdict: "right" | "wrong"; reason: string }> }>(
    `/api/answer-ratings/session/${encodeURIComponent(sessionId)}`,
  ).then((r) => r.ratings);
