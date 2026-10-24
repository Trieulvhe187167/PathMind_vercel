"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { listDomains, syncTreeFromDocument, type DomainSummary, type SyncResult } from "@/lib/knowledge-tree-api";

const BTN =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";
const NEW = "__new__";

/**
 * R&D: update a main-tree domain from the Co-Writer document (or create one).
 * The server keeps the node of every heading that stays in place, adds new
 * headings and removes missing ones; the update is revertable from the history.
 */
export default function UpdateTreeModal({
  title,
  markdown,
  initialDomainId,
  onClose,
}: {
  title: string;
  markdown: string;
  initialDomainId?: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [domains, setDomains] = useState<DomainSummary[] | null>(null);
  const [target, setTarget] = useState(initialDomainId || "");
  const [name, setName] = useState(title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SyncResult | null>(null);

  useEffect(() => {
    let alive = true;
    listDomains()
      .then((d) => {
        if (!alive) return;
        setDomains(d);
        setTarget((cur) => (cur && d.some((x) => x.id === cur) ? cur : d.length ? "" : NEW));
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  const domainName = domains?.find((d) => d.id === target)?.name ?? "";

  async function submit() {
    setBusy(true);
    setError("");
    try {
      setResult(
        await syncTreeFromDocument({
          domain_id: target === NEW ? null : target,
          name: target === NEW ? name.trim() : undefined,
          title: title || name,
          markdown,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="w-full max-w-md rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 text-[var(--foreground)] shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">{t("Update main graph")}</h2>
          <button type="button" aria-label={t("Close")} onClick={onClose} className="text-[var(--muted-foreground)]">
            <X size={16} />
          </button>
        </div>
        {result ? (
          <div className="space-y-3 text-[13px]">
            <p>
              {t("“{{name}}” updated: {{created}} added, {{updated}} changed, {{removed}} removed.", {
                name: result.name,
                created: String(result.created),
                updated: String(result.updated),
                removed: String(result.removed),
              })}
            </p>
            <p className="text-[12px] text-[var(--muted-foreground)]">
              {t("You can revert this update from the edit history.")}
            </p>
            <div className="flex justify-end gap-2">
              <Link href="/rnd/knowledge" className={BTN}>
                {t("Open in R&D console")}
              </Link>
              <button type="button" className={BTN} onClick={onClose}>
                {t("Done")}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3 text-[13px]">
            <p className="text-[12px] text-[var(--muted-foreground)]">
              {t("Headings become nodes. A heading that stays in place keeps its node; new headings are added and missing ones are removed.")}
            </p>
            {domains === null ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <select className={FIELD} value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="" disabled>
                  {t("Choose a domain…")}
                </option>
                {domains.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
                <option value={NEW}>{t("New domain…")}</option>
              </select>
            )}
            {target === NEW && (
              <input className={FIELD} value={name} maxLength={120} placeholder={t("Domain name")} onChange={(e) => setName(e.target.value)} />
            )}
            {target && target !== NEW && (
              <p className="text-[12px] text-[var(--warning,var(--muted-foreground))]">
                {t("The graph of “{{name}}” will match this document; nodes whose headings are not in it are removed.", { name: domainName })}
              </p>
            )}
            {error && (
              <p role="alert" className="text-[12px] text-[var(--destructive)]">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <button type="button" className={BTN} onClick={onClose}>
                {t("Cancel")}
              </button>
              <button
                type="button"
                className={`${BTN} border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 hover:bg-[var(--primary)]`}
                disabled={busy || !markdown.trim() || !target || (target === NEW && !name.trim())}
                onClick={() => void submit()}
              >
                {busy && <Loader2 size={12} className="animate-spin" />}
                {t("Update graph")}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
