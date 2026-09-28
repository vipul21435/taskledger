"""Content-addressed object store and the bundle dedupe cache built on it."""

from taskledger.cache.bundles import BundleCacheResult, cache_bundle, tree_document
from taskledger.cache.store import (
    KEY_PREFIX,
    CacheError,
    CorruptObjectError,
    GcResult,
    InvalidKeyError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStore,
    PutResult,
    normalize_key,
)

__all__ = [
    "KEY_PREFIX",
    "BundleCacheResult",
    "CacheError",
    "CorruptObjectError",
    "GcResult",
    "InvalidKeyError",
    "ObjectInfo",
    "ObjectNotFoundError",
    "ObjectStore",
    "PutResult",
    "cache_bundle",
    "normalize_key",
    "tree_document",
]
