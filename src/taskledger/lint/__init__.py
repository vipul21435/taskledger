"""Bundle linter: a rule registry with stable codes, rules and report formatters."""

from taskledger.lint.engine import LintReport, build_context, lint_bundle, lint_paths
from taskledger.lint.finding import Finding, Severity, Violation
from taskledger.lint.formats import format_json, format_text
from taskledger.lint.registry import (
    REGISTRY,
    Rule,
    RuleRegistry,
    Selection,
    UnknownSelectorError,
    rule,
)

__all__ = [
    "REGISTRY",
    "Finding",
    "LintReport",
    "Rule",
    "RuleRegistry",
    "Selection",
    "Severity",
    "UnknownSelectorError",
    "Violation",
    "build_context",
    "format_json",
    "format_text",
    "lint_bundle",
    "lint_paths",
    "rule",
]
