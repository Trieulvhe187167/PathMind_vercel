"""Opt-in, anonymised memory aggregates for the server.

What leaves an account, and only after the person turned sharing on:

* ``topic``       subject tags from a fixed vocabulary (``math``, ``programming``…)
                  matched against the profile / scope / recent memory documents;
* ``preference``  learning-style tags from a fixed vocabulary (``examples_first``…)
                  matched against the preferences document;
* ``activity``    how many memory events each surface logged this month.

Never raw text, never free-form labels: every label is checked against the
vocabulary below before it is stored. Rows are keyed by a random ``pseudo_id``
that lives only as long as the consent; withdrawing consent or deleting the
account removes the consent row and every insight, so nothing can be traced
back afterwards. Administrators only ever see groups of at least ``MIN_GROUP``
people (k-anonymity).
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import uuid

logger = logging.getLogger(__name__)

MIN_GROUP = 5
STALE_AFTER_DAYS = 7

# label -> lowercase keywords (English / Vietnamese / Chinese). Short on purpose:
# a tag only says "this person studies X", nothing more.
TOPICS: dict[str, tuple[str, ...]] = {
    "math": ("math", "calculus", "algebra", "geometry", "statistics", "toán", "代数", "数学", "微积分"),
    "physics": ("physics", "mechanics", "quantum", "vật lý", "物理"),
    "chemistry": ("chemistry", "organic", "hóa học", "hoá học", "化学"),
    "biology": ("biology", "genetics", "sinh học", "生物"),
    "programming": ("programming", "python", "javascript", "java ", "c++", "coding", "lập trình", "编程", "算法", "algorithm"),
    "data_science": ("machine learning", "data science", "deep learning", "học máy", "机器学习", "数据"),
    "english": ("english", "ielts", "toefl", "tiếng anh", "英语", "英文"),
    "chinese": ("chinese", "hsk", "mandarin", "tiếng trung", "汉语", "中文"),
    "japanese": ("japanese", "jlpt", "tiếng nhật", "日语", "日本語"),
    "economics": ("economics", "finance", "accounting", "kinh tế", "tài chính", "经济", "金融"),
    "history": ("history", "lịch sử", "历史"),
    "literature": ("literature", "writing", "essay", "văn học", "文学", "写作"),
    "law": ("law ", "legal", "pháp luật", "luật", "法律"),
    "medicine": ("medicine", "anatomy", "medical", "y học", "医学"),
    "engineering": ("engineering", "circuit", "electronics", "kỹ thuật", "工程", "电路"),
    "business": ("marketing", "management", "startup", "kinh doanh", "管理", "营销"),
    "design": ("design", "ui/ux", "thiết kế", "设计"),
    "exam_prep": ("exam", "exams", "test prep", "thi ", "ôn thi", "考试", "备考"),
}

PREFERENCES: dict[str, tuple[str, ...]] = {
    "examples_first": ("example", "examples", "ví dụ", "举例", "例子"),
    "concise": ("concise", "brief", "short answer", "short answers", "ngắn gọn", "简洁", "简短"),
    "detailed": ("detailed", "in depth", "step by step", "chi tiết", "từng bước", "详细", "一步一步"),
    "visual": ("diagram", "diagrams", "visual", "chart", "sơ đồ", "hình ảnh", "图表", "图示"),
    "quizzes": ("quiz", "quizzes", "practice question", "flashcard", "câu hỏi", "trắc nghiệm", "测验", "练习题"),
    "socratic": ("socratic", "guide me", "hint", "gợi ý", "提示", "引导"),
    "formal_tone": ("formal", "academic", "trang trọng", "正式", "学术"),
    "casual_tone": ("casual", "friendly", "thân thiện", "轻松", "友好"),
}

_KINDS = {"topic": TOPICS, "preference": PREFERENCES}
_ACTIVITY_LABELS = {"chat", "notebook", "quiz", "kb", "book", "partner", "cowriter"}
_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def current_period(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def valid_label(kind: str, label: str) -> bool:
    if kind == "activity":
        return label in _ACTIVITY_LABELS
    return label in _KINDS.get(kind, {})


def _hit(haystack: str, word: str) -> bool:
    word = word.strip()
    if word.isascii():
        # whole words only: "exam" must not fire on "examples"
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", haystack) is not None
    return word in haystack


def match_tags(text: str, vocabulary: dict[str, tuple[str, ...]]) -> list[str]:
    haystack = text.lower()
    return sorted(
        label for label, words in vocabulary.items() if any(_hit(haystack, w) for w in words)
    )


def _activity_counts(period: str) -> dict[str, int]:
    """L1 events logged this month, per surface (counts only)."""
    from pathmind.services.memory import paths

    counts: dict[str, int] = {}
    for surface in paths.SURFACES:
        try:
            folder = paths.trace_dir(surface)
            if not folder.exists():
                continue
            total = 0
            for f in folder.glob(f"{period}-*.jsonl"):
                with f.open("rb") as fh:
                    total += sum(1 for line in fh if line.strip())
            if total:
                counts[surface] = total
        except Exception:
            logger.warning("insight activity count failed for %s", surface, exc_info=True)
    return counts


def build_preview(period: str | None = None) -> dict:
    """Exactly what would be shared for the signed-in account right now."""
    from pathmind.services.memory import paths
    from pathmind.services.memory.store import get_memory_store

    period = period or current_period()
    store = get_memory_store()

    def raw(slot: str) -> str:
        try:
            return store.read_raw("L3", slot)
        except Exception:
            return ""

    about = "\n".join(raw(s) for s in ("profile", "scope", "recent"))
    items: list[dict] = []
    for label in match_tags(about, TOPICS):
        items.append({"kind": "topic", "label": label, "count": 1})
    for label in match_tags(raw("preferences"), PREFERENCES):
        items.append({"kind": "preference", "label": label, "count": 1})
    for surface, n in sorted(_activity_counts(period).items()):
        if surface in paths.SURFACES:
            items.append({"kind": "activity", "label": surface, "count": n})
    return {"period": period, "items": [i for i in items if valid_label(i["kind"], i["label"])]}


# ── consent + storage ────────────────────────────────────────────────────


def status(db, user_id: str) -> dict:
    row = db.get(_Consent(), user_id)
    return {
        "enabled": row is not None,
        "last_shared_at": row.last_shared_at.isoformat() if row and row.last_shared_at else None,
        "stale": bool(row) and _is_stale(row.last_shared_at),
        "min_group": MIN_GROUP,
    }


def _is_stale(ts: datetime | None) -> bool:
    return ts is None or (datetime.utcnow() - ts).days >= STALE_AFTER_DAYS


def _Consent():
    from pathmind.database.models import MemorySharingConsent

    return MemorySharingConsent


def _Insight():
    from pathmind.database.models import MemoryInsight

    return MemoryInsight


def enable(db, user_id: str) -> None:
    Consent = _Consent()
    if db.get(Consent, user_id) is None:
        db.add(Consent(user_id=user_id, pseudo_id=uuid.uuid4().hex))
        db.commit()


def delete_shared_data(db, user_id: str) -> int:
    row = db.get(_Consent(), user_id)
    if row is None:
        return 0
    n = db.query(_Insight()).filter(_Insight().pseudo_id == row.pseudo_id).delete(
        synchronize_session=False
    )
    row.last_shared_at = None
    db.commit()
    return int(n)


def withdraw(db, user_id: str) -> int:
    """Switch sharing off and erase everything shared so far."""
    row = db.get(_Consent(), user_id)
    if row is None:
        return 0
    n = db.query(_Insight()).filter(_Insight().pseudo_id == row.pseudo_id).delete(
        synchronize_session=False
    )
    db.delete(row)
    db.commit()
    return int(n)


def share_now(db, user_id: str) -> dict:
    """Store this month's preview for an opted-in account (replaces earlier rows for the month)."""
    Insight = _Insight()
    row = db.get(_Consent(), user_id)
    if row is None:
        raise PermissionError("sharing is off")
    preview = build_preview()
    period = preview["period"]
    db.query(Insight).filter(Insight.pseudo_id == row.pseudo_id, Insight.period == period).delete(
        synchronize_session=False
    )
    for it in preview["items"]:
        db.add(
            Insight(
                pseudo_id=row.pseudo_id,
                kind=it["kind"],
                label=it["label"],
                period=period,
                count=int(it["count"]),
            )
        )
    row.last_shared_at = datetime.utcnow()
    db.commit()
    return {"period": period, "shared": len(preview["items"])}


def aggregate(db, period: str | None = None, min_group: int = MIN_GROUP) -> dict:
    """What administrators may see: tags shared by at least ``min_group`` people."""
    from sqlalchemy import func

    Insight = _Insight()
    period = period or current_period()
    if not _PERIOD_RE.match(period):
        raise ValueError("bad period")
    rows = (
        db.query(
            Insight.kind,
            Insight.label,
            func.count(func.distinct(Insight.pseudo_id)),
            func.sum(Insight.count),
        )
        .filter(Insight.period == period)
        .group_by(Insight.kind, Insight.label)
        .all()
    )
    groups = [
        {"kind": k, "label": label, "people": int(people), "total": int(total or 0)}
        for k, label, people, total in rows
        if int(people) >= min_group
    ]
    groups.sort(key=lambda g: (-g["people"], g["kind"], g["label"]))
    consenting = db.query(func.count(_Consent().user_id)).scalar() or 0
    return {
        "period": period,
        "min_group": min_group,
        "consenting_accounts": int(consenting) if int(consenting) >= min_group else None,
        "groups": groups,
    }
