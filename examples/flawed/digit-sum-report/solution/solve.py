"""Reference solution: digit sums, one per input line."""

import sys
from pathlib import Path

workdir = Path(sys.argv[1])
numbers = (workdir / "numbers.txt").read_text(encoding="ascii").split()
sums = [sum(int(digit) for digit in value.lstrip("-")) for value in numbers]
(workdir / "report.txt").write_text("".join(f"{total}\n" for total in sums), encoding="ascii")
