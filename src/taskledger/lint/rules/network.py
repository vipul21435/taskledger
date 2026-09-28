"""TL003: the grader must not use the network at test time.

Graders run with networking disabled, and a grader that downloads anything is
neither reproducible nor safe to re-run. Checked:

- Python files under the tests directory: imports of network client or server
  modules (``requests``, ``httpx``, ``urllib.request``, ``socket``, ...) and
  string commands such as ``curl``, ``wget`` or ``pip install`` (also in list
  form, e.g. ``["pip", "install", "x"]``; docstrings are skipped);
- shell scripts under the tests directory, line by line, comments stripped;
- the manifest's ``[verifier].command``;
- ``CMD``/``ENTRYPOINT`` of the verifier Dockerfile. ``RUN`` is build time and
  is allowed to install dependencies.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from typing import Final

from taskledger.lint.context import LintContext
from taskledger.lint.dockerfile import parse_dockerfile
from taskledger.lint.finding import Severity, Violation
from taskledger.lint.pyast import ImportTable, docstring_nodes, violation_at
from taskledger.lint.registry import rule

NETWORK_MODULES: Final = frozenset(
    {
        "aiohttp",
        "ftplib",
        "grpc",
        "http.client",
        "http.server",
        "httpx",
        "imaplib",
        "paramiko",
        "poplib",
        "pycurl",
        "requests",
        "smtplib",
        "socket",
        "socketserver",
        "telnetlib",
        "urllib.request",
        "urllib3",
        "websocket",
        "websockets",
        "xmlrpc.client",
    }
)

NETWORK_COMMAND_RE: Final = re.compile(
    r"(?:^|(?<=[\s;&|(`'\"]))"
    r"(?P<cmd>curl|wget"
    r"|(?:python3?\s+-m\s+)?pip3?\s+(?:install|download)"
    r"|uv\s+(?:pip\s+install|add|sync|run\s+--with)"
    r"|apt(?:-get)?\s+(?:install|update)"
    r"|apk\s+add"
    r"|(?:npm|yarn|pnpm)\s+(?:install|add|ci)"
    r"|git\s+(?:clone|fetch|pull))"
    r"(?=$|[\s;&|)`'\"])"
)
_SHELL_COMMENT_RE: Final = re.compile(r"(?:^|\s)#.*$")


def network_module(qualified: str) -> str | None:
    """The network module ``qualified`` is, or lives in; None if it is not one."""
    parts = qualified.split(".")
    for end in range(1, len(parts) + 1):
        candidate = ".".join(parts[:end])
        if candidate in NETWORK_MODULES:
            return candidate
    return None


def network_command(text: str) -> re.Match[str] | None:
    """First network command in a shell string, if any."""
    return NETWORK_COMMAND_RE.search(text)


def _command_name(match: re.Match[str]) -> str:
    return " ".join(match.group("cmd").split())


def _string_items(node: ast.List | ast.Tuple) -> list[ast.Constant]:
    """String constants of a list or tuple display, in order (argv-style commands)."""
    return [
        elt for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    ]


def check_python(path: str, tree: ast.Module, lines: tuple[str, ...]) -> Iterator[Violation]:
    """Network imports and network commands in string literals."""
    for qualified, statement in ImportTable(tree).imports:
        module = network_module(qualified)
        if module is not None:
            message = f"grader imports network module '{module}'"
            yield violation_at(path, lines, statement, message)
    covered = docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.List | ast.Tuple) and (items := _string_items(node)):
            covered.update(id(item) for item in items)
            match = network_command(" ".join(str(item.value) for item in items))
            if match is not None:
                message = f"grader runs '{_command_name(match)}' at test time"
                yield violation_at(path, lines, node, message)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in covered
            and (match := network_command(node.value)) is not None
        ):
            yield violation_at(
                path, lines, node, f"grader runs '{_command_name(match)}' at test time"
            )


def check_shell(path: str, lines: tuple[str, ...]) -> Iterator[Violation]:
    """Network commands in a shell script, ignoring comments."""
    for number, line in enumerate(lines, start=1):
        code = _SHELL_COMMENT_RE.sub("", line)
        for match in NETWORK_COMMAND_RE.finditer(code):
            message = f"grader script runs '{_command_name(match)}' at test time"
            yield Violation(path, message, number, match.start("cmd") + 1)


def _exec_form(value: str) -> str:
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return " ".join(parsed)
    return value


@rule(
    "TL003",
    name="network-in-grader",
    severity=Severity.ERROR,
    description=(
        "The grader must not use the network at test time: no network-module imports "
        "(requests, httpx, urllib.request, socket, ...) and no curl, wget, pip install "
        "or similar in the tests, the verifier command or the verifier image's CMD."
    ),
    fix=(
        "Vendor the data the grader needs into the tests directory and install "
        "dependencies with RUN in the verifier Dockerfile (build time), not at test time."
    ),
)
def network_in_grader(ctx: LintContext) -> Iterator[Violation]:
    """Scan the grader for network use."""
    for path in ctx.files_under(ctx.layout.tests.path):
        if ctx.is_python(path):
            tree = ctx.python(path)
            if tree is not None:
                yield from check_python(path, tree, ctx.lines(path))
        elif ctx.is_shell(path):
            yield from check_shell(path, ctx.lines(path))

    command = ctx.layout.verifier.command
    match = network_command(" ".join(command))
    if match is not None:
        yield Violation(
            "task.toml",
            f"verifier command runs '{_command_name(match)}' at test time",
            ctx.manifest_line("verifier.command"),
        )

    dockerfile = ctx.layout.verifier.dockerfile
    text = ctx.text(dockerfile) if dockerfile in ctx.files else None
    if text is None:
        return
    for instruction in parse_dockerfile(text):
        if instruction.keyword in ("CMD", "ENTRYPOINT"):
            match = network_command(_exec_form(instruction.value))
            if match is not None:
                yield Violation(
                    dockerfile,
                    f"verifier {instruction.keyword} runs '{_command_name(match)}' when the "
                    "grader starts",
                    instruction.line,
                    instruction.column,
                )
