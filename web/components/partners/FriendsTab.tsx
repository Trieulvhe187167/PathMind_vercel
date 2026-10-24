"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  Bot,
  Check,
  Copy,
  DoorOpen,
  LogIn,
  MessageSquare,
  Plus,
  Radio,
  Search,
  Share2,
  ShieldOff,
  UserCheck,
  UserMinus,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  cancelFriendRequest,
  closeStudyRoom,
  createStudyRoom,
  fetchFriendRequests,
  fetchFriendsList,
  fetchMyRooms,
  fetchSharedPartners,
  joinStudyRoom,
  leaveStudyRoom,
  removeFriend,
  removeRoomMember,
  respondFriendRequest,
  revokePartnerShare,
  sendFriendRequest,
  type BlockedUser,
  type FriendItem,
  type PendingFriendRequest,
  type SentFriendRequest,
  type SharedPartnerItem,
  type StudyRoomItem,
} from "@/lib/friends-api";

const card = "rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5";
const input =
  "min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]";
const primaryBtn =
  "inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50";
const ghostBtn =
  "rounded-md p-1.5 text-[var(--muted-foreground)] hover:bg-muted hover:text-[var(--foreground)]";

type Confirm =
  | { kind: "unfriend"; friend: FriendItem }
  | { kind: "leave"; room: StudyRoomItem }
  | null;

export interface RoomPartnerOption {
  partner_id: string;
  name: string;
}

