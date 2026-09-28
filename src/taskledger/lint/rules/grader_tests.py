"""TL001: the grader must have tests, and every test must check something."""

from __future__ import annotations

import ast
import posixpath
import re
from collections.abc import Iterator, Sequence
from typing import Final

from taskledger.lint.context import LintContext
from taskledger.lint.finding import Severity, Violation
from taskledger.lint.pyast import FunctionNode, find_test_functions, violation_at
from taskledger.lint.registry import rule

_PYTEST_RE: Final = re.compile(r"(?:^|[\s/])(?:py\.test|pytest)(?=$|\s)")


def runs_pytest(command: Sequence[str]) -> bool:
    """True when the verifier command invokes pytest (directly or via ``-m``)."""
    return _PYTEST_RE.search(" ".join(command)) is not None


def is_pytest_file(path: str) -> bool:
    """pytest's default discovery: ``test_*.py`` or ``*_test.py``."""
    name = posixpath.basename(path)
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _is_noop(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        return True  # docstring, ``...`` or another bare constant
    return (
        isinstance(statement, ast.Assert)
        and isinstance(statement.test, ast.Constant)
        and bool(statement.test.value)
    )


def is_vacuous(function: FunctionNode) -> bool:
    """True when the body is only ``pass``, ``...``, docstrings or ``assert <truthy constant>``."""
    return all(_is_noop(statement) for statement in function.body)


def _has_content(ctx: LintContext, path: str) -> bool:
    if ctx.size(path) == 0:
        return False
    text = ctx.text(path)
    return text is None or bool(text.strip())


@rule(
    "TL001",
    name="missing-tests",
    severity=Severity.ERROR,
    description=(
        "The grader test directory must exist and hold tests; when the verifier runs "
        "pytest, every test file must define test functions that actually check something."
    ),
    fix=(
        "Add pytest tests under the tests directory that recompute the expected answer "
        "and assert on the agent's output; remove placeholder tests."
    ),
)
def missing_tests(ctx: LintContext) -> Iterator[Violation]:
    """Missing or empty test directory, test files without tests, vacuous tests."""
    tests_dir = ctx.layout.tests.path
    if not ctx.is_dir(tests_dir):
        yield Violation(tests_dir, f"grader test directory {tests_dir}/ does not exist")
        return
    files = ctx.files_under(tests_dir)
    if not any(_has_content(ctx, path) for path in files):
        yield Violation(tests_dir, f"grader test directory {tests_dir}/ has no non-empty files")
        return
    if not runs_pytest(ctx.layout.verifier.command):
        return
    test_files = [path for path in files if is_pytest_file(path)]
    if not test_files:
        yield Violation(
            tests_dir,
            f"the verifier runs pytest but {tests_dir}/ has no test_*.py or *_test.py files",
        )
        return
    for path in test_files:
        error = ctx.python_error(path)
        if error is not None:
            yield Violation(
                path,
                f"grader test file does not parse: {error.msg}",
                max(error.lineno or 1, 1),
                max(error.offset or 1, 1),
            )
            continue
        tree = ctx.python(path)
        if tree is None:
            continue
        functions = list(find_test_functions(tree))
        if not functions:
            yield Violation(path, "grader test file defines no test functions")
            continue
        lines = ctx.lines(path)
        for function in functions:
            if is_vacuous(function):
                yield violation_at(
                    path,
                    lines,
                    function,
                    f"test '{function.name}' checks nothing (only pass, ..., docstrings "
                    "or a constant assert)",
                )
