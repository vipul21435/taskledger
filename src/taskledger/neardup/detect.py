"""Near-duplicate tasks: fingerprints of a bundle and an index to search them.

A task's fingerprint is two MinHash signatures, one over its instruction's
word shingles and one over its solution's normalized code shingles. Two tasks
are near-duplicates when either similarity reaches the threshold: a reworded
instruction over the same solution, or a new instruction wrapped around a
renamed copy of an existing solution, are both resubmissions. Candidates come
from two LSH indexes (one per signature) and are confirmed with the signature
estimate, so the index never reports a pair below the threshold.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from taskledger.bundle import Bundle
from taskledger.neardup.lsh import LSHIndex
from taskledger.neardup.minhash import (
    DEFAULT_NUM_PERM,
    DEFAULT_SEED,
    MinHasher,
    Signature,
    estimate,
)
from taskledger.neardup.shingles import code_shingles, word_shingles

#: Default similarity at or above which two tasks are near-duplicates.
DEFAULT_THRESHOLD: Final = 0.5
#: Solution files larger than this are skipped (fixtures, not code).
MAX_SOLUTION_FILE_BYTES: Final = 256 * 1024


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """MinHash signatures of a task's instruction and of its solution."""

    instruction: Signature
    solution: Signature


def fingerprint_texts(
    instruction: str, solution_sources: Iterable[str], hasher: MinHasher
) -> Fingerprint:
    """Fingerprint from an instruction and the text of each solution file."""
    return Fingerprint(
        hasher.signature(word_shingles(instruction)),
        hasher.signature(code_shingles(solution_sources)),
    )


def _solution_sources(directory: Path) -> list[str]:
    sources = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        if path.stat().st_size > MAX_SOLUTION_FILE_BYTES:
            continue
        try:
            sources.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue  # binary files are not code
    return sources


def fingerprint_bundle(bundle: Bundle, hasher: MinHasher) -> Fingerprint:
    """Fingerprint of a loaded bundle's instruction file and solution directory."""
    manifest = bundle.manifest
    instruction = bundle.path(manifest.instruction.path).read_text(encoding="utf-8")
    return fingerprint_texts(
        instruction, _solution_sources(bundle.path(manifest.solution.path)), hasher
    )


@dataclass(frozen=True, slots=True)
class NearDuplicate:
    """An indexed task similar to the query, with both similarities."""

    key: str
    instruction_similarity: float
    solution_similarity: float

    @property
    def similarity(self) -> float:
        """The larger of the two similarities, which decides the match."""
        return max(self.instruction_similarity, self.solution_similarity)


@dataclass
class NearDupIndex:
    """Fingerprints of known tasks, searchable for near-duplicates."""

    threshold: float = DEFAULT_THRESHOLD
    num_perm: int = DEFAULT_NUM_PERM
    seed: int = DEFAULT_SEED
    hasher: MinHasher = field(init=False)
    _instruction: LSHIndex = field(init=False, repr=False)
    _solution: LSHIndex = field(init=False, repr=False)
    _fingerprints: dict[str, Fingerprint] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.hasher = MinHasher(self.num_perm, self.seed)
        self._instruction = LSHIndex(self.threshold, self.num_perm)
        self._solution = LSHIndex(self.threshold, self.num_perm)

    def add(self, key: str, fingerprint: Fingerprint) -> None:
        """Index a task under ``key``."""
        self._instruction.insert(key, fingerprint.instruction)
        self._solution.insert(key, fingerprint.solution)
        self._fingerprints[key] = fingerprint

    def find(self, fingerprint: Fingerprint) -> list[NearDuplicate]:
        """Indexed tasks at or above the threshold, most similar first."""
        candidates = self._instruction.query(fingerprint.instruction) | self._solution.query(
            fingerprint.solution
        )
        found = []
        for key in candidates:
            known = self._fingerprints[key]
            match = NearDuplicate(
                key,
                estimate(fingerprint.instruction, known.instruction),
                estimate(fingerprint.solution, known.solution),
            )
            if match.similarity >= self.threshold:
                found.append(match)
        return sorted(
            found,
            key=lambda m: (-m.similarity, -m.instruction_similarity - m.solution_similarity, m.key),
        )

    @property
    def bands(self) -> int:
        """LSH bands chosen for the threshold."""
        return self._instruction.bands

    @property
    def rows(self) -> int:
        """Signature rows per LSH band."""
        return self._instruction.rows

    def __contains__(self, key: object) -> bool:
        return key in self._fingerprints

    def __len__(self) -> int:
        return len(self._fingerprints)
