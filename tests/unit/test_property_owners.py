"""S1.1 multi-owner + trust/LLC contract tests.

Pins three things:

1. The owner-entity classifier against tests/fixtures/owner_entity_type_
   golden.json (golden fixture — acceptance gate for the slice).
2. The multi-owner explode: a source record with N occupied owner slots
   produces exactly N silver rows (duplicate Owner Links collapse).
3. Cross-path parity: the regex patterns and confidence literals defined
   once in backend/services/owner_classification.py must appear verbatim
   in the warehouse MERGE SQL and the Lakeflow (DLT) pipeline, and the
   unresolved-owner suppression must be wired into gold_borrower_360.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.owner_classification import (
    CONFIDENCE_INDIVIDUAL,
    CONFIDENCE_LLC_FLAG_ONLY,
    CONFIDENCE_LLC_PATTERN_AND_FLAG,
    CONFIDENCE_LLC_PATTERN_ONLY,
    CONFIDENCE_TRUST_NAME_COLUMN,
    CONFIDENCE_TRUST_NAME_PATTERN,
    CONFIDENCE_UNRESOLVED_NO_LINK,
    CONFIDENCE_UNRESOLVED_NO_NAME,
    LLC_NAME_PATTERN,
    MAX_OWNER_SLOTS,
    TRUST_NAME_PATTERN,
    build_property_owner_rows,
    classify_owner,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "owner_entity_type_golden.json"
SILVER_TRANSFORM = (
    REPO_ROOT / "sql" / "transformations" / "silver_property_owners.sql"
)
SILVER_DDL = REPO_ROOT / "sql" / "ddl" / "silver_property_owners.sql"
PRIMARY_VIEW_DDL = (
    REPO_ROOT / "sql" / "ddl" / "silver_property_owners_primary_view.sql"
)
PIPELINE_PATH = REPO_ROOT / "pipelines" / "lakeflow" / "mip_feature_pipeline.py"
GOLD_B360 = REPO_ROOT / "sql" / "transformations" / "gold_borrower_360.sql"
GOLD_LEAD_POP = REPO_ROOT / "sql" / "transformations" / "gold_lead_population.sql"

with GOLDEN_PATH.open(encoding="utf-8") as fh:
    GOLDEN_CASES = json.load(fh)["cases"]

ALL_CONFIDENCES = (
    CONFIDENCE_TRUST_NAME_COLUMN,
    CONFIDENCE_TRUST_NAME_PATTERN,
    CONFIDENCE_LLC_PATTERN_AND_FLAG,
    CONFIDENCE_LLC_PATTERN_ONLY,
    CONFIDENCE_LLC_FLAG_ONLY,
    CONFIDENCE_INDIVIDUAL,
    CONFIDENCE_UNRESOLVED_NO_LINK,
    CONFIDENCE_UNRESOLVED_NO_NAME,
)


# ---------------------------------------------------------------------------
# 1. Golden fixture (acceptance: >=1 golden fixture for the classification)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
def test_owner_entity_type_matches_golden_fixture(case: dict) -> None:
    result = classify_owner(**case["inputs"])
    if case["expected_entity_type"] is None:
        assert result is None, case["note"]
        return
    assert result is not None, case["note"]
    assert result.entity_type == case["expected_entity_type"], case["note"]
    assert result.resolution_confidence == pytest.approx(
        case["expected_confidence"]
    ), case["note"]


def test_unresolved_is_never_contact_eligible() -> None:
    """Acceptance: unresolved owners are excluded from contact-eligible
    populations. The Python mirror encodes that as is_contact_eligible."""
    for case in GOLDEN_CASES:
        if case["expected_entity_type"] is None:
            continue
        result = classify_owner(**case["inputs"])
        assert result is not None
        assert result.is_contact_eligible == (
            case["expected_entity_type"] != "unresolved"
        ), case["id"]


# ---------------------------------------------------------------------------
# 2. Multi-owner explode (acceptance: N rows for N owners)
# ---------------------------------------------------------------------------


def _record(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"clip": "SYN-CLIP-0001"}
    base.update(overrides)
    return base


def test_multi_owner_record_produces_one_row_per_owner() -> None:
    record = _record(
        owner_1_full_name="AVERY QUINCE",
        owner_1_corporate_indicator="N",
        owner_1_identifier="OL-SYN-000000101",
        owner_2_full_name="BLAKE QUINCE",
        owner_2_corporate_indicator="N",
        owner_2_identifier="OL-SYN-000000102",
        owner_3_full_name="QUINCE FAMILY LIVING TRUST",
        owner_3_corporate_indicator="Y",
        owner_3_identifier="OL-SYN-000000103",
    )
    rows = build_property_owner_rows(record)
    assert len(rows) == 3
    assert [r.owner_position for r in rows] == [1, 2, 3]
    assert [r.entity_type for r in rows] == ["individual", "individual", "trust"]


def test_four_owner_record_produces_four_rows_max() -> None:
    record = _record(
        **{
            f"owner_{i}_full_name": f"OWNER {i} SYNTHETIC"
            for i in range(1, MAX_OWNER_SLOTS + 1)
        },
        **{
            f"owner_{i}_identifier": f"OL-SYN-00000020{i}"
            for i in range(1, MAX_OWNER_SLOTS + 1)
        },
    )
    rows = build_property_owner_rows(record)
    assert len(rows) == MAX_OWNER_SLOTS


def test_duplicate_owner_link_collapses_to_lowest_slot() -> None:
    """Grain contract: one row per (clip, owner_link) for resolved owners."""
    record = _record(
        owner_1_full_name="AVERY QUINCE",
        owner_1_identifier="OL-SYN-000000301",
        owner_2_full_name="AVERY QUINCE",
        owner_2_identifier="OL-SYN-000000301",
    )
    rows = build_property_owner_rows(record)
    assert len(rows) == 1
    assert rows[0].owner_position == 1


def test_single_owner_record_produces_single_row() -> None:
    rows = build_property_owner_rows(
        _record(
            owner_1_full_name="AVERY QUINCE",
            owner_1_identifier="OL-SYN-000000401",
        )
    )
    assert len(rows) == 1
    assert rows[0].entity_type == "individual"
    assert rows[0].is_contact_eligible


def test_unresolved_slot_kept_as_row_but_not_contact_eligible() -> None:
    """Unresolved owners stay VISIBLE in silver (caveat surface) while being
    excluded from contact-eligible populations."""
    rows = build_property_owner_rows(
        _record(
            owner_1_full_name="AVERY QUINCE",
            owner_1_identifier="OL-SYN-000000501",
            owner_2_full_name="BLAKE QUINCE",  # no Owner Link -> unresolved
        )
    )
    assert len(rows) == 2
    assert rows[1].entity_type == "unresolved"
    assert not rows[1].is_contact_eligible
    assert rows[0].is_contact_eligible


# ---------------------------------------------------------------------------
# 3. Cross-path parity: SQL + DLT must embed the same contract
# ---------------------------------------------------------------------------


def _sql_escaped(pattern: str) -> str:
    """Regex literal as it must appear inside a single-quoted Spark SQL
    string (backslashes doubled)."""
    return pattern.replace("\\", "\\\\")


def test_silver_transformation_embeds_classifier_contract() -> None:
    text = SILVER_TRANSFORM.read_text(encoding="utf-8")
    assert _sql_escaped(TRUST_NAME_PATTERN) in text, (
        "silver_property_owners.sql must use TRUST_NAME_PATTERN verbatim "
        "(SQL-escaped) — see backend/services/owner_classification.py"
    )
    assert _sql_escaped(LLC_NAME_PATTERN) in text
    for value in ALL_CONFIDENCES:
        assert str(value) in text, (
            f"confidence literal {value} missing from silver_property_owners.sql"
        )
    for entity in ("'trust'", "'llc'", "'individual'", "'unresolved'"):
        assert entity in text
    # Explode covers all four owner slots.
    for i in range(1, MAX_OWNER_SLOTS + 1):
        assert f"owner_{i}_identifier" in text
    assert "owner_1_original_trust_name" in text
    # PII posture: raw names consumed, only the salted hash lands.
    assert "sha2(" in text.lower()
    assert "situs_state IS NOT NULL" in text


def test_lakeflow_pipeline_embeds_classifier_contract() -> None:
    text = PIPELINE_PATH.read_text(encoding="utf-8")
    assert TRUST_NAME_PATTERN in text, (
        "mip_feature_pipeline.py must define TRUST_NAME_PATTERN verbatim"
    )
    assert LLC_NAME_PATTERN in text
    for value in ALL_CONFIDENCES:
        assert str(value) in text
    assert 'name="property_owners"' in text
    # The DLT path builds the slot column families from a template; the
    # explode must cover every slot up to MAX_OWNER_SLOTS.
    assert 'F.col(f"owner_{position}_identifier")' in text
    assert 'F.col(f"owner_{position}_full_name")' in text
    assert 'F.col(f"owner_{position}_corporate_indicator")' in text
    assert "MAX_OWNER_SLOTS = 4" in text
    assert "range(1, MAX_OWNER_SLOTS + 1)" in text
    assert "owner_1_original_trust_name" in text


def test_silver_ddl_declares_classification_columns() -> None:
    text = SILVER_DDL.read_text(encoding="utf-8")
    for column in (
        "clip",
        "owner_position",
        "owner_link_id",
        "owner_name_hash",
        "owner_entity_type",
        "resolution_confidence",
        "is_contact_eligible",
    ):
        assert column in text, f"silver_property_owners.sql DDL missing {column}"


def test_primary_owner_compatibility_view_exists() -> None:
    """Compatibility contract: single-owner consumers read the primary-owner
    projection (owner_position = 1) with the legacy column vocabulary."""
    text = PRIMARY_VIEW_DDL.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE VIEW" in text.upper()
    assert "property_owners_primary" in text
    assert "owner_position = 1" in text
    # Legacy single-owner vocabulary preserved for existing consumers.
    assert "owner_is_corporate" in text
    assert "owner_link_id" in text


def test_gold_borrower_360_suppresses_unresolved_owners() -> None:
    """Acceptance: unresolved owners excluded from contact-eligible
    populations. Every contact-eligible query gates on marketing_eligible,
    so the borrower_360 CTAS must fail it closed and stamp the controlled
    suppression reason."""
    text = GOLD_B360.read_text(encoding="utf-8")
    assert "'unresolved_owner'" in text, (
        "gold_borrower_360.sql must stamp suppression_reason='unresolved_owner'"
    )
    assert "has_unresolved_owner" in text
    assert "owner_count" in text
    assert "primary_owner_entity_type" in text
    # marketing_eligible must AND-in the unresolved-owner gate (fail closed).
    assert "AND NOT" in text and "has_unresolved_owner" in text


def test_gold_lead_population_carries_owner_caveat_columns() -> None:
    text = GOLD_LEAD_POP.read_text(encoding="utf-8")
    for column in ("owner_count", "has_unresolved_owner", "primary_owner_entity_type"):
        assert column in text, f"gold_lead_population.sql must carry {column}"
