"use client";

import { useState } from "react";
import { BarChart3, Cpu, HardDrive, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  cancelSubscription,
  formatBillingDate,
  formatBytes,
  formatNumber,
  resumeSubscription,
  type UserUsageSummary,
} from "@/lib/billing-api";

function UsageBar({
  icon,
  label,
  value,
  percent,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  percent: number;
  hint: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--background)] p-4">
      <div className="mb-2 flex items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]">
        <span className="flex items-center gap-1.5">
          {icon}
          {label}
        </span>
        <span className="font-mono text-[var(--foreground)]">{value}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--muted)]">
        <div
          className={`h-full rounded-full transition-all duration-500 ${percent > 85 ? "bg-warning" : "bg-primary"}`}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
      <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">{hint}</p>
    </div>
  );
}

/**
 * Current plan, renewal controls and credit/storage usage. Shared by the
 * pricing page and Settings → Plans & billing.
 */
export function SubscriptionOverview({
  quota,
  onChanged,
  onNotice,
  onError,
  actions,
}: {
  quota: UserUsageSummary;
  /** Called after the subscription changed so the caller can reload. */
  onChanged: () => void | Promise<void>;
  onNotice?: (message: string) => void;
  onError?: (message: string) => void;
  /** Extra controls on the header row (e.g. a link to the pricing page). */
  actions?: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [busy, setBusy] = useState(false);
  const sub = quota.subscription;

  async function run(action: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try {
      await action();
      onNotice?.(ok);
      await onChanged();
    } catch (err: unknown) {
      onError?.(err instanceof Error ? err.message : t("Action failed"));
    } finally {
      setBusy(false);
      setConfirmCancel(false);
    }
  }

  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6">
      <div className="mb-4 flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <BarChart3 className="h-4 w-4 text-primary" />
          {t("Current plan")}
          <span className="text-[var(--muted-foreground)]">·</span>
          {quota.is_unlimited
            ? t("Administrator (unlimited)")
            : quota.plan.name}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-[var(--muted-foreground)]">
          {!quota.is_unlimited && sub && sub.plan_id !== "free" && (
            <>
              {sub.current_period_end ? (
                <span>
                  {sub.cancel_at_period_end
                    ? t("Ends on {{date}}", {
                        date: formatBillingDate(sub.current_period_end),
                      })
                    : t("Renews on {{date}}", {
                        date: formatBillingDate(sub.current_period_end),
                      })}
                  {sub.days_left != null &&
                    ` · ${t("{{days}} days left", { days: sub.days_left })}`}
                </span>
              ) : (
                <span>{t("No expiry (granted by an administrator)")}</span>
              )}
              {sub.current_period_end &&
                (sub.cancel_at_period_end ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void run(
                        resumeSubscription,
                        t("Auto-renewal is back on."),
                      )
                    }
                    className="rounded-lg border border-success/40 px-2.5 py-1 font-semibold text-success hover:bg-success-surface disabled:opacity-50"
                  >
                    {t("Turn auto-renewal back on")}
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setConfirmCancel(true)}
                    className="rounded-lg border border-[var(--border)] px-2.5 py-1 hover:text-destructive disabled:opacity-50"
                  >
                    {t("Cancel auto-renewal")}
                  </button>
                ))}
            </>
          )}
          {actions}
        </div>
      </div>

      {!quota.is_unlimited && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <UsageBar
            icon={<Cpu className="h-3.5 w-3.5" />}
            label={t("Credits today")}
            value={`${formatNumber(quota.usage_today.credits)} / ${formatNumber(quota.usage_today.limit)}`}
            percent={quota.usage_today.percent_used}
            hint={t("{{remaining}} left · resets daily at 00:00 UTC", {
              remaining: formatNumber(quota.usage_today.remaining),
            })}
          />
          <UsageBar
            icon={<Zap className="h-3.5 w-3.5 text-primary" />}
            label={t("Credits this month")}
            value={`${formatNumber(quota.usage_month.credits)} / ${formatNumber(quota.usage_month.limit)}`}
            percent={quota.usage_month.percent_used}
            hint={t("{{tokens}} actual tokens · resets on the 1st", {
              tokens: formatNumber(quota.usage_month.total_tokens),
            })}
          />
          <UsageBar
            icon={<HardDrive className="h-3.5 w-3.5" />}
            label={t("Document storage")}
            value={`${formatBytes(quota.storage.used_bytes)} / ${formatBytes(quota.storage.limit_bytes)}`}
            percent={quota.storage.percent_used}
            hint={t("Up to {{size}} per file", {
              size: formatBytes(quota.storage.max_file_size_bytes),
            })}
          />
        </div>
      )}

      <ConfirmDialog
        open={confirmCancel}
        title={t("Cancel auto-renewal?")}
        tone="danger"
        confirmLabel={t("Confirm")}
        cancelLabel={t("Keep it")}
        busy={busy}
        onConfirm={() =>
          void run(
            cancelSubscription,
            t("Auto-renewal is off. Your plan stays active until the end of the period."),
          )
        }
        onCancel={() => setConfirmCancel(false)}
      >
        {sub?.current_period_end
          ? t(
              "You keep {{plan}} until {{date}}, then your account moves to the free plan.",
              {
                plan: sub.plan_name,
                date: formatBillingDate(sub.current_period_end),
              },
            )
          : t("Your account will move to the free plan.")}
      </ConfirmDialog>
    </div>
  );
}
