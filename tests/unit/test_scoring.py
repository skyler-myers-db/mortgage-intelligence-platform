"""Parity tests for backend.services.scoring.lead_score.

Fixtures in tests/fixtures/lead_score_golden.json are the contract;
the SQL UDF mip.gold.fn_lead_score is validated against the same
set by sql/fixtures/lead_score_golden_validation.sql.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.scoring import lead_score, source_display_label

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "lead_score_golden.json"
)

with FIXTURE_PATH.open() as f:
    GOLDEN_CASES = json.load(f)


@pytest.mark.parametrize(
    "case",
    GOLDEN_CASES,
    ids=[c["id"] for c in GOLDEN_CASES],
)
def test_lead_score_matches_golden_fixture(case: dict) -> None:
    """Every golden case must produce the SQL-pinned expected_score."""
    assert lead_score(**case["inputs"]) == case["expected_score"], case.get("note", "")


def test_lead_score_clips_above_100() -> None:
    """Defense-in-depth: an out-of-range component still clips to 100."""
    assert lead_score(200, 200, 200, 200, 200) == 100


def test_lead_score_all_none_returns_zero() -> None:
    """All-NULL row returns 0, not None — keeps column NOT NULL downstream."""
    assert lead_score(None, None, None, None, None) == 0


def test_lead_score_is_importable_from_services() -> None:
    """Scorer is exported at backend.services.scoring.lead_score."""
    from backend.services import scoring

    assert callable(scoring.lead_score)


def test_itm_ruleset_label_matches_lineage_drawer_title() -> None:
    """The RowPreview chip should not imply a different source than the drawer."""
    assert source_display_label("rules.itm_v3") == "In-the-Money logic"
