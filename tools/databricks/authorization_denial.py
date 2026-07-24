"""Fail-closed classification of Databricks authorization denials."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from databricks.sdk.errors import NotFound, PermissionDenied, ResourceDoesNotExist

_DENIAL_ERROR_CODES = frozenset(
    {
        "FORBIDDEN",
        "PERMISSION_DENIED",
    }
)
_DENIAL_TEXT_MARKERS = (
    "does not have permission",
    "forbidden",
    "insufficient privileges",
    "permission denied",
    "permission_denied",
)


def _field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _is_denial_status(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == 403
    if isinstance(value, str):
        return value.strip() == "403"
    return False


def _is_denial_code(value: object) -> bool:
    if value is None:
        return False
    normalized = str(getattr(value, "value", value)).strip().upper()
    return normalized in _DENIAL_ERROR_CODES


def is_authorization_denied(
    error: object,
    *,
    allow_hidden_resource: bool = False,
) -> bool:
    """Return true only for typed, structured, or semantic denial evidence.

    Authentication failures are deliberately not accepted: a 401 proves that
    the presented credential failed, not that an authenticated identity lacks
    the probed capability. Raw status numbers embedded in free-form text are
    likewise not proof of an authorization boundary.
    """

    if isinstance(error, PermissionDenied):
        return True
    if allow_hidden_resource and isinstance(error, NotFound | ResourceDoesNotExist):
        return True

    response = _field(error, "response")
    if any(
        _is_denial_status(value)
        for value in (
            _field(error, "status_code"),
            _field(error, "http_status_code"),
            _field(response, "status_code"),
        )
    ):
        return True
    if any(
        _is_denial_code(_field(error, name))
        for name in ("error_code", "code")
    ):
        return True

    message = str(error).casefold()
    return any(marker in message for marker in _DENIAL_TEXT_MARKERS)
