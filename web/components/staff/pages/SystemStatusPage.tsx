"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import StaffPage from "@/components/staff/StaffPage";
import { getSystemStatus, listWrongAnswers, type SystemStatus, type WrongAnswer } from "@/lib/staff-api";

const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;

export default function SystemStatusPage() {
  const { t } = useTranslation();
  const [s, setS] = useState<SystemStatus | null>(null);
  const [err, setErr] = useState("");
  const [wrong, setWrong] = useState<WrongAnswer[]>([]);
  const load = useCallback(() => {
    getSystemStatus().then(setS).catch((e: Error) => setErr(e.message));
    listWrongAnswers().then(setWrong).catch(() => undefined);
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  const cards = s
    ? [
        [t("Accounts"), String(s.accounts.total), Object.entries(s.accounts.by_role).map(([k, v]) => `${k}: ${v}`).join(" · ")],
        [t("Active subscriptions"), String(s.subscriptions_active), ""],
        [t("Payments pending"), String(s.payments_pending), ""],
        [t("Recommendations waiting"), String(s.recommendations_pending), ""],
        [t("Answers marked wrong (7 days)"), String(s.answers_wrong_7d), ""],
        [t("Knowledge"), `${s.knowledge.nodes} ${t("nodes")}`, `${s.knowledge.domains} ${t("domains")} · ${s.knowledge.documents} ${t("documents")} · ${mb(s.knowledge.document_bytes)}`],
        [t("Last 24 hours"), `${s.activity_24h.store_actions} ${t("store actions")}`, `${s.activity_24h.credits_used_today.toLocaleString()} ${t("AI credits today")}`],
      ]
    : [];

  return (
    <StaffPage title={t("System Status")} subtitle={t("Read-only health of this deployment. Refreshes every 30 seconds.")}>
      {err && <p role="alert" className="text-[13px] text-[var(--destructive)]">{err}</p>}
      {s && (
        <>
          <ul className="mb-5 space-y-1.5">
            {s.checks.map((c) => (
              <li key={c.name} className="flex items-center gap-2 text-[13px]">
                <span className={`h-2 w-2 rounded-full ${c.ok ? "bg-emerald-500" : "bg-red-500"}`} />
                <span className="font-medium">{c.name}</span>
                <span className="text-[var(--muted-foreground)]">{c.detail}</span>
              </li>
            ))}
          </ul>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {cards.map(([label, value, hint]) => (
              <div key={label} className="rounded-xl border border-[var(--border)] p-4">
                <p className="text-[12px] text-[var(--muted-foreground)]">{label}</p>
                <p className="mt-1 text-xl font-semibold">{value}</p>
                {hint && <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">{hint}</p>}
              </div>
            ))}
          </div>
          {wrong.length > 0 && (
            <div className="mt-5">
              <h2 className="mb-2 text-sm font-semibold">{t("Recent answers marked wrong")}</h2>
              <ul className="space-y-1.5 text-[12px]">
                {wrong.slice(0, 15).map((w) => (
                  <li key={w.id} className="rounded-lg border border-[var(--border)] p-2">
                    <span className="block text-[var(--muted-foreground)]">{w.excerpt || "—"}</span>
                    {w.reason && <span className="block">{t("Reason")}: {w.reason}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="mt-4 text-[11px] text-[var(--muted-foreground)]">
            {t("Uptime")}: {Math.floor(s.uptime_seconds / 3600)}h {Math.floor((s.uptime_seconds % 3600) / 60)}m
          </p>
        </>
      )}
    </StaffPage>
  );
}
