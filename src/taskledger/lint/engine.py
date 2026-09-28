"""Run the enabled rules over a bundle and collect a :class:`LintReport`.

Selection comes from two layers, applied in order: ``[lint]`` in ``task.toml``
and then the ``select``/``ignore`` arguments (the CLI flags). See
:mod:`taskledger.lint.registry` for the precedence rules.

A finding is suppressed when the line it points at carries an inline comment
``taskledger: ignore[TL004]`` (codes or prefixes, comma separated) or a bare
``taskledger: ignore``. The comment works in any file type because it is
matched on the raw line text.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import taskledger.lint.rules  # noqa: F401  (registers the built-in rules)
from taskledger.bundle.loader import load_bundle
from taskledger.lint.context import Layout, LintContext, read_raw_manifest
from taskledger.lint.finding import Finding, Severity
from taskledger.lint.registry import REGISTRY, Rule, RuleRegistry, Selection

_SUPPRESS_RE: Final = re.compile(r"taskledger:\s*ignore(?:\[(?P<codes>[A-Z0-9,\s]*)\])?")


@dataclass(frozen=True, slots=True)
class LintReport:
    """Findings for one bundle, sorted by file and position."""

    path: str
    loaded: bool
    findings: tuple[Finding, ...]
    rules: tuple[Rule, ...]
    suppressed: int = 0

    def count(self, severity: Severity) -> int:
        """Number of findings with ``severity``."""
        return sum(1 for finding in self.findings if finding.severity is severity)

    @property
    def has_errors(self) -> bool:
        """True when any finding is an error (``taskledger lint`` then exits 1)."""
        return any(finding.severity is Severity.ERROR for finding in self.findings)


def _suppressed(finding: Finding, context: LintContext) -> bool:
    lines = context.lines(finding.file) if finding.file not in ("", ".") else ()
    if finding.line > len(lines):
        return False
    match = _SUPPRESS_RE.search(lines[finding.line - 1])
    if match is None:
        return False
    codes = match.group("codes")
    if codes is None:
        return True
    selectors = [code.strip() for code in codes.split(",") if code.strip()]
    return any(finding.code.startswith(selector) for selector in selectors)


def _fingerprint(finding: Finding, occurrence: int) -> str:
    material = "\x00".join((finding.code, finding.file, finding.message, str(occurrence)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _with_fingerprints(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    seen: Counter[tuple[str, str, str]] = Counter()
    out: list[Finding] = []
    for finding in findings:
        key = (finding.code, finding.file, finding.message)
        out.append(dataclasses.replace(finding, fingerprint=_fingerprint(finding, seen[key])))
        seen[key] += 1
    return tuple(out)


def _config_issues(layout: Layout, registry: RuleRegistry) -> tuple[tuple[str, str], ...]:
    issues: list[tuple[str, str]] = []
    for key in ("select", "ignore"):
        unknown = registry.unknown_selectors(getattr(layout.lint, key))
        if unknown:
            listed = ", ".join(repr(selector) for selector in unknown)
            issues.append((f"lint.{key}", f"unknown rule code or prefix: {listed}"))
    return tuple(issues)


def _known(selectors: Sequence[str], registry: RuleRegistry) -> tuple[str, ...]:
    unknown = set(registry.unknown_selectors(selectors))
    return tuple(selector for selector in selectors if selector not in unknown)


def build_context(path: str | os.PathLike[str], registry: RuleRegistry = REGISTRY) -> LintContext:
    """Load a bundle into a :class:`LintContext`, keeping loader issues for TL000."""
    result = load_bundle(path)
    if result.bundle is not None:
        manifest = result.bundle.manifest
        layout = Layout.from_manifest(manifest)
        root = result.bundle.root
    else:
        manifest = None
        root = result.root
        raw = read_raw_manifest(root) if root.is_dir() else None
        layout = Layout.best_effort(raw)
    return LintContext(
        root,
        layout,
        manifest=manifest,
        issues=result.issues,
        config_issues=_config_issues(layout, registry),
    )


def lint_bundle(
    path: str | os.PathLike[str],
    *,
    select: Sequence[str] = (),
    ignore: Sequence[str] = (),
    registry: RuleRegistry = REGISTRY,
) -> LintReport:
    """Lint one bundle directory.

    ``select`` and ``ignore`` override ``[lint]`` in ``task.toml``; unknown
    selectors raise :class:`~taskledger.lint.registry.UnknownSelectorError`
    (in ``task.toml`` they are reported as TL000 findings instead).
    """
    registry.check_selectors([*select, *ignore])
    context = build_context(path, registry)
    settings = context.layout.lint
    rules = registry.resolve(
        [
            Selection(_known(settings.select, registry), _known(settings.ignore, registry)),
            Selection(tuple(select), tuple(ignore)),
        ]
    )
    has_files = context.root.is_dir()
    findings: set[Finding] = set()
    suppressed = 0
    for rule in rules:
        if rule.needs_files and not has_files:
            continue
        for violation in rule.check(context):
            finding = rule.finding(violation)
            if _suppressed(finding, context):
                suppressed += 1
            else:
                findings.add(finding)
    ordered = sorted(findings, key=lambda finding: finding.sort_key)
    return LintReport(
        path=os.fspath(path),
        loaded=context.manifest is not None,
        findings=_with_fingerprints(ordered),
        rules=rules,
        suppressed=suppressed,
    )


def lint_paths(
    paths: Sequence[str | os.PathLike[str]],
    *,
    select: Sequence[str] = (),
    ignore: Sequence[str] = (),
    registry: RuleRegistry = REGISTRY,
) -> tuple[LintReport, ...]:
    """Lint several bundles with the same command-line selection."""
    registry.check_selectors([*select, *ignore])
    return tuple(
        lint_bundle(path, select=select, ignore=ignore, registry=registry) for path in paths
    )
