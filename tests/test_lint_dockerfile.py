from __future__ import annotations

import pytest

from taskledger.lint.dockerfile import (
    expand,
    flag_values,
    parse_arg,
    parse_dockerfile,
    parse_from,
)
from taskledger.lint.locate import split_lines


def keywords(text: str) -> list[tuple[str, str, int, int]]:
    return [(i.keyword, i.value, i.line, i.column) for i in parse_dockerfile(text)]


def test_parses_instructions_with_positions() -> None:
    text = "# comment\n\nfrom  python:3.12 AS base\n  RUN echo hi\n"
    assert keywords(text) == [
        ("FROM", "python:3.12 AS base", 3, 7),
        ("RUN", "echo hi", 4, 7),
    ]


def test_joins_continuations_and_skips_comments_inside_them() -> None:
    text = 'RUN apt-get update && \\\n# note\n\n    apt-get install -y curl\nCMD ["x"]\n'
    instructions = parse_dockerfile(text)
    assert [(i.keyword, i.line) for i in instructions] == [("RUN", 1), ("CMD", 5)]
    assert instructions[0].value == "apt-get update &&     apt-get install -y curl"


def test_escape_directive_changes_the_continuation_character() -> None:
    text = "# escape=`\nFROM base `\n  AS build\nRUN dir C:\\\n"
    assert keywords(text) == [("FROM", "base   AS build", 2, 6), ("RUN", "dir C:\\", 4, 5)]


def test_heredoc_bodies_are_not_instructions() -> None:
    text = (
        "FROM a@sha256:" + "0" * 64 + "\n"
        "RUN <<EOF\nFROM not-an-instruction\nEOF\n"
        "COPY <<-'CONF' /etc/x.conf\n\tFROM also-not\n\tCONF\n"
        "RUN cat <<<here-string\n"
        "USER nobody\n"
    )
    assert [i.keyword for i in parse_dockerfile(text)] == ["FROM", "RUN", "COPY", "RUN", "USER"]


def test_continuation_at_end_of_file_and_crlf() -> None:
    assert keywords("FROM a\r\nRUN x \\") == [("FROM", "a", 1, 6), ("RUN", "x", 2, 5)]


def test_column_of_offsets_beyond_the_first_line() -> None:
    [instruction] = parse_dockerfile("FROM --platform=linux/amd64 \\\n  python:3.12\n")
    parsed = parse_from(instruction.value)
    assert parsed is not None
    assert parsed.image == "python:3.12"
    assert instruction.column_of(0) == 6
    assert instruction.column_of(parsed.offset) == instruction.column


@pytest.mark.parametrize(
    ("value", "image", "offset", "stage"),
    [
        ("python:3.12", "python:3.12", 0, None),
        ("--platform=$BUILDPLATFORM golang:1.22 AS build", "golang:1.22", 26, "build"),
        ("base as Final", "base", 0, "Final"),
        ("base AS", "base", 0, None),
    ],
)
def test_parse_from(value: str, image: str, offset: int, stage: str | None) -> None:
    parsed = parse_from(value)
    assert parsed is not None
    assert (parsed.image, parsed.offset, parsed.stage) == (image, offset, stage)


def test_parse_from_without_image() -> None:
    assert parse_from("") is None
    assert parse_from("--platform=linux/amd64") is None


def test_parse_arg() -> None:
    assert parse_arg("BASE") == [("BASE", None)]
    assert parse_arg('A=1 B="two words" C=') == [("A", "1"), ("B", "two words"), ("C", "")]
    assert parse_arg('BROKEN="unbalanced') == [("BROKEN", '"unbalanced')]


def test_flag_values_only_reads_leading_flags() -> None:
    value = "--chown=app --from=build /src /dst --from=ignored"
    assert flag_values(value, "from") == [("build", 19)]
    assert value[19:24] == "build"
    assert flag_values("/src /dst", "from") == []


@pytest.mark.parametrize(
    ("text", "values", "expected", "missing"),
    [
        ("$A", {"A": "x"}, "x", ()),
        ("${A}:${B}", {"A": "img", "B": "1.0"}, "img:1.0", ()),
        ("${A}", {"A": None}, "", ("A",)),
        ("$UNDECLARED/x", {}, "/x", ("UNDECLARED",)),
        ("${A:-def}", {"A": None}, "def", ()),
        ("${A:-def}", {"A": ""}, "def", ()),
        ("${A-def}", {"A": ""}, "", ()),
        ("${A:-def}", {"A": "set"}, "set", ()),
        ("${A:+alt}", {"A": "set"}, "alt", ()),
        ("${A:+alt}", {"A": ""}, "", ()),
        ("${A+alt}", {"A": ""}, "alt", ()),
        ("${A+alt}", {}, "", ()),
        ("${A:-$B}", {"B": "nested"}, "nested", ()),
        ("${A:-$B}", {}, "", ("B",)),
        ("${A:+$B}", {"A": "x"}, "", ("B",)),
        ("plain", {}, "plain", ()),
    ],
)
def test_expand(
    text: str, values: dict[str, str | None], expected: str, missing: tuple[str, ...]
) -> None:
    assert expand(text, values) == (expected, missing)


def test_split_lines_only_breaks_on_newlines() -> None:
    assert split_lines("a\r\nb\rc\nd\x0ce\n") == ["a", "b", "c", "d\x0ce"]
    assert split_lines("") == []
    assert split_lines("x") == ["x"]


def test_other_directives_and_directive_only_files() -> None:
    text = "# syntax=docker/dockerfile:1\n# escape=x\nFROM a \\\n  AS b\n"
    assert keywords(text) == [("FROM", "a   AS b", 3, 6)]
    assert parse_dockerfile("# escape=`\n") == ()
    assert flag_values("--from=only-flags", "from") == [("only-flags", 7)]
