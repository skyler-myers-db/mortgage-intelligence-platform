"""Lead Queue destination proof enrichment for live Growth Agent evals."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

SNAPSHOT_HEADERS = (
    "X-Cohort-Snapshot-ID",
    "X-Source-Snapshot-ID",
    "X-Data-Snapshot-ID",
)


def lead_queue_destination_url(
    *,
    app_url: str,
    route: str,
    limit: int = 1,
    include_identity_proof: bool = False,
) -> str:
    parts = urlsplit(route)
    if parts.scheme or parts.netloc or parts.path != "/lead-queue":
        raise ValueError("Growth Agent response is missing a same-app Lead Queue route")
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "limit"]
    has_signed_handoff = any(key == "growth_handoff" for key, _value in query)
    query.append(("limit", str(limit)))
    if include_identity_proof and not has_signed_handoff:
        query.append(("include_identity_proof", "true"))
    return f"{app_url.rstrip('/')}/api/leads?{urlencode(query)}"


def cohort_fingerprint(*, cohort_digest: str, tool_result_hash: str) -> str:
    values = {
        "cohort_digest": cohort_digest.strip().lower(),
        "tool_result_hash": tool_result_hash.strip().lower(),
    }
    for label, value in values.items():
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    canonical = json.dumps(
        {
            **values,
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_id(headers: Any) -> str | None:
    for header in SNAPSHOT_HEADERS:
        value = str(headers.get(header) or "").strip()
        if value:
            return value
    return None


def destination_total(destination: Any) -> tuple[int | None, str | None]:
    raw_total = destination.headers.get("X-Total-Matching")
    if raw_total is None:
        return None, "Lead Queue response is missing X-Total-Matching"
    try:
        total = int(raw_total)
    except (TypeError, ValueError):
        return None, "Lead Queue X-Total-Matching is not an integer"
    if total < 0:
        return None, "Lead Queue X-Total-Matching is negative"
    return total, None


def with_destination_total(
    *,
    client: Any,
    app_url: str,
    admin_token: str,
    response: dict[str, Any],
    actor_token: str = "",
) -> dict[str, Any]:
    """Enrich an agent response with destination count and identity proof."""
    enriched = dict(response)
    try:
        url = lead_queue_destination_url(
            app_url=app_url,
            route=str(response.get("route") or ""),
            include_identity_proof=True,
        )
    except ValueError as exc:
        enriched["destination_error"] = str(exc)
        return enriched
    signed_handoff = "growth_handoff=" in str(response.get("route") or "")
    destination_token = (actor_token or admin_token) if signed_handoff else admin_token
    headers = {
        "Authorization": f"Bearer {destination_token}",
        "Accept": "application/json",
    }
    try:
        destination = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        enriched["destination_error"] = f"Lead Queue fetch failed: {type(exc).__name__}"
        return enriched
    if destination.status_code >= 400:
        enriched["destination_error"] = f"Lead Queue fetch returned HTTP {destination.status_code}"
        return enriched
    destination_count, total_error = destination_total(destination)
    if total_error is not None or destination_count is None:
        enriched["destination_error"] = total_error
        return enriched
    enriched["destination_total"] = destination_count
    tool_hash = str(response.get("tool_result_hash") or "")
    signed_fingerprint = str(destination.headers.get("X-Cohort-Fingerprint") or "").strip().lower()
    if signed_fingerprint:
        if len(signed_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in signed_fingerprint
        ):
            enriched["destination_identity_error"] = "Lead Queue X-Cohort-Fingerprint is invalid"
            return enriched
        enriched["destination_cohort_fingerprint"] = signed_fingerprint
    else:
        cohort_digest = str(destination.headers.get("X-Cohort-Digest") or "").strip().lower()
        try:
            enriched["destination_cohort_fingerprint"] = cohort_fingerprint(
                cohort_digest=cohort_digest,
                tool_result_hash=tool_hash,
            )
        except ValueError as exc:
            enriched["destination_identity_error"] = str(exc)
            return enriched
    enriched["destination_fingerprint_tool_result_hash"] = tool_hash

    destination_snapshot = snapshot_id(destination.headers)
    if not destination_snapshot:
        enriched["destination_identity_error"] = (
            "Lead Queue does not expose a cohort snapshot token for reconciliation"
        )
        return enriched
    enriched["destination_snapshot_id"] = destination_snapshot
    return enriched
