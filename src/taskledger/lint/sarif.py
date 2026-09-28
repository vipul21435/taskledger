"""SARIF 2.1.0 rendering of lint reports, plus a checker for the required fields.

``taskledger lint --format sarif`` writes one SARIF log with a single run: the
tool driver lists every rule that ran (id, title, description, fix hint and
default level) and each finding becomes a result with a rule id and index, a
level, a message, one physical location and a partial fingerprint.

File locations are URIs. A relative display path (the usual case when the
linter runs from the repository root) is percent-encoded and anchored at
``%SRCROOT%``, which is what code-scanning uploads expect; absolute paths and
paths that climb out of the working directory (``../x``) become ``file://``
URIs. Columns are counted in Unicode code points, which the run declares with
``columnKind``.

:func:`sarif_problems` checks a document against the properties the SARIF
2.1.0 JSON schema marks as required (and its enums and minimums), plus the
fields GitHub code scanning additionally requires of an upload: a results
array per run, and on each result a ``ruleId``, ``message.text`` and a
location with an artifact URI and a ``region.startLine``. The test suite runs
it over every document the formatter produces.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from taskledger import __version__
from taskledger.lint.engine import LintReport
from taskledger.lint.finding import Finding
from taskledger.lint.formats import display_path
from taskledger.lint.registry import REGISTRY, Rule

SARIF_VERSION: Final = "2.1.0"
SARIF_SCHEMA: Final = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI: Final = "https://github.com/vipul21435/taskledger"
SRCROOT: Final = "%SRCROOT%"
#: Key of the line-independent fingerprint in ``result.partialFingerprints``.
FINGERPRINT_KEY: Final = "taskledger/v1"
#: GitHub ``security-severity`` scores (0-10) of rules tagged ``security``.
SECURITY_SEVERITY: Final[Mapping[str, str]] = {"TL006": "8.0"}

LEVELS: Final = frozenset({"none", "note", "warning", "error"})
COLUMN_KINDS: Final = frozenset({"utf16CodeUnits", "unicodeCodePoints"})
_DRIVE_RE: Final = re.compile(r"[A-Za-z]:/")
_URI_FORBIDDEN_RE: Final = re.compile(r"[\s\\\"<>^`{|}]")


# -- rendering -----------------------------------------------------------------


def artifact_location(path: str) -> dict[str, str]:
    """SARIF ``artifactLocation`` for a display path (see module docs)."""
    if _DRIVE_RE.match(path):
        return {"uri": "file:///" + quote(path, safe="/:")}
    if path.startswith("/"):
        return {"uri": "file://" + quote(path, safe="/")}
    if path == ".." or path.startswith("../"):
        return {"uri": Path(path).resolve().as_uri()}
    return {"uri": quote(path, safe="/"), "uriBaseId": SRCROOT}


def _title(rule: Rule) -> str:
    return rule.name.replace("-", " ").capitalize()


def rule_descriptor(rule: Rule) -> dict[str, Any]:
    """SARIF ``reportingDescriptor`` for a registered rule."""
    descriptor: dict[str, Any] = {
        "id": rule.code,
        "name": rule.name,
        "shortDescription": {"text": _title(rule)},
        "fullDescription": {"text": rule.description},
        "help": {
            "text": f"{rule.description}\n\nFix: {rule.fix}",
            "markdown": f"{rule.description}\n\n**Fix:** {rule.fix}",
        },
        "defaultConfiguration": {"level": rule.severity.value},
    }
    if rule.code in SECURITY_SEVERITY:
        descriptor["properties"] = {
            "tags": ["security"],
            "security-severity": SECURITY_SEVERITY[rule.code],
        }
    return descriptor


def _region(finding: Finding) -> dict[str, int]:
    region = {"startLine": finding.line, "startColumn": finding.column}
    if finding.end_line is not None:
        region["endLine"] = finding.end_line
    if finding.end_column is not None:
        region["endColumn"] = finding.end_column
    return region


def _fingerprint(uri: str, finding: Finding) -> str:
    identity = finding.fingerprint or f"{finding.code}\x00{finding.message}"
    return hashlib.sha256(f"{uri}\x00{identity}".encode()).hexdigest()[:32]


def _result(report: LintReport, finding: Finding, index: Mapping[str, int]) -> dict[str, Any]:
    location = artifact_location(display_path(report.path, finding.file))
    rule_index = {"ruleIndex": index[finding.code]} if finding.code in index else {}
    return {
        "ruleId": finding.code,
        **rule_index,
        "level": finding.severity.value,
        "message": {"text": finding.message},
        "locations": [
            {"physicalLocation": {"artifactLocation": location, "region": _region(finding)}}
        ],
        "partialFingerprints": {FINGERPRINT_KEY: _fingerprint(location["uri"], finding)},
    }


def build_sarif(reports: Sequence[LintReport]) -> dict[str, Any]:
    """The SARIF log for ``reports`` as a JSON-ready dict (one run)."""
    rules: dict[str, Rule] = {}
    for report in reports:
        for rule in report.rules:
            rules.setdefault(rule.code, rule)
    for report in reports:  # a hand-built report may omit rules its findings use
        for finding in report.findings:
            if finding.code not in rules and finding.code in REGISTRY:
                rules[finding.code] = REGISTRY.get(finding.code)
    ordered = [rules[code] for code in sorted(rules)]
    index = {rule.code: position for position, rule in enumerate(ordered)}
    results = [_result(report, finding, index) for report in reports for finding in report.findings]
    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "taskledger",
                        "version": __version__,
                        "semanticVersion": __version__,
                        "informationUri": INFORMATION_URI,
                        "rules": [rule_descriptor(rule) for rule in ordered],
                    }
                },
                "columnKind": "unicodeCodePoints",
                "results": results,
            }
        ],
    }


def format_sarif(reports: Sequence[LintReport]) -> str:
    """SARIF 2.1.0 JSON for GitHub code scanning and other SARIF viewers."""
    return json.dumps(build_sarif(reports), indent=2) + "\n"


# -- checking ------------------------------------------------------------------


#: Returned by :meth:`_Checker.required` for an absent property (None is a value).
_MISSING: Final[Any] = object()


class _Checker:
    """Collects problems as ``<JSON path>: <what is wrong>`` strings."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def report(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    def obj(self, value: object, where: str) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        self.report(where, "must be an object")
        return None

    def array(self, value: object, where: str, *, non_empty: bool = False) -> list[Any]:
        if not isinstance(value, list):
            self.report(where, "must be an array")
            return []
        if non_empty and not value:
            self.report(where, "must not be empty")
        return value

    def required(self, parent: Mapping[str, Any], key: str, where: str) -> Any:
        if key not in parent:
            self.report(where, f"missing required property '{key}'")
            return _MISSING
        return parent[key]

    def string(self, value: object, where: str) -> None:
        if value is _MISSING:
            return
        if not isinstance(value, str) or not value:
            self.report(where, "must be a non-empty string")

    def integer(self, value: object, where: str, minimum: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            self.report(where, f"must be an integer >= {minimum}")

    def enum(self, value: object, where: str, allowed: frozenset[str]) -> None:
        if not isinstance(value, str) or value not in allowed:
            self.report(where, f"must be one of {', '.join(sorted(allowed))}, got {value!r}")

    def text(self, parent: Mapping[str, Any], key: str, where: str) -> None:
        """A ``message`` or ``multiformatMessageString``: requires ``text``."""
        value = self.obj(parent[key], f"{where}.{key}") if key in parent else None
        if value is not None:
            self.string(self.required(value, "text", f"{where}.{key}"), f"{where}.{key}.text")

    def log(self, document: object) -> None:
        log = self.obj(document, "$")
        if log is None:
            return
        version = self.required(log, "version", "$")
        if version is not _MISSING and version != SARIF_VERSION:
            self.report("$.version", f"must be {SARIF_VERSION!r}, got {version!r}")
        runs = self.required(log, "runs", "$")
        if runs is not _MISSING:
            for number, run in enumerate(self.array(runs, "$.runs")):
                self.run(run, f"$.runs[{number}]")

    def run(self, value: object, where: str) -> None:
        run = self.obj(value, where)
        if run is None:
            return
        rule_ids: list[str] = []
        tool = self.required(run, "tool", where)
        tool = self.obj(tool, f"{where}.tool") if tool is not _MISSING else None
        if tool is not None:
            driver = self.required(tool, "driver", f"{where}.tool")
            if driver is not _MISSING:
                rule_ids = self.driver(driver, f"{where}.tool.driver")
        if "columnKind" in run:
            self.enum(run["columnKind"], f"{where}.columnKind", COLUMN_KINDS)
        results = self.required(run, "results", where)  # GitHub requires the array
        if results is not _MISSING:
            for number, result in enumerate(self.array(results, f"{where}.results")):
                self.result(result, f"{where}.results[{number}]", rule_ids)

    def driver(self, value: object, where: str) -> list[str]:
        driver = self.obj(value, where)
        if driver is None:
            return []
        self.string(self.required(driver, "name", where), f"{where}.name")
        ids: list[str] = []
        for number, item in enumerate(self.array(driver.get("rules", []), f"{where}.rules")):
            at = f"{where}.rules[{number}]"
            descriptor = self.obj(item, at)
            if descriptor is None:
                ids.append("")
                continue
            rule_id = self.required(descriptor, "id", at)
            self.string(rule_id, f"{at}.id")
            if isinstance(rule_id, str) and rule_id in ids:
                self.report(f"{at}.id", f"duplicate rule id {rule_id!r}")
            ids.append(rule_id if isinstance(rule_id, str) else "")
            for key in ("shortDescription", "fullDescription", "help"):
                self.text(descriptor, key, at)
            if "defaultConfiguration" in descriptor:
                config = self.obj(descriptor["defaultConfiguration"], f"{at}.defaultConfiguration")
                if config is not None and "level" in config:
                    self.enum(config["level"], f"{at}.defaultConfiguration.level", LEVELS)
        return ids

    def result(self, value: object, where: str, rule_ids: Sequence[str]) -> None:
        result = self.obj(value, where)
        if result is None:
            return
        self.required(result, "message", where)
        self.text(result, "message", where)
        rule_id = self.required(result, "ruleId", where)  # GitHub
        self.string(rule_id, f"{where}.ruleId")
        if "ruleIndex" in result:
            rule_index = result["ruleIndex"]
            self.integer(rule_index, f"{where}.ruleIndex", -1)
            if isinstance(rule_index, int) and rule_index >= 0:
                if rule_index >= len(rule_ids):
                    self.report(f"{where}.ruleIndex", f"{rule_index} is not a rule index")
                elif rule_ids[rule_index] != rule_id:
                    self.report(
                        f"{where}.ruleIndex",
                        f"points at rule {rule_ids[rule_index]!r}, not {rule_id!r}",
                    )
        if "level" in result:
            self.enum(result["level"], f"{where}.level", LEVELS)
        locations = self.required(result, "locations", where)  # GitHub
        if locations is not _MISSING:
            items = self.array(locations, f"{where}.locations", non_empty=True)
            for number, location in enumerate(items):
                self.location(location, f"{where}.locations[{number}]")
        fingerprints = result.get("partialFingerprints", {})
        if not isinstance(fingerprints, dict) or not all(
            isinstance(item, str) for item in fingerprints.values()
        ):
            self.report(f"{where}.partialFingerprints", "must map names to strings")

    def location(self, value: object, where: str) -> None:
        location = self.obj(value, where)
        if location is None:
            return
        at = f"{where}.physicalLocation"
        if "physicalLocation" not in location:
            self.report(where, "missing required property 'physicalLocation'")  # GitHub
            return
        physical = self.obj(location["physicalLocation"], at)
        if physical is None:
            return
        if "artifactLocation" not in physical:
            self.report(at, "missing required property 'artifactLocation'")
        else:
            artifact = self.obj(physical["artifactLocation"], f"{at}.artifactLocation")
            if artifact is not None:
                self.uri(artifact, f"{at}.artifactLocation")
        if "region" not in physical:
            self.report(at, "missing required property 'region'")  # GitHub
        else:
            region = self.obj(physical["region"], f"{at}.region")
            if region is not None:
                self.region(region, f"{at}.region")

    def uri(self, artifact: Mapping[str, Any], where: str) -> None:
        uri = self.required(artifact, "uri", where)  # GitHub
        self.string(uri, f"{where}.uri")
        if isinstance(uri, str) and _URI_FORBIDDEN_RE.search(uri):
            self.report(f"{where}.uri", f"is not a valid URI reference: {uri!r}")

    def region(self, region: Mapping[str, Any], where: str) -> None:
        start = self.required(region, "startLine", where)  # GitHub
        for key in ("startLine", "startColumn", "endLine", "endColumn"):
            if key in region:
                self.integer(region[key], f"{where}.{key}", 1)
        end = region.get("endLine")
        if isinstance(start, int) and isinstance(end, int) and end < start:
            self.report(f"{where}.endLine", f"{end} is before startLine {start}")


def sarif_problems(document: object) -> list[str]:
    """Everything that keeps ``document`` from being a SARIF 2.1.0 log GitHub accepts.

    An empty list means every property the schema requires is present and
    well typed, enums and minimums hold, rule indexes point at the rule with
    the result's id, and the GitHub-required result fields are set.
    """
    checker = _Checker()
    checker.log(document)
    return checker.problems
