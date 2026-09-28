"""TL005 oversized-file."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import LINT_CLEAN_FILES, MINIMAL_MANIFEST, BundleFactory, lint_findings
from taskledger.lint import lint_bundle
from taskledger.lint.context import MAX_SCAN_BYTES, LintContext
from taskledger.lint.rules.size import human_size

KIB = 1024


def with_limits(**limits: int) -> str:
    body = "".join(f"{key} = {value}\n" for key, value in limits.items())
    return MINIMAL_MANIFEST + "\n[lint]\n" + body


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (1536, "1.5 KiB"),
        (1024 * 1024, "1.0 MiB"),
        (5 * 1024**3, "5.0 GiB"),
        (3 * 1024**5, "3072.0 TiB"),
    ],
)
def test_human_size(size: int, expected: str) -> None:
    assert human_size(size) == expected


def test_default_limits_pass_a_normal_bundle(make_bundle: BundleFactory) -> None:
    assert lint_findings(make_bundle, "TL005") == []


def test_file_at_the_limit_passes_and_one_byte_over_fails(make_bundle: BundleFactory) -> None:
    manifest = with_limits(max_file_kb=2)
    at_limit = {"baseline/data.bin": b"\x00" * (2 * KIB)}
    assert lint_findings(make_bundle, "TL005", at_limit, manifest) == []
    over = {"baseline/data.bin": b"\x00" * (2 * KIB + 1)}
    findings = lint_findings(make_bundle, "TL005", over, manifest)
    assert [(f.file, f.line, f.column, f.severity) for f in findings] == [
        ("baseline/data.bin", 1, 1, "error")
    ]
    assert findings[0].message == (
        "file is 2.0 KiB (2049 bytes), over the per-file limit of 2 KiB (lint.max_file_kb)"
    )


def test_default_file_limit_is_one_mebibyte(make_bundle: BundleFactory) -> None:
    files = {"baseline/dump.csv": "1,2,3\n" * (200 * KIB)}  # 1.2 MiB of text
    findings = lint_findings(make_bundle, "TL005", files)
    assert [f.file for f in findings] == ["baseline/dump.csv"]
    assert "1.2 MiB (1228800 bytes)" in findings[0].message


def test_bundle_total_is_reported_at_the_lint_section(make_bundle: BundleFactory) -> None:
    manifest = with_limits(max_file_kb=64, max_bundle_kb=8)
    files = {
        "baseline/a.txt": "a" * (3 * KIB),
        "baseline/b.txt": "b" * (4 * KIB),
        "baseline/c.txt": "c" * (2 * KIB),
        "baseline/d.txt": "d" * (2 * KIB),
    }
    findings = lint_findings(make_bundle, "TL005", files, manifest)
    assert len(findings) == 1
    (finding,) = findings
    lines = manifest.splitlines()
    assert (finding.file, finding.line) == ("task.toml", lines.index("max_bundle_kb = 8") + 1)
    assert finding.message.startswith("bundle files total ")
    assert finding.message.endswith(
        "over the bundle limit of 8 KiB (lint.max_bundle_kb); largest: "
        "baseline/b.txt (4.0 KiB), baseline/a.txt (3.0 KiB), baseline/c.txt (2.0 KiB)"
    )


def sparse(path: Path, size: int) -> None:
    """A file of ``size`` bytes that takes no real disk space."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def test_default_limits_without_a_lint_section(make_bundle: BundleFactory) -> None:
    bundle = make_bundle(files=LINT_CLEAN_FILES)
    sparse(bundle / "baseline" / "checkpoint.bin", 21 * 1024 * KIB)
    findings = lint_bundle(bundle, select=["TL005"]).findings
    assert [(f.file, f.line) for f in findings] == [
        ("baseline/checkpoint.bin", 1),
        ("task.toml", 1),  # no [lint] section to point at
    ]
    assert "over the per-file limit of 1024 KiB" in findings[0].message
    assert "over the bundle limit of 20480 KiB" in findings[1].message
    manifest_size = (bundle / "task.toml").stat().st_size
    verifier_size = (bundle / "verifier" / "Dockerfile").stat().st_size
    assert findings[1].message.endswith(
        "largest: baseline/checkpoint.bin (21.0 MiB), "
        f"task.toml ({manifest_size} B), verifier/Dockerfile ({verifier_size} B)"
    )


def test_ignored_clutter_and_symlinks_do_not_count(make_bundle: BundleFactory) -> None:
    manifest = with_limits(max_file_kb=1, max_bundle_kb=64)
    files = {
        ".git/objects/pack/big.pack": b"\x00" * (8 * KIB),
        "solution/__pycache__/solve.cpython-312.pyc": b"\x00" * (8 * KIB),
        ".DS_Store": b"\x00" * (8 * KIB),
    }
    bundle = make_bundle(manifest=manifest, files={**LINT_CLEAN_FILES, **files})
    (bundle / "baseline-link").symlink_to("/dev/zero")
    assert lint_bundle(bundle, select=["TL005"]).findings == ()


def test_file_that_vanishes_after_listing_is_skipped(
    make_bundle: BundleFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(files=LINT_CLEAN_FILES)
    real_size = LintContext.size

    def flaky_size(self: LintContext, relative: str) -> int:
        if relative == "instruction.md":
            raise FileNotFoundError(relative)
        return real_size(self, relative)

    monkeypatch.setattr(LintContext, "size", flaky_size)
    assert lint_bundle(bundle, select=["TL005"]).findings == ()


def test_files_too_large_to_scan_are_still_reported(make_bundle: BundleFactory) -> None:
    bundle = make_bundle(files=LINT_CLEAN_FILES)
    sparse(bundle / "baseline" / "huge.bin", MAX_SCAN_BYTES + 1)
    findings = lint_bundle(bundle, select=["TL005"]).findings
    assert [(f.code, f.file) for f in findings] == [("TL005", "baseline/huge.bin")]
