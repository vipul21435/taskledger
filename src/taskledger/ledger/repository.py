"""The shared ledger: registration with collision checks, review status, audit log.

Every write happens in one transaction together with its audit row, so the
ledger and its history can never disagree. Races are resolved by the
database, not by the caller: the unique constraints on ``content_hashes.hash``
and ``tasks.id_key`` make exactly one of two concurrent registrations of the
same content or ID win, and the loser gets the same typed collision error a
sequential caller would.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, delete, event, select, update
from sqlalchemy.engine import CursorResult, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from taskledger.ledger.audit import ChainReport, new_entry, verify_chain
from taskledger.ledger.models import (
    AuditEntry,
    ContentHash,
    LshBucket,
    Submission,
    Task,
    TaskSignature,
)
from taskledger.ledger.states import LedgerError, ReviewStatus, check_transition
from taskledger.neardup import (
    DEFAULT_THRESHOLD,
    Fingerprint,
    LSHIndex,
    NearDuplicate,
    Signature,
    estimate,
)
from taskledger.neardup.minhash import DEFAULT_NUM_PERM

MIGRATIONS_DIR: Final = Path(__file__).with_name("migrations")
HEAD_REVISION: Final = "0002"
#: The ledger's LSH buckets are cut for this threshold; queries at any other
#: threshold reuse them and confirm candidates with the signature estimate.
BUCKET_THRESHOLD: Final = DEFAULT_THRESHOLD
_SEPARATORS = re.compile(r"[\s_.\-]+")
_WRITE_ATTEMPTS: Final = 5
#: States in which a task's content may be replaced by a revision.
REVISABLE_STATES: Final = frozenset({ReviewStatus.DRAFT, ReviewStatus.NEEDS_CHANGES})


class ExactCollisionError(LedgerError):
    """The canonical content hash is already registered."""

    def __init__(self, content_hash: str, existing_slug: str, existing_version: str) -> None:
        self.content_hash = content_hash
        self.existing_slug = existing_slug
        self.existing_version = existing_version
        super().__init__(
            f"exact collision: {content_hash} is already registered as "
            f"{existing_slug} {existing_version}"
        )


class IdCollisionError(LedgerError):
    """Another task already uses this ID once case and separators are folded."""

    def __init__(self, slug: str, existing_slug: str, id_key: str) -> None:
        self.slug = slug
        self.existing_slug = existing_slug
        self.id_key = id_key
        same = "the same ID" if slug == existing_slug else f"{existing_slug!r}"
        super().__init__(
            f"ID collision: {slug!r} folds to {id_key!r}, already used by {same} "
            "with different content"
        )


class NearDuplicateError(LedgerError):
    """The submission is too similar to registered tasks (see ``matches``)."""

    def __init__(self, slug: str, matches: list[NearDuplicate], threshold: float) -> None:
        self.slug = slug
        self.matches = matches
        self.threshold = threshold
        best = matches[0]
        super().__init__(
            f"near-duplicate: '{slug}' is {best.similarity:.2f} similar to '{best.key}' "
            f"(instruction {best.instruction_similarity:.2f}, solution "
            f"{best.solution_similarity:.2f}; threshold {threshold}); "
            f"{len(matches)} match(es) in total. Pass allow_near_dup to register anyway."
        )


class RevisionNotAllowedError(LedgerError):
    """New content is only accepted while a task is a draft or needs changes."""

    def __init__(self, slug: str, status: ReviewStatus) -> None:
        self.slug = slug
        self.status = status
        allowed = " or ".join(sorted(REVISABLE_STATES))
        super().__init__(f"{slug}: cannot revise content in status {status} (only in {allowed})")


class TaskNotFoundError(LedgerError, KeyError):
    """No task with this slug."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(slug)

    def __str__(self) -> str:
        return f"no task {self.slug!r} in the ledger"


