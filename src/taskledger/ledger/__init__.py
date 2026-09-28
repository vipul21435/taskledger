"""Shared ledger: tasks, submissions, content hashes and a hash-chained audit log."""

from taskledger.ledger.audit import GENESIS_HASH, ChainReport, verify_chain
from taskledger.ledger.repository import (
    HEAD_REVISION,
    AuditRecord,
    ExactCollisionError,
    IdCollisionError,
    Ledger,
    TaskNotFoundError,
    TaskRecord,
    create_ledger_engine,
    fold_id,
)
from taskledger.ledger.states import (
    FINAL_STATES,
    TRANSITIONS,
    IllegalTransitionError,
    LedgerError,
    ReviewStatus,
    check_transition,
)

__all__ = [
    "FINAL_STATES",
    "GENESIS_HASH",
    "HEAD_REVISION",
    "TRANSITIONS",
    "AuditRecord",
    "ChainReport",
    "ExactCollisionError",
    "IdCollisionError",
    "IllegalTransitionError",
    "Ledger",
    "LedgerError",
    "ReviewStatus",
    "TaskNotFoundError",
    "TaskRecord",
    "check_transition",
    "create_ledger_engine",
    "fold_id",
    "verify_chain",
]
