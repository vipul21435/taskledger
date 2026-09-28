"""Canonical, Merkle-style content hash of a task bundle.

Two bundles that describe the same task get the same hash, and any change to
what the task *is* changes it.

Cosmetic changes (hash unchanged):

- CRLF and lone CR line endings in text files (normalized to LF);
- ``task.toml`` formatting: comments, whitespace, key and table order, string
  quoting, values spelled out that equal their defaults, the metadata fields
  ``authors``, ``created_at`` and ``notes``, and the ``[lint]`` tooling
  section (linter configuration says nothing about what the task is);
- ignored entries (:data:`DEFAULT_IGNORE`: ``.DS_Store``, ``__pycache__``,
  ``.git`` and similar), empty directories, file modes, timestamps and
  directory listing order;
- the Unicode normalization form of file names (names are compared in NFC).

Semantic changes (hash changes): any other byte of any other file, adding,
removing or renaming a file, retargeting a symlink, and any manifest value
other than the metadata fields and tooling sections.

Algorithm ``tl-merkle-sha256/v1``:

- A file with no NUL byte is text and has its line endings normalized; any
  other file is binary and hashed as is. The root ``task.toml`` is replaced by
  its canonical JSON (validated values, sorted keys, defaults, metadata and
  tooling sections dropped). A symlink contributes its target string and is never followed.
- Each file contributes ``sha256(content)``, so for LF-only text files the
  per-file digest matches ``sha256sum``.
- A directory node is ``sha256(0x01 || entries)`` over its children sorted by
  name, each entry being ``kind || name || 0x00 || child digest`` with kind one
  byte: ``f`` file, ``l`` symlink, ``d`` directory.
- The bundle hash is ``sha256("taskledger-bundle/v1" || 0x00 || root node)``,
  written as ``sha256:<hex>``.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Final, Literal

from taskledger.bundle.loader import Bundle
from taskledger.bundle.manifest import (
    MANIFEST_FILENAME,
    METADATA_FIELDS,
    TOOLING_SECTIONS,
    Manifest,
)

ALGORITHM: Final = "tl-merkle-sha256/v1"

#: Names (fnmatch patterns) skipped anywhere in a bundle. A matching directory
#: is skipped with everything below it.
DEFAULT_IGNORE: Final[frozenset[str]] = frozenset(
    {
        ".DS_Store",
        "._*",
        "Thumbs.db",
        "desktop.ini",
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".ipynb_checkpoints",
    }
)

FileKind = Literal["text", "binary", "symlink", "manifest"]

_CHUNK_SIZE: Final = 1 << 16
_NODE_PREFIX: Final = b"\x01"
_ROOT_TAG: Final = b"taskledger-bundle/v1\x00"
_KIND_BYTE: Final[Mapping[FileKind, bytes]] = {
    "text": b"f",
    "binary": b"f",
    "manifest": b"f",
    "symlink": b"l",
}


class HashError(Exception):
    """Raised when a bundle cannot be hashed (special files, clashing names)."""


@dataclass(frozen=True, slots=True)
class FileDigest:
    """Digest of one bundle entry; ``size`` counts the bytes actually hashed."""

    path: str
    kind: FileKind
    size: int
    sha256: str

    def to_dict(self) -> dict[str, str | int]:
        """JSON-ready representation."""
        return {"path": self.path, "kind": self.kind, "size": self.size, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class BundleDigest:
    """Canonical hash of a whole bundle plus the per-file digests behind it."""

    root: str
    files: tuple[FileDigest, ...]
    algorithm: str = ALGORITHM

    @property
    def value(self) -> str:
        """The bundle hash as ``sha256:<hex>``."""
        return f"sha256:{self.root}"

    def to_dict(self) -> dict[str, object]:
        """JSON-ready representation with files sorted by path."""
        return {
            "algorithm": self.algorithm,
            "hash": self.value,
            "files": [entry.to_dict() for entry in self.files],
        }


def normalize_newlines(data: bytes) -> bytes:
    """Turn CRLF and lone CR into LF."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


class NewlineNormalizer:
    """Incremental :func:`normalize_newlines` for data that arrives in chunks.

    A CR at the end of a chunk is held back until the next chunk shows whether
    it starts a CRLF pair, so any chunking gives the same output.
    """

    def __init__(self) -> None:
        self._pending_cr = False

    def feed(self, chunk: bytes) -> bytes:
        """Normalize the next chunk; may hold back one trailing CR."""
        if self._pending_cr:
            chunk = b"\r" + chunk
            self._pending_cr = False
        if chunk.endswith(b"\r"):
            chunk = chunk[:-1]
            self._pending_cr = True
        return normalize_newlines(chunk)

    def flush(self) -> bytes:
        """Emit a held-back CR (as LF) at end of input."""
        if self._pending_cr:
            self._pending_cr = False
            return b"\n"
        return b""


def canonical_manifest(manifest: Manifest) -> bytes:
    """Canonical JSON of the validated manifest, without defaults, metadata or tooling."""
    exclude: dict[str, set[str] | bool] = {"task": set(METADATA_FIELDS)}
    exclude.update(dict.fromkeys(TOOLING_SECTIONS, True))
    data = manifest.model_dump(mode="json", exclude_defaults=True, exclude=exclude)
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def is_ignored(relative: str, patterns: Collection[str] = DEFAULT_IGNORE) -> bool:
    """True when any component of the POSIX path ``relative`` matches a pattern."""
    return any(fnmatchcase(part, pattern) for part in relative.split("/") for pattern in patterns)


