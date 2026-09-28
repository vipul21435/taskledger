"""Configuration from ``TASKLEDGER_*`` environment variables (see .env.example)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_SIZE = re.compile(r"(?P<number>\d+)\s*(?P<unit>[kmgt]?i?b?)?", re.IGNORECASE)
_UNITS = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved configuration; construct with :meth:`from_env`."""

    home: Path
    database_url: str

    @property
    def cache_dir(self) -> Path:
        """Root of the content-addressed dedupe cache."""
        return self.home / "cache"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Read ``TASKLEDGER_HOME`` and ``TASKLEDGER_DATABASE_URL``."""
        env = os.environ if environ is None else environ
        home = Path(env.get("TASKLEDGER_HOME") or ".taskledger")
        url = env.get("TASKLEDGER_DATABASE_URL") or sqlite_url(home / "ledger.db")
        return cls(home=home, database_url=url)


def sqlite_url(path: Path) -> str:
    """SQLite URL for ``path``, escaped so ``%``, ``?`` and ``@`` stay in the file name.

    SQLAlchemy percent-decodes the database part of a URL and treats ``?`` as
    the start of the query string, so the path is quoted before it is embedded.
    """
    return "sqlite:///" + quote(path.as_posix(), safe="/:~")


def parse_size(text: str) -> int:
    """Parse ``1048576``, ``512K``, ``100MB`` or ``2GiB`` (binary units) into bytes."""
    match = _SIZE.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"not a size: {text!r} (examples: 1048576, 512K, 100MB, 2GiB)")
    unit = (match.group("unit") or "").lower().rstrip("b").rstrip("i")
    return int(match.group("number")) * _UNITS[unit]
