"""Practice (question bank + spaced repetition), mastery paths, AI-written books and reading."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import (
    BackgroundTask, Book, BookChapter, ChatMessage, ChatSession, LearningEvidence, LearningPath, LearningSession,
    LearningTopic, LearningTopicSource, PathNode, PracticeAttempt, PracticeImport, Question, ReadingAnnotation,
    ReadingDocument, ReviewState, StoredFile, User, utcnow,
)
from ..services import billing, extract, llm, practice, retrieval, storage, tasks, tutor
from .files import read_upload
from .learning import check_course, iso, owned

router = APIRouter()


# ── Practice ─────────────────────────────────────────────────────────────────


def _qb(db: Session, user: User) -> None:
    billing.require_feature(db, user, "question_bank")


@router.get("/practice/questions")
def list_questions(topic: str = "", course_id: str = "", q: str = "", due: bool = False, limit: int = 200,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    _qb(db, user)
    stmt = select(Question).where(Question.owner_id == user.id)
    if topic:
        stmt = stmt.where(Question.topic == topic)
    if course_id:
        stmt = stmt.where(Question.course_id == course_id)
    if q.strip():
        stmt = stmt.where(func.lower(Question.prompt).like(f"%{q.strip().lower()}%"))
    if due:
        stmt = stmt.join(ReviewState, (ReviewState.question_id == Question.id) & (ReviewState.user_id == user.id)).where(
            ReviewState.due_at <= utcnow())
    rows = db.scalars(stmt.order_by(Question.created_at.desc()).limit(max(1, min(limit, 1000)))).all()
    topics = [t for t, in db.execute(select(Question.topic).where(Question.owner_id == user.id, Question.topic != "")
                                     .group_by(Question.topic).order_by(Question.topic)).all()]
    return {"questions": [practice.question_public(x) for x in rows], "topics": topics}


class QuestionBody(BaseModel):
    type: str = "mcq"
    prompt: str = Field(min_length=1, max_length=5000)
    options: list[str] = []
    answer: dict | int | str | list | None = None
    explanation: str = Field(default="", max_length=5000)
    topic: str = Field(default="", max_length=200)
    difficulty: int = Field(default=2, ge=1, le=5)
    course_id: str | None = None
    tags: list[str] = []


def _store(db: Session, user: User, item: dict, *, course_id: str | None = None, source: dict | None = None,
           tags: list[str] | None = None) -> Question:
    q = Question(owner_id=user.id, course_id=course_id, topic=item.get("topic", ""), type=item["type"], prompt=item["prompt"],
                 options=item["options"], answer=item["answer"], explanation=item["explanation"],
                 difficulty=item["difficulty"], tags=(tags or [])[:20], source=source or {})
    db.add(q)
    return q


@router.post("/practice/questions", status_code=201)
def create_question(body: QuestionBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _qb(db, user)
    check_course(db, user, body.course_id)
    item = practice.clean(body.model_dump())
    if item is None:
        raise HTTPException(status_code=422, detail="Check the question: type, options and the correct answer.")
    q = _store(db, user, item, course_id=body.course_id, tags=body.tags)
    db.commit()
    return {"question": practice.question_public(q)}


@router.put("/practice/questions/{qid}")
def update_question(qid: str, body: QuestionBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = owned(db, Question, qid, user)
    item = practice.clean(body.model_dump())
    if item is None:
        raise HTTPException(status_code=422, detail="Check the question: type, options and the correct answer.")
    for k in ("type", "prompt", "options", "answer", "explanation", "difficulty", "topic"):
        setattr(q, k, item[k])
    q.tags, q.course_id = body.tags[:20], body.course_id
    db.commit()
    return {"question": practice.question_public(q)}


@router.delete("/practice/questions/{qid}")
def delete_question(qid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(owned(db, Question, qid, user))
    db.commit()
    return {"ok": True}


class GenerateBody(BaseModel):
    topic: str = Field(min_length=2, max_length=500)
    count: int = Field(default=5, ge=1, le=20)
    type: str = "mixed"
    difficulty: int = Field(default=2, ge=1, le=5)
    course_id: str | None = None
    kb_id: str | None = None
    message_id: str | None = None  # make questions from a chat answer


@router.post("/practice/generate", status_code=201)
def generate(body: GenerateBody, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _qb(db, user)
    check_course(db, user, body.course_id)
    material, source = "", {}
    if body.message_id:
        m = db.get(ChatMessage, body.message_id)
        s = db.get(ChatSession, m.session_id) if m else None
        if m is None or s is None or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="Message not found.")
        material, source = m.content, {"chat_message_id": m.id}
    elif body.kb_id:
        kbs = tutor.accessible_kbs(db, user, [body.kb_id])
        if not kbs:
            raise HTTPException(status_code=404, detail="Knowledge base not found.")
        hits = retrieval.search_kbs(db, request.app.state.settings.data_dir, body.topic, kbs, user=user, limit=8)
        material, source = "\n\n".join(h.doc.text for h in hits), {"kb_id": body.kb_id}
    items = practice.generate(db, user, topic=body.topic, count=body.count, qtype=body.type, difficulty=body.difficulty,
                              material=material)
    if not items:
        db.commit()
        raise HTTPException(status_code=502, detail="The AI did not return usable questions. Try again.")
    qs = [_store(db, user, it, course_id=body.course_id, source=source) for it in items]
    db.commit()
    return {"questions": [practice.question_public(q) for q in qs]}


@router.post("/practice/import", status_code=201)
def import_questions(request: Request, file: UploadFile = File(...), course_id: str = Form(""), topic: str = Form(""),
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    _qb(db, user)
    check_course(db, user, course_id or None)
    data = read_upload(file, 10 * 1024 * 1024)
    try:
        items = practice.parse_import(file.filename or "", data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) or "Could not read the file.") from exc
    if not items:
        raise HTTPException(status_code=422, detail="No valid questions found. Columns: type, prompt, option_a..option_d, answer, explanation, topic.")
    f = storage.save_bytes(db, request.app.state.settings.data_dir, owner_id=user.id, filename=file.filename or "questions",
                           data=data, mime_type=file.content_type or "", purpose="upload")
    imp = PracticeImport(owner_id=user.id, file_id=f.id, filename=(file.filename or "questions")[:255], question_count=len(items))
    db.add(imp)
    db.flush()
    for it in items:
        it["topic"] = it["topic"] or topic
        _store(db, user, it, course_id=course_id or None, source={"import_id": imp.id})
    db.commit()
    return {"imported": len(items), "import_id": imp.id}


class AttemptBody(BaseModel):
    choice: int | None = None
    choices: list[int] | None = None
    text: str | None = Field(default=None, max_length=20000)
    time_ms: int = Field(default=0, ge=0)
    path_node_id: str | None = None


@router.post("/practice/questions/{qid}/attempt")
def attempt(qid: str, body: AttemptBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = owned(db, Question, qid, user)
    response = body.model_dump(exclude={"time_ms", "path_node_id"}, exclude_none=True)
    ok, score, feedback = practice.grade(db, user, q, response)
    db.add(PracticeAttempt(user_id=user.id, question_id=q.id, response=response, is_correct=ok, score=score, time_ms=body.time_ms))
    state = db.get(ReviewState, (user.id, q.id))
    if state is None:
        state = ReviewState(user_id=user.id, question_id=q.id, ease=2.5, interval_days=0, repetitions=0, lapses=0)
        db.add(state)
    practice.schedule(state, 5 if ok else max(0, round((score or 0) * 5) - 1))
    node_id = body.path_node_id or (q.source or {}).get("path_node_id")
    if node_id:
        node = db.get(PathNode, node_id)
        if node is not None:
            _evidence(db, user, node, "practice", score if score is not None else (1.0 if ok else 0.0))
    db.commit()
    return {"correct": ok, "score": score, "feedback": feedback, "answer": q.answer, "explanation": q.explanation,
            "next_review": iso(state.due_at)}


@router.get("/practice/review")
def review_queue(limit: int = 20, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _qb(db, user)
    now = utcnow()
    due = db.scalars(select(Question).join(ReviewState, (ReviewState.question_id == Question.id) & (ReviewState.user_id == user.id))
                     .where(ReviewState.due_at <= now).order_by(ReviewState.due_at).limit(limit)).all()
    fresh = []
    if len(due) < limit:
        seen = select(ReviewState.question_id).where(ReviewState.user_id == user.id)
        fresh = db.scalars(select(Question).where(Question.owner_id == user.id, Question.id.not_in(seen))
                           .order_by(Question.created_at).limit(limit - len(due))).all()
    return {"questions": [practice.question_public(q, with_answer=False) for q in [*due, *fresh]],
            "due": len(due), "new": len(fresh)}


@router.get("/practice/stats")
def stats(db: Session = Depends(get_db), user: User = Depends(current_user)):
    total = db.scalar(select(func.count()).select_from(Question).where(Question.owner_id == user.id)) or 0
    attempts = db.scalar(select(func.count()).select_from(PracticeAttempt).where(PracticeAttempt.user_id == user.id)) or 0
    correct = db.scalar(select(func.count()).select_from(PracticeAttempt).where(
        PracticeAttempt.user_id == user.id, PracticeAttempt.is_correct.is_(True))) or 0
    due = db.scalar(select(func.count()).select_from(ReviewState).where(ReviewState.user_id == user.id, ReviewState.due_at <= utcnow())) or 0
    days = {str(d) for d, in db.execute(select(func.date(PracticeAttempt.created_at)).where(PracticeAttempt.user_id == user.id)
                                         .group_by(func.date(PracticeAttempt.created_at))).all()}
    from datetime import timedelta

    # Consecutive days with practice, ending today (or yesterday if not practised yet today).
    cur = utcnow().date()
    if str(cur) not in days:
        cur -= timedelta(days=1)
    streak = 0
    while str(cur) in days:
        streak += 1
        cur -= timedelta(days=1)
    return {"questions": total, "attempts": attempts, "accuracy": round(correct / attempts, 3) if attempts else None,
            "due": due, "streak_days": streak}


# ── Mastery paths ────────────────────────────────────────────────────────────


def _node_public(n: PathNode) -> dict:
    return {"id": n.id, "parent_id": n.parent_id, "kind": n.kind, "title": n.title, "objectives": n.objectives or [],
            "position": n.position, "state": n.state, "mastery": round(n.mastery or 0, 3)}


def topic_public(t: LearningTopic, db: Session, full: bool = False) -> dict:
    path = db.scalar(select(LearningPath).where(LearningPath.topic_id == t.id, LearningPath.status == "active")
                     .order_by(LearningPath.version.desc()))
    out = {"id": t.id, "title": t.title, "goal": t.goal, "level": t.level, "status": t.status, "course_id": t.course_id,
           "created_at": iso(t.created_at), "path_version": path.version if path else None}
    if path is not None:
        nodes = db.scalars(select(PathNode).where(PathNode.path_id == path.id).order_by(PathNode.position)).all()
        lessons = [n for n in nodes if n.kind != "unit"]
        out["progress"] = round(sum(1 for n in lessons if n.state == "mastered") / len(lessons), 3) if lessons else 0
        if full:
            out["nodes"] = [_node_public(n) for n in nodes]
    return out


def _generate_path(db: Session, user: User, t: LearningTopic, material: str) -> LearningPath:
    prompt = (f"Design a learning path for: {t.title}. Goal: {t.goal or 'master the topic'}. Learner level: {t.level}. "
              f"Write titles in {tutor.LANG.get(user.locale, 'Vietnamese')}. 3-6 units, each with 2-5 lessons. "
              "Respond with JSON: {\"units\": [{\"title\": \"...\", \"objectives\": [\"...\"], \"lessons\": "
              "[{\"title\": \"...\", \"objectives\": [\"...\"]}]}]}")
    if material:
        prompt += "\nBuild it around this material:\n" + material[:10000]
    data = llm.complete_json(db, user=user, messages=[{"role": "user", "content": prompt}], purpose="mastery_path", fallback={})
    units = (data or {}).get("units") if isinstance(data, dict) else None
    if not units or not isinstance(units, list):
        units = [{"title": t.title, "objectives": [t.goal] if t.goal else [],
                  "lessons": [{"title": f"{t.title}: {part}"} for part in ("Foundations", "Core ideas", "Practice", "Review")]}]
    version = (db.scalar(select(func.max(LearningPath.version)).where(LearningPath.topic_id == t.id)) or 0) + 1
    for old in db.scalars(select(LearningPath).where(LearningPath.topic_id == t.id, LearningPath.status == "active")):
        old.status = "superseded"
    path = LearningPath(topic_id=t.id, version=version, status="active")
    db.add(path)
    db.flush()
    pos, first = 0, True
    for u in units[:10]:
        if not isinstance(u, dict):
            continue
        pos += 1
        unit = PathNode(path_id=path.id, kind="unit", title=str(u.get("title") or "Unit")[:200],
                        objectives=[str(o)[:300] for o in (u.get("objectives") or [])][:8], position=pos, state="available")
        db.add(unit)
        db.flush()
        for lesson in (u.get("lessons") or [])[:10]:
            if not isinstance(lesson, dict):
                continue
            pos += 1
            db.add(PathNode(path_id=path.id, parent_id=unit.id, kind="lesson", title=str(lesson.get("title") or "Lesson")[:200],
                            objectives=[str(o)[:300] for o in (lesson.get("objectives") or [])][:8], position=pos,
                            state="available" if first else "locked"))
            first = False
    return path


class TopicBody(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    goal: str = Field(default="", max_length=2000)
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    course_id: str | None = None
    kb_ids: list[str] = []


@router.get("/mastery/topics")
def list_topics(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(LearningTopic).where(LearningTopic.owner_id == user.id).order_by(LearningTopic.updated_at.desc())).all()
    ent = billing.entitlements(db, user)
    return {"topics": [topic_public(t, db) for t in rows],
            "allowance": {"limit": ent.get("mastery_paths"), "used": billing.usage_count(db, user, "mastery_paths"),
                          "period": ent.get("allowance_period")}}


@router.post("/mastery/topics", status_code=201)
def create_topic(body: TopicBody, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    check_course(db, user, body.course_id)
    billing.reserve(db, user, "mastery_paths")
    db.commit()  # count the creation before the slow AI call; given back if it fails
    try:
        t = LearningTopic(owner_id=user.id, title=body.title.strip(), goal=body.goal, level=body.level, course_id=body.course_id)
        db.add(t)
        db.flush()
        kbs = tutor.accessible_kbs(db, user, body.kb_ids)
        for kb in kbs:
            db.add(LearningTopicSource(topic_id=t.id, knowledge_base_id=kb.id))
        material = ""
        if kbs:
            hits = retrieval.search_kbs(db, request.app.state.settings.data_dir, f"{body.title} {body.goal}", kbs, user=user, limit=8)
            material = "\n\n".join(h.doc.text for h in hits)
        _generate_path(db, user, t, material)
        db.commit()
    except Exception:
        db.rollback()
        billing.release(db, user, "mastery_paths")
        db.commit()
        raise
    return {"topic": topic_public(t, db, True)}


@router.get("/mastery/topics/{tid}")
def get_topic(tid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"topic": topic_public(owned(db, LearningTopic, tid, user), db, True)}


@router.post("/mastery/topics/{tid}/regenerate")
def regenerate(tid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = owned(db, LearningTopic, tid, user)
    _generate_path(db, user, t, "")
    db.commit()
    return {"topic": topic_public(t, db, True)}


@router.delete("/mastery/topics/{tid}")
def delete_topic(tid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(owned(db, LearningTopic, tid, user))
    db.commit()
    return {"ok": True}


def _own_node(db: Session, user: User, node_id: str) -> tuple[PathNode, LearningTopic]:
    n = db.get(PathNode, node_id)
    path = db.get(LearningPath, n.path_id) if n else None
    t = db.get(LearningTopic, path.topic_id) if path else None
    if n is None or t is None or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    return n, t


def _evidence(db: Session, user: User, node: PathNode, kind: str, score: float) -> None:
    score = max(0.0, min(1.0, float(score)))
    db.add(LearningEvidence(user_id=user.id, node_id=node.id, kind=kind, score=score))
    # One good result is not mastery yet: the first counts 70%, later ones are blended in.
    node.mastery = round(0.6 * (node.mastery or 0) + 0.4 * score, 4) if node.mastery else score * 0.7
    if node.state in ("available", "locked"):
        node.state = "in_progress"
    if node.mastery >= 0.8 and node.state != "mastered":
        node.state = "mastered"
        nxt = db.scalar(select(PathNode).where(PathNode.path_id == node.path_id, PathNode.kind != "unit",
                                               PathNode.position > node.position).order_by(PathNode.position))
        if nxt is not None and nxt.state == "locked":
            nxt.state = "available"
    path = db.get(LearningPath, node.path_id)
    lessons = db.scalars(select(PathNode).where(PathNode.path_id == node.path_id, PathNode.kind != "unit")).all()
    if path and lessons and all(x.state == "mastered" for x in lessons):
        topic = db.get(LearningTopic, path.topic_id)
        topic.status = "completed"


@router.post("/mastery/nodes/{node_id}/start")
def start_lesson(node_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Open a tutoring conversation for the lesson."""
    n, t = _own_node(db, user, node_id)
    if n.state == "locked":
        raise HTTPException(status_code=409, detail="Finish the previous lesson first.")
    s = ChatSession(user_id=user.id, title=f"{t.title}: {n.title}"[:200], mode="tutor", course_id=t.course_id,
                    settings={"path_node_id": n.id, "objectives": n.objectives})
    db.add(s)
    db.flush()
    goals = "; ".join(n.objectives or []) or n.title
    db.add(ChatMessage(session_id=s.id, seq=1, role="system",
                       content=f"Lesson '{n.title}' in the path '{t.title}' (level {t.level}). Objectives: {goals}. "
                               "Teach it step by step and check understanding before moving on."))
    db.add(LearningSession(user_id=user.id, node_id=n.id, chat_session_id=s.id))
    if n.state == "available":
        n.state = "in_progress"
    db.commit()
    return {"chat_session_id": s.id, "node": _node_public(n)}


