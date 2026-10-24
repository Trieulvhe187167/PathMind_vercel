"use client";

import { useEffect, useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useTranslation } from "react-i18next";

import Tooltip from "@/components/common/Tooltip";
import { clearAnswerRating, getSessionRatings, rateAnswer } from "@/lib/staff-api";

type Verdict = "right" | "wrong";
type Ratings = Record<string, { verdict: Verdict; reason: string }>;

// One fetch per session, shared by every answer in it.
const sessionCache = new Map<string, Promise<Ratings>>();

const ICON_BTN =
  "inline-flex items-center justify-center rounded-md p-1 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/50 hover:text-[var(--foreground)]";

/** Mark an AI answer right or wrong, with an optional reason for wrong ones. */
export function RateAnswerButtons({
  sessionId,
  messageId,
  content,
}: {
  sessionId?: string | null;
  messageId: string;
  content: string;
}) {
  const { t } = useTranslation();
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (!sessionId) return;
    let p = sessionCache.get(sessionId);
    if (!p) {
      p = getSessionRatings(sessionId).catch(() => ({}) as Ratings);
      sessionCache.set(sessionId, p);
    }
    let alive = true;
    void p.then((r) => {
      if (alive && r[messageId]) setVerdict(r[messageId].verdict);
    });
    return () => {
      alive = false;
    };
  }, [sessionId, messageId]);

  function send(next: Verdict, why = "") {
    const previous = verdict;
    setVerdict(next);
    setAsking(false);
    void rateAnswer({
      session_id: sessionId ?? "",
      message_id: messageId,
      verdict: next,
      reason: why,
      excerpt: content.slice(0, 300),
    }).catch(() => setVerdict(previous));
  }

  function toggle(next: Verdict) {
    if (verdict === next) {
      setVerdict(null);
      void clearAnswerRating(messageId).catch(() => setVerdict(next));
      return;
    }
    if (next === "wrong") setAsking(true);
    else send("right");
  }

  return (
    <>
      <Tooltip label={t("Right answer")} side="top">
        <button type="button" aria-pressed={verdict === "right"} aria-label={t("Right answer")} onClick={() => toggle("right")} className={`${ICON_BTN} ${verdict === "right" ? "text-emerald-600" : ""}`}>
          <ThumbsUp size={15} strokeWidth={1.5} />
        </button>
      </Tooltip>
      <Tooltip label={t("Wrong answer")} side="top">
        <button type="button" aria-pressed={verdict === "wrong"} aria-label={t("Wrong answer")} onClick={() => toggle("wrong")} className={`${ICON_BTN} ${verdict === "wrong" ? "text-red-500" : ""}`}>
          <ThumbsDown size={15} strokeWidth={1.5} />
        </button>
      </Tooltip>
      {asking && (
        <span className="ml-2 inline-flex items-center gap-1">
          <input
            autoFocus
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") send("wrong", reason);
              if (e.key === "Escape") setAsking(false);
            }}
            placeholder={t("What was wrong? (optional)")}
            className="w-56 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-[12px]"
          />
          <button type="button" className="rounded-md px-2 py-1 text-[12px] hover:bg-[var(--muted)]" onClick={() => send("wrong", reason)}>
            {t("Send")}
          </button>
        </span>
      )}
    </>
  );
}
