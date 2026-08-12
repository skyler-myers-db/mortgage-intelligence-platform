"""The `(city, state)` cohort filter, pinned at the layer each defect lives in.

A city-grain Genie answer could not hand off an honest cohort. Measured live on
paychex gold 2026-08-11 against an answer stating Chicago = 523,010 cash-out
candidates:

    rows [{city}]        -> `/lead-queue?product=Cash-out`   3,474,216   6.6x
    rows [{city, state}] -> the STATE substituted             1,181,043   2.3x

Both were merely disclosed. `cities=CHICAGO~IL` + cash-out returns 523,010 --
the number the answer states (re-measured live 2026-08-12).

Each test below asserts at the layer that can actually be wrong:

1. the six closed vocabularies agree                (a key one accepts and
                                                     another rejects 500s
                                                     AFTER the cohort is written)
2. the row reader pairs WITHIN a row                (a cross product invents
                                                     CYPRESS~TX from a CA row)
3. a city with no state emits nothing               (fail closed)
4. a city with a state emits the CITY, not the state
5. list / count / cohort_identity share one predicate, bound
6. the router parses a multi-word city off the URL
7. the handoff fingerprint moves when only `cities` moves
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.genie_geo_filters import (
    CITY_STATE_PAIR_RE,
    GENIE_CITY_FILTER_KEY,
    MAX_CITY_FILTER_VALUES,
    format_city_state_pair,
    parse_city_state_pair,
)
from backend.schemas.lead import LeadSummary
from backend.services.repositories import get_lead_repository
from backend.services.repositories.databricks_genie_actions import (
    _route_from_answer_rows,
    _row_pairs,
)
from backend.services.repositories.databricks_lead_cohorts import (
    LeadCohortFilters,
    normalise_lead_queue_handoff_filters,
)
from backend.services.repositories.databricks_leads import DatabricksLeadRepository

_B360 = "mip.gold.borrower_360"
_CITY_SQL = (
    f"SELECT city, state, COUNT(*) AS n FROM {_B360} "
    "WHERE recommended_offer_code = 'cash_out' GROUP BY city, state"
)


# --- 1. the six closed vocabularies ----------------------------------------
#
# PR #191 added a key to ONE of these. Every Genie action carrying it would
# have raised `AuditMetadataValueViolation` -- a RuntimeError, which
# `handle_genie_action`'s `except ValueError` does not catch -- as an
# unhandled 500, AFTER the Lakebase cohort row was already written.


def test_cities_is_in_every_closed_vocabulary_that_gates_the_handoff() -> None:
    from backend.schemas.campaign_json_projection import _GENIE_REPLAY_FILTER_KEYS
    from backend.services.audit_store import _ALLOWED_RESULT_FILTER_KEYS
    from backend.services.genie_actions import (
        _LEAD_QUEUE_REPLAY_KEYS,
        _REPLAYABLE_FILTER_KEYS,
    )
    from backend.services.repositories.databricks_genie_actions import (
        _REPLAYABLE_ROUTE_FILTER_KEYS,
    )

    vocabularies = {
        "databricks_genie_actions._REPLAYABLE_ROUTE_FILTER_KEYS": (
            _REPLAYABLE_ROUTE_FILTER_KEYS
        ),
        "genie_actions._REPLAYABLE_FILTER_KEYS": _REPLAYABLE_FILTER_KEYS,
        "genie_actions._LEAD_QUEUE_REPLAY_KEYS": _LEAD_QUEUE_REPLAY_KEYS,
        "audit_store._ALLOWED_RESULT_FILTER_KEYS": _ALLOWED_RESULT_FILTER_KEYS,
        "campaign_json_projection._GENIE_REPLAY_FILTER_KEYS": _GENIE_REPLAY_FILTER_KEYS,
    }
    missing = [name for name, vocab in vocabularies.items() if GENIE_CITY_FILTER_KEY not in vocab]
    assert not missing, f"{GENIE_CITY_FILTER_KEY} missing from: {missing}"


def test_the_sixth_vocabulary_projects_cities_into_the_treatment_set() -> None:
    """``cohort_filters_from_campaign_criteria`` is a mapping, not a key set.

    A key it ignores materializes the STATE the pairs live in rather than the
    cities the approved answer described.
    """

    from backend.services.campaign_treatment import cohort_filters_from_campaign_criteria

    filters = cohort_filters_from_campaign_criteria(
        {
            "source": "genie",
            "result_filters": {GENIE_CITY_FILTER_KEY: ["CHICAGO~IL"]},
        }
    )
    assert filters.city_states == ["CHICAGO~IL"]


def test_the_audit_ledger_accepts_what_the_cohort_writer_emits() -> None:
    """Same regex on both sides, so neither can refuse the other's pair."""

    from backend.services.audit_store import _assert_result_filters_value_policy

    _assert_result_filters_value_policy({GENIE_CITY_FILTER_KEY: ["FORT LAUDERDALE~FL"]})


