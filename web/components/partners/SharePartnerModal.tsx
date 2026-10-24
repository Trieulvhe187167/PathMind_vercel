"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { Check, Copy, MessageSquare, Radio, Share2, X } from "lucide-react";
import {
  createStudyRoom,
  fetchFriendsList,
  fetchPartnerShares,
  revokePartnerShare,
  sharePartnerWithFriend,
  type FriendItem,
  type PartnerShareRecipient,
} from "@/lib/friends-api";

/**
 * Share a partner you own with friends (they can chat with it, not edit it),
 * or open a study room bound to it.
 */
export default function SharePartnerModal({
  partnerId,
  partnerName,
  onClose,
}: {
  partnerId: string;
  partnerName: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [friends, setFriends] = useState<FriendItem[]>([]);
  const [shares, setShares] = useState<PartnerShareRecipient[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [roomName, setRoomName] = useState(
    t("Study room: {{name}}", { name: partnerName }),
  );
  const [roomCode, setRoomCode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const [f, s] = await Promise.all([
        fetchFriendsList(),
        fetchPartnerShares(partnerId),
      ]);
      setFriends(f);
      setShares(s);
      const sharedIds = new Set(s.map((x) => x.user_id));
      setSelected(
        (prev) =>
          prev || f.find((x) => !sharedIds.has(x.user_id))?.user_id || "",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not load data"));
    } finally {
      setLoading(false);
    }
  }, [partnerId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setError("");
    try {
      await fn();
      setNotice(ok);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Action failed"));
    } finally {
      setBusy(false);
    }
  }

  const sharedIds = new Set(shares.map((s) => s.user_id));
  const available = friends.filter((f) => !sharedIds.has(f.user_id));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="surface-raised max-h-[90vh] w-full max-w-md overflow-y-auto rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-serif text-lg font-semibold text-[var(--foreground)]">
            {t("Share {{name}}", { name: partnerName })}
          </h2>
          <button
            onClick={onClose}
            aria-label={t("Close")}
            className="text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          >
            <X size={18} />
          </button>
        </div>

        {(error || notice) && (
          <p
            className={`mb-3 text-sm ${error ? "text-destructive" : "text-success"}`}
          >
            {error || notice}
          </p>
        )}

        {/* Share with a friend */}
        <section className="mb-6">
          <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
            <Share2 size={15} className="text-primary" /> {t("Share with friends")}
          </h3>
          <p className="mb-3 text-xs text-[var(--muted-foreground)]">
            {t(
              "Friends can chat with this Partner (each in their own conversation) but can't change its settings.",
            )}
          </p>
          {loading ? (
            <p className="text-sm text-[var(--muted-foreground)]">
              {t("Loading...")}
            </p>
          ) : friends.length === 0 ? (
            <p className="text-sm text-[var(--muted-foreground)]">
              {t("You have no friends yet. Add friends on the Partners page, Friends & Study Rooms tab.")}
            </p>
          ) : (
            <div className="flex gap-2">
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)]"
              >
                {available.length === 0 && (
                  <option value="">{t("Already shared with all your friends")}</option>
                )}
                {available.map((f) => (
                  <option key={f.user_id} value={f.user_id}>
                    {f.username}
                  </option>
                ))}
              </select>
              <button
                disabled={busy || !selected}
                onClick={() =>
                  void act(async () => {
                    await sharePartnerWithFriend(partnerId, selected);
                    setSelected("");
                  }, t("Shared."))
                }
                className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
              >
                {t("Share")}
              </button>
            </div>
          )}
          {shares.length > 0 && (
            <div className="mt-3 space-y-1">
              {shares.map((s) => (
                <div
                  key={s.share_id}
                  className="flex items-center justify-between rounded-lg bg-muted/50 px-3 py-1.5 text-sm"
                >
                  <span className="text-[var(--foreground)]">{s.username}</span>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(
                        () => revokePartnerShare(s.share_id),
                        t("Share removed."),
                      )
                    }
                    className="text-xs text-[var(--muted-foreground)] hover:text-destructive"
                  >
                    {t("Remove")}
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Study room */}
        <section>
          <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
            <Radio size={15} className="text-primary" /> {t("Open a study room")}
          </h3>
          <p className="mb-3 text-xs text-[var(--muted-foreground)]">
            {t(
              "Anyone with the room code can join, chat together and ask this Partner while they are in the room.",
            )}
          </p>
          {roomCode ? (
            <div className="space-y-2">
            <div className="flex items-center justify-between rounded-xl border border-primary/30 bg-primary/10 px-4 py-3">
              <span className="font-mono text-xl font-semibold tracking-widest text-primary">
                {roomCode}
              </span>
              <button
                onClick={() => {
                  void navigator.clipboard?.writeText(roomCode);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                }}
                className="inline-flex items-center gap-1 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              >
                {copied ? <Check size={13} /> : <Copy size={13} />} {t("Copy")}
              </button>
            </div>
            <Link
              href={`/partners/rooms/${encodeURIComponent(roomCode)}`}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              <MessageSquare size={14} /> {t("Open room chat")}
            </Link>
            </div>
          ) : (
            <div className="flex gap-2">
              <input
                value={roomName}
                onChange={(e) => setRoomName(e.target.value)}
                maxLength={120}
                className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)]"
              />
              <button
                disabled={busy || !roomName.trim()}
                onClick={() =>
                  void act(async () => {
                    const room = await createStudyRoom(
                      roomName.trim(),
                      partnerId,
                    );
                    setRoomCode(room.room_code);
                  }, t("Room created."))
                }
                className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
              >
                {t("Create room")}
              </button>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
