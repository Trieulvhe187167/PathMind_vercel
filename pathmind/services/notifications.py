"""Email is the only external notification channel. Sends never block or break the caller."""

from __future__ import annotations

import logging
import threading

from pathmind.services import email_verification as ev

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> None:
    """Send in the background; with no SMTP the message is only logged."""
    to = (to or "").strip()
    if not to:
        return

    def _run() -> None:
        try:
            if not ev._send_mail(to, subject, body):
                logger.info("Email not sent (SMTP off or failed) to %s: %s", to, subject)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Email to %s failed: %s", to, exc)

    threading.Thread(target=_run, daemon=True).start()


def email_of(user_id: str) -> str:
    try:
        from pathmind.multi_user.identity import load_users

        for record in load_users().values():
            if str(record.get("id") or "") == str(user_id):
                return str(record.get("email") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def notify_user(user_id: str, subject: str, body: str) -> None:
    send_email(email_of(user_id), subject, body)