def fold_id(slug: str) -> str:
    """ID collision key: case folded, separators (``-``, ``_``, ``.``, space) removed."""
    return _SEPARATORS.sub("", slug.casefold())


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """A task as stored in the ledger."""

    slug: str
    title: str
    version: str
    content_hash: str
    status: ReviewStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, task: Task) -> TaskRecord:
        """Snapshot an ORM row."""
        return cls(
            slug=task.slug,
            title=task.title,
            version=task.version,
            content_hash=task.content_hash,
            status=ReviewStatus(task.status),
            created_at=_aware(task.created_at),
            updated_at=_aware(task.updated_at),
        )

    def to_dict(self) -> dict[str, str]:
        """JSON-ready representation."""
        return {
            "slug": self.slug,
            "title": self.title,
            "version": self.version,
            "content_hash": self.content_hash,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One audit log row."""

    seq: int
    at: str
    actor: str
    action: str
    task_slug: str | None
    payload: str
    row_hash: str

    def to_dict(self) -> dict[str, object]:
        """JSON-ready representation."""
        return {
            "seq": self.seq,
            "at": self.at,
            "actor": self.actor,
            "action": self.action,
            "task": self.task_slug,
            "payload": self.payload,
            "row_hash": self.row_hash,
        }


def _encode(signature: Signature) -> str:
    return " ".join(map(str, signature))


def _decode(text: str) -> Signature:
    return tuple(int(value) for value in text.split())


def _aware(value: datetime) -> datetime:
    """SQLite drops the offset of stored timestamps; they are always UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_ledger_engine(url: str) -> Engine:
    """Engine for ``url``; SQLite gets foreign keys, a busy timeout and serialized writes."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True)
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: object) -> None:
        # Let SQLAlchemy emit BEGIN itself (the pysqlite driver would defer it).
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        # Take the write lock up front, so two writers queue instead of both
        # reading the audit head and failing late.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


class Ledger:
    """Repository over the ledger database at ``url``."""

    def __init__(self, url: str, *, clock: Any = _utcnow) -> None:
        self.url = url
        self.engine = create_ledger_engine(url)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self._clock = clock

    @property
    def display_url(self) -> str:
        """The URL with any password masked."""
        return make_url(self.url).render_as_string(hide_password=True)

    def close(self) -> None:
        """Release pooled connections."""
        self.engine.dispose()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- schema ----------------------------------------------------------------

    def migrate(self) -> str:
        """Upgrade the schema to the latest Alembic revision and return it."""
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        return self.revision() or ""

    def revision(self) -> str | None:
        """Current Alembic revision, None for an empty database."""
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()

    # -- helpers -----------------------------------------------------------------

    def _now(self) -> datetime:
        value: datetime = self._clock()
        return value

    @staticmethod
    def _task(session: Session, slug: str) -> Task:
        task = session.scalar(select(Task).where(Task.slug == slug))
        if task is None:
            raise TaskNotFoundError(slug)
        return task

    @staticmethod
    def _audit(
        session: Session,
        *,
        at: datetime,
        actor: str,
        action: str,
        task_slug: str | None,
        payload: Mapping[str, object],
    ) -> AuditEntry:
        head = session.scalar(select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1))
        entry = new_entry(
            prev=head,
            at=at.isoformat(timespec="microseconds"),
            actor=actor,
            action=action,
            task_slug=task_slug,
            payload=payload,
        )
        session.add(entry)
        return entry

    @staticmethod
    def _raise_exact(session: Session, content_hash: str) -> None:
        existing = session.execute(
            select(Task.slug, Submission.version)
            .join(ContentHash, ContentHash.task_id == Task.id)
            .join(Submission, Submission.id == ContentHash.submission_id)
            .where(ContentHash.hash == content_hash)
        ).first()
        if existing is not None:
            raise ExactCollisionError(content_hash, existing.slug, existing.version)

    def _raise_collision(self, session: Session, content_hash: str, slug: str) -> None:
        self._raise_exact(session, content_hash)
        key = fold_id(slug)
        other = session.scalar(select(Task.slug).where(Task.id_key == key))
        if other is not None:
            raise IdCollisionError(slug, other, key)

    @staticmethod
    def _bucket_keys(fingerprint: Fingerprint) -> list[str]:
        index = LSHIndex(BUCKET_THRESHOLD, DEFAULT_NUM_PERM)
        return [f"i:{key}" for key in index.band_keys(fingerprint.instruction)] + [
            f"s:{key}" for key in index.band_keys(fingerprint.solution)
        ]

    def _store_fingerprint(
        self, session: Session, task: Task, submission: Submission, fingerprint: Fingerprint
    ) -> None:
        session.add(
            TaskSignature(
                task_id=task.id,
                submission_id=submission.id,
                instruction=_encode(fingerprint.instruction),
                solution=_encode(fingerprint.solution),
                created_at=submission.created_at,
            )
        )
        session.execute(delete(LshBucket).where(LshBucket.task_id == task.id))
        session.add_all(
            LshBucket(bucket=key, task_id=task.id) for key in self._bucket_keys(fingerprint)
        )

    # -- queries -----------------------------------------------------------------

    def near_duplicates(
        self,
        fingerprint: Fingerprint,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        exclude_slug: str | None = None,
    ) -> list[NearDuplicate]:
        """Registered tasks whose latest signatures reach ``threshold``, most similar first.

        Candidates are the tasks sharing an LSH bucket (one indexed query);
        each is confirmed against the signatures of its latest submission.
        """
        with self._sessions() as session:
            task_ids = set(
                session.scalars(
                    select(LshBucket.task_id).where(
                        LshBucket.bucket.in_(self._bucket_keys(fingerprint))
                    )
                )
            )
            found = []
            for task_id in sorted(task_ids):
                row = session.execute(
                    select(Task.slug, TaskSignature.instruction, TaskSignature.solution)
                    .join(TaskSignature, TaskSignature.task_id == Task.id)
                    .where(Task.id == task_id)
                    .order_by(TaskSignature.submission_id.desc())
                    .limit(1)
                ).one()
                if row.slug == exclude_slug:
                    continue
                match = NearDuplicate(
                    row.slug,
                    estimate(fingerprint.instruction, _decode(row.instruction)),
                    estimate(fingerprint.solution, _decode(row.solution)),
                )
                if match.similarity >= threshold:
                    found.append(match)
        return sorted(
            found,
            key=lambda m: (-m.similarity, -m.instruction_similarity - m.solution_similarity, m.key),
        )

    def get(self, slug: str) -> TaskRecord:
        """The task with ``slug``."""
        with self._sessions() as session:
            return TaskRecord.of(self._task(session, slug))

    def find_by_hash(self, content_hash: str) -> TaskRecord | None:
        """The task that registered ``content_hash``, if any."""
        with self._sessions() as session:
            task = session.scalar(
                select(Task)
                .join(ContentHash, ContentHash.task_id == Task.id)
                .where(ContentHash.hash == content_hash)
            )
            return None if task is None else TaskRecord.of(task)

    def find_by_id_key(self, id_key: str) -> TaskRecord | None:
        """The task whose folded ID is ``id_key``, if any."""
        with self._sessions() as session:
            task = session.scalar(select(Task).where(Task.id_key == id_key))
            return None if task is None else TaskRecord.of(task)

    def history(self, slug: str | None = None) -> list[AuditRecord]:
        """Audit rows in order, for one task or the whole ledger."""
        query = select(AuditEntry).order_by(AuditEntry.seq)
        if slug is not None:
            query = query.where(AuditEntry.task_slug == slug)
        with self._sessions() as session:
            return [
                AuditRecord(
                    seq=row.seq,
                    at=row.at,
                    actor=row.actor,
                    action=row.action,
                    task_slug=row.task_slug,
                    payload=row.payload,
                    row_hash=row.row_hash,
                )
                for row in session.scalars(query)
            ]

    def verify_chain(self) -> ChainReport:
        """Recompute the audit hash chain; ``ok`` is False after any tampering."""
        with self._sessions() as session:
            return verify_chain(session.scalars(select(AuditEntry).order_by(AuditEntry.seq)))

    # -- writes ------------------------------------------------------------------

    def register(
        self,
        *,
        slug: str,
        version: str,
        title: str,
        content_hash: str,
        actor: str,
        fingerprint: Fingerprint | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        allow_near_dup: bool = False,
    ) -> TaskRecord:
        """Register a new task in ``draft``.

        Raises :class:`ExactCollisionError` if the content hash is already in
        the ledger and :class:`IdCollisionError` if the folded slug is taken.
        With a ``fingerprint``, its signatures are stored for later checks and
        :class:`NearDuplicateError` is raised when a registered task reaches
        ``threshold``, unless ``allow_near_dup`` is set; an allowed
        registration lists the matches in its audit entry.
        """
        near: list[NearDuplicate] = []
        if fingerprint is not None:
            near = self.near_duplicates(fingerprint, threshold=threshold)
            if near and not allow_near_dup:
                with self._sessions() as session:
                    self._raise_collision(session, content_hash, slug)
                raise NearDuplicateError(slug, near, threshold)
        extra: dict[str, object] = {}
        if near:
            extra["near_duplicates_allowed"] = [
                {
                    "slug": match.key,
                    "similarity": round(match.similarity, 4),
                    "instruction_similarity": round(match.instruction_similarity, 4),
                    "solution_similarity": round(match.solution_similarity, 4),
                }
                for match in near
            ]
            extra["threshold"] = threshold
        for attempt in range(_WRITE_ATTEMPTS):
            with self._sessions() as session:
                self._raise_collision(session, content_hash, slug)
                now = self._now()
                task = Task(
                    slug=slug,
                    id_key=fold_id(slug),
                    title=title,
                    version=version,
                    content_hash=content_hash,
                    status=ReviewStatus.DRAFT.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(task)
                try:
                    session.flush()
                    submission = Submission(
                        task_id=task.id,
                        version=version,
                        content_hash=content_hash,
                        submitted_by=actor,
                        created_at=now,
                    )
                    session.add(submission)
                    session.flush()
                    session.add(
                        ContentHash(
                            hash=content_hash,
                            task_id=task.id,
                            submission_id=submission.id,
                            created_at=now,
                        )
                    )
                    if fingerprint is not None:
                        self._store_fingerprint(session, task, submission, fingerprint)
                    self._audit(
                        session,
                        at=now,
                        actor=actor,
                        action="register",
                        task_slug=slug,
                        payload={
                            "version": version,
                            "content_hash": content_hash,
                            "status": ReviewStatus.DRAFT.value,
                            **extra,
                        },
                    )
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    # A concurrent writer won: report its row like a sequential
                    # caller would see it, or retry if only the audit head moved.
                    with self._sessions() as fresh:
                        self._raise_collision(fresh, content_hash, slug)
                    if attempt == _WRITE_ATTEMPTS - 1:
                        raise
                    continue
                return TaskRecord.of(task)
        raise AssertionError("unreachable")  # pragma: no cover

    def revise(
        self,
        *,
        slug: str,
        version: str,
        title: str,
        content_hash: str,
        actor: str,
        fingerprint: Fingerprint | None = None,
    ) -> TaskRecord:
        """Record new content for a task in ``draft`` or ``needs_changes``.

        The status is unchanged (move ``needs_changes -> submitted`` next).
        Raises :class:`ExactCollisionError` if the content was ever registered,
        including as an earlier revision of this task, and
        :class:`RevisionNotAllowedError` in any other status.
        """
        for attempt in range(_WRITE_ATTEMPTS):
            with self._sessions() as session:
                task = self._task(session, slug)
                status = ReviewStatus(task.status)
                if status not in REVISABLE_STATES:
                    raise RevisionNotAllowedError(slug, status)
                self._raise_exact(session, content_hash)
                now = self._now()
                previous = {"version": task.version, "content_hash": task.content_hash}
                task.version, task.title = version, title
                task.content_hash, task.updated_at = content_hash, now
                try:
                    submission = Submission(
                        task_id=task.id,
                        version=version,
                        content_hash=content_hash,
                        submitted_by=actor,
                        created_at=now,
                    )
                    session.add(submission)
                    session.flush()
                    session.add(
                        ContentHash(
                            hash=content_hash,
                            task_id=task.id,
                            submission_id=submission.id,
                            created_at=now,
                        )
                    )
                    if fingerprint is not None:
                        self._store_fingerprint(session, task, submission, fingerprint)
                    self._audit(
                        session,
                        at=now,
                        actor=actor,
                        action="revise",
                        task_slug=slug,
                        payload={
                            "version": version,
                            "content_hash": content_hash,
                            "previous": previous,
                        },
                    )
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    with self._sessions() as fresh:
                        self._raise_exact(fresh, content_hash)
                    if attempt == _WRITE_ATTEMPTS - 1:
                        raise
                    continue
                return TaskRecord.of(task)
        raise AssertionError("unreachable")  # pragma: no cover

    def transition(
        self, slug: str, target: ReviewStatus, *, actor: str, note: str | None = None
    ) -> TaskRecord:
        """Move a task to ``target`` or raise :class:`IllegalTransitionError`."""
        for attempt in range(_WRITE_ATTEMPTS):
            with self._sessions() as session:
                task = self._task(session, slug)
                current = ReviewStatus(task.status)
                check_transition(slug, current, target)
                now = self._now()
                # Compare-and-set on the old status: a concurrent transition
                # makes this match no row instead of silently overwriting it.
                changed = cast(
                    "CursorResult[Any]",
                    session.execute(
                        update(Task)
                        .where(Task.id == task.id, Task.status == current.value)
                        .values(status=target.value, updated_at=now)
                        .execution_options(synchronize_session=False)
                    ),
                )
                if changed.rowcount != 1:  # pragma: no cover - needs a backend without
                    # BEGIN IMMEDIATE (Postgres); SQLite serializes writers.
                    session.rollback()
                    continue
                record = replace(TaskRecord.of(task), status=target, updated_at=now)
                payload: dict[str, object] = {"from": current.value, "to": target.value}
                if note:
                    payload["note"] = note
                self._audit(
                    session,
                    at=now,
                    actor=actor,
                    action="transition",
                    task_slug=slug,
                    payload=payload,
                )
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    if attempt == _WRITE_ATTEMPTS - 1:
                        raise
                    continue
                return record
        raise LedgerError(  # pragma: no cover - see the compare-and-set above
            f"{slug}: status kept changing concurrently; try again"
        )
