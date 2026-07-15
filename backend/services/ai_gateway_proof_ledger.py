"""Lakebase-backed exact-row proof ledger for AI Gateway capability claims."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from backend.config.settings import AI_GATEWAY_PROOF_FRESHNESS_MAX_S
from backend.services.ai_gateway_proof_attestation import (
    AI_GATEWAY_PROOF_ATTESTATION_ALG,
    gateway_proof_key_id,
    sign_gateway_proof,
    verify_gateway_proof,
)

ProofStatus = Literal["pending", "verified", "failed", "expired"]

# Lakebase, verifier hosts, and Databricks SQL may not share an exact clock.
# Five minutes is the only accepted positive skew; evidence farther in the
# future fails closed instead of extending proof freshness indefinitely.
AI_GATEWAY_PROOF_CLOCK_SKEW_S = 5 * 60


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
    attestation_alg: str | None = None
    attestation_key_id: str | None = None
    attestation_signature: str | None = None


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
    attestation_verify_key: str | None,
    now: datetime | None = None,
) -> AiGatewayVerifiedProof | None:
    if not attestation_verify_key:
        return None
    try:
        expected_key_id = gateway_proof_key_id(attestation_verify_key)
    except ValueError:
        return None
    reference_now = _as_aware_datetime(now or datetime.now(UTC))
    clock_skew = timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S)
    cutoff = reference_now - timedelta(seconds=bounded_gateway_proof_freshness_s(freshness_s))
    future_cutoff = reference_now + clock_skew
    row = lakebase.fetchone(
        """
        SELECT proof_id, git_sha, client_request_id, endpoint_name, inference_table,
               sent_at, verified_at, verify_latency_s, status,
               attestation_alg, attestation_key_id, attestation_signature
        FROM mip_app.ai_gateway_proof_ledger
        WHERE git_sha = %(git_sha)s
          AND endpoint_name = %(endpoint_name)s
          AND inference_table = %(inference_table)s
          AND status = 'verified'
          AND attestation_alg = %(attestation_alg)s
          AND attestation_key_id = %(attestation_key_id)s
          AND verified_at IS NOT NULL
          AND verified_at >= %(cutoff)s
          AND verified_at <= %(future_cutoff)s
          AND sent_at <= %(future_cutoff)s
          AND verified_at >= sent_at - (%(clock_skew_s)s * INTERVAL '1 second')
        ORDER BY verified_at DESC
        LIMIT 1
        """,
        {
            "git_sha": git_sha,
            "endpoint_name": endpoint_name,
            "inference_table": inference_table,
            "cutoff": cutoff,
            "future_cutoff": future_cutoff,
            "clock_skew_s": AI_GATEWAY_PROOF_CLOCK_SKEW_S,
            "attestation_alg": AI_GATEWAY_PROOF_ATTESTATION_ALG,
            "attestation_key_id": expected_key_id,
        },
    )
    if not row:
        return None
    proof = _proof_from_row(row)
    if not (
        cutoff <= proof.verified_at <= future_cutoff
        and proof.sent_at <= future_cutoff
        and proof.verified_at >= proof.sent_at - clock_skew
    ):
        return None
    if not verify_gateway_proof(
        verify_key=attestation_verify_key,
        attestation_alg=proof.attestation_alg,
        attestation_key_id=proof.attestation_key_id,
        attestation_signature=proof.attestation_signature,
        proof_id=proof.proof_id,
        git_sha=proof.git_sha,
        client_request_id=proof.client_request_id,
        endpoint_name=proof.endpoint_name,
        inference_table=proof.inference_table,
        sent_at=proof.sent_at,
        verified_at=proof.verified_at,
    ):
        return None
    return proof


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
    observed_now = datetime.now(UTC)
    proof_sent_at = _as_aware_datetime(sent_at or observed_now)
    _reject_future_gateway_timestamp("sent_at", proof_sent_at, now=observed_now)
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
                  sent_at, verified_at, verify_latency_s, status,
                  attestation_alg, attestation_key_id, attestation_signature
        """,
        {
            "proof_id": uuid4(),
            "git_sha": git_sha,
            "client_request_id": client_request_id,
            "endpoint_name": endpoint_name,
            "inference_table": inference_table,
            "sent_at": proof_sent_at,
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
                   sent_at, verified_at, verify_latency_s, status,
                   attestation_alg, attestation_key_id, attestation_signature
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
                   sent_at, verified_at, verify_latency_s, status,
                   attestation_alg, attestation_key_id, attestation_signature
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
    attestation_signing_key: str,
    verified_at: datetime | None = None,
) -> AiGatewayVerifiedProof:
    observed_now = datetime.now(UTC)
    proof_sent_at = _as_aware_datetime(sent_at)
    verified = _as_aware_datetime(verified_at or observed_now)
    _reject_future_gateway_timestamp("sent_at", proof_sent_at, now=observed_now)
    _reject_future_gateway_timestamp("verified_at", verified, now=observed_now)
    if verified < proof_sent_at - timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S):
        raise ValueError("AI Gateway verified_at precedes sent_at beyond clock tolerance")
    latency_s = max(0.0, (verified - proof_sent_at).total_seconds())
    current = lakebase.fetchone(
        """
        SELECT proof_id, git_sha, client_request_id, endpoint_name, inference_table,
               sent_at, verified_at, verify_latency_s, status,
               attestation_alg, attestation_key_id, attestation_signature
        FROM mip_app.ai_gateway_proof_ledger
        WHERE proof_id = %(proof_id)s
          AND status = 'pending'
        """,
        {"proof_id": proof_id},
    )
    if not current:
        raise RuntimeError("AI Gateway pending proof was not found")
    pending = _proof_from_row(current)
    attestation_alg, attestation_key_id, attestation_signature = sign_gateway_proof(
        signing_key=attestation_signing_key,
        proof_id=pending.proof_id,
        git_sha=pending.git_sha,
        client_request_id=pending.client_request_id,
        endpoint_name=pending.endpoint_name,
        inference_table=pending.inference_table,
        sent_at=pending.sent_at,
        verified_at=verified,
    )
    row = lakebase.fetchone(
        """
        UPDATE mip_app.ai_gateway_proof_ledger
        SET status = 'verified',
            verified_at = %(verified_at)s,
            verify_latency_s = %(verify_latency_s)s,
            attestation_alg = %(attestation_alg)s,
            attestation_key_id = %(attestation_key_id)s,
            attestation_signature = %(attestation_signature)s
        WHERE proof_id = %(proof_id)s
          AND status = 'pending'
        RETURNING proof_id, git_sha, client_request_id, endpoint_name, inference_table,
                  sent_at, verified_at, verify_latency_s, status,
                  attestation_alg, attestation_key_id, attestation_signature
        """,
        {
            "proof_id": proof_id,
            "verified_at": verified,
            "verify_latency_s": latency_s,
            "attestation_alg": attestation_alg,
            "attestation_key_id": attestation_key_id,
            "attestation_signature": attestation_signature,
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
        attestation_alg=str(row["attestation_alg"]) if row.get("attestation_alg") else None,
        attestation_key_id=(
            str(row["attestation_key_id"]) if row.get("attestation_key_id") else None
        ),
        attestation_signature=(
            str(row["attestation_signature"]) if row.get("attestation_signature") else None
        ),
    )


def _as_aware_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _reject_future_gateway_timestamp(label: str, value: datetime, *, now: datetime) -> None:
    future_cutoff = _as_aware_datetime(now) + timedelta(seconds=AI_GATEWAY_PROOF_CLOCK_SKEW_S)
    if _as_aware_datetime(value) > future_cutoff:
        raise ValueError(
            f"AI Gateway {label} exceeds the {AI_GATEWAY_PROOF_CLOCK_SKEW_S}-second "
            "clock tolerance"
        )
