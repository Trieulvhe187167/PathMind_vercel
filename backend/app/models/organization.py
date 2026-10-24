"""Company administration: learner groups, and bulk imports (CSV / Excel) of accounts+plans or knowledge trees."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._base import BigIntPK, JSONType, created_at, updated_at, user_fk, uuid_pk


class LearnerGroup(Base):
    """A named group of learners (a client company, a department, an intake) to manage plans together."""

    __tablename__ = "learner_groups"

    id: Mapped[str] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True, index=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class LearnerGroupMember(Base):
    __tablename__ = "learner_group_members"

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("learner_groups.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    added_by_import_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bulk_imports.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = created_at()


class BulkImport(Base):
    """One uploaded CSV/Excel/JSON file and its processing: preview (dry run) first, then apply."""

    __tablename__ = "bulk_imports"

    id: Mapped[str] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(20), index=True)  # learner_plans | knowledge_tree
    file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    options: Mapped[dict] = mapped_column(JSONType, default=dict)  # e.g. {default_plan, default_days, group_id, send_invites}
    # uploaded | previewed | applying | applied | failed | canceled
    status: Mapped[str] = mapped_column(String(12), default="uploaded", index=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    ok_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)  # {created: n, updated: n, plans_assigned: n, nodes: n}
    created_by: Mapped[str | None] = user_fk(ondelete="SET NULL", nullable=True)
    created_at: Mapped[datetime] = created_at()
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BulkImportRow(Base):
    """One row of an import with its outcome - shown in the preview and in the downloadable error report."""

    __tablename__ = "bulk_import_rows"
    __table_args__ = (UniqueConstraint("import_id", "row_number"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    import_id: Mapped[str] = mapped_column(String(36), ForeignKey("bulk_imports.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)  # the row as read from the file
    action: Mapped[str] = mapped_column(String(20), default="")  # create_user | update_user | assign_plan | add_node | skip
    status: Mapped[str] = mapped_column(String(8), default="pending")  # pending | ok | error | skipped
    error: Mapped[str] = mapped_column(String(500), default="")
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
