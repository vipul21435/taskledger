"""Everything a lint rule may look at, read lazily and cached per bundle.

Rules never touch the file system directly. They ask the :class:`LintContext`
for the bundle layout (where the tests, solution and Dockerfiles live), the
list of files, and a file's size, text, lines or parsed Python AST. Every read
is cached, so rules that look at the same file share one read and one parse.

The layout comes from the validated manifest. When the manifest is invalid the
linter still runs: each manifest section is validated on its own and falls
back to the conventional layout only if that section is broken, so a typo in
``[task]`` does not hide a missing grader.
"""

from __future__ import annotations

import ast
import os
import re
import tomllib
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from taskledger.bundle.hashing import DEFAULT_IGNORE, is_ignored
from taskledger.bundle.loader import BundleIssue
from taskledger.bundle.manifest import (
    MANIFEST_FILENAME,
    EnvironmentSpec,
    GraderTestsSpec,
    LintSettings,
    Manifest,
    SolutionSpec,
    VerifierSpec,
)
from taskledger.lint.locate import toml_line

#: Content rules skip files larger than this; TL005 reports them instead.
MAX_SCAN_BYTES: Final = 4 * 1024 * 1024

_SHEBANG_RE: Final = re.compile(rb"#![^\n]*\b(?P<interp>python3?|bash|sh|dash|zsh)\b")
_SHELL_SUFFIXES: Final = (".sh", ".bash")


@dataclass(frozen=True, slots=True)
class Layout:
    """Where the parts of a bundle live, plus the linter settings."""

    environment: EnvironmentSpec = field(default_factory=EnvironmentSpec)
    verifier: VerifierSpec = field(default_factory=VerifierSpec)
    solution: SolutionSpec = field(default_factory=SolutionSpec)
    tests: GraderTestsSpec = field(default_factory=GraderTestsSpec)
    lint: LintSettings = field(default_factory=LintSettings)

    @classmethod
    def from_manifest(cls, manifest: Manifest) -> Layout:
        """Layout of a bundle whose manifest validated."""
        return cls(
            environment=manifest.environment,
            verifier=manifest.verifier,
            solution=manifest.solution,
            tests=manifest.tests,
            lint=manifest.lint,
        )

    @classmethod
    def best_effort(cls, raw: Mapping[str, Any] | None) -> Layout:
        """Validate each section of a raw manifest on its own; defaults where broken."""
        raw = raw or {}

        def section[M: BaseModel](key: str, model: type[M]) -> M:
            value = raw.get(key, {})
            try:
                return model.model_validate(value)
            except ValidationError:
                return model()

        return cls(
            environment=section("environment", EnvironmentSpec),
            verifier=section("verifier", VerifierSpec),
            solution=section("solution", SolutionSpec),
            tests=section("tests", GraderTestsSpec),
            lint=section("lint", LintSettings),
        )


