from __future__ import annotations

import hashlib
import json
import os
import shutil
import unicodedata
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import MINIMAL_MANIFEST, BundleFactory
from taskledger.bundle import (
    ALGORITHM,
    Bundle,
    HashError,
    Manifest,
    hash_bundle,
    hash_files,
    hashing,
    load_bundle,
)
from taskledger.bundle.hashing import (
    FileDigest,
    NewlineNormalizer,
    canonical_manifest,
    digest_bytes,
    digest_file,
    is_ignored,
    merkle_root,
)
from taskledger.cli import app

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "bundles"
MANIFEST = Manifest.model_validate(
    {
        "schema_version": 1,
        "task": {"id": "tiny-task", "version": "1.0.0", "title": "Tiny", "category": "misc"},
    }
)
runner = CliRunner()


def sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def load(root: Path) -> Bundle:
    return load_bundle(root).unwrap()


def test_root_matches_the_documented_construction() -> None:
    files = {"a.txt": b"hi\r\n", "dir/b.bin": b"\x00\r\n"}
    dir_node = sha(b"\x01" + b"f" + b"b.bin\x00" + sha(b"\x00\r\n"))
    root_node = sha(
        b"\x01"
        + (b"f" + b"a.txt\x00" + sha(b"hi\n"))
        + (b"d" + b"dir\x00" + dir_node)
        + (b"f" + b"task.toml\x00" + sha(canonical_manifest(MANIFEST)))
    )
    expected = hashlib.sha256(b"taskledger-bundle/v1\x00" + root_node).hexdigest()
    digest = hash_files(files, MANIFEST)
    assert digest.root == expected
    assert digest.value == f"sha256:{expected}"
    assert digest.algorithm == ALGORITHM == "tl-merkle-sha256/v1"


def test_canonical_manifest_drops_defaults_and_metadata() -> None:
    manifest = Manifest.model_validate(
        {
            "schema_version": 1,
            "task": {
                "id": "tiny-task",
                "version": "1.0.0",
                "title": "Tiny",
                "category": "misc",
                "authors": ["Someone"],
                "notes": "draft",
            },
            "timeouts": {"verifier_sec": 300, "agent_sec": 60},
        }
    )
    assert json.loads(canonical_manifest(manifest)) == {
        "schema_version": 1,
        "task": {"category": "misc", "id": "tiny-task", "title": "Tiny", "version": "1.0.0"},
        "timeouts": {"agent_sec": 60},
    }


def test_canonical_manifest_drops_the_lint_section() -> None:
    configured = MANIFEST.model_validate(
        {**MANIFEST.model_dump(mode="json"), "lint": {"ignore": ["TL004"], "max_file_kb": 8}}
    )
    assert configured.lint.max_file_kb == 8
    assert canonical_manifest(configured) == canonical_manifest(MANIFEST)
    assert hash_files({}, configured) == hash_files({}, MANIFEST)


def test_digest_bytes_normalizes_text_but_not_binary() -> None:
    text = digest_bytes("a.txt", b"x\r\ny\rz\n")
    assert (text.kind, text.size) == ("text", 6)
    assert text.sha256 == hashlib.sha256(b"x\ny\nz\n").hexdigest()
    binary = digest_bytes("b.bin", b"\x00\r\n")
    assert (binary.kind, binary.size) == ("binary", 3)
    assert binary.sha256 == hashlib.sha256(b"\x00\r\n").hexdigest()


def test_lf_text_digest_matches_plain_sha256(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"plain\ntext\n")
    assert digest_file("f.txt", target).sha256 == hashlib.sha256(b"plain\ntext\n").hexdigest()


def test_disk_digest_is_chunk_boundary_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hashing, "_CHUNK_SIZE", 3)
    data = b"ab\r\ncd\r\r\n\rx\r"
    target = tmp_path / "f.txt"
    target.write_bytes(data)
    assert digest_file("f.txt", target) == digest_bytes("f.txt", data)
    target.write_bytes(b"abcdef\x00\r\n")
    assert digest_file("f.txt", target).kind == "binary"


def test_normalizer_holds_back_a_trailing_cr() -> None:
    normalizer = NewlineNormalizer()
    assert normalizer.feed(b"a\r") == b"a"
    assert normalizer.feed(b"\nb\r") == b"\nb"
    assert normalizer.feed(b"") == b""
    assert normalizer.flush() == b"\n"
    assert normalizer.flush() == b""


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        (".DS_Store", True),
        ("tests/__pycache__/test_x.cpython-312.pyc", True),
        (".git/HEAD", True),
        ("src/mod.pyc", True),
        ("docs/._notes.md", True),
        ("tests/test_x.py", False),
        ("solution/.gitkeep", False),
        ("git/HEAD", False),
    ],
)
def test_ignore_patterns_match_any_component(path: str, ignored: bool) -> None:
    assert is_ignored(path) is ignored


