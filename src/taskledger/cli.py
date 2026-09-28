"""Command line entry point for TaskLedger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from taskledger import __version__
from taskledger.bundle import LoadResult, load_bundle

app = typer.Typer(
    name="taskledger",
    help="Lint, dedupe, lock and gate benchmark task bundles before submission.",
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
