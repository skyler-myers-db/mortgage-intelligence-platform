from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from backend.schemas.portfolio import PortfolioCriteria
from backend.services.growth_agent_runtime import cohort_fingerprint
from backend.services.repositories.databricks_lead_cohorts import (
    GrowthAgentHandoffInvalid,
    GrowthAgentHandoffStale,
    LeadCohortFilters,
    LeadCohortQueries,
    issue_growth_agent_handoff,
    normalise_growth_agent_handoff_filters,
    normalise_lead_queue_handoff_filters,
    validate_growth_agent_handoff_identity,
    verify_growth_agent_handoff,
)


def test_cohort_identity_keeps_filters_and_snapshot_sources_in_one_statement() -> None:
    captured: dict[str, object] = {}

    class _Client:
        def execute_one(
            self,
            statement: str,
            parameters: dict[str, object] | None = None,
        ) -> dict[str, object]:
            captured["statement"] = statement
            captured["parameters"] = parameters or {}
            return {
                "n": 3,
                "cohort_digest": "A" * 64,
                "snapshot_id": "snapshot-1",
            }

    queries = LeadCohortQueries(
        _Client(),  # type: ignore[arg-type]
        cache_ttl_s=0,
    )

    identity = queries.cohort_identity(
        LeadCohortFilters(
            segment=None,
            state_codes=["IL"],
            segment_codes=["itm", "equity"],
            segment_mode="all",
            approval_status="approved",
        )
    )

    statement = str(captured["statement"])
    assert identity == {
        "total": 3,
        "cohort_digest": "a" * 64,
        "snapshot_id": "snapshot-1",
    }
    assert statement.count("WITH matched AS") == 1
    assert "COUNT(DISTINCT m.borrower_id) AS n" in statement
    assert "MAX(snapshot_validation.snapshot_id) AS snapshot_id" in statement
    assert "versions.borrower_360_at = anchor.refresh_at" in statement
    assert "versions.lifecycle_at IS NOT NULL" in statement
    assert captured["parameters"] == {
        "seg_0": "itm",
        "seg_1": "equity",
        "state_0": "IL",
        "approval_status": "approved",
    }


def test_identity_page_is_atomic_and_intentionally_uncached() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class _Client:
        def execute(
            self,
            statement: str,
            parameters: dict[str, object] | None = None,
        ) -> list[dict[str, Any]]:
            calls.append((statement, parameters or {}))
            return [
                {
                    "borrower_id": None,
                    "__identity_total": 0,
                    "__cohort_digest": "b" * 64,
                    "__snapshot_id": "snapshot-2",
                }
            ]

    queries = LeadCohortQueries(
        _Client(),  # type: ignore[arg-type]
        cache_ttl_s=300,
    )
    filters = LeadCohortFilters(segment="itm")

    first = queries.list_with_identity(filters, limit=25)
    second = queries.list_with_identity(filters, limit=25)

    assert (
        first
        == second
        == (
            [],
            {
                "total": 0,
                "cohort_digest": "b" * 64,
                "snapshot_id": "snapshot-2",
            },
        )
    )
    assert len(calls) == 2
    statement, parameters = calls[0]
    assert statement.count("WITH matched AS") == 1
    assert "versions.lead_population_at = anchor.refresh_at" in statement
    assert "LEFT JOIN ranked ON TRUE" in statement
    assert "LIMIT 25" in statement
    assert parameters == {"seg": "itm"}


def test_identity_page_fails_closed_without_metadata() -> None:
    class _Client:
        def execute(
            self,
            statement: str,
            parameters: dict[str, object] | None = None,
        ) -> list[dict[str, Any]]:
            _ = (statement, parameters)
            return []

    queries = LeadCohortQueries(
        _Client(),  # type: ignore[arg-type]
        cache_ttl_s=0,
    )

    with pytest.raises(
        ValueError,
        match="Lead Queue cohort identity proof returned no metadata",
    ):
        queries.list_with_identity(LeadCohortFilters(segment=None), limit=1)


