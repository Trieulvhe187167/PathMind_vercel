"""Company knowledge tree, curated by R&D, and the contribution workflow that feeds it.

Flow:
1. An educator files a *contribution* (documents + why + where it belongs), optionally
   answering an open *knowledge gap*.
2. R&D picks it up, discusses it in the contribution thread, asks for changes, approves or rejects.
3. On approval R&D opens a *change set*: the tool proposes nodes from the document's headings
   (and summaries), R&D edits the proposal, then publishes it. Publishing writes node versions,
   marks the document verified and cuts it into *chunks* that the AI retrieves and cites.
The tree is private to the company: only R&D (edit) and admins (read) can browse it. Learners and
educators never open it; the AI uses it for their answers and shows them only what
``citation_detail`` allows. Educators see domain names (to file contributions) and open gaps.
Good contributions earn reward points and badges.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import BigIntPK, JSONType, created_at, updated_at, user_fk, utcnow, uuid_pk


# ── The tree ──────────────────────────────────────────────────────────────


class KnowledgeDomain(Base):
    """One subject area of the company (root of a tree)."""

    __tablename__ = "knowledge_domains"

    id: Mapped[str] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    # strict: the AI answers questions in this domain only from verified knowledge, else says it doesn't know.
    # prefer: verified knowledge first; general knowledge allowed but labelled as unverified.
    answer_policy: Mapped[str] = mapped_column(String(10), default="prefer")
    # What a learner sees of a citation: title (document title + section) | quote (plus the quoted passage) | none
    citation_detail: Mapped[str] = mapped_column(String(8), default="title")
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | archived
    created_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class KnowledgeNode(Base):
    """A topic in the tree. Its published text is the current row; history is in node versions."""

    __tablename__ = "knowledge_nodes"

    id: Mapped[str] = uuid_pk()
    domain_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_domains.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")  # short, used to route questions to this node
    body: Mapped[str] = mapped_column(Text, default="")  # R&D's curated explanation (markdown)
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | published | archived
    version: Mapped[int] = mapped_column(Integer, default=0)  # last published version, 0 = never
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    updated_at: Mapped[datetime] = updated_at()


class KnowledgeNodeVersion(Base):
    """Snapshot of a node at each publish - lets R&D see history and roll back."""

    __tablename__ = "knowledge_node_versions"
    __table_args__ = (UniqueConstraint("node_id", "version"),)

    id: Mapped[str] = uuid_pk()
    node_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    change_set_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tree_change_sets.id", ondelete="SET NULL"), nullable=True)
    changed_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    change_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = created_at()


# ── Verified documents and what the AI reads ──────────────────────────────


class VerifiedDocument(Base):
    """A source document R&D approved for the tree. Only ``verified`` ones are used by the AI."""

    __tablename__ = "verified_documents"

    id: Mapped[str] = uuid_pk()
    domain_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_domains.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    contribution_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("contributions.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    doc_version: Mapped[str] = mapped_column(String(40), default="")  # the document's own version label, e.g. "v2.1"
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | verified | superseded | retired
    supersedes_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("verified_documents.id", ondelete="SET NULL"), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")  # extracted text
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    verified_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # When R&D must re-check it; past this date it is flagged (and, if set, retired automatically).
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = created_at()


class KnowledgeNodeDocument(Base):
    """Which verified documents back which node, and where in them."""

    __tablename__ = "knowledge_node_documents"

    node_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("verified_documents.id", ondelete="CASCADE"), primary_key=True, index=True)
    locator: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"pages": [3, 5], "section": "2.1"}


class KnowledgeChunk(Base):
    """A passage of a verified document - the unit the AI retrieves and cites.

    Searched two ways: PostgreSQL full-text search on ``text`` and a vector index keyed by ``id``
    (pgvector when available, otherwise the embedding engine's own index files).
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("document_id", "seq"),)

    id: Mapped[str] = uuid_pk()
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("verified_documents.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True, index=True)
    seq: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[str] = mapped_column(String(500), default="")  # "Chapter 2 > 2.1 Safety"
    text: Mapped[str] = mapped_column(Text)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding_model: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # false once the document is retired
    created_at: Mapped[datetime] = created_at()


# ── Contributions (educator -> R&D) ───────────────────────────────────────


class Contribution(Base):
    """An educator's proposal of documents for the tree, reviewed by R&D."""

    __tablename__ = "contributions"

    id: Mapped[str] = uuid_pk()
    contributor_id: Mapped[str] = user_fk()
    domain_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_domains.id", ondelete="SET NULL"), nullable=True, index=True)
    suggested_node_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True)
    gap_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_gaps.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")  # what it covers and why it is needed
    # draft | submitted | in_review | changes_requested | approved | rejected | integrated | withdrawn
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    reviewer_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)  # bumps on each resubmission
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ContributionFile(Base):
    __tablename__ = "contribution_files"

    id: Mapped[str] = uuid_pk()
    contribution_id: Mapped[str] = mapped_column(String(36), ForeignKey("contributions.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("files.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = created_at()


class ContributionEvent(Base):
    """The contribution's thread: comments between educator and R&D plus every status change."""

    __tablename__ = "contribution_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    contribution_id: Mapped[str] = mapped_column(String(36), ForeignKey("contributions.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    kind: Mapped[str] = mapped_column(String(12))  # comment | status | file
    from_status: Mapped[str] = mapped_column(String(20), default="")
    to_status: Mapped[str] = mapped_column(String(20), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ── R&D tools: drafts of tree changes, published in one step ──────────────


class TreeChangeSet(Base):
    """A batch of edits R&D prepares (by hand or proposed from a document) and publishes together."""

    __tablename__ = "tree_change_sets"

    id: Mapped[str] = uuid_pk()
    domain_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_domains.id", ondelete="CASCADE"), index=True)
    contribution_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("contributions.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(10), default="manual")  # manual | document | import | restore
    bulk_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bulk_imports.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)  # draft | published | discarded
    created_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True)
    published_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class TreeChange(Base):
    """One operation in a change set, applied in ``seq`` order on publish."""

    __tablename__ = "tree_changes"
    __table_args__ = (UniqueConstraint("change_set_id", "seq"),)

    id: Mapped[str] = uuid_pk()
    change_set_id: Mapped[str] = mapped_column(String(36), ForeignKey("tree_change_sets.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    # add_node | update_node | move_node | archive_node | attach_document | detach_document | verify_document | retire_document
    op: Mapped[str] = mapped_column(String(20))
    node_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=True)
    temp_key: Mapped[str] = mapped_column(String(40), default="")  # names a node added earlier in the same set
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    origin: Mapped[str] = mapped_column(String(10), default="manual")  # manual | suggested (by the tool)
    accepted: Mapped[bool] = mapped_column(Boolean, default=True)  # R&D can untick a suggestion
    created_at: Mapped[datetime] = created_at()


# ── Making the AI better: feedback and gaps ───────────────────────────────


class KnowledgeGap(Base):
    """A question the AI could not answer from verified knowledge (grouped by similar wording).

    R&D sees the most-asked gaps; educators see open gaps and can contribute documents for them.
    """

    __tablename__ = "knowledge_gaps"

    id: Mapped[str] = uuid_pk()
    domain_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("knowledge_domains.id", ondelete="SET NULL"), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text)  # representative wording
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)  # normalised hash for grouping
    ask_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open | requested | resolved | dismissed
    first_asked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_asked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # use_alter: contributions -> knowledge_gaps -> tree_change_sets -> contributions is a cycle,
    # so this one foreign key is added after all three tables exist.
    resolved_by_change_set_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tree_change_sets.id", ondelete="SET NULL", use_alter=True), nullable=True
    )


