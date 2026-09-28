"""TL005: files and bundles must stay under the configured size limits.

Oversized bundles are almost always an accident (a data dump, a virtualenv,
a core file, a model checkpoint) and they are expensive everywhere downstream:
every build context, cache object and ledger upload carries them. Two limits
from ``[lint]`` in ``task.toml`` apply:

- ``max_file_kb`` (default 1024): each file, reported at the file;
- ``max_bundle_kb`` (default 20480): the sum of every file, reported at the
  ``[lint]`` section of ``task.toml`` with the largest files listed.

Sizes are counted over the same file set the canonical hash covers: symlinks
and ignored clutter (``.git``, ``__pycache__``, ``.DS_Store``, ...) are skipped.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from taskledger.bundle.manifest import MANIFEST_FILENAME
from taskledger.lint.context import LintContext
from taskledger.lint.finding import Severity, Violation
from taskledger.lint.registry import rule

#: How many of the largest files a bundle-size finding names.
LARGEST_LISTED: Final = 3

_UNITS: Final = ("KiB", "MiB", "GiB", "TiB")


def human_size(size: int) -> str:
    """Binary-prefixed size with one decimal: ``512 B``, ``1.5 KiB``, ``20.0 MiB``."""
    if size < 1024:
        return f"{size} B"
    value = size / 1024
    index = 0
    while value >= 1024 and index < len(_UNITS) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {_UNITS[index]}"


@rule(
    "TL005",
    name="oversized-file",
    severity=Severity.ERROR,
    description=(
        "Every file must stay under lint.max_file_kb (default 1024 KiB) and the whole "
        "bundle under lint.max_bundle_kb (default 20480 KiB)."
    ),
    fix=(
        "Remove generated or downloaded artifacts, generate large inputs in the "
        "environment build instead, or raise max_file_kb / max_bundle_kb under [lint] "
        "in task.toml if the file is really needed."
    ),
)
def oversized_file(ctx: LintContext) -> Iterator[Violation]:
    """Report files over the per-file limit, then a bundle over the total limit."""
    settings = ctx.layout.lint
    file_limit = settings.max_file_kb * 1024
    bundle_limit = settings.max_bundle_kb * 1024
    sizes: dict[str, int] = {}
    for path in ctx.files:
        try:
            sizes[path] = ctx.size(path)
        except OSError:  # vanished between listing and stat
            continue
        if sizes[path] > file_limit:
            yield Violation(
                path,
                f"file is {human_size(sizes[path])} ({sizes[path]} bytes), over the "
                f"per-file limit of {settings.max_file_kb} KiB (lint.max_file_kb)",
            )
    total = sum(sizes.values())
    if total > bundle_limit:
        largest = sorted(sizes.items(), key=lambda item: (-item[1], item[0]))[:LARGEST_LISTED]
        listed = ", ".join(f"{path} ({human_size(size)})" for path, size in largest)
        yield Violation(
            MANIFEST_FILENAME,
            f"bundle files total {human_size(total)} ({total} bytes), over the bundle "
            f"limit of {settings.max_bundle_kb} KiB (lint.max_bundle_kb); largest: {listed}",
            ctx.manifest_line("lint.max_bundle_kb"),
        )
