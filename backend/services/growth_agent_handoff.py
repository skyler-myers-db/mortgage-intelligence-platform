"""Signed Growth Agent handoff proofs for Lead Queue cohort review."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

from backend.services.genie_actions import _action_token_keys, _current_action_token_key

_HANDOFF_DOMAIN = b"mip.growth-agent.lead-queue.v1\x00"
_HANDOFF_TTL_S = 2 * 60 * 60
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class GrowthAgentHandoffInvalid(ValueError):
    """The handoff token or its request binding is invalid."""


class GrowthAgentHandoffStale(ValueError):
    """The signed handoff no longer describes the current source cohort."""


@dataclass(frozen=True)
class GrowthAgentHandoffProof:
    run_id: str
    filters_fingerprint: str
    cohort_fingerprint: str
    total: int
    source_snapshot: str
    tool_result_hash: str
    issued_at: int
    expires_at: int


def handoff_filters_fingerprint(normalized_filters: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(normalized_filters),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def issue_growth_agent_handoff(
    *,
    actor: str,
    run_id: str,
    normalized_filters: Mapping[str, object],
    cohort_fingerprint: str,
    total: int,
    source_snapshot: str,
    tool_result_hash: str,
    now: int | None = None,
) -> str:
    """Issue a compact proof without putting actor email or row ids in the token."""

    issued_at = int(time.time()) if now is None else int(now)
    key_id, secret = _current_action_token_key()
    run = str(run_id).strip()
    snapshot = str(source_snapshot).strip()
    fingerprint = _require_hex64(cohort_fingerprint, "cohort fingerprint")
    result_hash = _require_hex64(tool_result_hash, "tool result hash")
    if not _OPAQUE_RUN_ID_RE.fullmatch(run):
        raise ValueError("Growth Agent run id is invalid")
    if not snapshot or len(snapshot) > 256 or "@" in snapshot:
        raise ValueError("Growth Agent source snapshot is invalid")
    if isinstance(total, bool) or int(total) < 0:
        raise ValueError("Growth Agent cohort total is invalid")
    claims: dict[str, object] = {
        "v": 1,
        "kid": key_id,
        "actor_binding": _actor_binding(actor, secret),
        "run_id": run,
        "filters_fingerprint": handoff_filters_fingerprint(normalized_filters),
        "cohort_fingerprint": fingerprint,
        "total": int(total),
        "source_snapshot": snapshot,
        "tool_result_hash": result_hash,
        "iat": issued_at,
        "exp": issued_at + _HANDOFF_TTL_S,
    }
    body = _b64url_encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(secret, _HANDOFF_DOMAIN + body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(signature)}"


def verify_growth_agent_handoff(
    token: str,
    *,
    actor: str,
    normalized_filters: Mapping[str, object],
    now: int | None = None,
) -> GrowthAgentHandoffProof:
    """Verify signature, actor, expiry, and exact normalized request filters."""

    try:
        body, supplied_signature = token.split(".", 1)
        if len(token) > 4096 or not body or not supplied_signature:
            raise ValueError("invalid token shape")
        claims = json.loads(_b64url_decode(body).decode("utf-8"))
        signature = _b64url_decode(supplied_signature)
        if not isinstance(claims, dict):
            raise ValueError("claims are not an object")
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise GrowthAgentHandoffInvalid("Growth Agent handoff proof is invalid") from exc

    claimed_kid = str(claims.get("kid") or "").strip()
    matched_secret: bytes | None = None
    for key_id, secret in _action_token_keys(kid_hint=claimed_kid or None):
        if claimed_kid and key_id != claimed_kid:
            continue
        expected = hmac.new(
            secret,
            _HANDOFF_DOMAIN + body.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if hmac.compare_digest(signature, expected):
            matched_secret = secret
            break
    if matched_secret is None:
        raise GrowthAgentHandoffInvalid("Growth Agent handoff proof is invalid")
    if not hmac.compare_digest(
        str(claims.get("actor_binding") or ""),
        _actor_binding(actor, matched_secret),
    ):
        raise GrowthAgentHandoffInvalid("Growth Agent handoff actor does not match")

    try:
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
        total = int(claims["total"])
        run_id = str(claims["run_id"])
        filters_fingerprint = _require_hex64(
            claims["filters_fingerprint"],
            "filters fingerprint",
        )
        cohort_fingerprint = _require_hex64(
            claims["cohort_fingerprint"],
            "cohort fingerprint",
        )
        tool_result_hash = _require_hex64(claims["tool_result_hash"], "tool result hash")
        source_snapshot = str(claims["source_snapshot"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise GrowthAgentHandoffInvalid("Growth Agent handoff proof is invalid") from exc

    current_time = int(time.time()) if now is None else int(now)
    if (
        claims.get("v") != 1
        or not _OPAQUE_RUN_ID_RE.fullmatch(run_id)
        or isinstance(claims.get("total"), bool)
        or total < 0
        or not source_snapshot
        or len(source_snapshot) > 256
        or "@" in source_snapshot
        or issued_at > current_time + 60
        or expires_at - issued_at != _HANDOFF_TTL_S
    ):
        raise GrowthAgentHandoffInvalid("Growth Agent handoff proof is invalid")
    if expires_at < current_time:
        raise GrowthAgentHandoffStale("Growth Agent handoff proof has expired")
    if not hmac.compare_digest(
        filters_fingerprint,
        handoff_filters_fingerprint(normalized_filters),
    ):
        raise GrowthAgentHandoffInvalid("Growth Agent handoff filters do not match")
    return GrowthAgentHandoffProof(
        run_id=run_id,
        filters_fingerprint=filters_fingerprint,
        cohort_fingerprint=cohort_fingerprint,
        total=total,
        source_snapshot=source_snapshot,
        tool_result_hash=tool_result_hash,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def validate_growth_agent_handoff_identity(
    proof: GrowthAgentHandoffProof,
    identity: Mapping[str, object],
) -> None:
    """Reconcile a verified handoff against one current atomic cohort read."""

    try:
        current_total = int(str(identity.get("total") or 0))
        current_snapshot = str(identity.get("snapshot_id") or "").strip()
        cohort_digest = _require_hex64(identity.get("cohort_digest"), "cohort digest")
        current_fingerprint = _cohort_fingerprint(
            cohort_digest=cohort_digest,
            tool_result_hash=proof.tool_result_hash,
        )
    except (TypeError, ValueError) as exc:
        raise GrowthAgentHandoffStale("Lead Queue cohort proof is incomplete") from exc
    if current_total != proof.total:
        raise GrowthAgentHandoffStale("Lead Queue cohort total has changed")
    if not hmac.compare_digest(current_snapshot, proof.source_snapshot):
        raise GrowthAgentHandoffStale("Lead Queue source snapshot has changed")
    if not hmac.compare_digest(current_fingerprint, proof.cohort_fingerprint):
        raise GrowthAgentHandoffStale("Lead Queue cohort fingerprint has changed")


def _cohort_fingerprint(*, cohort_digest: str, tool_result_hash: str) -> str:
    payload = {
        "cohort_digest": _require_hex64(cohort_digest, "cohort digest"),
        "tool_result_hash": _require_hex64(tool_result_hash, "tool result hash"),
        "version": 1,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _actor_binding(actor: str, secret: bytes) -> str:
    normalized = actor.strip().lower()
    if not normalized:
        raise ValueError("Growth Agent actor is invalid")
    return hmac.new(
        secret,
        _HANDOFF_DOMAIN + b"actor\x00" + normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _require_hex64(value: object, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if _HEX64_RE.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be a SHA-256 digest")
    return normalized


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    raw = base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
    if _b64url_encode(raw) != value:
        raise ValueError("non-canonical base64url encoding")
    return raw
