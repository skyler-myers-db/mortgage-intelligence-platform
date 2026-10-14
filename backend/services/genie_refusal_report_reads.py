"""Administrator and auditor reads of Genie refusal reports (D-audit-reads-d).

Two reads, both Lakebase-only (``mip_app``) and both behind
``require_audit_reader`` in ``backend/api/refusal_reports_admin.py``:

- a keyset page of reports, newest first, with per-family counts for the
  window. Report metadata only: whether a live question text exists and when
  it expires, never the text;
- one report's consented question text, which the router returns only after
  its own fail-closed ``VIEW_REFUSAL_REPORT_TEXT`` audit row is written.

Both statements filter ``purged_at IS NULL AND expires_at > now()`` in the
same SELECT that yields ``has_text`` or the text, so an expired question is
never served even before the sweep nulls it. The page cursor is an opaque,
HMAC-signed keyset over (reported_at DESC, report_id DESC), bound to the
caller's filters and keyed like the audit-ledger cursor (a domain-separated
SHA-256 of the Genie action secret; a process-random key under local and
test APP_ENV). Without a secret elsewhere there is no next page.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.config.settings import settings
from backend.services.genie_refusal_report import sweep_expired_refusal_texts
from backend.services.observability import emit

log = logging.getLogger(__name__)

_CURSOR_DOMAIN = b"mip.refusal_reports.cursor.v1\x00"
_LOCAL_CURSOR_SECRET = secrets.token_bytes(32)
_LOCAL_APP_ENVS = frozenset({"local", "test", "testing", "pytest"})
MAX_CURSOR_LENGTH = 2048

_PAGE_SQL = """
SELECT r.report_id::text AS report_id,
       r.reported_at,
       r.refusal_reason,
       r.actor_email,
       r.conversation_id,
       r.message_id,
       r.audit_event_id::text AS audit_event_id,
       (t.report_id IS NOT NULL AND t.purged_at IS NULL AND t.expires_at > now()) AS has_text,
       CASE WHEN t.purged_at IS NULL AND t.expires_at > now() THEN t.expires_at END
           AS text_expires_at
FROM mip_app.genie_refusal_reports r
LEFT JOIN mip_app.genie_refusal_report_texts t ON t.report_id = r.report_id
WHERE r.reported_at >= %(since)s
  AND (%(family)s::text IS NULL OR r.refusal_reason = %(family)s::text)
  AND (
      %(after_reported_at)s::timestamptz IS NULL
      OR (r.reported_at, r.report_id) < (%(after_reported_at)s::timestamptz, %(after_report_id)s::uuid)
  )
ORDER BY r.reported_at DESC, r.report_id DESC
LIMIT %(fetch_limit)s
"""

_FAMILY_COUNTS_SQL = """
SELECT refusal_reason, count(*) AS report_count
FROM mip_app.genie_refusal_reports
WHERE reported_at >= %(since)s
GROUP BY refusal_reason
ORDER BY refusal_reason
"""

_QUESTION_SQL = """
SELECT t.question_text, t.redacted, t.captured_at, t.expires_at,
       r.question_hash, r.refusal_reason
FROM mip_app.genie_refusal_report_texts t
JOIN mip_app.genie_refusal_reports r ON r.report_id = t.report_id
WHERE t.report_id = %(report_id)s::uuid
  AND t.purged_at IS NULL
  AND t.expires_at > now()
