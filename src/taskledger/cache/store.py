"""Content-addressed object store used as the dedupe cache.

Every object is stored under the sha256 of its bytes, so storing the same
content twice costs one lookup and no write, and a key names exactly one
content forever.

Layout under the store root::

    objects/ab/cdef...   object with key sha256:abcdef... (two hex fan-out)
    tmp/                 in-flight writes, renamed into objects/ when complete

Guarantees:

- **Atomic writes.** Content is streamed into a temp file in ``tmp/`` (same
  file system), hashed while it is written, fsynced and moved into place
  with :func:`os.replace`. Readers never see a partial object, and two
  writers racing on one key both succeed with identical bytes.
- **Verify on read.** :meth:`ObjectStore.get` rehashes what it read; an
  object whose bytes no longer match its key is deleted and reported with
  :class:`CorruptObjectError`, so it can be stored again.
- **LRU garbage collection.** Reads and repeated writes refresh an object's
  modification time; :meth:`ObjectStore.gc` deletes the least recently used
  objects until the store fits a byte budget, plus temp files abandoned by
  crashed writers.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

KEY_PREFIX: Final = "sha256:"
_HEX = re.compile(r"[0-9a-f]{64}")
_CHUNK_SIZE: Final = 1 << 16
#: Temp files older than this (seconds) belong to a crashed writer.
STALE_TMP_SECONDS: Final = 3600.0


class CacheError(Exception):
    """Base class of object store errors."""


class InvalidKeyError(CacheError, ValueError):
    """Raised for a key that is not ``sha256:`` plus 64 lowercase hex digits."""


class ObjectNotFoundError(CacheError, KeyError):
    """Raised when no object is stored under a key."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)

    def __str__(self) -> str:
        return f"no object {self.key}"


class CorruptObjectError(CacheError):
    """Raised when an object's bytes no longer hash to its key (it is removed)."""

    def __init__(self, key: str, actual: str) -> None:
        self.key = key
        self.actual = actual
        super().__init__(f"object {key} is corrupt (content hashes to {actual}); removed")


def normalize_key(key: str) -> str:
    """Return ``sha256:<hex>`` for a key given with or without its prefix."""
    hexdigest = key.removeprefix(KEY_PREFIX)
    if not _HEX.fullmatch(hexdigest):
        raise InvalidKeyError(f"not a sha256 object key: {key!r}")
    return KEY_PREFIX + hexdigest


@dataclass(frozen=True, slots=True)
class PutResult:
    """Outcome of storing one object."""

    key: str
    size: int
    created: bool


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """One stored object as seen by :meth:`ObjectStore.objects`."""

    key: str
    size: int
    last_used: float


@dataclass(frozen=True, slots=True)
class GcResult:
    """What a garbage collection pass removed and kept."""

    removed: int
    freed_bytes: int
    kept: int
    kept_bytes: int
    stale_tmp_removed: int

    def to_dict(self) -> dict[str, int]:
        """JSON-ready representation."""
        return {
            "removed": self.removed,
            "freed_bytes": self.freed_bytes,
            "kept": self.kept,
            "kept_bytes": self.kept_bytes,
            "stale_tmp_removed": self.stale_tmp_removed,
        }


