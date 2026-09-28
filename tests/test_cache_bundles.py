"""Bundle dedupe cache and the ``taskledger cache`` commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from conftest import BundleFactory
from taskledger.bundle import hash_bundle, load_bundle
from taskledger.cache import CacheError, ObjectStore, cache_bundle, tree_document
from taskledger.cli import app
from taskledger.settings import Settings, parse_size

runner = CliRunner()


def test_bundle_objects_are_keyed_by_their_canonical_file_digests(
    make_bundle: BundleFactory, tmp_path: Path
) -> None:
    root = make_bundle(files={"tests/data.bin": b"\x00\x01\x02", "notes.txt": "a\r\nb\r\n"})
    (root / "link").symlink_to("notes.txt")
    bundle = load_bundle(root).unwrap()
    store = ObjectStore(tmp_path / "cas")
    result = cache_bundle(store, bundle)
    digest = hash_bundle(bundle)
    assert result.bundle_hash == digest.value
    assert result.objects == len(digest.files) + 1
    assert result.new_objects == result.objects
    assert not result.already_cached
    for entry in digest.files:
        assert store.has(entry.sha256)
    assert store.get(hashlib.sha256(b"a\nb\n").hexdigest()) == b"a\nb\n"
    tree = json.loads(store.get(result.tree_key))
    assert tree["bundle_hash"] == digest.value
    assert {
        "path": "link",
        "kind": "symlink",
        "object": "sha256:" + hashlib.sha256(b"notes.txt").hexdigest(),
    } in tree["entries"]
    assert store.get(result.tree_key) == tree_document(digest.value, digest.files)


def test_identical_and_cosmetically_different_bundles_write_nothing(
    make_bundle: BundleFactory, tmp_path: Path
) -> None:
    store = ObjectStore(tmp_path / "cas")
    first = cache_bundle(store, load_bundle(make_bundle(name="a")).unwrap())
    crlf = {
        "instruction.md": "Write the sum of squares of /app/numbers.txt to /app/answer.txt.\r\n"
    }
    again = cache_bundle(store, load_bundle(make_bundle(name="b", files=crlf)).unwrap())
    assert again.already_cached
    assert again.bytes_written == 0
    assert again.tree_key == first.tree_key
    # One changed file adds that file and a new tree, nothing else.
    changed = make_bundle(
        name="c", files={"tests/test_answer.py": "def test_x() -> None:\n    assert True\n"}
    )
    third = cache_bundle(store, load_bundle(changed).unwrap())
    assert third.new_objects == 2
    assert third.tree_key != first.tree_key


def test_cache_bundle_detects_a_file_changing_underneath(
    make_bundle: BundleFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = load_bundle(make_bundle()).unwrap()
    real = Path.read_bytes

    def drifting(self: Path) -> bytes:
        return real(self) + b"late edit\n"

    monkeypatch.setattr(Path, "read_bytes", drifting)
    with pytest.raises(CacheError, match="content changed while it was being cached"):
        cache_bundle(ObjectStore(tmp_path / "cas"), bundle)


def test_settings_from_env(tmp_path: Path) -> None:
    default = Settings.from_env({})
    assert default.home == Path(".taskledger")
    assert default.cache_dir == Path(".taskledger/cache")
    assert default.database_url == "sqlite:///.taskledger/ledger.db"
    custom = Settings.from_env(
        {"TASKLEDGER_HOME": str(tmp_path), "TASKLEDGER_DATABASE_URL": "postgresql+psycopg://x/y"}
    )
    assert custom.cache_dir == tmp_path / "cache"
    assert custom.database_url == "postgresql+psycopg://x/y"


@pytest.mark.parametrize("name", ["pct%41dir", "q?x", "at@sign", "hash#mark", "sp ace"])
def test_default_ledger_url_keeps_special_characters_in_the_home_path(
    tmp_path: Path, name: str
) -> None:
    home = tmp_path / name
    url = sa.engine.make_url(Settings.from_env({"TASKLEDGER_HOME": str(home)}).database_url)
    assert url.drivername == "sqlite"
    assert url.database == (home / "ledger.db").as_posix()
    assert not url.query


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("1048576", 1048576),
        ("512K", 524288),
        ("100MB", 104857600),
        ("2GiB", 2 << 30),
        (" 3 kb ", 3072),
    ],
)
def test_parse_size(text: str, expected: int) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "ten", "-5", "5 XB", "1.5M"])
def test_parse_size_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError, match="not a size"):
        parse_size(text)


def test_cli_put_get_has_gc_round_trip(make_bundle: BundleFactory, tmp_path: Path) -> None:
    cas = tmp_path / "cas"
    bundle = make_bundle()
    blob = tmp_path / "blob.txt"
    blob.write_bytes(b"plain file\n")

    first = runner.invoke(app, ["cache", "put", "--cache-dir", str(cas), str(bundle), str(blob)])
    assert first.exit_code == 0, first.output
    tree_line, blob_line = first.stdout.splitlines()
    assert "(stored: bundle sha256:" in tree_line
    assert "7 of 7 objects new" in tree_line
    blob_key = "sha256:" + hashlib.sha256(b"plain file\n").hexdigest()
    assert blob_line == f"{blob_key}  {blob}  (stored: 11 bytes)"

    again = runner.invoke(
        app, ["cache", "put", "--cache-dir", str(cas), "--json", str(bundle), str(blob)]
    )
    assert again.exit_code == 0, again.output
    records = json.loads(again.stdout)
    assert records[0]["already_cached"] is True
    assert records[0]["bytes_written"] == 0
    assert records[1] == {
        "path": str(blob),
        "type": "file",
        "key": blob_key,
        "size": 11,
        "created": False,
    }

    got = runner.invoke(app, ["cache", "get", "--cache-dir", str(cas), blob_key])
    assert got.exit_code == 0
    assert got.stdout_bytes == b"plain file\n"
    out = tmp_path / "out.txt"
    assert (
        runner.invoke(
            app, ["cache", "get", "--cache-dir", str(cas), blob_key, "-o", str(out)]
        ).exit_code
        == 0
    )
    assert out.read_bytes() == b"plain file\n"
    unwritable = runner.invoke(
        app,
        ["cache", "get", "--cache-dir", str(cas), blob_key, "-o", str(tmp_path / "nodir" / "o")],
    )
    assert unwritable.exit_code == 1
    assert unwritable.exception is None or isinstance(unwritable.exception, SystemExit)
    assert "error: cannot write " in unwritable.stderr

    missing = "sha256:" + "0" * 64
    has = runner.invoke(app, ["cache", "has", "--cache-dir", str(cas), blob_key, missing])
    assert has.exit_code == 1
    assert has.stdout.splitlines() == [f"present  {blob_key}", f"missing  {missing}"]
    assert runner.invoke(app, ["cache", "has", "--cache-dir", str(cas), blob_key]).exit_code == 0

    gc = runner.invoke(app, ["cache", "gc", "--cache-dir", str(cas), "--max-size", "0", "--json"])
    assert gc.exit_code == 0, gc.output
    assert json.loads(gc.stdout)["kept"] == 0
    text = runner.invoke(app, ["cache", "gc", "--cache-dir", str(cas), "--max-size", "1M"])
    assert text.stdout.startswith(
        "removed 0 object(s), freed 0 bytes; kept 0 object(s), 0 bytes (budget 1048576)"
    )


def test_cli_errors(
    make_bundle: BundleFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TASKLEDGER_HOME", str(tmp_path / "home"))
    missing = runner.invoke(app, ["cache", "get", "sha256:" + "0" * 64])
    assert missing.exit_code == 1
    assert "error: no object sha256:" in missing.stderr
    bad_key = runner.invoke(app, ["cache", "get", "nope"])
    assert bad_key.exit_code == 1
    assert "not a sha256 object key" in bad_key.stderr
    assert runner.invoke(app, ["cache", "has", "nope"]).exit_code == 2
    assert runner.invoke(app, ["cache", "gc", "--max-size", "lots"]).exit_code == 2
    invalid = make_bundle(manifest=None, name="no-manifest")
    put = runner.invoke(app, ["cache", "put", str(invalid)])
    assert put.exit_code == 1
    assert "is not a valid bundle" in put.stderr
    unreadable = runner.invoke(app, ["cache", "put", str(tmp_path / "nothing-here")])
    assert unreadable.exit_code == 1
    assert "cannot cache" in unreadable.stderr
