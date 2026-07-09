"""Parity tests for backend.services.scoring lead-score primitives.

Fixtures in tests/fixtures/lead_score_golden.json are the contract;
the SQL UDF mip.gold.fn_lead_score is validated against the same
set by sql/fixtures/lead_score_golden_validation.sql.

Fixtures in tests/fixtures/estimated_upb_golden.json are the contract
for mip.gold.fn_estimated_upb. The SQL validation fixture expresses the
same cases in sql/fixtures/estimated_upb_validation.sql, so Python and
UC SQL stay in lockstep for the estimated-UPB gap A5 math.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.scoring import estimated_upb, lead_score, source_display_label

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "lead_score_golden.json"
)
ESTIMATED_UPB_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "estimated_upb_golden.json"
)

with FIXTURE_PATH.open() as f:
    GOLDEN_CASES = json.load(f)

with ESTIMATED_UPB_FIXTURE_PATH.open() as f:
    ESTIMATED_UPB_CASES = json.load(f)["cases"]


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


@pytest.mark.parametrize(
    "case",
    ESTIMATED_UPB_CASES,
    ids=[c["id"] for c in ESTIMATED_UPB_CASES],
)
def test_estimated_upb_matches_golden_fixture(case: dict) -> None:
    """Every golden case must produce the SQL-pinned expected_upb.

    This pins the Python mirror of ``mip.gold.fn_estimated_upb``. The
    matching SQL validation fixture carries the same rows so the amortized
    estimated-UPB semantics stay locked across engines.
    """
    assert estimated_upb(**case["inputs"]) == case["expected_upb"], case.get("note", "")


def test_estimated_upb_is_importable_from_services() -> None:
    """The estimated-UPB primitive must live beside the other scoring mirrors."""
    from backend.services import scoring

    assert callable(scoring.estimated_upb)


# ---------------------------------------------------------------------------
# Exact-decimal parity (2026-06-11 audit P1-1).
# fn_lead_score computes in Spark DECIMAL (exact) then BROUNDs. The original
# Python scorer used binary floats and diverged on ~0.67% of the input
# lattice, surfacing as false "integrity gap" warnings in the proof drawer.
# These tests pin the Decimal implementation against an independent exact
# oracle (integer arithmetic in hundredths) so the bug class cannot return.
# ---------------------------------------------------------------------------

def _exact_oracle(e: int, i: int, f: int, r: int, v: int) -> int:
    """fn_lead_score in pure-integer hundredths: exact, float-free."""
    hundredths = 35 * e + 30 * i + 15 * f + 10 * r + 10 * v
    quotient, remainder = divmod(hundredths, 100)
    if remainder > 50 or (remainder == 50 and quotient % 2 == 1):
        quotient += 1
    return max(0, min(100, quotient))


def test_lead_score_drift_zone_sentinel_matches_exact_decimal() -> None:
    """THE audit repro: exact 85.5 -> 86; a float scorer says 85."""
    assert lead_score(92, 94, 94, 85, 25) == 86


def test_lead_score_matches_exact_oracle_on_seeded_sweep() -> None:
    """200k seeded random points across the lattice: zero divergence allowed.

    Deterministic (fixed seed) so CI is stable; broad enough that any
    reintroduction of float arithmetic fails within this sweep — the float
    implementation diverged on ~1,333 of these exact points.
    """
    import random

    rng = random.Random(42)
    for _ in range(200_000):
        components = tuple(rng.randint(0, 100) for _ in range(5))
        assert lead_score(*components) == _exact_oracle(*components), components


def test_lead_score_matches_exact_oracle_on_half_boundary_lattice() -> None:
    """Exhaustive sweep of the highest-risk slice: every (e, i) pair with the
    remaining components fixed at drift-prone values, covering all exact-.5
    sums reachable in that plane."""
    for e in range(0, 101):
        for i in range(0, 101):
            assert lead_score(e, i, 94, 85, 25) == _exact_oracle(e, i, 94, 85, 25)


def test_itm_ruleset_label_matches_lineage_drawer_title() -> None:
    """The RowPreview chip should not imply a different source than the drawer."""
    assert source_display_label("rules.itm_v3") == "Refinance economics screen"
