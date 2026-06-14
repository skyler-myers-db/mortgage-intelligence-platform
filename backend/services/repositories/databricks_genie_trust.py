"""Trusted-SQL policy and proof helpers for Databricks Genie responses."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from backend.services.databricks_sql import DatabricksSqlClient, DatabricksSqlError
from backend.services.genie_answers import GenieDataFreshness, GenieProof
from backend.services.genie_trusted_assets import trusted_assets
from backend.services.observability import emit
from backend.services.repositories.databricks_genie_policy import (
    _extract_asset_refs,
    _scrub_sql_for_policy,
    _sql_has_unqualified_relations,
    _sql_mentions_pii_columns,
)
from backend.services.repositories.databricks_genie_visualization import _row_columns

log = logging.getLogger("backend.services.repositories.databricks_repo")


def _sql_uses_stale_evidence_signal_enum(sql_query: str | None) -> bool:
    if not sql_query:
        return False
    sql = re.sub(r"\s+", " ", sql_query.strip().lower())
    if not re.search(r"\bevidence_events\b", sql):
        return False
    for literal in ("lien-change", "lien_change", "competitor"):
        if re.search(rf"\bsignal_type\s*=\s*['\"]{re.escape(literal)}['\"]", sql):
            return True
        if re.search(rf"\bsignal_type\s+in\s*\([^)]*['\"]{re.escape(literal)}['\"]", sql):
            return True
    return False


def _trusted_genie_asset_names() -> frozenset[str]:
    """Return explicit trusted assets for the configured catalog."""

    return frozenset(trusted_assets())


_TRUSTED_GENIE_ASSETS: frozenset[str] = _trusted_genie_asset_names()


def _genie_question_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def _is_select_only(sql: str | None) -> bool:
    if not sql:
        return False
    policy_sql = _scrub_sql_for_policy(sql)
    if policy_sql is None:
        return False
    stripped = policy_sql.strip().lower()
    if not (stripped.startswith("select") or stripped.startswith("with")):
        return False
    blocked = "alter|create|delete|drop|grant|insert|merge|revoke|set|truncate|update|use"
    return re.search(rf"\b(?:{blocked})\b", stripped) is None


def _trusted_sql_policy(sql: str | None, trusted_assets: list[str]) -> bool:
    return _trusted_sql_policy_core(
        sql,
        trusted_assets,
        allow_stale_evidence_enum=False,
    )


def _trusted_sql_policy_allowing_stale_evidence_enum(
    sql: str | None,
    trusted_assets: list[str],
) -> bool:
    return _trusted_sql_policy_core(
        sql,
        trusted_assets,
        allow_stale_evidence_enum=True,
    )


def _trusted_sql_policy_core(
    sql: str | None,
    trusted_assets: list[str],
    *,
    allow_stale_evidence_enum: bool,
) -> bool:
    refs = _extract_asset_refs(sql)
    allowed_assets = _trusted_genie_asset_names()
    return (
        bool(refs)
        and all(asset in allowed_assets for asset in refs)
        and all(asset in allowed_assets for asset in trusted_assets)
        and _is_select_only(sql)
        and not _sql_mentions_pii_columns(sql)
        and not _sql_has_unqualified_relations(_scrub_sql_for_policy(sql) or "")
        and (allow_stale_evidence_enum or not _sql_uses_stale_evidence_signal_enum(sql))
    )


def _source_readiness_only_assets(assets: list[str]) -> bool:
    return bool(assets) and all(asset.endswith(".gold.source_readiness") for asset in assets)


def _bounded_genie_sql(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    return f"SELECT * FROM ({stripped}) AS genie_result LIMIT 500"


def _execute_trusted_genie_sql(
    sql_client: DatabricksSqlClient,
    sql: str,
) -> list[dict[str, Any]] | None:
    try:
        return sql_client.execute(_bounded_genie_sql(sql))
    except DatabricksSqlError as exc:
        emit(
            log,
            "trusted_genie_sql_replay_failed",
            level=logging.WARNING,
            dependency="warehouse",
            outcome="degraded",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )
        return None


def _extract_filters(sql: str | None) -> list[str]:
    if not sql:
        return []
    match = re.search(
        r"\bwhere\b(?P<where>.*?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    where = re.sub(r"\s+", " ", match.group("where")).strip()
    if not where:
        return []
    return [where[:500]]


def _freshness_from_rows(
    assets: list[str],
    rows: list[dict[str, Any]] | None,
) -> list[GenieDataFreshness]:
    freshness_cols = [
        col
        for col in _row_columns(rows)
        if col.lower() in {"refreshed_at", "snapshot_at", "data_refreshed_at"}
        or col.lower().endswith("_refreshed_at")
    ]
    values: list[str] = []
    for col in freshness_cols:
        for row in rows or []:
            value = row.get(col)
            if value is not None:
                values.append(str(value))
    refreshed_at = max(values) if values else None
    if refreshed_at:
        return [
            GenieDataFreshness(
                asset=asset,
                refreshed_at=refreshed_at,
                status="live",
                note="freshness returned by the generated SQL result",
            )
            for asset in assets
        ]
    return [
        GenieDataFreshness(
            asset=asset,
            status="source-cited",
            note="generated SQL did not return refreshed_at/snapshot_at; inspect SQL or query MAX(refreshed_at) for this asset",
        )
        for asset in assets
    ]


def _known_data_gaps(question: str, assets: list[str]) -> list[str]:
    material = " ".join([question, *assets]).lower()
    return _pending_feed_gaps_from_material(material)


def _pending_feed_gaps_from_material(material: str) -> list[str]:
    material = material.lower()
    gaps: list[str] = []
    if any(token in material for token in ("permit", "building permit", "has_permit")) and "heloc_propensity" not in material:
        gaps.append(
            "Cotality Building Permits feed is pending; filed permit flags remain false until a true permit source is present."
        )
    return gaps


def _pending_feed_gaps_from_rows(rows: list[dict[str, Any]] | None) -> list[str]:
    keys = " ".join(_row_columns(rows))
    return _pending_feed_gaps_from_material(keys)


def _known_data_gaps_for_result(
    *,
    question: str,
    assets: list[str],
    sql_query: str | None,
    rows: list[dict[str, Any]] | None,
) -> list[str]:
    gaps: list[str] = []
    for gap in [
        *_known_data_gaps(question, assets),
        *_pending_feed_gaps_from_material(sql_query or ""),
        *_pending_feed_gaps_from_rows(rows),
    ]:
        if gap not in gaps:
            gaps.append(gap)
    return gaps


def _build_genie_proof(
    *,
    sql_query: str | None,
    trusted_assets: list[str],
    rows: list[dict[str, Any]] | None,
    question: str,
    conversation_id: str,
    message_id: str,
    elapsed_ms: int,
    reasoning_trace: list[dict[str, str]] | None = None,
) -> GenieProof:
    trusted = _trusted_sql_policy(sql_query, trusted_assets)
    return GenieProof(
        sql_query=sql_query,
        source_assets=trusted_assets,
        data_freshness=_freshness_from_rows(trusted_assets, rows),
        row_count=len(rows) if rows else 0,
        filters=_extract_filters(sql_query),
        trusted=trusted,
        reasoning_trace=reasoning_trace or [],
        known_data_gaps=_known_data_gaps_for_result(
            question=question,
            assets=trusted_assets,
            sql_query=sql_query,
            rows=rows,
        ),
        conversation_id=conversation_id,
        message_id=message_id,
        elapsed_ms=elapsed_ms,
        generated_at=datetime.now(UTC).isoformat(),
    )
