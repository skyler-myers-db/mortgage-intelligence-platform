"""A NEGATIVE ``min_rate_spread_bps`` floor must replay end to end.

Live 2026-08-11, paychex ``mip.gold.borrower_360``, 5,156,184 rows:

    rate_spread_bps < 0          2,561,392 rows   49.7% of the book
    min / p1 / p50 / p99 / max      -569 / -447 / 0 / 122 / 830

So "borrowers whose spread is at or above -25 bps" is a real, common,
retention-side question, not a malformed one. Every downstream vocabulary used
to bound the value at 0..5000, which meant a Genie answer filtered to
``rate_spread_bps >= -25`` was parsed correctly, then REJECTED and merely
disclosed -- and the Lead Queue replayed the cohort BROADER than the answer it
was handed off from. That is the exact divergence this slice exists to close,
and it was still open for half the population.

The five vocabularies a floor has to survive, and what a mismatch costs:

    1. genie_actions._REPLAYABLE_NUMERIC_FILTERS   -> 400, cohort never written
    2. audit_store._RESULT_FILTER_NUMERIC_BOUNDS   -> 500 AFTER the cohort row
                                                      is written (the violation
                                                      subclasses RuntimeError,
                                                      the caller catches
                                                      ValueError) -- PR #191
    3. campaign_json_projection                    -> draft campaign can never
                                                      be approved
    4. lead_query_helpers.COHORT_NUMERIC_FILTER_BOUNDS -> 422 reading the row back
    5. campaign_treatment._numeric_floor           -> approved treatment set
                                                      diverges from the answer

They now share one object, so they cannot disagree; these tests pin the
behaviour rather than the wiring, so a future re-fork still fails here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import backend.schemas.campaign_json_projection as campaign_json_projection
import backend.services.campaign_treatment as campaign_treatment
from backend.schemas.genie_numeric_filters import (
    GENIE_NUMERIC_FILTER_BOUNDS,
    is_reviewed_numeric_floor,
)
from backend.schemas.portfolio import project_public_campaign_json_field
from backend.services.audit_store import build_safe_audit_metadata
from backend.services.campaign_treatment import cohort_filters_from_campaign_criteria
from backend.services.genie_actions import (
    _audit_payload,
    _cohort_route_filters,
    _reviewed_audit_metadata,
    _route_with_cohort,
)
from backend.services.growth_agent_handoff import handoff_filters_fingerprint
from backend.services.lead_query_helpers import cohort_numeric_floor
from backend.services.repositories.databricks_lead_cohort_support import LeadCohortQuerySupport
from backend.services.repositories.databricks_lead_cohorts import (
    LeadCohortFilters,
    normalise_lead_queue_handoff_filters,
)

# The floor a real retention-side answer would carry, and the reviewed edge.
_LIVE_SHAPED_FLOOR = -25
_REVIEWED_MINIMUM = GENIE_NUMERIC_FILTER_BOUNDS["min_rate_spread_bps"][0]
_LIVE_OBSERVED_MINIMUM = -569


def _route_filters(**filters: object) -> dict[str, object]:
    payload = SimpleNamespace(criteria={"result_filters": filters, "source": "genie"})
    return _cohort_route_filters(payload, [])


def _genie_criteria(floor: int) -> dict[str, object]:
    return {
        "source": "genie",
        "marketing_eligibility": "Eligible only",
        "borrower_ids": [],
        "criteria_hash": "abc123",
        "criteria_keys": ["result_filters"],
        "source_assets": [],
        "conversation_id": "conv-1",
        "message_id": "msg-1",
        "question_hash": "q-1",
        "row_count": 12,
        "route": "/lead-queue",
        "result_filters": {
            "states": ["IL"],
            "min_rate_spread_bps": floor,
            "source": "genie",
        },
    }


# --- the reviewed range itself ---------------------------------------------


def test_reviewed_minimum_clears_the_live_observed_minimum() -> None:
    """The bound is a domain guard, not a business threshold.

    It has to admit every floor a real answer could state. The live column
    bottoms out at -569 bps; anything at or above the reviewed minimum is
    accepted, so no legitimate question is refused.
    """

    assert _REVIEWED_MINIMUM < _LIVE_OBSERVED_MINIMUM
    assert _route_filters(states=["IL"], min_rate_spread_bps=_LIVE_OBSERVED_MINIMUM)[
        "min_rate_spread_bps"
    ] == _LIVE_OBSERVED_MINIMUM


def test_percentage_floors_keep_their_zero_bound() -> None:
    """Widening the SIGNED spread floor must not widen the percentages."""

    assert GENIE_NUMERIC_FILTER_BOUNDS["min_opportunity_score"] == (0, 100)
    assert GENIE_NUMERIC_FILTER_BOUNDS["min_equity_pct"] == (0, 100)


def test_every_vocabulary_reads_the_same_bounds_object() -> None:
    """Structural guard: re-forking a private copy must fail here, not in prod.

    The behavioural tests below would catch a divergent RANGE, but only for the
    values they happen to try. This catches the fork itself.
    """

    from backend.services.audit_store import _RESULT_FILTER_NUMERIC_BOUNDS
    from backend.services.genie_actions import _REPLAYABLE_NUMERIC_FILTERS
    from backend.services.lead_query_helpers import COHORT_NUMERIC_FILTER_BOUNDS

    for vocabulary in (
        _REPLAYABLE_NUMERIC_FILTERS,
        _RESULT_FILTER_NUMERIC_BOUNDS,
        COHORT_NUMERIC_FILTER_BOUNDS,
    ):
        assert vocabulary is GENIE_NUMERIC_FILTER_BOUNDS
    # The two that validate through the shared helper rather than the mapping.
    assert campaign_json_projection.GENIE_NUMERIC_FILTER_BOUNDS is GENIE_NUMERIC_FILTER_BOUNDS
    assert campaign_treatment.is_reviewed_numeric_floor is is_reviewed_numeric_floor


def test_the_canonical_mapping_is_not_mutable() -> None:
    """Five modules alias one object, so a stray write would move all of them."""

    with pytest.raises(TypeError):
        GENIE_NUMERIC_FILTER_BOUNDS["min_equity_pct"] = (-100, 100)  # type: ignore[index]


# --- vocabulary 1: the Genie action that writes the cohort row --------------


def test_vocabulary_1_cohort_writer_keeps_a_negative_floor() -> None:
    out = _route_filters(states=["IL"], min_rate_spread_bps=_LIVE_SHAPED_FLOOR)
    assert out["min_rate_spread_bps"] == _LIVE_SHAPED_FLOOR
    # It was applied, not disclosed as un-replayable.
    assert "unreplayable_filters" not in out


# --- vocabulary 2: the audit ledger ----------------------------------------


def test_vocabulary_2_audit_ledger_round_trips_a_negative_floor() -> None:
    """Nested in a Genie cohort's result_filters, and losslessly."""

    payload = SimpleNamespace(
        action_type="open_cohort",
        conversation_id="conv-1",
        message_id="msg-1",
        question_hash="q-1",
        borrower_ids=[],
        route="/lead-queue",
        criteria={
            "source": "genie",
            "row_count": 12,
            "result_filters": {"states": ["IL"], "min_rate_spread_bps": _LIVE_SHAPED_FLOOR},
        },
    )
    metadata = json.loads(_reviewed_audit_metadata("genie.open_cohort", _audit_payload(payload)))
    assert metadata["result_filters"]["min_rate_spread_bps"] == _LIVE_SHAPED_FLOOR


