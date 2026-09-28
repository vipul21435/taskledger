"""Content-addressed object store: layout, atomic writes, verify-on-read, LRU gc."""

from __future__ import annotations

import hashlib
import io
import os
import threading
from pathlib import Path

import pytest

from taskledger.cache import (
    CorruptObjectError,
    InvalidKeyError,
    ObjectNotFoundError,
    ObjectStore,
    normalize_key,
)
from taskledger.cache.store import STALE_TMP_SECONDS


def key_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "cas")


def test_put_bytes_uses_two_hex_fan_out_and_is_read_only(store: ObjectStore) -> None:
    result = store.put_bytes(b"hello\n")
    hexdigest = hashlib.sha256(b"hello\n").hexdigest()
    assert result.key == "sha256:" + hexdigest
    assert result.created
    assert result.size == 6
    path = store.root / "objects" / hexdigest[:2] / hexdigest[2:]
    assert path.read_bytes() == b"hello\n"
    assert store.path_for(result.key) == path
    assert path.stat().st_mode & 0o222 == 0
    assert list(store.tmp_dir.iterdir()) == []


def test_second_put_is_deduplicated(store: ObjectStore) -> None:
    first = store.put_bytes(b"same")
    second = store.put_bytes(b"same")
    streamed = store.put_stream(io.BytesIO(b"same"))
    assert first.created
    assert not second.created
    assert not streamed.created
    assert first.key == second.key == streamed.key
    assert store.size() == (1, 4)
    assert list(store.tmp_dir.iterdir()) == []


def test_put_stream_and_put_file_hash_while_writing(store: ObjectStore, tmp_path: Path) -> None:
    data = os.urandom(200_000)  # several chunks
    source = tmp_path / "blob.bin"
    source.write_bytes(data)
    result = store.put_file(source)
    assert result.key == key_of(data)
    assert result.size == len(data)
    assert store.get(result.key) == data


def test_put_stream_removes_the_temp_file_when_the_reader_fails(store: ObjectStore) -> None:
    class Broken(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
            raise OSError("disk went away")

    with pytest.raises(OSError, match="disk went away"):
        store.put_stream(io.BufferedReader(Broken()))
    assert list(store.tmp_dir.iterdir()) == []
    assert store.size() == (0, 0)


def test_commit_cleans_up_when_the_rename_fails(
    store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Path, target: Path) -> Path:
        raise OSError("rename refused")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="rename refused"):
        store.put_bytes(b"x")
    assert list(store.tmp_dir.iterdir()) == []


def test_concurrent_writers_of_one_key_all_succeed(store: ObjectStore) -> None:
    data = b"racing content\n" * 1000
    errors: list[BaseException] = []

    def write() -> None:
        try:
            assert store.put_bytes(data).key == key_of(data)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert store.get(key_of(data)) == data
    assert store.size() == (1, len(data))


def test_get_verifies_content_and_removes_corrupt_objects(store: ObjectStore) -> None:
    key = store.put_bytes(b"original").key
    path = store.path_for(key)
    path.chmod(0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(CorruptObjectError) as info:
        store.get(key)
    assert info.value.key == key
    assert info.value.actual == key_of(b"tampered")
    assert not store.has(key)
    # It can be stored again after the corrupt copy was dropped.
    assert store.put_bytes(b"original").created
    assert store.get(key) == b"original"


def test_get_missing_object(store: ObjectStore) -> None:
    missing = key_of(b"never stored")
    with pytest.raises(ObjectNotFoundError) as info:
        store.get(missing)
    assert str(info.value) == f"no object {missing}"
    assert not store.has(missing)


@pytest.mark.parametrize(
    "bad", ["", "sha256:", "sha256:" + "A" * 64, "md5:" + "a" * 32, "a" * 63, "../" + "a" * 61]
)
def test_invalid_keys_are_rejected(store: ObjectStore, bad: str) -> None:
    with pytest.raises(InvalidKeyError):
        normalize_key(bad)
    with pytest.raises(InvalidKeyError):
        store.has(bad)


def test_keys_accept_bare_hex(store: ObjectStore) -> None:
    key = store.put_bytes(b"x").key
    bare = key.removeprefix("sha256:")
    assert normalize_key(bare) == key
    assert store.has(bare)
    assert store.get(bare) == b"x"


def test_objects_ignores_foreign_files(store: ObjectStore) -> None:
    key = store.put_bytes(b"x").key
    (store.objects_dir / "README").write_text("not an object")
    (store.objects_dir / "zz").mkdir()
    (store.objects_dir / "zz" / "nothex").write_text("not an object")
    assert [info.key for info in store.objects()] == [key]
    assert list(ObjectStore(store.root / "empty").objects()) == []


def _age(store: ObjectStore, key: str, mtime: float) -> None:
    os.utime(store.path_for(key), (mtime, mtime))


def test_gc_evicts_least_recently_used_first(store: ObjectStore) -> None:
    keys = [store.put_bytes(bytes([n]) * 100).key for n in range(5)]
    for index, key in enumerate(keys):
        _age(store, key, 1_000_000 + index)
    # Reading the oldest object makes it the most recently used.
    store.get(keys[0])
    result = store.gc(300, now=2_000_000)
    assert result.removed == 2
    assert result.freed_bytes == 200
    assert result.kept == 3
    assert result.kept_bytes == 300
    assert sorted(info.key for info in store.objects()) == sorted([keys[0], keys[3], keys[4]])


def test_repeated_put_refreshes_lru_position(store: ObjectStore) -> None:
    old = store.put_bytes(b"a" * 10).key
    new = store.put_bytes(b"b" * 10).key
    _age(store, old, 1_000_000)
    _age(store, new, 1_000_001)
    store.put_bytes(b"a" * 10)
    store.gc(10)
    assert store.has(old)
    assert not store.has(new)


def test_gc_within_budget_removes_nothing_and_zero_budget_empties(store: ObjectStore) -> None:
    for n in range(3):
        store.put_bytes(bytes([n]))
    assert store.gc(10).removed == 0
    result = store.gc(0)
    assert (result.removed, result.kept, result.kept_bytes) == (3, 0, 0)
    assert result.to_dict()["freed_bytes"] == 3
    with pytest.raises(ValueError, match="max_bytes"):
        store.gc(-1)


def test_gc_removes_only_stale_temp_files(store: ObjectStore) -> None:
    store.put_bytes(b"x")
    stale = store.tmp_dir / "put-crashed"
    fresh = store.tmp_dir / "put-running"
    stale.write_bytes(b"partial")
    fresh.write_bytes(b"partial")
    now = stale.stat().st_mtime + STALE_TMP_SECONDS + 1
    os.utime(fresh, (now, now))
    result = store.gc(1 << 20, now=now)
    assert result.stale_tmp_removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_gc_on_an_empty_store(tmp_path: Path) -> None:
    result = ObjectStore(tmp_path / "nothing").gc(0)
    assert result.to_dict() == {
        "removed": 0,
        "freed_bytes": 0,
        "kept": 0,
        "kept_bytes": 0,
        "stale_tmp_removed": 0,
    }