class ObjectStore:
    """A directory of immutable objects addressed by the sha256 of their bytes."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.objects_dir = self.root / "objects"
        self.tmp_dir = self.root / "tmp"

    # -- paths -----------------------------------------------------------------

    def path_for(self, key: str) -> Path:
        """Where the object with ``key`` lives (whether or not it exists)."""
        hexdigest = normalize_key(key).removeprefix(KEY_PREFIX)
        return self.objects_dir / hexdigest[:2] / hexdigest[2:]

    def _ensure_dirs(self) -> None:
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    # -- writes ----------------------------------------------------------------

    def put_bytes(self, data: bytes) -> PutResult:
        """Store ``data`` and return its key; a no-op write if it is already stored."""
        key = KEY_PREFIX + hashlib.sha256(data).hexdigest()
        target = self.path_for(key)
        if _touch(target):
            return PutResult(key, len(data), created=False)
        self._ensure_dirs()
        fd, tmp_name = tempfile.mkstemp(dir=self.tmp_dir, prefix="put-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return self._commit(Path(tmp_name), key, len(data))

    def put_stream(self, stream: BinaryIO) -> PutResult:
        """Store everything read from ``stream``, hashing it while it is written."""
        self._ensure_dirs()
        hasher = hashlib.sha256()
        size = 0
        fd, tmp_name = tempfile.mkstemp(dir=self.tmp_dir, prefix="put-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                while chunk := stream.read(_CHUNK_SIZE):
                    hasher.update(chunk)
                    handle.write(chunk)
                    size += len(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        key = KEY_PREFIX + hasher.hexdigest()
        if _touch(self.path_for(key)):
            tmp.unlink()
            return PutResult(key, size, created=False)
        return self._commit(tmp, key, size)

    def put_file(self, path: Path | str) -> PutResult:
        """Store the bytes of a file on disk (streamed, constant memory)."""
        with Path(path).open("rb") as handle:
            return self.put_stream(handle)

    def _commit(self, tmp: Path, key: str, size: int) -> PutResult:
        try:
            with tmp.open("rb+") as handle:
                os.fsync(handle.fileno())
            tmp.chmod(0o444)
            target = self.path_for(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            created = not target.exists()
            tmp.replace(target)  # os.replace: an atomic rename on POSIX
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return PutResult(key, size, created=created)

    # -- reads -----------------------------------------------------------------

    def has(self, key: str) -> bool:
        """True when an object is stored under ``key`` (does not verify it)."""
        return self.path_for(key).is_file()

    def get(self, key: str) -> bytes:
        """Read and verify an object; refreshes its LRU position."""
        key = normalize_key(key)
        path = self.path_for(key)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise ObjectNotFoundError(key) from None
        actual = KEY_PREFIX + hashlib.sha256(data).hexdigest()
        if actual != key:
            path.unlink(missing_ok=True)
            raise CorruptObjectError(key, actual)
        _touch(path)
        return data

    def objects(self) -> Iterator[ObjectInfo]:
        """Every stored object, in no particular order."""
        if not self.objects_dir.is_dir():
            return
        for fan in sorted(self.objects_dir.iterdir()):
            if not fan.is_dir() or len(fan.name) != 2:
                continue
            for entry in sorted(fan.iterdir()):
                hexdigest = fan.name + entry.name
                if not _HEX.fullmatch(hexdigest):
                    continue
                with contextlib.suppress(FileNotFoundError):
                    stat = entry.stat()
                    yield ObjectInfo(KEY_PREFIX + hexdigest, stat.st_size, stat.st_mtime)

    def size(self) -> tuple[int, int]:
        """``(object count, total bytes)`` currently stored."""
        infos = list(self.objects())
        return len(infos), sum(info.size for info in infos)

    # -- garbage collection ----------------------------------------------------

    def gc(self, max_bytes: int, *, now: float | None = None) -> GcResult:
        """Delete least recently used objects until at most ``max_bytes`` remain.

        Temp files older than :data:`STALE_TMP_SECONDS` are removed too; younger
        ones may belong to a writer that is still running.
        """
        if max_bytes < 0:
            raise ValueError("max_bytes must be >= 0")
        now = time.time() if now is None else now
        stale = 0
        if self.tmp_dir.is_dir():
            for entry in self.tmp_dir.iterdir():
                with contextlib.suppress(FileNotFoundError):
                    if now - entry.stat().st_mtime > STALE_TMP_SECONDS:
                        entry.unlink()
                        stale += 1
        # Oldest first; the key breaks ties so a pass is deterministic.
        infos = sorted(self.objects(), key=lambda info: (info.last_used, info.key))
        total = sum(info.size for info in infos)
        removed = freed = 0
        for info in infos:
            if total <= max_bytes:
                break
            with contextlib.suppress(FileNotFoundError):
                self.path_for(info.key).unlink()
                removed += 1
                freed += info.size
            total -= info.size
        return GcResult(
            removed=removed,
            freed_bytes=freed,
            kept=len(infos) - removed,
            kept_bytes=total,
            stale_tmp_removed=stale,
        )


def _touch(path: Path) -> bool:
    """Refresh the LRU time of an existing object; False when it does not exist."""
    try:
        os.utime(path)
    except FileNotFoundError:
        return False
    return True
