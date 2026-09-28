"""scripts/demo.sh runs end to end, so `make demo` and the image demo cannot rot."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.slow
def test_demo_script_passes() -> None:
    cli = Path(sys.executable).with_name("taskledger")
    bash = shutil.which("bash")
    if not cli.exists() or bash is None:
        pytest.skip("needs bash and the installed taskledger console script")
    env = {**os.environ, "TASKLEDGER": str(cli), "EXAMPLES": str(ROOT / "examples")}
    result = subprocess.run(
        [bash, str(ROOT / "scripts" / "demo.sh")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "Found 6 findings (4 errors, 2 warnings) in 1 of 1 bundle." in out
    assert "(exit code 1, as expected)" in out
    assert "(identical: a resubmission with only cosmetic edits is a duplicate)" in out
    assert "(different: this is new content)" in out
    assert out.rstrip().endswith("==> Demo finished")
