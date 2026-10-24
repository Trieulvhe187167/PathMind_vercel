"use client";

import { useTranslation } from "react-i18next";
import {
  PAYMENT_STATUS_LABEL,
  formatBillingDate,
  formatUsd,
  type PaymentRecord,
} from "@/lib/billing-api";

export function PaymentHistory({
  payments,
  emptyText,
}: {
  payments: PaymentRecord[];
  /** Shown when there are no payments; omit to render nothing instead. */
  emptyText?: string;
}) {
  const { t } = useTranslation();
  if (payments.length === 0 && !emptyText) return null;
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6">
      <h2 className="mb-4 text-sm font-semibold">{t("Payment history")}</h2>
      {payments.length === 0 ? (
        <p className="text-sm text-[var(--muted-foreground)]">{emptyText}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-[var(--muted-foreground)]">
              <tr>
                <th className="py-2 pr-4">{t("Transaction ID")}</th>
                <th className="py-2 pr-4">{t("Plan", { context: "billing" })}</th>
                <th className="py-2 pr-4">{t("Billing cycle")}</th>
                <th className="py-2 pr-4">{t("Amount")}</th>
                <th className="py-2 pr-4">{t("Created")}</th>
                <th className="py-2">{t("Status")}</th>
              </tr>
            </thead>
            <tbody>
              {payments.map((pm) => (
                <tr key={pm.id} className="border-t border-[var(--border)]">
                  <td className="py-2 pr-4 font-mono">{pm.transaction_id}</td>
                  <td className="py-2 pr-4">{pm.plan_name}</td>
                  <td className="py-2 pr-4">
                    {pm.billing_interval === "yearly" ? t("Yearly") : t("Monthly")}
                  </td>
                  <td className="py-2 pr-4">{formatUsd(pm.amount)}</td>
                  <td className="py-2 pr-4">{formatBillingDate(pm.created_at)}</td>
                  <td className="py-2">
                    {PAYMENT_STATUS_LABEL[pm.status]
                      ? t(PAYMENT_STATUS_LABEL[pm.status])
                      : pm.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
