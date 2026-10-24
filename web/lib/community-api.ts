import i18n from "i18next";

import { apiFetch, apiUrl } from "@/lib/api";
import { notifyEntitlementsChanged } from "@/lib/entitlements-api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export const SKILL_CATEGORIES = [
  "academics",
  "languages",
  "skills-interests",
  "companions",
  "educators",
  "productivity",
  "other",
] as const;
export type SkillCategory = (typeof SKILL_CATEGORIES)[number];

export const FORUM_CATEGORIES = [
  "general",
  "help",
  "skills",
  "study",
  "feedback",
  "announcements",
] as const;
export type ForumCategory = (typeof FORUM_CATEGORIES)[number];

export interface CommunitySkill {
  id: string;
  slug: string;
  title: string;
  description: string;
  category: string;
  tags: string[];
  author_id: string;
  author: string;
  version: number;
  install_count: number;
  like_count: number;
  liked: boolean;
  installed_as: string | null;
  can_edit: boolean;
  file_count: number;
  created_at: string;
  updated_at: string;
}

export interface CommunitySkillDetail extends CommunitySkill {
  content: string;
  files: string[];
  hidden?: boolean;
  skipped_files?: string[];
}

export interface SkillListResponse {
  skills: CommunitySkill[];
  total: number;
  page: number;
  page_size: number;
  categories: { id: string; count: number }[];
}

export type SkillSource = "skill" | "cowriter" | "text";

export interface PublishSkillPayload {
  source: SkillSource;
  skill_name?: string;
  doc_id?: string;
  content?: string;
  title: string;
  description?: string;
  category: string;
  tags?: string[];
}

export interface ForumPost {
  id: string;
  title: string;
  body: string;
  truncated: boolean;
  category: string;
  author_id: string;
  author: string;
  score: number;
  my_vote: number;
  comment_count: number;
  pinned: boolean;
  locked: boolean;
  skill: { slug: string; title: string } | null;
  can_edit: boolean;
  created_at: string;
  edited_at: string;
}

export interface ForumComment {
  id: string;
  parent_id: string | null;
  author_id: string | null;
  author: string;
  body: string;
  deleted: boolean;
  score: number;
  my_vote: number;
  can_edit: boolean;
  created_at: string;
  edited_at: string;
}

