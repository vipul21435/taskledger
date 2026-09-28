from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import MINIMAL_MANIFEST, BundleFactory
from taskledger.bundle import BundleIssue, InvalidBundleError, load_bundle
from taskledger.bundle.loader import format_loc


def codes(issues: tuple[BundleIssue, ...]) -> list[tuple[str, str]]:
    return [(issue.where, issue.code) for issue in issues]


def test_loads_a_valid_bundle(make_bundle: BundleFactory) -> None:
    root = make_bundle()
    result = load_bundle(root)
    assert result.ok
    assert result.issues == ()
    bundle = result.unwrap()
    assert bundle.root == root.resolve()
    assert (bundle.id, bundle.version) == ("sum-of-squares", "1.0.0")
    assert bundle.path("solution/solve.sh").is_file()


def test_accepts_str_paths(make_bundle: BundleFactory) -> None:
    assert load_bundle(str(make_bundle())).ok


def test_missing_directory(tmp_path: Path) -> None:
    result = load_bundle(tmp_path / "nope")
    assert not result.ok
    assert codes(result.issues) == [(".", "bundle_not_found")]


def test_file_instead_of_directory(tmp_path: Path) -> None:
    target = tmp_path / "task.toml"
    target.write_text(MINIMAL_MANIFEST)
    assert codes(load_bundle(target).issues) == [(".", "bundle_not_a_directory")]


def test_missing_manifest(make_bundle: BundleFactory) -> None:
    result = load_bundle(make_bundle(manifest=None))
    assert codes(result.issues) == [("task.toml", "missing_manifest")]


def test_manifest_that_is_a_directory(make_bundle: BundleFactory) -> None:
    root = make_bundle(manifest=None)
    (root / "task.toml").mkdir()
    assert codes(load_bundle(root).issues) == [("task.toml", "missing_manifest")]


def test_unreadable_manifest(make_bundle: BundleFactory) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    root = make_bundle()
    manifest = root / "task.toml"
    manifest.chmod(0)
    try:
        assert codes(load_bundle(root).issues) == [("task.toml", "manifest_unreadable")]
    finally:
        manifest.chmod(0o644)


def test_toml_syntax_error_reports_line_and_column(make_bundle: BundleFactory) -> None:
    result = load_bundle(make_bundle(manifest="schema_version = 1\n[task\n"))
    [issue] = result.issues
    assert issue.code == "toml_syntax"
    assert "line 2" in issue.message


def test_manifest_must_be_utf8(make_bundle: BundleFactory) -> None:
    result = load_bundle(make_bundle(files={"task.toml": b"schema_version = 1\n# \xff\n"}))
    assert codes(result.issues) == [("task.toml", "manifest_encoding")]


def test_reports_every_schema_error_with_its_field_path(make_bundle: BundleFactory) -> None:
    manifest = """\
schema_version = 1
colour = "blue"

[task]
id = "Bad ID"
version = "1.0"
category = "arithmetic"
tags = ["ok", "Not Ok"]

[environment]
build_args = { "bad-name" = "1" }

[timeouts]
verifier_sec = "30"
"""
    result = load_bundle(make_bundle(manifest=manifest))
    assert not result.ok
    assert sorted(codes(result.issues)) == [
        ("task.toml:colour", "extra_forbidden"),
        ("task.toml:environment.build_args['bad-name'] (key)", "invalid_build_arg"),
        ("task.toml:task.id", "invalid_slug"),
        ("task.toml:task.tags[1]", "invalid_tag"),
        ("task.toml:task.title", "missing"),
        ("task.toml:task.version", "invalid_semver"),
        ("task.toml:timeouts.verifier_sec", "int_type"),
    ]
    messages = {issue.loc: issue.message for issue in result.issues}
    assert messages["task.title"] == "required field is missing"
    assert messages["colour"] == "unknown field"


def test_missing_declared_file(make_bundle: BundleFactory) -> None:
    result = load_bundle(make_bundle(files={"instruction.md": None}))
    [issue] = result.issues
    assert (issue.where, issue.code) == ("task.toml:instruction.path", "path_not_found")
    assert str(issue) == "task.toml:instruction.path: file not found: instruction.md"


def test_reports_all_missing_paths_at_once(make_bundle: BundleFactory) -> None:
    root = make_bundle(
        manifest=MINIMAL_MANIFEST + '\n[baseline]\npath = "start"\n',
        files={
            "environment/Dockerfile": None,
            "solution/solve.sh": None,
            "solution/NOTES.md": "The entry point is missing.\n",
        },
    )
    assert codes(load_bundle(root).issues) == [
        ("task.toml:environment.dockerfile", "path_not_found"),
        ("task.toml:solution.entrypoint", "path_not_found"),
        ("task.toml:baseline.path", "path_not_found"),
    ]


def test_wrong_path_kind(make_bundle: BundleFactory) -> None:
    manifest = MINIMAL_MANIFEST + '\n[tests]\npath = "instruction.md"\n'
    assert codes(load_bundle(make_bundle(manifest=manifest)).issues) == [
        ("task.toml:tests.path", "wrong_path_kind")
    ]


def test_symlink_escaping_the_bundle_is_rejected(
    make_bundle: BundleFactory, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n")
    root = make_bundle(files={"instruction.md": None})
    (root / "instruction.md").symlink_to(outside)
    [issue] = load_bundle(root).issues
    assert (issue.where, issue.code) == ("task.toml:instruction.path", "path_escapes_bundle")


def test_symlink_inside_the_bundle_is_allowed(make_bundle: BundleFactory) -> None:
    root = make_bundle(files={"instruction.md": None, "docs/prompt.md": "Do the thing.\n"})
    (root / "instruction.md").symlink_to(root / "docs" / "prompt.md")
    assert load_bundle(root).ok


def test_symlink_loop_is_reported(make_bundle: BundleFactory) -> None:
    root = make_bundle(files={"instruction.md": None})
    (root / "instruction.md").symlink_to(root / "instruction.md")
    [issue] = load_bundle(root).issues
    assert issue.code == "path_unreadable"


def test_bundle_reached_through_a_symlinked_root(
    make_bundle: BundleFactory, tmp_path: Path
) -> None:
    link = tmp_path / "link-to-bundle"
    link.symlink_to(make_bundle())
    assert load_bundle(link).ok


def test_unwrap_raises_with_every_issue(make_bundle: BundleFactory) -> None:
    result = load_bundle(make_bundle(files={"instruction.md": None, "tests/test_answer.py": None}))
    with pytest.raises(InvalidBundleError, match=r"\(2 issue\(s\)\)") as info:
        result.unwrap()
    assert info.value.issues == result.issues
    assert "task.toml:instruction.path: file not found: instruction.md" in str(info.value)
    assert "task.toml:tests.path: directory not found: tests" in str(info.value)


def test_issue_serialization() -> None:
    issue = BundleIssue("task.toml", "task.id", "invalid_slug", "bad")
    assert issue.to_dict() == {
        "file": "task.toml",
        "loc": "task.id",
        "code": "invalid_slug",
        "message": "bad",
    }
    assert BundleIssue("", "task.id", "x", "m").where == "task.id"


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        (("task", "id"), "task.id"),
        (("task", "tags", 2), "task.tags[2]"),
        (
            ("environment", "build_args", "my-arg", "[key]"),
            "environment.build_args['my-arg'] (key)",
        ),
        ((0, "x"), "[0].x"),
        ((), ""),
    ],
)
def test_format_loc(loc: tuple[int | str, ...], expected: str) -> None:
    assert format_loc(loc) == expected
