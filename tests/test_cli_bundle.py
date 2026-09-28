from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from conftest import MINIMAL_MANIFEST, BundleFactory
from taskledger.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "bundles"


def test_validate_accepts_example_bundles() -> None:
    paths = sorted(str(path) for path in EXAMPLES.iterdir())
    result = runner.invoke(app, ["validate", *paths])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("ok ")
    assert lines[0].endswith("(integer-linear-system 1.0.0)")


def test_validate_reports_issues_and_fails(make_bundle: BundleFactory) -> None:
    bad = make_bundle(
        manifest=MINIMAL_MANIFEST.replace('"1.0.0"', '"1.0"'),
        name="bad-bundle",
    )
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1
    assert f"invalid  {bad}  (1 issue(s))" in result.stdout
    assert "  task.toml:task.version: must be a semantic version" in result.stdout


def test_validate_json_report(make_bundle: BundleFactory, tmp_path: Path) -> None:
    good = make_bundle(name="good")
    missing = tmp_path / "missing"
    result = runner.invoke(app, ["validate", "--json", str(good), str(missing)])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["valid"] is False
    first, second = report["bundles"]
    assert first == {
        "path": str(good),
        "valid": True,
        "id": "sum-of-squares",
        "version": "1.0.0",
        "issues": [],
    }
    assert second["valid"] is False
    assert second["issues"][0]["code"] == "bundle_not_found"


def test_validate_requires_a_path() -> None:
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 2
