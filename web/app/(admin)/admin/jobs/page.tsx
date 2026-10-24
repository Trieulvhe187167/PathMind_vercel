"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import StaffPage from "@/components/staff/StaffPage";
import { listJobs, runJob, setJobEnabled, type JobRow } from "@/lib/admin-ops-api";

const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";

const when = (ms: number | null) => (ms ? new Date(ms).toLocaleString() : "—");
function scheduleText(s: JobRow["schedule"]) {
  if (s.kind === "every") return `every ${s.every_seconds}s`;
  if (s.kind === "cron") return `${s.expr}${s.tz ? ` (${s.tz})` : ""}`;
  return `at ${when(s.at_ms)}`;
}

export default function JobsPage() {
  const { t } = useTranslation();
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    listJobs().then(setJobs).catch((e: Error) => setMsg(e.message));
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  async function act(id: string, fn: () => Promise<unknown>) {
    setBusy(id);
    setMsg("");
    try {
      await fn();
      load();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  return (
    <StaffPage wide title={t("Background jobs")} subtitle={t("Scheduled tasks created by users and AI Partners. Enable, disable or run one now.")}>
      {msg && <p role="alert" className="mb-3 text-[13px] text-[var(--destructive)]">{msg}</p>}
      {jobs.length === 0 ? (
        <p className="text-[13px] text-[var(--muted-foreground)]">{t("No scheduled jobs.")}</p>
      ) : (
        <ul className="space-y-2">
          {jobs.map((j) => (
            <li key={j.id} className="rounded-xl border border-[var(--border)] p-3 text-[13px]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-medium">{j.name || j.id}</p>
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    {j.owner} · {scheduleText(j.schedule)}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button className={BTN} disabled={busy === j.id} onClick={() => void act(j.id, () => setJobEnabled(j.id, !j.enabled))}>
                    {j.enabled ? t("Disable") : t("Enable")}
                  </button>
                  <button className={BTN} disabled={busy === j.id} onClick={() => void act(j.id, () => runJob(j.id))}>
                    {t("Run now")}
                  </button>
                </div>
              </div>
              <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                {t("Next")}: {j.enabled ? when(j.next_run_at_ms) : t("disabled")} · {t("Last")}: {when(j.last_run_at_ms)} {j.last_status ? `(${j.last_status})` : ""}
              </p>
              {j.last_error && <p className="text-[11px] text-[var(--destructive)]">{j.last_error}</p>}
            </li>
          ))}
        </ul>
      )}
    </StaffPage>
  );
}
