"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Trans, useTranslation } from "react-i18next";
import {
  AlertCircle,
  CheckCircle2,
  CreditCard,
  RefreshCw,
  RotateCcw,
  X,
  XCircle,
} from "lucide-react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { fetchAuthStatus } from "@/lib/auth";
import {
  confirmPayment,
  getAdminStats,
  getVnpayStatus,
  listAdminPayments,
  refundPayment,
  rejectPayment,
  type AdminStats,
  type VnpayStatus,
} from "@/lib/admin-api";
import {
  formatUsd,
  formatVnd,
  type PaymentRecord,
  type PaymentStatus,
} from "@/lib/billing-api";

const FILTERS: { value: PaymentStatus | ""; label: string }[] = [
  { value: "pending", label: "Awaiting confirmation" },
  { value: "completed", label: "Paid" },
  { value: "", label: "All" },
];

const STATUS_STYLE: Record<PaymentStatus, string> = {
  pending: "bg-warning-surface text-warning",
  completed: "bg-success-surface text-success",
  failed: "bg-destructive/15 text-destructive",
  canceled: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  expired: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  refunded: "bg-info-surface text-info",
};

// English i18n keys; translated at render time.
const STATUS_LABEL: Record<PaymentStatus, string> = {
  pending: "Awaiting payment",
  completed: "Paid",
  failed: "Failed",
  canceled: "Cancelled",
  expired: "Expired",
  refunded: "Refunded",
};

type Action = { kind: "confirm" | "reject" | "refund"; payment: PaymentRecord };

function formatDateTime(iso: string | null, locale: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(locale);
}

