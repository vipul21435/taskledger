"""The shipped example bundles are valid and genuinely solvable.

For every bundle under examples/bundles/ the grader must reject the untouched
baseline, accept the reference solution, give the same verdict when re-run and
reject a reference output with one extra line appended.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from taskledger.bundle import Bundle, load_bundle

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "bundles"
BUNDLES = sorted(path for path in EXAMPLES.iterdir() if (path / "task.toml").is_file())


def test_ships_two_original_examples() -> None:
    assert [path.name for path in BUNDLES] == ["integer-linear-system", "modular-inverse-table"]


@pytest.mark.parametrize("root", BUNDLES, ids=lambda path: path.name)
def test_example_bundle_is_valid(root: Path) -> None:
    result = load_bundle(root)
    assert result.issues == ()
    bundle = result.unwrap()
    assert bundle.id == root.name
    assert bundle.manifest.baseline is not None
    image = bundle.manifest.environment.image
    assert image is not None
    assert image.startswith(f"taskledger/{bundle.id}-env:")


def _grade(bundle: Bundle, grader_dir: Path, env: dict[str, str]) -> int:
    command = bundle.manifest.verifier.command
    assert command[0] == "pytest"
    completed = subprocess.run(
        [sys.executable, "-m", *command],
        cwd=grader_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return completed.returncode


@pytest.mark.slow
@pytest.mark.parametrize("root", BUNDLES, ids=lambda path: path.name)
def test_grader_separates_baseline_from_reference(root: Path, tmp_path: Path) -> None:
    bundle = load_bundle(root).unwrap()
    manifest = bundle.manifest
    assert manifest.baseline is not None
    grader_dir = tmp_path / "grader"
    shutil.copytree(bundle.path(manifest.tests.path), grader_dir / "tests")
    workdir = tmp_path / "work"
    shutil.copytree(bundle.path(manifest.baseline.path), workdir)
    env = {**os.environ, "TASK_WORKDIR": str(workdir), "PYTHON": sys.executable}

    assert _grade(bundle, grader_dir, env) == 1, "grader must reject the untouched baseline"

    before = {path.name for path in workdir.iterdir()}
    subprocess.run(
        ["bash", str(bundle.path(manifest.solution.entrypoint_path))],
        env=env,
        check=True,
        timeout=120,
    )
    assert _grade(bundle, grader_dir, env) == 0, "grader must accept the reference solution"
    assert _grade(bundle, grader_dir, env) == 0, "grader verdict must be stable on re-run"

    [output] = sorted({path.name for path in workdir.iterdir()} - before)
    with (workdir / output).open("ab") as handle:
        handle.write(b"0\n")
    assert _grade(bundle, grader_dir, env) == 1, "grader must reject a corrupted output"
