"""Locality-sensitive hashing over MinHash signatures.

A signature of ``bands * rows`` values is cut into ``bands`` bands of ``rows``
values. Two sets with Jaccard similarity ``s`` share at least one band with
probability ``1 - (1 - s**rows)**bands``, an S-curve. :func:`optimal_params`
picks the band/row split that minimizes the weighted area of false positives
(the curve below the threshold) plus false negatives (the gap above it).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

#: Integration steps for the S-curve areas.
_STEPS: Final = 200


def collision_probability(similarity: float, bands: int, rows: int) -> float:
    """Chance that two sets of this Jaccard similarity share a band."""
    return 1.0 - (1.0 - similarity**rows) ** bands


def _integrate(bands: int, rows: int, low: float, high: float, *, miss: bool) -> float:
    if high <= low:
        return 0.0
    width = (high - low) / _STEPS
    total = 0.0
    for i in range(_STEPS + 1):
        s = low + i * width
        p = collision_probability(s, bands, rows)
        value = 1.0 - p if miss else p
        total += value * (0.5 if i in (0, _STEPS) else 1.0)
    return total * width


def false_positive_area(threshold: float, bands: int, rows: int) -> float:
    """Area under the S-curve below the threshold."""
    return _integrate(bands, rows, 0.0, threshold, miss=False)


def false_negative_area(threshold: float, bands: int, rows: int) -> float:
    """Area above the S-curve at or above the threshold."""
    return _integrate(bands, rows, threshold, 1.0, miss=True)


def optimal_params(
    threshold: float,
    num_perm: int,
    *,
    fp_weight: float = 0.5,
    fn_weight: float = 0.5,
) -> tuple[int, int]:
    """``(bands, rows)`` with ``bands * rows <= num_perm`` minimizing weighted error."""
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be strictly between 0 and 1")
    if num_perm < 1:
        raise ValueError("num_perm must be at least 1")
    best = (float("inf"), 1, num_perm)
    for bands in range(1, num_perm + 1):
        for rows in range(1, num_perm // bands + 1):
            error = fp_weight * false_positive_area(
                threshold, bands, rows
            ) + fn_weight * false_negative_area(threshold, bands, rows)
            if error < best[0]:
                best = (error, bands, rows)
    return best[1], best[2]


@dataclass
class LSHIndex:
    """Band buckets from signature slices to the keys that produced them."""

    threshold: float
    num_perm: int
    bands: int = field(init=False)
    rows: int = field(init=False)
    _buckets: dict[str, set[str]] = field(
        init=False, repr=False, default_factory=lambda: defaultdict(set)
    )
    _keys: dict[str, tuple[str, ...]] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.bands, self.rows = optimal_params(self.threshold, self.num_perm)

    def band_keys(self, signature: Sequence[int]) -> tuple[str, ...]:
        """One stable bucket key per band: band number plus a digest of its rows."""
        if len(signature) < self.bands * self.rows:
            raise ValueError(
                f"signature has {len(signature)} values, the index needs {self.bands * self.rows}"
            )
        keys = []
        for band in range(self.bands):
            chunk = signature[band * self.rows : (band + 1) * self.rows]
            data = b"".join(value.to_bytes(8, "big") for value in chunk)
            keys.append(f"{band}:{hashlib.blake2b(data, digest_size=8).hexdigest()}")
        return tuple(keys)

    def insert(self, key: str, signature: Sequence[int]) -> None:
        """Add ``key``; inserting the same key twice is an error."""
        if key in self._keys:
            raise KeyError(f"{key!r} is already in the index")
        band_keys = self.band_keys(signature)
        self._keys[key] = band_keys
        for bucket in band_keys:
            self._buckets[bucket].add(key)

    def query(self, signature: Sequence[int]) -> set[str]:
        """Keys sharing at least one band with ``signature`` (candidates only)."""
        found: set[str] = set()
        for bucket in self.band_keys(signature):
            found |= self._buckets.get(bucket, set())
        return found

    def __len__(self) -> int:
        return len(self._keys)
