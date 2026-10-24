"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { requestPasswordReset } from "@/lib/auth";

export default function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  return (
    <div className="mx-auto w-full max-w-sm space-y-4 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6">
      <h1 className="text-lg font-semibold">{t("Reset your password")}</h1>
      {sent ? (
        <p className="text-sm text-[var(--muted-foreground)]">
          {t("If an account uses this email, a reset link is on its way. It is valid for 1 hour.")}
        </p>
      ) : (
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            await requestPasswordReset(email.trim());
            setBusy(false);
            setSent(true);
          }}
        >
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={t("Email")}
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm"
          />
          <button disabled={busy || !email.includes("@")} className="w-full rounded-lg bg-[var(--primary)] px-3 py-2 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">
            {t("Send reset link")}
          </button>
        </form>
      )}
      <a href="/login" className="block text-center text-xs text-[var(--muted-foreground)] underline">{t("Back to sign in")}</a>
    </div>
  );
}
