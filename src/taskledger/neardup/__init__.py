"""Near-duplicate detection: shingles, seeded MinHash and LSH."""

from taskledger.neardup.detect import (
    DEFAULT_THRESHOLD,
    Fingerprint,
    NearDupIndex,
    NearDuplicate,
    fingerprint_bundle,
    fingerprint_texts,
)
from taskledger.neardup.lsh import LSHIndex, collision_probability, optimal_params
from taskledger.neardup.minhash import MinHasher, Signature, estimate, jaccard
from taskledger.neardup.shingles import code_shingles, code_tokens, word_shingles

__all__ = [
    "DEFAULT_THRESHOLD",
    "Fingerprint",
    "LSHIndex",
    "MinHasher",
    "NearDupIndex",
    "NearDuplicate",
    "Signature",
    "code_shingles",
    "code_tokens",
    "collision_probability",
    "estimate",
    "fingerprint_bundle",
    "fingerprint_texts",
    "jaccard",
    "optimal_params",
    "word_shingles",
]
