"""Trusted-SQL draft criteria store ``conversation_id: ""`` and must project.

A trusted-SQL rescue turn has no Genie conversation (databricks_genie_direct
mints it with ``conversation_id=""``), and the draft writer copies that value
into the campaign criteria. On 2026-10-28 every ``GET /api/v1/campaigns`` on
dev logged one ``campaign_json_public_projection_rejected`` (field
``criteria``) per such row: 78 stored drafts, all this exact shape. The
fixture below is one of those rows verbatim.

The projection is also the decision-proof input, and that proof compares a
fingerprint of the projection with the contract fingerprint hashed over the
raw stored criteria at create time, so the projection must echo the empty
string, not drop it. The closed-shape tests pin that only the exact empty
string is accepted as absence.
"""

from __future__ import annotations

import copy
import logging

import pytest

from backend.schemas.portfolio import project_public_campaign_json_field
from backend.services.campaign_targeting import campaign_treatment_fingerprint
from backend.services.repositories.databricks_portfolio import campaign_summary_from_row
from backend.services.state_footprint import (
    FootprintState,
    StateFootprintResolver,
    _reset_state_footprint_resolver_for_tests,
)

_STORED_TRUSTED_SQL_CRITERIA: dict[str, object] = {
    "borrower_ids": [],
    "conversation_id": "",
    "criteria_hash": "a680d83c293fa2b0",
    "criteria_keys": [
        "actionable_total",
        "result_filters",
        "row_count",
        "source",
        "source_assets",
        "sql_hash",
        "visualization_kind",
    ],
    "message_id": "trusted-sql-22d792f15f1d6ae2",
    "question_hash": "22d792f15f1d6ae2",
    "result_filters": {
        "segment_codes": ["itm"],
        "segment_mode": "any",
        "source": "trusted_sql",
        "states": ["IL", "FL", "TX", "CA", "WA", "CO"],
    },
    "route": "/lead-queue?states=IL%2CFL%2CTX%2CCA%2CWA%2CCO&segment=itm",
    "row_count": 6,
    "source": "trusted_sql",
    "source_assets": ["mip.gold.borrower_360"],
    "sql_hash": "e5c651f22fafcfc0",
    "visualization_kind": "bar",
}
_FOOTPRINT = [
    FootprintState(code, name, order, order == 1)
    for order, (code, name) in enumerate(
        (
            ("IL", "Illinois"),
            ("FL", "Florida"),
            ("TX", "Texas"),
            ("CA", "California"),
            ("WA", "Washington"),
            ("CO", "Colorado"),
            ("NY", "New York"),
        ),
        start=1,
    )
]


@pytest.fixture(autouse=True)
def _reviewed_footprint() -> object:
    resolver = StateFootprintResolver(ttl_s=60.0)
    resolver._load_from_uc = lambda: _FOOTPRINT  # type: ignore[method-assign]
    _reset_state_footprint_resolver_for_tests(resolver)
    yield
    _reset_state_footprint_resolver_for_tests(None)


def _criteria(**overrides: object) -> dict[str, object]:
    criteria = copy.deepcopy(_STORED_TRUSTED_SQL_CRITERIA)
    criteria.update(overrides)
    return criteria


def test_stored_trusted_sql_criteria_project_with_exact_parity() -> None:
    stored = _criteria()

    projected = project_public_campaign_json_field("criteria", copy.deepcopy(stored))

    assert projected == stored
    assert isinstance(projected, dict)
    assert projected["conversation_id"] == ""


def test_trusted_sql_projection_keeps_the_decision_proof_fingerprint() -> None:
    stored = _criteria()
    contract = {
        "json_contract_version": 1,
        "suppression_policy": {"default": "eligible_only", "marketing_eligibility": "Eligible only"},
        "holdout": None,
        "household_dedup": {
            "enabled": False,
            "dedupe_unit": "borrower",
            "primary_contact_strategy": "highest_opportunity_eligible",
        },
    }
    projected = project_public_campaign_json_field("criteria", copy.deepcopy(stored))
    assert isinstance(projected, dict)

    assert campaign_treatment_fingerprint(
        criteria=projected, **contract  # type: ignore[arg-type]
    ) == campaign_treatment_fingerprint(
        criteria=stored, **contract  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"conversation_id": " "},
        {"conversation_id": "\t"},
        {"conversation_id": "conv id"},
        {"conversation_id": "../conversation"},
        {"conversation_id": "Jordan Lee"},
        {"message_id": ""},
    ],
    ids=["space", "tab", "inner-space", "path", "person-name", "empty-message-id"],
)
def test_conversation_absence_is_closed_to_the_exact_empty_string(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="public-safe opaque id"):
        project_public_campaign_json_field("criteria", _criteria(**overrides))


def test_a_null_conversation_id_is_still_omitted() -> None:
    projected = project_public_campaign_json_field("criteria", _criteria(conversation_id=None))

    assert isinstance(projected, dict)
    assert "conversation_id" not in projected


def test_an_empty_conversation_id_is_not_a_replay_target() -> None:
    criteria = _criteria()
    del criteria["result_filters"]

    with pytest.raises(ValueError, match="reviewed replay target"):
        project_public_campaign_json_field("criteria", criteria)


def _campaign_row(*, json_contract_version: int) -> dict[str, object]:
    return {
        "campaign_id": "11111111-1111-4111-8111-111111111111",
        "name": "Genie strategy draft",
        "owner_email": "operator@summit.example",
        "status": "draft",
        "json_contract_version": json_contract_version,
        "treatment_state": "legacy_unbound",
        "criteria": _criteria(),
        "suppression_policy": {},
        "normalized_message_variants": None,
        "legacy_message_variants": [],
        "channel_cascade": [],
        "send_window": {},
        "holdout": None,
        "roi_assumptions": None,
    }


@pytest.mark.parametrize(
    ("json_contract_version", "expected_issue"),
    [(1, "treatment_unbound"), (0, "legacy_contract")],
)
def test_campaign_list_row_projects_trusted_sql_criteria_without_a_rejection(
    caplog: pytest.LogCaptureFixture,
    json_contract_version: int,
    expected_issue: str,
) -> None:
    caplog.set_level(logging.WARNING)

    summary = campaign_summary_from_row(_campaign_row(json_contract_version=json_contract_version))

    assert summary.criteria == _STORED_TRUSTED_SQL_CRITERIA
    # Projecting the criteria never makes a draft actionable on its own: a
    # legacy row stays legacy and an unbound treatment stays unbound.
    assert summary.actionable is False
    assert summary.actionability_issue == expected_issue
    assert not [
        record
        for record in caplog.records
        if getattr(record, "mip_event", None) == "campaign_json_public_projection_rejected"
    ]