export interface PostListResponse {
  posts: ForumPost[];
  total: number;
  page: number;
  page_size: number;
  is_admin: boolean;
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

export class CommunityApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function call<T>(
  path: string,
  init?: RequestInit,
  fallback = "Request failed",
): Promise<T> {
  const res = await apiFetch(apiUrl(path), { cache: "no-store", ...init });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = (data as { detail?: unknown }).detail;
    throw new CommunityApiError(
      typeof detail === "string"
        ? detail
        : i18n.t(fallback, { defaultValue: fallback }),
      res.status,
    );
  }
  return res.json() as Promise<T>;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const skillPath = (slug: string) => `/api/community/skills/${encodeURIComponent(slug)}`;
const postPath = (id: string) => `/api/community/posts/${encodeURIComponent(id)}`;

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export function listCommunitySkills(params: {
  q?: string;
  category?: string;
  sort?: "popular" | "new" | "likes";
  mine?: boolean;
  page?: number;
}): Promise<SkillListResponse> {
  return call(`/api/community/skills${qs(params)}`, undefined, "Could not load skills");
}

export function getCommunitySkill(slug: string): Promise<CommunitySkillDetail> {
  return call(skillPath(slug), undefined, "Could not load the skill");
}

export function publishCommunitySkill(payload: PublishSkillPayload): Promise<CommunitySkillDetail> {
  return call("/api/community/skills", send("POST", payload), "Could not share the skill");
}

export function uploadCommunitySkill(
  file: File,
  meta: { title: string; description?: string; category: string; tags?: string[] },
): Promise<CommunitySkillDetail> {
  const body = new FormData();
  body.append("file", file);
  body.append("title", meta.title);
  body.append("description", meta.description ?? "");
  body.append("category", meta.category);
  body.append("tags", (meta.tags ?? []).join(","));
  return call("/api/community/skills/upload", { method: "POST", body }, "Could not share the skill");
}

export function updateCommunitySkill(
  slug: string,
  payload: Partial<PublishSkillPayload>,
): Promise<CommunitySkillDetail> {
  return call(skillPath(slug), send("PUT", payload), "Could not update the skill");
}

export function updateCommunitySkillUpload(slug: string, file: File): Promise<CommunitySkillDetail> {
  const body = new FormData();
  body.append("file", file);
  return call(`${skillPath(slug)}/upload`, { method: "PUT", body }, "Could not update the skill");
}

export function deleteCommunitySkill(slug: string): Promise<unknown> {
  return call(skillPath(slug), { method: "DELETE" }, "Could not remove the skill");
}

export function toggleSkillLike(slug: string): Promise<{ liked: boolean; like_count: number }> {
  return call(`${skillPath(slug)}/like`, { method: "POST" }, "Action failed");
}

export function installCommunitySkill(
  slug: string,
  options: { name?: string; force?: boolean } = {},
): Promise<{ name: string; install_count: number; skipped: string[] }> {
  return call<{ name: string; install_count: number; skipped: string[] }>(
    `${skillPath(slug)}/install`,
    send("POST", options),
    "Could not add the skill",
  ).then((result) => {
    notifyEntitlementsChanged();
    return result;
  });
}

// ---------------------------------------------------------------------------
// Forum
// ---------------------------------------------------------------------------

export function listPosts(params: {
  category?: string;
  sort?: "hot" | "new" | "top";
  q?: string;
  mine?: boolean;
  page?: number;
}): Promise<PostListResponse> {
  return call(`/api/community/posts${qs(params)}`, undefined, "Could not load posts");
}

export function getPost(id: string): Promise<{
  post: ForumPost;
  comments: ForumComment[];
  is_admin: boolean;
}> {
  return call(postPath(id), undefined, "Could not load the post");
}

export function createPost(payload: {
  title: string;
  body: string;
  category: string;
  skill_slug?: string;
}): Promise<ForumPost> {
  return call("/api/community/posts", send("POST", payload), "Could not publish the post");
}

export function updatePost(
  id: string,
  payload: { title?: string; body?: string; category?: string },
): Promise<ForumPost> {
  return call(postPath(id), send("PUT", payload), "Could not save the post");
}

export function deletePost(id: string): Promise<unknown> {
  return call(postPath(id), { method: "DELETE" }, "Could not delete the post");
}

export function votePost(id: string, value: -1 | 0 | 1): Promise<{ score: number; my_vote: number }> {
  return call(`${postPath(id)}/vote`, send("POST", { value }), "Action failed");
}

export function moderatePost(
  id: string,
  payload: { pinned?: boolean; locked?: boolean },
): Promise<{ pinned: boolean; locked: boolean }> {
  return call(`${postPath(id)}/moderate`, send("POST", payload), "Action failed");
}

export function createComment(
  postId: string,
  body: string,
  parentId?: string | null,
): Promise<ForumComment> {
  return call(
    `${postPath(postId)}/comments`,
    send("POST", { body, parent_id: parentId ?? null }),
    "Could not post the comment",
  );
}

export function updateComment(id: string, body: string): Promise<{ body: string; edited_at: string }> {
  return call(
    `/api/community/comments/${encodeURIComponent(id)}`,
    send("PUT", { body }),
    "Could not save the comment",
  );
}

export function deleteComment(id: string): Promise<unknown> {
  return call(
    `/api/community/comments/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    "Could not delete the comment",
  );
}

export function voteComment(id: string, value: -1 | 0 | 1): Promise<{ score: number; my_vote: number }> {
  return call(
    `/api/community/comments/${encodeURIComponent(id)}/vote`,
    send("POST", { value }),
    "Action failed",
  );
}

// ---------------------------------------------------------------------------
// Reports (moderation)
// ---------------------------------------------------------------------------

export const REPORT_REASONS = [
  "spam",
  "harassment",
  "inappropriate",
  "misinformation",
  "copyright",
  "other",
] as const;
export type ReportReason = (typeof REPORT_REASONS)[number];
export type ReportTarget = "post" | "comment" | "skill";

export interface ReportGroup {
  target_type: ReportTarget;
  target_id: string;
  status: "open" | "dismissed" | "removed";
  resolved_by: string;
  resolved_at: string;
  title?: string;
  excerpt?: string;
  author?: string;
  post_id?: string;
  skill_slug?: string;
  removed?: boolean;
  missing?: boolean;
  count: number;
  reports: { reason: string; details: string; reporter: string; created_at: string }[];
}

export function reportContent(payload: {
  target_type: ReportTarget;
  target_id: string;
  reason: ReportReason;
  details?: string;
}): Promise<{ message: string }> {
  return call("/api/community/reports", send("POST", payload), "Could not send the report");
}

export function listReports(
  status: "open" | "resolved" = "open",
): Promise<{ items: ReportGroup[]; open_targets: number }> {
  return call(`/api/community/reports${qs({ status })}`, undefined, "Could not load reports");
}

export function resolveReports(payload: {
  target_type: ReportTarget;
  target_id: string;
  action: "dismiss" | "remove";
}): Promise<unknown> {
  return call("/api/community/reports/resolve", send("POST", payload), "Action failed");
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

export interface CommunityNotification {
  id: string;
  kind: "post_comment" | "comment_reply" | "skill_installed" | "content_removed" | string;
  actor: string;
  post_id: string | null;
  post_title: string;
  comment_id: string | null;
  skill_slug: string | null;
  skill_title: string;
  preview: string;
  read: boolean;
  created_at: string;
}

export function listNotifications(): Promise<{ items: CommunityNotification[]; unread: number }> {
  return call("/api/community/notifications", undefined, "Could not load notifications");
}

export function fetchUnreadCount(): Promise<{ unread: number }> {
  return call("/api/community/notifications/unread-count", undefined, "Request failed");
}

export function markNotificationsRead(payload: { ids?: string[]; all?: boolean }): Promise<{ unread: number }> {
  return call("/api/community/notifications/read", send("POST", payload), "Action failed");
}
