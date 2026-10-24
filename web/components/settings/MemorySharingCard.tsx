"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  deleteSharedData,
  getSharingPreview,
  getSharingStatus,
  setSharing,
  shareNow,
  type SharingPreview,
  type SharingStatus,
} from "@/lib/memory-sharing-api";
import { SettingRow, SettingSection } from "@/components/settings/shared";

const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";

/**
 * Opt-in sharing of anonymised memory aggregates. Off by default; shows exactly
 * what would be sent (tags and counts, never text); withdrawing erases it.
 * Open to every plan.
 */
export default function MemorySharingCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<SharingStatus | null>(null);
  const [preview, setPreview] = useState<SharingPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    const [s, p] = await Promise.all([getSharingStatus(), getSharingPreview()]);
    setStatus(s);
    setPreview(p);
    return s;
  }, []);

  useEffect(() => {
    let cancelled = false;
    void refresh()
      .then(async (s) => {
        // Keep the shared snapshot reasonably fresh without a background job.
        if (!cancelled && s.enabled && s.stale) {
          await shareNow();
          if (!cancelled) await refresh();
        }
      })
      .catch(() => {
        if (!cancelled) setMessage(t("Failed to load settings."));
      });
    return () => {
      cancelled = true;
    };
  }, [refresh, t]);

  async function run(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    setMessage("");
    try {
      await fn();
      await refresh();
      setMessage(done);
    } catch {
      setMessage(t("Action failed"));
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  const items = preview?.items ?? [];

  return (
    <SettingSection
      title={t("Share anonymous insights")}
      description={t(
        "Optional. Share topic tags, learning-style tags and activity counts so we can recommend better study material. Never your notes or memory text; not linked to your name.",
      )}
    >
      <SettingRow
        title={t("Share anonymous insights")}
        description={
          status.enabled
            ? status.last_shared_at
              ? t("Last shared: {{when}}", {
                  when: new Date(status.last_shared_at).toLocaleString(),
                })
              : t("Sharing is on. Nothing has been sent yet.")
            : t("Sharing is off. Nothing leaves your account.")
        }
        control={
          <button
            type="button"
            role="switch"
            aria-checked={status.enabled}
            disabled={busy}
            onClick={() =>
              void run(
                async () => {
                  if (status.enabled) await setSharing(false);
                  else {
                    await setSharing(true);
                    await shareNow();
                  }
                },
                status.enabled
                  ? t("Sharing turned off and shared data deleted.")
                  : t("Sharing turned on."),
              )
            }
            className={
              "relative inline-flex h-5 w-9 items-center rounded-full transition " +
              (status.enabled ? "bg-[var(--primary)]" : "bg-[var(--muted)]")
            }
          >
            <span
              className={
                "inline-block h-4 w-4 transform rounded-full bg-white transition " +
                (status.enabled ? "translate-x-4" : "translate-x-0.5")
              }
            />
          </button>
        }
      />
      <SettingRow
        title={t("What would be shared")}
        description={
          items.length
            ? items
                .map((i) =>
                  i.kind === "activity" ? `${i.label} ×${i.count}` : i.label,
                )
                .join(", ")
            : t("Nothing yet — add to your memory or use the tutor first.")
        }
        control={
          status.enabled ? (
            <div className="flex gap-2">
              <button
                type="button"
                className={BTN}
                disabled={busy}
                onClick={() => void run(shareNow, t("Shared."))}
              >
                {t("Share now")}
              </button>
              <button
                type="button"
                className={BTN}
                disabled={busy}
                onClick={() =>
                  void run(deleteSharedData, t("Shared data deleted."))
                }
              >
                {t("Delete shared data")}
              </button>
            </div>
          ) : (
            <span />
          )
        }
      />
      {busy && <Loader2 className="h-4 w-4 animate-spin" />}
      {message && (
        <p role="status" className="mt-2 text-[12px] text-[var(--muted-foreground)]">
          {message}
        </p>
      )}
    </SettingSection>
  );
}
