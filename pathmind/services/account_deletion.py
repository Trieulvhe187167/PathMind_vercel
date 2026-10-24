"""Permanent account deletion ("right to erasure", GDPR Art. 17).

Only the account owner can do this (Settings → Profile → Delete account,
``POST /api/auth/account/delete``). Administrators cannot delete accounts;
they can only lock / unlock them (``PUT /api/auth/users/{username}/status``).

What is erased:

Erased
  * the login record (username, password hash, full name, email, avatar);
  * everything under ``data/users/<id>/`` — chats, notes, notebooks, books,
    knowledge bases, memory, partners, uploaded files, settings, API keys;
  * per-user system state: permission grants, device credentials, stored
    connector secrets / MCP / CLI-app state;
  * anonymised memory aggregates the person opted to share (and the consent
    record that linked them);
  * social links: friendships, partner shares, study-room memberships (rooms
    the user hosted are closed), pending email-verification codes.

Kept, but no longer tied to a person
  * payments and daily usage/cost rows — needed for accounting and tax
    records. The ``users`` row they reference is anonymised
    (``deleted~<id8>``, no email or name, inactive).

Active paid subscriptions end immediately; there is no automatic refund.
"""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import re
import shutil

logger = logging.getLogger(__name__)

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PER_OWNER_SYSTEM_DIRS = ("user-secrets", "user-mcp")


class AccountDeletionError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _safe_rmtree(path: Path, root: Path) -> bool:
    """Remove ``path`` only if it is a direct child of ``root``."""
    try:
        resolved = path.resolve()
        if resolved.parent != root.resolve() or not resolved.exists():
            return False
        shutil.rmtree(resolved)
        return True
    except Exception as exc:
        logger.warning("Could not remove %s: %s", path, exc)
        return False


def _is_last_admin(username: str) -> bool:
    from pathmind.services.auth import list_users

    admins = [u for u in list_users() if u.get("role") == "admin" and not u.get("disabled")]
    return len(admins) <= 1 and any(u.get("username") == username for u in admins)


def _erase_files(user_id: str) -> dict[str, int]:
    from pathmind.multi_user import paths

    removed = {"user_dir": 0, "system": 0}
    if _safe_rmtree(paths.USERS_ROOT / user_id, paths.USERS_ROOT):
        removed["user_dir"] = 1
    for name in _PER_OWNER_SYSTEM_DIRS:
        root = paths.SYSTEM_ROOT / name
        if _safe_rmtree(root / user_id, root):
            removed["system"] += 1
    try:
        from pathmind.multi_user.grants import grant_path

        grant = grant_path(user_id)
        if grant.exists():
            grant.unlink()
            removed["system"] += 1
    except Exception as exc:
        logger.warning("Could not remove grants of %s: %s", user_id, exc)
    try:
        from pathmind.multi_user.device_credentials import revoke_device_credentials_for_user

        revoke_device_credentials_for_user(user_id, revoked_by="account_deleted")
    except Exception as exc:
        logger.warning("Could not revoke device credentials of %s: %s", user_id, exc)
    try:
        from pathmind.multi_user.identity import delete_avatar_file

        delete_avatar_file(user_id)
    except Exception:
        pass
    return removed


def _scrub_database(user_id: str) -> None:
    from sqlalchemy import or_

    from pathmind.database.connection import get_db_session
    from pathmind.database.models import (
        EmailVerification,
        Friendship,
        PartnerShare,
        RoomMember,
        SharedRoom,
        Subscription,
        User,
    )

    now = datetime.utcnow()
    with get_db_session() as db:
        db.query(Friendship).filter(
            or_(Friendship.user_id == user_id, Friendship.friend_id == user_id)
        ).delete(synchronize_session=False)
        db.query(PartnerShare).filter(
            or_(PartnerShare.owner_id == user_id, PartnerShare.shared_with_id == user_id)
        ).delete(synchronize_session=False)
        db.query(RoomMember).filter(RoomMember.user_id == user_id).delete(
            synchronize_session=False
        )
        for room in db.query(SharedRoom).filter(SharedRoom.host_id == user_id).all():
            room.is_active = False
            db.query(RoomMember).filter(RoomMember.room_id == room.id).delete(
                synchronize_session=False
            )
        db.query(EmailVerification).filter(EmailVerification.user_id == user_id).delete(
            synchronize_session=False
        )
        from pathmind.services.memory_insights import withdraw as _withdraw_insights

        _withdraw_insights(db, user_id)
        for sub in db.query(Subscription).filter(Subscription.user_id == user_id).all():
            if sub.status == "active":
                sub.status = "canceled"
                sub.cancel_at_period_end = True
                if hasattr(sub, "current_period_end"):
                    sub.current_period_end = now
        row = db.get(User, user_id)
        if row is not None:
            row.username = f"deleted~{user_id[:8]}~{int(now.timestamp())}"[:100]
            row.email = None
            row.full_name = None
            row.email_verified = None
            row.email_verified_at = None
            row.avatar = ""
            row.is_active = False
        db.commit()


def delete_account(username: str, *, actor: str) -> dict:
    """Erase ``username``'s account. Raises ``AccountDeletionError``."""
    from pathmind.services.auth import delete_user, get_user_info

    info = get_user_info(username)
    if info is None:
        raise AccountDeletionError("not_found")
    user_id = str(info.get("id") or "")
    if not _USER_ID_RE.match(user_id) or user_id in {"local-admin", "env-admin"}:
        raise AccountDeletionError("not_deletable")
    if info.get("role") == "admin" and _is_last_admin(username):
        raise AccountDeletionError("last_admin")

    # Login record first: from this moment the account cannot sign in and its
    # existing sessions stop working (tokens are checked against the store).
    if not delete_user(username):
        raise AccountDeletionError("not_found")
    removed = _erase_files(user_id)
    try:
        _scrub_database(user_id)
    except Exception as exc:
        logger.warning("Database clean-up for deleted account %s failed: %s", user_id, exc)
    try:
        from pathmind.multi_user.audit import log_admin_action

        log_admin_action("account_deleted", target_user_id=user_id, summary={"by": actor})
    except Exception:
        pass
    logger.info("Account %s deleted by %s: %s", user_id, actor, removed)
    return {"user_id": user_id, **removed}


__all__ = ["AccountDeletionError", "delete_account"]
