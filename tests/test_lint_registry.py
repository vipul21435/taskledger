from __future__ import annotations

from collections.abc import Iterator

import pytest

from taskledger.lint import REGISTRY, Rule, RuleRegistry, Selection, Severity, Violation
from taskledger.lint.context import LintContext
from taskledger.lint.finding import Finding
from taskledger.lint.registry import UnknownSelectorError


def _nothing(ctx: LintContext) -> Iterator[Violation]:
    yield from ()


def make_registry(*codes: str) -> RuleRegistry:
    registry = RuleRegistry()
    for index, code in enumerate(codes):
        registry.rule(
            code,
            name=f"rule-{index}",
            severity=Severity.WARNING,
            description=f"Rule {code}.",
            fix="Do the thing.",
        )(_nothing)
    return registry


def enabled(registry: RuleRegistry, *layers: Selection) -> list[str]:
    return [rule.code for rule in registry.resolve(layers)]


def test_decorator_registers_and_returns_the_function() -> None:
    registry = RuleRegistry()

    @registry.rule(
        "TL042",
        name="answer-rule",
        severity=Severity.NOTE,
        description="Something to check.",
        fix="Fix it.",
    )
    def check(ctx: LintContext) -> Iterator[Violation]:
        yield Violation("a.txt", "found")

    assert callable(check)
    rule = registry.get("TL042")
    assert rule.check is check
    assert (rule.code, rule.name, rule.severity) == ("TL042", "answer-rule", Severity.NOTE)
    assert rule.needs_files is True
    assert rule.to_dict() == {
        "code": "TL042",
        "name": "answer-rule",
        "severity": "note",
        "description": "Something to check.",
        "fix": "Fix it.",
    }
    assert "TL042" in registry
    assert "TL043" not in registry
    assert len(registry) == 1


@pytest.mark.parametrize(
    ("code", "name", "description", "fix", "message"),
    [
        ("TL1", "ok-name", "d", "f", "rule code must look like TL001"),
        ("XX001", "ok-name", "d", "f", "rule code must look like TL001"),
        ("TL001", "Bad_Name", "d", "f", "kebab-case"),
        ("TL001", "ok-name", " ", "f", "needs a description and a fix hint"),
        ("TL001", "ok-name", "d", "", "needs a description and a fix hint"),
    ],
)
def test_rule_metadata_is_validated(
    code: str, name: str, description: str, fix: str, message: str
) -> None:
    registry = RuleRegistry()
    with pytest.raises(ValueError, match=message):
        registry.rule(code, name=name, severity=Severity.ERROR, description=description, fix=fix)


def test_codes_and_names_are_unique() -> None:
    registry = make_registry("TL001")
    with pytest.raises(ValueError, match="TL001 is already registered"):
        registry.rule("TL001", name="other", severity=Severity.ERROR, description="d", fix="f")
    with pytest.raises(ValueError, match="'rule-0' is already registered"):
        registry.rule("TL002", name="rule-0", severity=Severity.ERROR, description="d", fix="f")


def test_rules_are_ordered_by_code() -> None:
    registry = make_registry("TL010", "TL002", "TL001")
    assert [rule.code for rule in registry] == ["TL001", "TL002", "TL010"]
    assert [rule.code for rule in registry.rules] == ["TL001", "TL002", "TL010"]


def test_rule_turns_violations_into_findings() -> None:
    rule = make_registry("TL007").get("TL007")
    finding = rule.finding(Violation("tests/x.py", "bad", 3, 5, 3, 9))
    assert finding == Finding(
        code="TL007",
        rule="rule-0",
        severity=Severity.WARNING,
        message="bad",
        file="tests/x.py",
        line=3,
        column=5,
        end_line=3,
        end_column=9,
        fix="Do the thing.",
    )


def test_unknown_selectors_are_reported_in_order() -> None:
    registry = make_registry("TL001", "TL012")
    assert registry.unknown_selectors(["ALL", "TL", "TL0", "TL01", "TL012"]) == []
    assert registry.unknown_selectors(["TL9", "tl001", "TL-1", "XY", "TL0123"]) == [
        "TL9",
        "tl001",
        "TL-1",
        "XY",
        "TL0123",
    ]
    with pytest.raises(UnknownSelectorError, match="unknown rule code or prefix: 'TL9', 'XY'"):
        registry.check_selectors(["TL001", "TL9", "XY"])
    registry.check_selectors(["TL0"])


def test_everything_is_enabled_by_default() -> None:
    registry = make_registry("TL001", "TL002", "TL011")
    assert enabled(registry) == ["TL001", "TL002", "TL011"]
    assert enabled(registry, Selection()) == ["TL001", "TL002", "TL011"]


def test_select_by_code_and_prefix() -> None:
    registry = make_registry("TL001", "TL002", "TL011", "TL101")
    assert enabled(registry, Selection(select=("TL002",))) == ["TL002"]
    assert enabled(registry, Selection(select=("TL0",))) == ["TL001", "TL002", "TL011"]
    assert enabled(registry, Selection(select=("TL00", "TL101"))) == ["TL001", "TL002", "TL101"]


def test_ignore_removes_matching_rules() -> None:
    registry = make_registry("TL001", "TL002", "TL011")
    assert enabled(registry, Selection(ignore=("TL00",))) == ["TL011"]
    assert enabled(registry, Selection(ignore=("ALL",))) == []


def test_more_specific_entry_wins_and_ignore_wins_ties() -> None:
    registry = make_registry("TL001", "TL002", "TL011")
    assert enabled(registry, Selection(select=("TL002",), ignore=("TL0",))) == ["TL002"]
    assert enabled(registry, Selection(select=("TL0",), ignore=("TL002",))) == ["TL001", "TL011"]
    assert enabled(registry, Selection(select=("TL002",), ignore=("TL002",))) == []
    assert enabled(registry, Selection(select=("ALL",), ignore=("TL00",))) == ["TL011"]


def test_later_layers_override_earlier_ones() -> None:
    registry = make_registry("TL001", "TL002", "TL004")
    manifest = Selection(ignore=("TL004",))
    assert enabled(registry, manifest) == ["TL001", "TL002"]
    # A command-line select replaces the set, so it can re-enable an ignored rule...
    assert enabled(registry, manifest, Selection(select=("TL004",))) == ["TL004"]
    # ...while a command-line ignore only removes more.
    assert enabled(registry, manifest, Selection(ignore=("TL001",))) == ["TL002"]


def test_builtin_registry_codes_are_stable() -> None:
    codes = [rule.code for rule in REGISTRY]
    assert codes[0] == "TL000"
    assert codes == sorted(codes)
    assert all(isinstance(rule, Rule) for rule in REGISTRY)
    assert REGISTRY.get("TL000").needs_files is False
