"""Near-duplicate signatures persisted in the ledger."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa

from paraphrase_corpus import TASKS, disguise
from taskledger.ledger import ExactCollisionError, Ledger, NearDuplicateError
from taskledger.neardup import Fingerprint, MinHasher, fingerprint_texts

HASHER = MinHasher()


def fp(index: int, *, paraphrase: bool = False) -> Fingerprint:
    task = TASKS[index]
    if paraphrase:
        return fingerprint_texts(task.paraphrase, [disguise(task.solution)], HASHER)
    return fingerprint_texts(task.instruction, [task.solution], HASHER)


def h(index: int) -> str:
    return "sha256:" + f"{index:x}" * 64


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(f"sqlite:///{tmp_path / 'ledger.db'}") as instance:
        instance.migrate()
        yield instance


def register(ledger: Ledger, index: int, **kwargs: object) -> None:
    ledger.register(
        slug=TASKS[index].key,
        version="1.0.0",
        title=TASKS[index].key,
        content_hash=h(index),
        actor="alice",
        fingerprint=kwargs.pop("fingerprint", fp(index)),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_signatures_and_buckets_are_stored(ledger: Ledger) -> None:
    register(ledger, 0)
    with ledger.engine.connect() as conn:
        signatures = conn.execute(sa.text("SELECT instruction, solution FROM task_signatures"))
        [(instruction, solution)] = signatures.all()
        buckets = conn.execute(sa.text("SELECT count(*) FROM lsh_buckets")).scalar_one()
    assert tuple(int(v) for v in instruction.split()) == fp(0).instruction
    assert tuple(int(v) for v in solution.split()) == fp(0).solution
    assert buckets == 2 * 25  # 25 bands for each of the two signatures


def test_near_duplicates_come_from_the_ledger(ledger: Ledger) -> None:
    for index in range(len(TASKS)):
        register(ledger, index)
    for index, task in enumerate(TASKS):
        matches = ledger.near_duplicates(fp(index, paraphrase=True))
        assert [m.key for m in matches] == [task.key]
        assert matches[0].solution_similarity == 1.0
        assert ledger.near_duplicates(fp(index), exclude_slug=task.key) == []


def test_registering_a_near_duplicate_is_refused_unless_allowed(ledger: Ledger) -> None:
    register(ledger, 3)
    with pytest.raises(NearDuplicateError, match=r"near-duplicate: 'copy' is 1\.00 similar to"):
        ledger.register(
            slug="copy",
            version="1.0.0",
            title="copy",
            content_hash=h(13),
            actor="bob",
            fingerprint=fp(3, paraphrase=True),
        )
    # Exact collisions are still reported as such.
    with pytest.raises(ExactCollisionError):
        ledger.register(
            slug="copy",
            version="1.0.0",
            title="copy",
            content_hash=h(3),
            actor="bob",
            fingerprint=fp(3, paraphrase=True),
        )
    ledger.register(
        slug="copy",
        version="1.0.0",
        title="copy",
        content_hash=h(13),
        actor="bob",
        fingerprint=fp(3, paraphrase=True),
        allow_near_dup=True,
    )
    payload = json.loads(ledger.history("copy")[0].payload)
    assert payload["threshold"] == 0.5
    [allowed] = payload["near_duplicates_allowed"]
    assert (allowed["slug"], allowed["solution_similarity"]) == (TASKS[3].key, 1.0)
    assert ledger.verify_chain().ok


def test_a_higher_threshold_lets_a_partial_match_through(ledger: Ledger) -> None:
    register(ledger, 4)
    extended = TASKS[4].solution + "".join(f"\nx{i} = helper_{i}(x{i - 1})" for i in range(1, 4))
    partial = fingerprint_texts("An unrelated instruction.", [extended], HASHER)
    [match] = ledger.near_duplicates(partial)
    assert 0.5 <= match.similarity < 0.95
    assert ledger.near_duplicates(partial, threshold=0.95) == []


def test_revise_replaces_the_buckets(ledger: Ledger) -> None:
    register(ledger, 5)
    ledger.revise(
        slug=TASKS[5].key,
        version="1.1.0",
        title=TASKS[5].key,
        content_hash=h(15),
        actor="alice",
        fingerprint=fp(6),
    )
    assert ledger.near_duplicates(fp(5)) == []
    assert [m.key for m in ledger.near_duplicates(fp(6))] == [TASKS[5].key]
    with ledger.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM task_signatures")).scalar_one() == 2
        assert conn.execute(sa.text("SELECT count(*) FROM lsh_buckets")).scalar_one() == 50


def test_registration_without_a_fingerprint_stores_none(ledger: Ledger) -> None:
    ledger.register(slug="plain", version="1.0.0", title="t", content_hash=h(1), actor="a")
    with ledger.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM task_signatures")).scalar_one() == 0
    assert ledger.near_duplicates(fp(0)) == []
