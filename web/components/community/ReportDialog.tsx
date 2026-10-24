"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Flag, Loader2 } from "lucide-react";

import { Modal, fieldClass, primaryButton, secondaryButton } from "@/components/community/community-ui";
import { REPORT_REASONS, reportContent, type ReportReason, type ReportTarget } from "@/lib/community-api";
import { notify } from "@/lib/notifications";

export function useReportReasonLabel() {
  const { t } = useTranslation();
  const labels: Record<string, string> = {
    spam: t("Spam or advertising"),
    harassment: t("Harassment or hate"),
    inappropriate: t("Inappropriate content"),
    misinformation: t("Misleading or wrong information"),
    copyright: t("Copyright or cheating"),
    other: t("Something else"),
  };
  return (reason: string) => labels[reason] ?? reason;
}

export default function ReportDialog({
  targetType,
  targetId,
  onClose,
}: {
  targetType: ReportTarget;
  targetId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const reasonLabel = useReportReasonLabel();
  const [reason, setReason] = useState<ReportReason>("spam");
  const [details, setDetails] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      const res = await reportContent({ target_type: targetType, target_id: targetId, reason, details });
      notify(res.message || t("Thanks — the moderators will review it."), { tone: "success" });
      onClose();
    } catch (e) {
      notify(e instanceof Error ? e.message : t("Could not send the report"), { tone: "error" });
      setBusy(false);
    }
  }

  const title =
    targetType === "post" ? t("Report post") : targetType === "comment" ? t("Report comment") : t("Report skill");

  return (
    <Modal title={title} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-[12.5px] text-[var(--muted-foreground)]">
          {t("Only moderators see reports. The author isn't told who reported.")}
        </p>
        <div className="space-y-1.5">
          {REPORT_REASONS.map((r) => (
            <label
              key={r}
              className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-[13px] ${
                reason === r
                  ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--foreground)]"
                  : "border-[var(--border)] text-[var(--foreground)] hover:bg-[var(--muted)]/50"
              }`}
            >
              <input
                type="radio"
                name="report-reason"
                checked={reason === r}
                onChange={() => setReason(r)}
                className="accent-[var(--primary)]"
              />
              {reasonLabel(r)}
            </label>
          ))}
        </div>
        <textarea
          value={details}
          onChange={(e) => setDetails(e.target.value)}
          rows={3}
          maxLength={500}
          placeholder={t("Anything the moderators should know? (optional)")}
          className={fieldClass}
        />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={secondaryButton}>
            {t("Cancel")}
          </button>
          <button type="button" disabled={busy} onClick={() => void submit()} className={primaryButton}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Flag size={14} />}
            {t("Report")}
          </button>
        </div>
      </div>
    </Modal>
  );
}
