from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.schemas.portfolio import HouseholdDedupConfig, PortfolioCreateRequest
from backend.services.campaign_targeting import CAMPAIGN_TREATMENT_ALGORITHM_VERSION
from backend.services.campaign_treatment import (
    _CAMPAIGN_FAIL_SQL,
    _CAMPAIGN_FINALIZE_SQL,
    _CAMPAIGN_LOOKUP_SQL,
    _CAMPAIGN_RECLAIM_SQL,
    _CAMPAIGN_RESERVE_SQL,
    CAMPAIGN_TREATMENT_ALGORITHM_VERSION_V2,
    CampaignTreatmentCoordinator,
    CampaignTreatmentCreateSpec,
    _holdout_basis_points,
)
from backend.services.lakebase import LakebaseError
from backend.services.repositories.databricks_lead_cohorts import (
    CampaignTreatmentBuildRejected,
    LeadCohortQueries,
)

CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OLD_MATERIALIZATION_ID = "22222222-2222-4222-8222-222222222222"


@pytest.mark.parametrize(
    ("size_pct", "expected_basis_points"),
    [(0, 0), (1.1, 110), (12.34, 1_234), (20.1, 2_010), (50, 5_000)],
)
def test_holdout_basis_points_uses_decimal_safe_public_contract(
    size_pct: int | float,
    expected_basis_points: int,
) -> None:
    assert (
        _holdout_basis_points({"method": "hash_modulo", "size_pct": size_pct})
        == expected_basis_points
    )


def test_holdout_basis_points_accepts_every_public_hundredth_percent() -> None:
    for expected_basis_points in range(5_001):
        size_pct = expected_basis_points / 100
        assert (
            _holdout_basis_points({"method": "hash_modulo", "size_pct": size_pct})
            == expected_basis_points
        )


def test_schema_normalized_fractional_holdout_reaches_treatment_materialization() -> None:
    holdout = PortfolioCreateRequest(
        holdout={"method": "hash_modulo", "size_pct": "1.10"}
    ).holdout

    assert _holdout_basis_points(holdout) == 110


def test_reservation_preserves_absent_optional_json_as_sql_null() -> None:
    params = CampaignTreatmentCoordinator._reserve_params(
        _spec(),
        materialization_id=OLD_MATERIALIZATION_ID,
        contract_fingerprint="f" * 64,
    )

    assert params["holdout"] is None
    assert params["roi_assumptions"] is None


def _spec(**updates: Any) -> CampaignTreatmentCreateSpec:
    values: dict[str, Any] = {
        "name": "Ready campaign",
        "owner_email": "owner@example.com",
        "idempotency_key": "idem-1",
        "request_payload_hash": "a" * 64,
        "criteria": {"marketing_eligibility": "Eligible only"},
        "suppression_policy": {"default": "eligible_only", "frequency_cap_days": 60},
        "household_dedup": HouseholdDedupConfig(),
    }
    values.update(updates)
    return CampaignTreatmentCreateSpec(**values)


def _manifest() -> dict[str, object]:
    return {
        "delta_version": 17,
        "candidate_count": 10,
        "selected_primary_count": 8,
        "treatment_count": 7,
        "holdout_count": 1,
        "assignment_digest": "b" * 64,
        "treatment_fingerprint": "c" * 64,
        "source_snapshot_id": "d" * 64,
        "household_count": 8,
        "owner_link_household_count": 2,
        "mailing_address_household_count": 3,
        "singleton_household_count": 3,
        "materialized_at": datetime(2026, 7, 15, tzinfo=UTC),
    }


class _Cohorts:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.recovered_manifest: dict[str, object] | None = None

    def materialize_campaign_treatment(
        self, _filters: object, **kwargs: object
    ) -> dict[str, object]:
        self.calls.append(kwargs)
        self.recovered_manifest = _manifest()
        return dict(self.recovered_manifest)

    def load_campaign_treatment_manifest(self, **_kwargs: object) -> dict[str, object] | None:
        return dict(self.recovered_manifest) if self.recovered_manifest is not None else None


class _FailingCohorts(_Cohorts):
    def materialize_campaign_treatment(
        self, _filters: object, **kwargs: object
    ) -> dict[str, object]:
        self.calls.append(kwargs)
        raise CampaignTreatmentBuildRejected(
            "Campaign treatment exceeds the synchronous build limit"
        )


class _LostManifestReadCohorts(_Cohorts):
    def load_campaign_treatment_manifest(self, **_kwargs: object) -> dict[str, object] | None:
        raise TimeoutError("warehouse recovery read timed out")