class EvidenceBody(BaseModel):
    kind: Literal["quiz", "explanation", "practice", "self_report"] = "self_report"
    score: float = Field(ge=0, le=1)


@router.post("/mastery/nodes/{node_id}/evidence")
def add_evidence(node_id: str, body: EvidenceBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n, t = _own_node(db, user, node_id)
    _evidence(db, user, n, body.kind, body.score)
    db.commit()
    return {"node": _node_public(n), "topic": topic_public(t, db)}


@router.post("/mastery/nodes/{node_id}/quiz", status_code=201)
def lesson_quiz(node_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    n, t = _own_node(db, user, node_id)
    items = practice.generate(db, user, topic=f"{t.title} - {n.title}. Objectives: {'; '.join(n.objectives or [])}",
                              count=3, qtype="mcq", difficulty={"beginner": 2, "intermediate": 3, "advanced": 4}[t.level])
    qs = [_store(db, user, it, course_id=t.course_id, source={"path_node_id": n.id}) for it in items]
    db.commit()
    return {"questions": [practice.question_public(q, with_answer=False) for q in qs]}


# ── Books ────────────────────────────────────────────────────────────────────


def book_public(b: Book, db: Session, full: bool = False) -> dict:
    out = {"id": b.id, "title": b.title, "topic": b.topic, "status": b.status, "course_id": b.course_id,
           "outline": b.outline or [], "created_at": iso(b.created_at), "updated_at": iso(b.updated_at)}
    chapters = db.scalars(select(BookChapter).where(BookChapter.book_id == b.id).order_by(BookChapter.position)).all()
    out["chapters"] = [{"id": c.id, "position": c.position, "title": c.title, "status": c.status,
                        **({"content": c.content} if full else {})} for c in chapters]
    task = db.scalar(select(BackgroundTask).where(BackgroundTask.kind == "book_generate",
                                                  BackgroundTask.payload["book_id"].as_string() == b.id)
                     .order_by(BackgroundTask.created_at.desc()).limit(1))
    out["task"] = tasks.public(task) if task else None
    return out


def _write_chapter(db: Session, user: User, b: Book, ch: BookChapter) -> None:
    outline = "\n".join(f"{i + 1}. {c.get('title')}" for i, c in enumerate(b.outline or []))
    ch.content = llm.complete(db, user=user, purpose="book_chapter", messages=[
        {"role": "system", "content": f"You write clear, engaging study books in {tutor.LANG.get(user.locale, 'Vietnamese')} "
                                      "using markdown: headings, short paragraphs, examples, and a short recap at the end."},
        {"role": "user", "content": f"Book: {b.title}\nTopic: {b.topic}\nOutline:\n{outline}\n\n"
                                    f"Write chapter {ch.position}: {ch.title}."}])
    ch.status = "ready"


def _generate_book(app_data_dir, book_id: str, user_id: str):
    def run(db: Session, task: BackgroundTask, report) -> dict:
        b, user = db.get(Book, book_id), db.get(User, user_id)
        try:
            data = llm.complete_json(db, user=user, purpose="book_outline", fallback={}, messages=[
                {"role": "user", "content": f"Plan a study book titled '{b.title}' about: {b.topic}. 5-8 chapters. "
                                            f"Titles in {tutor.LANG.get(user.locale, 'Vietnamese')}. Respond with JSON: "
                                            "{\"chapters\": [{\"title\": \"...\", \"summary\": \"...\"}]}"}])
            chapters = [c for c in (data or {}).get("chapters") or [] if isinstance(c, dict) and c.get("title")][:12]
            if not chapters:
                chapters = [{"title": t, "summary": ""} for t in ("Introduction", "Key ideas", "Worked examples", "Practice", "Summary")]
            b.outline = [{"title": str(c["title"])[:200], "summary": str(c.get("summary") or "")[:500]} for c in chapters]
            db.query(BookChapter).filter(BookChapter.book_id == b.id).delete()
            rows = [BookChapter(book_id=b.id, position=i + 1, title=c["title"], status="pending") for i, c in enumerate(b.outline)]
            db.add_all(rows)
            db.commit()
            for i, ch in enumerate(rows):
                _write_chapter(db, user, b, ch)
                db.commit()
                report((i + 1) / len(rows))
            b.status = "ready"
            db.commit()
            return {"chapters": len(rows)}
        except Exception:
            db.rollback()
            b = db.get(Book, book_id)
            b.status = "failed"
            db.commit()
            raise

    return run


class BookBody(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    topic: str = Field(default="", max_length=5000)
    course_id: str | None = None


@router.get("/books")
def list_books(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Book).where(Book.owner_id == user.id).order_by(Book.updated_at.desc())).all()
    ent = billing.entitlements(db, user)
    return {"books": [book_public(b, db) for b in rows],
            "allowance": {"limit": ent.get("books"), "used": billing.usage_count(db, user, "books"), "period": ent.get("allowance_period")}}


@router.post("/books", status_code=201)
def create_book(body: BookBody, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    check_course(db, user, body.course_id)
    llm.resolve(db, user)  # fail now (503/402) rather than inside the background task
    billing.reserve(db, user, "books")
    b = Book(owner_id=user.id, title=body.title.strip(), topic=body.topic or body.title, course_id=body.course_id,
             status="generating")
    db.add(b)
    db.commit()
    request.app.state.tasks.submit("book_generate", user.id, {"book_id": b.id},
                                   _generate_book(request.app.state.settings.data_dir, b.id, user.id))
    db.refresh(b)
    if b.status == "failed":
        billing.release(db, user, "books")
        db.commit()
    return {"book": book_public(b, db, True)}


@router.get("/books/{bid}")
def get_book(bid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"book": book_public(owned(db, Book, bid, user), db, True)}


class ChapterBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=500000)


@router.put("/books/{bid}/chapters/{cid}")
def edit_chapter(bid: str, cid: str, body: ChapterBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    b = owned(db, Book, bid, user)
    ch = db.get(BookChapter, cid)
    if ch is None or ch.book_id != b.id:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    ch.title, ch.content, ch.status = body.title.strip(), body.content, "ready"
    db.commit()
    return {"book": book_public(b, db, True)}


@router.post("/books/{bid}/chapters/{cid}/regenerate")
def regenerate_chapter(bid: str, cid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    b = owned(db, Book, bid, user)
    ch = db.get(BookChapter, cid)
    if ch is None or ch.book_id != b.id:
        raise HTTPException(status_code=404, detail="Chapter not found.")
    _write_chapter(db, user, b, ch)
    db.commit()
    return {"book": book_public(b, db, True)}


@router.delete("/books/{bid}")
def delete_book(bid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(owned(db, Book, bid, user))
    db.commit()
    return {"ok": True}


# ── Reading ──────────────────────────────────────────────────────────────────


def reading_public(r: ReadingDocument) -> dict:
    return {"id": r.id, "title": r.title, "kind": r.kind, "url": r.url, "file_id": r.file_id, "progress": r.progress,
            "position": r.position or {}, "created_at": iso(r.created_at), "last_opened_at": iso(r.last_opened_at)}


def _reading(db: Session, user: User) -> None:
    billing.require_feature(db, user, "reading")


@router.get("/reading")
def list_reading(db: Session = Depends(get_db), user: User = Depends(current_user)):
    ent = billing.entitlements(db, user)
    if not ent.get("reading"):
        return {"documents": [], "locked": True}
    rows = db.scalars(select(ReadingDocument).where(ReadingDocument.owner_id == user.id)
                      .order_by(func.coalesce(ReadingDocument.last_opened_at, ReadingDocument.created_at).desc())).all()
    return {"documents": [reading_public(r) for r in rows], "locked": False}


@router.post("/reading", status_code=201)
def add_reading(request: Request, file: UploadFile | None = File(None), url: str = Form(""), title: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    _reading(db, user)
    if file is None and not url.strip():
        raise HTTPException(status_code=422, detail="Upload a file or give a web address.")
    if file is not None:
        data = read_upload(file)
        billing.check_upload(db, user, len(data))
        name = (file.filename or "document").lower()
        kind = "pdf" if name.endswith(".pdf") else "epub" if name.endswith(".epub") else "web" if name.endswith((".html", ".htm")) else "text"
        f = storage.save_bytes(db, request.app.state.settings.data_dir, owner_id=user.id, filename=file.filename or "document",
                               data=data, mime_type=file.content_type or "", purpose="reading")
        r = ReadingDocument(owner_id=user.id, file_id=f.id, title=(title or file.filename or "Document").strip()[:300], kind=kind)
    else:
        u = url.strip()
        if not u.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="Enter a full web address starting with https://")
        kind = "video" if any(h in u for h in ("youtube.com", "youtu.be", "vimeo.com")) else "web"
        r = ReadingDocument(owner_id=user.id, url=u, title=(title or u).strip()[:300], kind=kind)
    db.add(r)
    db.commit()
    return {"document": reading_public(r)}


def _reading_text(db: Session, data_dir, r: ReadingDocument) -> extract.Extracted:
    if r.file_id:
        f = db.get(StoredFile, r.file_id)
        if f is None:
            raise HTTPException(status_code=404, detail="File missing.")
        try:
            return extract.extract(f.filename, storage.read_bytes(db, data_dir, f), f.mime_type)
        except extract.ExtractError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    from .kb import _fetch_web

    try:
        return _fetch_web(r.url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not load the page: {exc}") from exc


@router.get("/reading/{rid}")
def get_reading(rid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = owned(db, ReadingDocument, rid, user)
    r.last_opened_at = utcnow()
    db.commit()
    notes = db.scalars(select(ReadingAnnotation).where(ReadingAnnotation.document_id == r.id).order_by(ReadingAnnotation.created_at))
    return {"document": reading_public(r),
            "annotations": [{"id": a.id, "kind": a.kind, "quote": a.quote, "note": a.note, "anchor": a.anchor, "color": a.color,
                             "created_at": iso(a.created_at)} for a in notes]}


@router.get("/reading/{rid}/text")
def reading_text(rid: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = owned(db, ReadingDocument, rid, user)
    if r.kind == "video":
        return {"pages": [], "text": ""}
    ex = _reading_text(db, request.app.state.settings.data_dir, r)
    return {"pages": ex.pages, "text": ex.text[:2_000_000]}


class ProgressBody(BaseModel):
    progress: float = Field(ge=0, le=1)
    position: dict = {}


@router.put("/reading/{rid}/progress")
def save_progress(rid: str, body: ProgressBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = owned(db, ReadingDocument, rid, user)
    r.progress, r.position, r.last_opened_at = body.progress, body.position, utcnow()
    db.commit()
    return {"document": reading_public(r)}


@router.delete("/reading/{rid}")
def delete_reading(rid: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = owned(db, ReadingDocument, rid, user)
    if r.file_id:
        f = db.get(StoredFile, r.file_id)
        if f is not None:
            storage.delete_file(db, request.app.state.settings.data_dir, f)
    db.delete(r)
    db.commit()
    return {"ok": True}


class AnnotationBody(BaseModel):
    kind: Literal["highlight", "note", "bookmark"] = "highlight"
    quote: str = Field(default="", max_length=10000)
    note: str = Field(default="", max_length=10000)
    anchor: dict = {}
    color: str = Field(default="", max_length=16)


@router.post("/reading/{rid}/annotations", status_code=201)
def add_annotation(rid: str, body: AnnotationBody, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = owned(db, ReadingDocument, rid, user)
    a = ReadingAnnotation(document_id=r.id, kind=body.kind, quote=body.quote, note=body.note, anchor=body.anchor, color=body.color)
    db.add(a)
    db.commit()
    return {"annotation": {"id": a.id, "kind": a.kind, "quote": a.quote, "note": a.note, "anchor": a.anchor, "color": a.color}}


@router.delete("/reading/{rid}/annotations/{aid}")
def delete_annotation(rid: str, aid: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    r = owned(db, ReadingDocument, rid, user)
    a = db.get(ReadingAnnotation, aid)
    if a is None or a.document_id != r.id:
        raise HTTPException(status_code=404, detail="Annotation not found.")
    db.delete(a)
    db.commit()
    return {"ok": True}


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    selection: str = Field(default="", max_length=20000)
    page: int | None = None


@router.post("/reading/{rid}/ask")
def ask_reading(rid: str, body: AskBody, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Ask the AI about what you are reading (the selection, the current page, or the best matching passages)."""
    _reading(db, user)
    r = owned(db, ReadingDocument, rid, user)
    context = body.selection
    if not context and r.kind != "video":
        ex = _reading_text(db, request.app.state.settings.data_dir, r)
        if body.page and ex.pages and 0 < body.page <= len(ex.pages):
            context = ex.pages[body.page - 1]
        else:
            passages = extract.split_passages(ex, 1000)
            hits = retrieval.rank(body.question, [retrieval.Doc(str(p.seq), p.text) for p in passages], None, 4)
            context = "\n\n".join(h.doc.text for h in hits) or ex.text[:6000]
    answer = llm.complete(db, user=user, purpose="reading_ask", messages=[
        {"role": "system", "content": f"You help a learner understand '{r.title}'. Answer in {tutor.LANG.get(user.locale, 'Vietnamese')}, "
                                      "based on the excerpt; say so when the excerpt does not contain the answer."},
        {"role": "user", "content": f"Excerpt:\n{context[:12000]}\n\nQuestion: {body.question}"}])
    db.commit()
    return {"answer": answer}