def test_the_audit_ledger_still_refuses_a_bare_city_name() -> None:
    from backend.services.audit_store import (
        AuditMetadataValueViolation,
        _assert_result_filters_value_policy,
    )

    with pytest.raises(AuditMetadataValueViolation):
        _assert_result_filters_value_policy({GENIE_CITY_FILTER_KEY: ["CHICAGO"]})


# --- 2. the row reader pairs WITHIN a row ----------------------------------


def test_row_pairs_never_cross_products_two_rows() -> None:
    """The bug this reader exists to prevent.

    Reading city and state with two `_row_values` calls yields
    {CYPRESS, SUNNYVALE} x {CA, TX} = 4 pairs. Two of them are cohorts the
    answer never described, and both sit on the wrong side of a 14,631x split
    (CYPRESS is CA 14,630 / TX 1; SUNNYVALE is TX 3,833 / CA 1).
    """

    pairs = _row_pairs(
        [
            {"city": "CYPRESS", "state": "CA", "n": 14630},
            {"city": "SUNNYVALE", "state": "TX", "n": 3833},
        ],
        left_columns=("city", "situs_city"),
        right_columns=("state", "state_code"),
    )

    assert pairs == [("CYPRESS", "CA"), ("SUNNYVALE", "TX")]
    assert ("CYPRESS", "TX") not in pairs
    assert ("SUNNYVALE", "CA") not in pairs


def test_row_pairs_drops_a_row_that_carries_only_the_left_half() -> None:
    pairs = _row_pairs(
        [{"city": "CHICAGO", "n": 523010}, {"city": "HOUSTON", "state": "TX"}],
        left_columns=("city",),
        right_columns=("state",),
    )
    assert pairs == [("HOUSTON", "TX")]


# --- 3 & 4. what the route emits -------------------------------------------


def test_a_city_without_a_state_emits_no_geography_at_all() -> None:
    """Fail closed. Not `states`, not a guessed `cities` -- nothing."""

    route, filters = _route_from_answer_rows(
        question="Which city has the most cash-out candidates?",
        rows=[{"city": "CHICAGO", "n": 523010}],
        borrower_ids=[],
        sql_query=_CITY_SQL,
    )

    assert GENIE_CITY_FILTER_KEY not in filters
    assert "states" not in filters
    assert filters["city_grain_unreplayable"] is True
    assert "cities=" not in route
    assert "states=" not in route


def test_a_city_with_its_state_opens_the_city_and_not_the_state() -> None:
    route, filters = _route_from_answer_rows(
        question="Which city has the most cash-out candidates?",
        rows=[{"city": "CHICAGO", "state": "IL", "n": 523010}],
        borrower_ids=[],
        sql_query=_CITY_SQL,
    )

    assert filters[GENIE_CITY_FILTER_KEY] == ["CHICAGO~IL"]
    # The 2.3x substitution. The pair already carries IL; emitting `states`
    # beside it re-opens all 1,181,043 of Illinois.
    assert "states" not in filters
    assert "city_grain_unreplayable" not in filters
    assert "cities=CHICAGO~IL" in route


def test_one_unpairable_city_suppresses_the_whole_filter() -> None:
    """Partial replay would answer a NARROWER question under the same heading."""

    _, filters = _route_from_answer_rows(
        question="Which cities have the most cash-out candidates?",
        rows=[
            {"city": "CHICAGO", "state": "IL", "n": 523010},
            {"city": "HOUSTON", "n": 311000},
        ],
        borrower_ids=[],
        sql_query=_CITY_SQL,
    )

    assert GENIE_CITY_FILTER_KEY not in filters
    assert filters["city_grain_unreplayable"] is True


def test_the_two_state_split_survives_the_whole_route() -> None:
    """Both sides of the ambiguity, from one answer, keyed correctly."""

    _, filters = _route_from_answer_rows(
        question="Where are the CYPRESS cash-out candidates?",
        rows=[
            {"city": "CYPRESS", "state": "CA", "n": 14630},
            {"city": "CYPRESS", "state": "TX", "n": 1},
        ],
        borrower_ids=[],
        sql_query=_CITY_SQL,
    )

    assert filters[GENIE_CITY_FILTER_KEY] == ["CYPRESS~CA", "CYPRESS~TX"]


