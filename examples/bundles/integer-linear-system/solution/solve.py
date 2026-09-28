"""Reference solution: exact Gauss-Jordan elimination over the rationals."""

import json
import sys
from fractions import Fraction
from pathlib import Path


def solve_exact(matrix: list[list[int]], rhs: list[int]) -> list[Fraction]:
    n = len(matrix)
    rows = [[Fraction(v) for v in row] + [Fraction(rhs[i])] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = next(r for r in range(col, n) if rows[r][col] != 0)
        rows[col], rows[pivot] = rows[pivot], rows[col]
        lead = rows[col][col]
        rows[col] = [v / lead for v in rows[col]]
        for r in range(n):
            if r != col and rows[r][col] != 0:
                factor = rows[r][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col], strict=True)]
    return [row[n] for row in rows]


def solve(workdir: Path) -> None:
    system = json.loads((workdir / "system.json").read_text(encoding="utf-8"))
    solution = solve_exact(system["A"], system["b"])
    if any(value.denominator != 1 for value in solution):
        raise SystemExit("solution is not integral; the input breaks the task contract")
    text = "".join(f"{value.numerator}\n" for value in solution)
    (workdir / "x.txt").write_bytes(text.encode("ascii"))


if __name__ == "__main__":
    solve(Path(sys.argv[1]))
