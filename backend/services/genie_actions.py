"""Governed Genie action confirmation and persistence helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import HTTPException

from backend.config.runtime_secret_policy import (
    require_distinct_rotation_secrets,
    require_strong_runtime_secret,
    runtime_secret_text,
)
from backend.config.settings import settings
from backend.schemas._validators import normalize_public_lender_ref
from backend.schemas.common import validate_public_borrower_id
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.audit_store import (
    _assert_allowlisted,
    _assert_no_pii,
    _assert_public_safe_values,
    _sanitize_metadata,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_answers import (
    GenieActionRequest,
    GenieActionResponse,
    GenieMessageResponse,
)
from backend.services.genie_trusted_assets import trusted_assets
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import get_correlation_id
from backend.services.workspace_store import WorkspaceStore

_ALLOWED_ACTION_TYPES = frozenset(
    {
        "open_cohort",
        "save_borrowers",
        "create_draft_campaign",
        "compare_offer_strategies",
        "show_rationale",
    }
)

_ACTION_TOKEN_TTL_S = 2 * 60 * 60
_PROCESS_ACTION_SECRET = secrets.token_urlsafe(32)
_LOCAL_TEST_APP_ENVS = frozenset({"local", "test"})
_PLACEHOLDER_ACTION_SECRETS = frozenset(
    {
        "redacted",
        "changeme",
        "change-me",
        "change_me",
        "placeholder",
        "example",
        "your-secret",
        "your_secret",
    }
)
_MAX_ACTION_FILTER_VALUES = 500
_MAX_ACTION_STATE_VALUES = 56
_CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS = 3
_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S = 0.01
_LIVE_CAMPAIGN_RUN_MARKER_RE = re.compile(r"gha[a-j]+r[a-j]+")
_LEAD_QUEUE_REPLAY_KEYS = frozenset(
    {
        "state",
        "states",
        "zip",
        "zips",
        "county",
        "counties",
        "borrower_ids",
        "segment",
        "segment_codes",
        "segment_mode",
        "target_lender_ref",
        "cohort_id",
        "funnel_stage",
        "approval_status",
        "outreach_status",
        "assigned_to",
        "aged_days",
        "limit",
        "geography",
        "occupancy",
        "lien_status",
        "lender_relationship",
        "product",
        "min_equity_pct_label",
        "owner_link",
        "purchase_intent",
        "marketing_eligibility",
        "consent_status",
        "recency",
    }
)
_LEAD_QUEUE_PORTFOLIO_QUERY_KEYS = frozenset(
    {
        "geography",
        "occupancy",
        "lien_status",
        "lender_relationship",
        "product",
        "min_equity_pct_label",
        "owner_link",
        "purchase_intent",
        "recency",
    }
)

_CAMPAIGN_INSERT_SQL = """
WITH upserted_campaign AS (
  INSERT INTO mip_app.campaigns AS campaigns (
    name, owner_email, status, criteria,
    idempotency_key, request_payload_hash, updated_at
  ) VALUES (
    %(name)s,
    %(owner_email)s,
    'draft',
    %(criteria)s::jsonb,
    %(request_id)s,
    %(request_payload_hash)s,
    now()
  )
  ON CONFLICT (owner_email, idempotency_key)
    WHERE idempotency_key IS NOT NULL
  DO UPDATE SET
    request_payload_hash = campaigns.request_payload_hash
  WHERE campaigns.request_payload_hash = EXCLUDED.request_payload_hash
  RETURNING campaign_id, request_payload_hash
),
inserted_audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  )
  SELECT
    'GENIE_ACTION_CREATE_DRAFT_CAMPAIGN',
    %(owner_email)s,
    'campaign',
    upserted_campaign.campaign_id::text,
    %(request_id)s,
    %(correlation_id)s,
    ARRAY[]::TEXT[],
    jsonb_set(
      %(metadata)s::jsonb,
      '{campaign_id}',
      to_jsonb(upserted_campaign.campaign_id::text),
      true
    )
  FROM upserted_campaign
  ON CONFLICT (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL AND left(event_type, 13) = 'GENIE_ACTION_'
    DO NOTHING
  RETURNING audit_id
)
SELECT
  upserted_campaign.campaign_id,
  upserted_campaign.request_payload_hash,
  inserted_audit.audit_id
FROM upserted_campaign
LEFT JOIN inserted_audit ON TRUE
"""

_CAMPAIGN_IDEMPOTENCY_LOOKUP_SQL = """
SELECT campaign_id, request_payload_hash
FROM mip_app.campaigns
WHERE owner_email = %(owner_email)s
  AND idempotency_key = %(request_id)s
LIMIT 1
"""

_CAMPAIGN_AUDIT_BY_REQUEST_ID_SQL = """
SELECT audit_id
FROM mip_app.action_audit
WHERE request_id = %(request_id)s
  AND actor_email = %(actor_email)s
  AND event_type = 'GENIE_ACTION_CREATE_DRAFT_CAMPAIGN'
  AND entity_type = 'campaign'
  AND entity_id = %(campaign_id)s
  AND metadata ->> 'action_type' = 'create_draft_campaign'
