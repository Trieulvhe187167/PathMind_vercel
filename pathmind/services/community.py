"""Community skill packages: build them from a user's own material, install them.

A community skill is stored in the database (``CommunitySkill``) as the full
``SKILL.md`` text plus a small map of text support files. Packages come from:

* one of the publisher's own skills (``source="skill"``),
* a Co-Writer document (``source="cowriter"``) — its markdown becomes the body,
* pasted markdown (``source="text"``),
* an uploaded ``SKILL.md`` / ``.md`` file or a ``.zip`` skill package.

Installing writes the package to a temp dir and hands it to
:meth:`SkillService.install_tree` — the same import gate the EduHub installer
uses (frontmatter adaptation, ``always:`` stripped, suffix whitelist,
provenance in ``.hub-lock.json`` with ``hub = "community"``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import tempfile
from typing import Any

import yaml

HUB_NAME = "community"

SKILL_CATEGORIES = (
    "academics",
    "languages",
    "skills-interests",
    "companions",
    "educators",
    "productivity",
    "other",
)
FORUM_CATEGORIES = ("general", "help", "skills", "study", "feedback", "announcements")
ADMIN_ONLY_FORUM_CATEGORIES = frozenset({"announcements"})

MAX_SKILL_MD_CHARS = 200_000
MAX_SUPPORT_FILES = 50
MAX_SUPPORT_FILE_CHARS = 200_000
MAX_SUPPORT_TOTAL_CHARS = 2_000_000
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_TAGS = 8

_TAG_CLEAN_RE = re.compile(r"[^a-z0-9\- _]+")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_DOC_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")


class PackageError(ValueError):
    """The submitted material can't become a skill package (message is user-facing)."""


@dataclass(slots=True)
class SkillPackage:
    content: str  # SKILL.md (normalized frontmatter)
    description: str
    tags: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    candidate = re.sub(r"[^a-z0-9-]+", "-", (text or "").strip().lower())
    candidate = re.sub(r"-{2,}", "-", candidate).strip("-")[:60].strip("-")
    return candidate or "skill"


def clean_tags(raw: Any) -> list[str]:
    values = raw if isinstance(raw, list) else str(raw or "").split(",")
    out: list[str] = []
    for value in values:
        tag = _TAG_CLEAN_RE.sub("", str(value or "").strip().lower()).strip()[:32].strip()
        if tag and tag[0].isalnum() and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    return (data if isinstance(data, dict) else {}), (text or "")[match.end() :]


def normalize_skill_md(text: str, *, slug: str, description: str, tags: list[str]) -> str:
    """Frontmatter with our name/description/tags; ``always`` never travels."""
    meta, body = parse_frontmatter(text)
    meta.pop("always", None)
    meta["name"] = slug
    meta["description"] = description
    if tags:
        meta["tags"] = list(tags)
    else:
        meta.pop("tags", None)
    header = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{header}\n---\n\n{body.lstrip()}".rstrip() + "\n"


def _allowed_suffixes() -> frozenset[str]:
    from pathmind.services.skill.service import _IMPORT_ALLOWED_SUFFIXES

    return _IMPORT_ALLOWED_SUFFIXES


def _safe_rel(rel: str) -> str | None:
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    path = Path(rel)
    if not rel or path.is_absolute() or ".." in path.parts:
        return None
    if any(part.startswith(".") or part == "__MACOSX" for part in path.parts):
        return None
    return path.as_posix()


def _collect_files(pairs: list[tuple[str, str]]) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    skipped: list[str] = []
    total = 0
    suffixes = _allowed_suffixes()
    for rel, text in pairs:
        safe = _safe_rel(rel)
        if safe is None or safe == "SKILL.md":
            continue
        if Path(safe).suffix.lower() not in suffixes:
            skipped.append(safe)
            continue
        if len(text) > MAX_SUPPORT_FILE_CHARS or len(files) >= MAX_SUPPORT_FILES:
            skipped.append(safe)
            continue
        if total + len(text) > MAX_SUPPORT_TOTAL_CHARS:
            skipped.append(safe)
            continue
        total += len(text)
        files[safe] = text
    return files, skipped


def build_package(
    skill_md: str,
    *,
    slug: str,
    description: str | None,
    tags: list[str] | None,
    support: list[tuple[str, str]] | None = None,
    skipped: list[str] | None = None,
) -> SkillPackage:
    text = (skill_md or "").replace("\r\n", "\n")
    if not text.strip():
        raise PackageError("empty")
    if len(text) > MAX_SKILL_MD_CHARS:
        raise PackageError("too_large")
    meta, body = parse_frontmatter(text)
    if not body.strip():
        raise PackageError("empty")
    final_description = (description or "").strip() or str(meta.get("description") or "").strip()
    if not final_description:
        raise PackageError("no_description")
    final_tags = clean_tags(tags) if tags else clean_tags(meta.get("tags") or [])
    files, dropped = _collect_files(support or [])
    return SkillPackage(
        content=normalize_skill_md(
            text, slug=slug, description=final_description[:500], tags=final_tags
        ),
        description=final_description[:500],
        tags=final_tags,
        files=files,
        skipped=list(skipped or []) + dropped,
    )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def material_from_user_skill(name: str) -> tuple[str, list[tuple[str, str]]]:
    """(SKILL.md, support files) of one of the current user's own skills."""
    from pathmind.services.skill import get_skill_service
    from pathmind.services.skill.service import SkillNotFoundError

    service = get_skill_service()
    try:
        detail = service.get_detail(name)
    except (SkillNotFoundError, Exception) as exc:
        raise PackageError("skill_not_found") from exc
    if detail.source != "user":
        raise PackageError("skill_not_yours")
    support: list[tuple[str, str]] = []
    try:
        for rel in service.list_skill_files(name):
            rel = rel.replace("\\", "/")
            if rel == "SKILL.md":
                continue
            try:
                support.append((rel, service.read_skill_file(name, rel)))
            except Exception:
                continue
    except Exception:
        pass
    return detail.content, support


