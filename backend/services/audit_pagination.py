"""Opaque, filter-bound cursors for stable audit-ledger traversal."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_AUDIT_CURSOR_ID_PATTERN = re.compile(
    r"^(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|evt-[0-9a-f]{12})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditPageCursor:
    """Total-order boundaries carried between audit pages."""

    after_at: datetime
    after_id: str
    snapshot_at: datetime
    snapshot_id: str


def audit_filter_fingerprint(filters: dict[str, Any]) -> str:
    """Bind a cursor to the exact reviewed filter set that created it."""

    normalized = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in sorted(filters.items())
        if value is not None and value != ""
    }
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def encode_audit_cursor(
    *,
    after_at: datetime,
    after_id: str,
    snapshot_at: datetime,
    snapshot_id: str,
    filter_fingerprint: str,
) -> str:
    """Encode one bounded cursor without exposing SQL or filter values."""

    payload = {
        "v": 1,
        "a": after_at.isoformat(),
        "i": after_id,
        "s": snapshot_at.isoformat(),
        "j": snapshot_id,
        "f": filter_fingerprint,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_audit_cursor(value: str, *, filter_fingerprint: str) -> AuditPageCursor:
    """Decode and validate an audit cursor, failing closed on any mismatch."""

    if not value or len(value) > 2048:
        raise ValueError("invalid audit cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(
            base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        )
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError
        if payload.get("f") != filter_fingerprint:
            raise ValueError
        after_at = datetime.fromisoformat(str(payload["a"]).replace("Z", "+00:00"))
        snapshot_at = datetime.fromisoformat(str(payload["s"]).replace("Z", "+00:00"))
        after_id = str(payload["i"])
        snapshot_id = str(payload["j"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid audit cursor") from exc
    if (
        after_at.tzinfo is None
        or snapshot_at.tzinfo is None
        or not after_id
        or not snapshot_id
        or len(after_id) > 128
        or len(snapshot_id) > 128
        or _AUDIT_CURSOR_ID_PATTERN.fullmatch(after_id) is None
        or _AUDIT_CURSOR_ID_PATTERN.fullmatch(snapshot_id) is None
    ):
        raise ValueError("invalid audit cursor")
    return AuditPageCursor(
        after_at=after_at,
        after_id=after_id,
        snapshot_at=snapshot_at,
        snapshot_id=snapshot_id,
    )
