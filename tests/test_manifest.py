from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from taskledger.bundle.manifest import MAX_TAGS, DeclaredPath, Manifest

BASE: dict[str, Any] = {
    "schema_version": 1,
    "task": {
        "id": "sum-of-squares",
        "version": "1.0.0",
        "title": "Sum of squares",
        "category": "arithmetic",
    },
}


def with_field(dotted: str, value: object) -> dict[str, Any]:
    """BASE with one nested field set, e.g. with_field("task.version", "2.0.0")."""
    data = copy.deepcopy(BASE)
    *parents, leaf = dotted.split(".")
    node = data
    for key in parents:
        node = node.setdefault(key, {})
    node[leaf] = value
    return data


def errors_for(data: dict[str, Any]) -> list[tuple[tuple[int | str, ...], str]]:
    with pytest.raises(ValidationError) as info:
        Manifest.model_validate(data)
    return [(tuple(e["loc"]), e["type"]) for e in info.value.errors()]


def test_minimal_manifest_gets_conventional_defaults() -> None:
    manifest = Manifest.model_validate(BASE)
    assert manifest.task.difficulty == "medium"
    assert manifest.instruction.path == "instruction.md"
    assert manifest.environment.dockerfile == "environment/Dockerfile"
    assert manifest.environment.effective_context == "environment"
    assert manifest.environment.workdir == "/app"
    assert manifest.verifier.command == ("pytest", "-q", "tests")
    assert manifest.solution.entrypoint_path == "solution/solve.sh"
    assert manifest.baseline is None
    assert manifest.timeouts.verifier_sec == 300
    assert manifest.resources.network is False


def test_declared_paths_cover_every_required_file_and_directory() -> None:
    data = with_field("baseline.path", "baseline")
    data["verifier"] = {"context": "."}
    declared = Manifest.model_validate(data).declared_paths()
    assert declared == (
        DeclaredPath(("instruction", "path"), "instruction.md", "file"),
        DeclaredPath(("environment", "dockerfile"), "environment/Dockerfile", "file"),
        DeclaredPath(("verifier", "dockerfile"), "verifier/Dockerfile", "file"),
        DeclaredPath(("verifier", "context"), ".", "dir"),
        DeclaredPath(("solution", "path"), "solution", "dir"),
        DeclaredPath(("solution", "entrypoint"), "solution/solve.sh", "file"),
        DeclaredPath(("tests", "path"), "tests", "dir"),
        DeclaredPath(("baseline", "path"), "baseline", "dir"),
    )


def test_explicit_environment_context_is_declared() -> None:
    manifest = Manifest.model_validate(with_field("environment.context", "."))
    assert DeclaredPath(("environment", "context"), ".", "dir") in manifest.declared_paths()


def test_dockerfile_at_bundle_root_uses_root_as_context() -> None:
    manifest = Manifest.model_validate(with_field("environment.dockerfile", "Dockerfile"))
    assert manifest.environment.effective_context == "."


@pytest.mark.parametrize("task_id", ["abc", "modular-inverse-table", "a1-b2-c3", "x" * 64])
def test_accepts_slug_ids(task_id: str) -> None:
    assert Manifest.model_validate(with_field("task.id", task_id)).task.id == task_id


@pytest.mark.parametrize(
    "task_id",
    ["ab", "x" * 65, "Upper-Case", "-lead", "trail-", "dou--ble", "under_score", "sp ace"],
)
def test_rejects_non_slug_ids(task_id: str) -> None:
    assert errors_for(with_field("task.id", task_id)) == [(("task", "id"), "invalid_slug")]


@pytest.mark.parametrize(
    "version", ["0.0.1", "1.0.0", "10.20.30", "1.0.0-rc.1", "1.0.0-alpha.beta", "1.2.3+build.7"]
)
def test_accepts_semver(version: str) -> None:
    assert Manifest.model_validate(with_field("task.version", version)).task.version == version


@pytest.mark.parametrize(
    "version", ["1.0", "v1.0.0", "01.0.0", "1.0.0-", "1.0.0-01", "1.0.0+", "1.0.0\n", "1.0.\u0660"]
)
def test_rejects_non_semver(version: str) -> None:
    assert errors_for(with_field("task.version", version)) == [
        (("task", "version"), "invalid_semver")
    ]


