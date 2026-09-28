"""TL006 committed-secret.

Every credential below is assembled at run time from harmless pieces, so this
file never contains a literal that a secret scanner would (rightly) flag.
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import LINT_CLEAN_FILES, BundleFactory, lint_findings
from taskledger.lint import build_context, format_json, format_text, lint_bundle
from taskledger.lint.context import MAX_SCAN_BYTES
from taskledger.lint.rules.secrets import (
    ENTROPY_THRESHOLD,
    find_secrets,
    is_placeholder,
    is_secret_name,
    looks_random,
    redact,
    shannon_entropy,
)

ALNUM = string.ascii_letters + string.digits


def rand(length: int, alphabet: str = ALNUM, seed: int = 7) -> str:
    """A reproducible random string (a stand-in credential)."""
    rng = random.Random(seed)  # noqa: S311  (reproducible fixtures, not crypto)
    return "".join(rng.choice(alphabet) for _ in range(length))


UPPER_DIGITS = string.ascii_uppercase + string.digits
HEX = "0123456789abcdef"

#: (label, token) pairs in every known format; the scanner must catch each one.
KNOWN = [
    ("AWS access key ID", "AK" + "IA" + rand(16, UPPER_DIGITS)),
    ("GitHub token", "gh" + "p_" + rand(36)),
    ("GitHub fine-grained token", "github" + "_pat_" + rand(82, ALNUM + "_")),
    ("GitLab token", "gl" + "pat-" + rand(20)),
    ("Slack token", "xo" + "xb-" + rand(12, string.digits) + "-" + rand(24)),
    ("Stripe live key", "sk" + "_live_" + rand(24)),
    ("Google API key", "AI" + "za" + rand(35)),
    ("Anthropic API key", "sk" + "-ant-" + "api03-" + rand(40)),
    ("OpenAI API key", "sk" + "-proj-" + rand(20) + "T3Blbk" + "FJ" + rand(20)),
    ("Hugging Face token", "h" + "f_" + rand(34)),
    ("npm token", "np" + "m_" + rand(36)),
    ("PyPI token", "py" + "pi-AgEIcHlwaS5vcmc" + rand(60)),
    ("JSON web token", "ey" + "J" + rand(12) + ".ey" + "J" + rand(20) + "." + rand(24)),
]


def secret_file(factory: BundleFactory, content: str, name: str = "solution/config.py") -> list:
    return lint_findings(factory, "TL006", {name: content})


# -- helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "bits"),
    [("", 0.0), ("aaaa", 0.0), ("ab", 1.0), ("abcd", 2.0), ("aabb", 1.0), (HEX, 4.0)],
)
def test_shannon_entropy(value: str, bits: float) -> None:
    assert shannon_entropy(value) == pytest.approx(bits)


def test_redact_keeps_only_the_prefix_and_length() -> None:
    assert redact("abcdefgh") == "[redacted, 8 chars]"
    assert redact("ghp_" + "x" * 36, keep=4) == "ghp_[redacted, 40 chars]"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("api_key", True),
        ("API-KEY", True),
        ("apiKey", True),
        ("DB_PASSWORD", True),
        ("client_secret", True),
        ("access_token", True),
        ("session.key", True),
        ("token_path", False),
        ("SECRET_FILE", False),
        ("password_env", False),
        ("max_tokens", False),
        ("checksum", False),
        ("expected_digest", False),
    ],
)
def test_secret_names(name: str, expected: bool) -> None:
    assert is_secret_name(name) is expected


@pytest.mark.parametrize(
    "value",
    [
        "changeme-" + rand(12),
        "your-api-key-here-123",
        "${API_TOKEN}",
        "$(cat /run/secret)",
        "{{ secrets.TOKEN }}",
        "<token>",
        "xxxxxxxxxxxxxxxx1",
        "AKIA" + "IOSFODNN7" + "EXAMPLE",
    ],
)
def test_placeholders(value: str) -> None:
    assert is_placeholder(value)


def test_looks_random() -> None:
    assert looks_random(rand(24))
    assert looks_random(rand(40, HEX))  # hex has its own, lower threshold
    assert not looks_random(rand(15))  # too short
    assert not looks_random(rand(24, string.ascii_letters))  # no digit
    assert not looks_random(rand(24, string.digits))  # no letter
    assert not looks_random("aaaaaaaaaaaaaaaa1")  # low entropy
    assert shannon_entropy("aaaaaaaaaaaaaaaa1") < ENTROPY_THRESHOLD


# -- known formats -------------------------------------------------------------


@pytest.mark.parametrize(("label", "token"), KNOWN, ids=[label for label, _ in KNOWN])
def test_known_formats_are_found_and_redacted(
    make_bundle: BundleFactory, label: str, token: str
) -> None:
    content = f"# deploy settings\nUPLOAD = connect(credential='{token}')\n"
    findings = secret_file(make_bundle, content)
    assert len(findings) == 1
    (finding,) = findings
    start = content.splitlines()[1].index(token) + 1
    assert (finding.file, finding.line, finding.column) == ("solution/config.py", 2, start)
    assert (finding.end_line, finding.end_column) == (2, start + len(token))
    assert finding.message == (
        f"{label} committed in plain text: {token[:4]}[redacted, {len(token)} chars]"
    )
    assert token not in finding.message
    assert finding.severity == "error"


def test_known_format_boundaries(make_bundle: BundleFactory) -> None:
    aws = "AK" + "IA" + rand(16, UPPER_DIGITS)
    content = f"x{aws}\n{aws}9\nid = '{aws}'\n"
    findings = secret_file(make_bundle, content)
    assert [(f.line, f.column) for f in findings] == [(3, 7)]


def test_private_key_header_is_shown_but_not_the_key(make_bundle: BundleFactory) -> None:
    body = rand(64, ALNUM + "+/")
    content = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n"
    findings = secret_file(make_bundle, content, "verifier/deploy.pem")
    assert [(f.line, f.column, f.message) for f in findings] == [
        (1, 1, "private key block committed in plain text: -----BEGIN RSA PRIVATE KEY-----")
    ]
    public = "-----BEGIN PUBLIC KEY-----\n" + body + "\n"
    assert secret_file(make_bundle, public, "verifier/deploy.pub") == []


def test_password_in_url_is_fully_redacted(make_bundle: BundleFactory) -> None:
    password = "hunter" + "2x"
    content = (
        f'DSN = "postgres://grader:{password}@db:5432/tasks"\n'
        'SAFE = "postgres://grader:${DB_PASSWORD}@db:5432/tasks"\n'
        'SCP = "git@example.org:team/repo.git"\n'
    )
    findings = secret_file(make_bundle, content)
    assert [(f.line, f.column, f.end_column, f.message) for f in findings] == [
        (1, 26, 34, "password in URL committed in plain text: [redacted, 8 chars]")
    ]


def test_documentation_placeholders_are_skipped(make_bundle: BundleFactory) -> None:
    content = "AWS_ACCESS_KEY_ID = '" + "AKIA" + "IOSFODNN7" + "EXAMPLE'\n"
    assert secret_file(make_bundle, content) == []


# -- entropy check -------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ('api_key = "{v}"', "api_key"),
        ("API_TOKEN='{v}'", "API_TOKEN"),
        ('token: str = "{v}"', "token"),
        ('config = {{"client_secret": "{v}"}}', "client_secret"),
        ("export DB_PASSWORD={v}", "DB_PASSWORD"),
        ("auth_key: {v}", "auth_key"),
        ("session_key := {v}", "session_key"),
        ('call(password="{v}")', "password"),
    ],
)
def test_high_entropy_values_under_secret_names(
    make_bundle: BundleFactory, line: str, name: str
) -> None:
    value = rand(28)
    text = line.format(v=value)
    findings = secret_file(make_bundle, text + "\n", "tests/settings.txt")
    assert len(findings) == 1
    (finding,) = findings
    assert finding.column == text.index(value) + 1
    assert finding.end_column == text.index(value) + 1 + len(value)
    entropy = shannon_entropy(value)
    assert finding.message == (
        f"'{name}' is assigned a high-entropy value ({entropy:.1f} bits per character) "
        "that looks like a secret: [redacted, 28 chars]"
    )


@pytest.mark.parametrize(
    "line",
    [
        f'checksum = "{rand(28)}"',  # not a secret name
        f'token_path = "{rand(28)}"',  # a secret-ish name that is a path
        'api_key = "short1"',
        'api_key = "aaaaaaaaaaaaaaaaaaa1"',  # low entropy
        'api_key = "your-api-key-goes-here-1"',
        'api_key = os.environ["API_KEY"]',
        "password = get_password_from_vault()",
        'if token == "":',
        f'expected_sha256 = "{rand(64, HEX)}"',
    ],
)
def test_entropy_check_false_positive_guards(make_bundle: BundleFactory, line: str) -> None:
    assert secret_file(make_bundle, line + "\n") == []


def test_hex_secret_uses_the_hex_threshold(make_bundle: BundleFactory) -> None:
    value = rand(40, HEX)
    assert shannon_entropy(value) < ENTROPY_THRESHOLD
    findings = secret_file(make_bundle, f"secret = '{value}'\n")
    assert [f.line for f in findings] == [1]


def test_known_format_under_a_secret_name_is_reported_once(make_bundle: BundleFactory) -> None:
    token = "gh" + "p_" + rand(36)
    findings = secret_file(make_bundle, f'api_token = "{token}"\n')
    assert [f.message.split(" committed")[0] for f in findings] == ["GitHub token"]


def test_several_secrets_on_one_line_are_ordered(make_bundle: BundleFactory) -> None:
    first, second = rand(20, seed=1), rand(20, seed=2)
    line = f"api_key={first} password={second}"
    matches = find_secrets(line)
    assert [(m.start, m.end) for m in matches] == [(8, 28), (38, 58)]


# -- files ---------------------------------------------------------------------


def test_every_text_file_is_scanned(make_bundle: BundleFactory) -> None:
    token = "gl" + "pat-" + rand(20)
    files = {
        "task.toml.bak": f"token = '{token}'\n",
        "instruction.md": f"Use the key {token} to call the service.\n",
        "environment/Dockerfile": f"FROM {'python:3.12-slim@sha256:' + '4f' * 32}\n"
        f"ENV TOKEN={token}\n",
    }
    findings = lint_findings(make_bundle, "TL006", files)
    assert [(f.file, f.line) for f in findings] == [
        ("environment/Dockerfile", 2),
        ("instruction.md", 1),
        ("task.toml.bak", 1),
    ]


def test_binary_files_are_skipped(make_bundle: BundleFactory) -> None:
    token = "gh" + "p_" + rand(36)
    blob = b"\x00\x01" + token.encode() + b"\x00"
    assert lint_findings(make_bundle, "TL006", {"baseline/blob.bin": blob}) == []


def test_large_files_are_streamed_not_skipped(make_bundle: BundleFactory) -> None:
    token = "hf" + "_" + rand(34)
    filler = "0123456789abcdef" * 4 + "\r\n"  # 66 bytes per line, CRLF endings
    lines = MAX_SCAN_BYTES // len(filler) + 10
    content = filler * lines + f"HF = '{token}'\n"
    bundle = make_bundle(files={**LINT_CLEAN_FILES, "baseline/big.txt": content})
    report = lint_bundle(bundle, select=["TL006"])
    assert [(f.file, f.line, f.column) for f in report.findings] == [
        ("baseline/big.txt", lines + 1, 7)
    ]


def test_large_binary_file_is_skipped(make_bundle: BundleFactory) -> None:
    bundle = make_bundle(files=LINT_CLEAN_FILES)
    with (bundle / "baseline.bin").open("wb") as handle:
        handle.truncate(MAX_SCAN_BYTES + 1)  # sparse NUL bytes
    assert lint_bundle(bundle, select=["TL006"]).findings == ()


def test_unreadable_files_are_skipped(
    make_bundle: BundleFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(files=LINT_CLEAN_FILES)
    big = bundle / "baseline" / "big.txt"
    big.parent.mkdir()
    big.write_text("x\n" * (MAX_SCAN_BYTES // 2 + 1))
    context = build_context(bundle)
    real_open = Path.open

    def failing_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == big:
            raise PermissionError(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    assert list(context.stream_lines("baseline/big.txt")) == []
    assert list(context.stream_lines("missing.txt")) == []


def test_inline_suppression(make_bundle: BundleFactory) -> None:
    value = rand(24)
    content = f'api_key = "{value}"  # taskledger: ignore[TL006]\n'
    bundle = make_bundle(files={**LINT_CLEAN_FILES, "tests/fixture.py": content})
    report = lint_bundle(bundle, select=["TL006"])
    assert (report.findings, report.suppressed) == ((), 1)


def test_reports_never_contain_the_secret(make_bundle: BundleFactory) -> None:
    tokens = [token for _, token in KNOWN] + [rand(30, seed=11)]
    content = "".join(f"credential_{i} = '{t}'\n" for i, t in enumerate(tokens))
    bundle = make_bundle(files={**LINT_CLEAN_FILES, "solution/keys.py": content})
    report = lint_bundle(bundle, select=["TL006"])
    assert len(report.findings) == len(tokens)
    rendered = format_text([report]) + format_json([report])
    json.loads(format_json([report]))
    for token in tokens:
        assert token[4:] not in rendered


@given(st.text(alphabet=ALNUM, min_size=20, max_size=60))
def test_generic_findings_never_echo_the_value(value: str) -> None:
    for match in find_secrets(f'api_key = "{value}"'):
        assert value not in match.message
        assert value[:4] not in match.message.split("secret: ")[-1]
