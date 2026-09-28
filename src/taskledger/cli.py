"""Command line entry point for TaskLedger."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from taskledger import __version__
from taskledger.bundle import HashError, LoadResult, hash_bundle, load_bundle
from taskledger.lint import REGISTRY, UnknownSelectorError, format_json, format_text, lint_paths

app = typer.Typer(
    name="taskledger",
    help="Validate, lint and content-hash benchmark task bundles before submission.",
    no_args_is_help=True,
    add_completion=False,
)

JsonFlag = Annotated[bool, typer.Option("--json", help="Print a JSON report instead of text.")]


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"taskledger {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = False,
) -> None:
    """TaskLedger command line interface."""


@app.command("version")
def version_cmd() -> None:
    """Print the installed TaskLedger version."""
    typer.echo(__version__)


def _validation_record(path: Path, result: LoadResult) -> dict[str, Any]:
    bundle = result.bundle
    return {
        "path": str(path),
        "valid": bundle is not None,
        "id": bundle.id if bundle else None,
        "version": bundle.version if bundle else None,
        "issues": [issue.to_dict() for issue in result.issues],
    }


@app.command("validate")
def validate_cmd(
    paths: Annotated[list[Path], typer.Argument(help="Bundle directories to validate.")],
    as_json: JsonFlag = False,
) -> None:
    """Check bundles against the task bundle schema. Exit code 1 if any is invalid."""
    results = [(path, load_bundle(path)) for path in paths]
    all_valid = all(result.ok for _, result in results)
    if as_json:
        report = {
            "valid": all_valid,
            "bundles": [_validation_record(path, result) for path, result in results],
        }
        typer.echo(json.dumps(report, indent=2))
    else:
        for path, result in results:
            if result.bundle is not None:
                typer.echo(f"ok       {path}  ({result.bundle.id} {result.bundle.version})")
                continue
            typer.echo(f"invalid  {path}  ({len(result.issues)} issue(s))")
            for issue in result.issues:
                typer.echo(f"  {issue}")
    if not all_valid:
        raise typer.Exit(code=1)


@app.command("hash")
def hash_cmd(
    path: Annotated[Path, typer.Argument(help="Bundle directory to hash.")],
    as_json: JsonFlag = False,
) -> None:
    """Print the canonical content hash of a bundle.

    Line endings, manifest formatting, the authors/created_at/notes metadata and
    OS or tool clutter (.DS_Store, __pycache__, .git) never change the hash; any
    semantic change does. Exit code 1 if the bundle is invalid.
    """
    result = load_bundle(path)
    if result.bundle is None:
        typer.echo(f"invalid  {path}  ({len(result.issues)} issue(s))", err=True)
        for issue in result.issues:
            typer.echo(f"  {issue}", err=True)
        raise typer.Exit(code=1)
    try:
        digest = hash_bundle(result.bundle)
    except HashError as exc:
        typer.echo(f"cannot hash {path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if as_json:
        record = {
            "path": str(path),
            "id": result.bundle.id,
            "version": result.bundle.version,
            **digest.to_dict(),
        }
        typer.echo(json.dumps(record, indent=2))
    else:
        typer.echo(f"{digest.value}  {path}")


class LintFormat(StrEnum):
    """Output formats of ``taskledger lint``."""

    TEXT = "text"
    JSON = "json"


def _split_selectors(values: list[str] | None) -> list[str]:
    """Flatten repeated and comma separated ``--select``/``--ignore`` values."""
    return [part.strip() for value in values or [] for part in value.split(",") if part.strip()]


@app.command("lint")
def lint_cmd(
    paths: Annotated[list[Path], typer.Argument(help="Bundle directories to lint.")],
    output_format: Annotated[
        LintFormat, typer.Option("--format", "-f", help="Report format.")
    ] = LintFormat.TEXT,
    select: Annotated[
        list[str] | None,
        typer.Option(help="Rule codes or prefixes to enable (repeatable, comma separated)."),
    ] = None,
    ignore: Annotated[
        list[str] | None,
        typer.Option(help="Rule codes or prefixes to disable (repeatable, comma separated)."),
    ] = None,
    hints: Annotated[
        bool, typer.Option("--hints/--no-hints", help="Print a fix hint under each finding.")
    ] = True,
) -> None:
    """Run the lint rules over bundles.

    --select and --ignore are applied after the lint section of each
    bundle's task.toml. Exit code 1 if any finding is an error, 2 on an
    unknown rule code.
    """
    try:
        reports = lint_paths(
            [str(path) for path in paths],
            select=_split_selectors(select),
            ignore=_split_selectors(ignore),
        )
    except UnknownSelectorError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if output_format is LintFormat.JSON:
        typer.echo(format_json(reports), nl=False)
    else:
        typer.echo(format_text(reports, hints=hints), nl=False)
    if any(report.has_errors for report in reports):
        raise typer.Exit(code=1)


@app.command("rules")
def rules_cmd(as_json: JsonFlag = False) -> None:
    """List the registered lint rules with their codes and default severities."""
    rules = REGISTRY.rules
    if as_json:
        typer.echo(json.dumps([rule.to_dict() for rule in rules], indent=2))
        return
    for rule in rules:
        typer.echo(f"{rule.code}  {rule.severity.value:<7}  {rule.name}: {rule.description}")
