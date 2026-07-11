"""S8: canonical segment-predicate composition.

The intersection the Segment Intelligence cards preview must be the exact
predicate the Lead Queue, map rollups, and analytics drilldowns execute.
These tests pin the single composer: AND semantics for ``all``, OR for
``any``, order-preserving dedupe, the parameter-name contract, and the
injection posture (segment values only ever appear as bind parameters).
"""

from __future__ import annotations

from backend.schemas.analytics import AnalyticsFilters
from backend.services.repositories.databricks_analytics import DatabricksAnalyticsRepository
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
        "array_contains(segment_codes, :seg_0) AND "
        "array_contains(segment_codes, :seg_1)"
    )
    assert params == {"seg_0": "refi_propensity", "seg_1": "investor"}


def test_any_mode_composes_parenthesised_or() -> None:
    clause, params = compose_segment_predicate(["itm", "equity"], mode="any")
    assert clause == (
        "(array_contains(segment_codes, :seg_0) OR "
        "array_contains(segment_codes, :seg_1))"
    )
    assert params == {"seg_0": "itm", "seg_1": "equity"}


def test_single_code_binds_the_stable_segment_param() -> None:
    """One code always binds ``:seg`` regardless of mode — the composer's
    reserved namespace, pinned so warehouse result-cache keys stay stable."""
    for mode in ("any", "all"):
        clause, params = compose_segment_predicate(["retention"], mode=mode)
        assert clause == "array_contains(segment_codes, :seg)"
        assert params == {"seg": "retention"}


def test_duplicates_and_blanks_collapse_before_composition() -> None:
    clause, params = compose_segment_predicate(
        ["investor", " investor ", "", None, "itm", "investor"],
        mode="all",
    )
    assert clause == (
        "array_contains(segment_codes, :seg_0) AND "
        "array_contains(segment_codes, :seg_1)"
    )
    assert params == {"seg_0": "investor", "seg_1": "itm"}


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
        "array_contains(b.segment_codes, :seg_0) AND "
        "array_contains(b.segment_codes, :seg_1)"
    )


def test_hostile_codes_never_reach_the_sql_text() -> None:
    """Injection posture: a hostile 'code' may only ever appear as a bound
    parameter VALUE. The clause text stays the fixed array_contains shape."""
    hostile = "itm') OR 1=1 --"
    clause, params = compose_segment_predicate([hostile, "investor"], mode="all")
    assert hostile not in clause
    assert "1=1" not in clause
    assert clause == (
        "array_contains(segment_codes, :seg_0) AND "
        "array_contains(segment_codes, :seg_1)"
    )
    assert params["seg_0"] == hostile


def test_normalise_preserves_caller_order() -> None:
    assert normalise_segment_codes(["equity", "itm", "equity"]) == ["equity", "itm"]


def test_composer_params_never_collide_with_sibling_filter_names() -> None:
    """Cross-review B1: callers merge composer params into shared dicts that
    already carry sibling filter bindings (state_0, signal_type_0, and the
    historical segment/segment_0 names). The composer's reserved `seg`
    namespace must be disjoint from all of them — for the single-code AND
    the multi-code shapes — so a merge can never silently overwrite a bind."""
    sibling_params: dict[str, object] = {
        "segment": "legacy-single",
        "segment_0": "legacy-multi",
        "segment_1": "legacy-multi",
        "state_0": "IL",
        "signal_type_0": "equity",
        "target_lender_ref": "Competitor B",
    }
    for codes in (["itm"], ["refi_propensity", "investor"]):
        for mode in ("any", "all"):
            _, params = compose_segment_predicate(codes, mode=mode)
            overlap = set(params) & set(sibling_params)
            assert not overlap, f"composer params collide with sibling binds: {overlap}"
            merged = {**sibling_params, **params}
            assert merged["segment"] == "legacy-single"
            assert merged["segment_0"] == "legacy-multi"


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


def test_analytics_repository_delegates_to_the_canonical_composer() -> None:
    """Cross-review B2: the analytics predicate builder must emit exactly the
    canonical composed clause (alias-scoped, parenthesised) and its params —
    a future hand-rolled array_contains fragment in databricks_analytics.py
    fails here, the same guard the lead + geo repositories carry."""
    codes = ["refi_propensity", "investor"]
    for mode in ("any", "all"):
        canonical_clause, canonical_params = compose_segment_predicate(
            codes, mode=mode, column="b.segment_codes"
        )
        predicates, params = DatabricksAnalyticsRepository._borrower_predicates(
            AnalyticsFilters(segment_codes=codes, segment_mode=mode),
        )
        segment_predicates = [p for p in predicates if "array_contains" in p]
        assert segment_predicates == [f"({canonical_clause})"]
        assert params == canonical_params
