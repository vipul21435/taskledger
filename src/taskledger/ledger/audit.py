"""Hash chain of the audit log.

Each row's hash covers its own columns and the previous row's hash, so
editing, deleting, inserting or reordering any row breaks every hash after
it. The database blocks UPDATE and DELETE with triggers; :func:`verify_chain`
catches what the triggers cannot (a dropped trigger, a restored backup, a
hand-edited file).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from taskledger.ledger.models import AuditEntry

GENESIS_HASH: Final = "0" * 64


def canonical_json(value: object) -> str:
    """Sorted keys, no whitespace, ASCII only: the same bytes on every machine."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def row_hash(
    *,
    seq: int,
    at: str,
    actor: str,
    action: str,
    task_slug: str | None,
    payload: str,
    prev_hash: str,
) -> str:
    """sha256 hex of one audit row's canonical form."""
    document = {
        "seq": seq,
        "at": at,
        "actor": actor,
        "action": action,
        "task": task_slug,
        "payload": payload,
        "prev": prev_hash,
    }
    return hashlib.sha256(canonical_json(document).encode()).hexdigest()


def entry_hash(entry: AuditEntry) -> str:
    """Recompute the hash a stored row should carry."""
    return row_hash(
        seq=entry.seq,
        at=entry.at,
        actor=entry.actor,
        action=entry.action,
        task_slug=entry.task_slug,
        payload=entry.payload,
        prev_hash=entry.prev_hash,
    )


def new_entry(
    *,
    prev: AuditEntry | None,
    at: str,
    actor: str,
    action: str,
    task_slug: str | None,
    payload: Mapping[str, object],
) -> AuditEntry:
    """Build the row that follows ``prev`` (None for the first row)."""
    seq = 1 if prev is None else prev.seq + 1
    prev_hash = GENESIS_HASH if prev is None else prev.row_hash
    body = canonical_json(dict(payload))
    return AuditEntry(
        seq=seq,
        at=at,
        actor=actor,
        action=action,
        task_slug=task_slug,
        payload=body,
        prev_hash=prev_hash,
        row_hash=row_hash(
            seq=seq,
            at=at,
            actor=actor,
            action=action,
            task_slug=task_slug,
            payload=body,
            prev_hash=prev_hash,
        ),
    )


@dataclass(frozen=True, slots=True)
class ChainReport:
    """Result of :func:`verify_chain`."""

    ok: bool
    entries: int
    head: str
    first_bad_seq: int | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        """JSON-ready representation."""
        return {
            "ok": self.ok,
            "entries": self.entries,
            "head": self.head,
            "first_bad_seq": self.first_bad_seq,
            "reason": self.reason,
        }


def verify_chain(entries: Iterable[AuditEntry]) -> ChainReport:
    """Check sequence numbers, links and row hashes of entries in ``seq`` order."""
    prev_hash = GENESIS_HASH
    count = 0
    for expected_seq, entry in enumerate(entries, start=1):
        problem = None
        if entry.seq != expected_seq:
            problem = f"expected seq {expected_seq}, found {entry.seq} (row missing or inserted)"
        elif entry.prev_hash != prev_hash:
            problem = "prev_hash does not match the previous row's hash"
        elif entry_hash(entry) != entry.row_hash:
            problem = "row content does not match its hash (row edited)"
        if problem is not None:
            return ChainReport(False, count, prev_hash, first_bad_seq=entry.seq, reason=problem)
        prev_hash = entry.row_hash
        count += 1
    return ChainReport(True, count, prev_hash)