class _Lakebase:
    def __init__(self, existing: dict[str, object] | None = None) -> None:
        self.row = existing
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.finalize_returns_none = False
        self.finalize_raises_once = False

    def fetchone(
        self,
        sql: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        values = params or {}
        self.calls.append((sql, values))
        if sql == _CAMPAIGN_LOOKUP_SQL:
            return self.row
        if sql == _CAMPAIGN_RESERVE_SQL:
            if self.row is not None:
                return None
            self.row = {
                "campaign_id": CAMPAIGN_ID,
                "request_payload_hash": values["request_payload_hash"],
                "treatment_state": "building",
                "treatment_materialization_id": values["materialization_id"],
                "treatment_contract_fingerprint": values["contract_fingerprint"],
                "treatment_build_lease_until": datetime.now(UTC) + timedelta(minutes=5),
            }
            return self.row
        if sql == _CAMPAIGN_RECLAIM_SQL:
            assert self.row is not None
            assert values["materialization_id"] == self.row["treatment_materialization_id"]
            self.row["treatment_materialization_id"] = values["new_materialization_id"]
            self.row["treatment_build_lease_until"] = datetime.now(UTC) + timedelta(minutes=5)
            return self.row
        if sql == _CAMPAIGN_FAIL_SQL:
            assert self.row is not None
            assert values["materialization_id"] == self.row["treatment_materialization_id"]
            self.row["treatment_state"] = "failed"
            return self.row
        if sql == _CAMPAIGN_FINALIZE_SQL:
            assert self.row is not None
            assert values["materialization_id"] == self.row["treatment_materialization_id"]
            if self.finalize_raises_once:
                self.finalize_raises_once = False
                raise LakebaseError("lost finalize response")
            self.row.update(
                {
                    "treatment_state": "ready",
                    "creation_response": values["creation_response"],
                    "audit_id": "audit-1",
                }
            )
            if self.finalize_returns_none:
                return None
            return {"campaign_id": CAMPAIGN_ID, "audit_id": "audit-1"}
        raise AssertionError("unexpected SQL")


def test_new_campaign_uses_one_materialization_id_through_finalize() -> None:
    lakebase = _Lakebase()
    cohorts = _Cohorts()

    result = CampaignTreatmentCoordinator(
        lakebase=lakebase,  # type: ignore[arg-type]
        cohort_queries=cohorts,  # type: ignore[arg-type]
    ).create(_spec())

    finalized = next(params for sql, params in lakebase.calls if sql == _CAMPAIGN_FINALIZE_SQL)
    assert cohorts.calls[0]["materialization_id"] == finalized["materialization_id"]
    assert result.campaign_id == CAMPAIGN_ID
    assert result.replayed is False
    assert result.creation_response["marketable_population"] == 7


def test_expired_lease_reclaim_rotates_materialization_fence() -> None:
    lakebase = _Lakebase(
        {
            "campaign_id": CAMPAIGN_ID,
            "request_payload_hash": "a" * 64,
            "treatment_state": "building",
            "treatment_materialization_id": OLD_MATERIALIZATION_ID,
            "treatment_build_lease_until": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    cohorts = _Cohorts()

    CampaignTreatmentCoordinator(
        lakebase=lakebase,  # type: ignore[arg-type]
        cohort_queries=cohorts,  # type: ignore[arg-type]
    ).create(_spec())

    new_id = str(cohorts.calls[0]["materialization_id"])
    assert new_id != OLD_MATERIALIZATION_ID
    assert "treatment_materialization_id = %(materialization_id)s::uuid" in _CAMPAIGN_FINALIZE_SQL
    assert any(sql == _CAMPAIGN_RECLAIM_SQL for sql, _params in lakebase.calls)


def test_active_lease_fails_without_recomputing_treatment() -> None:
    lakebase = _Lakebase(
        {
            "campaign_id": CAMPAIGN_ID,
            "request_payload_hash": "a" * 64,
            "treatment_state": "building",
            "treatment_materialization_id": OLD_MATERIALIZATION_ID,
            "treatment_build_lease_until": datetime.now(UTC) + timedelta(minutes=1),
        }
    )
    cohorts = _Cohorts()

    with pytest.raises(LakebaseError, match="already in progress"):
        CampaignTreatmentCoordinator(
            lakebase=lakebase,  # type: ignore[arg-type]
            cohort_queries=cohorts,  # type: ignore[arg-type]
        ).create(_spec())

    assert cohorts.calls == []


def test_materialization_failure_is_fenced_and_marks_campaign_failed() -> None:
    lakebase = _Lakebase()
    cohorts = _FailingCohorts()

    with pytest.raises(ValueError, match="synchronous build limit"):
        CampaignTreatmentCoordinator(
            lakebase=lakebase,  # type: ignore[arg-type]
            cohort_queries=cohorts,  # type: ignore[arg-type]
        ).create(_spec())

    assert lakebase.row is not None
    assert lakebase.row["treatment_state"] == "failed"
    assert any(sql == _CAMPAIGN_FAIL_SQL for sql, _params in lakebase.calls)
    assert not any(sql == _CAMPAIGN_FINALIZE_SQL for sql, _params in lakebase.calls)


def test_lost_finalize_response_reuses_ready_result_without_recompute() -> None:
    lakebase = _Lakebase()
    lakebase.finalize_returns_none = True
    cohorts = _Cohorts()

    result = CampaignTreatmentCoordinator(
        lakebase=lakebase,  # type: ignore[arg-type]
        cohort_queries=cohorts,  # type: ignore[arg-type]
    ).create(_spec())

    assert result.replayed is True
    assert len(cohorts.calls) == 1


def test_finalize_failure_retry_recovers_same_t0_after_source_mutation() -> None:
    lakebase = _Lakebase()
    lakebase.finalize_raises_once = True
    cohorts = _Cohorts()
    coordinator = CampaignTreatmentCoordinator(
        lakebase=lakebase,  # type: ignore[arg-type]
        cohort_queries=cohorts,  # type: ignore[arg-type]
    )

    with pytest.raises(LakebaseError, match="lost finalize response"):
        coordinator.create(_spec())
    assert lakebase.row is not None
    original_materialization_id = lakebase.row["treatment_materialization_id"]
    # A changed live source must not cause a T1 recomputation on retry.
    result = coordinator.create(_spec())

    assert result.campaign_id == CAMPAIGN_ID
    assert len(cohorts.calls) == 1
    assert lakebase.row["treatment_materialization_id"] == original_materialization_id


def test_lost_post_merge_manifest_read_recovers_without_second_merge() -> None:
    class _SqlClient:
        def __init__(self) -> None:
            self.execute_calls = 0
            self.one_calls = 0

        def execute(
            self,
            _statement: str,
            _parameters: dict[str, object] | None = None,
        ) -> list[dict[str, object]]:
            self.execute_calls += 1
            return []

        def execute_one(
            self,
            statement: str,
            _parameters: dict[str, object] | None = None,
        ) -> dict[str, object]:
            self.one_calls += 1
            if self.one_calls == 1:
                return {"selected_primary_count": 1, "source_snapshot_id": "d" * 64}
            if statement.startswith("DESCRIBE HISTORY"):
                return {"version": 17}
            if self.one_calls == 3:
                raise TimeoutError("lost manifest response")
            return {
                "manifest_rows": 1,
                "member_rows": 1,
                "distinct_member_rows": 1,
                "candidate_count": 1,
                "selected_primary_count": 1,
                "treatment_count": 1,
                "holdout_count": 0,
                "assignment_digest": "b" * 64,
                "treatment_fingerprint": "c" * 64,
                "source_snapshot_id": "d" * 64,
            }

    sql_client = _SqlClient()
    lakebase = _Lakebase()

    result = CampaignTreatmentCoordinator(
        lakebase=lakebase,  # type: ignore[arg-type]
        cohort_queries=LeadCohortQueries(sql_client, cache_ttl_s=0),  # type: ignore[arg-type]
    ).create(_spec())

    assert result.campaign_id == CAMPAIGN_ID
    assert sql_client.execute_calls == 1
    assert lakebase.row is not None
    assert lakebase.row["treatment_state"] == "ready"


def test_lost_recovery_read_never_rotates_expired_materialization() -> None:
    lakebase = _Lakebase(
        {
            "campaign_id": CAMPAIGN_ID,
            "request_payload_hash": "a" * 64,
            "treatment_state": "building",
            "treatment_materialization_id": OLD_MATERIALIZATION_ID,
            "treatment_build_lease_until": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    cohorts = _LostManifestReadCohorts()

    with pytest.raises(TimeoutError, match="recovery read timed out"):
        CampaignTreatmentCoordinator(
            lakebase=lakebase,  # type: ignore[arg-type]
            cohort_queries=cohorts,  # type: ignore[arg-type]
        ).create(_spec())

    assert lakebase.row is not None
    assert lakebase.row["treatment_materialization_id"] == OLD_MATERIALIZATION_ID
    assert not any(sql == _CAMPAIGN_RECLAIM_SQL for sql, _params in lakebase.calls)


def test_ready_replay_and_payload_mismatch_never_materialize() -> None:
    ready = {
        "campaign_id": CAMPAIGN_ID,
        "request_payload_hash": "a" * 64,
        "treatment_state": "ready",
        "creation_response": {"name": "Ready campaign", "marketable_population": 7},
        "audit_id": "audit-1",
    }
    cohorts = _Cohorts()
    coordinator = CampaignTreatmentCoordinator(
        lakebase=_Lakebase(ready),  # type: ignore[arg-type]
        cohort_queries=cohorts,  # type: ignore[arg-type]
    )

    assert coordinator.create(_spec()).replayed is True
    with pytest.raises(ValueError, match="different campaign payload"):
        coordinator.create(_spec(request_payload_hash="e" * 64))
    assert cohorts.calls == []


def test_treatment_fingerprint_and_state_machine_share_v2_semantics() -> None:
    assert CAMPAIGN_TREATMENT_ALGORITHM_VERSION == "campaign-treatment-v2"
    assert CAMPAIGN_TREATMENT_ALGORITHM_VERSION_V2 == CAMPAIGN_TREATMENT_ALGORITHM_VERSION
    assert "campaign-treatment-v2" in _CAMPAIGN_RESERVE_SQL
