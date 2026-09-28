"""Find the line of a manifest field in ``task.toml`` text.

tomllib does not report positions, so findings about manifest fields are
located with a small line scanner that understands table headers
(``[section]``, ``[a.b]``, ``[[array]]``) and ``key = value`` lines with bare
or quoted keys. It is deliberately forgiving: when a key cannot be found it
falls back to the section header and then to line 1, so a finding always has a
valid position.
"""

from __future__ import annotations

import re
from typing import Final

_LINE_SPLIT_RE: Final = re.compile(r"\r\n|\r|\n")
_HEADER_RE: Final = re.compile(r"^\s*\[\[?\s*([^\]]*?)\s*\]\]?\s*(?:#.*)?$")
_KEY_RE: Final = re.compile(r"""^\s*(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9_-]+))\s*=""")
_LOC_PART_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def split_lines(text: str) -> list[str]:
    """Split on CRLF, CR or LF only, like Python's tokenizer and Docker.

    Unlike :meth:`str.splitlines`, form feeds and other Unicode separators do
    not start a new line, so line numbers agree with ``ast`` positions.
    """
    lines = _LINE_SPLIT_RE.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def loc_parts(loc: str) -> tuple[str, ...]:
    """Leading field names of a dotted location such as ``task.tags[1]``."""
    parts: list[str] = []
    for piece in loc.split("."):
        match = _LOC_PART_RE.match(piece)
        if match is None:
            break
        parts.append(match.group(0))
        if match.end() != len(piece):
            break
    return tuple(parts)


def _header_name(line: str) -> str | None:
    match = _HEADER_RE.match(line)
    if match is None:
        return None
    return ".".join(part.strip().strip("\"'") for part in match.group(1).split("."))


def _key_name(line: str) -> str | None:
    match = _KEY_RE.match(line)
    if match is None:
        return None
    return next(group for group in match.groups() if group is not None)


def toml_line(text: str, loc: str | tuple[str, ...]) -> int:
    """1-based line of the field at ``loc`` (``"verifier.command"`` or a tuple).

    Returns the line of the key if found, else the line of its table header,
    else 1.
    """
    parts = loc_parts(loc) if isinstance(loc, str) else loc
    if not parts:
        return 1
    *table_parts, key = parts
    table = ".".join(table_parts)
    current = ""
    exact_header: int | None = None
    table_header: int | None = None
    for number, line in enumerate(split_lines(text), start=1):
        header = _header_name(line)
        if header is not None:
            current = header
            if header == ".".join(parts) and exact_header is None:
                exact_header = number
            elif table and header == table and table_header is None:
                table_header = number
            continue
        if current == table and _key_name(line) == key:
            return number
    return exact_header or table_header or 1
