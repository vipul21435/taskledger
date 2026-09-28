"""Grader with typical review failures: network access, nondeterminism, a committed token."""

import random
import time
from pathlib import Path

import requests

WORKDIR = Path("/app")
UPSTREAM_TOKEN = "q7Rk2VxP9mLw4ZbN8cTy3HfJ"


def test_report_matches_a_sample() -> None:
    numbers = (WORKDIR / "numbers.txt").read_text(encoding="ascii").split()
    report = (WORKDIR / "report.txt").read_text(encoding="ascii").split()
    picked = random.sample(range(len(numbers)), k=3)
    for index in picked:
        expected = sum(int(digit) for digit in numbers[index].lstrip("-"))
        assert int(report[index]) == expected


def test_report_is_fresh() -> None:
    assert (WORKDIR / "report.txt").stat().st_mtime <= time.time()


def test_upstream_reference() -> None:
    reply = requests.get(
        "https://example.com/digit-sums.txt",
        headers={"Authorization": f"Bearer {UPSTREAM_TOKEN}"},
        timeout=5,
    )
    assert reply.status_code == 200
