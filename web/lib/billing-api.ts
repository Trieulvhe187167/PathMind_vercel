import i18n from "i18next";
import { apiFetch, apiUrl } from "@/lib/api";
import type { PlanEntitlements } from "@/lib/entitlements-api";

export type BillingInterval = "monthly" | "yearly";
export type PaymentGateway = "vnpay";
export type PaymentStatus =
  | "pending"
  | "completed"
  | "failed"
  | "canceled"
  | "expired"
  | "refunded";

export interface SubscriptionPlanItem {
  id: string;
  name: string;
  description?: string;
  sort_order?: number;
  price_usd?: number;
  price_monthly: number;
  price_yearly: number;
  max_tokens_per_day: number;
  max_tokens_per_month: number;
  max_storage_bytes: number;
  max_upload_file_size_bytes: number;
  allowed_models: string[];
  /** Model used when a user of this plan has not picked one ("" = deployment default). */
  default_model?: string;
  allow_custom_api_key?: boolean;
  is_active?: boolean;
  /** Features shown on the pricing card (custom or generated from limits). */
  features: string[];
  /** Only the admin-entered features (empty = auto-generated). */
  custom_features?: string[];
  /** Paid-only surfaces and allowances (null limit = unlimited). */
  entitlements?: PlanEntitlements;
}

export interface SubscriptionInfo {
  id: string;
  plan_id: string;
  plan_name: string;
  status: "active" | "canceled" | "expired" | "replaced";
  billing_interval: BillingInterval;
  source: string;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  days_left: number | null;
}

export interface UserUsageSummary {
  user_id: string;
  role: string;
  is_unlimited: boolean;
  plan: SubscriptionPlanItem;
  subscription: SubscriptionInfo | null;
  /** Model used when the user does not pick one, with its credit multiplier. */
  default_model: { id: string; name: string; multiplier: number } | null;
  /** Limits and usage are in credits (cost-weighted tokens). */
  usage_today: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    credits: number;
    limit: number;
    remaining: number;
    percent_used: number;
  };
  usage_month: {
    total_tokens: number;
    credits: number;
    limit: number;
    remaining: number;
    percent_used: number;
  };
  storage: {
    used_bytes: number;
    limit_bytes: number;
    max_file_size_bytes: number;
    percent_used: number;
  };
}

export interface PaymentRecord {
  id: string;
  transaction_id: string;
  user_id: string;
  username?: string | null;
  plan_id: string;
  plan_name: string;
  amount: number;
  currency: string;
  amount_vnd: number | null;
  billing_interval: BillingInterval;
  payment_method: string;
  status: PaymentStatus;
  created_at: string | null;
  paid_at: string | null;
  confirmed_by?: string | null;
  note?: string | null;
}

export interface CheckoutSession extends PaymentRecord {
  gateway: PaymentGateway;
  demo_mode: boolean;
  expires_at: string;
  transfer_content: string;
  qr_image_url: string | null;
  /** VNPAY: signed payment page URL to send the browser to. */
  checkout_url?: string | null;
  bank: {
    bank_bin: string;
    bank_name: string;
    account_no: string;
    account_name: string;
  } | null;
  message: string;
}

export interface BillingConfig {
  demo_mode: boolean;
  usd_vnd_rate: number;
  gateways: {
    id: PaymentGateway;
    label: string;
    enabled: boolean;
    demo_only: boolean;
  }[];
  bank: { bank_name: string; account_name: string } | null;
}

async function readError(res: Response, fallback: string): Promise<never> {
  const data = await res.json().catch(() => ({}));
  const detail = (data as { detail?: unknown }).detail;
  throw new Error(typeof detail === "string" ? detail : i18n.t(fallback));
}

async function postJson<T>(
  path: string,
  body?: unknown,
  fallback = "Request failed",
): Promise<T> {
  const res = await apiFetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) await readError(res, fallback);
  return res.json() as Promise<T>;
}

export async function fetchBillingConfig(): Promise<BillingConfig> {
  const res = await apiFetch(apiUrl("/api/billing/config"));
  if (!res.ok) await readError(res, "Failed to fetch billing config");
  return res.json();
}

export interface ModelRate {
  id: string;
  name: string;
  /** Credits consumed per token relative to the cheapest tier (×1). */
  multiplier: number;
}

export async function fetchModelRates(): Promise<{
  credit_base_usd_per_mtok: number;
  rates: ModelRate[];
}> {
  const res = await apiFetch(apiUrl("/api/billing/model-rates"));
  if (!res.ok) await readError(res, "Failed to fetch model rates");
  return res.json();
}

