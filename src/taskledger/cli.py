"""Command line entry point for TaskLedger."""

from __future__ import annotations

from typing import Annotated

import typer

from taskledger import __version__

app = typer.Typer(
    name="taskledger",
    help="Lint, dedupe, lock and gate benchmark task bundles before submission.",
    no_args_is_help=True,
    add_completion=False,
)


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
