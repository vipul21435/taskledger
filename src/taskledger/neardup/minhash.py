"""Seeded MinHash in pure Python.

Each shingle is hashed to a 61-bit integer ``x`` with BLAKE2b (stable across
processes, platforms and ``PYTHONHASHSEED``), then pushed through ``num_perm``
universal hash functions ``h_i(x) = (a_i * x + b_i) mod (2**61 - 1)`` whose
coefficients are derived from ``seed`` with BLAKE2b as well. The signature is
the minimum of each ``h_i`` over the set; the fraction of positions where two
signatures agree is an unbiased estimate of the Jaccard similarity of the sets,
with standard error ``sqrt(J * (1 - J) / num_perm)``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

#: The Mersenne prime 2**61 - 1, the modulus of the hash family.
PRIME: Final = (1 << 61) - 1
#: Signature value of an empty set (larger than any hash).
EMPTY: Final = PRIME
DEFAULT_NUM_PERM: Final = 128
DEFAULT_SEED: Final = 1

Signature = tuple[int, ...]


def _hash64(data: bytes, *, key: bytes = b"") -> int:
    return int.from_bytes(hashlib.blake2b(data, digest_size=8, key=key).digest(), "big")


def shingle_hash(shingle: str) -> int:
    """Stable 61-bit hash of one shingle."""
    return _hash64(shingle.encode("utf-8")) % PRIME


def jaccard(a: set[str], b: set[str]) -> float:
    """Exact Jaccard similarity; two empty sets are identical (1.0)."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class MinHasher:
    """``num_perm`` seeded hash functions and the signatures they produce."""

    num_perm: int = DEFAULT_NUM_PERM
    seed: int = DEFAULT_SEED
    _coefficients: tuple[tuple[int, int], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.num_perm < 1:
            raise ValueError("num_perm must be at least 1")
        key = self.seed.to_bytes(8, "big", signed=True)
        coefficients = []
        for i in range(self.num_perm):
            a = _hash64(b"a%d" % i, key=key) % (PRIME - 1) + 1
            b = _hash64(b"b%d" % i, key=key) % PRIME
            coefficients.append((a, b))
        object.__setattr__(self, "_coefficients", tuple(coefficients))

    def signature(self, shingles: Iterable[str]) -> Signature:
        """MinHash signature of a set of shingles."""
        hashes = {shingle_hash(shingle) for shingle in shingles}
        if not hashes:
            return (EMPTY,) * self.num_perm
        return tuple(min((a * x + b) % PRIME for x in hashes) for a, b in self._coefficients)


def estimate(a: Sequence[int], b: Sequence[int]) -> float:
    """Estimated Jaccard similarity of the sets behind two signatures."""
    if len(a) != len(b):
        raise ValueError(f"signatures differ in length: {len(a)} != {len(b)}")
    if not a:
        raise ValueError("signatures are empty")
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)
