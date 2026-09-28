"""Ledger core: migration, exact and ID collisions, state machine, audit chain."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from taskledger.ledger import (
    FINAL_STATES,
    GENESIS_HASH,
    HEAD_REVISION,
    TRANSITIONS,
    ExactCollisionError,
    IdCollisionError,
    IllegalTransitionError,
    Ledger,
    LedgerError,
    ReviewStatus,
    TaskNotFoundError,
    check_transition,
    fold_id,
)
from taskledger.ledger import audit as audit_module
from taskledger.ledger import repository as repository_module
from taskledger.ledger.models import Base

H1 = "sha256:" + "1" * 64
H2 = "sha256:" + "2" * 64
H3 = "sha256:" + "3" * 64


class StepClock:
    """Deterministic clock: one second per call."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(f"sqlite:///{tmp_path / 'db' / 'ledger.db'}", clock=StepClock()) as instance:
        instance.migrate()
        yield instance


def register(ledger: Ledger, slug: str = "matrix-rank", content_hash: str = H1) -> None:
    ledger.register(
        slug=slug,
        version="1.0.0",
        title="Rank of an integer matrix",
        content_hash=content_hash,
        actor="alice",
    )


def test_migrate_is_idempotent_and_matches_the_models(ledger: Ledger) -> None:
    assert ledger.revision() == HEAD_REVISION
    assert ledger.migrate() == HEAD_REVISION
    with ledger.engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []
    names = set(sa.inspect(ledger.engine).get_table_names())
    assert {"tasks", "submissions", "content_hashes", "audit_log"} <= names


def test_empty_database_has_no_revision(tmp_path: Path) -> None:
    with Ledger(f"sqlite:///{tmp_path / 'empty.db'}") as empty:
        assert empty.revision() is None
        empty.migrate()
        before = datetime.now(UTC)
        register(empty)
        assert empty.get("matrix-rank").created_at >= before
        assert empty.display_url.endswith("empty.db")


def test_display_url_masks_passwords() -> None:
    with Ledger("sqlite://") as memory:
        assert memory.display_url == "sqlite://"
    url = "postgresql+psycopg://user:secret@db:5432/ledger"
    assert "secret" not in repository_module.make_url(url).render_as_string(hide_password=True)


def test_register_and_get(ledger: Ledger) -> None:
    register(ledger)
    task = ledger.get("matrix-rank")
    assert task.status is ReviewStatus.DRAFT
    assert task.content_hash == H1
    assert task.created_at == datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert task.to_dict()["status"] == "draft"
    assert ledger.find_by_hash(H1) == task
    assert ledger.find_by_hash(H2) is None
    with pytest.raises(TaskNotFoundError, match="no task 'nope' in the ledger"):
        ledger.get("nope")


def test_exact_collision_names_the_existing_task(ledger: Ledger) -> None:
    register(ledger)
    with pytest.raises(ExactCollisionError) as info:
        register(ledger, slug="a-copy", content_hash=H1)
    assert info.value.existing_slug == "matrix-rank"
    assert info.value.existing_version == "1.0.0"
    assert str(info.value) == f"exact collision: {H1} is already registered as matrix-rank 1.0.0"
    assert [row.action for row in ledger.history()] == ["register"]


@pytest.mark.parametrize(
    "slug", ["matrix-rank", "Matrix-Rank", "matrix_rank", "matrixrank", "matrix.rank"]
)
def test_id_collision_folds_case_and_separators(ledger: Ledger, slug: str) -> None:
    register(ledger)
    with pytest.raises(IdCollisionError) as info:
        register(ledger, slug=slug, content_hash=H2)
    assert info.value.existing_slug == "matrix-rank"
    assert info.value.id_key == "matrixrank"


def test_id_collision_message() -> None:
    same = IdCollisionError("matrix-rank", "matrix-rank", "matrixrank")
    assert "already used by the same ID with different content" in str(same)
    other = IdCollisionError("matrix_rank", "matrix-rank", "matrixrank")
    assert "already used by 'matrix-rank'" in str(other)


def test_fold_id() -> None:
    assert fold_id("Modular-Inverse_Table.v2 x") == "modularinversetablev2x"
    assert fold_id("modular-inverse-table") != fold_id("modular-inverse-tables")


