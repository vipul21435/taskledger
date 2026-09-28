from __future__ import annotations

import json

import pytest

from taskledger import __version__
from taskledger.lint import Finding, LintReport, Severity, format_json, format_text
from taskledger.lint.formats import JSON_FORMAT_VERSION, display_path


def finding(code: str, severity: Severity, file: str, line: int = 1, fix: str = "") -> Finding:
    return Finding(
        code=code,
        rule=f"rule-{code.lower()}",
        severity=severity,
        message=f"{code} message",
        file=file,
        line=line,
        column=2,
        fix=fix,
        fingerprint="f" * 32,
    )


BROKEN = LintReport(
    path="bundles/broken/",
    loaded=True,
    findings=(
        finding("TL002", Severity.ERROR, "environment/Dockerfile", 3, fix="Pin it."),
        finding("TL004", Severity.WARNING, "tests/test_x.py", 9),
        finding("TL000", Severity.ERROR, "."),
    ),
    rules=(),
    suppressed=2,
)
CLEAN = LintReport(path="bundles/clean", loaded=True, findings=(), rules=())


@pytest.mark.parametrize(
    ("bundle", "file", "expected"),
    [
        ("bundles/x", "tests/a.py", "bundles/x/tests/a.py"),
        ("bundles/x/", "task.toml", "bundles/x/task.toml"),
        ("./x", "a", "x/a"),
        (".", "a/b", "a/b"),
        ("x", ".", "x"),
        ("x/", "", "x"),
        ("C:\\work\\x", "a", "C:/work/x/a"),
    ],
)
def test_display_path(bundle: str, file: str, expected: str) -> None:
    assert display_path(bundle, file) == expected


def test_text_format_lists_findings_hints_and_summary() -> None:
    text = format_text([BROKEN, CLEAN])
    assert text.splitlines() == [
        "bundles/broken/environment/Dockerfile:3:2: error TL002 rule-tl002: TL002 message",
        "    fix: Pin it.",
        "bundles/broken/tests/test_x.py:9:2: warning TL004 rule-tl004: TL004 message",
        "bundles/broken:1:2: error TL000 rule-tl000: TL000 message",
        "Found 3 findings (2 errors, 1 warning) in 1 of 2 bundles. 2 suppressed inline.",
    ]


def test_text_format_without_hints_and_without_findings() -> None:
    assert "fix:" not in format_text([BROKEN], hints=False)
    assert format_text([CLEAN]) == "No findings in 1 bundle.\n"
    one = LintReport("b", True, (finding("TL006", Severity.NOTE, "a"),), ())
    assert format_text([one]).splitlines()[-1] == "Found 1 finding (1 note) in 1 of 1 bundle."


def test_json_format_is_complete_and_stable() -> None:
    document = json.loads(format_json([BROKEN, CLEAN]))
    assert document["tool"] == "taskledger"
    assert document["version"] == __version__
    assert document["format_version"] == JSON_FORMAT_VERSION == 1
    assert document["summary"] == {
        "bundles": 2,
        "bundles_with_findings": 1,
        "findings": 3,
        "error": 2,
        "warning": 1,
        "note": 0,
        "suppressed": 2,
    }
    broken, clean = document["bundles"]
    assert clean == {
        "path": "bundles/clean",
        "loaded": True,
        "rules": [],
        "suppressed": 0,
        "findings": [],
    }
    assert broken["findings"][0] == {
        "code": "TL002",
        "rule": "rule-tl002",
        "severity": "error",
        "message": "TL002 message",
        "file": "environment/Dockerfile",
        "path": "bundles/broken/environment/Dockerfile",
        "line": 3,
        "column": 2,
        "end_line": None,
        "end_column": None,
        "fix": "Pin it.",
        "fingerprint": "f" * 32,
    }
    assert format_json([BROKEN]) == format_json([BROKEN])