@pytest.mark.parametrize(
    ("dotted", "value", "expected_type"),
    [
        ("task.version", 1, "string_type"),
        ("timeouts.verifier_sec", "30", "int_type"),
        ("resources.network", 1, "bool_type"),
        ("resources.cpus", "2", "float_type"),
        ("task.created_at", "2026-09-29", "date_type"),
        ("task.difficulty", "extreme", "literal_error"),
    ],
)
def test_strict_mode_never_coerces(dotted: str, value: object, expected_type: str) -> None:
    [(loc, error_type)] = errors_for(with_field(dotted, value))
    assert loc == tuple(dotted.split("."))
    assert error_type == expected_type


def test_integer_cpus_are_accepted_as_float() -> None:
    assert Manifest.model_validate(with_field("resources.cpus", 2)).resources.cpus == 2.0


def test_metadata_fields_are_typed() -> None:
    data = copy.deepcopy(BASE)
    data["task"] |= {"authors": ["A. Author"], "created_at": date(2026, 9, 29), "notes": "draft"}
    task = Manifest.model_validate(data).task
    assert task.authors == ("A. Author",)
    assert task.created_at == date(2026, 9, 29)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("./instruction.md", "instruction.md"),
        ("docs//prompt.md", "docs/prompt.md"),
        ("docs/./x/../prompt.md", "docs/prompt.md"),
    ],
)
def test_relative_paths_are_normalized(value: str, expected: str) -> None:
    assert Manifest.model_validate(with_field("instruction.path", value)).instruction.path == (
        expected
    )


@pytest.mark.parametrize(
    ("value", "expected_type"),
    [
        ("", "invalid_path"),
        ("/etc/passwd", "absolute_path"),
        ("C:/task/instruction.md", "absolute_path"),
        ("docs\\prompt.md", "invalid_path"),
        ("nul\x00byte", "invalid_path"),
        ("..", "path_escapes_bundle"),
        ("../outside.md", "path_escapes_bundle"),
        ("docs/../../outside.md", "path_escapes_bundle"),
    ],
)
def test_rejects_paths_outside_the_bundle(value: str, expected_type: str) -> None:
    assert errors_for(with_field("instruction.path", value)) == [
        (("instruction", "path"), expected_type)
    ]


def test_solution_entrypoint_cannot_escape_solution_directory_lexically() -> None:
    assert errors_for(with_field("solution.entrypoint", "../run.sh")) == [
        (("solution", "entrypoint"), "path_escapes_bundle")
    ]


def test_image_reference_is_validated() -> None:
    assert errors_for(with_field("environment.image", "Bad/Name")) == [
        (("environment", "image"), "invalid_image_ref")
    ]
    ok = with_field("environment.image", "ghcr.io/acme/env:1.0.0@sha256:" + "0" * 64)
    assert Manifest.model_validate(ok).environment.image is not None


@pytest.mark.parametrize("platform", ["linux/amd64", "linux/arm64/v8", "linux/arm_64"])
def test_accepts_platforms(platform: str) -> None:
    assert Manifest.model_validate(with_field("verifier.platform", platform))


@pytest.mark.parametrize("platform", ["amd64", "linux/", "Linux/amd64", "linux/amd64/v8/x"])
def test_rejects_platforms(platform: str) -> None:
    assert errors_for(with_field("verifier.platform", platform)) == [
        (("verifier", "platform"), "invalid_platform")
    ]


def test_build_arg_names_must_be_identifiers() -> None:
    assert errors_for(with_field("environment.build_args", {"bad-name": "1"})) == [
        (("environment", "build_args", "bad-name", "[key]"), "invalid_build_arg")
    ]


@pytest.mark.parametrize(
    ("value", "expected_type"),
    [("app", "invalid_workdir"), ("/app/../etc", "invalid_workdir"), ("/a//b", "invalid_workdir")],
)
def test_workdir_must_be_absolute_and_normalized(value: str, expected_type: str) -> None:
    assert errors_for(with_field("environment.workdir", value)) == [
        (("environment", "workdir"), expected_type)
    ]