def test_a_borrower_list_that_projects_city_emits_no_city_filter() -> None:
    """`borrower_ids` pins the cohort exactly; a city column broadens nothing."""

    _, filters = _route_from_answer_rows(
        question="Who are the top cash-out candidates?",
        rows=[{"borrower_id": "B-0000000000001", "city": "CHICAGO", "state": "IL"}],
        borrower_ids=["B-0000000000001"],
        sql_query=_CITY_SQL,
    )

    assert GENIE_CITY_FILTER_KEY not in filters
    assert "city_grain_unreplayable" not in filters


# --- the canonical pair shape ----------------------------------------------


@pytest.mark.parametrize(
    ("city", "state", "expected"),
    [
        ("CHICAGO", "IL", "CHICAGO~IL"),
        ("Fort Lauderdale", "fl", "FORT LAUDERDALE~FL"),
        # The one hyphenated value in gold, measured 2026-08-12.
        ("UNION HILL-NOVELTY HILL", "WA", "UNION HILL-NOVELTY HILL~WA"),
        ("  SPACED   OUT  ", "TX", "SPACED OUT~TX"),
        ("CHICAGO", "", ""),
        ("", "IL", ""),
        ("CHICAGO", "ILLINOIS", ""),
        # `~` is the separator; a city containing one would break the key.
        # Measured: no gold city contains it.
        ("CHIC~AGO", "IL", ""),
    ],
)
def test_format_city_state_pair_shape(city: str, state: str, expected: str) -> None:
    assert format_city_state_pair(city, state) == expected


def test_the_separator_survives_a_url_encode_literal() -> None:
    """`~` is RFC-3986 unreserved, which is why it was chosen."""

    from urllib.parse import urlencode

    assert urlencode({GENIE_CITY_FILTER_KEY: "CHICAGO~IL"}) == "cities=CHICAGO~IL"


def test_the_pair_regex_and_the_parser_agree() -> None:
    assert CITY_STATE_PAIR_RE.fullmatch("CHICAGO~IL") is not None
    assert parse_city_state_pair("CHICAGO~IL") == ("CHICAGO", "IL")
    assert parse_city_state_pair("CHICAGO") is None
    assert parse_city_state_pair("~IL") is None
    assert parse_city_state_pair(None) is None


# --- 5. one predicate, three callers, bound --------------------------------


class _CapturingClient:
    """Records the SQL and params each read path actually issued."""

    def __init__(self) -> None:
        self.calls: dict[str, tuple[str, dict[str, object]]] = {}

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        self.calls["list"] = (sql, dict(params or {}))
        return []

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object]:
        key = "identity" if "cohort_digest" in sql else "count"
        self.calls[key] = (sql, dict(params or {}))
        return {
            "n": 523010,
            "ranked_n": 100,
            "cohort_digest": "a" * 64,
            "snapshot_id": "snap-1",
        }


_EXPECTED_CITY_PREDICATE = (
    "AND ((UPPER(b.city) = :city_0 AND b.state = :cstate_0))"
)


def test_list_count_and_identity_emit_the_same_bound_city_predicate() -> None:
    """A threshold applied to the rows but not the total is the whole defect.

    `X-Total-Matching` comes from `count`, the rows from `list`, and the
    signed proof from `cohort_identity`. If any one of them omits the city
    predicate the header lies about the population on screen.
    """

    client = _CapturingClient()
    repo = DatabricksLeadRepository(client, cache_ttl_s=0)  # type: ignore[arg-type]
    kwargs: dict[str, object] = {
        "segment": None,
        "portfolio_id": None,
        "city_states": ["CHICAGO~IL"],
    }

    repo.list(**kwargs)  # type: ignore[arg-type]
    repo.count(**kwargs)  # type: ignore[arg-type]
    repo.cohort_identity(**kwargs)  # type: ignore[arg-type]

    assert set(client.calls) == {"list", "count", "identity"}
    for name, (sql, params) in client.calls.items():
        assert _EXPECTED_CITY_PREDICATE in sql, name
        # Bound, never interpolated: the value must not appear in the text.
        assert "CHICAGO" not in sql, name
        assert params["city_0"] == "CHICAGO", name
        assert params["cstate_0"] == "IL", name


