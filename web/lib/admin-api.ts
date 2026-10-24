import type { PlanEntitlements } from "@/lib/entitlements-api";
import { apiFetch, apiUrl } from "@/lib/api";
import type {
  BillingInterval,
  PaymentRecord,
  PaymentStatus,
  SubscriptionInfo,
  SubscriptionPlanItem,
} from "@/lib/billing-api";

export type UserRole = "admin" | "rnd" | "educator" | "user";
export type AccountPreset = "standard" | "learner" | "custom";

export interface UserRecord {
  id: string;
  username: string;
  role: UserRole;
  created_at: string;
  disabled?: boolean;
  /** Avatar marker: "", "icon:<name>:<color>", or "img:<version>". */
  avatar?: string;
  preset?: AccountPreset;
  full_name?: string;
  email?: string;
  email_verified?: boolean;
}

export async function listUsers(): Promise<UserRecord[]> {
  const res = await apiFetch(apiUrl("/api/auth/users"));
  if (!res.ok) throw new Error("Failed to fetch users");
  return res.json();
}

/**
 * Lock (disabled=true) or unlock an account. Admins cannot delete accounts;
 * only the owner can, from Settings → Profile.
 */
export async function setUserDisabled(username: string, disabled: boolean): Promise<void> {
  const res = await apiFetch(
    apiUrl(`/api/auth/users/${encodeURIComponent(username)}/status`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ disabled }),
    },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "Failed to update account status");
  }
}

/** Admin: mark a user's registered email as verified. */
export async function verifyUserEmail(username: string): Promise<void> {
  const res = await apiFetch(
    apiUrl(`/api/auth/users/${encodeURIComponent(username)}/verify-email`),
    { method: "POST" },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "Failed to verify email");
  }
}

export async function setUserRole(
  username: string,
  role: UserRole,
): Promise<void> {
  const res = await apiFetch(
    apiUrl(`/api/auth/users/${encodeURIComponent(username)}/role`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? "Failed to update role");
  }
}

export interface CreatedUser {
  user_id: string;
  username: string;
  role: UserRole;
  is_admin: boolean;
  preset: AccountPreset;
}

export async function createUser(
  username: string,
  password: string,
  preset: AccountPreset = "standard",
): Promise<CreatedUser> {
  const res = await apiFetch(apiUrl("/api/auth/users"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, preset }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail) && detail.length > 0 && detail[0]?.msg
          ? String(detail[0].msg)
          : "Failed to create user";
    throw new Error(message);
  }
  return (await res.json()) as CreatedUser;
}

async function adminError(res: Response, fallback: string): Promise<never> {
  const data = await res.json().catch(() => ({}));
  // Fallback messages are English i18n keys; translate them lazily so the
  // current UI language is used.
  const { default: i18n } = await import("i18next");
  const detail = (data as { detail?: unknown }).detail;
  const message =
    typeof detail === "string"
      ? detail
      : Array.isArray(detail) && detail[0]?.msg
        ? String(detail[0].msg)
        : i18n.t(fallback);
  throw new Error(message);
}