class AnswerFeedback(Base):
    """A learner's rating of one AI answer - wrong, outdated or missing sources go to R&D."""

    __tablename__ = "answer_feedback"

    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1 | -1
    reason: Mapped[str] = mapped_column(String(20), default="")  # wrong | outdated | missing_source | unclear | other
    comment: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(10), default="new")  # new | reviewed (by R&D)
    created_at: Mapped[datetime] = created_at()


# ── Rewards for good contributions ────────────────────────────────────────


class RewardLedger(Base):
    """Points earned (or spent) by contributors. Balance = sum(points). Append-only.

    Earned when a contribution is integrated, when the AI cites a document they contributed,
    when it closes a knowledge gap, or granted by R&D for quality. Spent when redeemed for
    plan days or credits (rates set by the admin in app settings).
    """

    __tablename__ = "reward_ledger"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = user_fk()
    points: Mapped[int] = mapped_column(Integer)  # positive = earned, negative = redeemed
    # contribution_integrated | quality_bonus | cited | gap_resolved | redeem_plan_days | redeem_credits | adjustment
    reason: Mapped[str] = mapped_column(String(24))
    contribution_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("contributions.id", ondelete="SET NULL"), nullable=True, index=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    granted_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)  # NULL = automatic
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class UserBadge(Base):
    """A badge shown on the contributor's profile, e.g. first_contribution, top_contributor_2026_10."""

    __tablename__ = "user_badges"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    badge: Mapped[str] = mapped_column(String(40), primary_key=True)
    awarded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    awarded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
