"""Study features: courses, notebooks, mastery paths, practice / question bank, books, reading, co-writer."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import BigIntPK, JSONType, created_at, updated_at, user_fk, utcnow, uuid_pk


# ── Courses: one subject that groups materials, paths, notes and chats ─────


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(16), default="")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class CourseKnowledgeBase(Base):
    __tablename__ = "course_knowledge_bases"

    course_id: Mapped[str] = mapped_column(String(36), ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)


# ── Notebooks ─────────────────────────────────────────────────────────────


class Notebook(Base):
    __tablename__ = "notebooks"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    course_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class NotebookEntry(Base):
    __tablename__ = "notebook_entries"

    id: Mapped[str] = uuid_pk()
    notebook_id: Mapped[str] = mapped_column(String(36), ForeignKey("notebooks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="note")  # note | chat_excerpt | quiz | summary
    title: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    source_message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    extra: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Tag(Base):
    """A user's own label for notebook entries (old "notebook categories")."""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    name: Mapped[str] = mapped_column(String(60))
    color: Mapped[str] = mapped_column(String(16), default="")


class NotebookEntryTag(Base):
    __tablename__ = "notebook_entry_tags"

    entry_id: Mapped[str] = mapped_column(String(36), ForeignKey("notebook_entries.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(36), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


# ── Mastery paths: topic -> generated path of lessons -> evidence of learning ─


class LearningTopic(Base):
    __tablename__ = "learning_topics"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    course_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(20), default="beginner")
    status: Mapped[str] = mapped_column(String(12), default="active")  # draft | active | completed | archived
    profile: Mapped[dict] = mapped_column(JSONType, default=dict)  # learner profile for this topic
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class LearningTopicSource(Base):
    __tablename__ = "learning_topic_sources"

    id: Mapped[str] = uuid_pk()
    topic_id: Mapped[str] = mapped_column(String(36), ForeignKey("learning_topics.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=True)
    url: Mapped[str] = mapped_column(String(1000), default="")


class LearningPath(Base):
    __tablename__ = "learning_paths"
    __table_args__ = (UniqueConstraint("topic_id", "version"),)

    id: Mapped[str] = uuid_pk()
    topic_id: Mapped[str] = mapped_column(String(36), ForeignKey("learning_topics.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | superseded
    created_at: Mapped[datetime] = created_at()


class PathNode(Base):
    """A unit / lesson / checkpoint in a path (tree via parent_id)."""

    __tablename__ = "path_nodes"

    id: Mapped[str] = uuid_pk()
    path_id: Mapped[str] = mapped_column(String(36), ForeignKey("learning_paths.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("path_nodes.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(12), default="lesson")  # unit | lesson | quiz | project
    title: Mapped[str] = mapped_column(String(200))
    objectives: Mapped[list] = mapped_column(JSONType, default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(12), default="locked")  # locked | available | in_progress | mastered
    mastery: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    updated_at: Mapped[datetime] = updated_at()


class LearningSession(Base):
    """Time spent on one path node, linked to the tutoring chat."""

    __tablename__ = "learning_sessions"

    id: Mapped[str] = uuid_pk()
    user_id: Mapped[str] = user_fk()
    node_id: Mapped[str] = mapped_column(String(36), ForeignKey("path_nodes.id", ondelete="CASCADE"), index=True)
    chat_session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LearningEvidence(Base):
    """Something that shows (lack of) mastery: a quiz score, an explanation, a practice result."""

    __tablename__ = "learning_evidence"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = user_fk()
    node_id: Mapped[str] = mapped_column(String(36), ForeignKey("path_nodes.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # quiz | explanation | practice | self_report
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── Practice: question bank + spaced repetition ───────────────────────────


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    course_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(200), default="", index=True)
    type: Mapped[str] = mapped_column(String(12), default="mcq")  # mcq | multi | true_false | short | essay
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSONType, default=list)
    answer: Mapped[dict] = mapped_column(JSONType, default=dict)
    explanation: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[int] = mapped_column(Integer, default=2)  # 1..5
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    source: Mapped[dict] = mapped_column(JSONType, default=dict)  # {chat_message_id} | {import_id} | {kb_id}
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class PracticeImport(Base):
    __tablename__ = "practice_imports"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at()


class PracticeAttempt(Base):
    __tablename__ = "practice_attempts"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = user_fk()
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    response: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReviewState(Base):
    """Spaced-repetition schedule of one question for one learner."""

    __tablename__ = "review_states"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(36), ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[float] = mapped_column(Float, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── Books: AI-written study books ─────────────────────────────────────────


class Book(Base):
    __tablename__ = "books"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    course_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | generating | ready | failed
    outline: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class BookChapter(Base):
    __tablename__ = "book_chapters"
    __table_args__ = (UniqueConstraint("book_id", "position"),)

    id: Mapped[str] = uuid_pk()
    book_id: Mapped[str] = mapped_column(String(36), ForeignKey("books.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")
    updated_at: Mapped[datetime] = updated_at()


# ── Reading: documents a learner reads with AI help ───────────────────────


class ReadingDocument(Base):
    __tablename__ = "reading_documents"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    url: Mapped[str] = mapped_column(String(1000), default="")
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(10), default="pdf")  # pdf | epub | web | video
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    position: Mapped[dict] = mapped_column(JSONType, default=dict)  # last page / cfi / timestamp
    created_at: Mapped[datetime] = created_at()
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReadingAnnotation(Base):
    __tablename__ = "reading_annotations"

    id: Mapped[str] = uuid_pk()
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("reading_documents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="highlight")  # highlight | note | bookmark
    quote: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    anchor: Mapped[dict] = mapped_column(JSONType, default=dict)
    color: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = created_at()


# ── Co-Writer: documents written together with the AI ─────────────────────


class WritingDocument(Base):
    __tablename__ = "writing_documents"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    course_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, default="")  # markdown
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WritingRevision(Base):
    """Saved snapshot of a writing document (manual save or before an AI rewrite)."""

    __tablename__ = "writing_revisions"

    id: Mapped[str] = uuid_pk()
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("writing_documents.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(String(20), default="manual")  # manual | before_ai_edit | autosave
    created_at: Mapped[datetime] = created_at()
