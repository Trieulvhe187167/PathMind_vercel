"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import StaffPage from "@/components/staff/StaffPage";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  getRewardRules,
  grantPoints,
  listRewardLedger,
  saveRewardRules,
  type LedgerEntry,
} from "@/lib/staff-api";

const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";

const LABELS: Record<string, string> = {
  "earn.recommendation_merged": "Points per merged recommendation",
  "earn.per_node_merged": "Extra points per merged node",
  "earn.node_mastered": "Points per mastered node (learners)",
  "cap.daily_activity_points": "Most activity points per user per day",
  "rate.points_per_plan_day": "Points needed for one plan day",
  "rate.credits_per_point": "AI credits per point",
  "bonus.credit_days": "Days bought credits stay valid",
  "min.redeem": "Minimum points per redemption",
};

export default function RewardsPage() {
  const { t } = useTranslation();
  // Administrators set the rules and grant points; R&D reads the ledger.
  const auth = useAuthStatus();
  const canEdit = !auth.enabled || auth.isAdmin;
  const [rules, setRules] = useState<Record<string, number>>({});
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [userId, setUserId] = useState("");
  const [points, setPoints] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(
    () =>
      Promise.all([getRewardRules(), listRewardLedger()]).then(([r, l]) => {
        setRules(r);
        setLedger(l);
      }),
    [],
  );
  useEffect(() => {
    let alive = true;
    Promise.all([getRewardRules(), listRewardLedger()])
      .then(([r, l]) => {
        if (!alive) return;
        setRules(r);
        setLedger(l);
      })
      .catch((e: Error) => alive && setMsg(e.message));
    return () => {
      alive = false;
    };
  }, []);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMsg("");
    try {
      await fn();
      await load();
      setMsg(ok);
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  return (
    <StaffPage
      title={t("Rewards")}
      subtitle={t("Points are earned through learning and merged recommendations, and redeemed for plan days or AI credits, never cash.")}
    >
      {msg && <p role="status" className="mb-3 text-[13px]">{msg}</p>}
      <section className="rounded-xl border border-[var(--border)] p-4">
        <h2 className="mb-3 text-sm font-semibold">{t("Rules")}</h2>
        <div className="grid gap-2 sm:grid-cols-2">
          {Object.keys(LABELS).map((k) => (
            <label key={k} className="flex items-center justify-between gap-3 text-[13px]">
              <span>{t(LABELS[k])}</span>
              <input
                type="number"
                min={0}
                disabled={!canEdit}
                className={`${FIELD} w-28`}
                value={rules[k] ?? 0}
                onChange={(e) => setRules({ ...rules, [k]: Number(e.target.value) })}
              />
            </label>
          ))}
        </div>
        {canEdit ? (
          <button className={`${BTN} mt-3`} onClick={() => void act(() => saveRewardRules(rules), t("Saved."))}>
            {t("Save rules")}
          </button>
        ) : (
          <p className="mt-3 text-[12px] text-[var(--muted-foreground)]">{t("Only administrators change the rules.")}</p>
        )}
      </section>

      {canEdit && (
      <section className="mt-5 rounded-xl border border-[var(--border)] p-4">
        <h2 className="mb-3 text-sm font-semibold">{t("Grant or take back points")}</h2>
        <div className="flex flex-wrap gap-2">
          <input className={`${FIELD} w-56`} placeholder={t("User id")} value={userId} onChange={(e) => setUserId(e.target.value)} />
          <input className={`${FIELD} w-28`} type="number" placeholder={t("Points (− to remove)")} value={points} onChange={(e) => setPoints(e.target.value)} />
          <input className={`${FIELD} min-w-48 flex-1`} placeholder={t("Reason")} value={note} onChange={(e) => setNote(e.target.value)} />
          <button
            className={BTN}
            disabled={!userId.trim() || !Number(points)}
            onClick={() => void act(() => grantPoints(userId.trim(), Number(points), note), t("Done."))}
          >
            {t("Apply")}
          </button>
        </div>
      </section>
      )}

      <section className="mt-5">
        <h2 className="mb-2 text-sm font-semibold">{t("Recent movements")}</h2>
        <ul className="space-y-1 text-[12px]">
          {ledger.length === 0 && <li className="text-[var(--muted-foreground)]">{t("Nothing yet.")}</li>}
          {ledger.map((l) => (
            <li key={l.id} className="flex flex-wrap gap-x-3 border-b border-[var(--border)]/50 py-1">
              <span className="w-36 text-[var(--muted-foreground)]">{l.created_at?.replace("T", " ").slice(0, 16)}</span>
              <span className="w-40 truncate font-mono">{l.user_id}</span>
              <span className={l.points >= 0 ? "text-emerald-600" : "text-red-500"}>{l.points > 0 ? "+" : ""}{l.points}</span>
              <span>{l.reason}</span>
              <span className="text-[var(--muted-foreground)]">{l.note}</span>
            </li>
          ))}
        </ul>
      </section>
    </StaffPage>
  );
}
