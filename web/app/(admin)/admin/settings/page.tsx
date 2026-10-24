"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import StaffPage from "@/components/staff/StaffPage";
import { getSettings, saveSettings, sendTestEmail, type SettingItem } from "@/lib/admin-ops-api";

const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";

const GROUPS: { title: string; keys: [string, string][] }[] = [
  {
    title: "Sign-in protection",
    keys: [
      ["auth.login_max_failures", "Failed attempts before lockout"],
      ["auth.login_lock_minutes", "Lockout minutes"],
    ],
  },
  {
    title: "Email (SMTP)",
    keys: [
      ["smtp.host", "Host"],
      ["smtp.port", "Port"],
      ["smtp.username", "Username"],
      ["smtp.password", "Password"],
      ["smtp.from", "Sender address"],
      ["smtp.tls", "Encryption"],
    ],
  },
];

export default function SettingsPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<Record<string, SettingItem>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const s = await getSettings();
    setData(s);
    setDraft(Object.fromEntries(Object.entries(s).map(([k, v]) => [k, v.value])));
  }, []);
  useEffect(() => {
    load().catch((e: Error) => setMsg(e.message));
  }, [load]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg("");
    try {
      await fn();
      setMsg(ok);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <StaffPage title={t("System settings")} subtitle={t("Values saved here override the server environment.")}>
      {msg && <p role="status" className="mb-3 text-[13px]">{msg}</p>}
      {GROUPS.map((g) => (
        <section key={g.title} className="mb-5 rounded-xl border border-[var(--border)] p-4">
          <h2 className="mb-3 text-sm font-semibold">{t(g.title)}</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {g.keys.map(([key, label]) => {
              const item = data[key];
              if (!item) return null;
              return (
                <label key={key} className="text-[12px]">
                  <span className="mb-1 block text-[var(--muted-foreground)]">
                    {t(label)} {item.source === "admin" && <em>· {t("set by admin")}</em>}
                  </span>
                  {item.type === "choice" ? (
                    <select className={FIELD} value={draft[key] ?? ""} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}>
                      {item.choices?.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  ) : (
                    <input
                      className={FIELD}
                      type={key === "smtp.password" ? "password" : item.type === "int" ? "number" : "text"}
                      min={item.min}
                      max={item.max}
                      placeholder={key === "smtp.password" && item.is_set ? t("Saved — leave blank to keep") : ""}
                      value={draft[key] ?? ""}
                      onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                    />
                  )}
                </label>
              );
            })}
          </div>
        </section>
      ))}
      <button
        className={BTN}
        disabled={busy}
        onClick={() => void run(async () => { setData(await saveSettings(draft)); await load(); }, t("Saved."))}
      >
        {t("Save settings")}
      </button>
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <input className={`${FIELD} max-w-xs`} type="email" placeholder="you@example.com" value={to} onChange={(e) => setTo(e.target.value)} />
        <button className={BTN} disabled={busy || !to.includes("@")} onClick={() => void run(() => sendTestEmail(to.trim()), t("Test email sent."))}>
          {t("Send test email")}
        </button>
        <span className="text-[11px] text-[var(--muted-foreground)]">{t("Save first, then test.")}</span>
      </div>
    </StaffPage>
  );
}
