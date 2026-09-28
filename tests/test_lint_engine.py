from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from conftest import LINT_CLEAN_FILES, MINIMAL_MANIFEST, BundleFactory
from taskledger.lint import (
    LintReport,
    RuleRegistry,
    Severity,
    UnknownSelectorError,
    Violation,
    build_context,
    lint_bundle,
    lint_paths,
)
from taskledger.lint.context import LintContext

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "bundles"


def codes(report: LintReport) -> list[str]:
    return [finding.code for finding in report.findings]


def test_example_bundles_are_lint_clean() -> None:
    reports = lint_paths(sorted(EXAMPLES.iterdir()))
    assert len(reports) == 2
    for report in reports:
        assert report.loaded
        assert report.findings == ()
        assert not report.has_errors
        assert report.rules[0].code == "TL000"


def test_minimal_clean_bundle_has_no_findings(make_bundle: BundleFactory) -> None:
    report = lint_bundle(make_bundle(files=LINT_CLEAN_FILES))
    assert report.findings == ()
    assert report.suppressed == 0


def test_missing_bundle_directory_is_reported_and_other_rules_skipped(tmp_path: Path) -> None:
    registry = RuleRegistry()
    registry.rule(
        "TL000", name="bundle", severity=Severity.ERROR, description="d", fix="f", needs_files=False
    )(lambda ctx: [Violation(".", "no bundle")])

    @registry.rule("TL001", name="needs-files", severity=Severity.ERROR, description="d", fix="f")
    def explode(ctx: LintContext) -> Iterator[Violation]:
        raise AssertionError("must not run without a bundle directory")

    report = lint_bundle(tmp_path / "missing", registry=registry)
    assert codes(report) == ["TL000"]
    assert not report.loaded


def test_loader_issues_become_located_tl000_findings(make_bundle: BundleFactory) -> None:
    manifest = MINIMAL_MANIFEST.replace('"1.0.0"', '"1.0"') + '\n[tests]\npath = "grader"\n'
    report = lint_bundle(make_bundle(manifest=manifest, files=LINT_CLEAN_FILES))
    tl000 = [f for f in report.findings if f.code == "TL000"]
    assert len(tl000) == 1
    finding = tl000[0]
    assert finding.file == "task.toml"
    assert finding.line == 5
    assert finding.message.startswith("task.version: must be a semantic version")
    assert finding.message.endswith("(invalid_semver)")
    assert finding.severity is Severity.ERROR
    assert not report.loaded
    assert report.has_errors


