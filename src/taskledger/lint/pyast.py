"""Helpers for rules that inspect Python source with :mod:`ast`.

The key piece is :class:`ImportTable`, which resolves a call such as
``dt.datetime.now()`` or ``randint(1, 6)`` to the fully qualified name the
module imported (``datetime.datetime.now``, ``random.randint``), so rules
match on what code calls rather than on how it spelled the import.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence

from taskledger.lint.finding import Violation

type FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class ImportTable:
    """Local names bound by absolute imports, mapped to fully qualified names."""

    def __init__(self, tree: ast.AST) -> None:
        self.aliases: dict[str, str] = {}
        #: Every imported module or name, fully qualified, with its import node.
        self.imports: list[tuple[str, ast.Import | ast.ImportFrom]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports.append((alias.name, node))
                    if alias.asname:
                        self.aliases[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".", 1)[0]
                        self.aliases[top] = top
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    if alias.name == "*":
                        self.imports.append((node.module, node))
                        continue
                    qualified = f"{node.module}.{alias.name}"
                    self.imports.append((qualified, node))
                    self.aliases[alias.asname or alias.name] = qualified

    def qualname(self, node: ast.expr) -> str | None:
        """Dotted name of a ``Name``/``Attribute`` chain with the head resolved.

        Names that were not imported (builtins, locals) are returned as
        written; anything that is not a plain dotted chain gives None.
        """
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        head = self.aliases.get(node.id, node.id)
        return ".".join([head, *reversed(parts)])


def docstring_nodes(tree: ast.AST) -> set[int]:
    """``id()`` of every docstring constant in the tree."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def parents(tree: ast.AST) -> dict[int, ast.AST]:
    """Map ``id(child)`` to its parent node."""
    mapping: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            mapping[id(child)] = node
    return mapping


def _column(lines: Sequence[str], line: int, byte_offset: int) -> int:
    """1-based code point column from the UTF-8 byte offset ast reports."""
    if not 1 <= line <= len(lines):
        return byte_offset + 1
    prefix = lines[line - 1].encode("utf-8")[:byte_offset]
    return len(prefix.decode("utf-8", errors="ignore")) + 1


def violation_at(file: str, lines: Sequence[str], node: ast.AST, message: str) -> Violation:
    """A violation spanning ``node`` (which must carry position attributes)."""
    line: int = getattr(node, "lineno", 1)
    column = _column(lines, line, getattr(node, "col_offset", 0))
    end_line: int | None = getattr(node, "end_lineno", None)
    end_offset: int | None = getattr(node, "end_col_offset", None)
    end_column = (
        _column(lines, end_line, end_offset)
        if end_line is not None and end_offset is not None
        else None
    )
    return Violation(file, message, line, column, end_line, end_column)


def find_test_functions(tree: ast.Module) -> Iterator[FunctionNode]:
    """Functions pytest would collect: module-level ``test*`` and ``Test*`` class methods."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("test"):
                yield node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and (
                    item.name.startswith("test")
                ):
                    yield item
