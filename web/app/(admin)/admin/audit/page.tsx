"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import StaffPage from "@/components/staff/StaffPage";
import { listAuditLog, type AuditRow } from "@/lib/admin-ops-api";

const FIELD = "rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";
const GROUPS = ["", "store.", "recommendation.", "rewards.", "auth.", "settings.", "job.", "branch."];

export default function AuditPage() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [prefix, setPrefix] = useState("");
  const [actor, setActor] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    listAuditLog(prefix, actor.trim()).then(setRows).catch((e: Error) => setErr(e.message));
  }, [prefix, actor]);
  useEffect(load, [load]);

  return (
    <StaffPage wide title={t("Audit log")} subtitle={t("Sensitive actions across the whole system. Entries cannot be edited.")}>
      <div className="mb-3 flex flex-wrap gap-2">
        <select className={FIELD} value={prefix} onChange={(e) => setPrefix(e.target.value)}>
          {GROUPS.map((g) => (
            <option key={g} value={g}>{g ? g.slice(0, -1) : t("All actions")}</option>
          ))}
        </select>
        <input className={`${FIELD} w-64`} placeholder={t("Actor id")} value={actor} onChange={(e) => setActor(e.target.value)} />
        <button className={`${FIELD} hover:bg-[var(--muted)]`} onClick={load}>{t("Refresh")}</button>
      </div>
      {err && <p role="alert" className="text-[13px] text-[var(--destructive)]">{err}</p>}
      <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
        <table className="w-full text-left text-[12px]">
          <thead className="bg-[var(--muted)]/50 text-[var(--muted-foreground)]">
            <tr>
              <th className="px-3 py-2">{t("Time (UTC)")}</th>
              <th className="px-3 py-2">{t("Actor")}</th>
              <th className="px-3 py-2">{t("Action")}</th>
              <th className="px-3 py-2">{t("Target")}</th>
              <th className="px-3 py-2">{t("Details")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td className="px-3 py-3 text-[var(--muted-foreground)]" colSpan={5}>{t("Nothing yet.")}</td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-[var(--border)]/60 align-top">
                <td className="whitespace-nowrap px-3 py-1.5">{r.created_at?.replace("T", " ").slice(0, 19)}</td>
                <td className="px-3 py-1.5 font-mono">{r.actor_id.slice(0, 12)}</td>
                <td className="px-3 py-1.5 font-medium">{r.action}</td>
                <td className="px-3 py-1.5">{r.target_type} {r.target_id.slice(0, 12)}</td>
                <td className="max-w-md truncate px-3 py-1.5 text-[var(--muted-foreground)]" title={JSON.stringify(r.data)}>{JSON.stringify(r.data)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </StaffPage>
  );
}