def test_race_is_resolved_by_the_unique_constraint(
    ledger: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that passed the pre-check still loses to the constraint."""
    register(ledger)
    real = Ledger._raise_collision
    calls: list[str] = []

    def skip_first(self: Ledger, session: sa.orm.Session, content_hash: str, slug: str) -> None:
        calls.append(slug)
        if len(calls) > 1:
            real(self, session, content_hash, slug)

    monkeypatch.setattr(Ledger, "_raise_collision", skip_first)
    with pytest.raises(ExactCollisionError):
        register(ledger, slug="late-writer", content_hash=H1)
    assert len(calls) == 2
    with pytest.raises(TaskNotFoundError):
        ledger.get("late-writer")


def test_audit_head_race_is_retried(ledger: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    register(ledger)
    real = repository_module.new_entry
    stale: list[bool] = []

    def stale_once(**kwargs: object) -> object:
        if not stale:
            stale.append(True)
            kwargs["prev"] = None  # as if another writer appended in between
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository_module, "new_entry", stale_once)
    register(ledger, slug="second-task", content_hash=H2)
    ledger.transition("second-task", ReviewStatus.SUBMITTED, actor="bob")
    assert [row.seq for row in ledger.history()] == [1, 2, 3]
    assert ledger.verify_chain().ok


def test_persistent_audit_conflict_is_raised(
    ledger: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    register(ledger)
    real = repository_module.new_entry

    def always_stale(**kwargs: object) -> object:
        kwargs["prev"] = None
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository_module, "new_entry", always_stale)
    with pytest.raises(IntegrityError):
        register(ledger, slug="second-task", content_hash=H2)
    with pytest.raises(IntegrityError):
        ledger.transition("matrix-rank", ReviewStatus.SUBMITTED, actor="bob")
    assert ledger.get("matrix-rank").status is ReviewStatus.DRAFT


def test_full_review_path_with_needs_changes_loop(ledger: Ledger) -> None:
    register(ledger)
    path = [
        ReviewStatus.SUBMITTED,
        ReviewStatus.IN_REVIEW,
        ReviewStatus.NEEDS_CHANGES,
        ReviewStatus.SUBMITTED,
        ReviewStatus.IN_REVIEW,
        ReviewStatus.ACCEPTED,
    ]
    for target in path:
        record = ledger.transition("matrix-rank", target, actor="rev", note=f"to {target}")
        assert record.status is target
        assert ledger.get("matrix-rank").status is target
    history = ledger.history("matrix-rank")
    assert [row.action for row in history] == ["register"] + ["transition"] * len(path)
    assert '"note":"to accepted"' in history[-1].payload
    assert history[-1].to_dict()["task"] == "matrix-rank"


def test_illegal_transitions_raise_typed_errors(ledger: Ledger) -> None:
    register(ledger)
    with pytest.raises(IllegalTransitionError) as info:
        ledger.transition("matrix-rank", ReviewStatus.ACCEPTED, actor="rev")
    assert info.value.current is ReviewStatus.DRAFT
    assert info.value.allowed == (ReviewStatus.SUBMITTED,)
    assert isinstance(info.value, LedgerError)
    assert ledger.get("matrix-rank").status is ReviewStatus.DRAFT
    assert len(ledger.history()) == 1
    with pytest.raises(TaskNotFoundError):
        ledger.transition("nope", ReviewStatus.SUBMITTED, actor="rev")


def test_state_machine_table() -> None:
    assert {ReviewStatus.ACCEPTED, ReviewStatus.REJECTED} == FINAL_STATES
    legal = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}
    assert len(legal) == 6
    for current in ReviewStatus:
        for target in ReviewStatus:
            if (current, target) in legal:
                check_transition("t", current, target)
            else:
                with pytest.raises(IllegalTransitionError):
                    check_transition("t", current, target)
    final = IllegalTransitionError("t", ReviewStatus.ACCEPTED, ReviewStatus.SUBMITTED)
    assert "allowed from accepted: none, it is final" in str(final)


def _populate(ledger: Ledger) -> None:
    register(ledger)
    register(ledger, slug="gcd-table", content_hash=H2)
    ledger.transition("matrix-rank", ReviewStatus.SUBMITTED, actor="alice")
    register(ledger, slug="prime-sieve", content_hash=H3)


def test_audit_chain_links_every_row(ledger: Ledger) -> None:
    _populate(ledger)
    report = ledger.verify_chain()
    assert report.ok
    assert report.entries == 4
    rows = ledger.history()
    assert report.head == rows[-1].row_hash
    assert report.to_dict()["first_bad_seq"] is None
    assert [row.seq for row in ledger.history("matrix-rank")] == [1, 3]


def test_empty_chain_verifies(ledger: Ledger) -> None:
    report = ledger.verify_chain()
    assert (report.ok, report.entries, report.head) == (True, 0, GENESIS_HASH)


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET actor = 'mallory'", "DELETE FROM audit_log"]
)
def test_triggers_block_update_and_delete(ledger: Ledger, statement: str) -> None:
    _populate(ledger)
    with (
        pytest.raises(IntegrityError, match="audit_log is append-only"),
        ledger.engine.begin() as conn,
    ):
        conn.execute(sa.text(statement))
    assert ledger.verify_chain().entries == 4


def _tamper(ledger: Ledger, *statements: str) -> None:
    with ledger.engine.begin() as conn:
        conn.execute(sa.text("DROP TRIGGER audit_log_no_update"))
        conn.execute(sa.text("DROP TRIGGER audit_log_no_delete"))
        for statement in statements:
            conn.execute(sa.text(statement))


@pytest.mark.parametrize(
    ("statements", "bad_seq", "reason"),
    [
        (["UPDATE audit_log SET actor = 'mallory' WHERE seq = 2"], 2, "row edited"),
        (["DELETE FROM audit_log WHERE seq = 2"], 3, "row missing or inserted"),
        (
            # Relinking a row to a forged predecessor breaks at that row.
            ["UPDATE audit_log SET prev_hash = '" + "f" * 64 + "' WHERE seq = 3"],
            3,
            "prev_hash does not match",
        ),
        (["DELETE FROM audit_log WHERE seq = 4"], None, None),
    ],
)
def test_verify_chain_detects_tampering(
    ledger: Ledger, statements: list[str], bad_seq: int | None, reason: str | None
) -> None:
    _populate(ledger)
    _tamper(ledger, *statements)
    report = ledger.verify_chain()
    if bad_seq is None:
        # Truncating the tail keeps a valid prefix: compare the head to a
        # published one to catch it (the CLI prints the head for that).
        assert report.ok
        assert report.entries == 3
        return
    assert not report.ok
    assert report.first_bad_seq == bad_seq
    assert reason is not None
    assert report.reason is not None
    assert reason in report.reason


def test_row_hash_is_canonical() -> None:
    first = audit_module.new_entry(
        prev=None,
        at="2026-01-01T00:00:00+00:00",
        actor="a",
        action="register",
        task_slug="t",
        payload={"b": 1, "a": 2},
    )
    second = audit_module.new_entry(
        prev=None,
        at="2026-01-01T00:00:00+00:00",
        actor="a",
        action="register",
        task_slug="t",
        payload={"a": 2, "b": 1},
    )
    assert first.payload == '{"a":2,"b":1}'
    assert first.row_hash == second.row_hash
    assert first.prev_hash == GENESIS_HASH
    follower = audit_module.new_entry(
        prev=first, at="x", actor="a", action="transition", task_slug="t", payload={}
    )
    assert (follower.seq, follower.prev_hash) == (2, first.row_hash)


def test_non_sqlite_urls_get_a_plain_pooled_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> str:
        seen.update(url=url, **kwargs)
        return "engine"

    monkeypatch.setattr(repository_module, "create_engine", fake_create_engine)
    url = "postgresql+psycopg://taskledger:secret@db:5432/taskledger"
    assert repository_module.create_ledger_engine(url) == "engine"  # type: ignore[comparison-overlap]
    assert seen == {"url": url, "pool_pre_ping": True}


def test_baseline_downgrade_drops_everything(ledger: Ledger) -> None:
    config = Config()
    config.set_main_option("script_location", str(repository_module.MIGRATIONS_DIR))
    with ledger.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert ledger.revision() is None
    assert set(sa.inspect(ledger.engine).get_table_names()) == {"alembic_version"}


def test_env_refuses_to_run_without_a_connection() -> None:
    config = Config()
    config.set_main_option("script_location", str(repository_module.MIGRATIONS_DIR))
    with pytest.raises(RuntimeError, match=r"Ledger\.migrate"):
        command.upgrade(config, "head")
