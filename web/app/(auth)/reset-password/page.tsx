"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslation } from "react-i18next";

import { resetPassword } from "@/lib/auth";

function ResetForm() {
  const { t } = useTranslation();
  const token = useSearchParams().get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  return (
    <div className="mx-auto w-full max-w-sm space-y-4 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6">
      <h1 className="text-lg font-semibold">{t("Choose a new password")}</h1>
      {done ? (
        <>
          <p className="text-sm">{t("Password changed. You can sign in now.")}</p>
          <a href="/login" className="block text-center text-sm underline">{t("Sign in")}</a>
        </>
      ) : (
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setError("");
            if (password !== confirm) return setError(t("Passwords do not match."));
            setBusy(true);
            const r = await resetPassword(token, password);
            setBusy(false);
            if (r.ok) setDone(true);
            else setError(r.error);
          }}
        >
          <input type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t("New password")} className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm" />
          <input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={t("Confirm password")} className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm" />
          <p className="text-[11px] text-[var(--muted-foreground)]">{t("At least 8 characters, with letters and numbers.")}</p>
          {error && <p role="alert" className="text-xs text-[var(--destructive)]">{error}</p>}
          <button disabled={busy || !token || !password} className="w-full rounded-lg bg-[var(--primary)] px-3 py-2 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">
            {t("Change password")}
          </button>
        </form>
      )}
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetForm />
    </Suspense>
  );
}
