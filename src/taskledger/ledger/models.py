"""SQLAlchemy 2.0 models of the shared ledger.

The Alembic baseline in ``migrations/versions`` creates exactly this schema
(a test compares the two), plus the triggers that make ``audit_log``
append-only.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: ``sha256:`` plus 64 hex digits.
HASH_LENGTH = 71

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with deterministic constraint names."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Task(Base):
    """One task, identified by its slug; ``id_key`` is the folded slug."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    id_key: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(HASH_LENGTH))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Submission(Base):
    """One submitted version of a task."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(HASH_LENGTH))
    submitted_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContentHash(Base):
    """Every canonical hash ever registered; the unique constraint is the dedupe."""

    __tablename__ = "content_hashes"
    __table_args__ = (UniqueConstraint("hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hash: Mapped[str] = mapped_column(String(HASH_LENGTH))
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEntry(Base):
    """One row of the append-only, hash-chained audit log.

    ``row_hash`` is the sha256 of the canonical JSON of every other column plus
    ``prev_hash``; ``seq`` and ``prev_hash`` are unique, so two writers can
    never fork the chain. ``at`` is stored as text because it is hashed.
    """

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    at: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    task_slug: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    row_hash: Mapped[str] = mapped_column(String(64), unique=True)