def material_from_cowriter(doc_id: str) -> tuple[str, str]:
    """(markdown, title) of one of the current user's Co-Writer documents."""
    if not _DOC_ID_RE.fullmatch(doc_id or ""):
        raise PackageError("doc_not_found")
    from pathmind.co_writer.storage import get_co_writer_storage

    document = get_co_writer_storage().load_document(doc_id)
    if document is None:
        raise PackageError("doc_not_found")
    return document.content or "", document.title or ""


def material_from_upload(
    filename: str, data: bytes
) -> tuple[str, list[tuple[str, str]], list[str]]:
    """A ``.md`` file is the SKILL.md; a ``.zip`` is a skill package.

    Returns ``(SKILL.md, text support files, skipped binary files)``.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise PackageError("too_large")
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        try:
            return data.decode("utf-8-sig"), [], []
        except UnicodeDecodeError as exc:
            raise PackageError("not_utf8") from exc
    if suffix != ".zip":
        raise PackageError("bad_file_type")
    from pathmind.services.skill.hub import _extract_skill_zip, _locate_package_root
    from pathmind.services.skill.service import SkillImportError

    with tempfile.TemporaryDirectory(prefix="community-upload-") as tmp:
        zip_path = Path(tmp) / "package.zip"
        zip_path.write_bytes(data)
        try:
            _extract_skill_zip(zip_path, Path(tmp) / "x")
            root = _locate_package_root(Path(tmp) / "x")
        except SkillImportError as exc:
            raise PackageError("bad_zip") from exc
        try:
            skill_md = (root / "SKILL.md").read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise PackageError("not_utf8") from exc
        support: list[tuple[str, str]] = []
        binary: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            if rel == "SKILL.md":
                continue
            try:
                support.append((rel, path.read_text(encoding="utf-8")))
            except (UnicodeDecodeError, OSError):
                binary.append(rel)
        return skill_md, support, binary


# ---------------------------------------------------------------------------
# Install into the current user's skills
# ---------------------------------------------------------------------------


def installed_community_slugs() -> dict[str, str]:
    """{community slug: local skill name} for the current user."""
    from pathmind.services.skill import get_skill_service

    try:
        lock = get_skill_service()._read_hub_lock()
    except Exception:
        return {}
    return {
        str(origin.get("slug")): name
        for name, origin in lock.items()
        if origin.get("hub") == HUB_NAME and origin.get("slug")
    }


def install_package(
    *,
    slug: str,
    version: int,
    content: str,
    files: dict[str, str] | None,
    rename_to: str | None = None,
    force: bool = False,
):
    """Install into the caller's own skill layer; returns ``SkillInstallResult``."""
    from pathmind.services.skill import get_skill_service

    service = get_skill_service()
    with tempfile.TemporaryDirectory(prefix="community-install-") as tmp:
        root = Path(tmp) / slug
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(content, encoding="utf-8")
        for rel, text in (files or {}).items():
            safe = _safe_rel(rel)
            if safe is None or safe == "SKILL.md":
                continue
            target = root / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return service.install_tree(
            root,
            rename_to=rename_to or slug,
            origin={
                "hub": HUB_NAME,
                "slug": slug,
                "version": str(version),
                "verdict": "community",
                "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            force=force,
            extra_tags=[HUB_NAME],
        )


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def hot_rank(score: int, created_at: datetime | None, comments: int = 0, now: datetime | None = None) -> float:
    """Reddit-like decay: newer + more upvoted/commented floats up."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    age_hours = max(0.0, ((now - created_at).total_seconds() / 3600) if created_at else 0.0)
    points = max(score, 0) + 0.5 * max(comments, 0) + 1
    return math.log10(points) * 10 / math.pow(age_hours + 2, 0.8) + 1 / (age_hours + 2)


__all__ = [
    "ADMIN_ONLY_FORUM_CATEGORIES",
    "FORUM_CATEGORIES",
    "HUB_NAME",
    "PackageError",
    "SKILL_CATEGORIES",
    "SkillPackage",
    "build_package",
    "clean_tags",
    "hot_rank",
    "install_package",
    "installed_community_slugs",
    "material_from_cowriter",
    "material_from_upload",
    "material_from_user_skill",
    "slugify",
]
