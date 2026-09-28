"""Load a task bundle from disk into a typed :class:`Bundle`.

:func:`load_bundle` never raises for a malformed bundle. It returns a
:class:`LoadResult` holding either the bundle or every problem found, each with
a precise location such as ``task.toml:environment.dockerfile``, so an author
can fix all of them in one pass.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from taskledger.bundle.manifest import MANIFEST_FILENAME, DeclaredPath, Manifest

_KIND_NAMES = {"file": "file", "dir": "directory"}
_FRIENDLY_MESSAGES = {
    "missing": "required field is missing",
    "extra_forbidden": "unknown field",
}


@dataclass(frozen=True, slots=True)
class BundleIssue:
    """One problem with a bundle.

    ``file`` is the bundle-relative file the problem is about (usually the
    manifest; empty for the bundle directory itself), ``loc`` is the dotted field
    path inside the manifest (empty when the problem is the whole file) and
    ``code`` is a stable machine-readable identifier.
    """

    file: str
    loc: str
    code: str
    message: str

    @property
    def where(self) -> str:
        """``file:loc``, or whichever of the two is set."""
        if self.file and self.loc:
            return f"{self.file}:{self.loc}"
        return self.file or self.loc or "."

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"

    def to_dict(self) -> dict[str, str]:
        """JSON-ready representation."""
        return {"file": self.file, "loc": self.loc, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class Bundle:
    """A validated task bundle rooted at an absolute, resolved directory."""

    root: Path
    manifest: Manifest

    @property
    def id(self) -> str:
        """The task slug from ``[task].id``."""
        return self.manifest.task.id

    @property
    def version(self) -> str:
        """The task version from ``[task].version``."""
        return self.manifest.task.version

    def path(self, relative: str) -> Path:
        """Absolute path of a bundle-relative POSIX path."""
        return self.root.joinpath(*relative.split("/"))


class InvalidBundleError(Exception):
    """Raised by :meth:`LoadResult.unwrap` when the bundle has issues."""

    def __init__(self, root: Path, issues: Sequence[BundleIssue]) -> None:
        self.root = root
        self.issues = tuple(issues)
        lines = "\n".join(f"  {issue}" for issue in self.issues)
        super().__init__(f"invalid task bundle {root} ({len(self.issues)} issue(s)):\n{lines}")


@dataclass(frozen=True, slots=True)
class LoadResult:
    """Outcome of :func:`load_bundle`: a bundle, or the issues that prevented one."""

    root: Path
    bundle: Bundle | None
    issues: tuple[BundleIssue, ...]

    @property
    def ok(self) -> bool:
        """True when the bundle loaded without issues."""
        return self.bundle is not None

    def unwrap(self) -> Bundle:
        """Return the bundle or raise :class:`InvalidBundleError`."""
        if self.bundle is None:
            raise InvalidBundleError(self.root, self.issues)
        return self.bundle


def format_loc(loc: Sequence[int | str]) -> str:
    """Render a pydantic error location as ``a.b[2].c``; dict keys stay readable."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part == "[key]":
            out += " (key)"
        elif part.isidentifier():
            out += f".{part}" if out else part
        else:
            out += f"[{part!r}]"
    return out


def _issues_from_validation(exc: ValidationError) -> list[BundleIssue]:
    issues: list[BundleIssue] = []
    for error in exc.errors(include_url=False):
        code = error["type"]
        message = _FRIENDLY_MESSAGES.get(code, error["msg"])
        issues.append(BundleIssue(MANIFEST_FILENAME, format_loc(error["loc"]), code, message))
    return issues


def _read_manifest(manifest_path: Path) -> dict[str, Any] | BundleIssue:
    def issue(code: str, message: str) -> BundleIssue:
        return BundleIssue(MANIFEST_FILENAME, "", code, message)

    try:
        with manifest_path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return issue("missing_manifest", "manifest file not found")
    except IsADirectoryError:
        return issue("missing_manifest", "expected a file, found a directory")
    except OSError as exc:
        return issue("manifest_unreadable", f"cannot read manifest: {exc.strerror or exc}")
    except UnicodeDecodeError as exc:
        return issue("manifest_encoding", f"not valid UTF-8: {exc.reason} at byte {exc.start}")
    except tomllib.TOMLDecodeError as exc:
        return issue("toml_syntax", f"invalid TOML: {exc}")


def _check_declared_path(root: Path, declared: DeclaredPath) -> BundleIssue | None:
    loc = format_loc(declared.loc)
    target = root.joinpath(*declared.path.split("/"))
    try:
        resolved = target.resolve(strict=True)
    except FileNotFoundError:
        return BundleIssue(
            MANIFEST_FILENAME,
            loc,
            "path_not_found",
            f"{_KIND_NAMES[declared.kind]} not found: {declared.path}",
        )
    except (OSError, RuntimeError) as exc:  # symlink loops, permission errors
        return BundleIssue(
            MANIFEST_FILENAME, loc, "path_unreadable", f"cannot resolve {declared.path}: {exc}"
        )
    if not resolved.is_relative_to(root):
        return BundleIssue(
            MANIFEST_FILENAME,
            loc,
            "path_escapes_bundle",
            f"{declared.path} resolves outside the bundle (via a symlink)",
        )
    is_right_kind = resolved.is_file() if declared.kind == "file" else resolved.is_dir()
    if not is_right_kind:
        return BundleIssue(
            MANIFEST_FILENAME,
            loc,
            "wrong_path_kind",
            f"expected a {_KIND_NAMES[declared.kind]}: {declared.path}",
        )
    return None


def load_bundle(path: str | os.PathLike[str]) -> LoadResult:
    """Load and validate the bundle at ``path``; never raises for bad bundles."""
    given = Path(path)
    try:
        root = given.resolve(strict=True)
    except (OSError, RuntimeError):
        issue = BundleIssue("", "", "bundle_not_found", f"bundle directory not found: {given}")
        return LoadResult(given, None, (issue,))
    if not root.is_dir():
        issue = BundleIssue("", "", "bundle_not_a_directory", f"not a directory: {given}")
        return LoadResult(root, None, (issue,))

    data = _read_manifest(root / MANIFEST_FILENAME)
    if isinstance(data, BundleIssue):
        return LoadResult(root, None, (data,))

    try:
        manifest = Manifest.model_validate(data)
    except ValidationError as exc:
        return LoadResult(root, None, tuple(_issues_from_validation(exc)))

    issues = [
        problem
        for declared in manifest.declared_paths()
        if (problem := _check_declared_path(root, declared)) is not None
    ]
    if issues:
        return LoadResult(root, None, tuple(issues))
    return LoadResult(root, Bundle(root=root, manifest=manifest), ())