ORDER BY event_at ASC
LIMIT 1
"""

_ACTION_AUDIT_BY_REQUEST_ID_SQL = """
SELECT audit_id, event_type, entity_id, metadata
FROM mip_app.action_audit
WHERE request_id = %(request_id)s
  AND actor_email = %(actor_email)s
  AND event_type = %(event_type)s
  AND metadata ->> 'action_type' = %(action_type)s
ORDER BY event_at DESC
LIMIT 1
"""

_GENIE_ACTION_AUDIT_INSERT_SQL = """
WITH inserted_audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  ) VALUES (
    %(event_type)s,
    %(actor_email)s,
    'genie_action',
    %(entity_id)s,
    %(request_id)s,
    %(correlation_id)s,
    ARRAY[]::TEXT[],
    %(metadata)s::jsonb
  )
  ON CONFLICT (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL AND left(event_type, 13) = 'GENIE_ACTION_'
    DO NOTHING
  RETURNING audit_id, entity_id, metadata
),
existing_audit AS (
  SELECT audit_id, entity_id, metadata
  FROM mip_app.action_audit
  WHERE actor_email = %(actor_email)s
    AND request_id = %(request_id)s
    AND event_type = %(event_type)s
    AND NOT EXISTS (SELECT 1 FROM inserted_audit)
  LIMIT 1
)
SELECT audit_id, entity_id, metadata
FROM inserted_audit
UNION ALL
SELECT audit_id, entity_id, metadata
FROM existing_audit
LIMIT 1
"""

_GENIE_COHORT_INSERT_SQL = """
WITH inserted_cohort AS (
  INSERT INTO mip_app.genie_cohorts (
    actor_email, request_id, conversation_id, message_id, question_hash,
    route_filters, source_assets, sql_hash, row_count
  ) VALUES (
    %(actor_email)s,
    %(request_id)s,
    %(conversation_id)s,
    %(message_id)s,
    %(question_hash)s,
    %(route_filters)s::jsonb,
    %(source_assets)s,
    %(sql_hash)s,
    %(row_count)s
  )
  ON CONFLICT (actor_email, request_id) DO NOTHING
  RETURNING cohort_id
),
existing_cohort AS (
  SELECT cohort_id
  FROM mip_app.genie_cohorts
  WHERE actor_email = %(actor_email)s
    AND request_id = %(request_id)s
    AND NOT EXISTS (SELECT 1 FROM inserted_cohort)
  LIMIT 1
)
SELECT cohort_id FROM inserted_cohort
UNION ALL
SELECT cohort_id FROM existing_cohort
LIMIT 1
"""

_GENIE_COHORT_MEMBER_INSERT_SQL = """
INSERT INTO mip_app.genie_cohort_members (cohort_id, borrower_id, rank_order)
VALUES (%(cohort_id)s, %(borrower_id)s, %(rank_order)s)
ON CONFLICT (cohort_id, borrower_id) DO UPDATE SET
  rank_order = LEAST(mip_app.genie_cohort_members.rank_order, EXCLUDED.rank_order)
