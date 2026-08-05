"""Opaque, filter-bound cursors for stable audit-ledger traversal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

from backend.config.settings import settings

_CURSOR_HMAC_DOMAIN = b"mip.audit.cursor.v2\x00"
_LOCAL_CURSOR_SECRET = secrets.token_bytes(32)
_LOCAL_APP_ENVS = frozenset({"local", "test", "testing", "pytest"})
_SNAPSHOT_PATTERN = re.compile(r"^(?:[0-9]+:[0-9]+:[0-9,]*|memory:[0-9]+)$")


@dataclass(frozen=True)
class AuditPageCursor:
    """Total-order boundaries carried between audit pages."""

    after_sequence: int
    snapshot_sequence: int
    snapshot_token: str


def audit_filter_fingerprint(filters: dict[str, Any]) -> str:
    """Bind a cursor to the exact reviewed filter set that created it."""

    normalized = {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in sorted(filters.items())
        if value is not None and value != ""
    }
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cursor_secret() -> bytes:
    """Return a domain-specific server secret for cursor authentication."""

    configured = settings.mip_genie_action_secret_current or settings.mip_genie_action_secret
    if configured is not None:
        value = configured.get_secret_value().strip()
        if value:
            return hashlib.sha256(_CURSOR_HMAC_DOMAIN + value.encode("utf-8")).digest()
    app_env = (settings.app_env or "").strip().lower()
    if app_env not in _LOCAL_APP_ENVS:
        raise RuntimeError("MIP_GENIE_ACTION_SECRET_CURRENT is required for audit pagination")
    return _LOCAL_CURSOR_SECRET


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)


def encode_audit_cursor(
    *,
    after_sequence: int,
    snapshot_sequence: int,
    snapshot_token: str,
    filter_fingerprint: str,
) -> str:
    """Encode one bounded cursor without exposing SQL or filter values."""

    payload = {
        "v": 2,
        "a": after_sequence,
        "s": snapshot_sequence,
        "t": snapshot_token,
        "f": filter_fingerprint,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(_cursor_secret(), _CURSOR_HMAC_DOMAIN + raw, hashlib.sha256).digest()
    return f"{_b64encode(raw)}.{_b64encode(signature)}"


def decode_audit_cursor(value: str, *, filter_fingerprint: str) -> AuditPageCursor:
    """Decode and validate an audit cursor, failing closed on any mismatch."""

    if not value or len(value) > 2048:
        raise ValueError("invalid audit cursor")
    try:
        encoded_payload, encoded_signature = value.split(".", maxsplit=1)
        raw = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
        expected = hmac.new(_cursor_secret(), _CURSOR_HMAC_DOMAIN + raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("v") != 2:
            raise ValueError
        if payload.get("f") != filter_fingerprint:
            raise ValueError
        after_sequence = int(payload["a"])
        snapshot_sequence = int(payload["s"])
        snapshot_token = str(payload["t"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid audit cursor") from exc
    if (
        after_sequence < 1
        or snapshot_sequence < 1
        or after_sequence > snapshot_sequence
        or len(snapshot_token) > 256
        or _SNAPSHOT_PATTERN.fullmatch(snapshot_token) is None
    ):
        raise ValueError("invalid audit cursor")
    return AuditPageCursor(
        after_sequence=after_sequence,
        snapshot_sequence=snapshot_sequence,
        snapshot_token=snapshot_token,
    )
