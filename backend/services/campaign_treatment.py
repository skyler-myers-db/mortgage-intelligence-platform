"""Reserve, materialize, and finalize immutable T0 campaign treatment sets."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from backend.schemas.genie_geo_filters import GENIE_CITY_FILTER_KEY
from backend.schemas.genie_numeric_filters import is_reviewed_numeric_floor
from backend.schemas.portfolio import (
    CAMPAIGN_BUILD_LIMIT,
    HouseholdDedupConfig,
    PortfolioCriteria,
)
from backend.services.audit_store import build_safe_audit_metadata
from backend.services.campaign_targeting import (
    CAMPAIGN_TREATMENT_ALGORITHM_VERSION,
    campaign_treatment_fingerprint,
)
from backend.services.campaign_treatment_runtime import require_campaign_treatment_runtime
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.lead_query_helpers import apply_cohort_equity_floor
from backend.services.repositories.databricks_lead_cohorts import (
    CampaignTreatmentBuildRejected,
    LeadCohortFilters,
    LeadCohortQueries,
)

CAMPAIGN_TREATMENT_ALGORITHM_VERSION_V2 = CAMPAIGN_TREATMENT_ALGORITHM_VERSION
_BUILD_LEASE_MINUTES = 5

_CAMPAIGN_LOOKUP_SQL = """
SELECT
  c.campaign_id::text,
  c.owner_email,
  c.request_payload_hash,
  c.creation_response,
  c.treatment_state,
  c.treatment_materialization_id::text,
  c.treatment_algorithm_version,
  c.treatment_contract_fingerprint,
  c.treatment_fingerprint,
  c.treatment_source_snapshot_id,
  c.treatment_delta_version,
  c.treatment_assignment_digest,
  c.treatment_candidate_count,
  c.treatment_selected_primary_count,
  c.treatment_count,
  c.treatment_holdout_count,
  c.treatment_materialized_at,
  c.treatment_build_lease_until,
  (
    SELECT a.audit_id::text
    FROM mip_app.action_audit a
    WHERE a.entity_type = 'campaign'
      AND a.entity_id = c.campaign_id::text
      AND a.event_type = %(event_type)s
    ORDER BY a.event_at ASC
    LIMIT 1
  ) AS audit_id
FROM mip_app.campaigns c
WHERE c.owner_email = %(owner_email)s
  AND c.idempotency_key = %(idempotency_key)s
LIMIT 1
"""

_CAMPAIGN_RESERVE_SQL = f"""
INSERT INTO mip_app.campaigns (
  name, owner_email, status, criteria, suppression_policy, message_variants,
  channel_cascade, send_window, holdout, roi_assumptions, household_dedup,
  household_summary, idempotency_key, request_payload_hash,
  treatment_state, treatment_materialization_id, treatment_algorithm_version,
  treatment_contract_fingerprint, treatment_build_lease_until, updated_at
) VALUES (
  %(name)s, %(owner_email)s, 'draft', %(criteria)s::jsonb,
  %(suppression_policy)s::jsonb, %(message_variants)s::jsonb,
  %(channel_cascade)s::jsonb, %(send_window)s::jsonb, %(holdout)s::jsonb,
  %(roi_assumptions)s::jsonb, %(household_dedup)s::jsonb, '{{}}'::jsonb,
  %(idempotency_key)s, %(request_payload_hash)s, 'building',
  %(materialization_id)s::uuid, '{CAMPAIGN_TREATMENT_ALGORITHM_VERSION_V2}',
  %(contract_fingerprint)s, now() + interval '{_BUILD_LEASE_MINUTES} minutes', now()
)
ON CONFLICT (owner_email, idempotency_key)
  WHERE idempotency_key IS NOT NULL
DO NOTHING
RETURNING campaign_id::text, treatment_materialization_id::text,
          request_payload_hash, treatment_state
"""

_CAMPAIGN_RECLAIM_SQL = f"""
UPDATE mip_app.campaigns
SET treatment_materialization_id = %(new_materialization_id)s::uuid,
    treatment_build_lease_until = now() + interval '{_BUILD_LEASE_MINUTES} minutes',
    updated_at = now()
WHERE campaign_id = %(campaign_id)s::uuid
  AND status = 'draft'
  AND treatment_state = 'building'
  AND treatment_materialization_id = %(materialization_id)s::uuid
  AND treatment_contract_fingerprint = %(contract_fingerprint)s
  AND treatment_build_lease_until <= now()
