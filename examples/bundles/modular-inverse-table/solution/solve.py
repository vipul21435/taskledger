"""Reference solution: modular inverses with Python's built-in pow(a, -1, m)."""

import sys
from pathlib import Path


def solve(workdir: Path) -> None:
    header, *rows = (workdir / "queries.txt").read_text(encoding="ascii").splitlines()
    modulus = int(header.split()[1])
    answers = []
    for row in rows:
        a = int(row) % modulus
        try:
            answers.append(str(pow(a, -1, modulus)))
        except ValueError:  # gcd(a, modulus) != 1, so no inverse exists
            answers.append("none")
    (workdir / "inverses.txt").write_bytes("".join(f"{x}\n" for x in answers).encode("ascii"))


if __name__ == "__main__":
    solve(Path(sys.argv[1]))