def test_workdir_trailing_slash_is_normalized() -> None:
    manifest = Manifest.model_validate(with_field("environment.workdir", "/srv/task/"))
    assert manifest.environment.workdir == "/srv/task"


@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("timeouts.verifier_sec", 0),
        ("timeouts.verifier_sec", 3601),
        ("timeouts.build_sec", -5),
        ("timeouts.agent_sec", 86401),
        ("resources.cpus", 0),
        ("resources.memory_mb", 32),
    ],
)
def test_limits_are_bounded(dotted: str, value: int) -> None:
    [(loc, _)] = errors_for(with_field(dotted, value))
    assert loc == tuple(dotted.split("."))


def test_tags_are_sorted_and_must_be_unique() -> None:
    manifest = Manifest.model_validate(with_field("task.tags", ["python", "math", "io"]))
    assert manifest.task.tags == ("io", "math", "python")
    assert errors_for(with_field("task.tags", ["math", "math"])) == [
        (("task", "tags"), "duplicate_item")
    ]


def test_tag_count_and_shape_are_limited() -> None:
    too_many = [f"tag-{i}" for i in range(MAX_TAGS + 1)]
    assert errors_for(with_field("task.tags", too_many)) == [(("task", "tags"), "too_long")]
    assert errors_for(with_field("task.tags", ["ok", "Not Ok"])) == [
        (("task", "tags", 1), "invalid_tag")
    ]


@pytest.mark.parametrize("title", ["  ", "two\nlines", "ab"])
def test_title_must_be_one_meaningful_line(title: str) -> None:
    [(loc, _)] = errors_for(with_field("task.title", title))
    assert loc == ("task", "title")


def test_title_is_stripped() -> None:
    assert Manifest.model_validate(with_field("task.title", "  Padded  ")).task.title == "Padded"


def test_verifier_command_must_not_be_empty() -> None:
    assert errors_for(with_field("verifier.command", [])) == [
        (("verifier", "command"), "too_short")
    ]
    assert errors_for(with_field("verifier.command", ["pytest", " "])) == [
        (("verifier", "command", 1), "blank_string")
    ]


def test_unknown_fields_and_schema_version_are_rejected() -> None:
    data = with_field("task.colour", "blue")
    data["schema_version"] = 2
    assert sorted(errors_for(data)) == [
        (("schema_version",), "literal_error"),
        (("task", "colour"), "extra_forbidden"),
    ]


def test_manifest_is_immutable() -> None:
    manifest = Manifest.model_validate(BASE)
    with pytest.raises(ValidationError):
        manifest.task.id = "other-id"  # type: ignore[misc]


def test_build_args_and_explicit_context_are_kept() -> None:
    data = with_field("environment.build_args", {"PY_VERSION": "3.12", "_EXTRA": ""})
    data["environment"]["context"] = "."
    environment = Manifest.model_validate(data).environment
    assert environment.build_args == {"PY_VERSION": "3.12", "_EXTRA": ""}
    assert environment.effective_context == "."


def test_lint_section_defaults_and_values() -> None:
    manifest = Manifest.model_validate(BASE)
    assert manifest.lint.select == ()
    assert manifest.lint.ignore == ()
    assert manifest.lint.max_file_kb == 1024
    assert manifest.lint.max_bundle_kb == 20480
    configured = Manifest.model_validate(
        with_field("lint", {"select": ["ALL"], "ignore": ["TL00", "TL004"], "max_file_kb": 8})
    )
    assert configured.lint.select == ("ALL",)
    assert configured.lint.ignore == ("TL00", "TL004")
    assert configured.lint.max_file_kb == 8


@pytest.mark.parametrize("selector", ["tl001", "TL-1", "", "TL00001", "TL0X", "T L"])
def test_lint_selectors_must_look_like_codes(selector: str) -> None:
    assert errors_for(with_field("lint.select", [selector])) == [
        (("lint", "select", 0), "invalid_rule_selector")
    ]


@pytest.mark.parametrize(("field", "value"), [("max_file_kb", 0), ("max_bundle_kb", 0)])
def test_lint_limits_are_bounded(field: str, value: int) -> None:
    assert errors_for(with_field(f"lint.{field}", value)) == [
        (("lint", field), "greater_than_equal")
    ]
