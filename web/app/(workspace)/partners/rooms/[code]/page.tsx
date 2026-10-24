"use client";

/**
 * Study room: a shared chat for everyone in the room, plus the room's Partner
 * (when it has one) answering publicly on request.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  Bot,
  Check,
  Copy,
  DoorOpen,
  Loader2,
  Send,
  Users,
} from "lucide-react";
import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  closeStudyRoom,
  fetchRoomMessages,
  fetchStudyRoom,
  leaveStudyRoom,
  postRoomMessage,
  type RoomChatMessage,
  type StudyRoomItem,
} from "@/lib/friends-api";

const POLL_MS = 2500;
const ROOM_POLL_MS = 15000;

function mergeMessages(
  current: RoomChatMessage[],
  incoming: RoomChatMessage[],
): RoomChatMessage[] {
  if (incoming.length === 0) return current;
  const byId = new Map(current.map((m) => [m.id, m]));
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

function timeLabel(iso?: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function StudyRoomPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const params = useParams<{ code: string }>();
  const code = decodeURIComponent(String(params?.code ?? "")).toUpperCase();
  const { userId } = useAuthStatus();

  const [room, setRoom] = useState<StudyRoomItem | null>(null);
  const [messages, setMessages] = useState<RoomChatMessage[]>([]);
  const [partnerBusy, setPartnerBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [askPartner, setAskPartner] = useState(false);
  const [sending, setSending] = useState(false);
  const [copied, setCopied] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [leaving, setLeaving] = useState(false);

  const lastIdRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const scrollerRef = useRef<HTMLDivElement>(null);

  const addMessages = useCallback((incoming: RoomChatMessage[]) => {
    if (incoming.length === 0) return;
    setMessages((prev) => mergeMessages(prev, incoming));
    lastIdRef.current = Math.max(lastIdRef.current, ...incoming.map((m) => m.id));
  }, []);

  const loadRoom = useCallback(async () => {
    try {
      setRoom(await fetchStudyRoom(code));
      setLoadError("");
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t("Could not load the room"));
    }
  }, [code, t]);

  const poll = useCallback(async () => {
    try {
      const res = await fetchRoomMessages(code, lastIdRef.current);
      addMessages(res.messages);
      setPartnerBusy(res.partner_busy);
    } catch {
      // Transient; the room request surfaces real errors (closed, removed).
    }
  }, [addMessages, code]);

  useEffect(() => {
    lastIdRef.current = 0;
    setMessages([]);
    void loadRoom();
    void poll();
    const fast = window.setInterval(() => {
      if (document.visibilityState === "visible") void poll();
    }, POLL_MS);
    const slow = window.setInterval(() => void loadRoom(), ROOM_POLL_MS);
    return () => {
      window.clearInterval(fast);
      window.clearInterval(slow);
    };
  }, [loadRoom, poll]);

  useEffect(() => {
    if (stickToBottom.current) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, sending]);

  const onScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  async function send() {
    const content = draft.trim();
    if (!content || sending) return;
    const ask = askPartner && Boolean(room?.partner_id);
    setSending(true);
    setError("");
    stickToBottom.current = true;
    setDraft("");
    if (ask) setPartnerBusy(true);
    try {
      const res = await postRoomMessage(code, content, ask);
      addMessages(res.messages);
    } catch (e) {
      setDraft(content);
      setError(e instanceof Error ? e.message : t("Could not send the message"));
    } finally {
      setSending(false);
      if (ask) setPartnerBusy(false);
    }
  }

  async function leave() {
    if (!room) return;
    setLeaving(true);
    try {
      if (room.is_host) await closeStudyRoom(room.room_code);
      else await leaveStudyRoom(room.room_code);
      router.push("/partners?tab=friends");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Action failed"));
      setLeaving(false);
      setConfirmLeave(false);
    }
  }

  const copyCode = () => {
    void navigator.clipboard?.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (loadError && !room) {
    return (
      <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center gap-4 px-6 text-center">
        <p className="text-sm text-[var(--muted-foreground)]">{loadError}</p>
        <Link
          href="/partners?tab=friends"
          className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
        >
          <ArrowLeft size={14} /> {t("Back to study rooms")}
        </Link>
      </div>
    );
  }

  if (!room) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
      </div>
    );
  }

  const hasPartner = Boolean(room.partner_id);

  return (
    <div className="mx-auto flex h-full max-w-5xl gap-5 px-4 py-4 md:px-6">
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--card)]">
        {/* Header */}
        <div className="flex flex-wrap items-center gap-3 border-b border-[var(--border)] px-4 py-3">
          <Link
            href="/partners?tab=friends"
            aria-label={t("Back to study rooms")}
            className="rounded-md p-1 text-[var(--muted-foreground)] hover:bg-muted hover:text-[var(--foreground)]"
          >
            <ArrowLeft size={16} />
          </Link>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[15px] font-semibold text-[var(--foreground)]">
              {room.room_name}
            </h1>
            <div className="flex flex-wrap items-center gap-x-2 text-xs text-[var(--muted-foreground)]">
              <button
                onClick={copyCode}
                title={t("Copy room code")}
                className="inline-flex items-center gap-1 font-mono hover:text-[var(--foreground)]"
              >
                {room.room_code} {copied ? <Check size={11} /> : <Copy size={11} />}
              </button>
              <span>· {t("{{count}} members", { count: room.members.length })}</span>
              {hasPartner && (
                <span className="inline-flex items-center gap-1">
                  · <Bot size={11} /> {room.partner_name || room.partner_id}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => setConfirmLeave(true)}
            className="inline-flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1 text-xs text-[var(--muted-foreground)] hover:text-destructive"
          >
            <DoorOpen size={13} /> {room.is_host ? t("Close room") : t("Leave room")}
          </button>
        </div>

        {/* Messages */}
        <div
          ref={scrollerRef}
          onScroll={onScroll}
          className="flex-1 space-y-3 overflow-y-auto px-4 py-4"
        >
          {messages.length === 0 && (
            <p className="py-10 text-center text-sm text-[var(--muted-foreground)]">
              {t("No messages yet. Share the room code {{code}} and say hi!", {
                code: room.room_code,
              })}
            </p>
          )}
          {messages.map((m) => {
            if (m.role === "system") {
              return (
                <div
                  key={m.id}
                  className="text-center text-[11px] text-[var(--muted-foreground)]"
                >
                  {m.content}
                </div>
              );
            }
            const mine = m.role === "user" && m.user_id === userId;
            const isPartner = m.role === "partner";
            return (
              <div key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[85%] ${mine ? "items-end" : "items-start"} flex flex-col`}>
                  <div className="mb-0.5 flex items-center gap-1.5 px-1 text-[11px] text-[var(--muted-foreground)]">
                    {isPartner && <Bot size={11} className="text-primary" />}
                    <span className={isPartner ? "font-medium text-primary" : ""}>
                      {mine ? t("You") : m.author}
                    </span>
                    <span>{timeLabel(m.created_at)}</span>
                  </div>
                  <div
                    className={`rounded-2xl px-3.5 py-2 text-sm ${
                      mine
                        ? "bg-primary text-primary-foreground"
                        : isPartner
                          ? "border border-primary/20 bg-primary/5 text-[var(--foreground)]"
                          : "bg-muted text-[var(--foreground)]"
                    }`}
                  >
                    {isPartner ? (
                      <MarkdownRenderer
                        content={m.content}
                        variant="compact"
                        className="text-sm leading-6"
                      />
                    ) : (
                      <span className="whitespace-pre-wrap break-words">{m.content}</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
          {partnerBusy && (
            <div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
              <Loader2 size={12} className="animate-spin" />
              {t("{{name}} is answering…", {
                name: room.partner_name || "Partner",
              })}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Composer */}
        <div className="border-t border-[var(--border)] p-3">
          {error && <p className="mb-2 text-xs text-destructive">{error}</p>}
          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={2}
              maxLength={4000}
              placeholder={
                askPartner && hasPartner
                  ? t("Ask {{name}} — everyone in the room will see the answer", {
                      name: room.partner_name || "Partner",
                    })
                  : t("Message the room (Enter to send, Shift+Enter for a new line)")
              }
              className="min-h-[44px] flex-1 resize-none rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]"
            />
            <button
              onClick={() => void send()}
              disabled={sending || !draft.trim()}
              aria-label={t("Send")}
              className="inline-flex h-[44px] items-center gap-1.5 rounded-xl bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {sending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
            </button>
          </div>
          {hasPartner && (
            <label className="mt-2 inline-flex cursor-pointer items-center gap-2 text-xs text-[var(--muted-foreground)]">
              <input
                type="checkbox"
                checked={askPartner}
                onChange={(e) => setAskPartner(e.target.checked)}
                className="accent-[var(--primary)]"
              />
              <Bot size={12} className="text-primary" />
              {t("Ask {{name}} (uses your credits)", {
                name: room.partner_name || "Partner",
              })}
            </label>
          )}
        </div>
      </div>

      {/* Members */}
      <aside className="hidden w-56 shrink-0 md:block">
        <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
            <Users size={15} className="text-primary" /> {t("Members")}
          </h2>
          <ul className="space-y-2">
            {room.members.map((member) => (
              <li key={member.user_id} className="flex items-center gap-2 text-sm">
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
                  {(member.username || "?")[0]?.toUpperCase()}
                </span>
                <span className="min-w-0 flex-1 truncate text-[var(--foreground)]">
                  {member.username}
                </span>
                {member.role === "host" && (
                  <span className="text-[10px] text-primary">{t("host")}</span>
                )}
              </li>
            ))}
          </ul>
          {hasPartner && (
            <div className="mt-4 border-t border-[var(--border)] pt-3 text-xs text-[var(--muted-foreground)]">
              <div className="mb-1 flex items-center gap-1.5 font-medium text-[var(--foreground)]">
                <Bot size={13} className="text-primary" />
                {room.partner_name || room.partner_id}
              </div>
              {t("Tick “Ask” under the message box to bring the Partner into the conversation.")}
            </div>
          )}
        </div>
      </aside>

      <ConfirmDialog
        open={confirmLeave}
        title={room.is_host ? t("Close this study room?") : t("Leave this study room?")}
        tone="danger"
        confirmLabel={t("Confirm")}
        busy={leaving}
        onConfirm={() => void leave()}
        onCancel={() => setConfirmLeave(false)}
      >
        {room.is_host
          ? t("Every member loses the room chat and access to the room's Partner.")
          : t("You will lose the room chat and access to the room's Partner.")}
      </ConfirmDialog>
    </div>
  );
}