export default function AdminPaymentsPage() {
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("zh") ? "zh-CN" : "en-US";
  const [filter, setFilter] = useState<PaymentStatus | "">("pending");
  const [payments, setPayments] = useState<PaymentRecord[]>([]);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [vnpay, setVnpay] = useState<VnpayStatus | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [rows, s, v] = await Promise.all([
        listAdminPayments(filter),
        getAdminStats().catch(() => null),
        getVnpayStatus().catch(() => null),
      ]);
      setPayments(rows);
      setStats(s);
      setVnpay(v);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("Failed to load payments"));
    } finally {
      setLoading(false);
    }
  }, [filter, t]);

  useEffect(() => {
    void fetchAuthStatus().then((auth) => {
      if (!auth?.authenticated)
        return router.replace("/login?next=/admin/payments");
      if (!auth.is_admin) return router.replace("/");
      setReady(true);
    });
  }, [router]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  async function runAction() {
    if (!action) return;
    setBusy(true);
    try {
      const tx = action.payment.transaction_id;
      if (action.kind === "confirm")
        await confirmPayment(tx, note || undefined);
      else if (action.kind === "reject")
        await rejectPayment(tx, note || undefined);
      else await refundPayment(tx, note || undefined);
      setAction(null);
      setNote("");
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("Action failed"));
      setAction(null);
    } finally {
      setBusy(false);
    }
  }

  const actionTitle =
    action?.kind === "confirm"
      ? t("Confirm payment received?")
      : action?.kind === "reject"
        ? t("Reject payment?")
        : t("Mark as refunded?");

  return (
    <div className="h-full overflow-y-auto bg-[var(--background)] px-4 py-8 text-[var(--foreground)] sm:px-6 lg:px-8">
      <div className="mx-auto max-w-6xl">

        <div className="mb-6 flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
          <div>
            <h1 className="font-serif text-xl font-semibold text-[var(--foreground)]">
              {" "}
              {t("Payment transactions")}
            </h1>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              <Trans
                i18nKey="Match bank statements by <b>transfer description</b> (= transaction ID), then confirm to activate the plan for the user."
                components={{ b: <b /> }}
              />
            </p>
          </div>
          <button
            onClick={() => void load()}
            className="flex items-center gap-1.5 self-start rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--muted-foreground)] hover:bg-[var(--card)]"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />{" "}
            {t("Reload")}
          </button>
        </div>

        {vnpay && (
          <div className="mb-6 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 text-xs">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold">
              <CreditCard size={16} /> VNPAY
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                  !vnpay.configured
                    ? "bg-destructive/10 text-destructive"
                    : vnpay.environment === "sandbox"
                      ? "bg-warning-surface text-warning"
                      : "bg-success-surface text-success"
                }`}
              >
                {!vnpay.configured
                  ? t("not configured")
                  : vnpay.environment === "sandbox"
                    ? t("Sandbox")
                    : t("Production")}
              </span>
              {vnpay.tmn_code && (
                <span className="font-mono text-[11px] text-[var(--muted-foreground)]">
                  {vnpay.tmn_code}
                </span>
              )}
            </div>
            <dl className="grid gap-1 text-[var(--muted-foreground)]">
              <div>
                {t("IPN URL to register with VNPAY")}:{" "}
                <code className="text-[var(--foreground)]">{vnpay.ipn_url}</code>
              </div>
              <div>
                {t("Return URL")}:{" "}
                <code className="text-[var(--foreground)]">{vnpay.return_url}</code>
              </div>
            </dl>
          </div>
        )}

        {stats && (
          <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-3">
            {[
              {
                label: t("Revenue this month"),
                value: formatUsd(stats.revenue_this_month_usd),
              },
              {
                label: t("API cost this month"),
                value: formatUsd(stats.api_cost_month_usd ?? 0),
              },
              {
                label: t("Gross margin this month"),
                value: formatUsd(stats.gross_margin_month_usd ?? 0),
                tone:
                  (stats.gross_margin_month_usd ?? 0) < 0
                    ? "text-destructive"
                    : "text-success",
              },
              {
                label: t("Total revenue"),
                value: formatUsd(stats.total_revenue_usd),
              },
              {
                label: t("Awaiting confirmation"),
                value: stats.pending_payments.toLocaleString(),
              },
              {
                label: t("Paid users"),
                value: stats.active_subscriptions.toLocaleString(),
              },
            ].map((c: { label: string; value: string; tone?: string }) => (
              <div
                key={c.label}
                className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"
              >
                <div className="mb-1 text-xs text-[var(--muted-foreground)]">
                  {c.label}
                </div>
                <div
                  className={`text-2xl font-semibold ${c.tone ?? "text-[var(--foreground)]"}`}
                >
                  {c.value}
                </div>
              </div>
            ))}
          </div>
        )}

        {stats && stats.cost_by_model_month?.length > 0 && (
          <div className="mb-6 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4">
            <div className="mb-2 text-xs font-semibold text-[var(--foreground)]">
              {t("API cost by model (this month)")}
            </div>
            <table className="w-full text-left text-xs">
              <thead className="text-[var(--muted-foreground)]">
                <tr>
                  <th className="py-1">{t("Model")}</th>
                  <th className="py-1 text-right">{t("Calls")}</th>
                  <th className="py-1 text-right">{t("Tokens")}</th>
                  <th className="py-1 text-right">{t("Cost")}</th>
                </tr>
              </thead>
              <tbody>
                {stats.cost_by_model_month.map((m) => (
                  <tr key={m.model} className="border-t border-[var(--border)]">
                    <td className="py-1 font-mono">{m.model}</td>
                    <td className="py-1 text-right">
                      {m.calls.toLocaleString()}
                    </td>
                    <td className="py-1 text-right">
                      {m.tokens.toLocaleString()}
                    </td>
                    <td className="py-1 text-right">
                      ${m.cost_usd.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[10px] text-[var(--muted-foreground)]">
              {t(
                "Revenue is recorded by payment date (yearly plans count entirely toward the month paid). Costs use the model price list on the Plans page.",
              )}
            </p>
          </div>
        )}

        <div className="mb-4 flex gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.label}
              onClick={() => setFilter(f.value)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                filter === f.value
                  ? "bg-primary text-primary-foreground"
                  : "border border-[var(--border)] text-[var(--muted-foreground)] hover:bg-[var(--card)]"
              }`}
            >
              {t(f.label)}
            </button>
          ))}
        </div>

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError("")} aria-label={t("Close")}>
              <X className="h-4 w-4" />
            </button>
          </div>
        )}

        <div className="overflow-x-auto rounded-2xl border border-[var(--border)] bg-[var(--card)]">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-[var(--border)] text-[var(--muted-foreground)]">
              <tr>
                <th className="px-4 py-3">{t("Code / Transfer description")}</th>
                <th className="px-4 py-3">{t("User")}</th>
                <th className="px-4 py-3">{t("Plan", { context: "billing" })}</th>
                <th className="px-4 py-3">{t("Amount")}</th>
                <th className="px-4 py-3">{t("Method")}</th>
                <th className="px-4 py-3">{t("Time")}</th>
                <th className="px-4 py-3">{t("Status")}</th>
                <th className="px-4 py-3 text-right">{t("Actions")}</th>
              </tr>
            </thead>
            <tbody>
              {!loading && payments.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-10 text-center text-[var(--muted-foreground)]"
                  >
                    {t("No transactions.")}
                  </td>
                </tr>
              )}
              {payments.map((p) => (
                <tr
                  key={p.id}
                  className="border-t border-[var(--border)] align-top"
                >
                  <td className="px-4 py-3 font-mono font-semibold">
                    {p.transaction_id}
                  </td>
                  <td className="px-4 py-3">{p.username ?? p.user_id}</td>
                  <td className="px-4 py-3">
                    {p.plan_name}
                    <div className="text-[10px] text-[var(--muted-foreground)]">
                      {p.billing_interval === "yearly"
                        ? t("12 months")
                        : t("1 month")}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {formatUsd(p.amount)}
                    <div className="text-[10px] text-[var(--muted-foreground)]">
                      {formatVnd(p.amount_vnd)}
                    </div>
                  </td>
                  <td className="px-4 py-3">{p.payment_method}</td>
                  <td className="px-4 py-3">
                    {formatDateTime(p.created_at, locale)}
                    {p.paid_at && (
                      <div className="text-[10px] text-success">
                        {t("Paid")}: {formatDateTime(p.paid_at, locale)}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_STYLE[p.status] ?? ""}`}
                    >
                      {STATUS_LABEL[p.status] ? t(STATUS_LABEL[p.status]) : p.status}
                    </span>
                    {(p.confirmed_by || p.note) && (
                      <div className="mt-1 max-w-[180px] text-[10px] text-[var(--muted-foreground)]">
                        {[p.confirmed_by, p.note].filter(Boolean).join(" · ")}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1.5">
                      {(p.status === "pending" ||
                        p.status === "expired" ||
                        p.status === "failed") && (
                        <button
                          onClick={() =>
                            setAction({ kind: "confirm", payment: p })
                          }
                          title={t("Confirm payment received")}
                          className="rounded-lg p-1.5 text-success hover:bg-success-surface"
                        >
                          <CheckCircle2 size={16} />
                        </button>
                      )}
                      {p.status === "pending" && (
                        <button
                          onClick={() =>
                            setAction({ kind: "reject", payment: p })
                          }
                          title={t("Reject")}
                          className="rounded-lg p-1.5 text-destructive hover:bg-destructive/10"
                        >
                          <XCircle size={16} />
                        </button>
                      )}
                      {p.status === "completed" && (
                        <button
                          onClick={() =>
                            setAction({ kind: "refund", payment: p })
                          }
                          title={t("Mark as refunded")}
                          className="rounded-lg p-1.5 text-muted-foreground hover:bg-info-surface"
                        >
                          <RotateCcw size={16} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmDialog
        open={action !== null}
        title={actionTitle}
        tone={action?.kind === "confirm" ? "default" : "danger"}
        confirmLabel={t("Confirm")}
        busy={busy}
        onConfirm={() => void runAction()}
        onCancel={() => {
          setAction(null);
          setNote("");
        }}
      >
        {action && (
          <div className="space-y-2">
            <p>
              <b className="font-mono">{action.payment.transaction_id}</b> —{" "}
              {action.payment.username ?? action.payment.user_id} —{" "}
              {action.payment.plan_name} — {formatUsd(action.payment.amount)} (
              {formatVnd(action.payment.amount_vnd)})
            </p>
            {action.kind === "confirm" && (
              <p>
                {t(
                  "The plan will be activated/renewed for the user immediately.",
                )}
              </p>
            )}
            {action.kind === "refund" && (
              <p>
                {t(
                  "If this payment is keeping the current plan active, the plan will be cancelled immediately.",
                )}
              </p>
            )}
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("Note (optional)")}
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-xs text-[var(--foreground)]"
            />
          </div>
        )}
      </ConfirmDialog>
    </div>
  );
}
