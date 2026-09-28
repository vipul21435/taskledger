"""Grader for integer-linear-system.

The grader proves the task is well posed (det(A) is +1 or -1, computed exactly
with fraction-free Bareiss elimination), so any integer vector with A x = b is
the one and only answer. It then checks the output format byte for byte.
"""

import hashlib
import json
import os
import re
from pathlib import Path

WORKDIR = Path(os.environ.get("TASK_WORKDIR", "/app"))
SYSTEM_SHA256 = "5465fabfd1bddd335d27a6f1247860ab057a94335a8b4a10fe3ecc6360531d83"
CANONICAL_INT = re.compile(r"0|-?[1-9][0-9]*")


def load_system() -> tuple[list[list[int]], list[int]]:
    system = json.loads((WORKDIR / "system.json").read_text(encoding="utf-8"))
    return system["A"], system["b"]


def bareiss_determinant(matrix: list[list[int]]) -> int:
    """Exact integer determinant; every intermediate division is exact."""
    m = [row[:] for row in matrix]
    n = len(m)
    sign, previous = 1, 1
    for k in range(n - 1):
        if m[k][k] == 0:
            swap = next((r for r in range(k + 1, n) if m[r][k] != 0), None)
            if swap is None:
                return 0
            m[k], m[swap] = m[swap], m[k]
            sign = -sign
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                m[i][j] = (m[i][j] * m[k][k] - m[i][k] * m[k][j]) // previous
        previous = m[k][k]
    return sign * m[n - 1][n - 1]


def read_answer() -> list[int]:
    lines = (WORKDIR / "x.txt").read_text(encoding="ascii").split("\n")
    assert lines[-1] == "", "file must end with a single newline"
    values = lines[:-1]
    for value in values:
        assert CANONICAL_INT.fullmatch(value), f"not a canonical integer: {value!r}"
    return [int(value) for value in values]


def test_system_is_untouched() -> None:
    digest = hashlib.sha256((WORKDIR / "system.json").read_bytes()).hexdigest()
    assert digest == SYSTEM_SHA256


def test_system_is_well_posed() -> None:
    matrix, rhs = load_system()
    assert len(matrix) == len(rhs) == 7
    assert all(len(row) == len(matrix) for row in matrix)
    assert bareiss_determinant(matrix) in (1, -1)


def test_output_file_exists() -> None:
    assert (WORKDIR / "x.txt").is_file()


def test_answer_solves_the_system() -> None:
    matrix, rhs = load_system()
    x = read_answer()
    assert len(x) == len(rhs)
    for row, expected in zip(matrix, rhs, strict=True):
        assert sum(a * v for a, v in zip(row, x, strict=True)) == expected


def test_output_is_byte_exact() -> None:
    x = read_answer()
    expected = "".join(f"{value}\n" for value in x).encode("ascii")
    assert (WORKDIR / "x.txt").read_bytes() == expected
