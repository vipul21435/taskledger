"""SARIF 2.1.0 formatter and the required-fields checker."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from typer.testing import CliRunner

from taskledger import __version__
from taskledger.cli import app
from taskledger.lint import (
    REGISTRY,
    Finding,
    LintReport,
    Severity,
    build_sarif,
    format_sarif,
    lint_paths,
    sarif_problems,
)
from taskledger.lint.sarif import (
    FINGERPRINT_KEY,
    SARIF_SCHEMA,
    SRCROOT,
    artifact_location,
    rule_descriptor,
)

ROOT = Path(__file__).resolve().parents[1]
FLAWED = ROOT / "examples" / "flawed" / "digit-sum-report"
EXAMPLES = ROOT / "examples" / "bundles"
RULES = REGISTRY.rules
runner = CliRunner()


def finding(code: str, file: str, **kw: Any) -> Finding:
    rule = REGISTRY.get(code)
    fields: dict[str, Any] = {
        "code": code,
        "rule": rule.name,
        "severity": rule.severity,
        "message": f"{code} message",
        "file": file,
        "fingerprint": "f" * 32,
    }
    fields.update(kw)
    return Finding(**fields)


REPORT = LintReport(
    path="bundles/broken/",
    loaded=True,
    findings=(
        finding("TL002", "environment/Dockerfile", line=3, column=6, end_line=3, end_column=22),
        finding("TL004", "tests/test x.py", line=9, column=2, severity=Severity.WARNING),
        finding("TL000", "."),
    ),
    rules=RULES,
)
CLEAN = LintReport(path="bundles/clean", loaded=True, findings=(), rules=RULES[:2])


def run_of(document: dict[str, Any]) -> dict[str, Any]:
    (run,) = document["runs"]
    return run


# -- rendering -----------------------------------------------------------------


def test_log_skeleton_and_driver() -> None:
    document = build_sarif([REPORT, CLEAN])
    assert document["$schema"] == SARIF_SCHEMA
    assert document["version"] == "2.1.0"
    run = run_of(document)
    driver = run["tool"]["driver"]
    assert driver["name"] == "taskledger"
    assert driver["version"] == driver["semanticVersion"] == __version__
    assert [rule["id"] for rule in driver["rules"]] == [rule.code for rule in RULES]
    assert run["columnKind"] == "unicodeCodePoints"
    assert sarif_problems(document) == []


def test_rule_descriptor() -> None:
    rule = REGISTRY.get("TL002")
    assert rule_descriptor(rule) == {
        "id": "TL002",
        "name": "unpinned-base-image",
        "shortDescription": {"text": "Unpinned base image"},
        "fullDescription": {"text": rule.description},
        "help": {
            "text": f"{rule.description}\n\nFix: {rule.fix}",
            "markdown": f"{rule.description}\n\n**Fix:** {rule.fix}",
        },
        "defaultConfiguration": {"level": "error"},
    }
    secrets = rule_descriptor(REGISTRY.get("TL006"))
    assert secrets["properties"] == {"tags": ["security"], "security-severity": "8.0"}


def test_results_carry_rule_level_message_location_and_fingerprint() -> None:
    results = run_of(build_sarif([REPORT]))["results"]
    fingerprint = results[0]["partialFingerprints"][FINGERPRINT_KEY]
    assert len(fingerprint) == 32
    assert results[0] == {
        "ruleId": "TL002",
        "ruleIndex": 2,
        "level": "error",
        "message": {"text": "TL002 message"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": "bundles/broken/environment/Dockerfile",
                        "uriBaseId": SRCROOT,
                    },
                    "region": {"startLine": 3, "startColumn": 6, "endLine": 3, "endColumn": 22},
                }
            }
        ],
        "partialFingerprints": {FINGERPRINT_KEY: fingerprint},
    }
    warning, bundle_level = results[1], results[2]
    assert warning["level"] == "warning"
    location = warning["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "bundles/broken/tests/test%20x.py"
    assert location["region"] == {"startLine": 9, "startColumn": 2}
    assert bundle_level["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == (
        "bundles/broken"
    )


def test_fingerprints_differ_per_file_and_ignore_line_numbers() -> None:
    same = LintReport("b", True, (finding("TL004", "a.py"), finding("TL004", "b.py")), RULES)
    moved = LintReport("b", True, (finding("TL004", "a.py", line=40),), RULES)
    unfingerprinted = LintReport("b", True, (finding("TL004", "a.py", fingerprint=""),), RULES)

    def prints(report: LintReport) -> list[str]:
        return [
            result["partialFingerprints"][FINGERPRINT_KEY]
            for result in run_of(build_sarif([report]))["results"]
        ]

    first, second = prints(same)
    assert first != second
    assert prints(moved) == [first]
    assert len(prints(unfingerprinted)[0]) == 32


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("examples/x/task.toml", {"uri": "examples/x/task.toml", "uriBaseId": SRCROOT}),
        ("a b/c#d.py", {"uri": "a%20b/c%23d.py", "uriBaseId": SRCROOT}),
        ("/srv/bundle/task.toml", {"uri": "file:///srv/bundle/task.toml"}),
        ("C:/work/x/a.py", {"uri": "file:///C:/work/x/a.py"}),
    ],
)
def test_artifact_locations(path: str, expected: dict[str, str]) -> None:
    assert artifact_location(path) == expected


def test_paths_outside_the_working_directory_become_file_uris(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    monkeypatch.chdir(inner)
    location = artifact_location("../bundle/task.toml")
    assert location == {"uri": (tmp_path.resolve() / "bundle" / "task.toml").as_uri()}


def test_empty_input_is_still_a_valid_log() -> None:
    document = build_sarif([])
    assert run_of(document)["results"] == []
    assert run_of(document)["tool"]["driver"]["rules"] == []
    assert sarif_problems(document) == []


def test_format_sarif_is_indented_stable_json() -> None:
    text = format_sarif([REPORT])
    assert text.endswith("}\n")
    assert json.loads(text) == build_sarif([REPORT])
    assert text == format_sarif([REPORT])


# -- the checker ---------------------------------------------------------------

VALID = build_sarif([REPORT])
RESULT = "$.runs[0].results[0]"
PHYSICAL = f"{RESULT}.locations[0].physicalLocation"


def without(document: dict[str, Any], *path: str | int) -> dict[str, Any]:
    broken = copy.deepcopy(document)
    parent: Any = broken
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    return broken


def with_value(document: dict[str, Any], value: object, *path: str | int) -> dict[str, Any]:
    broken = copy.deepcopy(document)
    parent: Any = broken
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    return broken


R0: tuple[str | int, ...] = ("runs", 0, "results", 0)
LOC: tuple[str | int, ...] = (*R0, "locations", 0, "physicalLocation")


@pytest.mark.parametrize(
    ("path", "problem"),
    [
        (("version",), "$: missing required property 'version'"),
        (("runs",), "$: missing required property 'runs'"),
        (("runs", 0, "tool"), "$.runs[0]: missing required property 'tool'"),
        (("runs", 0, "tool", "driver"), "$.runs[0].tool: missing required property 'driver'"),
        (
            ("runs", 0, "tool", "driver", "name"),
            "$.runs[0].tool.driver: missing required property 'name'",
        ),
        (
            ("runs", 0, "tool", "driver", "rules", 1, "id"),
            "$.runs[0].tool.driver.rules[1]: missing required property 'id'",
        ),
        (
            ("runs", 0, "tool", "driver", "rules", 1, "shortDescription", "text"),
            "$.runs[0].tool.driver.rules[1].shortDescription: missing required property 'text'",
        ),
        (("runs", 0, "results"), "$.runs[0]: missing required property 'results'"),
        ((*R0, "message"), f"{RESULT}: missing required property 'message'"),
        ((*R0, "message", "text"), f"{RESULT}.message: missing required property 'text'"),
        ((*R0, "ruleId"), f"{RESULT}: missing required property 'ruleId'"),
        ((*R0, "locations"), f"{RESULT}: missing required property 'locations'"),
        (
            (*R0, "locations", 0, "physicalLocation"),
            f"{RESULT}.locations[0]: missing required property 'physicalLocation'",
        ),
        (
            (*LOC, "artifactLocation"),
            f"{PHYSICAL}: missing required property 'artifactLocation'",
        ),
        (
            (*LOC, "artifactLocation", "uri"),
            f"{PHYSICAL}.artifactLocation: missing required property 'uri'",
        ),
        ((*LOC, "region"), f"{PHYSICAL}: missing required property 'region'"),
        (
            (*LOC, "region", "startLine"),
            f"{PHYSICAL}.region: missing required property 'startLine'",
        ),
    ],
)
def test_checker_reports_every_missing_required_field(
    path: tuple[str | int, ...], problem: str
) -> None:
    assert problem in sarif_problems(without(VALID, *path))


@pytest.mark.parametrize(
    ("path", "value", "problem"),
    [
        (("version",), "2.0.0", "$.version: must be '2.1.0', got '2.0.0'"),
        (("runs",), {}, "$.runs: must be an array"),
        (("runs", 0), [], "$.runs[0]: must be an object"),
        (("runs", 0, "tool"), "taskledger", "$.runs[0].tool: must be an object"),
        (("runs", 0, "tool", "driver"), None, "$.runs[0].tool.driver: must be an object"),
        (("runs", 0, "tool", "driver", "name"), "", "$.runs[0].tool.driver.name: must be a"),
        (("runs", 0, "tool", "driver", "rules", 0), "TL000", "rules[0]: must be an object"),
        (("runs", 0, "tool", "driver", "rules", 1, "id"), "TL000", "duplicate rule id 'TL000'"),
        (
            ("runs", 0, "tool", "driver", "rules", 1, "defaultConfiguration"),
            {"level": "fatal"},
            "rules[1].defaultConfiguration.level: must be one of error, none, note, warning",
        ),
        (
            ("runs", 0, "tool", "driver", "rules", 1, "defaultConfiguration"),
            "error",
            "rules[1].defaultConfiguration: must be an object",
        ),
        (("runs", 0, "columnKind"), "bytes", "$.runs[0].columnKind: must be one of"),
        (("runs", 0, "results", 0), 7, f"{RESULT}: must be an object"),
        ((*R0, "message"), "text", f"{RESULT}.message: must be an object"),
        ((*R0, "level"), "fatal", f"{RESULT}.level: must be one of"),
        ((*R0, "ruleIndex"), 99, f"{RESULT}.ruleIndex: 99 is not a rule index"),
        ((*R0, "ruleIndex"), 1, f"{RESULT}.ruleIndex: points at rule 'TL001', not 'TL002'"),
        ((*R0, "ruleIndex"), -2, f"{RESULT}.ruleIndex: must be an integer >= -1"),
        ((*R0, "ruleIndex"), True, f"{RESULT}.ruleIndex: must be an integer >= -1"),
        ((*R0, "locations"), [], f"{RESULT}.locations: must not be empty"),
        ((*R0, "locations", 0), "x", f"{RESULT}.locations[0]: must be an object"),
        ((*LOC,), [], f"{PHYSICAL}: must be an object"),
        ((*LOC, "artifactLocation"), "x", f"{PHYSICAL}.artifactLocation: must be an object"),
        (
            (*LOC, "artifactLocation", "uri"),
            "a b\\c",
            f"{PHYSICAL}.artifactLocation.uri: is not a valid URI reference",
        ),
        ((*LOC, "region"), 3, f"{PHYSICAL}.region: must be an object"),
        ((*LOC, "region", "startLine"), 0, f"{PHYSICAL}.region.startLine: must be an integer"),
        ((*LOC, "region", "startColumn"), "6", f"{PHYSICAL}.region.startColumn: must be an"),
        ((*LOC, "region", "endLine"), 2, f"{PHYSICAL}.region.endLine: 2 is before startLine 3"),
        ((*R0, "partialFingerprints"), {"k": 1}, "partialFingerprints: must map names to"),
    ],
)
def test_checker_reports_wrong_values(
    path: tuple[str | int, ...], value: object, problem: str
) -> None:
    problems = sarif_problems(with_value(VALID, value, *path))
    assert any(found.startswith(problem) or problem in found for found in problems), problems


def test_checker_rejects_non_objects_at_the_top() -> None:
    assert sarif_problems([]) == ["$: must be an object"]


def test_rule_index_minus_one_means_no_rule() -> None:
    assert sarif_problems(with_value(VALID, -1, *R0, "ruleIndex")) == []


# -- end to end ----------------------------------------------------------------


def test_real_lint_run_produces_a_valid_log() -> None:
    reports = lint_paths([FLAWED, *sorted(EXAMPLES.iterdir())])
    document = build_sarif(reports)
    assert sarif_problems(document) == []
    results = run_of(document)["results"]
    assert len(results) == sum(len(report.findings) for report in reports) == 7
    assert {result["ruleId"] for result in results} == {"TL002", "TL003", "TL004", "TL006"}
    fingerprints = [result["partialFingerprints"][FINGERPRINT_KEY] for result in results]
    assert len(set(fingerprints)) == len(fingerprints)


def test_cli_sarif_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    flawed = runner.invoke(app, ["lint", "--format", "sarif", "examples/flawed/digit-sum-report"])
    assert flawed.exit_code == 1
    document = json.loads(flawed.stdout)
    assert sarif_problems(document) == []
    results = run_of(document)["results"]
    assert len(results) == 7
    uris = {
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for result in results
    }
    assert "examples/flawed/digit-sum-report/tests/test_report.py" in uris
    clean = runner.invoke(app, ["lint", "-f", "sarif", *sorted(map(str, EXAMPLES.iterdir()))])
    assert clean.exit_code == 0
    assert run_of(json.loads(clean.stdout))["results"] == []


positions = st.integers(min_value=1, max_value=10_000)
PATH_RE = r"[A-Za-z0-9 ._#%-]{1,12}(/[A-Za-z0-9 ._#%-]{1,12}){0,2}"


@st.composite
def findings(draw: st.DrawFn) -> Finding:
    rule = draw(st.sampled_from(RULES))
    line = draw(positions)
    end_line = draw(st.none() | st.integers(min_value=line, max_value=line + 50))
    return Finding(
        code=rule.code,
        rule=rule.name,
        severity=draw(st.sampled_from(Severity)),
        message=draw(st.text(min_size=1, max_size=40)),
        file=draw(st.from_regex(PATH_RE, fullmatch=True)),
        line=line,
        column=draw(positions),
        end_line=end_line,
        end_column=draw(st.none() | positions),
        fingerprint=draw(st.text(alphabet="0123456789abcdef", max_size=32)),
    )


@given(st.lists(findings(), max_size=8), st.sampled_from(["b", "bundles/x/", "./y", "/abs/z"]))
def test_any_report_renders_to_a_valid_log(found: list[Finding], path: str) -> None:
    report = LintReport(path, True, tuple(found), RULES)
    document = json.loads(format_sarif([report]))
    assert sarif_problems(document) == []
    assert len(run_of(document)["results"]) == len(found)