"""


class InvalidRefusalReportCursor(ValueError):
    """A cursor that is malformed, unsigned, tampered or for other filters."""


@dataclass(frozen=True, slots=True)
class RefusalReportPage:
    rows: list[dict[str, Any]]
    family_counts: list[dict[str, Any]]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _Keyset:
    since: datetime
    reported_at: datetime
    report_id: str


def _cursor_secret() -> bytes | None:
    configured = settings.mip_genie_action_secret_current or settings.mip_genie_action_secret
    if configured is not None:
        value = configured.get_secret_value().strip()
        if value:
            return hashlib.sha256(_CURSOR_DOMAIN + value.encode("utf-8")).digest()
    if (settings.app_env or "").strip().lower() in _LOCAL_APP_ENVS:
        return _LOCAL_CURSOR_SECRET
    return None


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.b64decode((value + "=" * (-len(value) % 4)).encode("ascii"), altchars=b"-_", validate=True)


def encode_refusal_report_cursor(keyset: _Keyset, *, filter_fingerprint: str) -> str | None:
    """Sign the next page's keyset, or None when no cursor key exists."""

    secret = _cursor_secret()
    if secret is None:
        return None
    raw = json.dumps(
        {
            "v": 1,
            "s": keyset.since.isoformat(),
            "t": keyset.reported_at.isoformat(),
            "r": keyset.report_id,
            "f": filter_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(secret, _CURSOR_DOMAIN + raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(signature)}"


def decode_refusal_report_cursor(value: str, *, filter_fingerprint: str) -> _Keyset:
    """Verify and decode a cursor, failing closed on any mismatch."""

    secret = _cursor_secret()
    if secret is None or not value or len(value) > MAX_CURSOR_LENGTH:
        raise InvalidRefusalReportCursor("invalid refusal report cursor")
    try:
        encoded, encoded_signature = value.split(".", maxsplit=1)
        raw = _unb64(encoded)
        expected = hmac.new(secret, _CURSOR_DOMAIN + raw, hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(encoded_signature), expected):
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError
        if payload.get("f") != filter_fingerprint:
            raise ValueError
        keyset = _Keyset(
            since=datetime.fromisoformat(str(payload["s"])),
            reported_at=datetime.fromisoformat(str(payload["t"])),
            report_id=str(UUID(str(payload["r"]))),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidRefusalReportCursor("invalid refusal report cursor") from exc
    return keyset


def sweep_refusal_texts_fail_open(lakebase: Any) -> None:
    """Read-path retention: purge a batch, never fail or audit the read.

    Run first, in its own short transaction, by the auditor reads (W5c C12):
    a failing sweep is logged with counts only and never fails the list or
    the question read, which filter expired rows themselves. ``SKIP LOCKED``
    keeps it from waiting on a concurrent sweep. Never text.
    """

    try:
        with lakebase.transaction() as conn:
            purged = sweep_expired_refusal_texts(conn)
    except Exception as exc:  # noqa: BLE001 - system retention is best effort here
        emit(
            log,
            "refusal_text_sweep_failed",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="error",
            error_type=type(exc).__name__,
        )
        return
    if purged:
        emit(log, "refusal_text_purged", outcome="purged", purged_count=purged)


def read_refusal_report_page(
    lakebase: Any,
    *,
    since: datetime,
    family: str | None,
    limit: int,
    cursor: str | None,
    filter_fingerprint: str,
) -> RefusalReportPage:
    """One newest-first page; ``since`` comes from the cursor on later pages."""

    keyset = (
        decode_refusal_report_cursor(cursor, filter_fingerprint=filter_fingerprint)
        if cursor is not None
        else None
    )
    window_start = keyset.since if keyset is not None else since
    rows = lakebase.fetchall(
        _PAGE_SQL,
        {
            "since": window_start,
            "family": family,
            "after_reported_at": keyset.reported_at if keyset is not None else None,
            "after_report_id": keyset.report_id if keyset is not None else None,
            "fetch_limit": limit + 1,
        },
        limit=limit + 1,
    )
    counts = lakebase.fetchall(_FAMILY_COUNTS_SQL, {"since": window_start}, limit=50)
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_refusal_report_cursor(
            _Keyset(
                since=window_start,
                reported_at=last["reported_at"],
                report_id=str(last["report_id"]),
            ),
            filter_fingerprint=filter_fingerprint,
        )
    return RefusalReportPage(rows=page, family_counts=counts, next_cursor=next_cursor)


def read_refusal_question(lakebase: Any, report_id: str) -> dict[str, Any] | None:
    """The live (unpurged, unexpired) question text of one report, or None."""

    return lakebase.fetchone(_QUESTION_SQL, {"report_id": report_id})
