"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { fetchAuthStatus } from "@/lib/auth";
import { getInsightStats, type InsightStats } from "@/lib/memory-sharing-api";

const KIND_LABEL = {
  topic: "Topics",
  preference: "Learning styles",
  activity: "Activity",
} as const;

export default function InsightsPage() {
  const router = useRouter();
  const { t } = useTranslation();
  const [ready, setReady] = useState(false);
  const [stats, setStats] = useState<InsightStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void fetchAuthStatus().then((auth) => {
      if (!auth?.authenticated)
        return router.replace("/login?next=/admin/insights");
      if (!auth.is_admin) return router.replace("/");
      setReady(true);
    });
  }, [router]);

  useEffect(() => {
    if (!ready) return;
    getInsightStats()
      .then(setStats)
      .catch(() => setError(t("Failed to load")));
  }, [ready, t]);

  return (
    <div className="h-full overflow-y-auto bg-[var(--background)] px-4 py-8 text-[var(--foreground)] sm:px-6 lg:px-8">
      <div className="mx-auto max-w-4xl">
        <h1 className="font-serif text-xl font-semibold">
          {t("Learner insights")}
        </h1>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          {t(
            "Anonymous, opt-in aggregates. Only groups of at least {{n}} people are shown.",
            { n: stats?.min_group ?? 5 },
          )}
        </p>
        {error && (
          <p role="alert" className="mt-4 text-sm text-red-600">
            {error}
          </p>
        )}
        {stats && (
          <>
            <p className="mt-4 text-sm">
              {t("Month")}: {stats.period}
              {stats.consenting_accounts !== null &&
                ` · ${t("Accounts sharing")}: ${stats.consenting_accounts}`}
            </p>
            {stats.groups.length === 0 ? (
              <p className="mt-6 text-sm text-[var(--muted-foreground)]">
                {t("Not enough shared data yet.")}
              </p>
            ) : (
              (["topic", "preference", "activity"] as const).map((kind) => {
                const rows = stats.groups.filter((g) => g.kind === kind);
                if (!rows.length) return null;
                return (
                  <section key={kind} className="mt-6">
                    <h2 className="mb-2 text-sm font-semibold">
                      {t(KIND_LABEL[kind])}
                    </h2>
                    <table className="w-full text-left text-sm">
                      <thead className="text-xs text-[var(--muted-foreground)]">
                        <tr>
                          <th className="py-1">{t("Label")}</th>
                          <th className="py-1">{t("People")}</th>
                          <th className="py-1">{t("Total")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((g) => (
                          <tr
                            key={g.label}
                            className="border-t border-[var(--border)]"
                          >
                            <td className="py-1.5">{g.label}</td>
                            <td className="py-1.5">{g.people}</td>
                            <td className="py-1.5">{g.total}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </section>
                );
              })
            )}
          </>
        )}
      </div>
    </div>
  );
}