def test_vocabulary_2_view_leads_row_records_a_negative_floor() -> None:
    """Top level of a VIEW_LEADS row -- the other place audit bounds it."""

    metadata = build_safe_audit_metadata(
        {"min_rate_spread_bps": _LIVE_SHAPED_FLOOR, "limit": 50},
        action="view_leads_ranked",
    )
    assert metadata["min_rate_spread_bps"] == _LIVE_SHAPED_FLOOR


# --- vocabulary 3: the campaign JSON projection (approval decision proof) ---


def test_vocabulary_3_draft_campaign_with_a_negative_floor_stays_approvable() -> None:
    projected = project_public_campaign_json_field(
        "criteria", _genie_criteria(_LIVE_SHAPED_FLOOR)
    )
    assert isinstance(projected, dict)
    assert projected["result_filters"]["min_rate_spread_bps"] == _LIVE_SHAPED_FLOOR


# --- vocabulary 4: the Lead Queue reading the cohort row back ---------------


def test_vocabulary_4_queue_reads_a_negative_floor_back() -> None:
    assert (
        cohort_numeric_floor(
            {"min_rate_spread_bps": _LIVE_SHAPED_FLOOR}, "min_rate_spread_bps"
        )
        == _LIVE_SHAPED_FLOOR
    )


# --- vocabulary 5: the approved campaign treatment set ----------------------


def test_vocabulary_5_treatment_set_carries_a_negative_floor() -> None:
    filters = cohort_filters_from_campaign_criteria(
        {
            "source": "genie",
            "result_filters": {
                "states": ["IL"],
                "min_rate_spread_bps": _LIVE_SHAPED_FLOOR,
                "source": "genie",
            },
        }
    )
    assert filters.min_rate_spread_bps == _LIVE_SHAPED_FLOOR


# --- the SQL the floor compiles to -----------------------------------------


def test_negative_floor_binds_as_a_parameter_not_a_literal() -> None:
    """A leading `-` must never be string-spliced into the predicate."""

    clause, params = LeadCohortQuerySupport.numeric_floor_clause(
        min_rate_spread_bps=_LIVE_SHAPED_FLOOR
    )
    assert clause == "AND b.rate_spread_bps >= :replay_min_rate_spread_bps"
    assert params == {"replay_min_rate_spread_bps": _LIVE_SHAPED_FLOOR}
    assert str(_LIVE_SHAPED_FLOOR) not in clause


# --- the shareable cohort URL ----------------------------------------------