/** Find the rate for a model id as listed in a plan (tolerates version suffixes). */
export function rateFor(
  rates: ModelRate[],
  model: string,
): ModelRate | undefined {
  const name = model.toLowerCase().split("/").pop() ?? "";
  return (
    rates.find((r) => r.id === name) ??
    [...rates]
      .sort((a, b) => b.id.length - a.id.length)
      .find((r) => name.startsWith(r.id))
  );
}

export function formatMultiplier(m: number): string {
  if (m <= 0) return i18n.t("free");
  if (m < 1) return `×${m.toFixed(1)}`;
  return `×${m >= 10 ? Math.round(m) : m.toFixed(1).replace(/\.0$/, "")}`;
}

export function formatCredits(n: number): string {
  if (n >= 1_000_000)
    return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

export async function fetchSubscriptionPlans(): Promise<
  SubscriptionPlanItem[]
> {
  const lang = i18n.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
  const res = await apiFetch(apiUrl(`/api/billing/plans?lang=${lang}`));
  if (!res.ok) await readError(res, "Failed to fetch subscription plans");
  const data = await res.json();
  return Array.isArray(data) ? data : [];
}

/** Returns null when the visitor is not logged in. */
export async function fetchUserQuotaSummary(): Promise<UserUsageSummary | null> {
  const res = await apiFetch(apiUrl("/api/billing/summary"), {
    skipAuthRedirect: true,
  });
  if (res.status === 401) return null;
  if (!res.ok) await readError(res, "Failed to fetch quota summary");
  return res.json();
}

/** Switch to a FREE plan (paid plans must use createCheckoutSession). */
export function switchToFreePlan(planId: string) {
  return postJson<{
    status: string;
    message: string;
    subscription: SubscriptionInfo | null;
  }>("/api/billing/upgrade", { plan_id: planId }, "Could not switch plans");
}

export function createCheckoutSession(
  planId: string,
  interval: BillingInterval,
  gateway: PaymentGateway,
): Promise<CheckoutSession> {
  return postJson<CheckoutSession>(
    "/api/billing/checkout",
    { plan_id: planId, interval, gateway },
    "Could not create the payment",
  );
}

export async function fetchPaymentStatus(txId: string): Promise<PaymentRecord> {
  const res = await apiFetch(
    apiUrl(`/api/billing/payments/${encodeURIComponent(txId)}`),
  );
  if (!res.ok) await readError(res, "Failed to fetch payment");
  return res.json();
}

export async function listMyPayments(): Promise<PaymentRecord[]> {
  const res = await apiFetch(apiUrl("/api/billing/payments"), {
    skipAuthRedirect: true,
  });
  if (res.status === 401) return [];
  if (!res.ok) await readError(res, "Failed to fetch payments");
  return res.json();
}

export function cancelPendingPayment(txId: string) {
  return postJson<PaymentRecord>(
    `/api/billing/payments/${encodeURIComponent(txId)}/cancel`,
    undefined,
    "Could not cancel the payment",
  );
}

/** Back from VNPAY: forward the signed vnp_* parameters for verification. */
export function confirmVnpayReturn(params: Record<string, string>) {
  return postJson<PaymentRecord & { vnpay_code: string; vnpay_response_code?: string }>(
    "/api/billing/payments/vnpay-return",
    { params },
    "Could not check the VNPAY payment",
  );
}

export function simulatePayment(txId: string) {
  return postJson<{
    payment: PaymentRecord;
    subscription: SubscriptionInfo | null;
  }>(
    `/api/billing/payments/${encodeURIComponent(txId)}/simulate`,
    undefined,
    "Payment simulation failed",
  );
}

export function cancelSubscription() {
  return postJson<{ status: string; subscription: SubscriptionInfo }>(
    "/api/billing/cancel",
    undefined,
    "Could not cancel auto-renewal",
  );
}

export function resumeSubscription() {
  return postJson<{ status: string; subscription: SubscriptionInfo }>(
    "/api/billing/resume",
    undefined,
    "Could not turn auto-renewal back on",
  );
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) {
    const gb = bytes / 1024 ** 3;
    return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`;
  }
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

export function formatVnd(amount: number | null | undefined): string {
  if (amount == null) return "—";
  return `${amount.toLocaleString("vi-VN")} ₫`;
}

export function formatUsd(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

/** Locale for dates and numbers in billing screens (follows the UI language). */
export function billingLocale(): string {
  return i18n.language?.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
}

export function formatBillingDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleDateString(billingLocale(), {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
}

export function formatNumber(n: number): string {
  return n.toLocaleString(billingLocale());
}

/** English keys — render with ``t(PAYMENT_STATUS_LABEL[status])``. */
export const PAYMENT_STATUS_LABEL: Record<PaymentStatus, string> = {
  pending: "Awaiting payment",
  completed: "Paid",
  failed: "Failed",
  canceled: "Canceled",
  expired: "Expired",
  refunded: "Refunded",
};
