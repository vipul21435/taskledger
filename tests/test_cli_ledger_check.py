"""``taskledger ledger check`` and the near-duplicate gate on ``register``."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from paraphrase_corpus import disguise
from taskledger.cli import app

runner = CliRunner()
ORIGINAL = Path(__file__).resolve().parents[1] / "examples" / "bundles" / "integer-linear-system"


@pytest.fixture
def db(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'ledger.db'}"


def run(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.stdout, result.stderr


def resubmission(tmp_path: Path, task_id: str = "linear-system-v2") -> Path:
    copy = tmp_path / task_id
    shutil.copytree(ORIGINAL, copy)
    solve = copy / "solution" / "solve.py"
    solve.write_text(disguise(solve.read_text(encoding="utf-8")), encoding="utf-8")
    manifest = copy / "task.toml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace('id = "integer-linear-system"', f'id = "{task_id}"'), encoding="utf-8"
    )
    return copy


def test_check_is_clear_on_an_empty_ledger(db: str) -> None:
    code, out, _ = run("ledger", "check", "--db", db, str(ORIGINAL))
    assert code == 0
    assert out == f"clear     {ORIGINAL}  (no exact, ID or near-duplicate collision)\n"


def test_check_reports_exact_id_and_near_duplicates_together(db: str, tmp_path: Path) -> None:
    assert run("ledger", "register", "--db", db, str(ORIGINAL))[0] == 0
    code, out, _ = run("ledger", "check", "--db", db, str(ORIGINAL))
    assert code == 1
    lines = out.splitlines()
    assert lines[0].startswith("exact     integer-linear-system  (same canonical hash sha256:")
    assert (
        lines[1]
        == "id        integer-linear-system  (folds to the same ID as 'integer-linear-system')"
    )
    assert lines[2].startswith("near-dup  integer-linear-system  1.00")

    copy = resubmission(tmp_path)
    code, out, _ = run("ledger", "check", "--db", db, "--json", str(copy))
    assert code == 1
    report = json.loads(out)
    assert (report["exact"], report["id_collision"], report["threshold"]) == (None, None, 0.5)
    [near] = report["near_duplicates"]
    assert (near["slug"], near["solution_similarity"]) == ("integer-linear-system", 1.0)


def test_register_refuses_a_near_duplicate_unless_allowed(db: str, tmp_path: Path) -> None:
    assert run("ledger", "register", "--db", db, str(ORIGINAL))[0] == 0
    copy = resubmission(tmp_path)
    code, _, err = run("ledger", "register", "--db", db, str(copy))
    assert code == 1
    assert "near-duplicate: 'linear-system-v2' is 1.00 similar to 'integer-linear-system'" in err

    code, out, _ = run("ledger", "register", "--db", db, "--allow-near-dup", str(copy))
    assert code == 0, out
    code, out, _ = run("ledger", "history", "--db", db, "--json", "linear-system-v2")
    assert code == 0
    payload = json.loads(json.loads(out)[0]["payload"])
    assert payload["near_duplicates_allowed"][0]["slug"] == "integer-linear-system"


def test_threshold_option_is_bounded(db: str) -> None:
    code, _, err = run("ledger", "check", "--db", db, "--threshold", "1.5", str(ORIGINAL))
    assert code == 2
    assert "threshold" in err
