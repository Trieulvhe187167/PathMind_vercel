"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { KeyRound, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import ModelPriceTable from "@/components/admin/ModelPriceTable";
import StaffPage from "@/components/staff/StaffPage";
import { listModelPrices, type ModelPriceList } from "@/lib/admin-api";

const LINK =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)]";

/**
 * AI models, kept apart from the plans: the API price of each model (which
 * sets how many credits a call costs) and where providers and keys are set up.
 * Which models a plan may use is chosen on the Plans page.
 */
export default function AdminModelsPage() {
  const { t } = useTranslation();
  const [prices, setPrices] = useState<ModelPriceList | null>(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => listModelPrices().then(setPrices), []);
  useEffect(() => {
    let alive = true;
    listModelPrices()
      .then((p) => alive && setPrices(p))
      .catch((e: Error) => alive && setMsg(e.message));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <StaffPage
      wide
      title={t("Models")}
      subtitle={t("API price of every model (it decides how many credits a call costs) and the providers that serve them. Which models each plan may use is set on the Plans page.")}
    >
      <div className="mb-5 flex flex-wrap gap-2">
        <Link href="/admin/config/connections" className={LINK}>
          <KeyRound size={13} />
          {t("Providers & API keys")}
        </Link>
        <Link href="/admin/config/llm" className={LINK}>
          {t("Default chat model")}
        </Link>
        <Link href="/admin/plans" className={LINK}>
          {t("Models allowed per plan")}
        </Link>
      </div>
      {msg && (
        <p role="status" className="mb-3 text-[13px]">
          {msg}
        </p>
      )}
      {!prices ? (
        <Loader2 className="h-4 w-4 animate-spin text-[var(--muted-foreground)]" />
      ) : (
        <ModelPriceTable
          key={JSON.stringify(prices.prices)}
          data={prices}
          onChanged={() => {
            setMsg(t("Model price list updated."));
            void load().catch((e: Error) => setMsg(e.message));
          }}
          onError={setMsg}
        />
      )}
    </StaffPage>
  );
}
