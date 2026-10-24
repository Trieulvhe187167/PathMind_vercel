"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, ArrowRight, Check, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SettingsPageHeader } from "@/components/settings/shared";
import { PaymentHistory } from "@/components/billing/PaymentHistory";
import { SubscriptionOverview } from "@/components/billing/SubscriptionOverview";
import {
  fetchUserQuotaSummary,
  listMyPayments,
  type PaymentRecord,
  type UserUsageSummary,
} from "@/lib/billing-api";

/** Settings → Plans & billing: current plan, usage, renewal and history. */
export default function BillingSettingsSection() {
  const { t } = useTranslation();
  const [quota, setQuota] = useState<UserUsageSummary | null>(null);
  const [payments, setPayments] = useState<PaymentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [summary, history] = await Promise.all([
        fetchUserQuotaSummary(),
        listMyPayments().catch(() => [] as PaymentRecord[]),
      ]);
      setQuota(summary);
      setPayments(history);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("Could not load your plan"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const plansLink = (
    <Link
      href="/pricing"
      className="inline-flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground shadow-sm transition-colors hover:opacity-90"
    >
      {t("View plans & upgrade")}
      <ArrowRight size={14} />
    </Link>
  );

  return (
    <div className="space-y-5">
      <SettingsPageHeader
        title={t("Plans & billing")}
        description={t(
          "Your subscription, credit and storage usage, renewal and payment history.",
        )}
        actions={plansLink}
      />

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <button type="button" onClick={() => setError("")} aria-label={t("Close")}>
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
      {notice && (
        <div className="flex items-center gap-2 rounded-xl border border-success/30 bg-success-surface p-3 text-sm text-success">
          <Check className="h-4 w-4 shrink-0" />
          <span className="flex-1">{notice}</span>
          <button type="button" onClick={() => setNotice("")} aria-label={t("Close")}>
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-[var(--muted-foreground)]">
          <RefreshCw className="h-4 w-4 animate-spin" /> {t("Loading…")}
        </div>
      ) : quota ? (
        <>
          <SubscriptionOverview
            quota={quota}
            onChanged={load}
            onNotice={setNotice}
            onError={setError}
          />
          <PaymentHistory
            payments={payments}
            emptyText={t("No payments yet.")}
          />
        </>
      ) : (
        <p className="text-sm text-[var(--muted-foreground)]">
          {t("Sign in to see your plan.")}
        </p>
      )}
    </div>
  );
}
