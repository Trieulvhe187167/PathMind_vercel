"""Administrator-editable settings (login lockout, SMTP). DB value wins over the environment."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from pathmind.services import audit

# key -> (env var, default, type, secret, label)
SCHEMA: dict[str, dict[str, Any]] = {
    "auth.login_max_failures": {"env": "LOGIN_MAX_FAILURES", "default": "5", "type": "int", "min": 1, "max": 100},
    "auth.login_lock_minutes": {"env": "LOGIN_LOCK_MINUTES", "default": "15", "type": "int", "min": 1, "max": 1440},
    "smtp.host": {"env": "SMTP_HOST", "default": "", "type": "str"},
    "smtp.port": {"env": "SMTP_PORT", "default": "587", "type": "int", "min": 1, "max": 65535},
    "smtp.username": {"env": "SMTP_USERNAME", "default": "", "type": "str"},
    "smtp.password": {"env": "SMTP_PASSWORD", "default": "", "type": "str", "secret": True},
    "smtp.from": {"env": "SMTP_FROM", "default": "", "type": "str"},
    "smtp.tls": {"env": "SMTP_TLS", "default": "starttls", "type": "choice", "choices": ["starttls", "ssl", "none"]},
}


class SettingError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _stored(key: str) -> str | None:
    """Raw DB value, or None (also None if the DB is unavailable)."""
    try:
        from pathmind.database.connection import get_db_session
        from pathmind.database.models import SystemSetting

        with get_db_session() as db:
            row = db.get(SystemSetting, key)
            return row.value if row is not None else None
    except Exception:  # noqa: BLE001 - settings must never break login or mail
        return None


def get(key: str) -> str:
    spec = SCHEMA[key]
    value = _stored(key)
    if value is not None:
        return value
    return os.environ.get(spec["env"], "").strip() or spec["default"]


def get_int(key: str) -> int:
    spec = SCHEMA[key]
    try:
        return int(get(key))
    except ValueError:
        return int(spec["default"])


def view(db: Session) -> dict[str, dict[str, Any]]:
    """Every setting with its effective value; secrets are never returned."""
    from pathmind.database.models import SystemSetting

    stored = {r.key: r for r in db.query(SystemSetting).all()}
    out: dict[str, dict[str, Any]] = {}
    for key, spec in SCHEMA.items():
        row = stored.get(key)
        effective = row.value if row is not None else (os.environ.get(spec["env"], "").strip() or spec["default"])
        item: dict[str, Any] = {"type": spec["type"], "source": "admin" if row is not None else "default"}
        if spec.get("secret"):
            item["value"] = ""
            item["is_set"] = bool(effective)
        else:
            item["value"] = effective
        for extra in ("min", "max", "choices"):
            if extra in spec:
                item[extra] = spec[extra]
        out[key] = item
    return out


def update(db: Session, actor_id: str, values: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from pathmind.database.models import SystemSetting

    changed: list[str] = []
    for key, raw in values.items():
        spec = SCHEMA.get(key)
        if spec is None:
            raise SettingError(400, f"Unknown setting: {key}")
        text = str(raw if raw is not None else "").strip()
        if spec.get("secret") and text == "":
            continue  # blank = keep the current secret
        if spec["type"] == "int":
            try:
                n = int(text)
            except ValueError:
                raise SettingError(400, f"{key} must be a whole number.") from None
            if not spec["min"] <= n <= spec["max"]:
                raise SettingError(400, f"{key} must be between {spec['min']} and {spec['max']}.")
            text = str(n)
        elif spec["type"] == "choice" and text not in spec["choices"]:
            raise SettingError(400, f"{key} must be one of: {', '.join(spec['choices'])}.")
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key)
            db.add(row)
        row.value, row.updated_by, row.updated_at = text, actor_id, datetime.utcnow()
        changed.append(key)
    audit.record(db, actor_id, "settings.update", target_type="settings", data={"keys": changed}, commit=False)
    db.commit()
    return view(db)
