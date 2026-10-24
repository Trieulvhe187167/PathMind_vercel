"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { registerConfirmHost, type ConfirmRequest } from "@/lib/confirm";

/** Renders requests from ``confirmAction()`` one at a time. Mount once. */
export default function ConfirmHost() {
  const { t } = useTranslation();
  const [queue, setQueue] = useState<ConfirmRequest[]>([]);

  useEffect(
    () => registerConfirmHost((request) => setQueue((prev) => [...prev, request])),
    [],
  );

  const current = queue[0] ?? null;
  const settle = useCallback(
    (ok: boolean) => {
      if (!current) return;
      current.resolve(ok);
      setQueue((prev) => prev.slice(1));
    },
    [current],
  );

  if (!current) return null;
  const destructive =
    current.tone === "danger" ||
    /\b(delete|remove|revoke|archive|discard|clear)\b/i.test(current.message);
  return (
    <ConfirmDialog
      open
      title={current.title ?? current.message}
      tone={destructive ? "danger" : "default"}
      confirmLabel={
        current.confirmLabel ??
        (/\b(delete|remove)\b/i.test(current.message) ? t("Delete") : t("Confirm"))
      }
      cancelLabel={current.cancelLabel}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    >
      {current.title ? current.message : null}
    </ConfirmDialog>
  );
}
