"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { Bell, CheckCheck, Loader2 } from "lucide-react";

import { UserInitial, useTimeAgo } from "@/components/community/community-ui";
import { useCommunityUnread } from "@/hooks/useCommunityUnread";
import {
  listNotifications,
  markNotificationsRead,
  type CommunityNotification,
} from "@/lib/community-api";
import { setCommunityUnread } from "@/lib/community-unread";

function useNotificationText() {
  const { t } = useTranslation();
  return (n: CommunityNotification) => {
    switch (n.kind) {
      case "post_comment":
        return t("{{name}} commented on your post “{{title}}”", { name: n.actor, title: n.post_title });
      case "comment_reply":
        return t("{{name}} replied to your comment in “{{title}}”", { name: n.actor, title: n.post_title });
      case "skill_installed":
        return t("{{name}} added your skill “{{title}}”", { name: n.actor, title: n.skill_title });
      case "content_removed":
        return n.skill_title
          ? t("A moderator removed your skill “{{title}}”", { title: n.skill_title })
          : n.comment_id
            ? t("A moderator removed your comment in “{{title}}”", { title: n.post_title })
            : t("A moderator removed your post “{{title}}”", { title: n.post_title });
      default:
        return n.preview;
    }
  };
}

export default function NotificationBell() {
  const { t } = useTranslation();
  const router = useRouter();
  const timeAgo = useTimeAgo();
  const text = useNotificationText();
  const unread = useCommunityUnread();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<CommunityNotification[] | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next) return;
    try {
      const res = await listNotifications();
      setItems(res.items);
      setCommunityUnread(res.unread);
    } catch {
      setItems([]);
    }
  }

  async function markAll() {
    try {
      const res = await markNotificationsRead({ all: true });
      setCommunityUnread(res.unread);
      setItems((prev) => prev?.map((n) => ({ ...n, read: true })) ?? prev);
    } catch {
      /* keep as is */
    }
  }

  async function openItem(n: CommunityNotification) {
    setOpen(false);
    if (!n.read) {
      setItems((prev) => prev?.map((x) => (x.id === n.id ? { ...x, read: true } : x)) ?? prev);
      markNotificationsRead({ ids: [n.id] })
        .then((res) => setCommunityUnread(res.unread))
        .catch(() => undefined);
    }
    if (n.post_id) router.push(`/community/posts/${n.post_id}`);
    else if (n.skill_slug) router.push(`/community/skills/${encodeURIComponent(n.skill_slug)}`);
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => void toggle()}
        aria-label={t("Notifications")}
        className="relative inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--card)] text-[var(--foreground)] hover:bg-[var(--muted)]"
      >
        <Bell size={16} />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 min-w-[18px] rounded-full bg-[var(--primary)] px-1 text-center text-[10.5px] font-semibold leading-[18px] text-[var(--primary-foreground)]">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-[340px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover)] shadow-xl">
          <div className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2">
            <span className="text-[13px] font-semibold text-[var(--foreground)]">{t("Notifications")}</span>
            <button
              onClick={() => void markAll()}
              disabled={unread === 0}
              className="inline-flex items-center gap-1 text-[12px] text-[var(--primary)] hover:underline disabled:opacity-40"
            >
              <CheckCheck size={13} /> {t("Mark all as read")}
            </button>
          </div>
          <div className="max-h-[420px] overflow-y-auto">
            {items === null ? (
              <div className="flex justify-center py-6">
                <Loader2 size={16} className="animate-spin text-[var(--muted-foreground)]" />
              </div>
            ) : items.length === 0 ? (
              <p className="px-3 py-6 text-center text-[12.5px] text-[var(--muted-foreground)]">
                {t("No notifications yet.")}
              </p>
            ) : (
              items.map((n) => (
                <button
                  key={n.id}
                  onClick={() => void openItem(n)}
                  className={`flex w-full gap-2.5 border-b border-[var(--border)]/60 px-3 py-2.5 text-left last:border-0 hover:bg-[var(--muted)]/60 ${
                    n.read ? "" : "bg-[var(--primary)]/5"
                  }`}
                >
                  <UserInitial name={n.actor || "!"} size={28} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] leading-snug text-[var(--foreground)]">{text(n)}</div>
                    {n.preview && n.kind !== "skill_installed" && n.kind !== "content_removed" && (
                      <div className="mt-0.5 line-clamp-2 text-[12px] text-[var(--muted-foreground)]">{n.preview}</div>
                    )}
                    <div className="mt-0.5 text-[11px] text-[var(--muted-foreground)]">{timeAgo(n.created_at)}</div>
                  </div>
                  {!n.read && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-[var(--primary)]" />}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