def test_cosmetic_changes_on_disk_keep_the_example_hash(tmp_path: Path) -> None:
    original = EXAMPLES / "modular-inverse-table"
    copy = tmp_path / "copy"
    shutil.copytree(original, copy, ignore=shutil.ignore_patterns("__pycache__"))
    for path in copy.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    manifest = (copy / "task.toml").read_text()
    manifest = manifest.replace('authors = ["Vipul Raj Jha"]', 'authors = ["Someone Else"]')
    manifest = manifest.replace("created_at = 2026-09-29", "created_at = 2027-01-01")
    manifest = "# reformatted by an editor\n" + manifest.replace(" = ", "   =   ")
    (copy / "task.toml").write_text(manifest)
    (copy / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (copy / "tests" / "__pycache__").mkdir()
    (copy / "tests" / "__pycache__" / "test_inverses.cpython-312.pyc").write_bytes(b"\x00")
    (copy / ".git").mkdir()
    (copy / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (copy / "empty-dir").mkdir()
    (copy / "solution" / "solve.py").chmod(0o700)
    assert hash_bundle(load(copy)) == hash_bundle(load(original))


def test_semantic_change_on_disk_changes_the_hash(tmp_path: Path) -> None:
    original = EXAMPLES / "integer-linear-system"
    copy = tmp_path / "copy"
    shutil.copytree(original, copy)
    before = hash_bundle(load(copy))
    grader = copy / "tests" / "test_solution.py"
    grader.write_text(grader.read_text().replace("== 7", "== 8"))
    after = hash_bundle(load(copy))
    assert after.value != before.value
    changed = {f.path for f in after.files} - {f.path for f in before.files}
    assert changed == set()
    assert [a.path for a, b in zip(after.files, before.files, strict=True) if a != b] == [
        "tests/test_solution.py"
    ]


def test_symlinks_hash_their_target_and_are_not_followed(
    make_bundle: BundleFactory, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("v1\n")
    root = make_bundle()
    (root / "data").mkdir()
    (root / "data" / "link").symlink_to(outside)
    first = hash_bundle(load(root))
    [link] = [f for f in first.files if f.path == "data/link"]
    assert link.kind == "symlink"
    assert link.sha256 == hashlib.sha256(os.fsencode(str(outside))).hexdigest()

    outside.write_text("v2\n")
    assert hash_bundle(load(root)) == first

    (root / "data" / "link").unlink()
    (root / "data" / "link").symlink_to(tmp_path / "elsewhere.txt")
    assert hash_bundle(load(root)).value != first.value


def test_symlink_and_file_with_same_bytes_differ(make_bundle: BundleFactory) -> None:
    as_file = make_bundle(files={"pointer": "target.txt"})
    as_link = make_bundle()
    (as_link / "pointer").symlink_to("target.txt")
    assert hash_bundle(load(as_file)).value != hash_bundle(load(as_link)).value


def test_special_files_cannot_be_hashed(make_bundle: BundleFactory) -> None:
    root = make_bundle()
    os.mkfifo(root / "pipe")
    with pytest.raises(HashError, match="pipe: unsupported file type"):
        hash_bundle(load(root))


def test_file_names_are_compared_in_nfc(make_bundle: BundleFactory) -> None:
    name = "caf\u00e9.txt"
    composed = make_bundle(files={f"data/{unicodedata.normalize('NFC', name)}": "x\n"})
    decomposed = make_bundle(files={f"data/{unicodedata.normalize('NFD', name)}": "x\n"})
    first, second = hash_bundle(load(composed)), hash_bundle(load(decomposed))
    assert first == second
    assert "data/caf\u00e9.txt" in {f.path for f in first.files}


def test_names_that_clash_after_nfc_are_rejected() -> None:
    files = {"caf\u00e9": b"a", unicodedata.normalize("NFD", "caf\u00e9"): b"b"}
    with pytest.raises(HashError, match="more than one entry"):
        hash_files(files, MANIFEST)


@pytest.mark.parametrize("bad", ["/abs.txt", "a//b", "./a", "a/../b", "a/", ""])
def test_hash_files_requires_normalized_paths(bad: str) -> None:
    with pytest.raises(HashError, match="normalized bundle-relative"):
        hash_files({bad: b"x"}, MANIFEST)


def test_path_used_as_both_file_and_directory_is_rejected() -> None:
    with pytest.raises(HashError, match="parent a is a file"):
        merkle_root(
            [FileDigest("a", "text", 0, "00" * 32), FileDigest("a/b", "text", 0, "00" * 32)]
        )


def test_task_toml_in_memory_is_replaced_by_the_manifest() -> None:
    assert hash_files({"task.toml": b"anything"}, MANIFEST) == hash_files({}, MANIFEST)


def test_digest_serialization() -> None:
    digest = hash_files({"b.txt": b"b\n", "a.txt": b"a\n"}, MANIFEST)
    record = digest.to_dict()
    assert record["algorithm"] == ALGORITHM
    assert record["hash"] == digest.value
    assert [entry["path"] for entry in record["files"]] == ["a.txt", "b.txt", "task.toml"]


def test_hash_cli_prints_hash_and_path() -> None:
    root = EXAMPLES / "modular-inverse-table"
    result = runner.invoke(app, ["hash", str(root)])
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{hash_bundle(load(root)).value}  {root}\n"


def test_hash_cli_json() -> None:
    root = EXAMPLES / "integer-linear-system"
    result = runner.invoke(app, ["hash", "--json", str(root)])
    assert result.exit_code == 0, result.output
    record = json.loads(result.stdout)
    assert record["id"] == "integer-linear-system"
    assert record["version"] == "1.0.0"
    assert record["hash"] == hash_bundle(load(root)).value
    assert {entry["kind"] for entry in record["files"]} == {"text", "manifest"}


def test_hash_cli_rejects_invalid_bundles(make_bundle: BundleFactory) -> None:
    root = make_bundle(manifest=MINIMAL_MANIFEST.replace("[task]", "[tsk]"))
    result = runner.invoke(app, ["hash", str(root)])
    assert result.exit_code == 1
    assert "invalid" in result.stderr
    assert "task.toml:tsk: unknown field" in result.stderr
    assert result.stdout == ""


def test_hash_cli_reports_unhashable_bundles(make_bundle: BundleFactory) -> None:
    root = make_bundle()
    os.mkfifo(root / "pipe")
    result = runner.invoke(app, ["hash", str(root)])
    assert result.exit_code == 1
    assert "cannot hash" in result.stderr
