"""Campaign persistence for the Databricks portfolio repository: idempotent
create, listing, get, and the governed status PATCH with replay."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from backend.schemas.campaign_status import validate_campaign_status_transition
from backend.schemas.portfolio import (
    CAMPAIGN_BUILD_LIMIT,
    CampaignListResponse,
    CampaignStatusPatchRequest,
    CampaignSummary,
    HouseholdDedupSummary,
    PortfolioCreateRequest,
    PortfolioCreateResponse,
)
from backend.services.audit_store import build_safe_audit_metadata
from backend.services.campaign_intelligence import (
    campaign_criteria_fingerprint,
    inspect_campaign_variant_provenance,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.lakebase import (
    LakebaseError,
)
from backend.services.lakebase import (
    get_lakebase_client as _get_lakebase_client_default,
)
from backend.services.observability import get_correlation_id
from backend.services.repositories.databricks_portfolio_campaign_mappers import (
    _project_campaign_name_or_default,
    campaign_summary_from_row,
)
from backend.services.repositories.databricks_portfolio_campaign_sql import (
    _PortfolioCampaignSql,
)
from backend.services.repositories.databricks_portfolio_predicates import (
    coerce_utc_datetime,
    json_value,
)

_CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS = 3
_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S = 0.01
_CAMPAIGN_STATUS_LOOKUP_ATTEMPTS = 3


def _get_lakebase_client():
    """Preserve the historical ``databricks_repo.get_lakebase_client`` patch seam."""
    facade = sys.modules.get("backend.services.repositories.databricks_repo")
    patched = getattr(facade, "get_lakebase_client", None) if facade is not None else None
    if callable(patched):
        return patched()
    return _get_lakebase_client_default()


class _PortfolioCampaignPersistence(_PortfolioCampaignSql):
    """Lakebase campaign writes and reads shared by the portfolio repository.

    ``_client`` is bound by ``DatabricksPortfolioRepository.__init__``; it is
    annotated (never assigned) here so the campaign methods type-check.
    """

    _client: DatabricksSqlClient

    def create(
        self,
        payload: PortfolioCreateRequest,
        *,
        actor: str | None = None,
        idempotency_key: str,
    ) -> PortfolioCreateResponse:
        owner_email = actor or "unknown"
        request_payload = payload.model_dump(mode="json", exclude_none=False)
        request_payload_hash = hashlib.sha256(
            json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        lakebase = _get_lakebase_client()
        criteria_fingerprint = campaign_criteria_fingerprint(payload.criteria)
        variant_rows: list[dict[str, object]] = []
        for variant in payload.message_variants:
            if not str(variant.get("body") or "").strip():
                continue
            requested_mode = str(variant.get("generation_mode") or "")
            requested_label = str(variant.get("generator_label") or "")
            provenance_proof = inspect_campaign_variant_provenance(
                variant,
                criteria_fingerprint=criteria_fingerprint,
            )
            if provenance_proof is None:
                raise ValueError(
                    "model-generated campaign provenance is missing, expired, or invalid"
                )
            generation_mode = provenance_proof.generation_mode
            generator_label = provenance_proof.generator_label
            if requested_mode != generation_mode or requested_label != generator_label:
                raise ValueError(
                    "model-generated campaign provenance does not match the server proof"
                )
            variant_rows.append(
                {
                    "variant_name": str(
                        variant.get("variant_name") or variant.get("name") or "default"
                    )[:64],
                    "channel": str(variant.get("channel") or "email"),
                    "subject": variant.get("subject"),
                    "body": str(variant.get("body") or ""),
                    "weight_pct": variant.get("weight_pct"),
                    "generation_mode": generation_mode,
                    "generator_label": generator_label,
                    "provenance_key_id": (provenance_proof.key_id if provenance_proof else None),
                    "provenance_issued_at": (
                        datetime.fromtimestamp(provenance_proof.issued_at, UTC).isoformat()
                        if provenance_proof
                        else None
                    ),
                    "provenance_expires_at": (
                        datetime.fromtimestamp(provenance_proof.expires_at, UTC).isoformat()
                        if provenance_proof
                        else None
                    ),
                    "provenance_copy_hash": (
                        provenance_proof.copy_hash if provenance_proof else None
                    ),
                    "provenance_criteria_fingerprint": (
                        provenance_proof.criteria_fingerprint if provenance_proof else None
                    ),
                    "provenance_performance_fingerprint": (
                        provenance_proof.performance_fingerprint if provenance_proof else None
                    ),
                    "provenance_token_digest": (
                        provenance_proof.token_digest if provenance_proof else None
                    ),
                }
            )
        generation_modes = {
            str(variant.get("generation_mode") or "operator") for variant in variant_rows
        }
        generator_labels = {
            str(variant.get("generator_label") or "Operator edited") for variant in variant_rows
        }
        campaign_generation_mode = (
            next(iter(generation_modes))
            if len(generation_modes) == 1
            else "mixed"
            if generation_modes
            else "operator"
        )
        campaign_generator_label = (
            next(iter(generator_labels))
            if len(generator_labels) == 1
            else "Multiple generators"
            if generator_labels
            else "Operator edited"
        )
        variant_provenance: list[dict[str, object]] = []
        for variant in variant_rows:
            proof_row: dict[str, object] = {
                "variant_name": variant["variant_name"],
                "generation_mode": variant["generation_mode"],
                "generator_label": variant["generator_label"],
            }
            if variant["provenance_key_id"] is not None:
                proof_row.update(
                    {
                        "provenance_key_id": variant["provenance_key_id"],
                        "provenance_issued_at": variant["provenance_issued_at"],
                        "provenance_expires_at": variant["provenance_expires_at"],
                        "provenance_copy_hash": variant["provenance_copy_hash"],
                        "provenance_criteria_fingerprint": variant[
                            "provenance_criteria_fingerprint"
                        ],
                        "provenance_performance_fingerprint": variant[
                            "provenance_performance_fingerprint"
                        ],
                    }
                )
            variant_provenance.append(proof_row)
        # Local imports avoid a module cycle: LeadCohortQueries reuses this
        # module's reviewed Portfolio predicate compiler.
        from backend.services.campaign_treatment import (
            CampaignTreatmentCoordinator,
            CampaignTreatmentCreateSpec,
        )
        from backend.services.repositories.databricks_lead_cohorts import LeadCohortQueries

        coordinator = CampaignTreatmentCoordinator(
            lakebase=lakebase,
            cohort_queries=LeadCohortQueries(self._client, cache_ttl_s=0),
        )
        result = coordinator.create(
            CampaignTreatmentCreateSpec(
                name=payload.name,
                owner_email=owner_email,
                idempotency_key=idempotency_key,
                request_payload_hash=request_payload_hash,
                criteria=payload.criteria.model_dump(mode="json", exclude_none=True),
                suppression_policy=dict(payload.suppression_policy),
                holdout=dict(payload.holdout) if payload.holdout is not None else None,
                household_dedup=payload.household_dedup,
                message_variants=[dict(row) for row in variant_rows],
                channel_cascade=[dict(row) for row in payload.channel_cascade],
                send_window=dict(payload.send_window),
                roi_assumptions=(
                    dict(payload.roi_assumptions) if payload.roi_assumptions is not None else None
                ),
                variant_rows=[dict(row) for row in variant_rows],
                event_type="PORTFOLIO_CREATE",
                correlation_id=get_correlation_id(),
                audit_metadata=build_safe_audit_metadata(
                    {
                        "source": "portfolio_builder",
                        "portfolio_criteria": payload.criteria.model_dump(exclude_none=True),
                        "suppression_policy": payload.suppression_policy,
                        "channel_cascade": payload.channel_cascade,
                        "send_window": payload.send_window,
                        "holdout": payload.holdout,
                        "roi_assumptions": payload.roi_assumptions,
                        "dedupe_unit": payload.household_dedup.dedupe_unit,
                        "household_dedup_enabled": payload.household_dedup.enabled,
                        "household_primary_strategy": (
                            payload.household_dedup.primary_contact_strategy
                        ),
                        "campaign_generation_mode": campaign_generation_mode,
                        "generator_label": campaign_generator_label,
                        "variant_provenance": variant_provenance,
                    },
                    action="portfolio.create",
                ),
            )
        )
        response = result.creation_response
        try:
            household = HouseholdDedupSummary.model_validate(
                response.get("household_summary") or {}
            )
            return PortfolioCreateResponse(
                portfolio_id=result.campaign_id,
                campaign_id=result.campaign_id,
                name=_project_campaign_name_or_default(response.get("name")),
                marketable_population=int(response.get("marketable_population") or 0),
                campaign_build_limit=int(
                    response.get("campaign_build_limit") or CAMPAIGN_BUILD_LIMIT
                ),
                campaign_build_eligible=bool(response.get("campaign_build_eligible", True)),
                household_summary=household,
                audit_event_id=result.audit_id,
            )
        except (TypeError, ValueError) as exc:
            raise LakebaseError("campaign treatment result is invalid") from exc

    def _campaign_create_response_after_insert_conflict(
        self,
        lakebase: Any,
        *,
        owner_email: str,
        idempotency_key: str,
        expected_payload_hash: str,
    ) -> PortfolioCreateResponse:
        params = {"owner_email": owner_email, "idempotency_key": idempotency_key}
        for attempt in range(_CAMPAIGN_IDEMPOTENCY_LOOKUP_ATTEMPTS):
            if attempt:
                time.sleep(_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S * attempt)
            existing = lakebase.fetchone(self._CAMPAIGN_IDEMPOTENCY_LOOKUP_SQL, params)
            if existing is not None:
                return self._campaign_create_response_from_idempotency_row(
                    existing,
                    expected_payload_hash=expected_payload_hash,
                )
        raise LakebaseError("campaign insert returned no row and idempotency lookup was empty")

    def _campaign_create_response_from_idempotency_row(
        self,
        row: dict[str, Any],
        *,
        expected_payload_hash: str,
    ) -> PortfolioCreateResponse:
        stored_hash = str(row.get("request_payload_hash") or "")
        if stored_hash != expected_payload_hash:
            raise ValueError("Idempotency-Key already belongs to a different campaign payload")
        try:
            response_data = self._json_value(row.get("creation_response"), {})
            if not isinstance(response_data, dict):
                raise LakebaseError("campaign idempotency record is missing its creation response")
            campaign_id = str(row.get("campaign_id") or "")
            if not campaign_id:
                raise LakebaseError("campaign idempotency record is missing its campaign id")
            household = HouseholdDedupSummary.model_validate(
                response_data.get("household_summary") or {}
            )
            return PortfolioCreateResponse(
                portfolio_id=campaign_id,
                campaign_id=campaign_id,
                name=_project_campaign_name_or_default(response_data.get("name")),
                marketable_population=int(response_data.get("marketable_population") or 0),
                household_summary=household,
                audit_event_id=str(row["audit_id"]) if row.get("audit_id") else None,
            )
        except LakebaseError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise LakebaseError("campaign idempotency record is invalid") from exc

    @staticmethod
    def _json_value(value: Any, fallback: Any) -> Any:
        return json_value(value, fallback)

    @classmethod
    def _campaign_from_row(cls, row: dict[str, Any]) -> CampaignSummary:
        return campaign_summary_from_row(row)

    def list_campaigns(
        self,
        *,
        owner_email: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> CampaignListResponse:
        rows = _get_lakebase_client().fetchall(
            self._CAMPAIGN_LIST_SQL,
            {"owner_email": owner_email, "status": status, "limit": max(1, min(limit, 200))},
            limit=max(1, min(limit, 200)),
        )
        return CampaignListResponse(campaigns=[self._campaign_from_row(row) for row in rows])

    def get(self, portfolio_id: str) -> dict[str, object]:
        row = _get_lakebase_client().fetchone(
            self._CAMPAIGN_GET_SQL,
            {"campaign_id": portfolio_id},
        )
        if row is None:
            return {}
        return self._campaign_from_row(row).model_dump()

    @staticmethod
    def _campaign_status_request_id(
        portfolio_id: str,
        payload: CampaignStatusPatchRequest,
        *,
        actor: str,
        caller_request_id: str | None = None,
        source_status: str | None = None,
        source_updated_at: Any = None,
    ) -> str:
        source_timestamp = coerce_utc_datetime(source_updated_at)
        transition_instance = {
            "source_status": source_status,
            "source_updated_at": (
                source_timestamp.isoformat()
                if source_timestamp is not None
                else str(source_updated_at or "")
            ),
        }
        canonical = json.dumps(
            {
                "actor": actor.strip().lower(),
                "campaign_id": portfolio_id,
                "request_identity": (
                    {"caller_request_id": caller_request_id}
                    if caller_request_id
                    else {
                        **transition_instance,
                        "rationale": payload.rationale,
                        "status": payload.status,
                    }
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"campaign-status-{digest}"

    def _campaign_status_replay(
        self,
        lakebase: Any,
        *,
        portfolio_id: str,
        actor: str,
        request_id: str,
        target_status: str,
        expected_status: str | None,
        rationale: str | None,
        attempts: int = 1,
    ) -> CampaignSummary | None:
        params = {
            "campaign_id": portfolio_id,
            "actor": actor,
            "request_id": request_id,
        }
        for attempt in range(max(1, attempts)):
            if attempt:
                time.sleep(_CAMPAIGN_IDEMPOTENCY_RETRY_DELAY_S * attempt)
            row = lakebase.fetchone(self._CAMPAIGN_STATUS_IDEMPOTENCY_LOOKUP_SQL, params)
            if row is None:
                continue
            if not row.get("audit_id"):
                raise LakebaseError("campaign status idempotency row is missing audit_id")
            audit_metadata = self._json_value(row.get("audit_metadata"), {})
            audited_rationale = (
                audit_metadata.get("rationale") if isinstance(audit_metadata, dict) else None
            )
            audited_expected_status = (
                audit_metadata.get("expected_status") if isinstance(audit_metadata, dict) else None
            )
            if (
                str(row.get("status") or "") != target_status
                or audited_expected_status != expected_status
                or audited_rationale != rationale
            ):
                raise HTTPException(
                    status_code=409,
                    detail="campaign status idempotency key belongs to a different transition",
                )
            return self._campaign_from_row(row)
        return None

    def patch_status(
        self,
        portfolio_id: str,
        payload: CampaignStatusPatchRequest,
        *,
        actor: str | None = None,
    ) -> CampaignSummary:
        lakebase = _get_lakebase_client()
        existing = lakebase.fetchone(
            self._CAMPAIGN_GET_SQL,
            {"campaign_id": portfolio_id},
        )
        if existing is None:
            raise LakebaseError("campaign status update returned no row")
        treatment_state = str(existing.get("treatment_state") or "legacy_unbound")
        can_quarantine = payload.status == "archived" and (
            treatment_state in {"legacy_unbound", "failed"}
            or (
                treatment_state == "building"
                and existing.get("treatment_build_lease_expired") is True
            )
        )
        if treatment_state != "ready" and not can_quarantine:
            raise HTTPException(
                status_code=409,
                detail="Campaign must be rebuilt before its lifecycle can advance.",
            )
        actor_email = actor or "unknown"
        # The request middleware accepts and echoes X-Correlation-ID. A caller
        # preserves that ID for lost-response replay and uses a new one for a
        # later lifecycle transition instance.
        correlation_id = get_correlation_id()
        current_status = str(existing.get("status") or "")
        request_id = self._campaign_status_request_id(
            portfolio_id,
            payload,
            actor=actor_email,
            caller_request_id=correlation_id,
            source_status=current_status,
            source_updated_at=existing.get("updated_at"),
        )
        replay = self._campaign_status_replay(
            lakebase,
            portfolio_id=portfolio_id,
            actor=actor_email,
            request_id=request_id,
            target_status=payload.status,
            expected_status=payload.expected_status,
            rationale=payload.rationale,
        )
        if replay is not None:
            return replay
        if payload.expected_status is not None and current_status != payload.expected_status:
            raise HTTPException(
                status_code=409,
                detail="campaign status changed; refresh before retrying",
            )
        if current_status == payload.status:
            raise HTTPException(
                status_code=409,
                detail="campaign status was already changed by a different request",
            )
        transition_evidence = validate_campaign_status_transition(
            payload,
            campaign_id=portfolio_id,
            current_status=current_status,
            actor=actor_email,
        )
        if payload.status in {"pending_review", "approved", "live", "active"}:
            criteria = self._json_value(existing.get("criteria"), {})
            suppression_policy = self._json_value(existing.get("suppression_policy"), {})
            criteria_ok = (
                isinstance(criteria, dict)
                and criteria.get("marketing_eligibility") == "Eligible only"
            )
            policy_ok = isinstance(suppression_policy, dict) and (
                suppression_policy.get("default") == "eligible_only"
                or suppression_policy.get("require_marketing_eligible") is True
                or str(suppression_policy.get("marketing_eligibility") or "")
                .strip()
                .lower()
                .replace(" ", "_")
                == "eligible_only"
            )
            if not (criteria_ok and policy_ok):
                raise ValueError(
                    "campaign cannot advance without an Eligible only contactability policy"
                )
        if payload.status in {"approved", "live", "active"}:
            campaign = self._campaign_from_row(existing)
            variants = campaign.message_variants
            if (
                not campaign.actionable
                or not variants
                or not all(variant.get("copy_verified_at_creation") is True for variant in variants)
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Campaign cannot advance with operator-edited or unverified copy; "
                        "regenerate every message variant with the governed campaign agent."
                    ),
                )
        metadata: dict[str, object] = {
            "status": payload.status,
            "expected_status": payload.expected_status,
            "rationale": payload.rationale,
            "treatment_state": treatment_state,
            "terminal_archive_without_treatment": can_quarantine,
        }
        evidence_ids: list[str] = []
        if transition_evidence is not None:
            metadata.update(
                {
                    "approval_id": transition_evidence.approval_id,
                    "occurred_at": transition_evidence.authorized_at,
                }
            )
            evidence_ids.append(transition_evidence.approval_id)
        row = lakebase.fetchone(
            self._CAMPAIGN_PATCH_SQL,
            {
                "campaign_id": portfolio_id,
                "current_status": current_status,
                "current_treatment_state": treatment_state,
                "status": payload.status,
                "actor": actor_email,
                "request_id": request_id,
                "correlation_id": correlation_id,
                "transition_at": datetime.now(UTC),
                "evidence_ids": evidence_ids,
                "metadata": json.dumps(
                    build_safe_audit_metadata(
                        metadata,
                        action="campaign.status_update",
                    ),
                    sort_keys=True,
                ),
            },
        )
        if row is None:
            replay = self._campaign_status_replay(
                lakebase,
                portfolio_id=portfolio_id,
                actor=actor_email,
                request_id=request_id,
                target_status=payload.status,
                expected_status=payload.expected_status,
                rationale=payload.rationale,
                attempts=_CAMPAIGN_STATUS_LOOKUP_ATTEMPTS,
            )
            if replay is not None:
                return replay
            raise HTTPException(
                status_code=409,
                detail="campaign status changed concurrently; refresh before retrying",
            )
        if not row.get("audit_id"):
            raise LakebaseError("campaign status update returned no audit row")
        return self._campaign_from_row(row)
