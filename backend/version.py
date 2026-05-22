"""Package and API version helpers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

_FALLBACK_VERSION = "0.1.0"


def api_version() -> str:
    """Return the installed package version, with a source-tree fallback."""

    try:
        return version("mortgage-intelligence-platform")
    except PackageNotFoundError:
        return _FALLBACK_VERSION
