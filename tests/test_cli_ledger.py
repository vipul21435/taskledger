"""``taskledger ledger`` commands against a temporary SQLite ledger."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from conftest import MINIMAL_MANIFEST, BundleFactory
from taskledger.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "bundles"


@pytest.fixture
def db(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'ledger.db'}"


def run(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.stdout, result.stderr


def test_init_is_idempotent(db: str) -> None:
    for _ in range(2):
        code, out, _ = run("ledger", "init", "--db", db)
        assert code == 0
        assert out.startswith("ledger ready at sqlite:///")
        assert out.rstrip().endswith("(schema revision 0002)")


def test_init_uses_taskledger_home_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TASKLEDGER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TASKLEDGER_DATABASE_URL", raising=False)
    code, out, _ = run("ledger", "init")
    assert code == 0
    assert (tmp_path / "home" / "ledger.db").is_file()
    assert "home/ledger.db" in out


def test_register_collisions_transitions_history_and_verify(db: str, tmp_path: Path) -> None:
    bundle = EXAMPLES / "modular-inverse-table"
    code, out, err = run("ledger", "register", "--db", db, "--actor", "alice", str(bundle))
    assert code == 0, err
    assert out.startswith("registered  modular-inverse-table 1.0.0  draft  sha256:")

    # Same bundle again: exact collision.
    code, _, err = run("ledger", "register", "--db", db, str(bundle))
    assert code == 1
    assert "exact collision" in err
    assert "already registered as modular-inverse-table 1.0.0" in err

    # A cosmetically different copy (CRLF instruction) is still the same content.
    copy = tmp_path / "copy"
    shutil.copytree(bundle, copy, ignore=shutil.ignore_patterns("__pycache__"))
    text = (copy / "instruction.md").read_bytes()
    (copy / "instruction.md").write_bytes(text.replace(b"\n", b"\r\n"))
    code, _, err = run("ledger", "register", "--db", db, str(copy))
    assert code == 1
    assert "exact collision" in err

    # A semantic change under the same ID: ID collision.
    (copy / "instruction.md").write_bytes(text + b"\nOne more requirement.\n")
    code, _, err = run("ledger", "register", "--db", db, str(copy))
    assert code == 1
    assert "ID collision: 'modular-inverse-table' folds to 'modularinversetable'" in err

    code, out, _ = run("ledger", "status", "--db", db, "--json", "modular-inverse-table")
    assert code == 0
    assert json.loads(out)["status"] == "draft"

    code, out, _ = run(
        "ledger", "transition", "--db", db, "--actor", "alice", "modular-inverse-table", "submitted"
    )
    assert (code, out) == (0, "modular-inverse-table: draft -> submitted\n")
    code, _, err = run("ledger", "transition", "--db", db, "modular-inverse-table", "accepted")
    assert code == 1
    assert "cannot move from submitted to accepted (allowed from submitted: in_review)" in err
    code, out, _ = run(
        "ledger",
        "transition",
        "--db",
        db,
        "--actor",
        "rev",
        "--note",
        "graders re-run",
        "modular-inverse-table",
        "in_review",
    )
    assert code == 0

    code, out, _ = run("ledger", "status", "--db", db, "modular-inverse-table")
    assert " in_review " in out

    code, out, _ = run("ledger", "history", "--db", db, "modular-inverse-table")
    lines = out.splitlines()
    assert len(lines) == 3
    assert "  alice  register  modular-inverse-table  " in lines[0]
    assert '"note":"graders re-run"' in lines[2]
    code, out, _ = run("ledger", "history", "--db", db, "--json")
    assert [row["action"] for row in json.loads(out)] == ["register", "transition", "transition"]

    code, out, _ = run("ledger", "verify", "--db", db)
    assert code == 0
    assert out.startswith("audit chain ok: 3 entries, head ")


def test_verify_reports_tampering(db: str, make_bundle: BundleFactory) -> None:
    for index in range(3):
        manifest = MINIMAL_MANIFEST.replace('"sum-of-squares"', f'"sum-of-squares-{index}"')
        bundle = str(make_bundle(manifest=manifest))
        # Same instruction and solution under three IDs: near-duplicates by design.
        assert run("ledger", "register", "--db", db, "--allow-near-dup", bundle)[0] == 0
    engine = sa.create_engine(db)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TRIGGER audit_log_no_update"))
        conn.execute(sa.text("UPDATE audit_log SET actor = 'mallory' WHERE seq = 2"))
    engine.dispose()
    code, out, _ = run("ledger", "verify", "--db", db)
    assert code == 1
    assert out.startswith("audit chain BROKEN at seq 2: row content does not match its hash")
    code, out, _ = run("ledger", "verify", "--db", db, "--json")
    assert code == 1
    assert json.loads(out)["first_bad_seq"] == 2


def test_errors(db: str, make_bundle: BundleFactory, tmp_path: Path) -> None:
    code, _, err = run("ledger", "status", "--db", db, "nope")
    assert code == 1
    assert "no task 'nope' in the ledger" in err
    code, _, err = run("ledger", "transition", "--db", db, "nope", "submitted")
    assert code == 1
    code, _, err = run("ledger", "register", "--db", db, str(make_bundle(manifest=None)))
    assert code == 1
    assert "is not a valid bundle" in err
    code, _, _ = run("ledger", "transition", "--db", db, "nope", "shipped")
    assert code == 2
    fifo_bundle = make_bundle(name="with-fifo")
    os.mkfifo(fifo_bundle / "pipe")
    code, _, err = run("ledger", "register", "--db", db, str(fifo_bundle))
    assert code == 1
    assert "cannot hash" in err
    code, out, _ = run("ledger", "register", "--db", db, "--json", str(make_bundle()))
    assert code == 0
    assert json.loads(out)["slug"] == "sum-of-squares"
    code, out, _ = run("ledger", "history", "--db", db)
    assert out.count("\n") == 1


def test_revise_command(db: str, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(
        EXAMPLES / "modular-inverse-table", bundle, ignore=shutil.ignore_patterns("__pycache__")
    )
    assert run("ledger", "register", "--db", db, str(bundle))[0] == 0
    code, _, err = run("ledger", "revise", "--db", db, str(bundle))
    assert code == 1
    assert "exact collision" in err
    (bundle / "instruction.md").write_bytes(
        (bundle / "instruction.md").read_bytes() + b"\nPrint one inverse per line.\n"
    )
    code, out, err = run("ledger", "revise", "--db", db, "--actor", "alice", str(bundle))
    assert code == 0, err
    assert out.startswith("revised     modular-inverse-table 1.0.0  draft  sha256:")
    (bundle / "instruction.md").write_bytes(b"A shorter instruction.\n")
    code, out, _ = run("ledger", "revise", "--db", db, "--json", str(bundle))
    assert code == 0
    assert json.loads(out)["status"] == "draft"
    assert run("ledger", "transition", "--db", db, "modular-inverse-table", "submitted")[0] == 0
    (bundle / "instruction.md").write_bytes(b"Yet another change.\n")
    code, _, err = run("ledger", "revise", "--db", db, str(bundle))
    assert code == 1
    assert "cannot revise content in status submitted" in err
    code, out, _ = run("ledger", "history", "--db", db, "--json")
    actions = [row["action"] for row in json.loads(out)]
    assert actions == ["register", "revise", "revise", "transition"]
