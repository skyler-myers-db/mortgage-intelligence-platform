"""The audit ledger's filter SQL: one WHERE builder for page, count and facets.

``audit_filter_clauses`` is the filter block of ``AuditLakebaseStore.list``,
moved here verbatim (2026-09-30, audit ``tables-10``) so ``GET /audit/count``
counts exactly the rows ``GET /audit/events/page`` pages through for the same
parameters. ``list()`` keeps its own snapshot, sequence and event-id clauses
and appends these after them, so its rendered SQL and parameters are
byte-identical to before the move.

Every statement here is a read of ``mip_app.action_audit`` and writes no
audit row: the explorer's facets and count (``read_audit_facets``,
``read_audit_count``) are audit-free reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.schemas.audit import (
    AuditCountResponse,
    AuditFacetsResponse,
    AuditFacetsTruncated,
    AuditFacetValue,
)
from backend.services.lakebase import LakebaseClient
from backend.services.pii_redaction import mask_cotality_id

# GET /audit/count stops counting past this many matching rows.
AUDIT_COUNT_CAP = 50_000
# GET /audit/facets returns at most this many values per facet.
AUDIT_FACET_LIMIT = 200
# Facet name -> the ledger expression it groups by.
AUDIT_FACET_EXPRESSIONS: dict[str, str] = {
    "event_types": "event_type",
    "actions": "metadata->>'action'",
    "actors": "actor_email",
}


def audit_filter_clauses(
    *,
    actor: str | None = None,
    action: str | None = None,
    entity_id: str | None = None,
    borrower_id: str | None = None,
    subject_clip: str | None = None,
    event_type: str | None = None,
    correlation_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """The WHERE clauses and bound parameters for the explorer's filters."""

    clauses: list[str] = []
    params: dict[str, Any] = {}
    if actor:
        clauses.append("actor_email = %(actor)s")
        params["actor"] = actor
    if entity_id:
        clauses.append("entity_id = %(entity_id)s")
        params["entity_id"] = entity_id
    if borrower_id:
        clauses.append(
            "(entity_id = %(borrower_id)s OR metadata->>'borrower_id' = %(borrower_id)s)"
        )
        params["borrower_id"] = borrower_id
    if subject_clip:
        clauses.append("subject_clip = %(subject_clip)s")
        params["subject_clip"] = mask_cotality_id("clip", subject_clip)
    if event_type:
        clauses.append("event_type = %(event_type)s")
        params["event_type"] = event_type
    if correlation_id:
        clauses.append("correlation_id = %(correlation_id)s")
        params["correlation_id"] = correlation_id
    if action:
        clauses.append("metadata->>'action' = %(action)s")
        params["action"] = action
    if since:
        clauses.append("event_at >= %(since)s")
        params["since"] = since
    if until:
        clauses.append("event_at <= %(until)s")
        params["until"] = until
    return clauses, params


def audit_count_statement(**filters: Any) -> tuple[str, dict[str, Any]]:
    """``COUNT(*)`` of the filtered ledger, bounded at ``AUDIT_COUNT_CAP + 1`` rows."""

    clauses, params = audit_filter_clauses(**filters)
    where_clause = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    sql = (
        "SELECT COUNT(*) AS event_count FROM ("
        f"SELECT 1 FROM mip_app.action_audit {where_clause}"
        f"LIMIT {AUDIT_COUNT_CAP + 1}"
        ") t"
    )
    return sql, params


def audit_facet_statement(
    facet: str, *, since: datetime, until: datetime | None
) -> tuple[str, dict[str, Any]]:
    """Distinct non-null values of one facet with their counts, most frequent first."""

    expression = AUDIT_FACET_EXPRESSIONS[facet]
    clauses, params = audit_filter_clauses(since=since, until=until)
    clauses.append(f"{expression} IS NOT NULL")
    sql = (
        f"SELECT {expression} AS value, COUNT(*) AS event_count "
        "FROM mip_app.action_audit "
        f"WHERE {' AND '.join(clauses)} "
        "GROUP BY 1 ORDER BY 2 DESC, 1 "
        f"LIMIT {AUDIT_FACET_LIMIT + 1}"
    )
    return sql, params


def read_audit_count(lakebase: LakebaseClient, **filters: Any) -> AuditCountResponse:
    """Run the bounded count; ``capped`` when more rows match than the cap counts."""

    sql, params = audit_count_statement(**filters)
    row = lakebase.fetchone(sql, params)
    matched = int((row or {}).get("event_count") or 0)
    return AuditCountResponse(
        count=min(matched, AUDIT_COUNT_CAP),
        capped=matched > AUDIT_COUNT_CAP,
        cap=AUDIT_COUNT_CAP,
    )


def read_audit_facets(
    lakebase: LakebaseClient, *, since: datetime, until: datetime | None
) -> AuditFacetsResponse:
    """Run one bounded GROUP BY per facet; nulls dropped, truncation flagged."""

    facets: dict[str, list[AuditFacetValue]] = {}
    truncated: dict[str, bool] = {}
    for facet in AUDIT_FACET_EXPRESSIONS:
        sql, params = audit_facet_statement(facet, since=since, until=until)
        rows = lakebase.fetchall(sql, params, limit=AUDIT_FACET_LIMIT + 1)
        values = [
            AuditFacetValue(value=str(row["value"]), count=int(row.get("event_count") or 0))
            for row in rows
            if row.get("value") is not None
        ]
        truncated[facet] = len(values) > AUDIT_FACET_LIMIT
        facets[facet] = values[:AUDIT_FACET_LIMIT]
    return AuditFacetsResponse(
        event_types=facets["event_types"],
        actions=facets["actions"],
        actors=facets["actors"],
        truncated=AuditFacetsTruncated(**truncated),
        since=since,
        until=until,
    )


__all__ = [
    "AUDIT_COUNT_CAP",
    "AUDIT_FACET_EXPRESSIONS",
    "AUDIT_FACET_LIMIT",
    "audit_count_statement",
    "audit_facet_statement",
    "audit_filter_clauses",
    "read_audit_count",
    "read_audit_facets",
]
