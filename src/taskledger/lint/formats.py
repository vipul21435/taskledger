"""Human text and JSON renderings of lint reports.

Both formats print paths as ``<bundle path as given>/<file>``, so a path in
the output can be pasted straight into an editor from the directory the
command ran in.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Sequence
from typing import Final

from taskledger import __version__
from taskledger.lint.engine import LintReport
from taskledger.lint.finding import Finding, Severity

#: Version of the JSON report layout; bumped on incompatible changes.
JSON_FORMAT_VERSION: Final = 1


def display_path(bundle_path: str, file: str) -> str:
    """``bundle_path`` joined with a bundle-relative file, normalized, POSIX."""
    base = bundle_path.replace("\\", "/")
    if file in ("", "."):
        return posixpath.normpath(base)
    return posixpath.normpath(posixpath.join(base, file))


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def summarize(reports: Sequence[LintReport]) -> dict[str, int]:
    """Counts across reports: bundles, findings per severity, suppressed."""
    findings = [finding for report in reports for finding in report.findings]
    summary = {
        "bundles": len(reports),
        "bundles_with_findings": sum(1 for report in reports if report.findings),
        "findings": len(findings),
    }
    for severity in Severity:
        summary[severity.value] = sum(1 for f in findings if f.severity is severity)
    summary["suppressed"] = sum(report.suppressed for report in reports)
    return summary


def _text_line(report: LintReport, finding: Finding) -> str:
    where = f"{display_path(report.path, finding.file)}:{finding.line}:{finding.column}"
    return f"{where}: {finding.severity.value} {finding.code} {finding.rule}: {finding.message}"


def format_text(reports: Sequence[LintReport], *, hints: bool = True) -> str:
    """One line per finding plus an optional fix hint, then a summary line."""
    lines: list[str] = []
    for report in reports:
        for finding in report.findings:
            lines.append(_text_line(report, finding))
            if hints and finding.fix:
                lines.append(f"    fix: {finding.fix}")
    summary = summarize(reports)
    bundles = _plural(summary["bundles"], "bundle")
    if summary["findings"] == 0:
        tail = f"No findings in {bundles}."
    else:
        counts = ", ".join(
            _plural(summary[severity.value], severity.value)
            for severity in Severity
            if summary[severity.value]
        )
        tail = (
            f"Found {_plural(summary['findings'], 'finding')} ({counts}) in "
            f"{summary['bundles_with_findings']} of {bundles}."
        )
    if summary["suppressed"]:
        tail += f" {summary['suppressed']} suppressed inline."
    lines.append(tail)
    return "\n".join(lines) + "\n"


def report_to_dict(report: LintReport) -> dict[str, object]:
    """JSON-ready view of one bundle's report."""
    return {
        "path": report.path,
        "loaded": report.loaded,
        "rules": [rule.code for rule in report.rules],
        "suppressed": report.suppressed,
        "findings": [
            {**finding.to_dict(), "path": display_path(report.path, finding.file)}
            for finding in report.findings
        ],
    }


def format_json(reports: Sequence[LintReport]) -> str:
    """A stable, indented JSON document for machines (see :data:`JSON_FORMAT_VERSION`)."""
    document = {
        "tool": "taskledger",
        "version": __version__,
        "format_version": JSON_FORMAT_VERSION,
        "summary": summarize(reports),
        "bundles": [report_to_dict(report) for report in reports],
    }
    return json.dumps(document, indent=2) + "\n"
