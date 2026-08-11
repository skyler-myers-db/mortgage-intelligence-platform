"""A Genie answer's count and the Lead Queue it hands off to must MATCH.

Live measurement 2026-08-11 against paychex gold, one question three ways:

    Genie: in-the-money borrowers in IL                     1,766
    queue replaying segment_codes=[itm] + states=[IL]       1,766   exact
    queue replaying states=[IL] only (segment lost)        76,711   43x
    Genie: in-the-money in IL, opportunity_score >= 80         32
      -> the queue had no score predicate at all, replayed  1,766   55x

The reviewed replay subset used to be geography/segment/lender only, so every
numeric threshold an answer used — opportunity_score, equity_pct,
rate_spread_bps — was dropped on handoff and the queue answered a broader
question than the one the user just read, under the same heading. Disclosing
the drop was half the fix; these tests pin the other half: the three reviewed
floors are now REPLAYED, and only genuinely unreviewed predicates are
disclosed. The closed vocabulary is unchanged — an unknown key is still
rejected or named, never passed through.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.services.lead_cohort_source as cohort_source
from backend.main import app
from backend.services.genie_actions import (
    _MAX_UNREPLAYABLE_FILTER_KEYS,
    _REPLAYABLE_FILTER_KEYS,
    _REPLAYABLE_NUMERIC_FILTERS,
    _audit_payload,
    _cohort_route_filters,
    _reviewed_audit_metadata,
    _route_with_cohort,
)
from backend.services.repositories import get_lead_repository

# One synthetic book with the same SHAPE as the live measurement: a broad
# in-the-money cohort in IL, of which only a few clear a score threshold. The
# counts below are computed from it, never hardcoded.
_POPULATION: list[dict[str, Any]] = [
    {"state": "IL", "segments": ["itm"], "score": 92},
    {"state": "IL", "segments": ["itm"], "score": 88},
    {"state": "IL", "segments": ["itm"], "score": 81},
    {"state": "IL", "segments": ["itm"], "score": 74},
    {"state": "IL", "segments": ["itm"], "score": 61},
    {"state": "IL", "segments": ["itm", "equity"], "score": 55},
    {"state": "IL", "segments": ["itm"], "score": 42},
    {"state": "IL", "segments": ["itm"], "score": 12},
    {"state": "IL", "segments": ["equity"], "score": 97},
    {"state": "IL", "segments": ["listed"], "score": 84},
    {"state": "WI", "segments": ["itm"], "score": 95},
    {"state": "WI", "segments": ["itm"], "score": 44},
]
_ITM_IN_IL = 8
_ITM_IN_IL_SCORED_80 = 3


def _expected_count(
    *,
    states: list[str] | None = None,
    segments: list[str] | None = None,
    min_score: int | None = None,
) -> int:
    return len(
        [
            row
            for row in _POPULATION
            if (states is None or row["state"] in states)
            and (segments is None or any(code in row["segments"] for code in segments))
            and (min_score is None or row["score"] >= min_score)
        ]
    )


def _route_filters(**filters: object) -> dict[str, object]:
    payload = SimpleNamespace(criteria={"result_filters": filters, "source": "genie"})
    return _cohort_route_filters(payload, [])


def test_numeric_thresholds_are_replayed_not_dropped() -> None:
    out = _route_filters(
        states=["IL"],
        segment_codes=["itm"],
        min_opportunity_score=80,
        min_equity_pct=50,
        min_rate_spread_bps=100,
    )
    assert out["states"] == ["IL"]
    assert out["segment_codes"] == ["itm"]
    assert out["min_opportunity_score"] == 80
    assert out["min_equity_pct"] == 50
    assert out["min_rate_spread_bps"] == 100
    # Nothing was lost, so there is nothing to disclose.
    assert "unreplayable_filters" not in out


def test_zero_floors_survive_because_a_spread_floor_of_zero_is_a_predicate() -> None:
    # rate_spread_bps can be negative, so `>= 0` narrows the book. Dropping it
    # as a "no-op" would replay broader than the answer.
    out = _route_filters(states=["IL"], min_rate_spread_bps=0)
    assert out["min_rate_spread_bps"] == 0


def test_fully_replayable_cohorts_carry_no_disclosure() -> None:
    out = _route_filters(states=["IL"], segment_codes=["itm"])
    assert "unreplayable_filters" not in out


def test_unknown_predicates_are_still_named_as_unreplayable() -> None:
    """Closed vocabulary: a key the queue cannot apply is disclosed, not applied."""

    out = _route_filters(
        states=["IL"],
        segment_codes=["itm"],
        min_opportunity_score=80,
        **{"LTV <= 80": True, "min_credit_score": 700},
    )
    assert out["min_opportunity_score"] == 80
    named = out["unreplayable_filters"]
    assert "min_credit_score" in named
    # Model-authored key names are data: folded to the audit vocabulary's
    # identifier shape before they reach Lakebase or the audit ledger.
    assert "ltv_80" in named
    assert all(key.replace("_", "").isalnum() for key in named)
    # The unknown keys never became filters.
    assert not {"LTV <= 80", "min_credit_score"} & set(out)


def test_disclosure_never_becomes_the_only_filter() -> None:
    """An unreplayable-only cohort must stay empty so the action 400s.

    ``_materialize_genie_cohort`` rejects a cohort with no replayable filter.
    If the disclosure key counted as a filter it would smuggle an empty
    cohort past that gate and the queue would replay *everything*.
    """

    assert _route_filters(min_credit_score=700) == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_opportunity_score", 101),
        ("min_opportunity_score", -1),
        ("min_opportunity_score", "eighty"),
        ("min_opportunity_score", "80%"),
        ("min_opportunity_score", 80.5),
        ("min_opportunity_score", True),
        ("min_opportunity_score", [80]),
        ("min_equity_pct", 101),
        # Percentages have no negative half; widening the SIGNED spread floor
        # must not widen these two.
        ("min_equity_pct", -1),
        ("min_rate_spread_bps", 5001),
        ("min_rate_spread_bps", -1001),
        ("min_rate_spread_bps", "-25 bps"),
        ("min_rate_spread_bps", -24.5),
        ("min_rate_spread_bps", [-25]),
    ],
)
def test_numeric_floors_must_be_bounded_whole_numbers(field: str, value: object) -> None:
    with pytest.raises(HTTPException) as exc:
        _route_filters(states=["IL"], **{field: value})
    assert exc.value.status_code == 400


def test_negative_spread_floor_survives_the_cohort_writer() -> None:
    """Half the book has a NEGATIVE spread, so `>= -25` is a real question.

    Live 2026-08-11 on paychex gold: rate_spread_bps is negative on 2,561,392
    of 5,156,184 rows (min -569, p50 0). The floor used to be clamped at 0
    everywhere downstream, so a retention-side answer was rejected and merely
    disclosed -- and the queue replayed BROADER than the answer.
    """

    out = _route_filters(states=["IL"], min_rate_spread_bps=-25)
    assert out["min_rate_spread_bps"] == -25


def test_replayable_key_set_matches_what_the_queue_reads() -> None:
    # Every key the queue replays in backend/api/leads.py must be declared
    # replayable, or a real filter would be reported as dropped.
    for key in (
        "zips",
        "county",
        "counties",
        "states",
        "segment_codes",
        "borrower_ids",
        "min_opportunity_score",
        "min_equity_pct",
        "min_rate_spread_bps",
    ):
        assert key in _REPLAYABLE_FILTER_KEYS


def test_disclosure_list_is_bounded() -> None:
    out = _route_filters(states=["IL"], **{f"unknown_{i}": i for i in range(40)})
    assert len(out["unreplayable_filters"]) <= _MAX_UNREPLAYABLE_FILTER_KEYS


def test_cohort_route_url_states_the_thresholds_it_applies() -> None:
    filters = _route_filters(states=["IL"], segment_codes=["itm"], min_opportunity_score=80)
    route = _route_with_cohort("/lead-queue", cohort_id="c-1", filters=filters)
    assert "min_opportunity_score=80" in route
    assert "cohort_id=c-1" in route


def test_replayed_and_disclosed_filters_pass_the_audit_vocabulary() -> None:
    """The governed action must be able to WRITE what it just decided.

    ``result_filters`` is a closed vocabulary in the audit ledger too. A key
    the cohort emits but the ledger rejects turns a working handoff into an
    unhandled 500 *after* the Lakebase cohort row is already written.
    """

    payload = SimpleNamespace(
        action_type="open_cohort",
        conversation_id="conv-1",
        message_id="msg-1",
        question_hash="q-1",
        borrower_ids=[],
        route="/lead-queue",
        criteria={
            "source": "genie",
            "row_count": _ITM_IN_IL_SCORED_80,
            "result_filters": {
                "states": ["IL"],
                "segment_codes": ["itm"],
                "min_opportunity_score": 80,
                "min_equity_pct": 40,
                "min_rate_spread_bps": 75,
                "LTV <= 80": True,
            },
        },
    )
    metadata = json.loads(_reviewed_audit_metadata("genie.open_cohort", _audit_payload(payload)))
    filters = metadata["result_filters"]
    assert filters["min_opportunity_score"] == 80
    assert filters["unreplayable_filters"] == ["ltv_80"]


class _CountingLeadRepo:
    """Counts a synthetic book with the predicates the route hands it.

    Rows are not the subject here — the reconciliation headers come from
    ``count`` — so ``list`` returns empty and the route skips sales-state
    hydration.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> list[object]:
        self.calls.append(kwargs)
        return []

    def count(self, **kwargs: object) -> int:
        states = kwargs.get("state_codes")
        segments = kwargs.get("segment_codes")
        min_score = kwargs.get("min_opportunity_score")
        return _expected_count(
            states=list(states) if isinstance(states, list) else None,
            segments=list(segments) if isinstance(segments, list) else None,
            min_score=int(min_score) if isinstance(min_score, int) else None,
        )