def test_two_pairs_compile_to_an_or_of_ands_not_a_cross_product() -> None:
    """`city IN (...) AND state IN (...)` would also match CYPRESS~TX."""

    client = _CapturingClient()
    repo = DatabricksLeadRepository(client, cache_ttl_s=0)  # type: ignore[arg-type]
    repo.count(segment=None, portfolio_id=None, city_states=["CYPRESS~CA", "SUNNYVALE~TX"])

    sql, params = client.calls["count"]
    assert (
        "AND ((UPPER(b.city) = :city_0 AND b.state = :cstate_0) "
        "OR (UPPER(b.city) = :city_1 AND b.state = :cstate_1))"
    ) in sql
    assert params["city_0"] == "CYPRESS"
    assert params["cstate_0"] == "CA"
    assert params["city_1"] == "SUNNYVALE"
    assert params["cstate_1"] == "TX"


def test_a_malformed_pair_reaches_the_warehouse_as_no_predicate() -> None:
    """Last line of defence. A bare name must never compile to a city match."""

    client = _CapturingClient()
    repo = DatabricksLeadRepository(client, cache_ttl_s=0)  # type: ignore[arg-type]
    repo.count(segment=None, portfolio_id=None, city_states=["CHICAGO"], state_codes=["IL"])

    sql, _ = client.calls["count"]
    assert "UPPER(b.city)" not in sql


# --- 6. the router parses a multi-word city off the URL --------------------


class _EchoRepo:
    """Captures exactly what the router handed the repository."""

    seen: dict[str, object] = {}

    @classmethod
    def list(cls, **kwargs: object) -> list[LeadSummary]:
        cls.seen = dict(kwargs)
        return []

    @classmethod
    def count(cls, **kwargs: object) -> int:
        return 0


@pytest.fixture
def echo_repo():
    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: _EchoRepo
    try:
        yield _EchoRepo
    finally:
        # Snapshot/restore, never pop: conftest registers a session-wide
        # binding that a pop would delete for every later test.
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior


def test_a_multi_word_city_reaches_the_repository_parsed(echo_repo) -> None:
    """`parse_csv_filter`'s isalpha()/width check would 422 this.

    4 of the 428 gold city names are 4 words long; reusing that helper would
    have rejected most of the vocabulary.
    """

    response = TestClient(app).get("/api/leads?cities=FORT+LAUDERDALE~FL")

    assert response.status_code == 200, response.text
    assert echo_repo.seen["city_states"] == ["FORT LAUDERDALE~FL"]


def test_a_percent_encoded_separator_parses_identically(echo_repo) -> None:
    """A browser-built link sends `%7E`; both spellings are the same URI."""

    response = TestClient(app).get("/api/leads?cities=CHICAGO%7EIL")

    assert response.status_code == 200, response.text
    assert echo_repo.seen["city_states"] == ["CHICAGO~IL"]


def test_a_bare_city_name_on_the_url_is_refused_not_widened(echo_repo) -> None:
    response = TestClient(app).get("/api/leads?cities=CHICAGO")

    assert response.status_code == 422
    assert "CITY~ST" in response.json()["detail"]


def test_too_many_pairs_is_refused(echo_repo) -> None:
    pairs = ",".join(f"CITY{i}~IL" for i in range(MAX_CITY_FILTER_VALUES + 1))
    response = TestClient(app).get(f"/api/leads?cities={pairs}")

    assert response.status_code == 422


# --- 7. the handoff fingerprint ---------------------------------------------


def _fingerprint(city_states: list[str] | None) -> dict[str, object]:
    return normalise_lead_queue_handoff_filters(
        LeadCohortFilters(segment=None, state_codes=["IL"], city_states=city_states)
    )


def test_the_handoff_fingerprint_moves_when_only_cities_moves() -> None:
    """Outside the fingerprint, a city cohort signs the same proof as the state.

    `verify_growth_agent_handoff` HMACs these normalized filters, so a key it
    omits can be edited onto the URL after signing without invalidating it.
    """

    chicago = _fingerprint(["CHICAGO~IL"])
    midlothian = _fingerprint(["MIDLOTHIAN~IL"])
    none_at_all = _fingerprint(None)

    assert chicago[GENIE_CITY_FILTER_KEY] == ["CHICAGO~IL"]
    assert chicago != midlothian
    assert chicago != none_at_all
    assert GENIE_CITY_FILTER_KEY not in none_at_all


def test_the_fingerprint_is_order_stable() -> None:
    assert _fingerprint(["HOUSTON~TX", "CHICAGO~IL"]) == _fingerprint(
        ["CHICAGO~IL", "HOUSTON~TX"]
    )
