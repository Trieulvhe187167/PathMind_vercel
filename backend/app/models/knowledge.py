"""Files and personal knowledge bases (RAG). The company knowledge tree is in ``knowledge_tree.py``."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import JSONType, created_at, updated_at, user_fk, uuid_pk


class StoredFile(Base):
    """Any uploaded file (uploads, contributions, verified documents, exports, avatars).

    The bytes live behind a storage gateway: the default ``storage_backends`` row (an S3-compatible
    bucket) when the admin configured one, otherwise the server's own disk. This row is the index; downloads go through the API, which checks access
    and then streams the file or hands out a short-lived signed URL.
    """

    __tablename__ = "files"

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    # NULL = the server's own disk; otherwise the object storage that holds the bytes. Kept per
    # file so files saved before a switch stay readable; a background task can move them across.
    storage_backend_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("storage_backends.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)  # path on disk or object key
    purpose: Mapped[str] = mapped_column(String(20), default="upload")  # upload | chat | kb | avatar | reading
    created_at: Mapped[datetime] = created_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KnowledgeBase(Base):
    """A searchable document collection; the vector index lives in the engine's own files."""

    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[str] = uuid_pk()
    owner_id: Mapped[str] = user_fk()
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    engine: Mapped[str] = mapped_column(String(20), default="llamaindex")  # llamaindex | lightrag | graphrag
    embedding_model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(12), default="empty")  # empty | indexing | ready | error
    # private: owner only; shared: users in knowledge_base_access; public: everyone (admin-published)
    visibility: Mapped[str] = mapped_column(String(10), default="private")
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class KnowledgeBaseAccess(Base):
    __tablename__ = "knowledge_base_access"

    knowledge_base_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    created_at: Mapped[datetime] = created_at()


class KnowledgeBaseDocument(Base):
    __tablename__ = "knowledge_base_documents"

    id: Mapped[str] = uuid_pk()
    knowledge_base_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    folder: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | indexed | failed
    error: Mapped[str] = mapped_column(Text, default="")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()