class _CohortLakebase:
    def __init__(self, route_filters: dict[str, object], row_count: int) -> None:
        self._route_filters = route_filters
        self._row_count = row_count

    def fetchone(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object]:
        _ = (sql, params)
        return {"route_filters": json.dumps(self._route_filters), "row_count": self._row_count}


def _get_leads(monkeypatch: pytest.MonkeyPatch, lakebase: _CohortLakebase, cohort_id: str):
    repo = _CountingLeadRepo()
    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: repo
    monkeypatch.setattr(cohort_source, "get_lakebase_client", lambda: lakebase)
    try:
        response = TestClient(app).get(
            f"/api/leads?cohort_id={cohort_id}",
            headers={"X-Forwarded-Email": "lo@example.com"},
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior
    return response, repo


def test_score_narrowed_cohort_replays_to_the_stated_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline: the queue reproduces the answer's population exactly."""

    stated = _expected_count(states=["IL"], segments=["itm"], min_score=80)
    assert stated == _ITM_IN_IL_SCORED_80
    lakebase = _CohortLakebase(
        {
            "states": ["IL"],
            "segment_codes": ["itm"],
            "segment_mode": "any",
            "min_opportunity_score": 80,
            "source": "genie",
        },
        stated,
    )

    response, repo = _get_leads(monkeypatch, lakebase, "44444444-4444-4444-4444-444444444444")

    assert response.status_code == 200
    assert repo.calls[-1]["min_opportunity_score"] == 80
    assert response.headers["X-Cohort-Stated-Count"] == str(stated)
    assert response.headers["X-Total-Matching"] == str(stated)
    # Counts match, so there is no delta and nothing was dropped.
    assert "X-Cohort-Count-Delta" not in response.headers
    assert "X-Cohort-Unreplayable-Filters" not in response.headers
    # Regression guard on the measured defect: without the floor this same
    # cohort answers a strictly broader question.
    assert _expected_count(states=["IL"], segments=["itm"]) == _ITM_IN_IL > stated


def test_equity_floor_replays_through_the_reviewed_portfolio_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lakebase = _CohortLakebase(
        {"states": ["IL"], "min_equity_pct": 45, "source": "genie"},
        4,
    )

    response, repo = _get_leads(monkeypatch, lakebase, "55555555-5555-5555-5555-555555555555")

    assert response.status_code == 200
    criteria = repo.calls[-1]["portfolio_criteria"]
    assert criteria.min_equity_pct == 45
    # It reuses the existing equity predicate rather than inventing a second.
    assert "min_equity_pct" not in repo.calls[-1]


def test_unreplayable_cohort_still_discloses_the_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed disclosure survives: unknown keys are named, not applied."""

    lakebase = _CohortLakebase(
        {
            "states": ["IL"],
            "segment_codes": ["itm"],
            "segment_mode": "any",
            "unreplayable_filters": ["ltv_80"],
            "source": "genie",
        },
        _ITM_IN_IL_SCORED_80,
    )

    response, _repo = _get_leads(monkeypatch, lakebase, "66666666-6666-6666-6666-666666666666")

    assert response.status_code == 200
    assert response.headers["X-Cohort-Stated-Count"] == str(_ITM_IN_IL_SCORED_80)
    assert response.headers["X-Total-Matching"] == str(_ITM_IN_IL)
    assert response.headers["X-Cohort-Count-Delta"] == str(_ITM_IN_IL - _ITM_IN_IL_SCORED_80)
    assert response.headers["X-Cohort-Unreplayable-Filters"] == "ltv_80"


def test_negative_spread_floor_reaches_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retention-side cohort must replay its own floor, sign intact.

    Half of gold has a negative spread, so a cohort stored at ``>= -25`` that
    replayed without the predicate would open roughly twice the population the
    answer reported -- the same class of divergence as the 55x score defect,
    just on the other side of zero.
    """

    lakebase = _CohortLakebase(
        {"states": ["IL"], "min_rate_spread_bps": -25, "source": "genie"},
        4,
    )

    response, repo = _get_leads(monkeypatch, lakebase, "88888888-8888-8888-8888-888888888888")

    assert response.status_code == 200
    assert repo.calls[-1]["min_rate_spread_bps"] == -25


def test_a_negative_spread_floor_alone_makes_a_cohort_replayable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It compiles to a real predicate, so it satisfies the fail-closed gate.

    Contrast ``test_a_zero_floor_alone_is_not_a_replayable_cohort``: a 0 score
    or equity floor excludes nobody and must NOT pass the gate. A spread floor
    of any value does exclude somebody, so it may stand alone.
    """

    lakebase = _CohortLakebase({"min_rate_spread_bps": -25, "source": "genie"}, 4)

    response, repo = _get_leads(monkeypatch, lakebase, "99999999-9999-9999-9999-999999999999")

    assert response.status_code == 200
    assert repo.calls[-1]["min_rate_spread_bps"] == -25


@pytest.mark.parametrize(
    "stored",
    [
        {"states": ["IL"], "min_opportunity_score": 101},
        {"states": ["IL"], "min_opportunity_score": "80%"},
        {"states": ["IL"], "min_rate_spread_bps": -1001},
        {"states": ["IL"], "min_equity_pct": 100.5},
    ],
)
def test_out_of_range_stored_floors_are_rejected_not_ignored(
    monkeypatch: pytest.MonkeyPatch,
    stored: dict[str, object],
) -> None:
    # Ignoring a malformed floor would replay broader than the answer, which
    # is the defect. Fail the read instead.
    response, repo = _get_leads(
        monkeypatch,
        _CohortLakebase(stored, 3),
        "77777777-7777-7777-7777-777777777777",
    )
    assert response.status_code == 422
    assert repo.calls == []


def test_draft_campaign_built_from_a_narrowed_answer_stays_approvable() -> None:
    """The campaign JSON projection gates the approval decision proof.

    A ``create_draft_campaign`` action stores the same ``result_filters``, so a
    key the cohort emits but the projection rejects produces a campaign that
    can never be approved (``campaign criteria are invalid`` at decision time).
    """

    from backend.schemas.portfolio import project_public_campaign_json_field

    criteria = {
        "source": "genie",
        "marketing_eligibility": "Eligible only",
        "borrower_ids": [],
        "criteria_hash": "abc123",
        "criteria_keys": ["result_filters"],
        "source_assets": [],
        "conversation_id": "conv-1",
        "message_id": "msg-1",
        "question_hash": "q-1",
        "row_count": _ITM_IN_IL_SCORED_80,
        "route": "/lead-queue",
        "result_filters": {
            "states": ["IL"],
            "segment_codes": ["itm"],
            "segment_mode": "any",
            "min_opportunity_score": 80,
            "source": "genie",
            "unreplayable_filters": ["ltv_80"],
        },
    }
    projected = project_public_campaign_json_field("criteria", criteria)
    assert isinstance(projected, dict)
    assert projected["result_filters"]["min_opportunity_score"] == 80

    with pytest.raises(ValueError):
        project_public_campaign_json_field(
            "criteria",
            {**criteria, "result_filters": {"states": ["IL"], "min_credit_score": 700}},
        )


def test_draft_campaign_treatment_carries_the_same_floors_as_the_queue() -> None:
    """The approved population must be the one the answer described."""

    from backend.services.campaign_treatment import cohort_filters_from_campaign_criteria

    filters = cohort_filters_from_campaign_criteria(
        {
            "source": "genie",
            "result_filters": {
                "states": ["IL"],
                "segment_codes": ["itm"],
                "min_opportunity_score": 80,
                "min_equity_pct": 40,
                "min_rate_spread_bps": 75,
                "source": "genie",
            },
        }
    )
    assert filters.min_opportunity_score == 80
    assert filters.min_rate_spread_bps == 75
    assert filters.portfolio_criteria is not None
    assert filters.portfolio_criteria.min_equity_pct == 40

    with pytest.raises(ValueError):
        cohort_filters_from_campaign_criteria(
            {"source": "genie", "result_filters": {"min_opportunity_score": 101}}
        )


def test_floor_only_cohort_is_replayable_and_not_a_whole_book_replay() -> None:
    # A national score floor is a real predicate the queue applies verbatim,
    # so it is allowed to stand alone — unlike a disclosure key.
    assert _route_filters(min_opportunity_score=80)["min_opportunity_score"] == 80
    assert set(_REPLAYABLE_NUMERIC_FILTERS) == {
        "min_opportunity_score",
        "min_equity_pct",
        "min_rate_spread_bps",
    }


# --- Floor extraction must not lift a threshold that filters NOTHING --------
#
# Found by running the extractor against a real captured Genie turn and its
# neighbours (2026-08-11). A false floor is worse than no floor: no floor
# replays BROADER, which the disclosure headers already surface, while a false
# floor replays NARROWER and the user acts on a silently truncated list.


def _floors(sql: str) -> dict[str, int]:
    from backend.services.repositories.databricks_genie_actions import (
        _numeric_floors_from_sql,
    )

    return _numeric_floors_from_sql(sql)


def test_live_captured_turn_still_lifts_its_floor() -> None:
    """Verbatim SQL from the deployed space, 2026-08-11."""

    live = (
        "SELECT\n  COUNT(*) AS in_the_money_borrowers,\n"
        "  MAX(refreshed_at) AS refreshed_at\n"
        "FROM mip.gold.borrower_360\n"
        "WHERE in_the_money = TRUE\n  AND state = 'IL'\n"
        "  AND opportunity_score >= 80\n  AND opportunity_score IS NOT NULL"
    )
    assert _floors(live) == {"min_opportunity_score": 80}


@pytest.mark.parametrize(
    "sql",
    [
        # A CASE projects a label; it selects no rows.
        "SELECT CASE WHEN opportunity_score >= 80 THEN 'a' ELSE 'b' END FROM mip.gold.borrower_360",
        "SELECT CASE WHEN x THEN CASE WHEN opportunity_score >= 80 THEN 1 END END FROM mip.gold.borrower_360",
        # Comments are not predicates. The planned deep sweep appends each
        # sub-question as a trailing comment, so a question that merely
        # MENTIONS a threshold would otherwise become a filter.
        "SELECT * FROM mip.gold.borrower_360 -- opportunity_score >= 80\nWHERE state='IL'",
        "SELECT * FROM mip.gold.borrower_360 /* opportunity_score >= 80 */ WHERE state='IL'",
        "SELECT * FROM mip.gold.borrower_360 WHERE state='IL' "
        "-- [How many have opportunity_score >= 80?]",
    ],
)
def test_non_filtering_thresholds_lift_no_floor(sql: str) -> None:
    assert _floors(sql) == {}


def test_a_real_predicate_still_wins_past_a_case_expression() -> None:
    sql = (
        "SELECT CASE WHEN opportunity_score >= 80 THEN 'a' END "
        "FROM mip.gold.borrower_360 WHERE opportunity_score >= 90"
    )
    assert _floors(sql) == {"min_opportunity_score": 90}


# --- A floor only "narrows" when it compiles to a predicate -----------------
#
# Adversarial review 2026-08-11: `min_equity_pct: 0` satisfied the
# "cohort has replayable filters" gate but build_preview_predicates drops the
# clause unless the floor is > 0, so /leads returned the ENTIRE eligible book
# (216,403 live) under a cohort the user believed was narrowed. An explicit
# fail-closed gate had become fail-open.


@pytest.mark.parametrize("zero_floor", ["min_equity_pct", "min_opportunity_score"])
def test_a_zero_floor_alone_is_not_a_replayable_cohort(zero_floor: str) -> None:
    """The action must 400 rather than open the whole book."""

    from backend.services.genie_actions import _cohort_route_filters

    payload = SimpleNamespace(criteria={"result_filters": {zero_floor: 0}, "source": "genie"})
    stored = _cohort_route_filters(payload, [])
    # The floor is recorded faithfully...
    assert stored.get(zero_floor) == 0
    # ...but it must not be the thing that makes the cohort replayable.
    narrowing = bool(stored.get("min_opportunity_score")) or bool(stored.get("min_equity_pct"))
    assert not narrowing


def test_a_zero_rate_spread_floor_still_narrows() -> None:
    """rate_spread_bps is negative on ~half the book, so `>= 0` is real."""

    from backend.services.genie_actions import _cohort_route_filters

    payload = SimpleNamespace(
        criteria={"result_filters": {"min_rate_spread_bps": 0}, "source": "genie"}
    )
    stored = _cohort_route_filters(payload, [])
    assert stored["min_rate_spread_bps"] == 0
    assert stored["min_rate_spread_bps"] is not None


@pytest.mark.parametrize(
    "stored_key",
    [
        "ltv≤80",            # non-latin-1 -> UnicodeEncodeError out of the route
        "ltv\r\nX-Injected: pwned",  # CRLF -> h11 rejects the whole response
        "a" * 5000,                  # unbounded length
    ],
)
def test_stored_disclosure_names_are_refolded_before_reaching_a_header(stored_key: str) -> None:
    """Rows written by earlier builds stored these names unfolded."""

    safe = "".join(c for c in stored_key.lower() if c.isalnum() or c == "_")[:40]
    assert len(safe) <= 40
    safe.encode("latin-1")  # must not raise
    assert "\r" not in safe and "\n" not in safe