"""


def borrower_ids(ids: list[str]) -> list[str]:
    out: list[str] = []
    for value in ids:
        try:
            borrower_id = validate_public_borrower_id(str(value))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Genie action includes invalid borrower id",
            ) from exc
        if borrower_id not in out:
            out.append(borrower_id)
    if len(out) > _MAX_ACTION_FILTER_VALUES:
        raise HTTPException(
            status_code=400,
            detail="Genie action returned too many borrower filters to replay safely",
        )
    return out


def _genie_event_type(action_type: str) -> str:
    return f"GENIE_ACTION_{action_type.upper()}"


def _reviewed_audit_metadata(action: str, payload: dict[str, Any]) -> str:
    metadata = _sanitize_metadata({"action": action, **payload})
    _assert_no_pii(metadata)
    _assert_allowlisted(metadata)
    _assert_public_safe_values(metadata)
    return json.dumps(metadata)


def criteria_summary(criteria: dict[str, Any]) -> tuple[str, list[str], list[str], str | None]:
    """Return the audited digest for reviewed Genie action criteria."""

    source_assets = _validated_source_assets(criteria)
    criteria_keys = sorted(str(k) for k in criteria)
    canonical_payload = {
        str(k): criteria[k] for k in sorted(criteria, key=lambda value: str(value))
    }
    canonical = json.dumps(
        canonical_payload,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    criteria_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    visualization_kind = criteria.get("visualization_kind")
    return (
        criteria_hash,
        criteria_keys,
        source_assets,
        str(visualization_kind) if visualization_kind else None,
    )


def _validated_source_assets(criteria: dict[str, Any]) -> list[str]:
    assets = [str(v) for v in criteria.get("source_assets", []) if isinstance(v, str)]
    trusted = set(trusted_assets())
    invalid = [asset for asset in assets if asset not in trusted]
    if invalid:
        raise HTTPException(status_code=400, detail="Genie action includes untrusted source assets")
    return assets[:10]


def _configured_secret_bytes(
    configured: Any,
    *,
    name: str,
    require_strong: bool,
) -> bytes | None:
    value = runtime_secret_text(
        configured,
        extra_placeholders=_PLACEHOLDER_ACTION_SECRETS,
    )
    if value is None:
        return None
    if require_strong:
        value = require_strong_runtime_secret(
            value,
            name=name,
            extra_placeholders=_PLACEHOLDER_ACTION_SECRETS,
        )
    return value.encode("utf-8")


def _action_token_key_id() -> str:
    value = (settings.mip_genie_action_secret_kid or "").strip()
    return value or "v1"


def _current_action_token_key() -> tuple[str, bytes]:
    app_env = (settings.app_env or "").strip().lower()
    require_strong = app_env not in _LOCAL_TEST_APP_ENVS
    try:
        secret = _configured_secret_bytes(
            settings.mip_genie_action_secret_current,
            name="MIP_GENIE_ACTION_SECRET_CURRENT",
            require_strong=require_strong,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if secret is not None:
        return _action_token_key_id(), secret

    if app_env not in _LOCAL_TEST_APP_ENVS:
        raise RuntimeError(
            "MIP_GENIE_ACTION_SECRET_CURRENT is required outside local/test app environments"
        )

    legacy_secret = _configured_secret_bytes(
        settings.mip_genie_action_secret,
        name="MIP_GENIE_ACTION_SECRET",
        require_strong=False,
    )
    if legacy_secret is not None:
        return _action_token_key_id(), legacy_secret
    return "process", _PROCESS_ACTION_SECRET.encode("utf-8")


def _previous_action_token_key() -> tuple[str, bytes] | None:
    app_env = (settings.app_env or "").strip().lower()
    try:
        secret = _configured_secret_bytes(
            settings.mip_genie_action_secret_previous,
            name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
            require_strong=app_env not in _LOCAL_TEST_APP_ENVS,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if secret is None:
        return None
    current = _current_action_token_key()[1].decode("utf-8")
    try:
        require_distinct_rotation_secrets(
            current,
            secret.decode("utf-8"),
            current_name="MIP_GENIE_ACTION_SECRET_CURRENT",
            previous_name="MIP_GENIE_ACTION_SECRET_PREVIOUS",
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    key_id = (settings.mip_genie_action_secret_previous_kid or "").strip() or "previous"
    return key_id, secret


def _action_token_keys(*, kid_hint: str | None = None) -> list[tuple[str, bytes]]:
    keys = [_current_action_token_key()]
    previous = _previous_action_token_key()
    if previous is not None:
        keys.append(previous)
    if kid_hint:
        matching = [item for item in keys if item[0] == kid_hint]
        nonmatching = [item for item in keys if item[0] != kid_hint]
        return matching + nonmatching
    return keys


def _action_token_secret() -> bytes:
    """Return the current signing secret.

    Kept as a small compatibility helper for tests and adjacent modules; new
    verification code uses ``_action_token_keys`` so previous-key grace windows
    can validate in-flight tokens during rotation.
    """

    _key_id, secret = _current_action_token_key()
    return secret


def current_action_token_secret_for_cache() -> str:
    """Return a stable, non-logged secret string for actor-cache hashing."""

    return _action_token_secret().decode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _action_token_claims(
    *,
    actor: str,
    action_type: str,
    borrower_ids: list[str],
    criteria: dict[str, Any],
    route: str | None,
    conversation_id: str | None,
    message_id: str | None,
    question_hash: str | None,
    request_id: str,
    expires_at: int,
    nonce: str,
    key_id: str | None = None,
    live_campaign_run_marker: str | None = None,
) -> dict[str, Any]:
    criteria_hash, _criteria_keys, source_assets, _visualization_kind = criteria_summary(criteria)
    claims = {
        "v": 1,
        "actor": actor,
        "action_type": action_type,
        "borrower_ids": sorted(set(borrower_ids)),
        "conversation_id": conversation_id or "",
        "criteria_hash": criteria_hash,
        "exp": expires_at,
        "message_id": message_id or "",
        "nonce": nonce,
        "question_hash": question_hash or "",
        "request_id": request_id,
        "route": route or "",
        "trusted_assets": sorted(set(source_assets)),
    }
    if key_id is not None:
        claims["kid"] = key_id
    if live_campaign_run_marker is not None:
        claims["live_campaign_run_marker"] = live_campaign_run_marker
    return claims


def normalize_live_campaign_run_marker(value: object) -> str | None:
    """Accept only the non-PII marker format derived by the live workflow."""

    if value is None or value == "":
        return None
    marker = str(value).strip()
    if len(marker) > 40 or not _LIVE_CAMPAIGN_RUN_MARKER_RE.fullmatch(marker):
        raise ValueError("live campaign run marker is invalid")
    return marker


def _sign_action_claims(claims: dict[str, Any]) -> str:
    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":"), default=str)
    body = _b64url_encode(canonical.encode("utf-8"))
    sig = hmac.new(
        _action_token_secret(),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{body}.{_b64url_encode(sig)}"


def issue_response_action_tokens(
    response: GenieMessageResponse,
    *,
    actor: str,
    live_campaign_run_marker: str | None = None,
) -> None:
    normalized_run_marker = normalize_live_campaign_run_marker(live_campaign_run_marker)
    signed_actions = []
    for action in response.actions:
        expires_at = int(time.time()) + _ACTION_TOKEN_TTL_S
        request_id = action.request_id or f"genie-action-{uuid4()}"
        action.request_id = request_id
        key_id, _secret = _current_action_token_key()
        try:
            claims = _action_token_claims(
                actor=actor,
                action_type=action.action_type,
                borrower_ids=borrower_ids(action.borrower_ids),
                criteria=action.criteria,
                route=action.route,
                conversation_id=response.conversation_id,
                message_id=response.message_id,
                question_hash=response.question_hash,
                request_id=request_id,
                expires_at=expires_at,
                nonce=secrets.token_urlsafe(12),
                key_id=key_id,
                live_campaign_run_marker=(
                    normalized_run_marker
                    if action.action_type == "create_draft_campaign"
                    else None
                ),
            )
        except HTTPException:
            # Response actions are optional affordances. If a raw Genie answer
            # returns an oversized or unsafe replay action, preserve the answer
            # and proof but omit the unsafe confirmation path. Confirmed action
            # requests still use the strict validators below.
            continue
        action.confirmation_token = _sign_action_claims(claims)
        signed_actions.append(action)
    response.actions = signed_actions


def _decode_action_token(token: str) -> dict[str, Any]:
    try:
        body, supplied_sig = token.split(".", 1)
        claims = json.loads(_b64url_decode(body).decode("utf-8"))
        if not isinstance(claims, dict):
            raise ValueError("claims body is not an object")
        actual_sig = _b64url_decode(supplied_sig)
        kid_hint = str(claims.get("kid") or "") or None
        for _key_id, secret in _action_token_keys(kid_hint=kid_hint):
            expected_sig = hmac.new(
                secret,
                body.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if hmac.compare_digest(actual_sig, expected_sig):
                break
        else:
            raise ValueError("bad signature")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Genie action confirmation token is invalid",
        ) from exc
    if not isinstance(claims, dict):
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    return claims


def _lookup_existing_genie_action(
    lakebase: LakebaseClient,
    *,
    request_id: str | None,
    actor: str,
    action_type: str,
) -> GenieActionResponse | None:
    """Return the prior result for a replayed Genie action request."""

    if not request_id:
        return None
    try:
        row = lakebase.fetchone(
            _ACTION_AUDIT_BY_REQUEST_ID_SQL,
            {
                "request_id": request_id,
                "actor_email": actor,
                "event_type": _genie_event_type(action_type),
                "action_type": action_type,
            },
        )
    except LakebaseError:
        return None
    if row is None or "metadata" not in row:
        return None
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    audit_event_id = str(row.get("audit_id") or "")
    if not audit_event_id:
        return None
    campaign_id = metadata.get("campaign_id")
    if not campaign_id and action_type == "create_draft_campaign":
        campaign_id = row.get("entity_id")
    if action_type == "create_draft_campaign" and not campaign_id:
        return None
    saved_count = metadata.get("saved_count")
    try:
        parsed_saved_count = int(saved_count or 0)
    except (TypeError, ValueError):
        parsed_saved_count = 0
    return GenieActionResponse(
        ok=True,
        action_type=action_type,
        audit_event_id=audit_event_id,
        route=str(metadata.get("route") or "") or None,
        saved_count=parsed_saved_count,
        campaign_id=str(campaign_id) if campaign_id else None,
        message="Genie action was already recorded for this request.",
    )


def _campaign_request_payload_hash(
    payload: GenieActionRequest,
    campaign_payload: dict[str, Any],
    *,
    live_campaign_run_marker: str | None,
) -> str:
    canonical = json.dumps(
        {
            "action_type": payload.action_type,
            "borrower_ids": borrower_ids(payload.borrower_ids),
            "campaign_criteria": campaign_payload,
            "conversation_id": payload.conversation_id,
            "message_id": payload.message_id,
            "question_hash": payload.question_hash,
            "route": payload.route,
            "live_campaign_run_marker": live_campaign_run_marker,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _campaign_identity(
    row: dict[str, Any],
    *,
    expected_payload_hash: str,
) -> str:
    stored_hash = str(row.get("request_payload_hash") or "")
    if stored_hash != expected_payload_hash:
        raise HTTPException(
            status_code=409,
            detail="Genie campaign request id already belongs to a different payload",
        )
    campaign_id = str(row.get("campaign_id") or "")
    if not campaign_id:
        raise LakebaseError("Genie campaign idempotency row is missing campaign_id")
    return campaign_id


def _lookup_campaign_idempotency_row(
    lakebase: LakebaseClient,
    *,
    actor: str,
    request_id: str,
    attempts: int = 1,
) -> dict[str, Any] | None:
    params = {"owner_email": actor, "request_id": request_id}
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S * attempt)
        row = lakebase.fetchone(_CAMPAIGN_IDEMPOTENCY_LOOKUP_SQL, params)
        if row is not None:
            return row
    return None


def _lookup_campaign_audit_id(
    lakebase: LakebaseClient,
    *,
    actor: str,
    request_id: str,
    campaign_id: str,
) -> str:
    params = {
        "actor_email": actor,
        "request_id": request_id,
        "campaign_id": campaign_id,
    }
    for attempt in range(_CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS):
        if attempt:
            time.sleep(_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S * attempt)
        row = lakebase.fetchone(_CAMPAIGN_AUDIT_BY_REQUEST_ID_SQL, params)
        if row is not None and row.get("audit_id"):
            return str(row["audit_id"])
    raise LakebaseError("Genie campaign audit winner was not visible after bounded lookup")


def _campaign_action_response(
    *,
    payload: GenieActionRequest,
    campaign_id: str,
    audit_event_id: str,
    replayed: bool,
) -> GenieActionResponse:
    if not campaign_id or not audit_event_id:
        raise LakebaseError("Genie campaign confirmation is missing persisted identifiers")
    return GenieActionResponse(
        ok=True,
        action_type=payload.action_type,
        audit_event_id=audit_event_id,
        route=payload.route or "/lead-queue",
        campaign_id=campaign_id,
        message=(
            "Genie campaign creation was already recorded for this request."
            if replayed
            else "Created a Lakebase draft campaign from this Genie result."
        ),
    )


def _validate_action_confirmation(payload: GenieActionRequest, *, actor: str) -> dict[str, Any]:
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Genie action requires explicit confirmation")
    if not payload.confirmation_token:
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    claims = _decode_action_token(payload.confirmation_token)
    if claims.get("exp") is None:
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    try:
        expires_at = int(claims["exp"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Genie action confirmation token is invalid",
        ) from exc
    if expires_at < int(time.time()):
        raise HTTPException(status_code=400, detail="Genie action confirmation token expired")
    token_request_id = str(claims.get("request_id") or "")
    if not token_request_id:
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    if payload.request_id != token_request_id:
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    try:
        live_campaign_run_marker = normalize_live_campaign_run_marker(
            claims.get("live_campaign_run_marker")
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Genie action confirmation token is invalid"
        ) from exc
    if live_campaign_run_marker is not None and payload.action_type != "create_draft_campaign":
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    expected_claims = _action_token_claims(
        actor=actor,
        action_type=payload.action_type,
        borrower_ids=borrower_ids(payload.borrower_ids),
        criteria=payload.criteria,
        route=payload.route,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        question_hash=payload.question_hash,
        request_id=token_request_id,
        expires_at=expires_at,
        nonce=str(claims.get("nonce") or ""),
        key_id=str(claims["kid"]) if claims.get("kid") is not None else None,
        live_campaign_run_marker=live_campaign_run_marker,
    )
    for key, expected_value in expected_claims.items():
        if claims.get(key) != expected_value:
            raise HTTPException(
                status_code=400, detail="Genie action confirmation token is invalid"
            )
    if claims.get("v") != 1 or not claims.get("nonce"):
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    return claims


def _audit_payload(
    payload: GenieActionRequest, *, saved_count: int = 0, campaign_id: str | None = None
) -> dict[str, Any]:
    criteria_hash, criteria_keys, source_assets, visualization_kind = criteria_summary(
        payload.criteria
    )
    rendered_borrower_ids = borrower_ids(payload.borrower_ids)
    out: dict[str, Any] = {
        "action_type": payload.action_type,
        "conversation_id": payload.conversation_id,
        "message_id": payload.message_id,
        "question_hash": payload.question_hash,
        "rendered_borrower_ids": rendered_borrower_ids,
        "row_count": int(payload.criteria.get("row_count") or len(rendered_borrower_ids) or 0),
        "saved_count": saved_count,
        "campaign_id": campaign_id,
        "source": str(payload.criteria.get("source") or "genie"),
        "criteria_hash": criteria_hash,
        "criteria_keys": criteria_keys,
        "source_assets": source_assets,
        "visualization_kind": visualization_kind,
        "route": payload.route,
    }
    result_filters = _cohort_route_filters(payload, [])
    if result_filters:
        out["result_filters"] = result_filters
    sql_hash = payload.criteria.get("sql_hash")
    if sql_hash:
        out["sql_hash"] = str(sql_hash)
    return out


def _campaign_criteria(payload: GenieActionRequest) -> dict[str, Any]:
    payload_borrower_ids = borrower_ids(payload.borrower_ids)
    criteria_hash, criteria_keys, source_assets, visualization_kind = criteria_summary(
        payload.criteria
    )
    out: dict[str, Any] = {
        "source": str(payload.criteria.get("source") or "genie"),
        "marketing_eligibility": "Eligible only",
        "borrower_ids": payload_borrower_ids,
        "criteria_hash": criteria_hash,
        "criteria_keys": criteria_keys,
        "source_assets": source_assets,
        "visualization_kind": visualization_kind,
        "conversation_id": payload.conversation_id,
        "message_id": payload.message_id,
        "question_hash": payload.question_hash,
        "row_count": int(payload.criteria.get("row_count") or len(payload_borrower_ids) or 0),
        "route": payload.route,
    }
    result_filters = _cohort_route_filters(payload, [])
    if result_filters:
        out["result_filters"] = result_filters
    sql_hash = payload.criteria.get("sql_hash")
    if sql_hash:
        out["sql_hash"] = str(sql_hash)
    return out


def _list_filter(
    raw: Any,
    *,
    field: str,
    max_items: int,
    pattern: re.Pattern[str],
) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=400,
            detail=f"Genie cohort {field} filter must be a reviewed list",
        )
    out: list[str] = []
    for item in raw:
        value = str(item).strip()
        if not pattern.fullmatch(value):
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes invalid replay filter",
            )
        value = value.upper() if pattern.pattern != r"^\d{5}$" else value
        if value not in out:
            if len(out) >= max_items:
                raise HTTPException(
                    status_code=400,
                    detail="Genie cohort includes too many replay filters",
                )
            out.append(value)
    return out


def _cohort_route_filters(
    payload: GenieActionRequest, payload_borrower_ids: list[str]
) -> dict[str, Any]:
    """Return the reviewed, lead-queue-replayable filter subset."""

    filters_raw = payload.criteria.get("result_filters")
    if filters_raw is not None and not isinstance(filters_raw, dict):
        raise HTTPException(
            status_code=400,
            detail="Genie cohort result_filters must be a reviewed object",
        )
    filters = filters_raw if isinstance(filters_raw, dict) else {}
    out: dict[str, Any] = {}
    source = str(payload.criteria.get("source") or "genie")

    zips = _list_filter(
        filters.get("zips"),
        field="zips",
        max_items=_MAX_ACTION_FILTER_VALUES,
        pattern=re.compile(r"^\d{5}$"),
    )
    if zips:
        out["zips"] = zips
    county_raw = str(filters.get("county") or "").strip()
    if county_raw:
        if not re.fullmatch(r"^\d{5}$", county_raw):
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes invalid county filter",
            )
        out["county"] = county_raw
    counties = _list_filter(
        filters.get("counties"),
        field="counties",
        max_items=_MAX_ACTION_FILTER_VALUES,
        pattern=re.compile(r"^\d{5}$"),
    )
    if counties:
        out["counties"] = counties
    states = _list_filter(
        filters.get("states"),
        field="states",
        max_items=_MAX_ACTION_STATE_VALUES,
        pattern=re.compile(r"^[A-Za-z]{2}$"),
    )
    if states:
        out["states"] = states
    segment_codes = _list_filter(
        filters.get("segment_codes"),
        field="segment_codes",
        max_items=6,
        pattern=re.compile(r"^(itm|listed|permit|investor|equity|retention)$", re.IGNORECASE),
    )
    if segment_codes:
        out["segment_codes"] = [s.lower() for s in segment_codes]
        mode = str(filters.get("segment_mode") or "any").lower()
        if mode not in {"any", "all"}:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes invalid segment mode",
            )
        out["segment_mode"] = mode
    target_lender_ref = str(filters.get("target_lender_ref") or "").strip()
    if target_lender_ref:
        try:
            target_lender_ref = (
                normalize_public_lender_ref(
                    target_lender_ref,
                    allow_all=True,
                )
                or ""
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unsafe lender alias",
            ) from exc
    if target_lender_ref and target_lender_ref != "All":
        out["target_lender_ref"] = target_lender_ref

    portfolio_raw = filters.get("portfolio_criteria")
    if portfolio_raw is None:
        portfolio_raw = payload.criteria.get("portfolio_criteria")
    if portfolio_raw is not None and not isinstance(portfolio_raw, dict):
        raise HTTPException(
            status_code=400,
            detail="Genie cohort includes unreviewed portfolio criteria",
        )
    if isinstance(portfolio_raw, dict):
        try:
            portfolio_model = PortfolioCriteria(**portfolio_raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unreviewed portfolio criteria",
            ) from exc
        if portfolio_model.marketing_eligibility not in {None, "Eligible only"}:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unsupported marketing eligibility filter",
            )
        if portfolio_model.consent_status not in {None, "Any"}:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unsupported consent filter",
            )
        if not portfolio_model.has_effective_predicate(count_default_marketing=False):
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unreviewed portfolio criteria",
            )
        portfolio_criteria = portfolio_model.model_dump(exclude_none=True)
        portfolio_criteria["marketing_eligibility"] = "Eligible only"
        portfolio_criteria.pop("consent_status", None)
        if portfolio_criteria:
            out["portfolio_criteria"] = portfolio_criteria

    if payload_borrower_ids:
        out["borrower_ids"] = payload_borrower_ids
    if out and source in {"genie", "trusted_sql"}:
        out["source"] = source
    _assert_no_pii(out)
    _assert_allowlisted({"result_filters": out})
    return out


def _route_with_cohort(
    route: str | None,
    *,
    cohort_id: str,
    filters: dict[str, Any],
) -> str:
    base = route or "/lead-queue"
    parts = urlsplit(base)
    path = parts.path or "/lead-queue"
    if path != "/lead-queue":
        path = "/lead-queue"
    query = {
        key: value
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key not in _LEAD_QUEUE_REPLAY_KEYS
    }

    def set_one_or_many(*, singular: str, plural: str, values: object) -> None:
        if not isinstance(values, list) or not values:
            return
        if len(values) == 1:
            query[singular] = str(values[0])
        else:
            query[plural] = ",".join(str(v) for v in values)

    set_one_or_many(singular="zip", plural="zips", values=filters.get("zips"))
    set_one_or_many(singular="state", plural="states", values=filters.get("states"))
    set_one_or_many(singular="county", plural="counties", values=filters.get("counties"))
    borrower_ids = filters.get("borrower_ids")
    if isinstance(borrower_ids, list) and borrower_ids:
        query["borrower_ids"] = ",".join(str(v) for v in borrower_ids)
    if "county" in filters:
        query["county"] = str(filters["county"])

    segment_codes = filters.get("segment_codes")
    if isinstance(segment_codes, list) and segment_codes:
        if len(segment_codes) == 1:
            query["segment"] = str(segment_codes[0])
        else:
            query["segment_codes"] = ",".join(str(v) for v in segment_codes)
            query["segment_mode"] = str(filters.get("segment_mode") or "any")

    if "target_lender_ref" in filters:
        query["target_lender_ref"] = str(filters["target_lender_ref"])
    portfolio_criteria = filters.get("portfolio_criteria")
    if isinstance(portfolio_criteria, dict):
        for key in sorted(_LEAD_QUEUE_PORTFOLIO_QUERY_KEYS):
            value = portfolio_criteria.get(key)
            if value is not None and value != "":
                query[key] = str(value)
    query["cohort_id"] = cohort_id
    return urlunsplit(("", "", path, urlencode(query), ""))


def _materialize_genie_cohort(
    lakebase: LakebaseClient,
    *,
    actor: str,
    request_id: str,
    payload: GenieActionRequest,
    payload_borrower_ids: list[str],
) -> tuple[str, dict[str, Any]]:
    route_filters = _cohort_route_filters(payload, payload_borrower_ids)
    if not route_filters:
        raise HTTPException(
            status_code=400,
            detail="Genie cohort action has no replayable lead filters",
        )
    criteria_hash, _criteria_keys, source_assets, _visualization_kind = criteria_summary(
        payload.criteria
    )
    _ = criteria_hash
    row = lakebase.fetchone(
        _GENIE_COHORT_INSERT_SQL,
        {
            "actor_email": actor,
            "request_id": request_id,
            "conversation_id": payload.conversation_id,
            "message_id": payload.message_id,
            "question_hash": payload.question_hash,
            "route_filters": json.dumps(route_filters, sort_keys=True),
            "source_assets": source_assets,
            "sql_hash": str(payload.criteria.get("sql_hash") or "") or None,
            "row_count": int(payload.criteria.get("row_count") or len(payload_borrower_ids) or 0),
        },
    )
    if row is None or not row.get("cohort_id"):
        raise LakebaseError("genie cohort insert returned no row")
    cohort_id = str(row["cohort_id"])
    if payload_borrower_ids:
        lakebase.executemany(
            _GENIE_COHORT_MEMBER_INSERT_SQL,
            [
                {
                    "cohort_id": cohort_id,
                    "borrower_id": borrower_id,
                    "rank_order": rank,
                }
                for rank, borrower_id in enumerate(payload_borrower_ids, start=1)
            ],
        )
    return cohort_id, route_filters


def _campaign_treatment_coordinator(lakebase: LakebaseClient) -> Any:
    """Build the live coordinator lazily so non-campaign Genie actions stay warehouse-neutral."""

    from backend.services.campaign_treatment import CampaignTreatmentCoordinator
    from backend.services.databricks_sql import get_sql_client
    from backend.services.repositories.databricks_lead_cohorts import LeadCohortQueries

    return CampaignTreatmentCoordinator(
        lakebase=lakebase,
        cohort_queries=LeadCohortQueries(get_sql_client(), cache_ttl_s=0),
    )


def handle_genie_action(
    payload: GenieActionRequest,
    *,
    actor: str,
    workspace: WorkspaceStore,
    lakebase: LakebaseClient,
) -> GenieActionResponse:
    payload_borrower_ids = borrower_ids(payload.borrower_ids)
    action_type = payload.action_type
    if action_type not in _ALLOWED_ACTION_TYPES:
        raise HTTPException(status_code=400, detail="unsupported Genie action")
    claims = _validate_action_confirmation(payload, actor=actor)
    request_id = str(claims["request_id"])
    if action_type != "create_draft_campaign":
        existing = _lookup_existing_genie_action(
            lakebase,
            request_id=request_id,
            actor=actor,
            action_type=action_type,
        )
        if existing is not None:
            return existing

    try:
        if action_type == "save_borrowers":
            audit_metadata = _audit_payload(payload, saved_count=len(payload_borrower_ids))
            saved, audit_event_id = workspace.save_leads_from_genie_action(
                actor=actor,
                borrower_ids=payload_borrower_ids,
                request_id=request_id,
                entity_id=payload.message_id or payload.conversation_id or request_id,
                metadata=audit_metadata,
            )
            return GenieActionResponse(
                ok=True,
                action_type=action_type,
                audit_event_id=audit_event_id,
                route=payload.route,
                saved_count=saved,
                message=f"Saved {saved} borrower{'' if saved == 1 else 's'} to the governed workspace.",
            )

        if action_type == "open_cohort":
            cohort_id, route_filters = _materialize_genie_cohort(
                lakebase,
                actor=actor,
                request_id=request_id,
                payload=payload,
                payload_borrower_ids=payload_borrower_ids,
            )
            route = _route_with_cohort(payload.route, cohort_id=cohort_id, filters=route_filters)
            metadata = {
                **_audit_payload(payload),
                "cohort_id": cohort_id,
                "route": route,
                "result_filters": route_filters,
            }
            row = lakebase.fetchone(
                _GENIE_ACTION_AUDIT_INSERT_SQL,
                {
                    "event_type": _genie_event_type(action_type),
                    "actor_email": actor,
                    "entity_id": cohort_id,
                    "request_id": request_id,
                    "correlation_id": get_correlation_id(),
                    "metadata": _reviewed_audit_metadata(
                        "genie.open_cohort",
                        metadata,
                    ),
                },
            )
            if row is None:
                raise LakebaseError("genie cohort action audit insert returned no row")
            return GenieActionResponse(
                ok=True,
                action_type=action_type,
                audit_event_id=str(row.get("audit_id") or ""),
                route=route,
                saved_count=0,
                message="Opened a Lakebase-governed cohort in the lead queue.",
            )

        if action_type == "create_draft_campaign":
            campaign_payload = _campaign_criteria(payload)
            if not campaign_payload.get("borrower_ids") and not campaign_payload.get(
                "result_filters"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Genie campaign action has no replayable lead filters",
                )
            live_campaign_run_marker = normalize_live_campaign_run_marker(
                claims.get("live_campaign_run_marker")
            )
            request_payload_hash = _campaign_request_payload_hash(
                payload,
                campaign_payload,
                live_campaign_run_marker=live_campaign_run_marker,
            )
            from backend.schemas.portfolio import HouseholdDedupConfig
            from backend.services.campaign_treatment import CampaignTreatmentCreateSpec

            metadata = json.loads(
                _reviewed_audit_metadata(
                    "genie.create_draft_campaign",
                    {**_audit_payload(payload)},
                )
            )
            result = _campaign_treatment_coordinator(lakebase).create(
                CampaignTreatmentCreateSpec(
                    name=(
                        f"Genie strategy draft {live_campaign_run_marker}"
                        if live_campaign_run_marker
                        else "Genie strategy draft"
                    ),
                    owner_email=actor,
                    idempotency_key=request_id,
                    request_payload_hash=request_payload_hash,
                    criteria=campaign_payload,
                    suppression_policy={
                        "default": "eligible_only",
                        "marketing_eligibility": "Eligible only",
                    },
                    holdout=None,
                    household_dedup=HouseholdDedupConfig(),
                    event_type="GENIE_ACTION_CREATE_DRAFT_CAMPAIGN",
                    correlation_id=get_correlation_id(),
                    audit_metadata=metadata,
                )
            )
            audit_event_id = result.audit_id or ""
            if not audit_event_id:
                raise LakebaseError("Genie campaign finalization is missing its audit id")
            return _campaign_action_response(
                payload=payload,
                campaign_id=result.campaign_id,
                audit_event_id=audit_event_id,
                replayed=result.replayed,
            )

        row = lakebase.fetchone(
            _GENIE_ACTION_AUDIT_INSERT_SQL,
            {
                "event_type": _genie_event_type(action_type),
                "actor_email": actor,
                "entity_id": payload.message_id or payload.conversation_id or request_id,
                "request_id": request_id,
                "correlation_id": get_correlation_id(),
                "metadata": _reviewed_audit_metadata(
                    f"genie.{action_type}",
                    _audit_payload(payload),
                ),
            },
        )
        if row is None:
            raise LakebaseError("genie action audit insert returned no row")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc

    return GenieActionResponse(
        ok=True,
        action_type=action_type,
        audit_event_id=str(row.get("audit_id") or ""),
        route=payload.route,
        saved_count=0,
        message="Genie action recorded to the governed audit ledger.",
    )
