"""TL004 nondeterminism in the grader and the reference solution."""

from __future__ import annotations

import ast

import pytest

from conftest import BundleFactory, lint_findings
from taskledger.lint import REGISTRY, Severity
from taskledger.lint.rules.determinism import check_module


def messages(source: str) -> list[tuple[int, str]]:
    """``(line, message)`` of every TL004 violation in one module."""
    lines = tuple(source.splitlines())
    return [(v.line, v.message) for v in check_module("m.py", ast.parse(source), lines)]


def lines_of(source: str) -> list[int]:
    return [line for line, _ in messages(source)]


def test_rule_is_registered_as_a_warning() -> None:
    rule = REGISTRY.get("TL004")
    assert (rule.name, rule.severity) == ("nondeterminism", Severity.WARNING)


def test_deterministic_grader_passes(make_bundle: BundleFactory) -> None:
    source = (
        "import os\n"
        "import random\n"
        "from pathlib import Path\n"
        "\n"
        "random.seed(7)\n"
        "\n"
        "def test_x() -> None:\n"
        "    rng = random.Random(1234)\n"
        "    names = sorted(os.listdir('.'))\n"
        "    files = sorted(Path('.').glob('*.txt'))\n"
        "    seen = {1, 2, 3}\n"
        "    assert len(seen) == 3 and 2 in seen and sum(seen) == 6\n"
        "    assert random.randint(1, 6) and rng.choice(names or ['a']) and files is not None\n"
        "    assert hash(5) == 5\n"
    )
    assert lint_findings(make_bundle, "TL004", {"tests/test_answer.py": source}) == []


def test_wall_clock_and_entropy_with_aliases() -> None:
    source = (
        "import time, uuid, os, secrets\n"
        "from datetime import datetime as dt, date\n"
        "t = time.time()\n"
        "n = dt.now()\n"
        "d = date.today()\n"
        "u = uuid.uuid4()\n"
        "b = os.urandom(8)\n"
        "s = secrets.token_hex(4)\n"
    )
    assert messages(source) == [
        (3, "'time.time()' reads the wall clock, so the result changes between runs"),
        (4, "'datetime.datetime.now()' reads the wall clock, so the result changes between runs"),
        (5, "'datetime.date.today()' reads the wall clock, so the result changes between runs"),
        (6, "'uuid.uuid4()' draws from OS entropy and cannot be reproduced"),
        (7, "'os.urandom()' draws from OS entropy and cannot be reproduced"),
        (8, "'secrets.token_hex()' draws from OS entropy and cannot be reproduced"),
    ]


def test_unseeded_global_random_is_reported() -> None:
    source = "import random\nfrom random import choice\nx = random.randint(1, 6)\ny = choice([1])\n"
    assert messages(source) == [
        (3, "'random.randint()' uses the global random generator, never seeded in this file"),
        (4, "'random.choice()' uses the global random generator, never seeded in this file"),
    ]


def test_seed_without_value_and_unseeded_constructors() -> None:
    source = (
        "import random\n"
        "import numpy as np\n"
        "random.seed()\n"
        "random.seed(None)\n"
        "r = random.Random()\n"
        "g = np.random.default_rng()\n"
        "ok = np.random.default_rng(42)\n"
        "ok2 = random.Random(seed=3)\n"
    )
    assert messages(source) == [
        (3, "'random.seed()' without a value seeds from OS entropy"),
        (4, "'random.seed()' without a value seeds from OS entropy"),
        (5, "'random.Random()' without a seed is unseeded"),
        (6, "'numpy.random.default_rng()' without a seed is unseeded"),
    ]


def test_numpy_global_state_respects_seeding() -> None:
    unseeded = "import numpy as np\nx = np.random.rand(3)\ng = np.random.Generator(None)\n"
    assert messages(unseeded) == [
        (2, "'numpy.random.rand()' uses numpy's global random state, never seeded in this file")
    ]
    seeded = "import numpy as np\nnp.random.seed(0)\nx = np.random.rand(3)\n"
    assert messages(seeded) == []


def test_hash_of_non_int_is_reported() -> None:
    assert messages("a = hash('abc')\nb = hash(3)\nc = hash(x)\n") == [
        (1, "'hash()' of str or bytes is salted per process (PYTHONHASHSEED)"),
        (3, "'hash()' of str or bytes is salted per process (PYTHONHASHSEED)"),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "import os\nfor name in os.listdir('.'):\n    print(name)\n",
        "import glob\nfiles = glob.glob('*.py')\n",
        "from pathlib import Path\nitems = list(Path('.').iterdir())\n",
        "import os\nfor root, dirs, files in os.walk('.'):\n    pass\n",
    ],
)
def test_unsorted_listings_are_reported(source: str) -> None:
    found = messages(source)
    assert len(found) == 1
    assert "file-system order; wrap it in sorted()" in found[0][1]


@pytest.mark.parametrize(
    "source",
    [
        "import os\nnames = sorted(os.listdir('.'))\n",
        "import os\nn = len(os.listdir('.'))\n",
        "import os\nok = 'a' in os.listdir('.')\n",
        "import os\nnames = {n for n in os.listdir('.')}\n",
        "import os\nnames = sorted(n.upper() for n in os.listdir('.'))\n",
        "import glob\ntotal = sum(1 for _ in glob.iglob('*'))\n",
    ],
)
def test_order_insensitive_listings_pass(source: str) -> None:
    assert messages(source) == []


def test_module_attribute_named_like_a_listing_method_is_not_a_listing() -> None:
    assert messages("import fnmatch\nimport glob\nx = glob.escape('a')\n") == []


def test_set_order_dependent_output() -> None:
    source = (
        "names = {'b', 'a'}\n"
        "for n in names:\n"
        "    print(n)\n"
        "joined = ','.join(names)\n"
        "as_list = list(names | {'c'})\n"
        "firsts = [n[0] for n in names]\n"
        "label = f'{names}'\n"
        "print(*names)\n"
        "fine = sorted(names)\n"
        "also_fine = {n.upper() for n in names}\n"
    )
    assert lines_of(source) == [2, 4, 5, 6, 7, 8]
    assert "a for loop over a set" in messages(source)[0][1]


def test_names_reassigned_to_non_sets_are_not_treated_as_sets() -> None:
    source = (
        "items = {1, 2}\n"
        "items = [1, 2]\n"
        "for i in items:\n"
        "    pass\n"
        "def f(values):\n"
        "    return list(values)\n"
        "values = set()\n"
    )
    assert messages(source) == []


def test_set_methods_are_sets() -> None:
    assert lines_of("a = set('ab')\nfor x in a.union('c'):\n    pass\n") == [2]


def test_solution_files_are_scanned_and_shell_is_not(make_bundle: BundleFactory) -> None:
    files = {
        "solution/solve.py": "import time\nprint(time.time())\n",
        "solution/run.sh": "#!/bin/sh\ndate +%s\n",
        "tests/helper.py": "import uuid\nTOKEN = uuid.uuid4().hex\n",
    }
    findings = lint_findings(make_bundle, "TL004", files)
    assert [(f.file, f.line, f.code, f.severity) for f in findings] == [
        ("solution/solve.py", 2, "TL004", Severity.WARNING),
        ("tests/helper.py", 2, "TL004", Severity.WARNING),
    ]


def test_unparsable_python_is_skipped(make_bundle: BundleFactory) -> None:
    source = "import time\nt = time.time(\n"
    assert lint_findings(make_bundle, "TL004", {"tests/test_answer.py": source}) == []


def test_set_method_on_a_literal_is_a_set() -> None:
    assert lines_of("print(list({1, 2}.intersection([2])))\n") == [1]