def test_cohort_route_url_carries_the_leading_minus() -> None:
    """`-` is unreserved, so urlencode leaves it literal -- assert it does."""

    filters = _route_filters(states=["IL"], min_rate_spread_bps=_LIVE_SHAPED_FLOOR)
    route = _route_with_cohort("/lead-queue", cohort_id="c-1", filters=filters)
    assert f"min_rate_spread_bps={_LIVE_SHAPED_FLOOR}" in route
    assert "%2D" not in route.upper()
    # A shared link that lost the sign would state a strictly narrower cohort
    # than the queue applies.
    assert "min_rate_spread_bps=25" not in route


# --- the signed handoff fingerprint ----------------------------------------


def _handoff_filters(floor: int | None) -> dict[str, object]:
    return normalise_lead_queue_handoff_filters(
        LeadCohortFilters(segment=None, state_codes=["IL"], min_rate_spread_bps=floor)
    )


def test_handoff_fingerprint_carries_a_negative_floor_losslessly() -> None:
    normalized = _handoff_filters(_LIVE_SHAPED_FLOOR)
    assert normalized["min_rate_spread_bps"] == _LIVE_SHAPED_FLOOR
    # Round-trips through the canonical JSON the fingerprint is taken over.
    assert json.loads(json.dumps(normalized))["min_rate_spread_bps"] == _LIVE_SHAPED_FLOOR


def test_handoff_fingerprint_is_bound_to_the_sign() -> None:
    """Dropping or flipping the sign must not verify against the same proof."""

    signed = handoff_filters_fingerprint(_handoff_filters(_LIVE_SHAPED_FLOOR))
    assert signed != handoff_filters_fingerprint(_handoff_filters(-_LIVE_SHAPED_FLOOR))
    assert signed != handoff_filters_fingerprint(_handoff_filters(None))
    # Stable for the same value, or every read would look stale.
    assert signed == handoff_filters_fingerprint(_handoff_filters(_LIVE_SHAPED_FLOOR))


# --- the guards that must NOT have moved ------------------------------------


@pytest.mark.parametrize("key", ["min_opportunity_score", "min_equity_pct"])
@pytest.mark.parametrize("value", [-1, -25, -1000])
def test_percentage_floors_still_reject_negatives_in_every_vocabulary(
    key: str, value: int
) -> None:
    from fastapi import HTTPException

    from backend.services.audit_store import AuditMetadataValueViolation

    with pytest.raises(HTTPException) as action_exc:
        _route_filters(states=["IL"], **{key: value})
    assert action_exc.value.status_code == 400

    with pytest.raises(AuditMetadataValueViolation):
        _reviewed_audit_metadata(
            "genie.open_cohort",
            {
                "action_type": "open_cohort",
                "result_filters": {"states": ["IL"], key: value},
            },
        )

    with pytest.raises(ValueError):
        project_public_campaign_json_field(
            "criteria",
            {
                **_genie_criteria(0),
                "result_filters": {"states": ["IL"], key: value, "source": "genie"},
            },
        )

    with pytest.raises(HTTPException) as queue_exc:
        cohort_numeric_floor({key: value}, key)
    assert queue_exc.value.status_code == 422

    with pytest.raises(ValueError):
        cohort_filters_from_campaign_criteria(
            {"source": "genie", "result_filters": {key: value, "source": "genie"}}
        )


@pytest.mark.parametrize("value", [_REVIEWED_MINIMUM - 1, 5001, -100_000])
def test_spread_floor_outside_the_reviewed_range_is_still_rejected(value: int) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _route_filters(states=["IL"], min_rate_spread_bps=value)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "value",
    [True, False, "-25 bps", "minus 25", "-25%", -24.5, [-25], {"gte": -25}, (-25,)],
)
def test_a_negative_range_did_not_open_the_type_guards(value: object) -> None:
    """Bools, prose, fractions, and containers are still not floors."""

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _route_filters(states=["IL"], min_rate_spread_bps=value)
    assert exc.value.status_code == 400


def test_an_omitted_floor_is_not_a_bad_floor() -> None:
    """The vocabulary has always distinguished "no threshold" from "invalid"."""

    assert "min_rate_spread_bps" not in _route_filters(states=["IL"], min_rate_spread_bps=None)
    assert "min_rate_spread_bps" not in _route_filters(states=["IL"], min_rate_spread_bps="")


def test_a_numeric_string_keeps_its_sign_through_coercion() -> None:
    """Pre-existing behaviour: a whole-number string is coerced, not rejected.

    Pinned here because the coercion is the one place a leading `-` could be
    silently dropped -- ``int("-25")`` must not become 25, and the stored value
    must be the same int as the unquoted form so the ledger (which requires a
    real ``int``) accepts it.
    """

    quoted = _route_filters(states=["IL"], min_rate_spread_bps="-25")
    assert quoted["min_rate_spread_bps"] == _LIVE_SHAPED_FLOOR
    assert quoted == _route_filters(states=["IL"], min_rate_spread_bps=_LIVE_SHAPED_FLOOR)
    assert isinstance(quoted["min_rate_spread_bps"], int)


def test_an_unreplayable_only_cohort_is_still_empty() -> None:
    """The 400-on-no-replayable-filters gate must not have been widened."""

    assert _route_filters(min_credit_score=700) == {}
    assert _route_filters(**{"rate_spread_bps BETWEEN -50 AND 0": True}) == {}
