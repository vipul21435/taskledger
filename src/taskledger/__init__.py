"""TaskLedger: submission pipeline tooling for benchmark-task teams."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("taskledger")
except PackageNotFoundError:  # pragma: no cover - only when running from a raw checkout
    __version__ = "0.0.0"

__all__ = ["__version__"]
