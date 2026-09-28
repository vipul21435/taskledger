"""TL002 unpinned-base-image."""

from __future__ import annotations

import pytest

from conftest import MINIMAL_MANIFEST, PINNED_BASE, BundleFactory, lint_findings, where
from taskledger.lint.rules.docker import is_dockerfile_name

DIGEST = "sha256:" + "4f" * 32


def env_findings(factory: BundleFactory, dockerfile: str, manifest: str = MINIMAL_MANIFEST) -> list:
    return lint_findings(factory, "TL002", {"environment/Dockerfile": dockerfile}, manifest)


def test_pinned_images_pass(make_bundle: BundleFactory) -> None:
    dockerfile = (
        f"FROM {PINNED_BASE} AS build\n"
        "FROM build AS test\n"
        "FROM scratch\n"
        "COPY --from=build /x /x\n"
        "COPY --from=0 /y /y\n"
        f"COPY --from=registry.example.com/tools@{DIGEST} /bin/t /bin/t\n"
    )
    assert env_findings(make_bundle, dockerfile) == []


def test_unpinned_from_is_reported_at_the_image(make_bundle: BundleFactory) -> None:
    findings = env_findings(make_bundle, "# base\nFROM --platform=linux/amd64 python:3.12-slim\n")
    assert where(findings) == [("environment/Dockerfile", 2, 29)]
    assert findings[0].message == "base image 'python:3.12-slim' is not pinned by digest"
    assert findings[0].severity == "error"


def test_every_stage_is_checked(make_bundle: BundleFactory) -> None:
    dockerfile = (
        "FROM golang:1.22 AS build\n"
        f"FROM {PINNED_BASE} AS runtime\n"
        "FROM build AS again\n"
        "from ubuntu\n"
    )
    findings = env_findings(make_bundle, dockerfile)
    assert [(f.line, f.message) for f in findings] == [
        (1, "base image 'golang:1.22' is not pinned by digest"),
        (4, "base image 'ubuntu' is not pinned by digest"),
    ]


def test_arg_defaults_are_resolved(make_bundle: BundleFactory) -> None:
    dockerfile = (
        "ARG REGISTRY=docker.io/library\n"
        "ARG TAG=3.12-slim\n"
        f'ARG PINNED="{PINNED_BASE}"\n'
        "FROM ${REGISTRY}/python:${TAG}\n"
        "FROM $PINNED\n"
    )
    findings = env_findings(make_bundle, dockerfile)
    assert [(f.line, f.column, f.message) for f in findings] == [
        (
            4,
            6,
            "base image 'docker.io/library/python:3.12-slim' (resolved from "
            "'${REGISTRY}/python:${TAG}') is not pinned by digest",
        ),
    ]


def test_arg_without_default_cannot_be_verified(make_bundle: BundleFactory) -> None:
    findings = env_findings(make_bundle, "ARG BASE\nFROM ${BASE}\n")
    assert [f.message for f in findings] == [
        "base image '${BASE}' depends on build arg BASE, which has no value in the "
        "Dockerfile or in [environment].build_args, so its digest cannot be checked"
    ]


def test_manifest_build_args_override_defaults(make_bundle: BundleFactory) -> None:
    dockerfile = "ARG BASE=python:3.12-slim\nFROM ${BASE}\n"
    manifest = MINIMAL_MANIFEST + f'\n[environment.build_args]\nBASE = "{PINNED_BASE}"\n'
    assert env_findings(make_bundle, dockerfile, manifest) == []
    unpinned = MINIMAL_MANIFEST + '\n[environment.build_args]\nBASE = "python:3.13"\n'
    [finding] = env_findings(make_bundle, "ARG BASE\nFROM $BASE\n", unpinned)
    assert (
        finding.message
        == "base image 'python:3.13' (resolved from '$BASE') is not pinned by digest"
    )


def test_stage_args_do_not_reach_later_from_lines(make_bundle: BundleFactory) -> None:
    dockerfile = f"FROM {PINNED_BASE}\nARG LATE=python:3.12\nFROM ${{LATE}}\n"
    [finding] = env_findings(make_bundle, dockerfile)
    assert "depends on build arg LATE" in finding.message


def test_copy_from_external_image(make_bundle: BundleFactory) -> None:
    dockerfile = (
        f"FROM {PINNED_BASE}\n"
        "ARG TOOLS=ghcr.io/example/tools:1.0\n"
        "COPY --chown=app --from=${TOOLS} /bin/t /bin/t\n"
        "ADD --from=busybox /bin/sh /sh\n"
    )
    findings = env_findings(make_bundle, dockerfile)
    assert [(f.line, f.column, f.message) for f in findings] == [
        (
            3,
            25,
            "COPY --from image 'ghcr.io/example/tools:1.0' (resolved from '${TOOLS}') is "
            "not pinned by digest",
        ),
        (4, 12, "COPY --from image 'busybox' is not pinned by digest"),
    ]


def test_invalid_references_are_reported(make_bundle: BundleFactory) -> None:
    [finding] = env_findings(make_bundle, "FROM Python:Latest!\n")
    assert finding.message.startswith("base image 'Python:Latest!' is not a valid image reference")


def test_from_without_image_is_ignored(make_bundle: BundleFactory) -> None:
    assert env_findings(make_bundle, f"FROM --platform=linux/amd64\nFROM {PINNED_BASE}\n") == []


def test_verifier_and_extra_dockerfiles_are_checked(make_bundle: BundleFactory) -> None:
    files = {
        "verifier/Dockerfile": "ARG V\nFROM python:${V:-3.12}\n",
        "tools/build.Dockerfile": "FROM node:22\n",
        "tools/Dockerfile.dockerignore": "FROM ignored\n",
        "tools/Containerfile": b"FROM binary\x00",
    }
    findings = lint_findings(make_bundle, "TL002", files)
    assert [(f.file, f.message) for f in findings] == [
        ("tools/build.Dockerfile", "base image 'node:22' is not pinned by digest"),
        (
            "verifier/Dockerfile",
            "base image 'python:3.12' (resolved from 'python:${V:-3.12}') is not pinned by digest",
        ),
    ]


def test_undeclared_dockerfile_mentions_only_the_dockerfile(make_bundle: BundleFactory) -> None:
    [finding] = lint_findings(make_bundle, "TL002", {"extra/Dockerfile": "FROM $X\n"})
    assert finding.message.endswith(
        "which has no value in the Dockerfile, so its digest cannot be checked"
    )


def test_shared_dockerfile_is_checked_once(make_bundle: BundleFactory) -> None:
    manifest = MINIMAL_MANIFEST + '\n[verifier]\ndockerfile = "environment/Dockerfile"\n'
    files = {"environment/Dockerfile": "FROM alpine\n", "verifier/Dockerfile": None}
    findings = lint_findings(make_bundle, "TL002", files, manifest)
    assert where(findings) == [("environment/Dockerfile", 1, 6)]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Dockerfile", True),
        ("dockerfile", True),
        ("Containerfile", True),
        ("app.Dockerfile", True),
        ("Dockerfile.gpu", True),
        ("Dockerfile.dockerignore", False),
        ("Dockerfile.bak.dockerignore", False),
        ("README.md", False),
        ("dockerfiles", False),
    ],
)
def test_is_dockerfile_name(name: str, expected: bool) -> None:
    assert is_dockerfile_name(name) is expected
