"""What lint rules report: violations, findings and their severities.

A rule yields :class:`Violation` objects (a message at a position). The engine
turns each one into a :class:`Finding` by attaching the rule's code, name,
severity and fix hint, so rules never repeat their own metadata.

Positions are 1-based lines and 1-based columns counted in Unicode code
points. A finding about a whole file points at line 1, column 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """How serious a finding is; any ``error`` makes ``taskledger lint`` exit 1."""

    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


def _check_position(line: int, column: int, end_line: int | None, end_column: int | None) -> None:
    if line < 1 or column < 1:
        raise ValueError(f"positions are 1-based, got line={line} column={column}")
    if end_line is not None and end_line < line:
        raise ValueError(f"end_line {end_line} is before line {line}")
    if end_column is not None and end_column < 1:
        raise ValueError(f"end_column must be 1-based, got {end_column}")


@dataclass(frozen=True, slots=True)
class Violation:
    """A problem found by a rule, at a bundle-relative POSIX path."""

    file: str
    message: str
    line: int = 1
    column: int = 1
    end_line: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        _check_position(self.line, self.column, self.end_line, self.end_column)


@dataclass(frozen=True, slots=True)
class Finding:
    """A violation with the metadata of the rule that produced it.

    ``fingerprint`` identifies the finding independently of its line number
    (rule code, file, message and occurrence index), so moving code around
    does not make it look new to code-scanning tools.
    """

    code: str
    rule: str
    severity: Severity
    message: str
    file: str
    line: int = 1
    column: int = 1
    end_line: int | None = None
    end_column: int | None = None
    fix: str = ""
    fingerprint: str = ""

    def __post_init__(self) -> None:
        _check_position(self.line, self.column, self.end_line, self.end_column)

    @property
    def sort_key(self) -> tuple[str, int, int, str, str]:
        """Order by file, position, code, then message."""
        return (self.file, self.line, self.column, self.code, self.message)

    def to_dict(self) -> dict[str, str | int | None]:
        """JSON-ready representation."""
        return {
            "code": self.code,
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "fix": self.fix,
            "fingerprint": self.fingerprint,
        }
