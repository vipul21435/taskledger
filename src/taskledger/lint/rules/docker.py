"""TL002: every base image must be pinned by digest, including multi-stage and ARG forms."""

from __future__ import annotations

import posixpath
from collections.abc import Iterator, Mapping, Set
from dataclasses import dataclass

from taskledger.bundle.imageref import ImageRefError, parse_image_ref
from taskledger.lint.context import LintContext
from taskledger.lint.dockerfile import expand, flag_values, parse_arg, parse_dockerfile, parse_from
from taskledger.lint.finding import Severity, Violation
from taskledger.lint.registry import rule


@dataclass(frozen=True, slots=True)
class DockerfileRef:
    """A Dockerfile in the bundle; ``section`` is the manifest section declaring it."""

    path: str
    build_args: Mapping[str, str]
    section: str | None


def is_dockerfile_name(name: str) -> bool:
    """Dockerfile, Containerfile, ``*.Dockerfile`` and ``Dockerfile.*`` (not ignore files)."""
    lower = name.lower()
    if lower.endswith(".dockerignore"):
        return False
    return (
        lower in ("dockerfile", "containerfile")
        or lower.endswith((".dockerfile", ".containerfile"))
        or lower.startswith(("dockerfile.", "containerfile."))
    )


def dockerfiles(ctx: LintContext) -> list[DockerfileRef]:
    """Declared Dockerfiles first (with their build args), then any other Dockerfile."""
    layout = ctx.layout
    found: list[DockerfileRef] = []
    seen: set[str] = set()
    declared = (
        (layout.environment.dockerfile, layout.environment.build_args, "environment"),
        (layout.verifier.dockerfile, layout.verifier.build_args, "verifier"),
    )
    for path, build_args, section in declared:
        if path in ctx.files and path not in seen:
            found.append(DockerfileRef(path, build_args, section))
            seen.add(path)
    for path in ctx.files:
        if path not in seen and is_dockerfile_name(posixpath.basename(path)):
            found.append(DockerfileRef(path, {}, None))
            seen.add(path)
    return found


def _image_problem(
    raw: str, values: Mapping[str, str | None], stages: Set[str], ref: DockerfileRef, what: str
) -> str | None:
    expanded, missing = expand(raw, values)
    if missing:
        names = ", ".join(dict.fromkeys(missing))
        where = "in the Dockerfile"
        if ref.section is not None:
            where += f" or in [{ref.section}].build_args"
        return (
            f"{what} '{raw}' depends on build arg {names}, which has no value {where}, "
            "so its digest cannot be checked"
        )
    if expanded.lower() == "scratch" or expanded.lower() in stages:
        return None
    try:
        image = parse_image_ref(expanded)
    except ImageRefError as exc:
        return f"{what} '{expanded}' is not a valid image reference: {exc}"
    if image.is_pinned:
        return None
    resolved = f" (resolved from '{raw}')" if expanded != raw else ""
    return f"{what} '{expanded}'{resolved} is not pinned by digest"


def check_dockerfile(text: str, ref: DockerfileRef) -> Iterator[Violation]:
    """Unpinned images in ``FROM`` and ``COPY/ADD --from`` of one Dockerfile."""
    global_args: dict[str, str | None] = {}
    stage_args: dict[str, str | None] = {}
    stages: set[str] = set()
    seen_from = False
    for instruction in parse_dockerfile(text):
        if instruction.keyword == "ARG":
            target = stage_args if seen_from else global_args
            target.update(parse_arg(instruction.value))
        elif instruction.keyword == "FROM":
            seen_from = True
            stage_args = {}
            parsed = parse_from(instruction.value)
            if parsed is None:
                continue
            values = {**global_args, **ref.build_args}
            problem = _image_problem(parsed.image, values, stages, ref, "base image")
            if problem is not None:
                column = instruction.column_of(parsed.offset)
                yield Violation(ref.path, problem, instruction.line, column)
            if parsed.stage is not None:
                stages.add(parsed.stage.lower())
        elif instruction.keyword in ("COPY", "ADD"):
            for source, offset in flag_values(instruction.value, "from"):
                if source.isdigit() or source.lower() in stages:
                    continue
                values = {**global_args, **stage_args, **ref.build_args}
                problem = _image_problem(source, values, stages, ref, "COPY --from image")
                if problem is not None:
                    column = instruction.column_of(offset)
                    yield Violation(ref.path, problem, instruction.line, column)


@rule(
    "TL002",
    name="unpinned-base-image",
    severity=Severity.ERROR,
    description=(
        "Every image a Dockerfile builds on (FROM, including multi-stage and "
        "ARG-substituted forms, and COPY --from) must be pinned by an @sha256 digest, "
        "so the environment cannot drift after review."
    ),
    fix=(
        "Pin the image by digest, e.g. FROM python:3.12-slim@sha256:<digest> "
        "(get it with: docker buildx imagetools inspect python:3.12-slim)."
    ),
)
def unpinned_base_image(ctx: LintContext) -> Iterator[Violation]:
    """Check every Dockerfile in the bundle."""
    for ref in dockerfiles(ctx):
        text = ctx.text(ref.path)
        if text is not None:
            yield from check_dockerfile(text, ref)
