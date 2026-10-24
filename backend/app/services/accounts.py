"""Shared rules for creating accounts (sign-up, admin-created accounts, accepted invitations)."""

from __future__ import annotations

import re
import secrets
import string

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import User

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def error(code: int, field: str | None, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"field": field, "message": message})


def clean_username(raw: str) -> str:
    username = raw.strip()
    if not USERNAME_RE.match(username):
        raise error(422, "username", "Username: 3-32 letters, numbers, dot, dash or underscore.")
    return username


def check_password(password: str, *, username: str = "", email: str = "") -> None:
    if len(password) < 8:
        raise error(422, "password", "Password must be at least 8 characters.")
    if len(password) > 128:
        raise error(422, "password", "Password must be at most 128 characters.")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise error(422, "password", "Password must contain both letters and numbers.")
    if password.lower() in {username.lower(), email.lower()}:
        raise error(422, "password", "Password must not be your username or email.")


def ensure_available(db: Session, username: str, email: str | None) -> None:
    """Call while holding ``accounts_lock``; the unique indexes are the final word."""
    if db.scalar(select(User.id).where(func.lower(User.username) == username.lower())):
        raise error(409, "username", "That username is taken.")
    if email and db.scalar(select(User.id).where(User.email == email)):
        raise error(409, "email", "That email is already registered.")


# No look-alike characters (0/O, 1/l/I), so a password read out or copied by hand still works.
_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


def temporary_password(length: int = 12) -> str:
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw):
            return pw
