"""A Genie answer's count and the Lead Queue it hands off to must reconcile.

Live measurement 2026-08-10 against paychex gold, one question three ways:

    Genie: in-the-money borrowers in IL                     1,766
    queue replaying segment_codes=[itm] + states=[IL]       1,766   exact
    queue replaying states=[IL] only (segment lost)        76,711   43x
    Genie: in-the-money in IL, opportunity_score >= 80         32
      -> the queue has no score predicate at all, replays  1,766   55x

The reviewed replay subset is geography/segment/lender only, so every numeric
threshold a Genie answer uses — opportunity_score, equity_pct, rate_spread_bps,
LTV — is dropped on handoff. The queue then answers a broader question than the
one the user just read, under the same heading. These tests pin that the drop
is *named* and the two counts travel together so the surface can say so.
"""

from __future__ import annotations

from backend.services.genie_actions import (
    _MAX_UNREPLAYABLE_FILTER_KEYS,
    _REPLAYABLE_FILTER_KEYS,
)


def _route_filters(**filters: object) -> dict[str, object]:
    from types import SimpleNamespace

    from backend.services.genie_actions import _cohort_route_filters

    payload = SimpleNamespace(criteria={"result_filters": filters, "source": "genie"})
    return _cohort_route_filters(payload, [])


def test_numeric_thresholds_are_named_as_unreplayable() -> None:
    out = _route_filters(
        states=["IL"],
        segment_codes=["itm"],
        min_opportunity_score=80,
        min_equity_pct=50,
        min_rate_spread_bps=100,
    )
    assert out["states"] == ["IL"]
    assert out["segment_codes"] == ["itm"]
    named = out["unreplayable_filters"]
    assert "min_opportunity_score" in named
    assert "min_equity_pct" in named
    assert "min_rate_spread_bps" in named


def test_fully_replayable_cohorts_carry_no_disclosure() -> None:
    out = _route_filters(states=["IL"], segment_codes=["itm"])
    assert "unreplayable_filters" not in out


def test_disclosure_never_becomes_the_only_filter() -> None:
    """An unreplayable-only cohort must stay empty so the action 400s.

    ``_materialize_genie_cohort`` rejects a cohort with no replayable filter.
    If the disclosure key counted as a filter it would smuggle an empty
    cohort past that gate and the queue would replay *everything*.
    """

    assert _route_filters(min_opportunity_score=80) == {}


def test_replayable_key_set_matches_what_the_queue_reads() -> None:
    # Every key the queue replays in backend/api/leads.py must be declared
    # replayable, or a real filter would be reported as dropped.
    for key in ("zips", "county", "counties", "states", "segment_codes", "borrower_ids"):
        assert key in _REPLAYABLE_FILTER_KEYS


def test_disclosure_list_is_bounded() -> None:
    out = _route_filters(states=["IL"], **{f"unknown_{i}": i for i in range(40)})
    assert len(out["unreplayable_filters"]) <= _MAX_UNREPLAYABLE_FILTER_KEYS
