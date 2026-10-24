"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import StaffPage from "@/components/staff/StaffPage";
import { confirmAction } from "@/lib/confirm";
import {
  addStorage,
  defaultStorage,
  deleteStorage,
  editStorage,
  listStorage,
  testStorage,
  type StorageList,
  type StorageRow,
} from "@/lib/admin-ops-api";

const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12px] transition hover:bg-[var(--muted)] disabled:opacity-50";
const FIELD = "w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";

interface Draft {
  id: string | null;
  name: string;
  kind: "local" | "s3";
  enabled: boolean;
  path: string;
  endpoint: string;
  bucket: string;
  access_key: string;
  secret_key: string;
  region: string;
  prefix: string;
  path_style: boolean;
  secretSet: boolean;
}
const EMPTY: Draft = {
  id: null, name: "", kind: "local", enabled: true, path: "", endpoint: "", bucket: "",
  access_key: "", secret_key: "", region: "us-east-1", prefix: "", path_style: true, secretSet: false,
};

function toDraft(r: StorageRow): Draft {
  const c = r.config;
  return {
    ...EMPTY,
    id: r.id, name: r.name, kind: r.kind, enabled: r.enabled,
    path: String(c.path ?? ""), endpoint: String(c.endpoint ?? ""), bucket: String(c.bucket ?? ""),
    access_key: String(c.access_key ?? ""), region: String(c.region ?? "us-east-1"),
    prefix: String(c.prefix ?? ""), path_style: c.path_style !== false, secretSet: Boolean(c.secret_key_set),
  };
}

function payload(d: Draft) {
  const config =
    d.kind === "local"
      ? { path: d.path }
      : { endpoint: d.endpoint, bucket: d.bucket, access_key: d.access_key, secret_key: d.secret_key,
          region: d.region, prefix: d.prefix, path_style: d.path_style };
  return { name: d.name, kind: d.kind, config, enabled: d.enabled };
}

