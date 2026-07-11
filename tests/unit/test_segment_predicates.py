"""S8: canonical segment-predicate composition.

The intersection the Segment Intelligence cards preview must be the exact
predicate the Lead Queue, map rollups, and analytics drilldowns execute.
These tests pin the single composer: AND semantics for ``all``, OR for
``any``, order-preserving dedupe, the parameter-name contract, and the
injection posture (segment values only ever appear as bind parameters).
"""

from __future__ import annotations

from backend.services.repositories.databricks_geo import DatabricksGeoRepository
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from backend.services.segment_predicates import (
    compose_segment_predicate,
    normalise_segment_codes,
)


def test_all_mode_composes_and_of_array_contains() -> None:
    clause, params = compose_segment_predicate(
        ["refi_propensity", "investor"], mode="all"
    )
    assert clause == (
        "array_contains(segment_codes, :segment_0) AND "
        "array_contains(segment_codes, :segment_1)"
    )
    assert params == {"segment_0": "refi_propensity", "segment_1": "investor"}


def test_any_mode_composes_parenthesised_or() -> None:
    clause, params = compose_segment_predicate(["itm", "equity"], mode="any")
    assert clause == (
        "(array_contains(segment_codes, :segment_0) OR "
        "array_contains(segment_codes, :segment_1))"
    )
    assert params == {"segment_0": "itm", "segment_1": "equity"}


def test_single_code_binds_the_stable_segment_param() -> None:
    """One code always binds ``:segment`` regardless of mode — the name the
    repository templates and warehouse result-cache keys already rely on."""
    for mode in ("any", "all"):
        clause, params = compose_segment_predicate(["retention"], mode=mode)
        assert clause == "array_contains(segment_codes, :segment)"
        assert params == {"segment": "retention"}


def test_duplicates_and_blanks_collapse_before_composition() -> None:
    clause, params = compose_segment_predicate(
        ["investor", " investor ", "", None, "itm", "investor"],
        mode="all",
    )
    assert clause == (
        "array_contains(segment_codes, :segment_0) AND "
        "array_contains(segment_codes, :segment_1)"
    )
    assert params == {"segment_0": "investor", "segment_1": "itm"}


def test_empty_input_composes_no_filter_not_a_tautology() -> None:
    assert compose_segment_predicate(None, mode="all") == ("", {})
    assert compose_segment_predicate(["", "  "], mode="any") == ("", {})


def test_unknown_mode_falls_back_to_any_union() -> None:
    """Routers 422 unknown modes upstream; the composer itself must still
    fail open to the wider OR cohort rather than fabricating AND SQL."""
    clause, _ = compose_segment_predicate(["itm", "equity"], mode="ALL")
    assert " OR " in clause and " AND " not in clause


def test_column_override_scopes_every_fragment() -> None:
    clause, _ = compose_segment_predicate(
        ["itm", "equity"], mode="all", column="b.segment_codes"
    )
    assert clause == (
        "array_contains(b.segment_codes, :segment_0) AND "
        "array_contains(b.segment_codes, :segment_1)"
    )


def test_hostile_codes_never_reach_the_sql_text() -> None:
    """Injection posture: a hostile 'code' may only ever appear as a bound
    parameter VALUE. The clause text stays the fixed array_contains shape."""
    hostile = "itm') OR 1=1 --"
    clause, params = compose_segment_predicate([hostile, "investor"], mode="all")
    assert hostile not in clause
    assert "1=1" not in clause
    assert clause == (
        "array_contains(segment_codes, :segment_0) AND "
        "array_contains(segment_codes, :segment_1)"
    )
    assert params["segment_0"] == hostile


def test_normalise_preserves_caller_order() -> None:
    assert normalise_segment_codes(["equity", "itm", "equity"]) == ["equity", "itm"]


def test_lead_and_geo_repositories_delegate_to_the_canonical_composer() -> None:
    """Re-drift guard: both repository helpers must return byte-identical
    output to the canonical composer for the same selection."""
    codes = ["refi_propensity", "investor", "itm"]
    for mode in ("any", "all"):
        canonical = compose_segment_predicate(codes, mode=mode)
        assert (
            DatabricksLeadRepository._segment_filter_clause(
                segment=None, segment_codes=codes, segment_mode=mode
            )
            == canonical
        )
        assert (
            DatabricksGeoRepository._state_segment_filter_clause(
                codes, segment_mode=mode
            )
            == canonical
        )
