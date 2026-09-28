"""TL000: the bundle must load (schema and declared paths) and ``[lint]`` must be valid."""

from __future__ import annotations

from collections.abc import Iterator

from taskledger.bundle.manifest import MANIFEST_FILENAME
from taskledger.lint.context import LintContext
from taskledger.lint.finding import Severity, Violation
from taskledger.lint.registry import rule


@rule(
    "TL000",
    name="invalid-bundle",
    severity=Severity.ERROR,
    description=(
        "The bundle must pass `taskledger validate`: a readable task.toml that matches "
        "the schema and declares paths that exist inside the bundle."
    ),
    fix="Run `taskledger validate PATH` and fix every issue it lists.",
    needs_files=False,
)
def invalid_bundle(ctx: LintContext) -> Iterator[Violation]:
    """Report loader issues and unknown rule selectors in ``[lint]``."""
    for issue in ctx.issues:
        if not issue.file:
            yield Violation(".", f"{issue.message} ({issue.code})")
            continue
        line = ctx.manifest_line(issue.loc) if issue.file == MANIFEST_FILENAME else 1
        where = f"{issue.loc}: " if issue.loc else ""
        yield Violation(issue.file, f"{where}{issue.message} ({issue.code})", line)
    for loc, message in ctx.config_issues:
        yield Violation(MANIFEST_FILENAME, f"{loc}: {message}", ctx.manifest_line(loc))
