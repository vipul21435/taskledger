"""Grader for modular-inverse-table.

Every expected value is recomputed here with the extended Euclidean algorithm,
independently of the reference solution. The input is pinned by digest, so
rewriting the queries cannot make a wrong table pass.
"""

import hashlib
import os
from pathlib import Path

WORKDIR = Path(os.environ.get("TASK_WORKDIR", "/app"))
QUERIES_SHA256 = "04bc2813e359fdb177eef4b90ae566ba245e263cf4a4afd41cd0ac1b28b16179"


def extended_gcd(a: int, b: int) -> tuple[int, int]:
    """Return (g, s) with g = gcd(a, b) and s * a = g (mod b)."""
    old_r, r = a, b
    old_s, s = 1, 0
    while r:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
    return old_r, old_s


def read_queries() -> tuple[int, list[int]]:
    header, *rows = (WORKDIR / "queries.txt").read_text(encoding="ascii").splitlines()
    keyword, modulus = header.split()
    assert keyword == "modulus"
    return int(modulus), [int(row) for row in rows]


def expected_lines() -> list[str]:
    modulus, queries = read_queries()
    lines = []
    for a in queries:
        g, s = extended_gcd(a % modulus, modulus)
        lines.append(str(s % modulus) if g == 1 else "none")
    return lines


def test_queries_are_untouched() -> None:
    digest = hashlib.sha256((WORKDIR / "queries.txt").read_bytes()).hexdigest()
    assert digest == QUERIES_SHA256


def test_dataset_is_well_posed() -> None:
    modulus, queries = read_queries()
    assert modulus > 1
    assert len(queries) == 40
    assert any(extended_gcd(a % modulus, modulus)[0] == 1 for a in queries)
    assert any(extended_gcd(a % modulus, modulus)[0] != 1 for a in queries)


def test_output_file_exists() -> None:
    assert (WORKDIR / "inverses.txt").is_file()


def test_output_is_byte_exact() -> None:
    expected = "".join(f"{line}\n" for line in expected_lines()).encode("ascii")
    assert (WORKDIR / "inverses.txt").read_bytes() == expected


def test_every_answer_checks_out() -> None:
    modulus, queries = read_queries()
    answers = (WORKDIR / "inverses.txt").read_text(encoding="ascii").splitlines()
    assert len(answers) == len(queries)
    for a, answer in zip(queries, answers, strict=True):
        if answer == "none":
            assert extended_gcd(a % modulus, modulus)[0] != 1
        else:
            x = int(answer)
            assert 0 <= x < modulus
            assert (a * x) % modulus == 1
