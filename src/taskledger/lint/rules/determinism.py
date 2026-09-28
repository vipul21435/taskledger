"""TL004: signals that the grader or reference solution is not deterministic.

A grader is re-run and its results compared byte for byte, and the reference
output is checked in as the answer, so both must produce the same bytes on
every run. This rule flags, in Python files under the tests and solution
directories:

- wall-clock reads: ``time.time()``, ``datetime.now()``, ``date.today()``, ...;
- OS entropy: ``uuid4()``, ``os.urandom()``, ``secrets.*``, ``SystemRandom``;
- unseeded randomness: module-level ``random.*`` or ``numpy.random.*`` calls in
  a file that never seeds them, ``random.Random()`` or ``default_rng()``
  without a seed, and ``random.seed()`` without a value;
- ``hash()``, which is salted per process for str and bytes;
- set-order dependent output: iterating a set in a ``for`` loop or an ordered
  comprehension, or passing one to ``list``/``tuple``/``print``/``str.join``
  and friends (set order of str and bytes changes between processes);
- unsorted directory listings: ``os.listdir``, ``os.scandir``, ``os.walk``,
  ``glob.glob`` and ``Path.iterdir/glob/rglob/walk`` whose result is not passed
  straight to ``sorted()`` or another order-insensitive consumer.

Calls are matched on fully qualified names, so ``from datetime import
datetime as dt; dt.now()`` is found too.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import Final

from taskledger.lint.context import LintContext
from taskledger.lint.finding import Severity, Violation
from taskledger.lint.pyast import ImportTable, parents, violation_at
from taskledger.lint.registry import rule

WALL_CLOCK: Final = frozenset(
    {
        "time.time",
        "time.time_ns",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
    }
)
OS_ENTROPY: Final = frozenset(
    {
        "os.urandom",
        "os.getrandom",
        "uuid.uuid1",
        "uuid.uuid4",
        "random.SystemRandom",
        "secrets.SystemRandom",
        "secrets.choice",
        "secrets.randbelow",
        "secrets.randbits",
        "secrets.token_bytes",
        "secrets.token_hex",
        "secrets.token_urlsafe",
    }
)
RANDOM_FUNCTIONS: Final = frozenset(
    {
        "betavariate",
        "binomialvariate",
        "choice",
        "choices",
        "expovariate",
        "gammavariate",
        "gauss",
        "getrandbits",
        "lognormvariate",
        "normalvariate",
        "paretovariate",
        "randbytes",
        "randint",
        "random",
        "randrange",
        "sample",
        "shuffle",
        "triangular",
        "uniform",
        "vonmisesvariate",
        "weibullvariate",
    }
)
NUMPY_RANDOM_NON_GLOBAL: Final = frozenset(
    {"BitGenerator", "Generator", "MT19937", "PCG64", "Philox", "SFC64", "SeedSequence"}
)
SEEDABLE_CONSTRUCTORS: Final = frozenset(
    {"random.Random", "numpy.random.default_rng", "numpy.random.RandomState"}
)
LISTINGS: Final = frozenset({"os.listdir", "os.scandir", "os.walk", "glob.glob", "glob.iglob"})
LISTING_METHODS: Final = frozenset({"iterdir", "glob", "rglob", "walk"})
ORDER_INSENSITIVE: Final = frozenset(
    {"all", "any", "collections.Counter", "frozenset", "len", "max", "min", "set", "sorted", "sum"}
)
ORDER_SENSITIVE: Final = frozenset(
    {"enumerate", "iter", "list", "next", "print", "repr", "str", "tuple", "zip"}
)
SET_BUILDERS: Final = frozenset({"set", "frozenset"})
SET_METHODS: Final = frozenset({"difference", "intersection", "symmetric_difference", "union"})
_SET_OPERATORS: Final = (ast.BitOr, ast.BitAnd, ast.Sub, ast.BitXor)


def _has_seed(call: ast.Call) -> bool:
    arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
    return any(not (isinstance(arg, ast.Constant) and arg.value is None) for arg in arguments)


def _receiver_is_module(func: ast.expr, table: ImportTable) -> bool:
    node = func.value if isinstance(func, ast.Attribute) else func
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id in table.aliases


class _ModuleChecker:
    """Walks one parsed module and yields determinism violations."""

    def __init__(self, path: str, tree: ast.Module, lines: tuple[str, ...]) -> None:
        self.path = path
        self.tree = tree
        self.lines = lines
        self.table = ImportTable(tree)
        self.parent = parents(tree)
        self.calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.qualnames = {id(call): self.table.qualname(call.func) for call in self.calls}
        self.seeded = {
            name.rsplit(".", 1)[0]
            for call in self.calls
            if (name := self.qualnames[id(call)]) in ("random.seed", "numpy.random.seed")
            and _has_seed(call)
        }
        self.set_names = self._set_valued_names()

    def violation(self, node: ast.AST, message: str) -> Violation:
        return violation_at(self.path, self.lines, node, message)

    # -- calls ---------------------------------------------------------------

    def call_signal(self, call: ast.Call) -> str | None:
        name = self.qualnames[id(call)]
        if name is not None:
            message = self.entropy_signal(name, call) or self.global_random_signal(name, call)
            if message is not None:
                return message
        if self.is_listing(call) and not self.consumed_without_order(call):
            label = name if name in LISTINGS else f".{self.listing_method(call)}"
            return (
                f"'{label}()' returns entries in file-system order; wrap it in sorted() "
                "so output does not depend on the directory layout"
            )
        return None

    @staticmethod
    def entropy_signal(name: str, call: ast.Call) -> str | None:
        """Wall clock, OS entropy and unseeded generator construction."""
        if name in WALL_CLOCK:
            return f"'{name}()' reads the wall clock, so the result changes between runs"
        if name in OS_ENTROPY:
            return f"'{name}()' draws from OS entropy and cannot be reproduced"
        if _has_seed(call):
            return None
        if name in ("random.seed", "numpy.random.seed"):
            return f"'{name}()' without a value seeds from OS entropy"
        if name in SEEDABLE_CONSTRUCTORS:
            return f"'{name}()' without a seed is unseeded"
        return None

    def global_random_signal(self, name: str, call: ast.Call) -> str | None:
        """Module-level generator state that this file never seeds, and ``hash()``."""
        module, _, function = name.rpartition(".")
        if module == "random" and function in RANDOM_FUNCTIONS and module not in self.seeded:
            return f"'{name}()' uses the global random generator, never seeded in this file"
        if (
            module == "numpy.random"
            and function not in NUMPY_RANDOM_NON_GLOBAL | {"seed", "default_rng", "RandomState"}
            and module not in self.seeded
        ):
            return f"'{name}()' uses numpy's global random state, never seeded in this file"
        if name == "hash" and not (
            call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, int)
        ):
            return "'hash()' of str or bytes is salted per process (PYTHONHASHSEED)"
        return None

    @staticmethod
    def listing_method(call: ast.Call) -> str:
        return call.func.attr if isinstance(call.func, ast.Attribute) else ""

    def is_listing(self, call: ast.Call) -> bool:
        if self.qualnames[id(call)] in LISTINGS:
            return True
        return (
            isinstance(call.func, ast.Attribute)
            and call.func.attr in LISTING_METHODS
            and not _receiver_is_module(call.func, self.table)
        )

    def consumed_without_order(self, node: ast.AST) -> bool:
        """True when ``node``'s order cannot influence what its consumer produces."""
        parent = self.parent.get(id(node))
        if isinstance(parent, ast.Call) and any(arg is node for arg in parent.args):
            return self.table.qualname(parent.func) in ORDER_INSENSITIVE
        if isinstance(parent, ast.Compare):
            for operator, comparator in zip(parent.ops, parent.comparators, strict=True):
                if comparator is node:
                    return isinstance(operator, ast.In | ast.NotIn)
            return False
        if isinstance(parent, ast.comprehension) and parent.iter is node:
            owner = self.parent.get(id(parent))
            if isinstance(owner, ast.SetComp):
                return True
            if isinstance(owner, ast.GeneratorExp):
                return self.consumed_without_order(owner)
        return False

    # -- sets ----------------------------------------------------------------

    def _direct_set(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Set | ast.SetComp):
            return True
        if isinstance(node, ast.Call):
            if self.table.qualname(node.func) in SET_BUILDERS:
                return True
            return (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in SET_METHODS
                and self._direct_set(node.func.value)
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, _SET_OPERATORS):
            return self._direct_set(node.left) or self._direct_set(node.right)
        return False

    def _set_valued_names(self) -> set[str]:
        """Names only ever assigned set expressions (flow-insensitive)."""
        set_valued: set[str] = set()
        other: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        (set_valued if self._direct_set(node.value) else other).add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None and self._direct_set(node.value):
                    set_valued.add(node.target.id)
                else:
                    other.add(node.target.id)
            elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                other.add(node.target.id)
            elif isinstance(node, ast.arg):
                other.add(node.arg)
        return set_valued - other

    def is_set(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.set_names
        if isinstance(node, ast.BinOp) and isinstance(node.op, _SET_OPERATORS):
            return self.is_set(node.left) or self.is_set(node.right)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SET_METHODS
        ):
            return self.is_set(node.func.value)
        return self._direct_set(node)

    def set_order_uses(self) -> Iterator[tuple[ast.AST, str]]:
        message = (
            "{what} depends on set iteration order, which is arbitrary and changes "
            "between processes for str and bytes; iterate over sorted(...) instead"
        )
        for node in ast.walk(self.tree):
            if isinstance(node, ast.For | ast.AsyncFor) and self.is_set(node.iter):
                yield node.iter, message.format(what="a for loop over a set")
            elif isinstance(node, ast.comprehension) and self.is_set(node.iter):
                if not self.consumed_without_order(node.iter):
                    yield node.iter, message.format(what="a comprehension over a set")
            elif isinstance(node, ast.Call):
                name = self.qualnames.get(id(node))
                is_join = isinstance(node.func, ast.Attribute) and node.func.attr == "join"
                if name in ORDER_SENSITIVE or is_join:
                    label = "str.join" if is_join else name
                    for arg in node.args:
                        if self.is_set(arg):
                            yield arg, message.format(what=f"'{label}()' of a set")
            elif isinstance(node, ast.FormattedValue) and self.is_set(node.value):
                yield node.value, message.format(what="formatting a set")
            elif isinstance(node, ast.Starred) and self.is_set(node.value):
                yield node, message.format(what="unpacking a set")

    def check(self) -> Iterator[Violation]:
        for call in self.calls:
            message = self.call_signal(call)
            if message is not None:
                yield self.violation(call, message)
        for node, message in self.set_order_uses():
            yield self.violation(node, message)


def check_module(path: str, tree: ast.Module, lines: tuple[str, ...]) -> Iterator[Violation]:
    """Determinism violations of one parsed Python module."""
    return _ModuleChecker(path, tree, lines).check()


@rule(
    "TL004",
    name="nondeterminism",
    severity=Severity.WARNING,
    description=(
        "The grader and the reference solution must produce the same bytes on every run: "
        "no wall clock, OS entropy, unseeded randomness, per-process hash(), set-order "
        "dependent output or unsorted directory listings."
    ),
    fix=(
        "Seed every generator with a constant (random.Random(1234)), iterate over "
        "sorted(...) sets and directory listings, and never put times or UUIDs in "
        "graded output."
    ),
)
def nondeterminism(ctx: LintContext) -> Iterator[Violation]:
    """Scan Python files under the tests and solution directories."""
    scanned: set[str] = set()
    for directory in (ctx.layout.tests.path, ctx.layout.solution.path):
        for path in ctx.files_under(directory):
            if path in scanned or not ctx.is_python(path):
                continue
            scanned.add(path)
            tree = ctx.python(path)
            if tree is not None:
                yield from check_module(path, tree, ctx.lines(path))
