from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import BundleFactory
from taskledger.lint import build_context
from taskledger.lint import context as context_module


def test_files_skip_ignored_entries_and_symlinks(make_bundle: BundleFactory) -> None:
    root = make_bundle(
        files={
            ".DS_Store": "x",
            ".git/config": "x",
            "tests/__pycache__/x.pyc": b"\x00",
            "data/b.txt": "b",
            "data/a.txt": "a",
        }
    )
    (root / "data" / "link.txt").symlink_to("a.txt")
    (root / "linked-dir").symlink_to(root / "data", target_is_directory=True)
    ctx = build_context(root)
    assert ctx.files == (
        "data/a.txt",
        "data/b.txt",
        "environment/Dockerfile",
        "instruction.md",
        "solution/solve.sh",
        "task.toml",
        "tests/test_answer.py",
        "verifier/Dockerfile",
    )
    assert ctx.files_under("data") == ("data/a.txt", "data/b.txt")
    assert ctx.files_under("data/") == ("data/a.txt", "data/b.txt")
    assert ctx.files_under(".") == ctx.files
    assert ctx.is_dir("data")
    assert not ctx.is_dir("data/a.txt")
    assert ctx.size("data/a.txt") == 1


def test_text_lines_and_binary_detection(make_bundle: BundleFactory) -> None:
    ctx = build_context(
        make_bundle(files={"a.txt": "one\r\ntwo\n", "b.bin": b"\x00\x01", "c.txt": b"\xff\xfe"})
    )
    assert ctx.text("a.txt") == "one\r\ntwo\n"
    assert ctx.lines("a.txt") == ("one", "two")
    assert ctx.text("b.bin") is None
    assert ctx.lines("b.bin") == ()
    assert ctx.text("c.txt") == "\ufffd\ufffd"
    assert ctx.read_bytes("missing.txt") is None


def test_large_files_are_not_read(
    make_bundle: BundleFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context_module, "MAX_SCAN_BYTES", 4)
    ctx = build_context(make_bundle(files={"big.txt": "12345", "small.txt": "1234"}))
    assert ctx.read_bytes("big.txt") is None
    assert ctx.read_bytes("small.txt") == b"1234"


@pytest.mark.parametrize(
    ("name", "content", "python", "shell"),
    [
        ("a.py", "", True, False),
        ("run", "#!/usr/bin/env python3\nprint(1)\n", True, False),
        ("run", "#!/usr/bin/python\n", True, False),
        ("a.sh", "", False, True),
        ("a.bash", "", False, True),
        ("run", "#!/bin/bash\n", False, True),
        ("run", "#!/usr/bin/env sh\n", False, True),
        ("run", "#!/usr/bin/env node\n", False, False),
        ("data.txt", "plain\n", False, False),
        ("blob", b"\x00" * 8, False, False),
    ],
)
def test_language_detection(
    make_bundle: BundleFactory, name: str, content: str | bytes, python: bool, shell: bool
) -> None:
    ctx = build_context(make_bundle(files={f"x/{name}": content}))
    assert ctx.is_python(f"x/{name}") is python
    assert ctx.is_shell(f"x/{name}") is shell


def test_python_parsing_is_cached_and_reports_syntax_errors(make_bundle: BundleFactory) -> None:
    ctx = build_context(
        make_bundle(
            files={
                "ok.py": 'import re\nPATTERN = "\\d+"\n',  # invalid escape: warning, not error
                "bad.py": "def broken(:\n",
                "nul.py": b"x = 1\x00",
            }
        )
    )
    module = ctx.python("ok.py")
    assert module is not None
    assert ctx.python("ok.py") is module
    assert ctx.python_error("ok.py") is None
    assert ctx.python("bad.py") is None
    error = ctx.python_error("bad.py")
    assert error is not None
    assert error.lineno == 1
    assert ctx.python("nul.py") is None
    assert ctx.python_error("nul.py") is None


def test_deeply_nested_python_is_skipped(make_bundle: BundleFactory) -> None:
    ctx = build_context(make_bundle(files={"deep.py": "x = " + "-" * 200_000 + "1"}))
    assert ctx.python("deep.py") is None
    assert ctx.python_error("deep.py") is None
    assert not ctx.is_python("missing")
    assert not ctx.is_shell("missing")


def test_manifest_text_and_lines(make_bundle: BundleFactory, tmp_path: Path) -> None:
    ctx = build_context(make_bundle())
    assert ctx.manifest_text.startswith("schema_version = 1")
    assert ctx.manifest_line("task.title") == 6
    missing = build_context(tmp_path / "nowhere")
    assert missing.manifest_text == ""
    assert missing.manifest_line("task.title") == 1


def test_unreadable_file_reads_as_none(make_bundle: BundleFactory) -> None:
    root = make_bundle(files={"secret.txt": "x"})
    target = root / "secret.txt"
    target.chmod(0)
    try:
        ctx = build_context(root)
        if os.access(target, os.R_OK):  # pragma: no cover - running as root
            pytest.skip("file permissions are not enforced for this user")
        assert ctx.read_bytes("secret.txt") is None
    finally:
        target.chmod(0o644)
