"""Review status state machine.

::

    draft -> submitted -> in_review -> accepted
                 ^                  -> rejected
                 |                  -> needs_changes
                 +---------------------------+

``accepted`` and ``rejected`` are final. Anything else raises
:class:`IllegalTransitionError`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class LedgerError(Exception):
    """Base class of ledger errors."""


class ReviewStatus(StrEnum):
    """Where a task is in review."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"


TRANSITIONS: Final[dict[ReviewStatus, frozenset[ReviewStatus]]] = {
    ReviewStatus.DRAFT: frozenset({ReviewStatus.SUBMITTED}),
    ReviewStatus.SUBMITTED: frozenset({ReviewStatus.IN_REVIEW}),
    ReviewStatus.IN_REVIEW: frozenset(
        {ReviewStatus.ACCEPTED, ReviewStatus.REJECTED, ReviewStatus.NEEDS_CHANGES}
    ),
    ReviewStatus.NEEDS_CHANGES: frozenset({ReviewStatus.SUBMITTED}),
    ReviewStatus.ACCEPTED: frozenset(),
    ReviewStatus.REJECTED: frozenset(),
}

FINAL_STATES: Final = frozenset(state for state, targets in TRANSITIONS.items() if not targets)


class IllegalTransitionError(LedgerError):
    """Raised for a status change the state machine does not allow."""

    def __init__(self, slug: str, current: ReviewStatus, target: ReviewStatus) -> None:
        self.slug = slug
        self.current = current
        self.target = target
        self.allowed = tuple(sorted(TRANSITIONS[current]))
        allowed = ", ".join(self.allowed) or "none, it is final"
        super().__init__(
            f"{slug}: cannot move from {current} to {target} (allowed from {current}: {allowed})"
        )


def check_transition(slug: str, current: ReviewStatus, target: ReviewStatus) -> None:
    """Raise :class:`IllegalTransitionError` unless ``current -> target`` is allowed."""
    if target not in TRANSITIONS[current]:
        raise IllegalTransitionError(slug, current, target)
