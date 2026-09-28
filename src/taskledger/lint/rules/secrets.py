"""TL006: credentials must never be committed in a bundle.

Every text file in the bundle is scanned line by line (files too large for the
content cache are streamed, so size never hides a key). Two detectors run:

- **Known key formats**: provider tokens with a recognizable shape (cloud and
  code-hosting access keys, chat, payment and model-provider API keys, package
  registry tokens, JWTs, PEM private key blocks and passwords embedded in
  URLs such as ``postgres://user:pw@host``).
- **Shannon entropy**: a value of at least 16 characters, with letters and
  digits, assigned to a secret-looking name (``api_key = ...``,
  ``"token": ...``, ``PASSWORD=...``) whose character entropy is at least 3.5
  bits (3.0 for hex). Gating on the name keeps digests, base64 fixtures and
  expected outputs from tripping the check.

Documentation placeholders (``example``, ``changeme``, ``xxxx``, ``${VAR}``,
``<token>``, ...) are skipped. Findings are redacted: a known format shows its
public prefix (``AKIA``, ``ghp_``) and the length, a generic secret only its
length, so the lint report itself never leaks the credential.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from taskledger.lint.context import LintContext
from taskledger.lint.finding import Severity, Violation
from taskledger.lint.registry import rule

#: Shortest value the entropy detector considers.
MIN_SECRET_LENGTH: Final = 16
#: Minimum Shannon entropy, in bits per character, of a generic secret.
ENTROPY_THRESHOLD: Final = 3.5
#: Lower bar for hex values, whose alphabet has only 16 symbols.
HEX_ENTROPY_THRESHOLD: Final = 3.0
#: Leading characters of a known-format token shown in a finding.
KEPT_PREFIX: Final = 4

_HEX_RE: Final = re.compile(r"[0-9a-fA-F]+")
_PLACEHOLDER_RE: Final = re.compile(
    r"example|placeholder|changeme|change[_-]me|dummy|fake|sample|redacted|"
    r"your[_-]|xxxx|\*\*\*\*|\.\.\.|\$\{|\$\(|\{\{|%\(|<[^>]*>",
    re.IGNORECASE,
)
_SECRET_NAME_RE: Final = re.compile(
    r"secret|token|passw(?:or)?d|passwd|pwd|api[_.-]?key|access[_.-]?key|"
    r"auth[_.-]?key|private[_.-]?key|signing[_.-]?key|credential|bearer|session[_.-]?key",
    re.IGNORECASE,
)
_NOT_A_SECRET_NAME_RE: Final = re.compile(
    r"(?:^|[_.-])(?:file|path|dir|env|var|name|id|url|uri|endpoint|count|size|len|length|"
    r"limit|max|min|type|field|header|prefix|kind)s?$|^(?:max|min|num|n)[_.-]",
    re.IGNORECASE,
)
_ASSIGNMENT_RE: Final = re.compile(
    r"""(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)["']?"""
    r"""(?:\s*:\s*[A-Za-z_][A-Za-z0-9_.\[\], |]*?(?=\s*=[^=]))?"""  # annotation
    r"""\s*(?::=|=>|[:=])\s*"""
    r"""(?:[rRbBuU]{0,2}(?P<quote>["'`])(?P<quoted>[^"'`\s]+)(?P=quote)"""
    r"""|(?P<bare>[A-Za-z0-9+/=_.~-]+))"""
)


@dataclass(frozen=True, slots=True)
class SecretFormat:
    """A credential with a recognizable shape.

    When ``pattern`` has a ``secret`` group only that part is the credential
    (the password of a URL); otherwise the whole match is. ``redact=False``
    marks matches that are markers rather than secret material (PEM headers).
    """

    label: str
    pattern: re.Pattern[str]
    redact: bool = True


def _format(label: str, pattern: str, *, redact: bool = True) -> SecretFormat:
    return SecretFormat(label, re.compile(pattern), redact)


_B: Final = r"(?<![A-Za-z0-9_-])"  # token start boundary
_E: Final = r"(?![A-Za-z0-9_-])"  # token end boundary