class LintContext:
    """Read-only, cached view of one bundle for lint rules."""

    def __init__(
        self,
        root: Path,
        layout: Layout,
        *,
        manifest: Manifest | None = None,
        issues: tuple[BundleIssue, ...] = (),
        config_issues: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.root = root
        self.layout = layout
        self.manifest = manifest
        #: Problems reported by the bundle loader (schema and path errors).
        self.issues = issues
        #: ``(manifest location, message)`` problems in the ``[lint]`` section.
        self.config_issues = config_issues
        self._bytes: dict[str, bytes | None] = {}
        self._python: dict[str, ast.Module | SyntaxError | None] = {}

    # -- files ---------------------------------------------------------------

    @cached_property
    def files(self) -> tuple[str, ...]:
        """Every regular file in the bundle, as sorted POSIX paths.

        Symlinks are not followed and not listed; entries matching the hash
        ignore list (``.git``, ``__pycache__``, ``.DS_Store``, ...) are skipped.
        """
        found: list[str] = []
        for directory, dirnames, filenames in os.walk(self.root):
            base = Path(directory).relative_to(self.root).as_posix()
            prefix = "" if base == "." else base + "/"
            dirnames[:] = [
                name
                for name in dirnames
                if not is_ignored(name, DEFAULT_IGNORE) and not Path(directory, name).is_symlink()
            ]
            for name in filenames:
                path = Path(directory, name)
                if is_ignored(name, DEFAULT_IGNORE) or path.is_symlink() or not path.is_file():
                    continue
                found.append(prefix + name)
        return tuple(sorted(found))

    def path(self, relative: str) -> Path:
        """Absolute path of a bundle-relative POSIX path."""
        return self.root.joinpath(*relative.split("/"))

    def files_under(self, directory: str) -> tuple[str, ...]:
        """Files inside ``directory`` (bundle-relative, ``"."`` for everything)."""
        if directory in ("", "."):
            return self.files
        prefix = directory.rstrip("/") + "/"
        return tuple(name for name in self.files if name.startswith(prefix))

    def is_dir(self, relative: str) -> bool:
        """True when ``relative`` is a directory inside the bundle."""
        return self.path(relative).is_dir()

    def size(self, relative: str) -> int:
        """Size in bytes of a listed file."""
        return self.path(relative).lstat().st_size

    # -- contents ------------------------------------------------------------

    def read_bytes(self, relative: str) -> bytes | None:
        """File content, or None if unreadable or larger than :data:`MAX_SCAN_BYTES`."""
        if relative not in self._bytes:
            data: bytes | None
            try:
                data = (
                    None
                    if self.size(relative) > MAX_SCAN_BYTES
                    else self.path(relative).read_bytes()
                )
            except OSError:
                data = None
            self._bytes[relative] = data
        return self._bytes[relative]

    def text(self, relative: str) -> str | None:
        """Decoded text of a text file; None for binary (NUL byte) or skipped files."""
        data = self.read_bytes(relative)
        if data is None or b"\x00" in data:
            return None
        return data.decode("utf-8", errors="replace")

    def lines(self, relative: str) -> tuple[str, ...]:
        """Lines of a text file without line endings; empty for binary files."""
        text = self.text(relative)
        return tuple(text.splitlines()) if text is not None else ()

    def is_python(self, relative: str) -> bool:
        """``*.py`` files and extension-less files with a python shebang."""
        return relative.endswith(".py") or self._shebang(relative) in ("python", "python3")

    def is_shell(self, relative: str) -> bool:
        """``*.sh``/``*.bash`` files and files with a POSIX shell shebang."""
        if relative.endswith(_SHELL_SUFFIXES):
            return True
        return self._shebang(relative) in ("bash", "sh", "dash", "zsh")

    def _shebang(self, relative: str) -> str | None:
        data = self.read_bytes(relative)
        if data is None:
            return None
        match = _SHEBANG_RE.match(data)
        return match.group("interp").decode() if match else None

    def _parse(self, relative: str) -> ast.Module | SyntaxError | None:
        if relative not in self._python:
            text = self.text(relative)
            result: ast.Module | SyntaxError | None = None
            if text is not None:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")  # e.g. invalid escape sequences
                        result = ast.parse(text, filename=relative)
                except SyntaxError as exc:
                    result = exc
                except (ValueError, RecursionError, MemoryError):  # too deep to parse
                    result = None
            self._python[relative] = result
        return self._python[relative]

    def python(self, relative: str) -> ast.Module | None:
        """Parsed module, or None when the file is binary, skipped or invalid."""
        parsed = self._parse(relative)
        return parsed if isinstance(parsed, ast.Module) else None

    def python_error(self, relative: str) -> SyntaxError | None:
        """The syntax error that stopped :meth:`python`, if any."""
        parsed = self._parse(relative)
        return parsed if isinstance(parsed, SyntaxError) else None

    # -- manifest ------------------------------------------------------------

    @cached_property
    def manifest_text(self) -> str:
        """Text of ``task.toml`` ("" when missing or unreadable)."""
        try:
            return (self.root / MANIFEST_FILENAME).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def manifest_line(self, loc: str | tuple[str, ...]) -> int:
        """Line of a manifest field such as ``verifier.command`` (1 if not found)."""
        return toml_line(self.manifest_text, loc)


def read_raw_manifest(root: Path) -> dict[str, Any] | None:
    """Parse ``task.toml`` without validating it; None if it cannot be parsed."""
    try:
        with (root / MANIFEST_FILENAME).open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
