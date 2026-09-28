from __future__ import annotations

import ast

from taskledger.lint.pyast import (
    ImportTable,
    docstring_nodes,
    find_test_functions,
    parents,
    violation_at,
)

SOURCE = '''"""Module docstring."""
import os
import os.path
import datetime as dt
import xml.etree.ElementTree
from random import randint as roll, choice
from pathlib import *
from . import sibling


class Thing:
    """Class docstring."""

    def method(self):
        """Method docstring."""
        return dt.datetime.now()


def helper():
    return roll(1, 6) + len(choice("ab")) + os.path.getsize(".")
'''


def calls(tree: ast.AST, table: ImportTable) -> list[str | None]:
    return [table.qualname(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]


def test_import_table_resolves_aliases() -> None:
    tree = ast.parse(SOURCE)
    table = ImportTable(tree)
    assert table.aliases == {
        "os": "os",
        "dt": "datetime",
        "xml": "xml",
        "roll": "random.randint",
        "choice": "random.choice",
    }
    assert [name for name, _ in table.imports] == [
        "os",
        "os.path",
        "datetime",
        "xml.etree.ElementTree",
        "random.randint",
        "random.choice",
        "pathlib",
    ]
    assert sorted(filter(None, calls(tree, table))) == [
        "datetime.datetime.now",
        "len",
        "os.path.getsize",
        "random.choice",
        "random.randint",
    ]


def test_qualname_of_non_dotted_expressions_is_none() -> None:
    table = ImportTable(ast.parse("import os"))
    call = ast.parse("os.getcwd().strip()").body[0]
    assert isinstance(call, ast.Expr)
    assert isinstance(call.value, ast.Call)
    assert table.qualname(call.value.func) is None


def test_docstring_nodes_and_parents() -> None:
    tree = ast.parse(SOURCE)
    docs = docstring_nodes(tree)
    texts = sorted(
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and id(node) in docs
    )
    assert texts == ["Class docstring.", "Method docstring.", "Module docstring."]
    parent_of = parents(tree)
    method = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "method"
    )
    assert isinstance(parent_of[id(method)], ast.ClassDef)


def test_violation_columns_count_code_points_not_bytes() -> None:
    source = 's = "éé"; import socket\n'
    tree = ast.parse(source)
    node = tree.body[1]
    violation = violation_at("t.py", source.splitlines(), node, "m")
    assert (violation.line, violation.column) == (1, 11)
    assert violation.end_column == 24
    assert source[violation.column - 1 : violation.end_column - 1] == "import socket"


def test_violation_at_nodes_without_positions_and_lines() -> None:
    violation = violation_at("t.py", [], ast.Module(body=[], type_ignores=[]), "m")
    assert (violation.line, violation.column, violation.end_line) == (1, 1, None)
    node = ast.parse("\n\nx = 1").body[0]
    assert violation_at("t.py", [], node, "m").column == 1


def test_find_test_functions() -> None:
    tree = ast.parse(
        "def test_a(): pass\n"
        "async def test_b(): pass\n"
        "def helper(): pass\n"
        "class TestGroup:\n"
        "    def test_c(self): pass\n"
        "    def setup_method(self): pass\n"
        "    x = 1\n"
        "class Helper:\n"
        "    def test_d(self): pass\n"
    )
    assert [f.name for f in find_test_functions(tree)] == ["test_a", "test_b", "test_c"]