KNOWN_FORMATS: Final = (
    _format("AWS access key ID", rf"{_B}(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{{16}}{_E}"),
    _format("GitHub token", rf"{_B}gh[pousr]_[A-Za-z0-9]{{36,255}}{_E}"),
    _format("GitHub fine-grained token", rf"{_B}github_pat_[A-Za-z0-9_]{{60,255}}{_E}"),
    _format("GitLab token", rf"{_B}glpat-[A-Za-z0-9_-]{{20,}}{_E}"),
    _format("Slack token", rf"{_B}xox[abposr]-[A-Za-z0-9-]{{10,}}{_E}"),
    _format(
        "Slack webhook URL",
        r"https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]{16,}",
    ),
    _format("Stripe live key", rf"{_B}(?:sk|rk)_live_[A-Za-z0-9]{{20,}}{_E}"),
    _format("Google API key", rf"{_B}AIza[0-9A-Za-z_-]{{35}}{_E}"),
    _format("Anthropic API key", rf"{_B}sk-ant-[A-Za-z0-9_-]{{32,}}{_E}"),
    _format(
        "OpenAI API key",
        rf"{_B}sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{{16,}}T3BlbkFJ[A-Za-z0-9_-]{{16,}}{_E}",
    ),
    _format("Hugging Face token", rf"{_B}hf_[A-Za-z0-9]{{34,}}{_E}"),
    _format("npm token", rf"{_B}npm_[A-Za-z0-9]{{36}}{_E}"),
    _format("PyPI token", rf"{_B}pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{{50,}}{_E}"),
    _format(
        "JSON web token",
        rf"{_B}eyJ[A-Za-z0-9_-]{{8,}}\.eyJ[A-Za-z0-9_-]{{8,}}\.[A-Za-z0-9_-]{{8,}}{_E}",
    ),
    _format(
        "private key block",
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----",
        redact=False,
    ),
    _format(
        "password in URL",
        r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@'\"]+:(?P<secret>[^\s/@'\"]+)@",
    ),
)


def shannon_entropy(value: str) -> float:
    """Shannon entropy of the characters of ``value`` in bits per character."""
    if not value:
        return 0.0
    total = len(value)
    return -sum(count / total * math.log2(count / total) for count in Counter(value).values())


def redact(value: str, *, keep: int = 0) -> str:
    """``value`` reduced to its first ``keep`` characters plus its total length."""
    return f"{value[:keep]}[redacted, {len(value)} chars]"


def is_placeholder(value: str) -> bool:
    """True for documentation stand-ins such as ``changeme`` or ``${TOKEN}``."""
    return _PLACEHOLDER_RE.search(value) is not None


def is_secret_name(name: str) -> bool:
    """True for names such as ``api_key`` or ``DB_PASSWORD`` but not ``token_path``."""
    return _SECRET_NAME_RE.search(name) is not None and _NOT_A_SECRET_NAME_RE.search(name) is None


def looks_random(value: str) -> bool:
    """Long enough, letters and digits, and high Shannon entropy."""
    if len(value) < MIN_SECRET_LENGTH:
        return False
    if not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
        return False
    threshold = HEX_ENTROPY_THRESHOLD if _HEX_RE.fullmatch(value) else ENTROPY_THRESHOLD
    return shannon_entropy(value) >= threshold


@dataclass(frozen=True, slots=True)
class SecretMatch:
    """A secret on one line: 0-based code point span and a redacted message."""

    start: int
    end: int
    message: str


def _known(line: str) -> Iterator[SecretMatch]:
    for fmt in KNOWN_FORMATS:
        for match in fmt.pattern.finditer(line):
            group = "secret" if "secret" in fmt.pattern.groupindex else 0
            secret = match.group(group)
            if is_placeholder(secret):
                continue
            start, end = match.span(group)
            if not fmt.redact:
                shown = secret
            else:
                shown = redact(secret, keep=KEPT_PREFIX if group == 0 else 0)
            yield SecretMatch(start, end, f"{fmt.label} committed in plain text: {shown}")


def _assigned(line: str, taken: list[tuple[int, int]]) -> Iterator[SecretMatch]:
    for match in _ASSIGNMENT_RE.finditer(line):
        group = "quoted" if match.group("quoted") is not None else "bare"
        value = match.group(group)
        start, end = match.span(group)
        if any(start < t_end and t_start < end for t_start, t_end in taken):
            continue
        name = match.group("name")
        if not is_secret_name(name) or is_placeholder(value) or not looks_random(value):
            continue
        entropy = shannon_entropy(value)
        yield SecretMatch(
            start,
            end,
            f"'{name}' is assigned a high-entropy value ({entropy:.1f} bits per character) "
            f"that looks like a secret: {redact(value)}",
        )


def find_secrets(line: str) -> list[SecretMatch]:
    """Every secret on one line, known formats first, ordered by position."""
    known = list(_known(line))
    taken = [(match.start, match.end) for match in known]
    found = known + list(_assigned(line, taken))
    return sorted(found, key=lambda match: (match.start, match.end))


@rule(
    "TL006",
    name="committed-secret",
    severity=Severity.ERROR,
    description=(
        "No credentials in the bundle: known key formats (cloud, code-hosting, chat, "
        "payment and model-provider keys, JWTs, private keys, passwords in URLs) and "
        "high-entropy values assigned to secret-looking names."
    ),
    fix=(
        "Delete the credential, rotate it (it is already exposed to everyone who saw the "
        "bundle), and read it from an environment variable or a build secret instead."
    ),
)
def committed_secret(ctx: LintContext) -> Iterator[Violation]:
    """Scan every text file in the bundle, line by line."""
    for path in ctx.files:
        for number, line in enumerate(ctx.stream_lines(path), start=1):
            for match in find_secrets(line):
                yield Violation(path, match.message, number, match.start + 1, number, match.end + 1)
