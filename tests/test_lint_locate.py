from __future__ import annotations

import pytest

from taskledger.lint.locate import loc_parts, toml_line

MANIFEST = """\
# comment line
schema_version = 1

[task]
id = "sum-of-squares"
"version" = "1.0"
'title' = "Sum"
tags = ["a", "b"]

[environment]   # the agent image
dockerfile = "environment/Dockerfile"

[environment.build_args]
BASE = "python"

[[extra]]
name = "x"

[lint]
select = ["TL0"]
"""


@pytest.mark.parametrize(
    ("loc", "parts"),
    [
        ("task.tags[1]", ("task", "tags")),
        ("resources.cpus", ("resources", "cpus")),
        ("environment.build_args['x y'] (key)", ("environment", "build_args")),
        ("schema_version", ("schema_version",)),
        ("", ()),
        ("[0]", ()),
    ],
)
def test_loc_parts(loc: str, parts: tuple[str, ...]) -> None:
    assert loc_parts(loc) == parts


@pytest.mark.parametrize(
    ("loc", "line"),
    [
        ("schema_version", 2),
        ("task.id", 5),
        ("task.version", 6),
        ("task.title", 7),
        ("task.tags[0]", 8),
        ("environment.dockerfile", 11),
        ("environment.build_args", 13),
        ("environment.build_args.BASE", 14),
        ("lint.select", 20),
        ("lint", 19),
        ("task.category", 4),
        ("verifier.command", 1),
        ("", 1),
    ],
)
def test_toml_line_finds_keys_then_headers(loc: str, line: int) -> None:
    assert toml_line(MANIFEST, loc) == line


def test_toml_line_accepts_tuples_and_ignores_other_tables() -> None:
    assert toml_line(MANIFEST, ("extra", "name")) == 17
    assert toml_line("[a]\nkey = 1\n[b]\nkey = 2\n", ("b", "key")) == 4
    assert toml_line("", ("task", "id")) == 1
