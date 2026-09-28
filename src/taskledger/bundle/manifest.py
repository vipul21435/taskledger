"""Typed model of a task bundle manifest (``task.toml``).

A task bundle is a directory::

    task.toml                 manifest (this model)
    instruction.md            what the agent is asked to do
    environment/Dockerfile    image the agent works in
    verifier/Dockerfile       image the grader runs in
    solution/solve.sh         reference solution entry point
    tests/                    grader tests
    baseline/                 optional untouched starting workspace

Every section except ``[task]`` is optional and defaults to the conventional
layout above, so a minimal manifest is ``schema_version`` plus ``[task]``.

The model is purely syntactic. It validates types (strictly: no coercion of
``"5"`` to ``5``), identifiers, versions, image references, limits and the shape
of every path. Whether declared paths exist and stay inside the bundle on disk
is checked by :mod:`taskledger.bundle.loader`, which reports problems at the
same field locations.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field
from pydantic_core import PydanticCustomError

from taskledger.bundle.imageref import ImageRefError, parse_image_ref

MANIFEST_FILENAME: Final = "task.toml"
SCHEMA_VERSION: Final = 1

#: ``[task]`` fields that record who wrote a task and when, not what it is.
#: They never influence the canonical content hash.
METADATA_FIELDS: Final = ("authors", "created_at", "notes")

_SLUG_RE: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SEMVER_RE: Final = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)
_ENV_NAME_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLATFORM_RE: Final = re.compile(r"[a-z0-9]+/[a-z0-9_]+(?:/[a-z0-9]+)?")
_WINDOWS_DRIVE_RE: Final = re.compile(r"[A-Za-z]:")

MAX_TAGS: Final = 16


def _slug(value: str) -> str:
    if not 3 <= len(value) <= 64 or _SLUG_RE.fullmatch(value) is None:
        raise PydanticCustomError(
            "invalid_slug",
            "must be a lowercase slug of 3-64 characters (letters, digits and single "
            "hyphens), got '{value}'",
            {"value": value},
        )
    return value


def _tag(value: str) -> str:
    if not 2 <= len(value) <= 32 or _SLUG_RE.fullmatch(value) is None:
        raise PydanticCustomError(
            "invalid_tag",
            "tags must be lowercase slugs of 2-32 characters, got '{value}'",
            {"value": value},
        )
    return value


def _semver(value: str) -> str:
    if _SEMVER_RE.fullmatch(value) is None:
        raise PydanticCustomError(
            "invalid_semver",
            "must be a semantic version such as 1.0.0 or 2.1.0-rc.1, got '{value}'",
            {"value": value},
        )
    return value


def _single_line(value: str) -> str:
    stripped = value.strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        raise PydanticCustomError("invalid_title", "must be a single non-blank line")
    return stripped


def _non_blank(value: str) -> str:
    if not value.strip():
        raise PydanticCustomError("blank_string", "must not be blank")
    return value


def _rel_path(value: str) -> str:
    """Check the shape of a bundle-relative POSIX path and return its normal form."""
    if not value:
        raise PydanticCustomError("invalid_path", "must not be empty")
    if "\x00" in value:
        raise PydanticCustomError("invalid_path", "must not contain NUL bytes")
    if "\\" in value:
        raise PydanticCustomError(
            "invalid_path", "must use '/' as the separator, got '{value}'", {"value": value}
        )
    if value.startswith("/") or _WINDOWS_DRIVE_RE.match(value):
        raise PydanticCustomError(
            "absolute_path", "must be relative to the bundle root, got '{value}'", {"value": value}
        )
    normalized = posixpath.normpath(value)
    if normalized == ".." or normalized.startswith("../"):
        raise PydanticCustomError(
            "path_escapes_bundle", "must stay inside the bundle, got '{value}'", {"value": value}
        )
    return normalized


def _abs_posix_path(value: str) -> str:
    if not value.startswith("/") or "\\" in value or "\x00" in value:
        raise PydanticCustomError(
            "invalid_workdir", "must be an absolute POSIX path, got '{value}'", {"value": value}
        )
    normalized = posixpath.normpath(value)
    if normalized != value.rstrip("/") and value != "/":
        raise PydanticCustomError(
            "invalid_workdir",
            "must be normalized (no '.', '..' or repeated '/'), got '{value}'",
            {"value": value},
        )
    return normalized


def _image_ref(value: str) -> str:
    try:
        parse_image_ref(value)
    except ImageRefError as exc:
        raise PydanticCustomError(
            "invalid_image_ref", "invalid image reference: {reason}", {"reason": str(exc)}
        ) from exc
    return value


def _platform(value: str) -> str:
    if _PLATFORM_RE.fullmatch(value) is None:
        raise PydanticCustomError(
            "invalid_platform",
            "must look like os/arch or os/arch/variant (e.g. linux/amd64), got '{value}'",
            {"value": value},
        )
    return value


def _env_name(value: str) -> str:
    if _ENV_NAME_RE.fullmatch(value) is None:
        raise PydanticCustomError(
            "invalid_build_arg",
            "build arg names must be identifiers, got '{value}'",
            {"value": value},
        )
    return value


def _array_to_tuple(value: object) -> object:
    """TOML arrays arrive as lists; strict mode only accepts tuples for tuple fields."""
    return tuple(value) if isinstance(value, list) else value


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    for item in values:
        if item in seen:
            raise PydanticCustomError(
                "duplicate_item", "contains '{item}' more than once", {"item": item}
            )
        seen.add(item)
    return tuple(sorted(values))


Slug = Annotated[str, AfterValidator(_slug)]
Tag = Annotated[str, AfterValidator(_tag)]
SemVer = Annotated[str, AfterValidator(_semver)]
RelPath = Annotated[str, AfterValidator(_rel_path)]
AbsPosixPath = Annotated[str, AfterValidator(_abs_posix_path)]
ImageRefStr = Annotated[str, AfterValidator(_image_ref)]
Platform = Annotated[str, AfterValidator(_platform)]
BuildArgName = Annotated[str, AfterValidator(_env_name)]
NonBlank = Annotated[str, AfterValidator(_non_blank)]
Title = Annotated[str, Field(min_length=3, max_length=120), AfterValidator(_single_line)]
Tags = Annotated[
    tuple[Tag, ...],
    BeforeValidator(_array_to_tuple),
    Field(max_length=MAX_TAGS),
    AfterValidator(_sorted_unique),
]
Authors = Annotated[tuple[NonBlank, ...], BeforeValidator(_array_to_tuple)]
Command = Annotated[tuple[NonBlank, ...], BeforeValidator(_array_to_tuple), Field(min_length=1)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TaskInfo(_Section):
    """``[task]``: identity and classification of the task."""

    id: Slug
    version: SemVer
    title: Title
    category: Slug
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    tags: Tags = ()
    # Metadata: excluded from the canonical content hash (see METADATA_FIELDS).
    authors: Authors = ()
    created_at: date | None = None
    notes: str | None = None


class InstructionSpec(_Section):
    """``[instruction]``: the prompt shown to the agent."""

    path: RelPath = "instruction.md"


class _ImageSpec(_Section):
    dockerfile: RelPath
    context: RelPath | None = None
    image: ImageRefStr | None = None
    platform: Platform = "linux/amd64"
    build_args: dict[BuildArgName, str] = Field(default_factory=dict)

    @property
    def effective_context(self) -> str:
        """Build context directory; defaults to the directory holding the Dockerfile."""
        if self.context is not None:
            return self.context
        return posixpath.dirname(self.dockerfile) or "."


class EnvironmentSpec(_ImageSpec):
    """``[environment]``: the image the agent works in."""

    dockerfile: RelPath = "environment/Dockerfile"
    workdir: AbsPosixPath = "/app"


class VerifierSpec(_ImageSpec):
    """``[verifier]``: the image the grader runs in and the command that grades."""

    dockerfile: RelPath = "verifier/Dockerfile"
    command: Command = ("pytest", "-q", "tests")


class SolutionSpec(_Section):
    """``[solution]``: the reference solution; ``entrypoint`` is relative to ``path``."""

    path: RelPath = "solution"
    entrypoint: RelPath = "solve.sh"

    @property
    def entrypoint_path(self) -> str:
        """Entry point relative to the bundle root."""
        return posixpath.normpath(posixpath.join(self.path, self.entrypoint))


class GraderTestsSpec(_Section):
    """``[tests]``: the grader test directory."""

    path: RelPath = "tests"


class BaselineSpec(_Section):
    """``[baseline]``: the untouched starting workspace the grader must reject."""

    path: RelPath = "baseline"


class Timeouts(_Section):
    """``[timeouts]``: wall-clock limits in seconds."""

    build_sec: int = Field(default=900, ge=1, le=7200)
    agent_sec: int = Field(default=1800, ge=1, le=86400)
    verifier_sec: int = Field(default=300, ge=1, le=3600)


class Resources(_Section):
    """``[resources]``: limits for the agent environment container."""

    cpus: float = Field(default=1.0, gt=0, le=64)
    memory_mb: int = Field(default=2048, ge=64, le=262144)
    network: bool = False


@dataclass(frozen=True, slots=True)
class DeclaredPath:
    """A path the manifest promises exists, with the field that declared it."""

    loc: tuple[str, ...]
    path: str
    kind: Literal["file", "dir"]


class Manifest(_Section):
    """The whole ``task.toml``."""

    schema_version: Literal[1]
    task: TaskInfo
    instruction: InstructionSpec = Field(default_factory=InstructionSpec)
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    verifier: VerifierSpec = Field(default_factory=VerifierSpec)
    solution: SolutionSpec = Field(default_factory=SolutionSpec)
    tests: GraderTestsSpec = Field(default_factory=GraderTestsSpec)
    baseline: BaselineSpec | None = None
    timeouts: Timeouts = Field(default_factory=Timeouts)
    resources: Resources = Field(default_factory=Resources)

    def declared_paths(self) -> tuple[DeclaredPath, ...]:
        """Every path the bundle must contain, in manifest order."""
        paths = [
            DeclaredPath(("instruction", "path"), self.instruction.path, "file"),
            DeclaredPath(("environment", "dockerfile"), self.environment.dockerfile, "file"),
        ]
        if self.environment.context is not None:
            paths.append(DeclaredPath(("environment", "context"), self.environment.context, "dir"))
        paths.append(DeclaredPath(("verifier", "dockerfile"), self.verifier.dockerfile, "file"))
        if self.verifier.context is not None:
            paths.append(DeclaredPath(("verifier", "context"), self.verifier.context, "dir"))
        paths += [
            DeclaredPath(("solution", "path"), self.solution.path, "dir"),
            DeclaredPath(("solution", "entrypoint"), self.solution.entrypoint_path, "file"),
            DeclaredPath(("tests", "path"), self.tests.path, "dir"),
        ]
        if self.baseline is not None:
            paths.append(DeclaredPath(("baseline", "path"), self.baseline.path, "dir"))
        return tuple(paths)
