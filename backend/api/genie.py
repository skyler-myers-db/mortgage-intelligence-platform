"""Thin Genie router.

Slice-7 posture: `/api/genie/message` delegates to
``DatabricksGenieRepository`` which wraps ``ResilientGenieClient`` with
an honest degraded response gated on the ``genie`` circuit breaker. Happy
path always queries the live Databricks Genie space; no local answer body,
metric, row, or recommendation is served while Genie is reconnecting.

Prior to the 2026-04-22 real-data walkthrough this router could bypass the
live Genie path. That regression has been corrected; production modules now
serve only live Genie/trusted-SQL answers or an explicit degraded-state message.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.config.settings import settings
from backend.schemas.common import validate_public_borrower_id
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.audit_store import (
    AuditStore,
    _assert_allowlisted,
    _assert_no_pii,
    _assert_public_safe_values,
    _sanitize_metadata,
    get_audit_store,
    resolve_actor,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_answers import (
    GenieActionRequest,
    GenieActionResponse,
    GenieMessageResponse,
    GenieProof,
    load_sample_questions,
)
from backend.services.genie_client import GenieClientError
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.pii_redaction import normalize_public_lender_ref
from backend.services.repositories import BorrowerRepository, GenieAnswerRepository
from backend.services.repositories.factory import get_borrower_repository, get_genie_answer_repository
from backend.services.resilience import DependencyDownError
from backend.services.sales_state import SalesStateStore
from backend.services.state_footprint import get_state_footprint_resolver
from backend.services.workspace_store import WorkspaceStore, get_workspace_store

router = APIRouter(prefix="/api/genie", tags=["genie"])

# Annotated[...] variant of Depends so ruff's B008 stays quiet (Depends
# is not a default *value*; it's FastAPI's dependency marker).
RepoDep = Annotated[GenieAnswerRepository, Depends(get_genie_answer_repository)]
AuditDep = Annotated[AuditStore, Depends(get_audit_store)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]
WorkspaceDep = Annotated[WorkspaceStore, Depends(get_workspace_store)]
BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]


class GenieMessageRequest(BaseModel):
    question: str
    conversation_id: str | None = None


_TRUSTED_ASSET_PAIRS = (
    ("gold", "lead_population"),
    ("gold", "segment_population"),
    ("gold", "lead_scores"),
    ("gold", "borrower_360"),
    ("gold", "borrower_dossier"),
    ("gold", "evidence_events"),
    ("gold", "source_readiness"),
    ("gold", "lockin_cohort"),
    ("gold", "county_rollup"),
    ("gold", "zip_rollup"),
    ("semantics", "lead_generation_metric_view"),
    ("semantics", "segment_performance_metric_view"),
    ("semantics", "borrower_opportunity_metric_view"),
)

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
_MAX_ACTION_FILTER_VALUES = 500
_MAX_ACTION_STATE_VALUES = 56

_PROTECTED_PROMPT_TERMS = (
    "age",
    "asian",
    "black",
    "disability",
    "disabled",
    "ethnic",
    "ethnicity",
    "familial status",
    "female",
    "gender",
    "hispanic",
    "latino",
    "latina",
    "male",
    "marital status",
    "national origin",
    "native american",
    "pacific islander",
    "pregnant",
    "race",
    "religion",
    "religious",
    "sex",
    "sexual orientation",
    "white",
    "woman",
    "women",
)

_US_STATE_NAMES: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_AMBIGUOUS_STATE_CODES: frozenset[str] = frozenset({"HI", "ID", "IN", "ME", "OH", "OK", "OR"})


def _ambiguous_state_code_match_is_contextual(
    question: str, match: re.Match[str]
) -> bool:
    """Treat common-word USPS codes as states only with geography context."""

    before = question[: match.start()]
    after = question[match.end() :]
    has_geo_preface = bool(
        re.search(
            r"(?:^|[\s(,/;:-])(?:in|for|from|state|states|market|coverage|geography|geo)[:\s]+$",
            before,
            flags=re.IGNORECASE,
        )
    )
    if not has_geo_preface and not before.rstrip().endswith(("(", "[")):
        return False

    next_word = re.match(r"[\s,;:.-]+([A-Za-z]+)", after)
    if next_word is None:
        return True
    return next_word.group(1).lower() in {"is", "are", "has", "have", "with", "and"}

_CAMPAIGN_INSERT_SQL = """
WITH existing_audit AS (
  SELECT audit_id, entity_id, metadata
  FROM mip_app.action_audit
  WHERE actor_email = %(owner_email)s
    AND request_id = %(request_id)s
    AND event_type = 'GENIE_ACTION_CREATE_DRAFT_CAMPAIGN'
  LIMIT 1
),
inserted_campaign AS (
  INSERT INTO mip_app.campaigns (name, owner_email, status, criteria)
  SELECT
    %(name)s,
    %(owner_email)s,
    'draft',
    %(criteria)s::jsonb
  WHERE NOT EXISTS (SELECT 1 FROM existing_audit)
  RETURNING campaign_id
),
inserted_audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, evidence_ids, metadata
  )
  SELECT
    'GENIE_ACTION_CREATE_DRAFT_CAMPAIGN',
    %(owner_email)s,
    'campaign',
    inserted_campaign.campaign_id::text,
    %(request_id)s,
    ARRAY[]::TEXT[],
    jsonb_set(
      %(metadata)s::jsonb,
      '{campaign_id}',
      to_jsonb(inserted_campaign.campaign_id::text),
      true
    )
  FROM inserted_campaign
  ON CONFLICT (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL AND left(event_type, 13) = 'GENIE_ACTION_'
    DO NOTHING
  RETURNING audit_id, entity_id, metadata
)
SELECT
  COALESCE(
    NULLIF((SELECT entity_id FROM inserted_audit), ''),
    NULLIF((SELECT entity_id FROM existing_audit), ''),
    NULLIF((SELECT metadata ->> 'campaign_id' FROM existing_audit), '')
  ) AS campaign_id,
  COALESCE(
    (SELECT audit_id FROM inserted_audit),
    (SELECT audit_id FROM existing_audit)
  ) AS audit_id
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
    request_id, evidence_ids, metadata
  ) VALUES (
    %(event_type)s,
    %(actor_email)s,
    'genie_action',
    %(entity_id)s,
    %(request_id)s,
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

_LATEST_GENIE_SESSION_SQL = """
SELECT conversation_id
FROM mip_app.genie_sessions
WHERE actor_email = %(actor_email)s
  AND source NOT IN ('degraded', 'policy_blocked', 'refused', 'data_gap', 'out_of_footprint')
ORDER BY updated_at DESC
LIMIT 1
"""

_GENIE_SESSION_OWNERSHIP_SQL = """
SELECT conversation_id
FROM mip_app.genie_sessions
WHERE actor_email = %(actor_email)s
  AND conversation_id = %(conversation_id)s
  AND source NOT IN ('degraded', 'policy_blocked', 'refused', 'data_gap', 'out_of_footprint')
LIMIT 1
"""

_GENIE_SESSION_UPSERT_SQL = """
INSERT INTO mip_app.genie_sessions (
  actor_email, conversation_id, last_message_id, last_question_hash,
  source, trusted_assets, updated_at
) VALUES (
  %(actor_email)s, %(conversation_id)s, %(last_message_id)s, %(last_question_hash)s,
  %(source)s, %(trusted_assets)s, now()
)
ON CONFLICT (actor_email, conversation_id) DO UPDATE SET
  last_message_id = EXCLUDED.last_message_id,
  last_question_hash = EXCLUDED.last_question_hash,
  source = EXCLUDED.source,
  trusted_assets = EXCLUDED.trusted_assets,
  updated_at = now()
"""

_GENIE_MESSAGE_INSERT_SQL = """
INSERT INTO mip_app.genie_messages (
  conversation_id, message_id, actor_email, question_hash,
  source, row_count, visualization_kind, trusted_assets, request_id
) VALUES (
  %(conversation_id)s, %(message_id)s, %(actor_email)s, %(question_hash)s,
  %(source)s, %(row_count)s, %(visualization_kind)s, %(trusted_assets)s,
  %(request_id)s
)
ON CONFLICT (conversation_id, message_id) DO NOTHING
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


def _actor(request: Request) -> str:
    if settings.trust_forwarded_headers:
        email = request.headers.get("X-Forwarded-Email")
        if email:
            return email
        user = request.headers.get("X-Forwarded-User")
        if user:
            return user
        raise HTTPException(status_code=401, detail="genie action identity required")
    return resolve_actor(request)


def _safe_audit_write(store: AuditStore, **kwargs: Any) -> None:
    try:
        store.write(**kwargs)
    except Exception:
        # The answer itself must not fail because a best-effort read audit
        # row was unavailable. Governed write actions below still fail closed.
        return


def _required_audit_write(store: AuditStore, **kwargs: Any) -> None:
    try:
        store.write(**kwargs)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


def _borrower_ids(ids: list[str]) -> list[str]:
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


def _criteria_summary(criteria: dict[str, Any]) -> tuple[str, list[str], list[str], str | None]:
    # The confirmation token must bind the full reviewed action criteria,
    # not just a few display keys. Otherwise a caller could keep a valid
    # token while changing `result_filters` and turning a confirmed cohort
    # into a different one.
    source_assets = _validated_source_assets(criteria)
    criteria_keys = sorted(str(k) for k in criteria)
    canonical_payload = {
        str(k): criteria[k]
        for k in sorted(criteria, key=lambda value: str(value))
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
    trusted = set(_trusted_assets())
    invalid = [asset for asset in assets if asset not in trusted]
    if invalid:
        raise HTTPException(status_code=400, detail="Genie action includes untrusted source assets")
    return assets[:10]


def _action_token_secret() -> bytes:
    configured = settings.mip_genie_action_secret
    if configured is not None:
        value = configured.get_secret_value().strip()
        if value:
            return value.encode("utf-8")
    return _PROCESS_ACTION_SECRET.encode("utf-8")


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
) -> dict[str, Any]:
    criteria_hash, _criteria_keys, source_assets, _visualization_kind = _criteria_summary(criteria)
    return {
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


def _sign_action_claims(claims: dict[str, Any]) -> str:
    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":"), default=str)
    body = _b64url_encode(canonical.encode("utf-8"))
    sig = hmac.new(
        _action_token_secret(),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _issue_response_action_tokens(
    response: GenieMessageResponse,
    *,
    actor: str,
) -> None:
    for action in response.actions:
        expires_at = int(time.time()) + _ACTION_TOKEN_TTL_S
        request_id = action.request_id or f"genie-action-{uuid4()}"
        action.request_id = request_id
        claims = _action_token_claims(
            actor=actor,
            action_type=action.action_type,
            borrower_ids=_borrower_ids(action.borrower_ids),
            criteria=action.criteria,
            route=action.route,
            conversation_id=response.conversation_id,
            message_id=response.message_id,
            question_hash=response.question_hash,
            request_id=request_id,
            expires_at=expires_at,
            nonce=secrets.token_urlsafe(12),
        )
        action.confirmation_token = _sign_action_claims(claims)


def _decode_action_token(token: str) -> dict[str, Any]:
    try:
        body, supplied_sig = token.split(".", 1)
        expected_sig = hmac.new(
            _action_token_secret(),
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual_sig = _b64url_decode(supplied_sig)
        if not hmac.compare_digest(actual_sig, expected_sig):
            raise ValueError("bad signature")
        claims = json.loads(_b64url_decode(body).decode("utf-8"))
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
    """Return the prior result for a replayed Genie action request.

    The browser generates one request id per confirmed click. If the
    network drops after Lakebase commits, a retry should not duplicate the
    campaign, saved-workspace mutation, or audit row.
    """
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
    campaign_id = metadata.get("campaign_id")
    if not campaign_id and action_type == "create_draft_campaign":
        campaign_id = row.get("entity_id")
    saved_count = metadata.get("saved_count")
    try:
        parsed_saved_count = int(saved_count or 0)
    except (TypeError, ValueError):
        parsed_saved_count = 0
    return GenieActionResponse(
        ok=True,
        action_type=action_type,
        audit_event_id=str(row.get("audit_id") or ""),
        route=str(metadata.get("route") or "") or None,
        saved_count=parsed_saved_count,
        campaign_id=str(campaign_id) if campaign_id else None,
        message="Genie action was already recorded for this request.",
    )


def _latest_genie_conversation(
    lakebase: LakebaseClient,
    *,
    actor: str,
) -> str | None:
    try:
        row = lakebase.fetchone(_LATEST_GENIE_SESSION_SQL, {"actor_email": actor})
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if row is None:
        return None
    conversation_id = row.get("conversation_id")
    return str(conversation_id) if conversation_id else None


def _assert_genie_conversation_owned(
    lakebase: LakebaseClient,
    *,
    actor: str,
    conversation_id: str | None,
) -> None:
    if not conversation_id:
        return
    try:
        row = lakebase.fetchone(
            _GENIE_SESSION_OWNERSHIP_SQL,
            {
                "actor_email": actor,
                "conversation_id": conversation_id,
            },
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if row is None:
        raise HTTPException(
            status_code=403,
            detail="conversation_id is not owned by the current actor",
        )


def _record_genie_session(
    lakebase: LakebaseClient,
    *,
    actor: str,
    response: GenieMessageResponse,
) -> None:
    conversation_id = response.conversation_id
    if not conversation_id:
        return
    if response.source in {"degraded", "policy_blocked", "refused", "data_gap", "out_of_footprint"}:
        return
    question_hash = response.question_hash or hashlib.sha256(
        response.question.encode("utf-8")
    ).hexdigest()[:16]
    message_id = response.message_id or f"{response.source}-{question_hash}"
    params = {
        "actor_email": actor,
        "conversation_id": conversation_id,
        "last_message_id": message_id,
        "last_question_hash": question_hash,
        "source": response.source,
        "trusted_assets": response.trusted_assets,
        "question_hash": question_hash,
        "message_id": message_id,
        "row_count": int(response.row_count or 0),
        "visualization_kind": response.visualization.kind if response.visualization else None,
        "request_id": f"genie-{uuid4()}",
    }
    try:
        lakebase.execute(_GENIE_SESSION_UPSERT_SQL, params)
        lakebase.execute(_GENIE_MESSAGE_INSERT_SQL, params)
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


def _finalize_genie_response(
    lakebase: LakebaseClient,
    *,
    actor: str,
    response: GenieMessageResponse,
) -> GenieMessageResponse:
    _issue_response_action_tokens(response, actor=actor)
    _record_genie_session(lakebase, actor=actor, response=response)
    return response


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
    expected_claims = _action_token_claims(
        actor=actor,
        action_type=payload.action_type,
        borrower_ids=_borrower_ids(payload.borrower_ids),
        criteria=payload.criteria,
        route=payload.route,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        question_hash=payload.question_hash,
        request_id=token_request_id,
        expires_at=expires_at,
        nonce=str(claims.get("nonce") or ""),
    )
    for key, expected_value in expected_claims.items():
        if claims.get(key) != expected_value:
            raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    if claims.get("v") != 1 or not claims.get("nonce"):
        raise HTTPException(status_code=400, detail="Genie action confirmation token is invalid")
    return claims


def _audit_payload(payload: GenieActionRequest, *, saved_count: int = 0, campaign_id: str | None = None) -> dict[str, Any]:
    criteria_hash, criteria_keys, source_assets, visualization_kind = _criteria_summary(payload.criteria)
    borrower_ids = _borrower_ids(payload.borrower_ids)
    out: dict[str, Any] = {
        "action_type": payload.action_type,
        "conversation_id": payload.conversation_id,
        "message_id": payload.message_id,
        "question_hash": payload.question_hash,
        "rendered_borrower_ids": borrower_ids,
        "row_count": int(payload.criteria.get("row_count") or len(borrower_ids) or 0),
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
    borrower_ids = _borrower_ids(payload.borrower_ids)
    criteria_hash, criteria_keys, source_assets, visualization_kind = _criteria_summary(payload.criteria)
    out: dict[str, Any] = {
        "source": str(payload.criteria.get("source") or "genie"),
        "borrower_ids": borrower_ids,
        "criteria_hash": criteria_hash,
        "criteria_keys": criteria_keys,
        "source_assets": source_assets,
        "visualization_kind": visualization_kind,
        "conversation_id": payload.conversation_id,
        "message_id": payload.message_id,
        "question_hash": payload.question_hash,
        "row_count": int(payload.criteria.get("row_count") or len(borrower_ids) or 0),
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


def _cohort_route_filters(payload: GenieActionRequest, borrower_ids: list[str]) -> dict[str, Any]:
    """Return the reviewed, lead-queue-replayable filter subset.

    Genie can return broad action criteria, but only a small set is allowed
    to drive a lead queue: ZIPs, states, segment codes/mode, target lender
    alias, and synthetic borrower ids. Everything else stays in audit proof
    but is not treated as an executable lead predicate.
    """

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
            target_lender_ref = normalize_public_lender_ref(
                target_lender_ref,
                allow_all=True,
            ) or ""
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
        if not portfolio_model.has_effective_predicate(count_default_marketing=False):
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unreviewed portfolio criteria",
            )
        portfolio_criteria = portfolio_model.model_dump(exclude_none=True)
        if portfolio_criteria:
            out["portfolio_criteria"] = portfolio_criteria

    if borrower_ids:
        out["borrower_ids"] = borrower_ids
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
    # Goverened cohort actions should always return the lead queue. If a
    # malformed action suggests another route, keep the audit proof but
    # land on the controlled queue rather than an arbitrary path.
    if path != "/lead-queue":
        path = "/lead-queue"
    query = dict(parse_qsl(parts.query, keep_blank_values=False))
    for key in ("zips", "states", "counties", "segment_codes", "borrower_ids"):
        values = filters.get(key)
        if isinstance(values, list) and values:
            query[key] = ",".join(str(v) for v in values)
    if "county" in filters:
        query["county"] = str(filters["county"])
    if "segment_mode" in filters:
        query["segment_mode"] = str(filters["segment_mode"])
    if "target_lender_ref" in filters:
        query["target_lender_ref"] = str(filters["target_lender_ref"])
    query["cohort_id"] = cohort_id
    return urlunsplit(("", "", path, urlencode(query), ""))


def _materialize_genie_cohort(
    lakebase: LakebaseClient,
    *,
    actor: str,
    request_id: str,
    payload: GenieActionRequest,
    borrower_ids: list[str],
) -> tuple[str, dict[str, Any]]:
    route_filters = _cohort_route_filters(payload, borrower_ids)
    if not route_filters:
        raise HTTPException(
            status_code=400,
            detail="Genie cohort action has no replayable lead filters",
        )
    criteria_hash, _criteria_keys, source_assets, _visualization_kind = _criteria_summary(payload.criteria)
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
            "row_count": int(payload.criteria.get("row_count") or len(borrower_ids) or 0),
        },
    )
    if row is None or not row.get("cohort_id"):
        raise LakebaseError("genie cohort insert returned no row")
    cohort_id = str(row["cohort_id"])
    if borrower_ids:
        lakebase.executemany(
            _GENIE_COHORT_MEMBER_INSERT_SQL,
            [
                {
                    "cohort_id": cohort_id,
                    "borrower_id": borrower_id,
                    "rank_order": rank,
                }
                for rank, borrower_id in enumerate(borrower_ids, start=1)
            ],
        )
    return cohort_id, route_filters


def _trusted_assets() -> list[str]:
    assets = [qualify(schema, table) for schema, table in _TRUSTED_ASSET_PAIRS]
    for schema, table in _TRUSTED_ASSET_PAIRS:
        asset = qualify(schema, table, catalog="mip")
        if asset not in assets:
            assets.append(asset)
    return assets


def _protected_prompt_match(question: str) -> str | None:
    for term in _PROTECTED_PROMPT_TERMS:
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        if re.search(pattern, question, flags=re.IGNORECASE):
            return term
    return None


def _mentioned_states(question: str) -> list[tuple[str, str]]:
    q = question.lower()
    matched: list[tuple[str, str]] = []
    for name, code in _US_STATE_NAMES.items():
        name_pattern = r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])"
        code_pattern = r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])"
        code_match = False
        exact_code_matches = tuple(re.finditer(code_pattern, question, flags=re.IGNORECASE))
        if exact_code_matches:
            # Ambiguous state abbreviations such as IN/OR/ME/ID are also
            # English words. Treat them as USPS codes only when they appear
            # as a code token, not as the first word of a following phrase
            # ("borrowers IN the money", "A or B", etc.).
            code_match = code not in _AMBIGUOUS_STATE_CODES or any(
                _ambiguous_state_code_match_is_contextual(question, match)
                for match in exact_code_matches
            )
        if re.search(name_pattern, q) or code_match:
            matched.append((name.title(), code))
    return matched


def _footprint_metadata_gap_match(question: str) -> tuple[str, str] | None:
    matched = _mentioned_states(question)
    if not matched:
        return None
    resolver = get_state_footprint_resolver()
    if not resolver.using_fallback():
        return None
    return matched[0]


def _outside_footprint_match(question: str) -> tuple[str, str, list[str]] | None:
    matched = _mentioned_states(question)
    if not matched:
        return None
    footprint_codes = get_state_footprint_resolver().state_codes()
    allowed_codes = set(footprint_codes)
    for name, code in matched:
        if code not in allowed_codes:
            return (name, code, footprint_codes)
    return None


def _is_outreach_writer_request(question: str) -> bool:
    q = question.lower()
    patterns = (
        r"\bwrite\b.*\b(email|sms|text|message|letter)\b",
        r"\b(email|sms|text|message|letter)\b.*\b(send|write|draft)\b",
        r"\bsend\b.*\b(email|sms|text|message|letter)\b",
        r"\bdraft\b.*\b(email|sms|text|message|letter)\b",
    )
    return any(re.search(pattern, q) for pattern in patterns)


def _sales_ops_question_kind(question: str) -> str | None:
    """Detect Sales Manager operational prompts that live in Lakebase state.

    Databricks Genie is intentionally scoped away from `mip_app.*`, but Sam's
    LO-workflow questions are explicitly about assignment/disposition state.
    Route those narrow prompts through the governed backend adapter instead of
    letting Genie fabricate SQL over UC tables that cannot contain LO activity.
    """

    q = question.lower()
    if re.search(r"\b(lo|loan officer)\b", q) and re.search(
        r"application[-\s]?start|app(?:lication)? start|conversion", q
    ):
        return "conversion"
    if "approved" in q and re.search(r"application[-\s]?started|application started", q):
        return "approval_to_application"
    if re.search(r"\b(lo|loan officer)\b", q) and re.search(r"\bcalls?\b|called|standup", q):
        return "standup"
    if re.search(r"approved", q) and re.search(r"untouched|not touched|stale|aging|older than|7 days", q):
        return "aging"
    if re.search(r"\b(lo|loan officer)\b", q) and re.search(r"\bqueue\b|call[-\s]?list|borrowers", q):
        return "queue"
    return None


def _sales_ops_genie_response(
    lakebase: LakebaseClient,
    borrower_repo: BorrowerRepository,
    *,
    actor: str,
    question: str,
    conversation_id: str | None,
) -> GenieMessageResponse | None:
    kind = _sales_ops_question_kind(question)
    if kind is None:
        return None
    try:
        sales_store = SalesStateStore(lakebase)
        sales_store.require_manager_actor(actor)
        visible_lo_emails = sales_store.visible_lo_emails(actor=actor)
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail="Sales Ops Genie questions require sales-manager access") from exc
    started = time.monotonic()
    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    message_id = f"sales-ops-{uuid4()}"
    source_assets: list[str]
    sql_query: str
    rows: list[dict[str, Any]]
    answer: str
    if kind == "conversion":
        week_start = datetime.now(UTC).date() - timedelta(days=datetime.now(UTC).date().weekday())
        source_assets = ["mip_app.call_dispositions"]
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        sql_query = """
SELECT lo_email AS group_key,
       COUNT(*) AS calls_attempted,
       COUNT(*) FILTER (WHERE outcome IN ('connected','callback_scheduled','application_started')) AS contacts_reached,
       COUNT(*) FILTER (WHERE outcome = 'callback_scheduled') AS callbacks_scheduled,
       COUNT(*) FILTER (WHERE outcome = 'application_started') AS applications_started
FROM mip_app.call_dispositions
WHERE occurred_at >= %(week_start)s::date
  {lo_filter}
GROUP BY lo_email
ORDER BY CASE WHEN COUNT(*) = 0 THEN 0
              ELSE COUNT(*) FILTER (WHERE outcome = 'application_started')::float / COUNT(*)
         END DESC,
         applications_started DESC,
         calls_attempted DESC
LIMIT 10
""".format(lo_filter=lo_filter)
        raw_rows = lakebase.fetchall(
            sql_query,
            {"week_start": week_start.isoformat(), "lo_emails": sorted(visible_lo_emails or [])},
            limit=10,
        )
        rows = []
        for row in raw_rows:
            calls = int(row.get("calls_attempted") or 0)
            apps = int(row.get("applications_started") or 0)
            rows.append(
                {
                    "lo_email": row.get("group_key"),
                    "calls_attempted": calls,
                    "contacts_reached": int(row.get("contacts_reached") or 0),
                    "callbacks_scheduled": int(row.get("callbacks_scheduled") or 0),
                    "applications_started": apps,
                    "application_start_rate": round(apps / calls, 4) if calls else 0.0,
                }
            )
        if rows:
            top = rows[0]
            answer = (
                f"{top['lo_email']} has the highest application-start rate this week at "
                f"{top['application_start_rate'] * 100:.1f}% "
                f"({top['applications_started']} applications from {top['calls_attempted']} logged calls). "
                "This answer is routed through governed Sales Ops state because LO dispositions live in Lakebase."
            )
        else:
            answer = (
                "No loan-officer dispositions have been logged this week, so there is no application-start "
                "rate to rank yet. Source: governed Sales Ops state in Lakebase."
            )
    elif kind == "approval_to_application":
        week_start = datetime.now(UTC).date() - timedelta(days=datetime.now(UTC).date().weekday())
        source_assets = ["mip_app.call_dispositions"]
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        sql_query = """
SELECT COUNT(*) FILTER (WHERE outcome = 'application_started') AS applications_started,
       COUNT(*) AS calls_attempted
FROM mip_app.call_dispositions
WHERE occurred_at >= %(week_start)s::date
  {lo_filter}
""".format(lo_filter=lo_filter)
        raw_rows = lakebase.fetchall(
            sql_query,
            {"week_start": week_start.isoformat(), "lo_emails": sorted(visible_lo_emails or [])},
            limit=1,
        )
        first = raw_rows[0] if raw_rows else {}
        apps = int(first.get("applications_started") or 0)
        calls = int(first.get("calls_attempted") or 0)
        rows = [{"applications_started": apps, "calls_attempted": calls}]
        answer = (
            f"{apps} approved leads have progressed to application started this week "
            f"across {calls} logged LO dispositions. Source: governed Sales Ops state in Lakebase."
        )
    elif kind == "standup":
        yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        source_assets = ["mip_app.call_dispositions"]
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        sql_query = """
SELECT lo_email, outcome, COUNT(*) AS n
FROM mip_app.call_dispositions
WHERE occurred_at >= %(day)s::date
  AND occurred_at < (%(day)s::date + interval '1 day')
  {lo_filter}
GROUP BY lo_email, outcome
ORDER BY lo_email, outcome
""".format(lo_filter=lo_filter)
        raw_rows = lakebase.fetchall(
            sql_query,
            {"day": yesterday, "lo_emails": sorted(visible_lo_emails or [])},
            limit=500,
        )
        rows = [
            {"lo_email": row.get("lo_email"), "outcome": row.get("outcome"), "count": int(row.get("n") or 0)}
            for row in raw_rows
        ]
        calls = sum(int(row["count"]) for row in rows)
        apps = sum(int(row["count"]) for row in rows if row.get("outcome") == "application_started")
        callbacks = sum(int(row["count"]) for row in rows if row.get("outcome") == "callback_scheduled")
        answer = (
            f"Yesterday's LO standup has {calls} logged calls, {callbacks} callbacks scheduled, "
            f"and {apps} applications started. Source: governed Sales Ops state in Lakebase."
        )
    elif kind == "queue":
        source_assets = ["mip_app.lead_assignments", "mip.gold.borrower_360"]
        lo_filter = "AND assigned_to_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        sql_query = """
SELECT borrower_id, assigned_to_email, assigned_at
FROM mip_app.lead_assignments
WHERE released_at IS NULL
  {lo_filter}
ORDER BY assigned_at ASC
LIMIT 10
""".format(lo_filter=lo_filter)
        rows = [
            {
                "borrower_id": row.get("borrower_id"),
                "assigned_to_email": row.get("assigned_to_email"),
                "assigned_at": str(row.get("assigned_at") or ""),
            }
            for row in lakebase.fetchall(sql_query, {"lo_emails": sorted(visible_lo_emails or [])}, limit=10)
        ]
        answer = (
            "Open Lead Queue with `assigned_to=<lo email>&approval_status=approved&outreach_status=queued` "
            "to rank an LO's current queue by aging, score, and equity. The backend keeps assignment state "
            "in Lakebase and borrower rank in `mip.gold.borrower_360`."
        )
    else:
        source_assets = ["mip_app.approvals", "mip_app.lead_assignments", "mip_app.call_dispositions"]
        lo_filter = (
            "AND (la.assigned_to_email IS NULL OR la.assigned_to_email = ANY(%(lo_emails)s))"
            if visible_lo_emails is not None
            else ""
        )
        sql_query = """
WITH latest_approval AS (
  SELECT DISTINCT ON (borrower_id) borrower_id, action, decided_at
  FROM mip_app.approvals
  ORDER BY borrower_id, decided_at DESC
),
latest_disposition AS (
  SELECT DISTINCT ON (borrower_id) borrower_id, occurred_at
  FROM mip_app.call_dispositions
  ORDER BY borrower_id, occurred_at DESC, created_at DESC
)
SELECT a.borrower_id,
       FLOOR(EXTRACT(EPOCH FROM (now() - a.decided_at)) / 86400)::int AS age_days
FROM latest_approval a
LEFT JOIN latest_disposition d ON d.borrower_id = a.borrower_id
LEFT JOIN mip_app.lead_assignments la
  ON la.borrower_id = a.borrower_id
 AND la.released_at IS NULL
WHERE a.action = 'approve'
  AND d.occurred_at IS NULL
  {lo_filter}
  AND a.decided_at <= now() - interval '7 days'
ORDER BY a.decided_at ASC
LIMIT 100
""".format(lo_filter=lo_filter)
        rows = []
        for row in lakebase.fetchall(sql_query, {"lo_emails": sorted(visible_lo_emails or [])}, limit=100):
            borrower_id = str(row.get("borrower_id") or "")
            if not borrower_id or borrower_repo.get(borrower_id) is None:
                continue
            rows.append({"borrower_id": borrower_id, "age_days": int(row.get("age_days") or 0)})
            if len(rows) >= 10:
                break
        answer = (
            f"{len(rows)} approved leads are older than 7 days with no outreach in the returned triage window. "
            "Open Lead Queue with `approval_status=approved&outreach_status=queued&aged_days=7` for the full working list. "
            "Source: governed Sales Ops state in Lakebase."
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return GenieMessageResponse(
        conversation_id=conversation_id or "",
        question=question,
        answer=answer,
        source="sales_ops",
        trusted_assets=source_assets,
        message_id=message_id,
        elapsed_ms=elapsed_ms,
        question_hash=question_hash,
        sql_query=sql_query.strip(),
        row_count=len(rows),
        proof=GenieProof(
            sql_query=sql_query.strip(),
            source_assets=source_assets,
            row_count=len(rows),
            trusted=True,
            filters=[f"sales_ops_kind = {kind}"],
            conversation_id=conversation_id,
            message_id=message_id,
            elapsed_ms=elapsed_ms,
            generated_at=datetime.now(UTC).isoformat(),
        ),
        table_rows=rows,
        follow_up_questions=[
            "Show approved leads that have not been touched in 7 days.",
            "How many calls did each LO make yesterday?",
        ],
    )


@router.post("/start")
def genie_start(
    request: Request,
    lakebase: LakebaseDep,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    _ = payload
    actor = resolve_actor(request)
    return {
        "conversation_id": _latest_genie_conversation(lakebase, actor=actor),
        "trusted_assets": _trusted_assets(),
        "sample_questions": load_sample_questions()[:4],
    }


@router.post("/message", response_model=GenieMessageResponse)
def genie_message(
    payload: GenieMessageRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    borrower_repo: BorrowerRepoDep,
) -> GenieMessageResponse:
    actor = resolve_actor(request)
    _assert_genie_conversation_owned(
        lakebase,
        actor=actor,
        conversation_id=payload.conversation_id,
    )
    protected_term = _protected_prompt_match(payload.question)
    if protected_term:
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.refused_prompt",
            entity_type="genie_message",
            entity_id=payload.conversation_id or question_hash,
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "refused_prompt",
            },
            event_type="RUN_GENIE",
        )
        response = GenieMessageResponse(
            conversation_id=payload.conversation_id or "",
            question=payload.question,
            answer=(
                "For fair-lending compliance, I cannot segment, score, rank, "
                "or target borrowers using protected-class attributes or "
                "proxies. Ask for a permitted Module 0 strategy using trusted "
                "mortgage, lien, equity, segment, and offer signals."
            ),
            source="refused",
            trusted_assets=[],
            question_hash=question_hash,
            row_count=0,
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                filters=[],
                known_data_gaps=[
                    f"prompt refused before Genie execution due protected-class term: {protected_term}"
                ],
                conversation_id=payload.conversation_id,
            ),
            table_rows=[],
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    metadata_gap = _footprint_metadata_gap_match(payload.question)
    if metadata_gap is not None:
        state_name, state_code = metadata_gap
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.footprint_metadata_gap",
            entity_type="genie_message",
            entity_id=payload.conversation_id or question_hash,
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "footprint_metadata_gap",
                "requested_state": state_code,
            },
            event_type="RUN_GENIE",
        )
        response = GenieMessageResponse(
            conversation_id=payload.conversation_id or "",
            question=payload.question,
            answer=(
                f"I cannot answer the {state_name} ({state_code}) scope because the "
                "current gold geography coverage is temporarily unavailable. I will not "
                "fall back to generic US-state metadata for a data-bearing answer; "
                "retry after the footprint and geography rollups reconnect."
            ),
            source="data_gap",
            trusted_assets=[],
            question_hash=question_hash,
            row_count=0,
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                filters=[f"requested_state = {state_code}"],
                known_data_gaps=[
                    "Gold geography coverage unavailable; generic geography metadata is not a data scope."
                ],
                conversation_id=payload.conversation_id,
            ),
            follow_up_questions=load_sample_questions()[:2],
            table_rows=[],
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    outside_footprint = _outside_footprint_match(payload.question)
    if outside_footprint is not None:
        state_name, state_code, footprint_codes = outside_footprint
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        footprint_label = ", ".join(footprint_codes)
        footprint_view = f"full {len(footprint_codes)}-state coverage view" if footprint_codes else "full coverage view"
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.outside_footprint",
            entity_type="genie_message",
            entity_id=payload.conversation_id or question_hash,
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "outside_footprint",
                "requested_state": state_code,
                "footprint_states": footprint_codes,
            },
            event_type="RUN_GENIE",
        )
        response = GenieMessageResponse(
            conversation_id=payload.conversation_id or "",
            question=payload.question,
            answer=(
                f"{state_name} ({state_code}) is outside the current refreshed data "
                f"coverage ({footprint_label}). I will not treat that coverage gap "
                f"as zero borrower demand. Ask for one of the covered states, or "
                f"ask for the {footprint_view}."
            ),
            source="out_of_footprint",
            trusted_assets=[],
            question_hash=question_hash,
            row_count=0,
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                filters=[f"requested_state = {state_code}"],
                known_data_gaps=[
                    f"{state_code} is outside the current refreshed data coverage: {footprint_label}"
                ],
                conversation_id=payload.conversation_id,
            ),
            follow_up_questions=load_sample_questions()[:2],
            table_rows=[],
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    if _is_outreach_writer_request(payload.question):
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.outreach_guardrail",
            entity_type="genie_message",
            entity_id=payload.conversation_id or question_hash,
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "outreach_guardrail",
            },
            event_type="RUN_GENIE",
        )
        response = GenieMessageResponse(
            conversation_id=payload.conversation_id or "",
            question=payload.question,
            answer=(
                "Use governed outreach workflow for borrower communications. "
                "Genie can size the cohort, explain why borrowers qualify, and "
                "create an audited draft campaign, but borrower-specific email "
                "or SMS copy must stay in the Offer Orchestrator / outreach "
                "review path with explicit human approval."
            ),
            source="refused",
            trusted_assets=[],
            question_hash=question_hash,
            row_count=0,
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                filters=[],
                known_data_gaps=[],
                conversation_id=payload.conversation_id,
            ),
            follow_up_questions=load_sample_questions()[:2],
            table_rows=[],
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    try:
        sales_ops_response = _sales_ops_genie_response(
            lakebase,
            borrower_repo,
            actor=actor,
            question=payload.question,
            conversation_id=payload.conversation_id,
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if sales_ops_response is not None:
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.sales_ops_query",
            entity_type="genie_message",
            entity_id=sales_ops_response.message_id or sales_ops_response.question_hash or "sales_ops",
            payload_json={
                "conversation_id": sales_ops_response.conversation_id,
                "message_id": sales_ops_response.message_id,
                "question_hash": sales_ops_response.question_hash,
                "row_count": sales_ops_response.row_count or 0,
                "source_assets": sales_ops_response.trusted_assets,
                "visualization_kind": None,
                "action_type": "sales_ops_query",
            },
            event_type="RUN_GENIE",
        )
        return _finalize_genie_response(lakebase, actor=actor, response=sales_ops_response)
    # repo.respond() returns a GenieMessageResponse by contract; the
    # protocol annotates `object` only to dodge a forward-import cycle.
    try:
        result = repo.respond(payload.question, conversation_id=payload.conversation_id)
    except GenieClientError as exc:
        raise DependencyDownError(
            "genie",
            reason="genie client returned an unrecoverable response",
            last_error=exc,
            kind=DependencyDownError.KIND_RETRIES_EXHAUSTED,
        ) from exc
    _ = background
    _required_audit_write(
        audit,
        actor=actor,
        action="genie.run_query",
        entity_type="genie_message",
        entity_id=result.message_id or result.conversation_id,
        payload_json={
            "conversation_id": result.conversation_id,
            "message_id": result.message_id,
            "question_hash": result.question_hash,
            "row_count": result.row_count or 0,
            "source_assets": result.trusted_assets,
            "visualization_kind": result.visualization.kind if result.visualization else None,
        },
        event_type="RUN_GENIE",
    )
    return _finalize_genie_response(lakebase, actor=actor, response=result)  # type: ignore[arg-type]


@router.post("/actions", response_model=GenieActionResponse)
def genie_action(
    payload: GenieActionRequest,
    request: Request,
    audit: AuditDep,
    workspace: WorkspaceDep,
    lakebase: LakebaseDep,
) -> GenieActionResponse:
    _ = audit
    actor = _actor(request)
    borrower_ids = _borrower_ids(payload.borrower_ids)
    action_type = payload.action_type
    if action_type not in _ALLOWED_ACTION_TYPES:
        raise HTTPException(status_code=400, detail="unsupported Genie action")
    claims = _validate_action_confirmation(payload, actor=actor)
    request_id = str(claims["request_id"])
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
            audit_metadata = _audit_payload(payload, saved_count=len(borrower_ids))
            saved, audit_event_id = workspace.save_leads_from_genie_action(
                actor=actor,
                borrower_ids=borrower_ids,
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
                borrower_ids=borrower_ids,
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
            if not campaign_payload.get("borrower_ids") and not campaign_payload.get("result_filters"):
                raise HTTPException(
                    status_code=400,
                    detail="Genie campaign action has no replayable lead filters",
                )
            metadata = {
                **_audit_payload(payload),
            }
            row = lakebase.fetchone(
                _CAMPAIGN_INSERT_SQL,
                {
                    "name": "Genie strategy draft",
                    "owner_email": actor,
                    "criteria": json.dumps(campaign_payload),
                    "request_id": request_id,
                    "metadata": _reviewed_audit_metadata(
                        "genie.create_draft_campaign",
                        metadata,
                    ),
                },
            )
            if row is None:
                raise LakebaseError("campaign insert returned no row")
            return GenieActionResponse(
                ok=True,
                action_type=action_type,
                audit_event_id=str(row.get("audit_id") or ""),
                route=payload.route or "/lead-queue",
                campaign_id=str(row.get("campaign_id") or ""),
                message="Created a Lakebase draft campaign from this Genie result.",
            )

        row = lakebase.fetchone(
            _GENIE_ACTION_AUDIT_INSERT_SQL,
            {
                "event_type": _genie_event_type(action_type),
                "actor_email": actor,
                "entity_id": payload.message_id or payload.conversation_id or request_id,
                "request_id": request_id,
                "metadata": _reviewed_audit_metadata(
                    f"genie.{action_type}",
                    _audit_payload(payload),
                ),
            },
        )
        if row is None:
            raise LakebaseError("genie action audit insert returned no row")
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
