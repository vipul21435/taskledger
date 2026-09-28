"""Shared fixtures: a factory that writes small, valid task bundles to disk."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from hypothesis import settings

from taskledger.lint import Finding, lint_bundle

# "dev" keeps the local loop fast; CI sets HYPOTHESIS_PROFILE=ci for a deeper search.
settings.register_profile("dev", max_examples=100, deadline=None)
settings.register_profile("ci", max_examples=400, deadline=None, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

MINIMAL_MANIFEST = """\
schema_version = 1

[task]
id = "sum-of-squares"
version = "1.0.0"
title = "Sum of squares of a list"
category = "arithmetic"
"""

DEFAULT_FILES: Mapping[str, str] = {
    "instruction.md": "Write the sum of squares of /app/numbers.txt to /app/answer.txt.\n",
    "environment/Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\n",
    "verifier/Dockerfile": "FROM python:3.12-slim\nCOPY tests/ /grader/tests/\n",
    "solution/solve.sh": "#!/usr/bin/env bash\nset -euo pipefail\necho 14 > /app/answer.txt\n",
    "tests/test_answer.py": "def test_answer() -> None:\n    assert 1 + 4 + 9 == 14\n",
}


PINNED_BASE = "python:3.12-slim@sha256:" + "4f" * 32

#: Overrides that make the default bundle lint-clean (pinned base images).
LINT_CLEAN_FILES: Mapping[str, str] = {
    "environment/Dockerfile": f"FROM {PINNED_BASE}\nWORKDIR /app\n",
    "verifier/Dockerfile": f"FROM {PINNED_BASE}\nCOPY tests/ /grader/tests/\n",
}


class BundleFactory:
    """Writes a bundle directory under a temporary root."""

    def __init__(self, base: Path) -> None:
        self._base = base
        self._count = 0

    def __call__(
        self,
        manifest: str | None = MINIMAL_MANIFEST,
        files: Mapping[str, str | bytes | None] | None = None,
        name: str | None = None,
    ) -> Path:
        """Create a bundle. ``files`` overrides defaults; a None value deletes one."""
        self._count += 1
        root = self._base / (name or f"bundle-{self._count}")
        contents: dict[str, str | bytes | None] = dict(DEFAULT_FILES)
        if manifest is not None:
            contents["task.toml"] = manifest
        contents.update(files or {})
        for relative, content in contents.items():
            if content is None:
                continue
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_bytes(content.encode())
        root.mkdir(parents=True, exist_ok=True)
        return root


@pytest.fixture
def make_bundle(tmp_path: Path) -> BundleFactory:
    return BundleFactory(tmp_path)


def lint_findings(
    factory: BundleFactory,
    code: str,
    files: Mapping[str, str | bytes | None] | None = None,
    manifest: str = MINIMAL_MANIFEST,
) -> list[Finding]:
    """Findings of one rule on a lint-clean bundle with ``files`` overridden."""
    bundle = factory(manifest=manifest, files={**LINT_CLEAN_FILES, **(files or {})})
    return list(lint_bundle(bundle, select=[code]).findings)


def where(findings: list[Finding]) -> list[tuple[str, int, int]]:
    """``(file, line, column)`` of each finding."""
    return [(finding.file, finding.line, finding.column) for finding in findings]
