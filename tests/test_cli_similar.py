"""``taskledger similar``: near-duplicate pairs among bundles."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from conftest import MINIMAL_MANIFEST, BundleFactory
from paraphrase_corpus import disguise
from taskledger.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "bundles"
ORIGINAL = EXAMPLES / "integer-linear-system"


def _resubmission(tmp_path: Path) -> Path:
    copy = tmp_path / "resubmitted"
    shutil.copytree(ORIGINAL, copy)
    solve = copy / "solution" / "solve.py"
    solve.write_text(disguise(solve.read_text(encoding="utf-8")), encoding="utf-8")
    instruction = copy / "instruction.md"
    instruction.write_text(
        "# A new task\n\nSomething else entirely: summarize a log file by hour.\n",
        encoding="utf-8",
    )
    return copy


def test_distinct_examples_are_not_near_duplicates() -> None:
    result = runner.invoke(app, ["similar", *sorted(str(p) for p in EXAMPLES.iterdir())])
    assert result.exit_code == 0, result.output
    assert result.stdout == "No near-duplicates among 2 bundle(s) at threshold 0.5.\n"


def test_renamed_solution_under_a_new_instruction_is_reported(tmp_path: Path) -> None:
    copy = _resubmission(tmp_path)
    result = runner.invoke(app, ["similar", str(ORIGINAL), str(copy)])
    assert result.exit_code == 1
    line = result.stdout.strip()
    assert line.startswith(f"near-dup  1.00  {ORIGINAL}  {copy}  (instruction 0.")
    assert line.endswith("solution 1.00)")

    result = runner.invoke(app, ["similar", "--json", str(ORIGINAL), str(copy)])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert (report["threshold"], report["bands"], report["rows"]) == (0.5, 25, 5)
    [pair] = report["pairs"]
    assert (pair["a"], pair["b"], pair["solution_similarity"]) == (str(ORIGINAL), str(copy), 1.0)
    assert pair["instruction_similarity"] < 0.5


def test_same_path_twice_is_indexed_once() -> None:
    result = runner.invoke(app, ["similar", str(ORIGINAL), str(ORIGINAL)])
    assert result.exit_code == 1
    assert len(result.stdout.splitlines()) == 1


def test_invalid_bundles_fail(make_bundle: BundleFactory) -> None:
    bad = make_bundle(manifest=MINIMAL_MANIFEST.replace('"1.0.0"', '"1.0"'), name="bad")
    result = runner.invoke(app, ["similar", str(bad)])
    assert result.exit_code == 1
    assert "invalid" in result.stderr

    result = runner.invoke(app, ["similar", "--json", "--threshold", "0.8", str(ORIGINAL)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["pairs"] == []