RETURNING campaign_id::text, treatment_materialization_id::text,
          request_payload_hash, treatment_state
"""

_CAMPAIGN_FAIL_SQL = """
UPDATE mip_app.campaigns
SET treatment_state = 'failed',
    treatment_build_lease_until = NULL,
    updated_at = now()
WHERE campaign_id = %(campaign_id)s::uuid
  AND treatment_state = 'building'
  AND treatment_materialization_id = %(materialization_id)s::uuid
  AND treatment_contract_fingerprint = %(contract_fingerprint)s
RETURNING campaign_id::text, treatment_state
"""

_CAMPAIGN_FINALIZE_SQL = """
WITH finalized AS (
  UPDATE mip_app.campaigns
  SET treatment_state = 'ready',
      treatment_fingerprint = %(treatment_fingerprint)s,
      treatment_source_snapshot_id = %(source_snapshot_id)s,
      treatment_delta_version = %(delta_version)s,
      treatment_assignment_digest = %(assignment_digest)s,
      treatment_candidate_count = %(candidate_count)s,
      treatment_selected_primary_count = %(selected_primary_count)s,
      treatment_count = %(treatment_count)s,
      treatment_holdout_count = %(holdout_count)s,
      treatment_materialized_at = %(materialized_at)s,
      treatment_build_lease_until = NULL,
      household_summary = %(household_summary)s::jsonb,
      creation_response = %(creation_response)s::jsonb,
      updated_at = now()
  WHERE campaign_id = %(campaign_id)s::uuid
    AND treatment_state = 'building'
    AND treatment_materialization_id = %(materialization_id)s::uuid
    AND treatment_contract_fingerprint = %(contract_fingerprint)s
    AND request_payload_hash = %(request_payload_hash)s
  RETURNING campaign_id, owner_email, creation_response, request_payload_hash
),
inserted_variants AS (
  INSERT INTO mip_app.campaign_message_variants (
    campaign_id, variant_name, channel, subject, body, weight_pct,
    generation_mode, generator_label, provenance_key_id,
    provenance_issued_at, provenance_expires_at, provenance_copy_hash,
    provenance_criteria_fingerprint, provenance_performance_fingerprint,
    provenance_token_digest
  )
  SELECT
    finalized.campaign_id, variant.variant_name, variant.channel,
    variant.subject, variant.body, variant.weight_pct,
    variant.generation_mode, variant.generator_label, variant.provenance_key_id,
    variant.provenance_issued_at, variant.provenance_expires_at,
    variant.provenance_copy_hash, variant.provenance_criteria_fingerprint,
    variant.provenance_performance_fingerprint, variant.provenance_token_digest
  FROM finalized
  CROSS JOIN jsonb_to_recordset(%(variant_rows)s::jsonb) AS variant(
    variant_name TEXT, channel TEXT, subject TEXT, body TEXT, weight_pct NUMERIC,
    generation_mode TEXT, generator_label TEXT, provenance_key_id TEXT,
    provenance_issued_at TIMESTAMPTZ, provenance_expires_at TIMESTAMPTZ,
    provenance_copy_hash TEXT, provenance_criteria_fingerprint TEXT,
    provenance_performance_fingerprint TEXT, provenance_token_digest TEXT
  )
  ON CONFLICT (campaign_id, variant_name, channel) DO NOTHING
  RETURNING campaign_id
),
inserted_audit AS (
  INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    request_id, correlation_id, evidence_ids, metadata
  )
  SELECT
    %(event_type)s, finalized.owner_email, 'campaign', finalized.campaign_id::text,
    %(idempotency_key)s, %(correlation_id)s, ARRAY[]::TEXT[],
    jsonb_set(%(audit_metadata)s::jsonb, '{campaign_id}',
              to_jsonb(finalized.campaign_id::text), true)
  FROM finalized
  ON CONFLICT DO NOTHING
  RETURNING audit_id::text
)
SELECT finalized.campaign_id::text, finalized.request_payload_hash,
       finalized.creation_response, inserted_audit.audit_id,
       (SELECT COUNT(*) FROM inserted_variants) AS variant_count
