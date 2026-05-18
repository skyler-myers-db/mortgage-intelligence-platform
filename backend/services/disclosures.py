"""Tenant disclosure resolution for governed outreach drafts.

Outreach copy must never contain "insert disclosure here" placeholders.
The draft path resolves an active tenant/state/channel disclosure from
Lakebase and fails closed when none is configured.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config.settings import settings
from backend.schemas.common import contains_pii_marker
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


def _validate_disclosure_block(*, body: str, channel: str, state: str) -> None:
    normalized = " ".join(str(body or "").split())
    lowered = normalized.lower()
    if not normalized:
        raise MissingTenantDisclosureError(
            f"tenant disclosure body is blank for state {state} and channel {channel}"
        )
    if contains_pii_marker(normalized) or any(token in lowered for token in _DISCLOSURE_PLACEHOLDER_PATTERNS):
        raise MissingTenantDisclosureError(
            f"tenant disclosure body is not publishable for state {state} and channel {channel}"
        )
    if "nmls" not in lowered:
        raise MissingTenantDisclosureError(
            f"tenant disclosure missing NMLS language for state {state} and channel {channel}"
        )
    if "equal housing" not in lowered:
        raise MissingTenantDisclosureError(
            f"tenant disclosure missing Equal Housing language for state {state} and channel {channel}"
        )
    has_opt_out = any(
        phrase in lowered
        for phrase in ("opt out", "unsubscribe", "stop")
    )
    if not has_opt_out:
        raise MissingTenantDisclosureError(
            f"tenant disclosure missing opt-out language for state {state} and channel {channel}"
        )
    if channel == "sms" and "stop" not in lowered:
        raise MissingTenantDisclosureError(
            f"tenant SMS disclosure missing STOP language for state {state}"
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