export default function StoragePage() {
  const { t } = useTranslation();
  const [data, setData] = useState<StorageList | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    listStorage().then(setData).catch((e: Error) => setMsg(e.message));
  }, []);
  useEffect(load, [load]);

  async function act(fn: () => Promise<unknown>, ok = "") {
    setBusy(true);
    setMsg("");
    try {
      await fn();
      if (ok) setMsg(ok);
      load();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));

  return (
    <StaffPage
      title={t("Storage")}
      subtitle={t("Where uploaded documents are kept. New files go to the default; existing files stay where they were written.")}
    >
      {msg && <p role="status" className="mb-3 text-[13px]">{msg}</p>}
      {data?.implicit.in_use && (
        <p className="mb-3 rounded-lg border border-[var(--border)] p-3 text-[12px] text-[var(--muted-foreground)]">
          {t("No default is set: files go to the built-in folder")} <span className="font-mono">{data.implicit.path}</span> ({data.implicit.files} {t("files")}).
        </p>
      )}
      <ul className="space-y-2">
        {data?.backends.map((b) => (
          <li key={b.id} className="rounded-xl border border-[var(--border)] p-3 text-[13px]">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="font-medium">
                  {b.name} <span className="text-[11px] uppercase text-[var(--muted-foreground)]">{b.kind === "s3" ? "S3 / MinIO" : t("Local folder")}</span>
                  {b.is_default && <span className="ml-2 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-600">{t("Default")}</span>}
                  {!b.enabled && <span className="ml-2 text-[11px] text-[var(--muted-foreground)]">{t("disabled")}</span>}
                </p>
                <p className="truncate font-mono text-[11px] text-[var(--muted-foreground)]">
                  {b.kind === "local" ? String(b.config.path) : `${b.config.endpoint}/${b.config.bucket}${b.config.prefix ? `/${b.config.prefix}` : ""}`}
                </p>
                <p className="text-[11px] text-[var(--muted-foreground)]">
                  {b.files} {t("files")} ·{" "}
                  {b.last_test_ok === null ? t("not tested") : b.last_test_ok ? `✓ ${b.last_test_message}` : `✗ ${b.last_test_message}`}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button className={BTN} disabled={busy} onClick={() => void act(() => testStorage(b.id))}>{t("Test")}</button>
                {!b.is_default && b.enabled && (
                  <button className={BTN} disabled={busy} onClick={() => void act(() => defaultStorage(b.id), t("Default changed."))}>{t("Make default")}</button>
                )}
                <button className={BTN} disabled={busy} onClick={() => setDraft(toDraft(b))}>{t("Edit")}</button>
                {!b.is_default && (
                  <button
                    className={BTN}
                    disabled={busy}
                    onClick={async () => {
                      if (!(await confirmAction(`${t("Remove")}: ${b.name}?`, { tone: "danger" }))) return;
                      void act(() => deleteStorage(b.id), t("Removed."));
                    }}
                  >
                    {t("Remove")}
                  </button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
      {!draft && <button className={`${BTN} mt-3`} onClick={() => setDraft({ ...EMPTY })}>{t("Add storage")}</button>}

      {draft && (
        <section className="mt-4 space-y-3 rounded-xl border border-[var(--border)] p-4">
          <h2 className="text-sm font-semibold">{draft.id ? t("Edit storage") : t("Add storage")}</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-[12px]">
              <span className="mb-1 block text-[var(--muted-foreground)]">{t("Name")}</span>
              <input className={FIELD} value={draft.name} onChange={(e) => set({ name: e.target.value })} />
            </label>
            <label className="text-[12px]">
              <span className="mb-1 block text-[var(--muted-foreground)]">{t("Type")}</span>
              <select className={FIELD} value={draft.kind} disabled={!!draft.id} onChange={(e) => set({ kind: e.target.value as "local" | "s3" })}>
                <option value="local">{t("Local folder on the server disk")}</option>
                <option value="s3">S3 / MinIO</option>
              </select>
            </label>
            {draft.kind === "local" ? (
              <label className="text-[12px] sm:col-span-2">
                <span className="mb-1 block text-[var(--muted-foreground)]">{t("Absolute folder path")}</span>
                <input className={`${FIELD} font-mono`} placeholder="/var/lib/pathmind/objects" value={draft.path} onChange={(e) => set({ path: e.target.value })} />
              </label>
            ) : (
              <>
                <label className="text-[12px] sm:col-span-2">
                  <span className="mb-1 block text-[var(--muted-foreground)]">{t("Endpoint")}</span>
                  <input className={`${FIELD} font-mono`} placeholder="https://s3.amazonaws.com or http://minio:9000" value={draft.endpoint} onChange={(e) => set({ endpoint: e.target.value })} />
                </label>
                <label className="text-[12px]">
                  <span className="mb-1 block text-[var(--muted-foreground)]">{t("Bucket")}</span>
                  <input className={FIELD} value={draft.bucket} onChange={(e) => set({ bucket: e.target.value })} />
                </label>
                <label className="text-[12px]">
                  <span className="mb-1 block text-[var(--muted-foreground)]">{t("Region")}</span>
                  <input className={FIELD} value={draft.region} onChange={(e) => set({ region: e.target.value })} />
                </label>
                <label className="text-[12px]">
                  <span className="mb-1 block text-[var(--muted-foreground)]">{t("Access key")}</span>
                  <input className={FIELD} autoComplete="off" value={draft.access_key} onChange={(e) => set({ access_key: e.target.value })} />
                </label>
                <label className="text-[12px]">
                  <span className="mb-1 block text-[var(--muted-foreground)]">{t("Secret key")}</span>
                  <input className={FIELD} type="password" autoComplete="new-password" placeholder={draft.secretSet ? t("Saved — leave blank to keep") : ""} value={draft.secret_key} onChange={(e) => set({ secret_key: e.target.value })} />
                </label>
                <label className="text-[12px]">
                  <span className="mb-1 block text-[var(--muted-foreground)]">{t("Folder prefix (optional)")}</span>
                  <input className={FIELD} value={draft.prefix} onChange={(e) => set({ prefix: e.target.value })} />
                </label>
                <label className="flex items-center gap-2 pt-5 text-[12px]">
                  <input type="checkbox" checked={draft.path_style} onChange={(e) => set({ path_style: e.target.checked })} />
                  {t("Path-style URLs (needed for MinIO)")}
                </label>
              </>
            )}
          </div>
          <label className="flex items-center gap-2 text-[12px]">
            <input type="checkbox" checked={draft.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
            {t("Enabled")}
          </label>
          <div className="flex gap-2">
            <button
              className={BTN}
              disabled={busy || !draft.name.trim()}
              onClick={() =>
                void act(async () => {
                  const row = draft.id ? await editStorage(draft.id, payload(draft)) : await addStorage(payload(draft));
                  setDraft(null);
                  setMsg(`${t("Saved.")} ${t("Run a test before making it the default.")}`);
                  return row;
                })
              }
            >
              {t("Save")}
            </button>
            <button className={BTN} disabled={busy} onClick={() => setDraft(null)}>{t("Cancel")}</button>
          </div>
        </section>
      )}
    </StaffPage>
  );
}
