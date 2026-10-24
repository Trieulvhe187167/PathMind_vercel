"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { inviteRnd, listInvitations, revokeInvitation, type InvitationRow } from "@/lib/admin-api";

/** Administrators invite R&D members by email; a signed link creates the account. */
export default function InviteRndCard() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<InvitationRow[]>([]);

  const load = useCallback(() => {
    listInvitations().then(setRows).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  async function revoke(id: string) {
    try {
      await revokeInvitation(id);
      load();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  async function send() {
    setBusy(true);
    setMsg("");
    setLink("");
    try {
      const r = await inviteRnd(email.trim());
      setMsg(r.delivery === "email" ? t("Invitation sent.") : t("Email is not set up. Copy this link and send it yourself:"));
      if (r.link) setLink(r.link);
      setEmail("");
      load();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-4 rounded-xl border border-[var(--border)] p-4">
      <h2 className="text-sm font-semibold">{t("Invite R&D by email")}</h2>
      <p className="mt-1 text-[12px] text-[var(--muted-foreground)]">
        {t("The link is valid for 7 days. The person sets their own password.")}
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@example.com"
          className="min-w-64 flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]"
        />
        <button
          className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50"
          disabled={busy || !email.includes("@")}
          onClick={() => void send()}
        >
          {t("Send invitation")}
        </button>
      </div>
      {msg && <p role="status" className="mt-2 text-[12px]">{msg}</p>}
      {rows.length > 0 && (
        <ul className="mt-3 space-y-1 text-[12px]">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-2">
              <span className="truncate">{r.email}</span>
              <span className="flex shrink-0 items-center gap-2 text-[var(--muted-foreground)]">
                {t(r.status === "pending" ? "Waiting" : r.status === "accepted" ? "Accepted" : "Revoked")}
                {r.status === "pending" && (
                  <button className="underline" onClick={() => void revoke(r.id)}>{t("Revoke")}</button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {link && <input readOnly value={link} onFocus={(e) => e.currentTarget.select()} className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--muted)] px-2 py-1 font-mono text-[11px]" />}
    </div>
  );
}
