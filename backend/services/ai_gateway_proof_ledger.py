"""Lakebase-backed exact-row proof ledger for AI Gateway capability claims."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from backend.config.settings import AI_GATEWAY_PROOF_FRESHNESS_MAX_S

ProofStatus = Literal["pending", "verified", "failed", "expired"]


@dataclass(frozen=True)
class AiGatewayVerifiedProof:
    proof_id: str
    git_sha: str
    client_request_id: str
    endpoint_name: str
    inference_table: str
    sent_at: datetime
    verified_at: datetime
    verify_latency_s: float
    status: ProofStatus


def normalize_gateway_sha(raw: str | None) -> str | None:
    text = (raw or "").strip().lower()
    if len(text) != 40:
        return None
    if not all(ch in "0123456789abcdef" for ch in text):
        return None
    return text


def latest_verified_proof(
    lakebase: Any,
    *,
    git_sha: str,
    endpoint_name: str,
    inference_table: str,
    freshness_s: float,
    now: datetime | None = None,
) -> AiGatewayVerifiedProof | None:
    cutoff = (now or datetime.now(UTC)) - timedelta(
        seconds=bounded_gateway_proof_freshness_s(freshness_s)
    )
    row = lakebase.fetchone(
        """
        SELECT proof_id, git_sha, client_request_id, endpoint_name, inference_table,
               sent_at, verified_at, verify_latency_s, status
        FROM mip_app.ai_gateway_proof_ledger
        WHERE git_sha = %(git_sha)s
          AND endpoint_name = %(endpoint_name)s
          AND inference_table = %(inference_table)s
          AND status = 'verified'
          AND verified_at IS NOT NULL
          AND verified_at >= %(cutoff)s
        ORDER BY verified_at DESC
        LIMIT 1
        """,
        {
            "git_sha": git_sha,
            "endpoint_name": endpoint_name,
            "inference_table": inference_table,
            "cutoff": cutoff,
        },
    )
    return _proof_from_row(row) if row else None


def bounded_gateway_proof_freshness_s(value: Any) -> float:
    """Bound defensive callers to the documented 26-hour proof window."""
    try:
        freshness_s = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(freshness_s) or freshness_s <= 0:
        return 0.0
    return min(freshness_s, float(AI_GATEWAY_PROOF_FRESHNESS_MAX_S))


def insert_pending_proof(
    lakebase: Any,
    *,
    git_sha: str,
    client_request_id: str,
    endpoint_name: str,
    inference_table: str,
    sent_at: datetime | None = None,
) -> AiGatewayVerifiedProof:
    row = lakebase.fetchone(
        """
        INSERT INTO mip_app.ai_gateway_proof_ledger (
          proof_id, git_sha, client_request_id, endpoint_name, inference_table,
          sent_at, status
        )
        VALUES (
          %(proof_id)s, %(git_sha)s, %(client_request_id)s, %(endpoint_name)s,
          %(inference_table)s, %(sent_at)s, 'pending'
        )
        ON CONFLICT (client_request_id) DO NOTHING
        RETURNING proof_id, git_sha, client_request_id, endpoint_name, inference_table,
                  sent_at, verified_at, verify_latency_s, status
        """,
        {
            "proof_id": uuid4(),
            "git_sha": git_sha,
            "client_request_id": client_request_id,
            "endpoint_name": endpoint_name,
            "inference_table": inference_table,
            "sent_at": sent_at or datetime.now(UTC),
        },
    )
    if not row:
        raise RuntimeError("AI Gateway proof insert returned no row")
    return _proof_from_row(row)


def list_pending_proofs(
    lakebase: Any,
    *,
    git_sha: str | None = None,
    limit: int = 100,
) -> list[AiGatewayVerifiedProof]:
    if git_sha:
        rows = lakebase.fetchall(
            """
            SELECT proof_id, git_sha, client_request_id, endpoint_name, inference_table,
                   sent_at, verified_at, verify_latency_s, status
            FROM mip_app.ai_gateway_proof_ledger
            WHERE git_sha = %(git_sha)s
              AND status = 'pending'
            ORDER BY sent_at ASC
            LIMIT %(limit)s
            """,
            {"git_sha": git_sha, "limit": limit},
            limit=limit,
        )
    else:
        rows = lakebase.fetchall(
            """
            SELECT proof_id, git_sha, client_request_id, endpoint_name, inference_table,
                   sent_at, verified_at, verify_latency_s, status
            FROM mip_app.ai_gateway_proof_ledger
            WHERE status = 'pending'
            ORDER BY sent_at ASC
            LIMIT %(limit)s
            """,
            {"limit": limit},
            limit=limit,
        )
    return [_proof_from_row(row) for row in rows]


def mark_proof_verified(
    lakebase: Any,
    *,
    proof_id: str,
    sent_at: datetime,
    verified_at: datetime | None = None,
) -> AiGatewayVerifiedProof:
    verified = verified_at or datetime.now(UTC)
    latency_s = max(0.0, (verified - _as_aware_datetime(sent_at)).total_seconds())
    row = lakebase.fetchone(
        """
        UPDATE mip_app.ai_gateway_proof_ledger
        SET status = 'verified',
            verified_at = %(verified_at)s,
            verify_latency_s = %(verify_latency_s)s
        WHERE proof_id = %(proof_id)s
        RETURNING proof_id, git_sha, client_request_id, endpoint_name, inference_table,
                  sent_at, verified_at, verify_latency_s, status
        """,
        {
            "proof_id": proof_id,
            "verified_at": verified,
            "verify_latency_s": latency_s,
        },
    )
    if not row:
        raise RuntimeError("AI Gateway proof verify update returned no row")
    return _proof_from_row(row)


def mark_expired_pending_proofs(
    lakebase: Any,
    *,
    older_than: datetime,
    git_sha: str | None = None,
) -> int:
    params: dict[str, Any] = {"older_than": older_than}
    sha_clause = ""
    if git_sha:
        sha_clause = "AND git_sha = %(git_sha)s"
        params["git_sha"] = git_sha
    row = lakebase.fetchone(
        f"""
        WITH updated AS (
          UPDATE mip_app.ai_gateway_proof_ledger
          SET status = 'expired'
          WHERE status = 'pending'
            AND sent_at < %(older_than)s
            {sha_clause}
          RETURNING proof_id
        )
        SELECT COUNT(*) AS row_count FROM updated
        """,
        params,
    )
    return int((row or {}).get("row_count") or 0)


def _proof_from_row(row: dict[str, Any]) -> AiGatewayVerifiedProof:
    verified_at = row.get("verified_at")
    if row.get("status") == "verified" and verified_at is None:
        raise ValueError("verified AI Gateway proof is missing verified_at")
    return AiGatewayVerifiedProof(
        proof_id=str(row["proof_id"]),
        git_sha=str(row["git_sha"]),
        client_request_id=str(row["client_request_id"]),
        endpoint_name=str(row["endpoint_name"]),
        inference_table=str(row["inference_table"]),
        sent_at=_as_aware_datetime(row["sent_at"]),
        verified_at=_as_aware_datetime(verified_at or row["sent_at"]),
        verify_latency_s=float(row.get("verify_latency_s") or 0.0),
        status=str(row.get("status") or "pending"),  # type: ignore[arg-type]
    )


def _as_aware_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
