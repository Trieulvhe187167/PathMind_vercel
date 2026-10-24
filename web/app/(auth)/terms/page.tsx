"use client";

import Link from "next/link";
import { useTranslation } from "react-i18next";
import { TermsSections } from "@/components/auth/TermsSections";

export default function TermsPage() {
  const { t } = useTranslation();
  return (
    <main className="w-full max-w-2xl px-5 py-12">
      <h1 className="font-serif text-3xl font-medium tracking-[-0.015em]">{t("Terms of Use")}</h1>
      <p className="mt-2 text-sm text-[var(--muted-foreground)]">
        {t("Please read these terms before creating a PathMind account.")}
      </p>
      <div className="mt-8">
        <TermsSections />
      </div>
      <Link
        href="/register"
        className="mt-10 inline-block text-sm font-medium text-[var(--primary)] hover:underline"
      >
        ← {t("Back to sign up")}
      </Link>
    </main>
  );
}
