"""Package and API version helpers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

_FALLBACK_VERSION = "0.1.0"
# URL/API contract version (``/api/v1`` + the ``X-API-Version`` header). Lives
# here, not in ``backend.main``, so middleware modules can read it without a
# circular import; ``backend.main`` re-imports it for existing call sites.
API_VERSION = "v1"


def api_version() -> str:
    """Return the installed package version, with a source-tree fallback."""

    try:
        return version("mortgage-intelligence-platform")
    except PackageNotFoundError:
        return _FALLBACK_VERSION
