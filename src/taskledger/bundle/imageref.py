"""Parse and validate container image references: ``[registry/]path[:tag][@digest]``.

The grammar follows the reference format used by Docker and OCI registries,
with one deliberate restriction: the only accepted digest algorithm is sha256,
because that is what every registry in practice serves and what the linter
checks base images against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

MAX_NAME_LENGTH: Final = 255

_DOMAIN_COMPONENT: Final = r"(?:[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9])"
_DOMAIN_RE: Final = re.compile(rf"{_DOMAIN_COMPONENT}(?:\.{_DOMAIN_COMPONENT})*(?::[0-9]+)?")
_PATH_COMPONENT_RE: Final = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
_TAG_RE: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_DIGEST_RE: Final = re.compile(r"sha256:[0-9a-f]{64}")


class ImageRefError(ValueError):
    """Raised when a string is not a valid image reference."""


@dataclass(frozen=True, slots=True)
class ImageRef:
    """A parsed image reference. ``domain`` is None for Docker Hub short names."""

    domain: str | None
    path: str
    tag: str | None
    digest: str | None

    @property
    def name(self) -> str:
        """Repository name including the registry host, without tag or digest."""
        return f"{self.domain}/{self.path}" if self.domain else self.path

    @property
    def is_pinned(self) -> bool:
        """True when the reference names immutable content (it carries a digest)."""
        return self.digest is not None

    def __str__(self) -> str:
        text = self.name
        if self.tag is not None:
            text += f":{self.tag}"
        if self.digest is not None:
            text += f"@{self.digest}"
        return text


def parse_image_ref(text: str) -> ImageRef:
    """Parse ``text`` into an :class:`ImageRef` or raise :class:`ImageRefError`."""
    if not text or text != text.strip():
        raise ImageRefError("must be a non-empty image reference without surrounding whitespace")

    remainder = text
    digest: str | None = None
    if "@" in remainder:
        remainder, digest = remainder.split("@", 1)
        if _DIGEST_RE.fullmatch(digest) is None:
            raise ImageRefError(
                f"digest must be 'sha256:' followed by 64 lowercase hex characters, got {digest!r}"
            )

    tag: str | None = None
    colon = remainder.rfind(":")
    if colon > remainder.rfind("/"):
        remainder, tag = remainder[:colon], remainder[colon + 1 :]
        if _TAG_RE.fullmatch(tag) is None:
            raise ImageRefError(
                f"invalid tag {tag!r}: use up to 128 letters, digits, '_', '.' or '-', "
                "not starting with '.' or '-'"
            )

    if not remainder:
        raise ImageRefError("missing repository name")
    if len(remainder) > MAX_NAME_LENGTH:
        raise ImageRefError(f"repository name is longer than {MAX_NAME_LENGTH} characters")

    domain: str | None = None
    first, sep, rest = remainder.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        if _DOMAIN_RE.fullmatch(first) is None:
            raise ImageRefError(f"invalid registry host {first!r}")
        domain, remainder = first, rest

    for component in remainder.split("/"):
        if _PATH_COMPONENT_RE.fullmatch(component) is None:
            raise ImageRefError(
                f"invalid repository path component {component!r}: use lowercase letters "
                "and digits joined by '.', '_', '__' or '-'"
            )
    return ImageRef(domain=domain, path=remainder, tag=tag, digest=digest)
