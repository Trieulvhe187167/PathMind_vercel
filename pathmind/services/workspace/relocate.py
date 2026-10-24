"""Re-anchor stored workspace paths after the installation folder moves.

Workspace rows, the managed-root setting and legacy bindings store absolute
paths (``C:\\OldName\\data\\users\\...``). Renaming or moving the project folder,
or restoring ``data/`` on another server, leaves those paths pointing nowhere
and every chat fails with "The selected workspace folder does not exist".

``relocate_stale_path`` maps such a path back into the current data root when
(and only when) the stored path no longer exists and the same location exists
under the current ``data/`` directory.
"""

from __future__ import annotations

from pathlib import Path
import re


def _data_root() -> Path:
    from pathmind.multi_user.paths import ADMIN_WORKSPACE_ROOT

    return ADMIN_WORKSPACE_ROOT


def relocate_stale_path(raw: str | Path | None, *, data_root: Path | None = None) -> str | None:
    """Return the current location of a moved data path, or ``None``."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if Path(text).expanduser().exists():
            return None
    except OSError:
        pass
    root = (data_root or _data_root()).resolve()
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    if any(part in {".", ".."} for part in parts):
        return None
    name = root.name.lower()
    for index in range(len(parts) - 1, -1, -1):
        if parts[index].lower() != name:
            continue
        candidate = root.joinpath(*parts[index + 1 :])
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.exists():
            return str(resolved)
    return None


__all__ = ["relocate_stale_path"]