def test_missing_manifest_and_missing_paths(make_bundle: BundleFactory, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    report = lint_bundle(empty)
    assert [(f.file, f.line) for f in report.findings if f.code == "TL000"] == [("task.toml", 1)]

    no_solution = make_bundle(files={**LINT_CLEAN_FILES, "solution/solve.sh": None})
    report = lint_bundle(no_solution)
    assert [(f.line, f.message) for f in report.findings if f.code == "TL000"] == [
        (1, "solution.entrypoint: file not found: solution/solve.sh (path_not_found)"),
        (1, "solution.path: directory not found: solution (path_not_found)"),
    ]


def test_file_that_is_not_a_bundle(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x")
    report = lint_bundle(target)
    assert [(f.code, f.file) for f in report.findings] == [("TL000", ".")]
    assert "not a directory" in report.findings[0].message


def test_invalid_manifest_keeps_valid_sections_for_the_layout(make_bundle: BundleFactory) -> None:
    manifest = MINIMAL_MANIFEST.replace('"1.0.0"', "1") + (
        '\n[tests]\npath = "grader"\n\n[verifier]\ncommand = "pytest"\n'
        '\n[lint]\nignore = ["TL00"]\n'
    )
    context = build_context(make_bundle(manifest=manifest))
    assert context.manifest is None
    assert context.layout.tests.path == "grader"  # valid section kept
    assert context.layout.verifier.command == ("pytest", "-q", "tests")  # broken: default
    assert context.layout.lint.ignore == ("TL00",)
    assert {issue.loc for issue in context.issues} == {"task.version", "verifier.command"}


def test_unparsable_manifest_uses_the_default_layout(make_bundle: BundleFactory) -> None:
    context = build_context(make_bundle(manifest="this is not toml ["))
    assert context.layout.tests.path == "tests"
    assert [issue.code for issue in context.issues] == ["toml_syntax"]


def test_unknown_selectors_in_task_toml_are_findings(make_bundle: BundleFactory) -> None:
    manifest = MINIMAL_MANIFEST + '\n[lint]\nselect = ["TL0", "TL9"]\nignore = ["QQ1"]\n'
    report = lint_bundle(make_bundle(manifest=manifest, files=LINT_CLEAN_FILES))
    messages = [(f.line, f.message) for f in report.findings]
    assert messages == [
        (10, "lint.select: unknown rule code or prefix: 'TL9'"),
        (11, "lint.ignore: unknown rule code or prefix: 'QQ1'"),
    ]
    assert report.rules[0].code == "TL000"


def test_unknown_command_line_selectors_raise(make_bundle: BundleFactory) -> None:
    bundle = make_bundle(files=LINT_CLEAN_FILES)
    with pytest.raises(UnknownSelectorError):
        lint_bundle(bundle, select=["TL9"])
    with pytest.raises(UnknownSelectorError):
        lint_paths([bundle], ignore=["nope"])


def _registry_with_lines(lines: list[int]) -> RuleRegistry:
    registry = RuleRegistry()
    for code, name in (("TL001", "first"), ("TL002", "second")):

        def check(ctx: LintContext, code: str = code) -> Iterator[Violation]:
            for line in lines:
                yield Violation("tests/test_answer.py", f"{code} hit", line)

        registry.rule(code, name=name, severity=Severity.WARNING, description="d", fix="f")(check)
    return registry


def test_task_toml_and_command_line_selection(make_bundle: BundleFactory) -> None:
    registry = _registry_with_lines([1])
    manifest = MINIMAL_MANIFEST + '\n[lint]\nignore = ["TL002"]\n'
    bundle = make_bundle(manifest=manifest, files=LINT_CLEAN_FILES)
    assert codes(lint_bundle(bundle, registry=registry)) == ["TL001"]
    assert codes(lint_bundle(bundle, select=["TL002"], registry=registry)) == ["TL002"]
    assert codes(lint_bundle(bundle, ignore=["TL001"], registry=registry)) == []
    assert [rule.code for rule in lint_bundle(bundle, registry=registry).rules] == ["TL001"]


def test_inline_suppression_comments(make_bundle: BundleFactory) -> None:
    test_file = "\n".join(
        [
            "x = 1  # taskledger: ignore[TL001]",
            "y = 2  # taskledger: ignore",
            "z = 3  # taskledger: ignore[TL00]",
            "w = 4  # taskledger: ignore[TL003, TL002]",
            "v = 5",
        ]
    )
    bundle = make_bundle(files={**LINT_CLEAN_FILES, "tests/test_answer.py": test_file})
    report = lint_bundle(bundle, registry=_registry_with_lines([1, 2, 3, 4, 5, 9]))
    remaining = [(f.code, f.line) for f in report.findings]
    assert remaining == [
        ("TL002", 1),
        ("TL001", 4),
        ("TL001", 5),
        ("TL002", 5),
        ("TL001", 9),
        ("TL002", 9),
    ]
    assert report.suppressed == 6


def test_findings_are_deduplicated_sorted_and_fingerprinted(make_bundle: BundleFactory) -> None:
    registry = RuleRegistry()

    @registry.rule("TL001", name="dup", severity=Severity.ERROR, description="d", fix="f")
    def check(ctx: LintContext) -> Iterator[Violation]:
        yield Violation("b.txt", "same", 7)
        yield Violation("a.txt", "same", 3)
        yield Violation("a.txt", "same", 1)
        yield Violation("a.txt", "same", 1)  # exact duplicate

    report = lint_bundle(make_bundle(files=LINT_CLEAN_FILES), registry=registry)
    assert [(f.file, f.line) for f in report.findings] == [("a.txt", 1), ("a.txt", 3), ("b.txt", 7)]
    prints = [f.fingerprint for f in report.findings]
    assert len(set(prints)) == 3
    assert all(len(value) == 32 and int(value, 16) >= 0 for value in prints)


def test_fingerprints_do_not_depend_on_line_numbers(make_bundle: BundleFactory) -> None:
    def fingerprint_at(line: int) -> str:
        registry = RuleRegistry()
        registry.rule("TL001", name="one", severity=Severity.ERROR, description="d", fix="f")(
            lambda ctx: [Violation("a.txt", "msg", line)]
        )
        [finding] = lint_bundle(make_bundle(files=LINT_CLEAN_FILES), registry=registry).findings
        return finding.fingerprint

    assert fingerprint_at(3) == fingerprint_at(40)


def test_counts_by_severity(make_bundle: BundleFactory) -> None:
    registry = RuleRegistry()
    registry.rule("TL001", name="warn", severity=Severity.WARNING, description="d", fix="f")(
        lambda ctx: [Violation("a", "w1"), Violation("a", "w2")]
    )
    registry.rule("TL002", name="note", severity=Severity.NOTE, description="d", fix="f")(
        lambda ctx: [Violation("a", "n")]
    )
    report = lint_bundle(make_bundle(files=LINT_CLEAN_FILES), registry=registry)
    assert report.count(Severity.WARNING) == 2
    assert report.count(Severity.NOTE) == 1
    assert report.count(Severity.ERROR) == 0
    assert not report.has_errors


def test_violation_and_finding_positions_are_validated() -> None:
    with pytest.raises(ValueError, match="1-based"):
        Violation("a", "m", line=0)
    with pytest.raises(ValueError, match="1-based"):
        Violation("a", "m", column=0)
    with pytest.raises(ValueError, match="before line"):
        Violation("a", "m", line=5, end_line=4)
    with pytest.raises(ValueError, match="end_column"):
        Violation("a", "m", end_column=0)
