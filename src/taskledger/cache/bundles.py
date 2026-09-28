"""Store a task bundle in the object store, deduplicated file by file.

Each entry of the canonical hash (the canonical manifest JSON, LF-normalized
text, binary bytes, symlink targets) is stored under its per-file digest, so
the object key of a file is exactly the digest the bundle hash is built from.
A tree object (canonical JSON of the bundle hash and its entries) ties them
together. Storing an identical bundle again writes nothing: every key is
already present, which is how the cache recognizes a resubmission without
re-running anything.
"""

from __future__ import annotations

import json
import os
from collections.abc import Collection
from dataclasses import dataclass

from taskledger.bundle import DEFAULT_IGNORE, Bundle, FileDigest, hash_bundle
from taskledger.bundle.hashing import canonical_manifest, normalize_newlines
from taskledger.cache.store import KEY_PREFIX, CacheError, ObjectStore

TREE_FORMAT = "taskledger-tree/v1"


@dataclass(frozen=True, slots=True)
class BundleCacheResult:
    """Outcome of :func:`cache_bundle`."""

    bundle_hash: str
    tree_key: str
    objects: int
    new_objects: int
    bytes_total: int
    bytes_written: int

    @property
    def already_cached(self) -> bool:
        """True when every object, tree included, was already in the store."""
        return self.new_objects == 0

    def to_dict(self) -> dict[str, object]:
        """JSON-ready representation."""
        return {
            "bundle_hash": self.bundle_hash,
            "tree": self.tree_key,
            "objects": self.objects,
            "new_objects": self.new_objects,
            "bytes_total": self.bytes_total,
            "bytes_written": self.bytes_written,
            "already_cached": self.already_cached,
        }


def _canonical_content(bundle: Bundle, entry: FileDigest) -> bytes:
    if entry.kind == "manifest":
        return canonical_manifest(bundle.manifest)
    path = bundle.path(entry.path)
    if entry.kind == "symlink":
        return os.fsencode(path.readlink().as_posix())
    data = path.read_bytes()
    return data if entry.kind == "binary" else normalize_newlines(data)


def tree_document(bundle_hash: str, files: tuple[FileDigest, ...]) -> bytes:
    """Canonical JSON of a bundle tree (sorted keys, no whitespace, trailing LF)."""
    document = {
        "format": TREE_FORMAT,
        "bundle_hash": bundle_hash,
        "entries": [
            {"path": entry.path, "kind": entry.kind, "object": KEY_PREFIX + entry.sha256}
            for entry in files
        ],
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def cache_bundle(
    store: ObjectStore, bundle: Bundle, *, ignore: Collection[str] = DEFAULT_IGNORE
) -> BundleCacheResult:
    """Store every canonical entry of ``bundle`` plus its tree object."""
    digest = hash_bundle(bundle, ignore=ignore)
    new = written = total = 0
    for entry in digest.files:
        data = _canonical_content(bundle, entry)
        result = store.put_bytes(data)
        if result.key != KEY_PREFIX + entry.sha256:
            # The file changed between hashing and reading it.
            raise CacheError(f"{entry.path}: content changed while it was being cached")
        total += result.size
        if result.created:
            new += 1
            written += result.size
    tree = store.put_bytes(tree_document(digest.value, digest.files))
    total += tree.size
    if tree.created:
        new += 1
        written += tree.size
    return BundleCacheResult(
        bundle_hash=digest.value,
        tree_key=tree.key,
        objects=len(digest.files) + 1,
        new_objects=new,
        bytes_total=total,
        bytes_written=written,
    )
