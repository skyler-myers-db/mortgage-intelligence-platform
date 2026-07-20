"""Tenant disclosure resolution for governed outreach drafts.

Outreach copy must never contain "insert disclosure here" placeholders.
The draft path resolves an active tenant/state/channel disclosure from
Lakebase and fails closed when none is configured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.config.settings import settings
from backend.schemas.common import contains_pii_marker
from backend.schemas.lender_identity import (
    validate_public_lender_name,
    validate_public_lender_nmls_id,
)
from backend.services.lakebase import LakebaseClient


class MissingTenantDisclosureError(RuntimeError):
    """Raised when no active disclosure block exists for a state/channel."""


@dataclass(frozen=True)
class DisclosureBlock:
    state: str
    channel: str
    disclosure_version: str
    body: str


_DISCLOSURE_SELECT_SQL = """
SELECT state, channel, disclosure_version, body
FROM mip_app.tenant_disclosures
WHERE tenant_id = %(tenant_id)s
  AND channel = %(channel)s
  AND state IN (%(state)s, '_ALL')
  AND active = TRUE
ORDER BY CASE WHEN state = %(state)s THEN 0 ELSE 1 END, updated_at DESC
LIMIT 1
"""

_DISCLOSURE_PLACEHOLDER_PATTERNS = (
    "insert governed",
    "insert disclosure",
    "todo",
    "[first name]",
    "{first_name}",
    "{first name}",
    "nmls...",
)

_REVIEWED_DISCLOSURE_SUFFIXES: dict[str, dict[str, frozenset[str]]] = {
    "email": {
        "_ALL": frozenset(
            {
                "Equal Housing Lender. Reply unsubscribe to opt out.",
                "Equal Housing Lender. This is not a commitment to lend. Terms subject to "
                "credit, collateral, and underwriting approval. To opt out of marketing, "
                "reply unsubscribe or contact {lender} at its governed compliance address.",
            }
        ),
        "CA": frozenset(
            {
                "Equal Housing Lender. California residents: this is not a commitment to "
                "lend and terms are subject to credit, collateral, and underwriting "
                "approval. To opt out of marketing, reply unsubscribe or contact {lender} "
                "at its governed compliance address."
            }
        ),
        "NY": frozenset(
            {
                "Equal Housing Lender. New York residents: mortgage terms are subject to "
                "licensed review, credit, collateral, and underwriting approval. To opt "
                "out of marketing, reply unsubscribe or contact {lender} at its governed "
                "compliance address."
            }
        ),
    },
    "direct_mail": {
        "_ALL": frozenset(
            {
                "Equal Housing Lender. Reply unsubscribe to opt out.",
                "Equal Housing Lender. This is not a commitment to lend. Terms subject to "
                "credit, collateral, and underwriting approval. To opt out of marketing, "
                "contact {lender} at its governed compliance address.",
            }
        )
    },
    "sms": {
        "_ALL": frozenset(
            {
                "Equal Housing Lender. Reply STOP to opt out.",
                "Equal Housing Lender. Reply STOP to opt out. Msg and data rates may apply.",
            }
        ),
        "CA": frozenset(
            {
                "Equal Housing Lender. CA residents may reply STOP to opt out. Msg and data "
                "rates may apply."
            }
        ),
    },
}
_DISCLOSURE_PREFIX_RE = re.compile(
    r"(?P<lender>.+?)(?P<comma>,?) NMLS #(?P<nmls>[1-9]\d{3,11})\. (?P<suffix>.+)",
)


def _is_reviewed_disclosure(*, normalized: str, channel: str, state: str) -> bool:
    match = _DISCLOSURE_PREFIX_RE.fullmatch(normalized)
    if match is None:
        return False
    lender_name = validate_public_lender_name(settings.mip_lender_name)
    if match.group("lender") != lender_name:
        return False
    lender_nmls_id = validate_public_lender_nmls_id(settings.mip_lender_nmls_id)
    if match.group("nmls") != lender_nmls_id:
        return False
    expected_comma = "" if channel == "sms" else ","
    if match.group("comma") != expected_comma:
        return False
    channel_suffixes = _REVIEWED_DISCLOSURE_SUFFIXES.get(channel, {})
    suffixes = channel_suffixes.get(state, frozenset())
    rendered_suffixes = {suffix.format(lender=lender_name) for suffix in suffixes}
    return match.group("suffix") in rendered_suffixes


def _validate_disclosure_block(*, body: str, channel: str, state: str) -> None:
    normalized = " ".join(str(body or "").split())
    lowered = normalized.lower()
    if not normalized:
        raise MissingTenantDisclosureError(
            f"tenant disclosure body is blank for state {state} and channel {channel}"
        )
    if contains_pii_marker(normalized) or any(
        token in lowered for token in _DISCLOSURE_PLACEHOLDER_PATTERNS
    ):
        raise MissingTenantDisclosureError(
            f"tenant disclosure body is not publishable for state {state} and channel {channel}"
        )
    if not _is_reviewed_disclosure(normalized=normalized, channel=channel, state=state):
        raise MissingTenantDisclosureError(
            f"tenant disclosure does not match a reviewed legal template for state {state} "
            f"and channel {channel}"
        )


def resolve_tenant_disclosure(
    lakebase: LakebaseClient,
    *,
    state: str,
    channel: str,
    tenant_id: str | None = None,
) -> DisclosureBlock:
    """Return the active disclosure block or raise fail-closed."""

    normalized_state = (state or "").strip().upper()[:2] or "_ALL"
    tenant_key = tenant_id or settings.effective_tenant_id()
    row = lakebase.fetchone(
        _DISCLOSURE_SELECT_SQL,
        {
            "tenant_id": tenant_key,
            "state": normalized_state,
            "channel": channel,
        },
    )
    if not row:
        raise MissingTenantDisclosureError(
            f"tenant disclosure not configured for state {normalized_state} and channel {channel}"
        )
    resolved_state = str(row.get("state") or normalized_state)
    resolved_channel = str(row.get("channel") or channel)
    body = str(row.get("body") or "")
    _validate_disclosure_block(body=body, channel=resolved_channel, state=resolved_state)
    return DisclosureBlock(
        state=resolved_state,
        channel=resolved_channel,
        disclosure_version=str(row.get("disclosure_version") or ""),
        body=body,
    )


def disclosure_audit_payload(block: DisclosureBlock) -> dict[str, Any]:
    return {
        "disclosure_version": block.disclosure_version,
        "disclosure_state": block.state,
        "disclosure_channel": block.channel,
    }