FROM finalized
LEFT JOIN inserted_audit ON TRUE
"""


@dataclass(frozen=True)
class CampaignTreatmentCreateSpec:
    name: str
    owner_email: str
    idempotency_key: str
    request_payload_hash: str
    criteria: dict[str, Any]
    suppression_policy: dict[str, Any] = field(default_factory=dict)
    holdout: dict[str, Any] | None = None
    household_dedup: HouseholdDedupConfig = field(default_factory=HouseholdDedupConfig)
    message_variants: list[dict[str, Any]] = field(default_factory=list)
    channel_cascade: list[dict[str, Any]] = field(default_factory=list)
    send_window: dict[str, Any] = field(default_factory=dict)
    roi_assumptions: dict[str, Any] | None = None
    variant_rows: list[dict[str, Any]] = field(default_factory=list)
    event_type: str = "PORTFOLIO_CREATE"
    correlation_id: str | None = None
    audit_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignTreatmentCreateResult:
    campaign_id: str
    creation_response: dict[str, Any]
    audit_id: str | None
    replayed: bool


def _strings(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    normalized = [str(item).strip() for item in value if str(item).strip()]
    return normalized or None


def _numeric_floor(filters: dict[str, Any], key: str) -> int | None:
    """Return a stored reviewed floor, or raise if the criteria are malformed.

    The range comes from the canonical vocabulary rather than the call site, so
    the approved treatment set is bounded exactly like the cohort the answer
    handed off -- including the signed ``min_rate_spread_bps`` floor.
    """

    raw = filters.get(key)
    if raw is None or raw == "":
        return None
    if not is_reviewed_numeric_floor(key, raw):
        raise ValueError(f"campaign criteria {key} filter is invalid")
    return int(raw)


def cohort_filters_from_campaign_criteria(criteria: dict[str, Any]) -> LeadCohortFilters:
    """Project reviewed Portfolio/Genie criteria into the canonical lead filters."""

    if criteria.get("source") not in {"genie", "trusted_sql"}:
        return LeadCohortFilters(
            segment=None,
            portfolio_criteria=PortfolioCriteria.model_validate(criteria),
        )
    nested = criteria.get("result_filters")
    filters = dict(nested) if isinstance(nested, dict) else {}
    top_level_ids = _strings(criteria.get("borrower_ids"))
    filter_ids = _strings(filters.get("borrower_ids"))
    borrower_ids = (
        sorted(set(top_level_ids) & set(filter_ids))
        if top_level_ids and filter_ids
        else top_level_ids or filter_ids
    )
    portfolio_raw = filters.get("portfolio_criteria")
    # Reviewed numeric floors travel with the answer that built this draft, so
    # the treatment set is the population the user approved -- not the broader
    # one that ignoring the thresholds would materialize.
    portfolio_criteria = apply_cohort_equity_floor(
        PortfolioCriteria.model_validate(portfolio_raw if isinstance(portfolio_raw, dict) else {}),
        _numeric_floor(filters, "min_equity_pct"),
    )
    return LeadCohortFilters(
        segment=str(filters.get("segment") or "").strip() or None,
        state=str(filters.get("state") or "").strip() or None,
        zip_code=str(filters.get("zip") or "").strip() or None,
        county_fips=str(filters.get("county") or "").strip() or None,
        county_fipses=_strings(filters.get("counties")),
        state_codes=_strings(filters.get("states")),
        zip_codes=_strings(filters.get("zips")),
        # Without this the approved treatment set materializes the STATE
        # the pairs live in rather than the cities the answer described.
        city_states=_strings(filters.get(GENIE_CITY_FILTER_KEY)),
        borrower_ids=borrower_ids,
        segment_codes=_strings(filters.get("segment_codes")),
        segment_mode=str(filters.get("segment_mode") or "any"),
        target_lender_ref=str(filters.get("target_lender_ref") or "").strip() or None,
        funnel_stage=str(filters.get("funnel_stage") or "").strip() or None,
        portfolio_criteria=portfolio_criteria,
        min_opportunity_score=_numeric_floor(filters, "min_opportunity_score"),
        min_rate_spread_bps=_numeric_floor(filters, "min_rate_spread_bps"),
        approval_status=str(filters.get("approval_status") or "").strip() or None,
        outreach_status=str(filters.get("outreach_status") or "").strip() or None,
        aged_days=int(filters["aged_days"]) if filters.get("aged_days") is not None else None,
    )


def _frequency_cap_days(policy: dict[str, Any]) -> int:
    raw = policy.get("frequency_cap_days", 30)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 30 <= raw <= 365:
        raise ValueError("campaign suppression contract is invalid")
    return raw


def _holdout_basis_points(holdout: dict[str, Any] | None) -> int:
    if holdout is None:
        return 0
    if holdout.get("method") != "hash_modulo":
        raise ValueError("campaign holdout contract is invalid")
    raw = holdout.get("size_pct")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ValueError("campaign holdout contract is invalid")
    try:
        percentage = Decimal(str(raw))
        basis_points_decimal = percentage * Decimal(100)
    except (InvalidOperation, ValueError):
        raise ValueError("campaign holdout contract is invalid") from None
    if (
        not percentage.is_finite()
        or basis_points_decimal != basis_points_decimal.to_integral_value()
    ):
        raise ValueError("campaign holdout contract is invalid")
    basis_points = int(basis_points_decimal)
    if not 0 <= basis_points <= 5_000:
        raise ValueError("campaign holdout contract is invalid")
    return basis_points


class CampaignTreatmentCoordinator:
    """Cross-store state machine for one campaign creation request."""

    def __init__(
        self,
        *,
        lakebase: LakebaseClient,
        cohort_queries: LeadCohortQueries,
    ) -> None:
        self._lakebase = lakebase
        self._cohort_queries = cohort_queries

    def create(self, spec: CampaignTreatmentCreateSpec) -> CampaignTreatmentCreateResult:
        # Deployment-promotion write gate (2026-07-30 restructure): every
        # reserve/materialize/finalize path funnels through here, so this one
        # per-request check keeps treatment writes fail-closed on a baseline
        # (un-promoted) deploy without the old boot-time process kill. UC
        # MODIFY quiesce remains the authoritative backstop underneath.
        require_campaign_treatment_runtime()
        contract_fingerprint = campaign_treatment_fingerprint(
            json_contract_version=1,
            criteria=spec.criteria,
            suppression_policy=spec.suppression_policy,
            holdout=spec.holdout,
            household_dedup=spec.household_dedup.model_dump(mode="json"),
        )
        lookup_params = {
            "owner_email": spec.owner_email,
            "idempotency_key": spec.idempotency_key,
            "event_type": spec.event_type,
        }
        existing = self._lakebase.fetchone(_CAMPAIGN_LOOKUP_SQL, lookup_params)
        if existing is not None:
            return self._existing_or_reclaim(
                spec,
                existing,
                contract_fingerprint=contract_fingerprint,
            )

        materialization_id = str(uuid4())
        reserve_params = self._reserve_params(
            spec,
            materialization_id=materialization_id,
            contract_fingerprint=contract_fingerprint,
        )
        reserved = self._lakebase.fetchone(_CAMPAIGN_RESERVE_SQL, reserve_params)
        if reserved is None:
            winner = self._lakebase.fetchone(_CAMPAIGN_LOOKUP_SQL, lookup_params)
            if winner is None:
                raise LakebaseError(
                    "campaign reservation returned no row and idempotency winner was not visible"
                )
            return self._existing_or_reclaim(
                spec,
                winner,
                contract_fingerprint=contract_fingerprint,
            )
        return self._materialize_and_finalize(
            spec,
            campaign_id=str(reserved["campaign_id"]),
            materialization_id=str(reserved["treatment_materialization_id"]),
            contract_fingerprint=contract_fingerprint,
        )

    def _existing_or_reclaim(
        self,
        spec: CampaignTreatmentCreateSpec,
        row: dict[str, Any],
        *,
        contract_fingerprint: str,
    ) -> CampaignTreatmentCreateResult:
        if str(row.get("request_payload_hash") or "") != spec.request_payload_hash:
            raise ValueError("Idempotency-Key already belongs to a different campaign payload")
        state = str(row.get("treatment_state") or "legacy_unbound")
        if state == "ready":
            response = row.get("creation_response")
            if isinstance(response, str):
                response = json.loads(response)
            if not isinstance(response, dict):
                raise LakebaseError("ready campaign is missing its creation response")
            return CampaignTreatmentCreateResult(
                campaign_id=str(row["campaign_id"]),
                creation_response=response,
                audit_id=str(row.get("audit_id") or "") or None,
                replayed=True,
            )
        if state != "building":
            raise ValueError("Campaign must be rebuilt before it can be used")
        recovered = self._cohort_queries.load_campaign_treatment_manifest(
            campaign_id=str(row["campaign_id"]),
            materialization_id=str(row["treatment_materialization_id"]),
            request_payload_hash=spec.request_payload_hash,
            contract_fingerprint=contract_fingerprint,
        )
        if recovered is not None:
            return self._finalize_manifest(
                spec,
                campaign_id=str(row["campaign_id"]),
                materialization_id=str(row["treatment_materialization_id"]),
                contract_fingerprint=contract_fingerprint,
                manifest=recovered,
            )
        lease = row.get("treatment_build_lease_until")
        if isinstance(lease, str):
            lease = datetime.fromisoformat(lease.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        if isinstance(lease, datetime):
            if lease.tzinfo is None:
                lease = lease.replace(tzinfo=UTC)
            if lease > now:
                raise LakebaseError("campaign treatment materialization is already in progress")
        reclaim = self._lakebase.fetchone(
            _CAMPAIGN_RECLAIM_SQL,
            {
                "campaign_id": row["campaign_id"],
                "materialization_id": row["treatment_materialization_id"],
                "new_materialization_id": str(uuid4()),
                "contract_fingerprint": contract_fingerprint,
            },
        )
        if reclaim is None:
            raise LakebaseError("campaign treatment materialization is already in progress")
        return self._materialize_and_finalize(
            spec,
            campaign_id=str(row["campaign_id"]),
            materialization_id=str(reclaim["treatment_materialization_id"]),
            contract_fingerprint=contract_fingerprint,
        )

    def _materialize_and_finalize(
        self,
        spec: CampaignTreatmentCreateSpec,
        *,
        campaign_id: str,
        materialization_id: str,
        contract_fingerprint: str,
    ) -> CampaignTreatmentCreateResult:
        try:
            manifest = self._cohort_queries.materialize_campaign_treatment(
                cohort_filters_from_campaign_criteria(spec.criteria),
                campaign_id=campaign_id,
                materialization_id=materialization_id,
                request_payload_hash=spec.request_payload_hash,
                contract_fingerprint=contract_fingerprint,
                frequency_cap_days=_frequency_cap_days(spec.suppression_policy),
                holdout_basis_points=_holdout_basis_points(spec.holdout),
                household_dedup_enabled=spec.household_dedup.enabled,
            )
        except CampaignTreatmentBuildRejected:
            with suppress(Exception):
                self._lakebase.fetchone(
                    _CAMPAIGN_FAIL_SQL,
                    {
                        "campaign_id": campaign_id,
                        "materialization_id": materialization_id,
                        "contract_fingerprint": contract_fingerprint,
                    },
                )
            raise
        except Exception:
            recovered: dict[str, Any] | None = None
            with suppress(Exception):
                recovered = self._cohort_queries.load_campaign_treatment_manifest(
                    campaign_id=campaign_id,
                    materialization_id=materialization_id,
                    request_payload_hash=spec.request_payload_hash,
                    contract_fingerprint=contract_fingerprint,
                )
            if recovered is not None:
                return self._finalize_manifest(
                    spec,
                    campaign_id=campaign_id,
                    materialization_id=materialization_id,
                    contract_fingerprint=contract_fingerprint,
                    manifest=recovered,
                )
            raise
        return self._finalize_manifest(
            spec,
            campaign_id=campaign_id,
            materialization_id=materialization_id,
            contract_fingerprint=contract_fingerprint,
            manifest=manifest,
        )

    def _finalize_manifest(
        self,
        spec: CampaignTreatmentCreateSpec,
        *,
        campaign_id: str,
        materialization_id: str,
        contract_fingerprint: str,
        manifest: dict[str, Any],
    ) -> CampaignTreatmentCreateResult:
        candidate_count = int(manifest.get("candidate_count") or 0)
        selected_primary_count = int(manifest.get("selected_primary_count") or 0)
        household_summary = {
            "enabled": spec.household_dedup.enabled,
            "candidate_borrower_count": candidate_count,
            "selected_primary_count": selected_primary_count,
            "suppressed_co_owner_count": candidate_count - selected_primary_count,
            "household_count": int(manifest.get("household_count") or 0),
            "owner_link_household_count": int(manifest.get("owner_link_household_count") or 0),
            "mailing_address_household_count": int(
                manifest.get("mailing_address_household_count") or 0
            ),
            "singleton_household_count": int(manifest.get("singleton_household_count") or 0),
            "primary_contact_strategy": spec.household_dedup.primary_contact_strategy,
            "source_assets": ["mip.gold.household_rollup", "mip.gold.borrower_360"],
        }
        creation_response = {
            "name": spec.name,
            "marketable_population": int(manifest.get("treatment_count") or 0),
            "campaign_build_limit": CAMPAIGN_BUILD_LIMIT,
            "campaign_build_eligible": True,
            "household_summary": household_summary,
        }
        audit_payload = {
            **spec.audit_metadata,
            "treatment_algorithm_version": CAMPAIGN_TREATMENT_ALGORITHM_VERSION_V2,
            "treatment_contract_fingerprint": contract_fingerprint,
            "treatment_fingerprint": manifest["treatment_fingerprint"],
            "source_snapshot_id": manifest["source_snapshot_id"],
            "candidate_count": candidate_count,
            "selected_primary_count": selected_primary_count,
            "treatment_count": int(manifest.get("treatment_count") or 0),
            "holdout_count": int(manifest.get("holdout_count") or 0),
        }
        audit_action = str(audit_payload.pop("action", "campaign.create"))
        audit_metadata = build_safe_audit_metadata(
            audit_payload,
            action=audit_action,
        )
        params = {
            "campaign_id": campaign_id,
            "materialization_id": materialization_id,
            "contract_fingerprint": contract_fingerprint,
            "request_payload_hash": spec.request_payload_hash,
            "treatment_fingerprint": manifest["treatment_fingerprint"],
            "source_snapshot_id": manifest["source_snapshot_id"],
            "delta_version": int(manifest["delta_version"]),
            "assignment_digest": manifest["assignment_digest"],
            "candidate_count": candidate_count,
            "selected_primary_count": selected_primary_count,
            "treatment_count": int(manifest.get("treatment_count") or 0),
            "holdout_count": int(manifest.get("holdout_count") or 0),
            "materialized_at": manifest.get("materialized_at") or datetime.now(UTC),
            "household_summary": json.dumps(household_summary, sort_keys=True),
            "creation_response": json.dumps(creation_response, sort_keys=True),
            "variant_rows": json.dumps(spec.variant_rows, sort_keys=True, default=str),
            "event_type": spec.event_type,
            "idempotency_key": spec.idempotency_key,
            "correlation_id": spec.correlation_id,
            "audit_metadata": json.dumps(audit_metadata, sort_keys=True, default=str),
        }
        finalized = self._lakebase.fetchone(_CAMPAIGN_FINALIZE_SQL, params)
        if finalized is None:
            existing = self._lakebase.fetchone(
                _CAMPAIGN_LOOKUP_SQL,
                {
                    "owner_email": spec.owner_email,
                    "idempotency_key": spec.idempotency_key,
                    "event_type": spec.event_type,
                },
            )
            if existing is None:
                raise LakebaseError("campaign treatment finalized without a visible campaign")
            return self._existing_or_reclaim(
                spec,
                existing,
                contract_fingerprint=contract_fingerprint,
            )
        return CampaignTreatmentCreateResult(
            campaign_id=campaign_id,
            creation_response=creation_response,
            audit_id=str(finalized.get("audit_id") or "") or None,
            replayed=False,
        )

    @staticmethod
    def _reserve_params(
        spec: CampaignTreatmentCreateSpec,
        *,
        materialization_id: str,
        contract_fingerprint: str,
    ) -> dict[str, Any]:
        return {
            "name": spec.name,
            "owner_email": spec.owner_email,
            "criteria": json.dumps(spec.criteria, sort_keys=True),
            "suppression_policy": json.dumps(spec.suppression_policy, sort_keys=True),
            "message_variants": json.dumps(spec.message_variants, sort_keys=True, default=str),
            "channel_cascade": json.dumps(spec.channel_cascade, sort_keys=True),
            "send_window": json.dumps(spec.send_window, sort_keys=True),
            "holdout": (
                None if spec.holdout is None else json.dumps(spec.holdout, sort_keys=True)
            ),
            "roi_assumptions": (
                None
                if spec.roi_assumptions is None
                else json.dumps(spec.roi_assumptions, sort_keys=True)
            ),
            "household_dedup": json.dumps(spec.household_dedup.model_dump(mode="json")),
            "idempotency_key": spec.idempotency_key,
            "request_payload_hash": spec.request_payload_hash,
            "materialization_id": materialization_id,
            "contract_fingerprint": contract_fingerprint,
        }


__all__ = [
    "CAMPAIGN_TREATMENT_ALGORITHM_VERSION_V2",
    "CampaignTreatmentCoordinator",
    "CampaignTreatmentCreateResult",
    "CampaignTreatmentCreateSpec",
    "cohort_filters_from_campaign_criteria",
]
