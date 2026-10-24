"""Send e-mail through the SMTP server the admin configured (Settings -> E-mail).

Without SMTP, messages are written to the backend log instead, so codes and links can still be
found during setup.
"""

from __future__ import annotations

from email.message import EmailMessage
import logging
import smtplib
import ssl

from sqlalchemy.orm import Session

from .app_settings import get_setting, secret

log = logging.getLogger("pathmind.mail")
# Tests collect messages here instead of sending.
OUTBOX: list[dict] | None = None


def configured(db: Session) -> bool:
    cfg = get_setting(db, "smtp")
    return bool(cfg.get("host") and cfg.get("from_address"))


def send(db: Session, to: str, subject: str, body: str) -> bool:
    """Send a plain-text e-mail. Returns False (and logs) when SMTP is not configured or fails."""
    if OUTBOX is not None:
        OUTBOX.append({"to": to, "subject": subject, "body": body})
        return True
    cfg = get_setting(db, "smtp")
    if not (cfg.get("host") and cfg.get("from_address")):
        log.warning("SMTP not configured; e-mail to %s not sent. Subject: %s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = cfg["from_address"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        port = int(cfg.get("port") or (465 if cfg.get("use_ssl") else 587))
        if cfg.get("use_ssl"):
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg["host"], port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg["host"], port, timeout=20)
            if cfg.get("use_tls"):
                server.starttls(context=ssl.create_default_context())
        with server:
            if cfg.get("username"):
                server.login(cfg["username"], secret(db, "smtp", "password"))
            server.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Could not send e-mail to %s: %s", to, exc)
        raise MailError(str(exc)) from exc


class MailError(RuntimeError):
    pass
