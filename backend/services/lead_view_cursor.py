"""Signed Lead Queue view cursors (D-audit-reads-a), modelled on audit_pagination.

A Lead Queue view is page 0 plus pages loaded one explicit "Load next" at a
time. Page 0 mints a ``view_id``; each served page whose rows continue carries
an opaque ``X-Next-Cursor`` binding, under an HMAC, everything the next page
must not change:

* ``view`` / ``page`` -- the view and the page index the cursor requests;
* ``fp`` -- the keyed fingerprint of the request-level filters and sort
  (``lead_view_filter_fingerprint``), so a cursor used with other filters is
  refused;
* ``refreshed`` / ``total`` -- page 0's gold refresh stamp and total, so one
  view never mixes snapshots and later pages echo page 0's total;
* ``handoff`` -- page 0's verified Growth Agent provenance (or null);
* ``after`` -- the keyset tuple of the last row delivered;
* ``iat`` / ``exp`` -- one hour, never past the handoff's own expiry.

The MAC also binds the lower-cased actor; the readable payload carries no
actor, email or email hash, and no cursor is ever logged. A refusal raises
``LeadCursorRejected`` with a closed ``reason``; a deployment with no secret
outside local/test raises ``LeadCursorUnavailable`` (never a bare
RuntimeError), and page 0 is then served unpaged.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import SecretStr

from backend.config.settings import settings
from backend.schemas.lead_query import LEAD_MAX_PAGE_INDEX, LeadQueryParams
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.audit_fingerprint import (
    LEAD_VIEW_FINGERPRINT_DOMAIN,
    keyed_filter_fingerprint,
    keyed_filter_fingerprint_candidates,
)
from backend.services.repositories.databricks_lead_order import LeadKeyset, LeadOrder

_CURSOR_DOMAIN = b"mip.leads.cursor.v1\x00"
_LOCAL_CURSOR_KEY = secrets.token_bytes(32)
_LOCAL_APP_ENVS = frozenset({"local", "test", "testing", "pytest"})
_VIEW_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_CURSOR_CHARS = 2048
CURSOR_TTL_S = 3600

LeadCursorRejectReason = Literal["signature", "filters", "expired", "page_cap", "malformed"]

# The Growth Agent provenance a page past 0 echoes into its VIEW_LEADS row.
HANDOFF_PROVENANCE_KEYS: tuple[str, ...] = (
    "growth_agent_run_id",
    "growth_agent_filters_fingerprint",
    "growth_agent_cohort_fingerprint",
    "growth_agent_source_snapshot",
    "tool_result_hash",
)


class LeadCursorUnavailable(RuntimeError):
    """No cursor key is configured outside local/test: the view is unpaged."""


class LeadCursorRejected(ValueError):
    """A cursor that must not be served; ``reason`` is the only thing logged."""

    def __init__(self, reason: LeadCursorRejectReason) -> None:
        super().__init__(reason)
        self.reason: LeadCursorRejectReason = reason


@dataclass(frozen=True)
class LeadViewCursor:
    view_id: str
    page: int
    fp: str
    refreshed: str | None
    total: int
    handoff: dict[str, str] | None
    after: LeadKeyset
    iat: int
    exp: int


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value().strip()
    return text or None


def _keys() -> list[bytes]:
    """Signing key first (current, then the legacy secret), then the previous one."""

    current = _secret(settings.mip_genie_action_secret_current) or _secret(settings.mip_genie_action_secret)
    previous = _secret(settings.mip_genie_action_secret_previous)
    keys = [hashlib.sha256(_CURSOR_DOMAIN + value.encode("utf-8")).digest() for value in (current, previous) if value]
    if current is None:
        if (settings.app_env or "").strip().lower() in _LOCAL_APP_ENVS:
            return [_LOCAL_CURSOR_KEY]
        raise LeadCursorUnavailable("lead view cursors need MIP_GENIE_ACTION_SECRET_CURRENT")
    return keys


def _mac(key: bytes, actor: str, raw: bytes) -> bytes:
    return hmac.new(key, _CURSOR_DOMAIN + actor.strip().lower().encode("utf-8") + b"\x00" + raw, hashlib.sha256).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.b64decode((value + "=" * (-len(value) % 4)).encode("ascii"), altchars=b"-_", validate=True)


def encode_lead_cursor(
    *,
    actor: str,
    view_id: str,
    page: int,
    fp: str,
    refreshed: str | None,
    total: int,
    handoff: Mapping[str, str] | None,
    handoff_expires_at: int | None,
    after: LeadKeyset,
    now: int | None = None,
) -> str:
    """Sign the cursor for page ``page`` of view ``view_id``."""

    iat = int(time.time()) if now is None else int(now)
    exp = iat + CURSOR_TTL_S
    if handoff_expires_at is not None:
        exp = min(exp, int(handoff_expires_at))
    payload = {
        "v": 1,
        "view": view_id,
        "page": page,
        "fp": fp,
        "refreshed": refreshed,
        "total": total,
        "iat": iat,
        "exp": exp,
        "handoff": dict(handoff) if handoff is not None else None,
        "after": list(after),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{_b64encode(raw)}.{_b64encode(_mac(_keys()[0], actor, raw))}"


def _payload(value: str, actor: str) -> dict[str, Any]:
    if not value or len(value) > _MAX_CURSOR_CHARS or value.count(".") != 1:
        raise LeadCursorRejected("malformed")
    encoded_payload, encoded_signature = value.split(".", 1)
    try:
        raw = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
    except (ValueError, binascii.Error) as exc:
        raise LeadCursorRejected("malformed") from exc
    if not any(hmac.compare_digest(signature, _mac(key, actor, raw)) for key in _keys()):
        raise LeadCursorRejected("signature")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LeadCursorRejected("malformed") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise LeadCursorRejected("malformed")
    return payload


def _keyset(value: object) -> LeadKeyset:
    if not isinstance(value, list) or len(value) not in (2, 4):
        raise LeadCursorRejected("malformed")
    for item in value:
        if item is not None and (isinstance(item, bool) or not isinstance(item, int | float | str)):
            raise LeadCursorRejected("malformed")
    return tuple(value)


def _handoff(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(HANDOFF_PROVENANCE_KEYS):
        raise LeadCursorRejected("malformed")
    if not all(isinstance(item, str) for item in value.values()):
        raise LeadCursorRejected("malformed")
    return dict(value)


def decode_lead_cursor(
    value: str,
    *,
    actor: str,
    fp_candidates: Sequence[str],
    now: int | None = None,
) -> LeadViewCursor:
    """Verify and decode a cursor; refuse it with one closed reason."""

    payload = _payload(value, actor)
    try:
        cursor = LeadViewCursor(
            view_id=str(payload["view"]),
            page=int(payload["page"]),
            fp=str(payload["fp"]),
            refreshed=None if payload["refreshed"] is None else str(payload["refreshed"]),
            total=int(payload["total"]),
            handoff=_handoff(payload["handoff"]),
            after=_keyset(payload["after"]),
            iat=int(payload["iat"]),
            exp=int(payload["exp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, LeadCursorRejected):
            raise
        raise LeadCursorRejected("malformed") from exc
    if not _VIEW_ID.fullmatch(cursor.view_id) or cursor.page < 1 or cursor.total < 0:
        raise LeadCursorRejected("malformed")
    if not any(hmac.compare_digest(cursor.fp, candidate) for candidate in fp_candidates):
        raise LeadCursorRejected("filters")
    current = int(time.time()) if now is None else int(now)
    if cursor.exp < current:
        raise LeadCursorRejected("expired")
    if cursor.page > LEAD_MAX_PAGE_INDEX:
        raise LeadCursorRejected("page_cap")
    return cursor


def _sorted_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = sorted({item.strip() for item in value.split(",") if item.strip()})
    return items or None


def _city_pairs(value: str | None) -> list[list[str]] | None:
    pairs = _sorted_csv(value)
    if pairs is None:
        return None
    return sorted([[part.strip().upper() for part in pair.split("~", 1)] for pair in pairs])


_CSV_FIELDS = ("segment_codes", "states", "zips", "counties", "borrower_ids")


def lead_view_filter_digest(
    params: LeadQueryParams,
    *,
    portfolio_criteria: PortfolioCriteria | None,
    order: LeadOrder,
    growth_handoff: str | None,
    approval_request_batch: str | None,
) -> str:
    """The PLAIN sha256 over the request-level inputs of one view.

    Request-level only: the validated query parameters (CSV lists and city
    pairs sorted), the effective portfolio criteria, the public bounds, the
    canonical approval request id, the sort and a digest of the raw handoff.
    Never a Lakebase-resolved list (an assignee's borrowers, a cohort replay's
    ids, a request's open ids) and never ``limit``: a decision taken between
    pages must not invalidate the view.
    """

    normalized: dict[str, Any] = {}
    for field in dataclasses.fields(params):
        value = getattr(params, field.name)
        if field.name in _CSV_FIELDS:
            value = _sorted_csv(value)
        elif field.name == "cities":
            value = _city_pairs(value)
        elif field.name == "assigned_to" and isinstance(value, str):
            value = value.strip().lower() or None
        normalized[field.name] = value
    normalized.update(
        {
            "approval_request_batch": approval_request_batch,
            "effective_marketing_eligibility": (
                None if params.include_suppressed_for_analytics else params.marketing_eligibility
            ),
            "public_bounds": params.public_bounds(),
            "portfolio_criteria": (
                portfolio_criteria.model_dump(mode="json", exclude_none=True) if portfolio_criteria else None
            ),
            "sort": order.sort,
            "sort_dir": order.sort_dir,
            "growth_handoff": (
                hashlib.sha256(growth_handoff.encode("utf-8")).hexdigest() if growth_handoff else None
            ),
        }
    )
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lead_view_filter_fingerprint(digest: str) -> str | None:
    """The KEYED fingerprint (VIEW_LEADS metadata and the cursor's ``fp``), or None without a key."""

    return keyed_filter_fingerprint(digest, domain=LEAD_VIEW_FINGERPRINT_DOMAIN)


def lead_view_fingerprint_candidates(digest: str) -> list[str]:
    """The fingerprints a cursor minted under the current or previous key may carry."""

    return keyed_filter_fingerprint_candidates(digest, domain=LEAD_VIEW_FINGERPRINT_DOMAIN)