async function adminJson<T>(
  path: string,
  method: "GET" | "POST" | "PUT" | "DELETE",
  body?: unknown,
  fallback = "Request failed",
): Promise<T> {
  const res = await apiFetch(apiUrl(path), {
    method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) await adminError(res, fallback);
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

export interface AdminStats {
  total_users: number;
  active_subscriptions: number;
  plan_breakdown: Record<string, number>;
  total_tokens_consumed: number;
  tokens_today: number;
  total_storage_bytes: number;
  total_revenue_usd: number;
  revenue_this_month_usd: number;
  pending_payments: number;
  api_cost_today_usd: number;
  api_cost_month_usd: number;
  gross_margin_month_usd: number;
  cost_by_model_month: { model: string; calls: number; tokens: number; cost_usd: number }[];
}

export function getAdminStats(): Promise<AdminStats> {
  return adminJson<AdminStats>(
    "/api/admin/stats",
    "GET",
    undefined,
    "Failed to fetch admin stats",
  );
}

// ---------------------------------------------------------------------------
// User subscriptions
// ---------------------------------------------------------------------------

export interface AdminUserSubscription {
  id: string;
  username: string;
  role: UserRole;
  plan_id: string;
  plan: string;
  subscription: SubscriptionInfo | null;
  tokens_today: number;
  tokens_month: number;
  limit_day: number;
  limit_month: number;
  cost_month_usd: number;
}

export function listUserSubscriptions(): Promise<AdminUserSubscription[]> {
  return adminJson<AdminUserSubscription[]>(
    "/api/admin/users",
    "GET",
    undefined,
    "Failed to fetch user subscriptions",
  );
}

/**
 * Grant/switch a plan without payment.
 * durationDays: undefined → 30/365 days from interval, 0 → no expiry.
 */
export function updateUserSubscriptionPlan(
  userId: string,
  planId: string,
  durationDays?: number,
  interval: BillingInterval = "monthly",
): Promise<{ subscription: SubscriptionInfo | null }> {
  return adminJson(
    `/api/admin/users/${encodeURIComponent(userId)}/plan`,
    "PUT",
    { plan_id: planId, duration_days: durationDays ?? null, interval },
    "Failed to update user plan",
  );
}

export function cancelUserSubscription(userId: string): Promise<unknown> {
  return adminJson(
    `/api/admin/users/${encodeURIComponent(userId)}/subscription/cancel`,
    "POST",
    undefined,
    "Failed to cancel subscription",
  );
}

// ---------------------------------------------------------------------------
// Plans
// ---------------------------------------------------------------------------

export interface AdminSubscriptionPlan extends SubscriptionPlanItem {
  is_active: boolean;
  allow_custom_api_key: boolean;
  active_subscribers: number;
  /** API cost if one subscriber uses the whole monthly credit quota. */
  max_api_cost_month_usd: number;
  /** max_api_cost_month_usd / price_monthly (null for free plans). */
  max_cost_ratio: number | null;
}

export type PlanPatch = Partial<
  Pick<
    AdminSubscriptionPlan,
    | "name"
    | "description"
    | "sort_order"
    | "price_monthly"
    | "price_yearly"
    | "max_tokens_per_day"
    | "max_tokens_per_month"
    | "max_storage_bytes"
    | "max_upload_file_size_bytes"
    | "allowed_models"
    | "default_model"
    | "allow_custom_api_key"
    | "is_active"
  >
> & { features?: string[]; entitlements?: Partial<PlanEntitlements> };

export function listAdminPlans(): Promise<AdminSubscriptionPlan[]> {
  return adminJson(
    "/api/admin/plans",
    "GET",
    undefined,
    "Failed to fetch admin subscription plans",
  );
}

export function createPlan(
  body: PlanPatch & { id: string; name: string },
): Promise<AdminSubscriptionPlan> {
  return adminJson("/api/admin/plans", "POST", body, "Failed to create plan");
}

export function updatePlanDetails(
  planId: string,
  patch: PlanPatch,
): Promise<AdminSubscriptionPlan> {
  return adminJson(
    `/api/admin/plans/${encodeURIComponent(planId)}`,
    "PUT",
    patch,
    "Failed to update plan details",
  );
}

export function updatePlanModels(
  planId: string,
  allowedModels: string[],
): Promise<unknown> {
  return adminJson(
    `/api/admin/plans/${encodeURIComponent(planId)}/models`,
    "PUT",
    { allowed_models: allowedModels },
    "Failed to update plan models",
  );
}

export function deletePlan(planId: string): Promise<unknown> {
  return adminJson(
    `/api/admin/plans/${encodeURIComponent(planId)}`,
    "DELETE",
    undefined,
    "Failed to delete plan",
  );
}

export function getAvailableModels(): Promise<string[]> {
  return adminJson(
    "/api/admin/available-models",
    "GET",
    undefined,
    "Failed to fetch available models",
  );
}

// ---------------------------------------------------------------------------
// Payments
// ---------------------------------------------------------------------------

export function listAdminPayments(
  status?: PaymentStatus | "",
): Promise<PaymentRecord[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return adminJson(
    `/api/admin/payments${q}`,
    "GET",
    undefined,
    "Failed to fetch payments",
  );
}

export function confirmPayment(txId: string, note?: string): Promise<unknown> {
  return adminJson(
    `/api/admin/payments/${encodeURIComponent(txId)}/confirm`,
    "POST",
    { note: note ?? null },
    "Failed to confirm payment",
  );
}

export interface VnpayStatus {
  configured: boolean;
  environment: "sandbox" | "production";
  payment_url: string;
  tmn_code: string;
  ipn_url: string;
  return_url: string;
}

export function getVnpayStatus(): Promise<VnpayStatus> {
  return adminJson("/api/admin/vnpay/status", "GET", undefined, "Failed to load VNPAY status");
}

export function rejectPayment(txId: string, note?: string): Promise<unknown> {
  return adminJson(
    `/api/admin/payments/${encodeURIComponent(txId)}/reject`,
    "POST",
    { note: note ?? null },
    "Failed to reject payment",
  );
}

export function refundPayment(txId: string, note?: string): Promise<unknown> {
  return adminJson(
    `/api/admin/payments/${encodeURIComponent(txId)}/refund`,
    "POST",
    { note: note ?? null },
    "Failed to refund payment",
  );
}

// ---------------------------------------------------------------------------
// Model price list
// ---------------------------------------------------------------------------

export interface ModelPriceRow {
  id: string;
  display_name: string;
  provider: string;
  input_per_m: number;
  cached_input_per_m: number | null;
  output_per_m: number;
  aliases: string[];
  is_active: boolean;
  notes: string;
  blended_per_m: number;
  multiplier: number;
}

export interface ModelPriceList {
  credit_base_usd_per_mtok: number;
  default_model: string;
  default_model_price_id: string | null;
  prices: ModelPriceRow[];
}

export type ModelPricePatch = Partial<
  Pick<
    ModelPriceRow,
    | "display_name"
    | "provider"
    | "input_per_m"
    | "cached_input_per_m"
    | "output_per_m"
    | "aliases"
    | "is_active"
    | "notes"
  >
>;

export function listModelPrices(): Promise<ModelPriceList> {
  return adminJson("/api/admin/model-prices", "GET", undefined, "Failed to load model prices");
}

export function createModelPrice(
  body: ModelPricePatch & { id: string; input_per_m: number; output_per_m: number },
): Promise<ModelPriceRow> {
  return adminJson("/api/admin/model-prices", "POST", body, "Failed to add model");
}

export function updateModelPrice(id: string, patch: ModelPricePatch): Promise<ModelPriceRow> {
  return adminJson(
    `/api/admin/model-prices/${encodeURIComponent(id)}`,
    "PUT",
    patch,
    "Failed to save model price",
  );
}

export function deleteModelPrice(id: string): Promise<unknown> {
  return adminJson(
    `/api/admin/model-prices/${encodeURIComponent(id)}`,
    "DELETE",
    undefined,
    "Failed to delete model",
  );
}

export async function inviteRnd(email: string) {
  const res = await apiFetch(apiUrl("/api/auth/invite"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const data = (await res.json().catch(() => ({}))) as { detail?: unknown; delivery?: string; link?: string | null };
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data as { ok: boolean; delivery: "email" | "link"; link: string | null };
}

export interface InvitationRow {
  id: string;
  email: string;
  role: string;
  status: "pending" | "accepted" | "revoked";
  expires_at: string;
  created_at: string | null;
}
export async function listInvitations() {
  const res = await apiFetch(apiUrl("/api/auth/invitations"));
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return ((await res.json()) as { items: InvitationRow[] }).items;
}
export async function revokeInvitation(id: string) {
  const res = await apiFetch(apiUrl(`/api/auth/invitations/${id}`), { method: "DELETE" });
  if (!res.ok) {
    const d = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`);
  }
}
