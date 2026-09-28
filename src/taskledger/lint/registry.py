"""Decorator-based rule registry with stable codes and ruff-style selection.

Each rule is a function ``check(ctx) -> Iterable[Violation]`` registered with
a stable code (``TL002``), a kebab-case name, a default severity, a one-line
description and a fix hint::

    @rule(
        "TL002",
        name="unpinned-base-image",
        severity=Severity.ERROR,
        description="Dockerfile FROM lines must pin the base image by digest.",
        fix="Append @sha256:<digest> to the image reference.",
    )
    def unpinned_base_image(ctx: LintContext) -> Iterator[Violation]: ...

Selection works on codes and code prefixes. A :class:`Selection` layer holds
``select`` and ``ignore`` lists; layers are applied in order (``task.toml``
first, then the command line):

- a layer with a non-empty ``select`` replaces the enabled set with every rule
  that matches a select entry more specifically (longer prefix) than any
  ignore entry; on a tie ``ignore`` wins;
- a layer with only ``ignore`` removes the matching rules from the set.

``ALL`` matches every rule with the lowest specificity, so ``select = ["ALL"]``
plus ``ignore = ["TL00"]`` keeps only rules outside ``TL00x``, and
``select = ["TL004"]`` on the command line re-enables a rule that
``task.toml`` ignored.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from taskledger.lint.finding import Finding, Severity, Violation

if TYPE_CHECKING:
    from taskledger.lint.context import LintContext

type Check = Callable[[LintContext], Iterable[Violation]]

CODE_RE: Final = re.compile(r"TL[0-9]{3}")
NAME_RE: Final = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
SELECTOR_RE: Final = re.compile(r"ALL|[A-Z]{1,8}[0-9]{0,4}")
ALL: Final = "ALL"


@dataclass(frozen=True, slots=True)
class Rule:
    """A registered rule: stable metadata plus the check function."""

    code: str
    name: str
    severity: Severity
    description: str
    fix: str
    check: Check
    needs_files: bool = True

    def finding(self, violation: Violation) -> Finding:
        """Attach this rule's metadata to a violation."""
        return Finding(
            code=self.code,
            rule=self.name,
            severity=self.severity,
            message=violation.message,
            file=violation.file,
            line=violation.line,
            column=violation.column,
            end_line=violation.end_line,
            end_column=violation.end_column,
            fix=self.fix,
        )

    def to_dict(self) -> dict[str, str]:
        """JSON-ready metadata (without the check function)."""
        return {
            "code": self.code,
            "name": self.name,
            "severity": self.severity.value,
            "description": self.description,
            "fix": self.fix,
        }


class UnknownSelectorError(ValueError):
    """Raised for select/ignore entries that match no registered rule."""

    def __init__(self, selectors: Sequence[str]) -> None:
        self.selectors = tuple(selectors)
        listed = ", ".join(repr(selector) for selector in self.selectors)
        super().__init__(f"unknown rule code or prefix: {listed}")


@dataclass(frozen=True, slots=True)
class Selection:
    """One layer of rule selection (``task.toml`` or the command line)."""

    select: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()


def _specificity(selectors: Iterable[str], code: str) -> int:
    """Length of the most specific selector matching ``code``; -1 if none does."""
    best = -1
    for selector in selectors:
        if selector == ALL:
            best = max(best, 0)
        elif code.startswith(selector):
            best = max(best, len(selector))
    return best


class RuleRegistry:
    """An ordered collection of rules keyed by code."""

    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}

    def rule[F: Check](
        self,
        code: str,
        *,
        name: str,
        severity: Severity,
        description: str,
        fix: str,
        needs_files: bool = True,
    ) -> Callable[[F], F]:
        """Register the decorated check function under ``code``.

        ``needs_files=False`` marks a rule that can run when the bundle
        directory itself is missing (only the invalid-bundle rule does).
        """
        if CODE_RE.fullmatch(code) is None:
            raise ValueError(f"rule code must look like TL001, got {code!r}")
        if NAME_RE.fullmatch(name) is None:
            raise ValueError(f"rule name must be kebab-case, got {name!r}")
        if not description.strip() or not fix.strip():
            raise ValueError(f"rule {code} needs a description and a fix hint")
        if code in self._rules:
            raise ValueError(f"rule code {code} is already registered")
        if any(existing.name == name for existing in self._rules.values()):
            raise ValueError(f"rule name {name!r} is already registered")

        def register(check: F) -> F:
            self._rules[code] = Rule(code, name, severity, description, fix, check, needs_files)
            return check

        return register

    def __iter__(self) -> Iterator[Rule]:
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self._rules)

    def __contains__(self, code: object) -> bool:
        return code in self._rules

    @property
    def rules(self) -> tuple[Rule, ...]:
        """All rules ordered by code."""
        return tuple(self._rules[code] for code in sorted(self._rules))

    def get(self, code: str) -> Rule:
        """The rule registered under ``code``; KeyError if there is none."""
        return self._rules[code]

    def unknown_selectors(self, selectors: Iterable[str]) -> list[str]:
        """Selectors that are malformed or match no registered rule, in input order."""
        unknown: list[str] = []
        for selector in selectors:
            if selector == ALL:
                continue
            if SELECTOR_RE.fullmatch(selector) is None or not any(
                code.startswith(selector) for code in self._rules
            ):
                unknown.append(selector)
        return unknown

    def check_selectors(self, selectors: Iterable[str]) -> None:
        """Raise :class:`UnknownSelectorError` if any selector matches no rule."""
        unknown = self.unknown_selectors(selectors)
        if unknown:
            raise UnknownSelectorError(unknown)

    def resolve(self, layers: Iterable[Selection]) -> tuple[Rule, ...]:
        """The rules enabled after applying ``layers`` in order (see module docs)."""
        enabled = set(self._rules)
        for layer in layers:
            if layer.select:
                enabled = {
                    code
                    for code in self._rules
                    if _specificity(layer.select, code) > _specificity(layer.ignore, code)
                }
            elif layer.ignore:
                enabled = {code for code in enabled if _specificity(layer.ignore, code) < 0}
        return tuple(self._rules[code] for code in sorted(enabled))


#: The registry every built-in rule registers into.
REGISTRY: Final = RuleRegistry()

#: Decorator registering a rule into :data:`REGISTRY`.
rule: Final = REGISTRY.rule