export default function FriendsTab({
  partners = [],
}: {
  /** Partners the user manages — any of them can be bound to a new room. */
  partners?: RoomPartnerOption[];
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const [friends, setFriends] = useState<FriendItem[]>([]);
  const [incoming, setIncoming] = useState<PendingFriendRequest[]>([]);
  const [sent, setSent] = useState<SentFriendRequest[]>([]);
  const [blocked, setBlocked] = useState<BlockedUser[]>([]);
  const [shared, setShared] = useState<SharedPartnerItem[]>([]);
  const [rooms, setRooms] = useState<StudyRoomItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [target, setTarget] = useState("");
  const [sending, setSending] = useState(false);
  const [roomName, setRoomName] = useState("");
  const [roomPartner, setRoomPartner] = useState("");
  const [creating, setCreating] = useState(false);
  const [roomCode, setRoomCode] = useState("");
  const [joining, setJoining] = useState(false);
  const [copied, setCopied] = useState("");
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [f, requests, s, r] = await Promise.all([
        fetchFriendsList(),
        fetchFriendRequests(),
        fetchSharedPartners(),
        fetchMyRooms(),
      ]);
      setFriends(f);
      setIncoming(requests.pending_requests ?? []);
      setSent(requests.sent_requests ?? []);
      setBlocked(requests.blocked ?? []);
      setShared(s);
      setRooms(r);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not load your friends"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setError("");
    try {
      await fn();
      if (ok) setNotice(ok);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Action failed"));
    }
  };

  const openRoom = (code: string) =>
    router.push(`/partners/rooms/${encodeURIComponent(code)}`);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!target.trim()) return;
    setSending(true);
    await run(async () => {
      const res = await sendFriendRequest(target.trim());
      setNotice(res.message);
      setTarget("");
    });
    setSending(false);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!roomName.trim()) return;
    setCreating(true);
    setError("");
    try {
      const room = await createStudyRoom(roomName.trim(), roomPartner || undefined);
      openRoom(room.room_code);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Could not create the room"));
      setCreating(false);
    }
  }

  async function handleJoin(e: React.FormEvent) {
    e.preventDefault();
    if (!roomCode.trim()) return;
    setJoining(true);
    setError("");
    try {
      const room = await joinStudyRoom(roomCode);
      openRoom(room.room_code);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : t("Room code not found or the room is closed"),
      );
      setJoining(false);
    }
  }

  async function handleConfirm() {
    if (!confirm) return;
    setBusy(true);
    if (confirm.kind === "unfriend") {
      await run(() => removeFriend(confirm.friend.user_id), t("Friend removed."));
    } else if (confirm.room.is_host) {
      await run(() => closeStudyRoom(confirm.room.room_code), t("Room closed."));
    } else {
      await run(() => leaveStudyRoom(confirm.room.room_code), t("You left the room."));
    }
    setBusy(false);
    setConfirm(null);
  }

  const copyCode = (code: string) => {
    void navigator.clipboard?.writeText(code);
    setCopied(code);
    setTimeout(() => setCopied(""), 1500);
  };

  if (loading) {
    return (
      <div className="py-16 text-center text-sm text-[var(--muted-foreground)]">
        {t("Loading...")}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {(error || notice) && (
        <div
          className={`flex items-center gap-2 rounded-xl border p-3 text-sm ${
            error
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : "border-success/30 bg-success-surface text-success"
          }`}
        >
          <span className="flex-1">{error || notice}</span>
          <button
            onClick={() => (error ? setError("") : setNotice(""))}
            aria-label={t("Close")}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Study rooms: create / join */}
      <div className="grid gap-5 md:grid-cols-2">
        <div className={`${card} border-primary/30`}>
          <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
            <Plus size={16} className="text-primary" /> {t("Create a study room")}
          </h3>
          <p className="mb-3 text-xs text-[var(--muted-foreground)]">
            {t(
              "Chat together in real time and invite friends with the room code. Add one of your Partners so anyone in the room can ask it.",
            )}
          </p>
          <form onSubmit={handleCreate} className="space-y-2">
            <input
              value={roomName}
              onChange={(e) => setRoomName(e.target.value)}
              placeholder={t("Room name, e.g. Physics revision")}
              maxLength={120}
              className={`${input} w-full`}
            />
            <div className="flex gap-2">
              <select
                value={roomPartner}
                onChange={(e) => setRoomPartner(e.target.value)}
                className={input}
                aria-label={t("Room Partner")}
              >
                <option value="">{t("No Partner (people only)")}</option>
                {partners.map((p) => (
                  <option key={p.partner_id} value={p.partner_id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <button
                type="submit"
                disabled={creating || !roomName.trim()}
                className={primaryBtn}
              >
                <Plus size={14} /> {t("Create room")}
              </button>
            </div>
          </form>
        </div>

        <div className={card}>
          <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
            <LogIn size={16} className="text-primary" /> {t("Join with a code")}
          </h3>
          <p className="mb-3 text-xs text-[var(--muted-foreground)]">
            {t("Ask the host for the 6-character room code.")}
          </p>
          <form onSubmit={handleJoin} className="flex gap-2">
            <input
              value={roomCode}
              onChange={(e) => setRoomCode(e.target.value.toUpperCase())}
              placeholder={t("Room code")}
              maxLength={12}
              className={`${input} font-mono uppercase tracking-widest`}
            />
            <button
              type="submit"
              disabled={joining || !roomCode.trim()}
              className={primaryBtn}
            >
              {t("Join")}
            </button>
          </form>
        </div>
      </div>

      {/* My rooms */}
      <div className={card}>
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
          <Radio size={16} className="text-primary" />{" "}
          {t("My study rooms ({{count}})", { count: rooms.length })}
        </h3>
        {rooms.length === 0 ? (
          <p className="text-sm text-[var(--muted-foreground)]">
            {t("You're not in any study room yet. Create one above or join with a code.")}
          </p>
        ) : (
          rooms.map((room) => (
            <div
              key={room.room_code}
              className="border-t border-[var(--border)] py-3 first:border-0"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-[var(--foreground)]">
                    {room.room_name}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-2 text-xs text-[var(--muted-foreground)]">
                    <button
                      onClick={() => copyCode(room.room_code)}
                      title={t("Copy room code")}
                      className="inline-flex items-center gap-1 font-mono hover:text-[var(--foreground)]"
                    >
                      {room.room_code}{" "}
                      {copied === room.room_code ? (
                        <Check size={11} />
                      ) : (
                        <Copy size={11} />
                      )}
                    </button>
                    <span>· {t("Host: {{name}}", { name: room.host_username })}</span>
                    {room.partner_id && (
                      <span className="inline-flex items-center gap-1">
                        · <Bot size={11} /> {room.partner_name || room.partner_id}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => openRoom(room.room_code)}
                    className="inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:opacity-90"
                  >
                    <MessageSquare size={13} /> {t("Open chat")}
                  </button>
                  <button
                    onClick={() => setConfirm({ kind: "leave", room })}
                    className="inline-flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--muted-foreground)] hover:text-destructive"
                  >
                    <DoorOpen size={13} />{" "}
                    {room.is_host ? t("Close room") : t("Leave room")}
                  </button>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {room.members.map((m) => (
                  <span
                    key={m.user_id}
                    className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-[var(--foreground)]"
                  >
                    {m.username}
                    {m.role === "host" && (
                      <span className="text-[10px] text-primary">{t("host")}</span>
                    )}
                    {room.is_host && m.role !== "host" && (
                      <button
                        title={t("Remove from room")}
                        onClick={() =>
                          void run(() => removeRoomMember(room.room_code, m.user_id))
                        }
                        className="text-[var(--muted-foreground)] hover:text-destructive"
                      >
                        <X size={11} />
                      </button>
                    )}
                  </span>
                ))}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Incoming requests */}
      {incoming.length > 0 && (
        <div className={card}>
          <h3 className="mb-3 text-sm font-semibold text-[var(--foreground)]">
            {t("Friend requests ({{count}})", { count: incoming.length })}
          </h3>
          {incoming.map((r) => (
            <div
              key={r.request_id}
              className="flex items-center justify-between border-t border-[var(--border)] py-2 first:border-0"
            >
              <span className="text-sm font-medium text-[var(--foreground)]">
                {r.requester_username}
              </span>
              <div className="flex gap-1">
                <button
                  title={t("Accept")}
                  onClick={() =>
                    void run(
                      () => respondFriendRequest(r.request_id, "accept"),
                      t("You are now friends."),
                    )
                  }
                  className="rounded-md p-1.5 text-success hover:bg-success-surface"
                >
                  <Check size={16} />
                </button>
                <button
                  title={t("Decline")}
                  onClick={() =>
                    void run(() => respondFriendRequest(r.request_id, "reject"))
                  }
                  className={ghostBtn}
                >
                  <X size={16} />
                </button>
                <button
                  title={t("Block")}
                  onClick={() =>
                    void run(
                      () => respondFriendRequest(r.request_id, "block"),
                      t("User blocked."),
                    )
                  }
                  className="rounded-md p-1.5 text-[var(--muted-foreground)] hover:bg-destructive/10 hover:text-destructive"
                >
                  <ShieldOff size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-5 md:grid-cols-2">
        {/* Friends */}
        <div className={card}>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
            <Users size={16} className="text-primary" />{" "}
            {t("Friends ({{count}})", { count: friends.length })}
          </h3>
          <form onSubmit={handleSend} className="mb-3 flex gap-2">
            <div className="relative flex min-w-0 flex-1">
              <Search
                size={14}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)]"
              />
              <input
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                placeholder={t("Username or email")}
                className={`${input} pl-8`}
              />
            </div>
            <button
              type="submit"
              disabled={sending || !target.trim()}
              className={primaryBtn}
            >
              <UserPlus size={14} /> {t("Add friend")}
            </button>
          </form>
          {friends.length === 0 ? (
            <p className="text-sm text-[var(--muted-foreground)]">
              {t("No friends yet. Send a request by username above.")}
            </p>
          ) : (
            friends.map((f) => (
              <div
                key={f.user_id}
                className="flex items-center justify-between border-t border-[var(--border)] py-2 first:border-0"
              >
                <div className="flex items-center gap-2.5">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/15 text-sm font-semibold text-primary">
                    {(f.username || "?")[0]?.toUpperCase()}
                  </div>
                  <div>
                    <div className="text-sm font-medium text-[var(--foreground)]">
                      {f.username}
                    </div>
                    <div className="flex items-center gap-1 text-[10px] text-[var(--muted-foreground)]">
                      <UserCheck size={10} /> {t("Friend")}
                    </div>
                  </div>
                </div>
                <button
                  title={t("Remove friend")}
                  onClick={() => setConfirm({ kind: "unfriend", friend: f })}
                  className={ghostBtn}
                >
                  <UserMinus size={15} />
                </button>
              </div>
            ))
          )}
          {sent.length > 0 && (
            <div className="mt-4 border-t border-[var(--border)] pt-3">
              <div className="mb-1.5 text-xs text-[var(--muted-foreground)]">
                {t("Sent, waiting for a reply")}
              </div>
              {sent.map((r) => (
                <div
                  key={r.request_id}
                  className="flex items-center justify-between py-1 text-sm"
                >
                  <span className="text-[var(--foreground)]">{r.target_username}</span>
                  <button
                    onClick={() =>
                      void run(
                        () => cancelFriendRequest(r.request_id),
                        t("Request canceled."),
                      )
                    }
                    className="text-xs text-[var(--muted-foreground)] hover:text-destructive"
                  >
                    {t("Cancel")}
                  </button>
                </div>
              ))}
            </div>
          )}
          {blocked.length > 0 && (
            <div className="mt-4 border-t border-[var(--border)] pt-3">
              <div className="mb-1.5 text-xs text-[var(--muted-foreground)]">
                {t("Blocked")}
              </div>
              {blocked.map((b) => (
                <div
                  key={b.request_id}
                  className="flex items-center justify-between py-1 text-sm"
                >
                  <span className="text-[var(--foreground)]">{b.username}</span>
                  <button
                    onClick={() =>
                      void run(() => cancelFriendRequest(b.request_id), t("User unblocked."))
                    }
                    className="text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                  >
                    {t("Unblock")}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Shared partners */}
        <div className={card}>
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
            <Share2 size={16} className="text-primary" />{" "}
            {t("Shared with me ({{count}})", { count: shared.length })}
          </h3>
          {shared.length === 0 ? (
            <p className="text-sm text-[var(--muted-foreground)]">
              {t("No Partners have been shared with you yet.")}
            </p>
          ) : (
            shared.map((sp) => (
              <div
                key={sp.share_id}
                className="flex items-center justify-between border-t border-[var(--border)] py-2 first:border-0"
              >
                <Link
                  href={`/partners/${encodeURIComponent(sp.partner_id)}`}
                  className="flex items-center gap-2.5 hover:underline"
                >
                  <Bot size={16} className="text-primary" />
                  <div>
                    <div className="text-sm font-medium text-[var(--foreground)]">
                      {sp.partner_name}
                    </div>
                    <div className="text-[10px] text-[var(--muted-foreground)]">
                      {t("from {{name}}", { name: sp.owner_username })}
                    </div>
                  </div>
                </Link>
                <button
                  title={t("Remove from my list")}
                  onClick={() => void run(() => revokePartnerShare(sp.share_id))}
                  className={ghostBtn}
                >
                  <X size={15} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirm !== null}
        title={
          confirm?.kind === "unfriend"
            ? t("Remove this friend?")
            : confirm?.room.is_host
              ? t("Close this study room?")
              : t("Leave this study room?")
        }
        tone="danger"
        confirmLabel={t("Confirm")}
        busy={busy}
        onConfirm={() => void handleConfirm()}
        onCancel={() => setConfirm(null)}
      >
        {confirm?.kind === "unfriend"
          ? t("Partners you shared with each other will be unshared too.")
          : confirm?.room.is_host
            ? t("Every member loses the room chat and access to the room's Partner.")
            : t("You will lose the room chat and access to the room's Partner.")}
      </ConfirmDialog>
    </div>
  );
}
