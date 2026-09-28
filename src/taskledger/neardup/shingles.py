"""Shingles: the sets MinHash compares.

- Instructions become lower-cased word ``k``-grams, so reordering a sentence or
  swapping a few words keeps most shingles while a different task shares few.
- Solutions become normalized code tokens first: comments, docstrings and
  whitespace are dropped, every string literal becomes ``STR``, and each
  identifier that is not a keyword or builtin becomes ``ID<n>`` in order of
  first use. Renaming variables or re-wrapping lines therefore changes nothing.
  Files that Python cannot tokenize fall back to a regex tokenizer that applies
  the same identifier rule and strips ``#`` and ``//`` line comments.
"""

from __future__ import annotations

import builtins
import io
import keyword
import re
import tokenize
from collections.abc import Iterable, Sequence
from typing import Final

#: Words per instruction shingle.
WORD_SHINGLE_SIZE: Final = 3
#: Code tokens per solution shingle.
CODE_SHINGLE_SIZE: Final = 5

_WORD_RE: Final = re.compile(r"[a-z0-9]+")
_FALLBACK_TOKEN_RE: Final = re.compile(
    r"""(?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(?P<name>[A-Za-z_]\w*)|(?P<num>\d[\w.]*)|(?P<op>\S)"""
)
_LINE_COMMENT_RE: Final = re.compile(r"(?:^|\s)(?:#|//).*$", re.MULTILINE)
#: Keywords and public builtins keep their names; ``_`` and dunders are only
#: present in some interpreters (``_`` in a REPL), so they are renamed like
#: any other identifier to keep signatures identical everywhere.
_KEEP: Final = (
    frozenset(keyword.kwlist)
    | frozenset(keyword.softkwlist)
    | frozenset(name for name in dir(builtins) if not name.startswith("_"))
) - {"_"}
_SKIP: Final = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
        tokenize.ENCODING,
    }
)


def words(text: str) -> list[str]:
    """Lower-cased alphanumeric words of ``text`` (markdown punctuation dropped)."""
    return _WORD_RE.findall(text.lower())


def ngrams(items: Sequence[str], size: int) -> set[str]:
    """Every run of ``size`` consecutive items, joined by a space.

    A sequence shorter than ``size`` yields itself as one shingle, and an empty
    one yields nothing.
    """
    if size < 1:
        raise ValueError("size must be at least 1")
    if not items:
        return set()
    if len(items) <= size:
        return {" ".join(items)}
    return {" ".join(items[i : i + size]) for i in range(len(items) - size + 1)}


def word_shingles(text: str, size: int = WORD_SHINGLE_SIZE) -> set[str]:
    """Word ``size``-grams of an instruction."""
    return ngrams(words(text), size)


class _Namer:
    """Maps identifiers to ``ID0``, ``ID1``, ... in order of first use."""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}

    def __call__(self, name: str) -> str:
        if name in _KEEP:
            return name
        return self._names.setdefault(name, f"ID{len(self._names)}")


def _python_tokens(source: str) -> list[str]:
    namer = _Namer()
    out: list[str] = []
    previous = tokenize.NEWLINE
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        kind = token.type
        if kind in _SKIP:
            if kind in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                previous = tokenize.NEWLINE
            continue
        if kind == tokenize.STRING and previous == tokenize.NEWLINE:
            previous = kind
            continue  # a docstring or bare string statement
        previous = kind
        if kind == tokenize.NAME:
            out.append(namer(token.string))
        elif kind in (tokenize.STRING, tokenize.FSTRING_START):
            out.append("STR")
        elif kind in (tokenize.FSTRING_MIDDLE, tokenize.FSTRING_END):
            continue
        elif kind == tokenize.NUMBER:
            out.append(token.string.lower())
        else:
            out.append(token.string)
    return out


def _fallback_tokens(source: str) -> list[str]:
    namer = _Namer()
    out: list[str] = []
    for match in _FALLBACK_TOKEN_RE.finditer(_LINE_COMMENT_RE.sub("", source)):
        if match.group("str") is not None:
            out.append("STR")
        elif (name := match.group("name")) is not None:
            out.append(namer(name))
        else:
            out.append(match.group(0).lower())
    return out


def code_tokens(source: str) -> list[str]:
    """Normalized tokens of a source file (Python first, regex fallback)."""
    try:
        return _python_tokens(source)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return _fallback_tokens(source)


def code_shingles(sources: Iterable[str], size: int = CODE_SHINGLE_SIZE) -> set[str]:
    """Token ``size``-grams over every source file of a solution.

    Each file is tokenized on its own, so identifier numbering restarts per file
    and the order the files are given in does not matter.
    """
    shingles: set[str] = set()
    for source in sources:
        shingles |= ngrams(code_tokens(source), size)
    return shingles
