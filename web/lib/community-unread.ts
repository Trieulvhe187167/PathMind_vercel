/**
 * Shared unread-notification count for the Community badge (sidebar + bell).
 *
 * One poller per page no matter how many components read it; any component
 * that changes read state calls ``setCommunityUnread`` so every badge updates
 * at once.
 */

import { fetchUnreadCount } from "@/lib/community-api";

const POLL_MS = 60_000;

let unread = 0;
let timer: ReturnType<typeof setInterval> | null = null;
let consumers = 0;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of Array.from(listeners)) listener();
}

export function getCommunityUnread(): number {
  return unread;
}

export function setCommunityUnread(value: number): void {
  const next = Math.max(0, Math.floor(value || 0));
  if (next === unread) return;
  unread = next;
  emit();
}

export async function refreshCommunityUnread(): Promise<void> {
  try {
    const res = await fetchUnreadCount();
    setCommunityUnread(res.unread);
  } catch {
    // Signed out / offline: keep the last value.
  }
}

/** Subscribe (for ``useSyncExternalStore``); the first subscriber starts polling. */
export function subscribeCommunityUnread(listener: () => void): () => void {
  listeners.add(listener);
  consumers += 1;
  if (consumers === 1 && typeof window !== "undefined") {
    void refreshCommunityUnread();
    timer = setInterval(() => {
      if (document.visibilityState === "visible") void refreshCommunityUnread();
    }, POLL_MS);
  }
  return () => {
    listeners.delete(listener);
    consumers -= 1;
    if (consumers === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}
