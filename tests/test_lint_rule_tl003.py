"""TL003 network-in-grader."""

from __future__ import annotations

import pytest

from conftest import MINIMAL_MANIFEST, PINNED_BASE, BundleFactory, lint_findings, where
from taskledger.lint.rules.network import network_command, network_module


def grader(factory: BundleFactory, source: str, name: str = "tests/test_answer.py") -> list:
    return lint_findings(factory, "TL003", {name: source})


def test_offline_grader_passes(make_bundle: BundleFactory) -> None:
    source = (
        '"""Mentions pip install and curl only in a docstring."""\n'
        "import json\n"
        "import urllib.parse\n"
        "from http import HTTPStatus\n"
        "from urllib import parse\n"
        "\n"
        "def test_x() -> None:\n"
        '    assert urllib.parse.quote("a b") == "a%20b"\n'
        '    assert ["curly", "brace"] != ("wgetting",)\n'
        '    assert "curly" != "wgetting"\n'
    )
    assert grader(make_bundle, source) == []


def test_network_imports_are_reported(make_bundle: BundleFactory) -> None:
    source = (
        "import requests\n"
        "import urllib.request as fetch\n"
        "from socket import socket, AF_INET\n"
        "from urllib import request\n"
        "import http.client, os\n"
        "from . import helpers\n"
    )
    findings = grader(make_bundle, source)
    assert [(f.line, f.message) for f in findings] == [
        (1, "grader imports network module 'requests'"),
        (2, "grader imports network module 'urllib.request'"),
        (3, "grader imports network module 'socket'"),
        (4, "grader imports network module 'urllib.request'"),
        (5, "grader imports network module 'http.client'"),
    ]


def test_nested_imports_are_found(make_bundle: BundleFactory) -> None:
    source = "def test_x() -> None:\n    import httpx\n    assert httpx\n"
    assert where(grader(make_bundle, source)) == [("tests/test_answer.py", 2, 5)]


def test_network_commands_in_strings_and_lists(make_bundle: BundleFactory) -> None:
    source = (
        "import subprocess, sys, os\n"
        "\n"
        "def test_x() -> None:\n"
        '    subprocess.run(["curl", "-sSf", "https://example.com"], check=True)\n'
        '    subprocess.run([sys.executable, "-m", "pip", "install", "numpy"])\n'
        '    os.system("cd /tmp && wget -q http://example.com/data.bin")\n'
        '    subprocess.run(("git", "clone", "repo"))\n'
        '    assert ["curl"] != ["wget"]\n'
    )
    findings = grader(make_bundle, source)
    assert [(f.line, f.column, f.message) for f in findings] == [
        (4, 20, "grader runs 'curl' at test time"),
        (5, 20, "grader runs 'pip install' at test time"),
        (6, 15, "grader runs 'wget' at test time"),
        (7, 20, "grader runs 'git clone' at test time"),
        (8, 12, "grader runs 'curl' at test time"),
        (8, 24, "grader runs 'wget' at test time"),
    ]


def test_conftest_and_shell_scripts_are_scanned(make_bundle: BundleFactory) -> None:
    files = {
        "tests/conftest.py": "import aiohttp\n",
        "tests/setup.sh": (
            "#!/bin/sh\n"
            "# curl is only mentioned in this comment\n"
            "set -e\n"
            "python3 -m pip install -q numpy  # needs the network\n"
            "echo done; apt-get update\n"
        ),
        "tests/fixture.txt": "curl http://example.com\n",
    }
    findings = lint_findings(make_bundle, "TL003", files)
    assert [(f.file, f.line, f.column, f.message) for f in findings] == [
        ("tests/conftest.py", 1, 1, "grader imports network module 'aiohttp'"),
        ("tests/setup.sh", 4, 1, "grader script runs 'python3 -m pip install' at test time"),
        ("tests/setup.sh", 5, 12, "grader script runs 'apt-get update' at test time"),
    ]


def test_unparsable_python_is_skipped(make_bundle: BundleFactory) -> None:
    assert grader(make_bundle, "import requests\ndef broken(:\n") == []


def test_verifier_command_is_checked(make_bundle: BundleFactory) -> None:
    manifest = (
        MINIMAL_MANIFEST
        + '\n[verifier]\ncommand = ["bash", "-c", "pip install pytest && pytest -q"]\n'
    )
    findings = lint_findings(make_bundle, "TL003", manifest=manifest)
    assert [(f.file, f.line, f.message) for f in findings] == [
        ("task.toml", 10, "verifier command runs 'pip install' at test time")
    ]


def test_verifier_cmd_and_entrypoint_but_not_run(make_bundle: BundleFactory) -> None:
    dockerfile = (
        f"FROM {PINNED_BASE}\n"
        "RUN pip install --no-cache-dir pytest==8.3.5\n"
        'ENTRYPOINT ["sh", "-c", "curl -s http://example.com/key > /k && exec \\"$@\\""]\n'
        "CMD uv pip install x && pytest\n"
        'CMD {"not": "a list"}\n'
    )
    findings = lint_findings(make_bundle, "TL003", {"verifier/Dockerfile": dockerfile})
    assert [(f.file, f.line, f.column, f.message) for f in findings] == [
        ("verifier/Dockerfile", 3, 12, "verifier ENTRYPOINT runs 'curl' when the grader starts"),
        ("verifier/Dockerfile", 4, 5, "verifier CMD runs 'uv pip install' when the grader starts"),
    ]


def test_missing_verifier_dockerfile_is_not_an_error_here(make_bundle: BundleFactory) -> None:
    assert lint_findings(make_bundle, "TL003", {"verifier/Dockerfile": None}) == []


@pytest.mark.parametrize(
    ("qualified", "module"),
    [
        ("requests", "requests"),
        ("requests.adapters.HTTPAdapter", "requests"),
        ("urllib.request.urlopen", "urllib.request"),
        ("urllib.parse", None),
        ("http.HTTPStatus", None),
        ("socketserver.TCPServer", "socketserver"),
        ("json", None),
    ],
)
def test_network_module(qualified: str, module: str | None) -> None:
    assert network_module(qualified) == module


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("curl -sSf https://x", "curl"),
        ("$(wget -qO- x)", "wget"),
        ("pip3 install x", "pip3 install"),
        ("python -m pip download x", "python -m pip download"),
        ("uv add httpx", "uv add"),
        ("uv run --with rich x.py", "uv run --with"),
        ("npm ci", "npm ci"),
        ("apk add git", "apk add"),
        ("git fetch origin", "git fetch"),
        ("curly braces", None),
        ("https://curl.se", None),
        ("pip list", None),
        ("pip-install", None),
        ("uv run pytest", None),
    ],
)
def test_network_command(text: str, command: str | None) -> None:
    match = network_command(text)
    assert (match.group("cmd") if match else None) == command