def digest_bytes(path: str, data: bytes) -> FileDigest:
    """Digest in-memory file content, normalizing line endings of text."""
    if b"\x00" in data:
        return FileDigest(path, "binary", len(data), hashlib.sha256(data).hexdigest())
    normalized = normalize_newlines(data)
    return FileDigest(path, "text", len(normalized), hashlib.sha256(normalized).hexdigest())


def digest_manifest(manifest: Manifest) -> FileDigest:
    """Digest of the canonical manifest, reported at ``task.toml``."""
    data = canonical_manifest(manifest)
    return FileDigest(MANIFEST_FILENAME, "manifest", len(data), hashlib.sha256(data).hexdigest())


def digest_symlink(path: str, target: str) -> FileDigest:
    """Digest of a symlink: its target string, never the file it points to."""
    data = os.fsencode(target)
    return FileDigest(path, "symlink", len(data), hashlib.sha256(data).hexdigest())


def _contains_nul(fs_path: Path) -> bool:
    with fs_path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            if b"\x00" in chunk:
                return True
    return False


def digest_file(path: str, fs_path: Path) -> FileDigest:
    """Digest a file on disk in fixed-size chunks (two passes, constant memory)."""
    binary = _contains_nul(fs_path)
    hasher = hashlib.sha256()
    size = 0
    normalizer = NewlineNormalizer()
    with fs_path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            out = chunk if binary else normalizer.feed(chunk)
            hasher.update(out)
            size += len(out)
    tail = normalizer.flush()
    hasher.update(tail)
    size += len(tail)
    return FileDigest(path, "binary" if binary else "text", size, hasher.hexdigest())


type _Tree = dict[str, _Tree | FileDigest]


def _encode_name(name: str) -> bytes:
    return name.encode("utf-8", "surrogateescape")


def _node_digest(tree: _Tree) -> bytes:
    hasher = hashlib.sha256(_NODE_PREFIX)
    for name in sorted(tree, key=_encode_name):
        child = tree[name]
        if isinstance(child, FileDigest):
            kind, digest = _KIND_BYTE[child.kind], bytes.fromhex(child.sha256)
        else:
            kind, digest = b"d", _node_digest(child)
        hasher.update(kind + _encode_name(name) + b"\x00" + digest)
    return hasher.digest()


def merkle_root(files: Iterable[FileDigest]) -> str:
    """Hex bundle hash of a set of file digests; paths must be unique."""
    tree: _Tree = {}
    for entry in files:
        *parents, name = entry.path.split("/")
        node = tree
        for part in parents:
            child = node.setdefault(part, {})
            if isinstance(child, FileDigest):
                raise HashError(f"{entry.path}: parent {child.path} is a file")
            node = child
        if name in node:
            raise HashError(f"{entry.path}: more than one entry has this path")
        node[name] = entry
    return hashlib.sha256(_ROOT_TAG + _node_digest(tree)).hexdigest()


def _assemble(files: list[FileDigest]) -> BundleDigest:
    files.sort(key=lambda entry: _encode_name(entry.path))
    return BundleDigest(root=merkle_root(files), files=tuple(files))


def _canonical_relpath(relative: str) -> str:
    parts = relative.split("/")
    if relative.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise HashError(f"{relative!r}: expected a normalized bundle-relative POSIX path")
    return "/".join(unicodedata.normalize("NFC", part) for part in parts)


def hash_files(
    files: Mapping[str, bytes],
    manifest: Manifest,
    *,
    ignore: Collection[str] = DEFAULT_IGNORE,
) -> BundleDigest:
    """Hash an in-memory bundle: relative POSIX path -> file content.

    A ``task.toml`` entry, if present, is ignored in favour of ``manifest``;
    the result equals :func:`hash_bundle` of the same tree written to disk.
    """
    digests = [digest_manifest(manifest)]
    for relative, data in files.items():
        path = _canonical_relpath(relative)
        if path == MANIFEST_FILENAME or is_ignored(path, ignore):
            continue
        digests.append(digest_bytes(path, data))
    return _assemble(digests)


def _walk(root: Path, ignore: Collection[str]) -> Iterator[FileDigest]:
    pending: list[tuple[str, Path]] = [("", root)]
    while pending:
        prefix, directory = pending.pop()
        with os.scandir(directory) as scan:
            entries = list(scan)
        for entry in entries:
            name = unicodedata.normalize("NFC", entry.name)
            relative = prefix + name
            if is_ignored(name, ignore) or relative == MANIFEST_FILENAME:
                continue
            if entry.is_symlink():
                yield digest_symlink(relative, Path(entry.path).readlink().as_posix())
            elif entry.is_dir(follow_symlinks=False):
                pending.append((relative + "/", Path(entry.path)))
            elif entry.is_file(follow_symlinks=False):
                yield digest_file(relative, Path(entry.path))
            else:
                raise HashError(f"{relative}: unsupported file type (not a file, dir or symlink)")


def hash_bundle(bundle: Bundle, *, ignore: Collection[str] = DEFAULT_IGNORE) -> BundleDigest:
    """Canonical content hash of a loaded bundle on disk."""
    digests = [digest_manifest(bundle.manifest), *_walk(bundle.root, ignore)]
    return _assemble(digests)