def test_growth_agent_and_lead_queue_filters_normalise_to_same_proof() -> None:
    growth_filters = normalise_growth_agent_handoff_filters(
        {
            "lead_queue_filters": {
                "source": "trusted_sql",
                "segment_codes": ["itm"],
                "segment_mode": "any",
                "states": ["TX", "IL"],
                "portfolio_criteria": {
                    "marketing_eligibility": "Eligible only",
                    "states": ["IL", "TX"],
                },
            }
        }
    )
    lead_filters = normalise_lead_queue_handoff_filters(
        LeadCohortFilters(
            segment="itm",
            state_codes=["IL", "TX", "IL"],
            portfolio_criteria=PortfolioCriteria(marketing_eligibility="Eligible only"),
        )
    )

    assert growth_filters == lead_filters == {
        "segment_codes": ["itm"],
        "segment_mode": "any",
        "states": ["IL", "TX"],
        "portfolio_criteria": {"marketing_eligibility": "Eligible only"},
    }


def test_growth_agent_handoff_is_private_and_rejects_tampering() -> None:
    actor = "operator@example.com"
    filters = {
        "segment_codes": ["itm"],
        "segment_mode": "any",
        "portfolio_criteria": {"marketing_eligibility": "Eligible only"},
    }
    digest = "b" * 64
    result_hash = "a" * 64
    fingerprint = cohort_fingerprint(
        cohort_digest=digest,
        tool_result_hash=result_hash,
    )
    token = issue_growth_agent_handoff(
        actor=actor,
        run_id="11111111-1111-4111-8111-111111111111",
        normalized_filters=filters,
        cohort_fingerprint=fingerprint,
        total=3,
        source_snapshot="snapshot-1",
        tool_result_hash=result_hash,
        now=1_000,
    )

    encoded_claims = token.split(".", 1)[0]
    claims = json.loads(
        base64.urlsafe_b64decode(encoded_claims + "=" * (-len(encoded_claims) % 4))
    )
    assert actor not in token
    assert "borrower" not in json.dumps(claims).lower()
    assert claims["actor_binding"] != actor
    assert claims["run_id"] == "11111111-1111-4111-8111-111111111111"

    proof = verify_growth_agent_handoff(
        token,
        actor=actor,
        normalized_filters=filters,
        now=1_001,
    )
    assert proof.cohort_fingerprint == fingerprint
    assert proof.total == 3

    with pytest.raises(GrowthAgentHandoffInvalid, match="actor does not match"):
        verify_growth_agent_handoff(
            token,
            actor="other@example.com",
            normalized_filters=filters,
            now=1_001,
        )
    with pytest.raises(GrowthAgentHandoffInvalid, match="filters do not match"):
        verify_growth_agent_handoff(
            token,
            actor=actor,
            normalized_filters={**filters, "segment_codes": ["equity"]},
            now=1_001,
        )
    with pytest.raises(GrowthAgentHandoffInvalid, match="proof is invalid"):
        verify_growth_agent_handoff(
            token[:-1] + ("A" if token[-1] != "A" else "B"),
            actor=actor,
            normalized_filters=filters,
            now=1_001,
        )
    with pytest.raises(GrowthAgentHandoffStale, match="has expired"):
        verify_growth_agent_handoff(
            token,
            actor=actor,
            normalized_filters=filters,
            now=1_000 + 2 * 60 * 60 + 1,
        )


@pytest.mark.parametrize(
    "identity",
    [
        {"total": 4, "cohort_digest": "b" * 64, "snapshot_id": "snapshot-1"},
        {"total": 3, "cohort_digest": "b" * 64, "snapshot_id": "snapshot-2"},
        {"total": 3, "cohort_digest": "c" * 64, "snapshot_id": "snapshot-1"},
    ],
)
def test_growth_agent_handoff_rejects_current_cohort_drift(
    identity: dict[str, str | int],
) -> None:
    filters = {"segment_codes": ["itm"], "segment_mode": "any"}
    tool_result_hash = "a" * 64
    token = issue_growth_agent_handoff(
        actor="operator@example.com",
        run_id="11111111-1111-4111-8111-111111111111",
        normalized_filters=filters,
        cohort_fingerprint=cohort_fingerprint(
            cohort_digest="b" * 64,
            tool_result_hash=tool_result_hash,
        ),
        total=3,
        source_snapshot="snapshot-1",
        tool_result_hash=tool_result_hash,
        now=1_000,
    )
    proof = verify_growth_agent_handoff(
        token,
        actor="operator@example.com",
        normalized_filters=filters,
        now=1_001,
    )

    with pytest.raises(GrowthAgentHandoffStale):
        validate_growth_agent_handoff_identity(proof, identity)
