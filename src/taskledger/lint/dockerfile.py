"""A small, position-preserving Dockerfile parser.

It understands what the rules need and nothing more: the ``escape`` parser
directive, comments, line continuations (with comment and blank lines inside
them skipped, as BuildKit does), heredoc bodies (``RUN <<EOF ... EOF``, which
are skipped so their lines are never mistaken for instructions), ``FROM``
flags and stage names, ``ARG`` declarations with defaults, and variable
expansion in the forms ``$NAME``, ``${NAME}``, ``${NAME:-word}``,
``${NAME-word}``, ``${NAME:+word}`` and ``${NAME+word}``.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from taskledger.lint.locate import split_lines

_DIRECTIVE_RE: Final = re.compile(r"#\s*([A-Za-z]+)\s*=\s*(\S+)\s*$")
_HEREDOC_RE: Final = re.compile(r"(?<!<)<<(?!<)-?\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1")
_TOKEN_RE: Final = re.compile(r"\S+")
_VAR_RE: Final = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-+])(?P<word>[^}]*))?\}"
    r"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)
_HEREDOC_KEYWORDS: Final = frozenset({"RUN", "COPY", "ADD"})


@dataclass(frozen=True, slots=True)
class Instruction:
    """One logical instruction; ``value`` has continuations joined."""

    keyword: str
    value: str
    line: int
    column: int
    first_segment: int

    def column_of(self, offset: int) -> int:
        """Column of ``value[offset]``, or the value start if it is on a later line."""
        return self.column + offset if offset < self.first_segment else self.column


@dataclass(frozen=True, slots=True)
class FromLine:
    """The parts of a ``FROM`` instruction the linter cares about."""

    image: str
    offset: int
    stage: str | None


def _strip_escape(segment: str, escape: str) -> tuple[str, bool]:
    trimmed = segment.rstrip()
    if trimmed.endswith(escape):
        return trimmed[: -len(escape)], True
    return segment, False


def parse_dockerfile(text: str) -> tuple[Instruction, ...]:
    """Parse ``text`` into logical instructions with 1-based positions."""
    lines = split_lines(text)
    escape = "\\"
    index = 0
    while index < len(lines):
        directive = _DIRECTIVE_RE.match(lines[index].strip())
        if directive is None:
            break
        if directive.group(1).lower() == "escape" and directive.group(2) in ("\\", "`"):
            escape = directive.group(2)
        index += 1

    instructions: list[Instruction] = []
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        index += 1
        if not stripped or stripped.startswith("#"):
            continue
        keyword = stripped.split(maxsplit=1)[0]
        value_start = len(raw) - len(raw.lstrip()) + len(keyword)
        value_start += len(raw[value_start:]) - len(raw[value_start:].lstrip())
        start_line = index
        piece, continued = _strip_escape(raw[value_start:], escape)
        parts = [piece]
        while continued and index < len(lines):
            following = lines[index]
            index += 1
            if not following.strip() or following.lstrip().startswith("#"):
                continue
            piece, continued = _strip_escape(following, escape)
            parts.append(piece)
        keyword = keyword.upper()
        value = "".join(parts).rstrip()
        instructions.append(
            Instruction(
                keyword=keyword,
                value=value,
                line=start_line,
                column=value_start + 1,
                first_segment=len(parts[0]),
            )
        )
        if keyword in _HEREDOC_KEYWORDS:
            for heredoc in _HEREDOC_RE.finditer(value):
                delimiter = heredoc.group(2)
                while index < len(lines) and lines[index].strip() != delimiter:
                    index += 1
                index += 1
    return tuple(instructions)


def parse_from(value: str) -> FromLine | None:
    """Image, its offset in ``value`` and the stage name of a ``FROM`` value."""
    tokens = [(match.group(0), match.start()) for match in _TOKEN_RE.finditer(value)]
    position = 0
    while position < len(tokens) and tokens[position][0].startswith("--"):
        position += 1
    if position >= len(tokens):
        return None
    image, offset = tokens[position]
    stage = None
    if position + 2 < len(tokens) and tokens[position + 1][0].lower() == "as":
        stage = tokens[position + 2][0]
    return FromLine(image, offset, stage)


def parse_arg(value: str) -> list[tuple[str, str | None]]:
    """``(name, default)`` pairs of an ``ARG`` value; default None when absent."""
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError:  # unbalanced quotes
        tokens = value.split()
    declared: list[tuple[str, str | None]] = []
    for token in tokens:
        name, equals, default = token.partition("=")
        declared.append((name, default if equals else None))
    return declared


def flag_values(value: str, flag: str) -> list[tuple[str, int]]:
    """Values of ``--flag=value`` options at the start of an instruction, with offsets."""
    found: list[tuple[str, int]] = []
    prefix = f"--{flag}="
    for match in _TOKEN_RE.finditer(value):
        token = match.group(0)
        if not token.startswith("--"):
            break
        if token.startswith(prefix):
            found.append((token[len(prefix) :], match.start() + len(prefix)))
    return found


def expand(text: str, values: Mapping[str, str | None]) -> tuple[str, tuple[str, ...]]:
    """Substitute build args; returns the result and the names that had no value.

    ``values`` maps declared names to their value, or None when declared
    without a default. Undeclared names count as unset.
    """
    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("bare")
        value = values.get(name)
        operator = match.group("op")
        if operator is None:
            if value is None:
                missing.append(name)
                return ""
            return value
        word, word_missing = expand(match.group("word"), values)
        is_set = value is not None and (value != "" or not operator.startswith(":"))
        if operator.endswith("-"):
            if is_set and value is not None:
                return value
            missing.extend(word_missing)
            return word
        if is_set:
            missing.extend(word_missing)
            return word
        return ""

    return _VAR_RE.sub(substitute, text), tuple(missing)
