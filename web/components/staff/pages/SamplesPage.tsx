"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Download, PenLine } from "lucide-react";
import { useTranslation } from "react-i18next";

import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import StaffPage from "@/components/staff/StaffPage";
import { createCoWriterDocument } from "@/lib/co-writer-api";
import {
  downloadSample,
  getDomainMarkdown,
  getRights,
  getSampleMarkdown,
  listDomains,
  type DomainSummary,
  type SampleFormat,
} from "@/lib/knowledge-tree-api";

const BTN =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";

const FORMATS: { id: SampleFormat; label: string; hint: string }[] = [
  { id: "md", label: "Markdown (.md)", hint: "Headings with #, ##, ###, ####." },
  { id: "docx", label: "Word (.docx)", hint: "Built-in styles Heading 1 to Heading 4." },
  { id: "txt", label: "Plain text (.txt)", hint: "Numbered headings: 1., 1.1, 1.1.1." },
  { id: "pdf", label: "PDF (.pdf)", hint: "Numbered headings, like plain text." },
];

/**
 * How a document becomes the main graph: the sample in every readable format,
 * and Co-Writer as the editor (start from the sample or from a domain, then
 * "Update main graph" from Co-Writer's toolbar).
 */
export default function SamplesPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const [sample, setSample] = useState<{ title: string; markdown: string } | null>(null);
  const [domains, setDomains] = useState<DomainSummary[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  const [domainId, setDomainId] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    let alive = true;
    Promise.all([getSampleMarkdown(), listDomains(), getRights()])
      .then(([s, d, r]) => {
        if (!alive) return;
        setSample(s);
        setDomains(d);
        setCanEdit(r.can_edit);
      })
      .catch((e: Error) => alive && setMsg(e.message));
    return () => {
      alive = false;
    };
  }, []);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setMsg("");
    try {
      await fn();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const openInCoWriter = (title: string, content: string, tree?: string) =>
    run(async () => {
      const doc = await createCoWriterDocument({ title, content });
      router.push(`/co-writer/${doc.id}${tree ? `?tree=${encodeURIComponent(tree)}` : ""}`);
    });

  return (
    <StaffPage
      wide
      title={t("Samples")}
      subtitle={t("How a document is read into the main graph: every heading becomes a node, the text under it becomes the node's content.")}
    >
      {msg && (
        <p role="status" className="mb-3 text-[13px]">
          {msg}
        </p>
      )}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="min-w-0 rounded-xl border border-[var(--border)] p-4">
          <h2 className="mb-2 text-sm font-semibold">{t("Sample document")}</h2>
          {sample ? <MarkdownRenderer content={sample.markdown} variant="prose" /> : null}
        </section>

        <div className="space-y-5">
          <section className="rounded-xl border border-[var(--border)] p-4">
            <h2 className="mb-1 text-sm font-semibold">{t("Download the sample")}</h2>
            <p className="mb-3 text-[12px] text-[var(--muted-foreground)]">
              {t("Same content in each format the importer reads, with headings marked the way that format needs.")}
            </p>
            <ul className="space-y-2">
              {FORMATS.map((f) => (
                <li key={f.id} className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-[13px]">{f.label}</div>
                    <div className="text-[11px] text-[var(--muted-foreground)]">{t(f.hint)}</div>
                  </div>
                  <button type="button" className={BTN} disabled={busy} onClick={() => void run(() => downloadSample(f.id))}>
                    <Download size={12} />
                    {t("Download")}
                  </button>
                </li>
              ))}
            </ul>
          </section>

          {canEdit && (
            <section className="rounded-xl border border-[var(--border)] p-4">
              <h2 className="mb-1 text-sm font-semibold">{t("Write in Co-Writer")}</h2>
              <p className="mb-3 text-[12px] text-[var(--muted-foreground)]">
                {t("Write or keep writing in Co-Writer, then use “Update main graph” in its toolbar. Headings that keep their place keep their node; every update can be reverted.")}
              </p>
              <button
                type="button"
                className={BTN}
                disabled={busy || !sample}
                onClick={() => sample && void openInCoWriter(sample.title, sample.markdown)}
              >
                <PenLine size={12} />
                {t("Start from the sample")}
              </button>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <select className={`${FIELD} min-w-0 flex-1`} value={domainId} onChange={(e) => setDomainId(e.target.value)}>
                  <option value="">{t("Choose a domain…")}</option>
                  {domains.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className={BTN}
                  disabled={busy || !domainId}
                  onClick={() =>
                    void run(async () => {
                      const doc = await getDomainMarkdown(domainId);
                      const created = await createCoWriterDocument({ title: doc.name, content: doc.markdown });
                      router.push(`/co-writer/${created.id}?tree=${encodeURIComponent(domainId)}`);
                    })
                  }
                >
                  <PenLine size={12} />
                  {t("Continue this domain")}
                </button>
              </div>
            </section>
          )}
        </div>
      </div>
    </StaffPage>
  );
}
