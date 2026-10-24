"use client";

import { useSyncExternalStore } from "react";

import { getCommunityUnread, subscribeCommunityUnread } from "@/lib/community-unread";

/** Unread Community notifications (polled every minute while mounted). */
export function useCommunityUnread(enabled = true): number {
  const count = useSyncExternalStore(
    enabled ? subscribeCommunityUnread : noopSubscribe,
    getCommunityUnread,
    () => 0,
  );
  return enabled ? count : 0;
}

function noopSubscribe(): () => void {
  return () => undefined;
}
