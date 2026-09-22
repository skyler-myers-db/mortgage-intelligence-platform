"""Governed Genie action confirmation and persistence helpers."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException

from backend.schemas.genie_geo_filters import GENIE_CITY_FILTER_KEY
from backend.services.audit_store import (
    _assert_allowlisted,
    _assert_no_pii,
    _assert_public_safe_values,
    _sanitize_metadata,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_action_filters import (
    _GENIE_SEGMENT_CODE_RE,  # noqa: F401 - compatibility re-export
    _LEAD_QUEUE_PORTFOLIO_QUERY_KEYS,
    _LEAD_QUEUE_REPLAY_KEYS,
    _MAX_UNREPLAYABLE_FILTER_KEYS,  # noqa: F401 - compatibility re-export
    _REPLAYABLE_FILTER_KEYS,  # noqa: F401 - compatibility re-export
    _REPLAYABLE_NUMERIC_FILTERS,
    _cohort_route_filters,
    _disclosed_filter_key,  # noqa: F401 - compatibility re-export
)
from backend.services.genie_action_tokens import (
    _action_token_claims,
    _action_token_keys,  # noqa: F401 - compatibility re-export
    _current_action_token_key,  # noqa: F401 - compatibility re-export
    _decode_action_token,
    _previous_action_token_key,  # noqa: F401 - compatibility re-export
    _sign_action_claims,  # noqa: F401 - compatibility re-export
    borrower_ids,
    criteria_summary,
    decode_genie_claims,  # noqa: F401 - compatibility re-export
    genie_claims_key_id,  # noqa: F401 - compatibility re-export
    issue_response_action_tokens,  # noqa: F401 - compatibility re-export
    normalize_live_campaign_run_marker,
    sign_genie_claims,  # noqa: F401 - compatibility re-export
)
from backend.services.genie_answers import (
    GenieActionRequest,
    GenieActionResponse,
)
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
_CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS = 3
_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S = 0.01

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


def _genie_event_type(action_type: str) -> str:
    return f"GENIE_ACTION_{action_type.upper()}"


def _reviewed_audit_metadata(action: str, payload: dict[str, Any]) -> str:
    metadata = _sanitize_metadata({"action": action, **payload})
    _assert_no_pii(metadata)
    _assert_allowlisted(metadata)
    _assert_public_safe_values(metadata)
    return json.dumps(metadata)


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
    # No singular form: one key, always plural. `~` is unreserved, so urlencode
    # leaves it literal and a shared link reads `cities=CHICAGO~IL`.
    city_states = filters.get(GENIE_CITY_FILTER_KEY)
    if isinstance(city_states, list) and city_states:
        query[GENIE_CITY_FILTER_KEY] = ",".join(str(v) for v in city_states)
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
    for numeric_key in _REPLAYABLE_NUMERIC_FILTERS:
        # The cohort row is the governed source of truth (/leads ignores query
        # params once cohort_id is present); these are on the URL so a shared
        # link states the thresholds the queue is applying. A negative spread
        # floor keeps its sign: `-` is unreserved, so urlencode leaves it
        # literal and the link reads `min_rate_spread_bps=-25`.
        if numeric_key in filters:
            query[numeric_key] = str(filters[numeric_key])
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
