import re

import pytest

from taskledger.bundle.imageref import ImageRefError, parse_image_ref

DIGEST = "sha256:" + "ab" * 32


@pytest.mark.parametrize(
    ("text", "domain", "path", "tag", "digest"),
    [
        ("python", None, "python", None, None),
        ("python:3.12-slim", None, "python", "3.12-slim", None),
        (f"python:3.12-slim@{DIGEST}", None, "python", "3.12-slim", DIGEST),
        (f"python@{DIGEST}", None, "python", None, DIGEST),
        ("library/python:3.12", None, "library/python", "3.12", None),
        ("ghcr.io/acme/task-env:1.0.0", "ghcr.io", "acme/task-env", "1.0.0", None),
        ("localhost/env", "localhost", "env", None, None),
        ("localhost:5000/team/env:dev", "localhost:5000", "team/env", "dev", None),
        ("Registry.Example.com:443/a/b", "Registry.Example.com:443", "a/b", None, None),
        ("a__b/c.d/e-f/g--h", None, "a__b/c.d/e-f/g--h", None, None),
        ("img:_underscore", None, "img", "_underscore", None),
    ],
)
def test_parses_valid_references(
    text: str, domain: str | None, path: str, tag: str | None, digest: str | None
) -> None:
    ref = parse_image_ref(text)
    assert (ref.domain, ref.path, ref.tag, ref.digest) == (domain, path, tag, digest)
    assert str(ref) == text
    assert ref.is_pinned is (digest is not None)


def test_name_includes_registry_but_not_tag() -> None:
    ref = parse_image_ref(f"ghcr.io/acme/env:1@{DIGEST}")
    assert ref.name == "ghcr.io/acme/env"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "non-empty"),
        (" python", "whitespace"),
        ("Python", "path component 'Python'"),
        ("python:", "invalid tag ''"),
        ("python:-dash", "invalid tag"),
        ("python:" + "t" * 129, "invalid tag"),
        ("python@sha256:abc", "64 lowercase hex"),
        ("python@md5:" + "a" * 32, "sha256"),
        (f"python@{DIGEST.upper()}", "sha256"),
        (":tag", "missing repository name"),
        ("a//b", "path component ''"),
        ("a/b_", "path component 'b_'"),
        ("-bad.host/x", "invalid registry host"),
        ("host.example.com:port/x", "invalid registry host"),
        ("a" * 256, "longer than 255"),
    ],
)
def test_rejects_invalid_references(text: str, fragment: str) -> None:
    with pytest.raises(ImageRefError, match=re.escape(fragment)):
        parse_image_ref(text)
