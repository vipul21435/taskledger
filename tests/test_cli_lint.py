"""The ``taskledger lint`` and ``taskledger rules`` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import LINT_CLEAN_FILES, BundleFactory
from taskledger.cli import app
from taskledger.lint import REGISTRY

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "bundles"
FLAWED = ROOT / "examples" / "flawed" / "digit-sum-report"


def test_example_bundles_are_lint_clean() -> None:
    paths = sorted(str(path) for path in EXAMPLES.iterdir())
    result = runner.invoke(app, ["lint", *paths])
    assert result.exit_code == 0, result.output
    assert result.stdout == "No findings in 2 bundles.\n"


def test_flawed_example_fails_with_every_rule_it_breaks() -> None:
    result = runner.invoke(app, ["lint", "--format", "json", str(FLAWED)])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["summary"]["error"] == 5
    assert report["summary"]["warning"] == 2
    codes = sorted({finding["code"] for finding in report["bundles"][0]["findings"]})
    assert codes == ["TL002", "TL003", "TL004", "TL006"]


def test_text_output_with_and_without_hints(make_bundle: BundleFactory) -> None:
    bundle = make_bundle(files={"tests/test_answer.py": "import time\nT = time.time()\n"})
    with_hints = runner.invoke(app, ["lint", "--select", "TL004", str(bundle)])
    assert with_hints.exit_code == 0, with_hints.output
    lines = with_hints.stdout.splitlines()
    assert lines[0] == (
        f"{bundle}/tests/test_answer.py:2:5: warning TL004 nondeterminism: "
        "'time.time()' reads the wall clock, so the result changes between runs"
    )
    assert lines[1].startswith("    fix: Seed every generator")
    assert lines[2] == "Found 1 finding (1 warning) in 1 of 1 bundle."
    no_hints = runner.invoke(app, ["lint", "--select", "TL004", "--no-hints", str(bundle)])
    assert "fix:" not in no_hints.stdout


def test_select_and_ignore_accept_commas_and_repeats(make_bundle: BundleFactory) -> None:
    bundle = make_bundle()  # unpinned base images: TL002 errors
    failing = runner.invoke(app, ["lint", str(bundle)])
    assert failing.exit_code == 1
    assert "TL002" in failing.stdout
    ignored = runner.invoke(
        app, ["lint", "--ignore", "TL002, TL003", "--ignore", "TL004", str(bundle)]
    )
    assert ignored.exit_code == 0, ignored.output
    selected = runner.invoke(app, ["lint", "--select", "TL001,TL003", str(bundle)])
    assert selected.exit_code == 0, selected.output


def test_unknown_selector_exits_2(make_bundle: BundleFactory) -> None:
    bundle = make_bundle(files=dict(LINT_CLEAN_FILES))
    result = runner.invoke(app, ["lint", "--select", "TL999", str(bundle)])
    assert result.exit_code == 2
    assert "unknown rule code or prefix: 'TL999'" in result.stderr


def test_missing_bundle_is_a_tl000_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["lint", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "TL000" in result.stdout


@pytest.mark.parametrize("flag", [[], ["--json"]])
def test_rules_lists_every_registered_rule(flag: list[str]) -> None:
    result = runner.invoke(app, ["rules", *flag])
    assert result.exit_code == 0, result.output
    codes = [rule.code for rule in REGISTRY.rules]
    if flag:
        assert [entry["code"] for entry in json.loads(result.stdout)] == codes
    else:
        lines = result.stdout.splitlines()
        assert [line.split()[0] for line in lines] == codes
        assert lines[4].startswith("TL004  warning  nondeterminism: ")


def test_help_lists_only_implemented_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Validate, lint and content-hash" in result.stdout
    for command in ("validate", "hash", "lint", "rules"):
        assert command in result.stdout
    lint_help = runner.invoke(app, ["lint", "--help"])
    assert "after the lint section of each" in lint_help.stdout
