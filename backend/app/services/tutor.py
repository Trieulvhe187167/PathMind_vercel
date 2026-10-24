"""The AI tutor's answer pipeline: context, grounding in the company tree, policies, citations.

prepare()  -> decide what the model sees (sources, memories, persona, attachments) and whether a
              strict domain forbids answering without verified knowledge;
finish()   -> keep only the sources the answer actually cites, reward contributors, log gaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ChatMessage, ChatMessageCitation, ChatSession, ChatSessionKnowledgeBase, Contribution, KnowledgeBase,
    KnowledgeBaseAccess, KnowledgeDomain, Memory, Partner, Persona, RewardLedger, StoredFile, User, VerifiedDocument,
    utcnow,
)
from . import extract, retrieval, storage
from .app_settings import get_setting
from .knowledge_tree import award, record_gap

LANG = {"vi": "Vietnamese", "en": "English", "zh": "Simplified Chinese"}
MODES = {
    "chat": "Explain clearly and check understanding with a short question when useful.",
    "solve": ("Solve the problem step by step. Number the steps, show the reasoning for each, and end with a short "
              "summary of the method so the learner can reuse it."),
    "quiz": ("Write a short quiz (3-5 questions) on what the learner asks about. For each question give options, "
             "then list the answers with one-line explanations at the end."),
    "deep_research": ("Give a thorough, well-structured answer: an overview, the key points with evidence from the "
                      "sources, open questions or disagreements, and a conclusion."),
    "tutor": "Teach Socratically: ask guiding questions, give hints before answers, and adapt to the learner's replies.",
}
NOT_VERIFIED = {"vi": "Chưa kiểm duyệt", "en": "Not verified", "zh": "未经核实"}
STRICT_NO_ANSWER = {
    "vi": "Mình chưa có tài liệu đã được R&D kiểm duyệt để trả lời câu hỏi này, nên không thể trả lời chắc chắn. "
          "Câu hỏi đã được ghi lại để bộ phận R&D bổ sung.",
    "en": "I don't have documents verified by R&D that answer this question, so I can't answer it reliably. "
          "The question has been logged so R&D can add the missing knowledge.",
    "zh": "目前还没有经研发部门审核的资料可以回答这个问题，因此无法给出可靠的答案。该问题已被记录，研发部门会补充相关知识。",
}


@dataclass
class Source:
    n: int
    kind: str  # tree | kb
    text: str
    title: str
    section: str = ""
    page: int | None = None
    chunk_id: str | None = None
    node_id: str | None = None
    document_id: str | None = None
    kb_document_id: str | None = None
    score: float = 0.0
    contribution_id: str | None = None


@dataclass
class Prepared:
    messages: list[dict]
    sources: list[Source]
    grounding: str  # verified | mixed | personal_kb | general | none
    fixed_answer: str | None = None  # strict domain without knowledge: answer without calling the model
    domain_id: str | None = None
    gap_id: str | None = None
    model: str | None = None
    debug: dict = field(default_factory=dict)


def accessible_kbs(db: Session, user: User, kb_ids: list[str]) -> list[KnowledgeBase]:
    out = []
    for kb_id in dict.fromkeys(kb_ids):
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            continue
        if kb.owner_id == user.id or kb.visibility == "public" or db.get(KnowledgeBaseAccess, (kb.id, user.id)):
            out.append(kb)
    return out


def attachment_text(db: Session, data_dir: Path, user: User, file_ids: list[str], limit: int = 20000) -> list[tuple[str, str]]:
    out = []
    for fid in file_ids[:5]:
        f = db.get(StoredFile, fid)
        if f is None or f.owner_id != user.id or f.deleted_at is not None:
            continue
        try:
            ex = extract.extract(f.filename, storage.read_bytes(db, data_dir, f), f.mime_type)
            out.append((f.filename, ex.text[:limit]))
        except (extract.ExtractError, storage.StorageError):
            out.append((f.filename, "(This file type cannot be read as text.)"))
    return out


def memories_for(db: Session, user: User, limit: int = 8) -> list[str]:
    if not get_setting(db, "ai").get("memory_enabled", True):
        return []
    rows = db.scalars(select(Memory).where(Memory.user_id == user.id, Memory.deleted_at.is_(None))
                      .order_by(Memory.importance.desc(), Memory.updated_at.desc()).limit(limit)).all()
    return [f"- ({m.kind}) {m.content}" for m in rows]


def prepare(db: Session, data_dir: Path, user: User, *, question: str, history: list[dict], mode: str = "chat",
            session: ChatSession | None = None, kb_ids: list[str] | None = None, attachments: list[str] | None = None,
            use_tree: bool = True, record_gaps: bool = True) -> Prepared:
    ai = get_setting(db, "ai")
    k = int(ai.get("max_context_chunks") or 6)
    threshold = float(ai.get("min_retrieval_score") or 0.35)
    lang = LANG.get(user.locale, "Vietnamese")
    sources: list[Source] = []
    debug: dict = {"threshold": threshold}

    # 1. Company tree (only verified, active passages).
    tree = retrieval.search_tree(db, data_dir, question, user=user, limit=k) if use_tree else None
    domain: KnowledgeDomain | None = tree.domain if tree else None
    good = [h for h in (tree.hits if tree else []) if retrieval.relevance(h) >= threshold * 0.6]
    tree_ok = bool(tree and tree.best >= threshold and good)
    debug["tree"] = [{"title": h.doc.meta["document"].title, "section": h.doc.meta["chunk"].heading_path,
                      "score": round(h.score, 3), "coverage": round(h.coverage, 3), "cosine": round(h.cosine, 3),
                      "domain": h.doc.meta["domain"].name} for h in (tree.hits if tree else [])]
    debug["best"] = round(tree.best, 3) if tree else 0
    debug["domain"] = domain.name if domain else None
    if tree_ok:
        for h in good:
            c, d = h.doc.meta["chunk"], h.doc.meta["document"]
            sources.append(Source(len(sources) + 1, "tree", c.text, d.title, c.heading_path, c.page_from, chunk_id=c.id,
                                  node_id=c.node_id, document_id=d.id, score=h.score, contribution_id=d.contribution_id))

    # 2. Personal knowledge bases attached to the conversation.
    ids = list(kb_ids or [])
    if session is not None:
        ids += list(db.scalars(select(ChatSessionKnowledgeBase.knowledge_base_id).where(ChatSessionKnowledgeBase.session_id == session.id)))
    kbs = accessible_kbs(db, user, ids)
    if kbs:
        for h in retrieval.search_kbs(db, data_dir, question, kbs, user=user, limit=4):
            item = h.doc.meta["item"]
            sources.append(Source(len(sources) + 1, "kb", item["text"], item.get("title") or h.doc.meta["kb"].name,
                                  item.get("heading", ""), item.get("page"), kb_document_id=item["doc_id"], score=h.score))

    # 3. Policy: a strict domain answers only from verified knowledge.
    gap_id = None
    # Only questions that belong to a company domain count as gaps (not "write me a poem").
    if use_tree and not tree_ok and record_gaps and len(question.strip()) >= 8 and domain is not None:
        gap = record_gap(db, question.strip(), domain.id if domain else None)
        gap_id = gap.id
    debug["policy"] = domain.answer_policy if domain else None
    if domain is not None and domain.answer_policy == "strict" and not tree_ok:
        return Prepared([], sources, "none", fixed_answer=STRICT_NO_ANSWER.get(user.locale, STRICT_NO_ANSWER["en"]),
                        domain_id=domain.id, gap_id=gap_id, debug=debug)

    # 4. The prompt.
    system = [f"You are PathMind, a patient AI tutor. Reply in {lang} unless the learner writes in another language.",
              MODES.get(mode, MODES["chat"])]
    persona_prompt, partner_model = _persona(db, session)
    if persona_prompt:
        system.append(persona_prompt)
    if session is not None:  # e.g. the lesson a mastery-path conversation is about
        for note in db.scalars(select(ChatMessage.content).where(ChatMessage.session_id == session.id,
                                                                 ChatMessage.role == "system").order_by(ChatMessage.seq)):
            system.append(note)
    mems = memories_for(db, user)
    if mems:
        system.append("What you remember about this learner (use it to personalise, never recite it):\n" + "\n".join(mems))
    files = attachment_text(db, data_dir, user, attachments or [])
    if files:
        system.append("Files the learner attached:\n" + "\n\n".join(f"=== {name} ===\n{text}" for name, text in files))
    if sources:
        lines = []
        for s in sources:
            label = "Company knowledge, verified by R&D" if s.kind == "tree" else "Learner's own knowledge base"
            where = f" > {s.section}" if s.section else ""
            lines.append(f"[{s.n}] ({label}) {s.title}{where}\n{s.text}")
        system.append(
            "Sources:\n" + "\n\n".join(lines) + "\n\nAnswer from these sources and cite them inline like [1] or [2][3] "
            "right after the sentence they support. Prefer the verified company sources when sources disagree. "
            "Never cite a number that is not in the list.")
    marker = NOT_VERIFIED.get(user.locale, NOT_VERIFIED["en"])
    if not tree_ok:
        if domain is not None or _has_tree(db):
            system.append(f"No verified company source covers this question. You may answer from general knowledge, but "
                          f"begin that part with '{marker}:' so the learner knows it was not checked by R&D.")
    grounding = ("verified" if tree_ok and not any(s.kind == "kb" for s in sources) else
                 "mixed" if tree_ok else "personal_kb" if sources else "general")
    msgs = [{"role": "system", "content": "\n\n".join(system)}]
    msgs += history[-12:]
    msgs.append({"role": "user", "content": question})
    return Prepared(msgs, sources, grounding, domain_id=domain.id if domain else None, gap_id=gap_id,
                    model=partner_model, debug=debug)


def _has_tree(db: Session) -> bool:
    return db.scalar(select(KnowledgeDomain.id).where(KnowledgeDomain.status == "active").limit(1)) is not None


def _persona(db: Session, session: ChatSession | None) -> tuple[str, str | None]:
    if session is None:
        return "", None
    parts, model = [], None
    if session.partner_id:
        p = db.get(Partner, session.partner_id)
        if p is not None:
            if p.persona_id:
                per = db.get(Persona, p.persona_id)
                if per is not None and per.system_prompt:
                    parts.append(per.system_prompt)
            if p.system_prompt:
                parts.append(f"You are {p.name}. {p.system_prompt}")
            model = p.model or None
    elif session.persona_id:
        per = db.get(Persona, session.persona_id)
        if per is not None and per.system_prompt:
            parts.append(per.system_prompt)
    return "\n\n".join(parts), model


CITE = re.compile(r"\[(\d{1,2})\]")


def finish(db: Session, user: User, message: ChatMessage, prepared: Prepared) -> list[ChatMessageCitation]:
    """Store the sources the answer cites; reward the educators whose documents were cited."""
    cited = []
    for m in CITE.finditer(message.content or ""):
        n = int(m.group(1))
        if n not in cited:
            cited.append(n)
    by_n = {s.n: s for s in prepared.sources}
    rows = []
    for seq, n in enumerate([n for n in cited if n in by_n], start=1):
        s = by_n[n]
        row = ChatMessageCitation(message_id=message.id, seq=n, chunk_id=s.chunk_id, node_id=s.node_id,
                                  document_id=s.document_id, kb_document_id=s.kb_document_id, quote=s.text[:1500],
                                  page=s.page, score=s.score)
        db.add(row)
        rows.append(row)
        if s.contribution_id:
            _reward_cited(db, s.contribution_id)
    message.grounding = prepared.grounding
    return rows


def _reward_cited(db: Session, contribution_id: str) -> None:
    rw = get_setting(db, "rewards")
    pts, cap = int(rw.get("points_cited") or 0), int(rw.get("cited_daily_cap") or 0)
    if pts <= 0:
        return
    c = db.get(Contribution, contribution_id)
    if c is None:
        return
    since = utcnow() - timedelta(days=1)
    today = db.scalar(select(func.coalesce(func.sum(RewardLedger.points), 0)).where(
        RewardLedger.user_id == c.contributor_id, RewardLedger.reason == "cited", RewardLedger.created_at >= since)) or 0
    if cap and today >= cap:
        return
    award(db, c.contributor_id, pts, "cited", contribution_id=c.id)


def citation_view(db: Session, c: ChatMessageCitation, viewer: User) -> dict:
    """What a person may see of a source. R&D and admins see everything; others what the domain allows."""
    base = {"n": c.seq, "page": c.page}
    if c.kb_document_id:
        from ..models import KnowledgeBaseDocument

        d = db.get(KnowledgeBaseDocument, c.kb_document_id)
        return {**base, "kind": "kb", "title": d.title if d else "Knowledge base", "quote": c.quote[:600],
                "file_id": d.file_id if d else None, "url": d.url if d else ""}
    doc = db.get(VerifiedDocument, c.document_id) if c.document_id else None
    domain = db.get(KnowledgeDomain, doc.domain_id) if doc else None
    section = ""
    if c.chunk_id:
        from ..models import KnowledgeChunk

        ch = db.get(KnowledgeChunk, c.chunk_id)
        section = ch.heading_path if ch else ""
    contributor = None
    if doc and doc.contribution_id:
        con = db.get(Contribution, doc.contribution_id)
        u = db.get(User, con.contributor_id) if con else None
        contributor = (u.full_name or u.username) if u else None
    out = {**base, "kind": "tree", "verified": True, "contributor": contributor}
    if viewer.role in ("rnd", "admin"):
        return {**out, "title": doc.title if doc else "", "section": section, "quote": c.quote[:1500],
                "document_id": c.document_id, "node_id": c.node_id, "domain": domain.name if domain else None}
    detail = domain.citation_detail if domain else "title"
    if detail == "none":
        return {**out, "title": None, "section": None, "page": None, "contributor": None}
    out.update({"title": doc.title if doc else "", "section": section})
    if detail == "quote":
        out["quote"] = c.quote[:600]
    return out


# ── Memory ─────────────────────────────────────────────────────────────────────


def extract_memories(db: Session, user: User, session: ChatSession, recent: list[dict]) -> int:
    """Every few turns, ask the model what is worth remembering about the learner."""
    from . import llm

    if not get_setting(db, "ai").get("memory_enabled", True):
        return 0
    convo = "\n".join(f"{m['role']}: {m['content'][:800]}" for m in recent[-8:])
    existing = memories_for(db, user, 30)
    prompt = ("From this tutoring conversation, list durable facts about the LEARNER worth remembering for future "
              "lessons: goals, preferences, strengths, difficulties. Skip anything already known or trivial. "
              "Respond with JSON: {\"memories\": [{\"kind\": \"goal|preference|fact|difficulty\", \"content\": \"...\", "
              "\"importance\": 1-5}]} (empty list if nothing new).\n\nAlready known:\n" + "\n".join(existing) +
              "\n\nConversation:\n" + convo)
    try:
        data = llm.complete_json(db, user=user, messages=[{"role": "user", "content": prompt}], purpose="memory",
                                 fallback={}, max_tokens=500)
    except Exception:  # noqa: BLE001 - memory is a nice-to-have
        return 0
    n = 0
    for m in (data or {}).get("memories") or []:
        if not isinstance(m, dict) or not str(m.get("content") or "").strip():
            continue
        kind = m.get("kind") if m.get("kind") in ("goal", "preference", "fact", "difficulty") else "fact"
        try:
            imp = max(1, min(5, int(m.get("importance") or 2)))
        except (TypeError, ValueError):
            imp = 2
        db.add(Memory(user_id=user.id, kind=kind, content=str(m["content"]).strip()[:1000], importance=imp,
                      source_session_id=session.id))
        n += 1
    return n
