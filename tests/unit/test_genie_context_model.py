"""Genie context model and its server-rendered scope (audit 2026-09-21 genie-04, phase 2).

``POST /api/genie/context-scope`` (canonical ``/api/v1/genie/context-scope``)
renders a closed :class:`GenieContext` (codes only) into a reviewed Genie
question, or names the closed reason none is offered. These tests pin:

(i)   the closed model, through the route: anything outside it is a 422;
(ii)  the battery: every rendering the Lead Queue can ask for passes the exact
      deterministic matcher set the phase-1 template battery runs, the Sales Ops
      router and the request model, and names a governed place;
(iii) parity with the frontend registry and the phase-1 template JSON;
(iv)  the runtime battery withholds a refused rendering and its event carries
      neither the prompt nor any slot value;
(v)   the route reads no borrower data and writes nothing;
(vi)  the scope rides in the prompt of record: the submit route's
      ``genie.message_submitted`` row hashes the full scoped question;
(vii) an assignee filter is named as omitted and never rendered.

The governed place dimension is installed the way
``test_genie_context_templates.py`` installs it, plus a committed live-shaped
city sample, and resolved once so the startup warm's publication is in place.
Guard code is not touched; this test only reads it.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

import backend.api.genie as genie_api
import backend.services.genie_context_scope as scope_module
from backend.main import app
from backend.schemas.genie_context import (
    STATE_FIPS_TO_USPS,
    GenieContext,
    GenieContextSegmentCode,
    GenieContextStateCode,
)
from backend.schemas.marketing_selection_reviewed_places import (
    COUNTY_NAME_EXCLUSIONS,
    GOVERNED_SCOPE_ZIP_FRAGMENT,
    is_governed_analytics_location,
)
from backend.schemas.usps import USPS_STATE_CODES
from backend.services.audit_store import get_audit_store
from backend.services.county_names import county_name_for_fips
from backend.services.genie_context_scope import (
    COUNTY_FIPS_EXCLUSIONS,
    COVERAGE_SCOPE,
    GENIE_CONTEXT_PROMPT_MATCHERS,
    LENDER_REF_EXCLUSIONS,
    PLACE_TEMPLATE,
    SEGMENT_PHRASES,
    SEGMENT_TEMPLATE,
    SEGMENTS_WITHOUT_TEMPLATES,
    STATE_NAME_BY_CODE,
    genie_context_battery_hit,
    render_genie_context_scope,
    scope_place_phrase,
)
from backend.services.genie_place_dimension import (
    GovernedPlaceDimensionResolver,
    _reset_governed_place_dimension_for_tests,
    get_governed_place_dimension,
)
from backend.services.genie_progress import genie_question_hash
from backend.services.lakebase import get_lakebase_client
from backend.services.repositories.factory import get_genie_answer_repository
from tests.fixtures.genie_job_lakebase import FakeJobLakebase
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit import test_genie_context_templates as phase_one

ROOT = Path(__file__).resolve().parents[2]
COUNTIES_PATH = ROOT / "frontend" / "public" / "us-counties.json"
ROUTE = "/api/genie/context-scope"
V1_ROUTE = "/api/v1/genie/context-scope"
HEADERS = {"X-Forwarded-Email": "analyst@summit.example", "X-Forwarded-Groups": ""}

#: A committed, live-shaped sample of the governed gold city dimension (stored
#: uppercase, no state). Includes the values the place gate is known to refuse
#: (national-origin bank) and given-name-shaped values the prose heuristic reads.
CITY_SAMPLE: dict[str, str] = {
    "TACOMA": "WA",
    "HAWAIIAN GARDENS": "CA",
    "INDIAN HEAD PARK": "IL",
    "ELIZABETH": "NJ",
    "ALISO VIEJO": "CA",
    "MEDINA": "OH",
    "PARKER": "CO",
    "KENT": "WA",
    "CHICAGO": "IL",
    "ST. LOUIS": "MO",
}
#: Sample cities the governed place gate does not admit (the ``-oma`` condition
#: morphology; the national-origin bank), so the renderer withholds them
#: ('place_unavailable'). An honest list: the test fails if one becomes
#: admissible, and then it moves to the rendered set.
CITY_SAMPLE_WITHHELD: frozenset[str] = frozenset({"TACOMA", "HAWAIIAN GARDENS", "INDIAN HEAD PARK"})
LENDER_REFS: tuple[str, ...] = tuple(
    [f"Competitor {chr(code)}" for code in range(ord("A"), ord("Z") + 1)] + ["Competitor Other"]
)
ZIP_SAMPLE: dict[str, str] = {"00501": "NY", "60601": "IL", "99950": "AK"}
PAIR_STATES: frozenset[str] = frozenset({"OK", "NY", "DC"})
FIXED_SEGMENT = "itm"
LENDER_STATE = "IL"

_SEGMENT_ENTRY_RE = re.compile(r"code:\s*'([a-z_]+)',\s*name:\s*'([^']+)'")
_ZIP_PHRASE_RE = re.compile(GOVERNED_SCOPE_ZIP_FRAGMENT, re.IGNORECASE)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _resolved_place_dimension() -> Iterator[None]:
    _reset_governed_place_dimension_for_tests(
        GovernedPlaceDimensionResolver(dimension_reader=lambda: list(CITY_SAMPLE))
    )
    # The startup warm resolves and PUBLISHES the city set before any request.
    get_governed_place_dimension().known_place_values()
    yield
    _reset_governed_place_dimension_for_tests(None)


def _templated_segments() -> list[str]:
    return [code for code in get_args(GenieContextSegmentCode) if code not in SEGMENTS_WITHOUT_TEMPLATES]


def _counties() -> list[tuple[str, str, str]]:
    """``(fips, state, name)`` for every county geometry in the shipped artifact."""

    payload = json.loads(COUNTIES_PATH.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str]] = []
    for geometry in payload["objects"]["counties"]["geometries"]:
        fips = str(geometry["id"])
        rows.append((fips, STATE_FIPS_TO_USPS[fips[:2]], str(geometry["properties"]["name"])))
    return rows


def _excluded_county(name: str) -> bool:
    return " ".join(name.lower().split()) in COUNTY_NAME_EXCLUSIONS


def _withheld_county(fips: str, name: str) -> bool:
    return fips in COUNTY_FIPS_EXCLUSIONS or _excluded_county(name)


def _contexts_for_state(state: str) -> list[GenieContext]:
    """Every rendering the battery owns for one state (parametrized per state)."""

    segments = _templated_segments()
    contexts = [GenieContext(route="lead-queue", state=state)]
    contexts += [GenieContext(route="lead-queue", state=state, segment_codes=[code]) for code in segments]
    if state in PAIR_STATES:
        for pair in itertools.combinations(segments, 2):
            for mode in ("all", "any"):
                contexts.append(
                    GenieContext(route="lead-queue", state=state, segment_codes=list(pair), segment_mode=mode)
                )
    for fips, county_state, name in _counties():
        if county_state != state or _withheld_county(fips, name):
            continue
        contexts.append(GenieContext(route="lead-queue", state=state, county_fips=fips))
        contexts.append(
            GenieContext(route="lead-queue", state=state, county_fips=fips, segment_codes=[FIXED_SEGMENT])
        )
    if state == LENDER_STATE:
        for ref in LENDER_REFS:
            contexts.append(GenieContext(route="lead-queue", state=state, lender_ref=ref))
            contexts.append(
                GenieContext(route="lead-queue", state=state, lender_ref=ref, segment_codes=[FIXED_SEGMENT])
            )
    for zip_code, zip_state in ZIP_SAMPLE.items():
        if zip_state == state:
            contexts.append(GenieContext(route="lead-queue", zip=zip_code))
            contexts.append(GenieContext(route="lead-queue", zip=zip_code, segment_codes=[FIXED_SEGMENT]))
        # A state beside a ZIP (the map's ZIP drill, or a mismatched pair): both are named.
        contexts.append(GenieContext(route="lead-queue", state=state, zip=zip_code))
    contexts.append(GenieContext(route="lead-queue", state=state, zip="60601", segment_codes=[FIXED_SEGMENT]))
    for city, city_state in CITY_SAMPLE.items():
        if city_state == state and city not in CITY_SAMPLE_WITHHELD:
            key = f"{city}~{city_state}"
            contexts.append(GenieContext(route="lead-queue", city=key))
            contexts.append(GenieContext(route="lead-queue", city=key, segment_codes=[FIXED_SEGMENT]))
    return contexts


def _assert_governed_place(ctx: GenieContext, prompt: str) -> None:
    place = scope_place_phrase(ctx)
    if place is None:
        assert COVERAGE_SCOPE in prompt
        return
    assert place in prompt
    if ctx.zip is not None:
        zip_phrase = _ZIP_PHRASE_RE.pattern + (f", {ctx.state}" if ctx.state is not None else "")
        assert re.fullmatch(zip_phrase, place, re.IGNORECASE), place
    else:
        assert is_governed_analytics_location(place), place


# --- (i) the closed model, through the route --------------------------------------


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("an extra key", {"route": "lead-queue", "state": "IL", "borrower_ids": ["x"]}),
        ("a free-text question", {"route": "lead-queue", "state": "IL", "question": "anything"}),
        ("a state name for a code", {"route": "lead-queue", "state": "Illinois"}),
        ("a territory code", {"route": "lead-queue", "state": "PR"}),
        ("a free-text segment", {"route": "lead-queue", "segment_codes": ["in the money"]}),
        ("three segments", {"route": "lead-queue", "segment_codes": ["itm", "listed", "equity"]}),
        ("a repeated segment", {"route": "lead-queue", "segment_codes": ["itm", "itm"]}),
        ("an unknown mode", {"route": "lead-queue", "segment_codes": ["itm"], "segment_mode": "none"}),
        ("a county name for a FIPS code", {"route": "lead-queue", "county_fips": "Cook"}),
        ("a four-digit FIPS", {"route": "lead-queue", "county_fips": "1703"}),
        ("a territory county", {"route": "lead-queue", "county_fips": "72001"}),
        ("a state/FIPS mismatch", {"route": "lead-queue", "state": "TX", "county_fips": "17031"}),
        ("a county and a ZIP", {"route": "lead-queue", "county_fips": "17031", "zip": "60601"}),
        ("a ZIP and a city", {"route": "lead-queue", "zip": "60601", "city": "CHICAGO~IL"}),
        ("a ZIP+4", {"route": "lead-queue", "zip": "60601-1234"}),
        ("free text in the city slot", {"route": "lead-queue", "city": "call 555 0100~IL"}),
        ("a five-token city", {"route": "lead-queue", "city": "A B C D E~IL"}),
        ("a city outside the USPS list", {"route": "lead-queue", "city": "SAN JUAN~PR"}),
        ("a city/state mismatch", {"route": "lead-queue", "state": "TX", "city": "CHICAGO~IL"}),
        ("a lender outside the pattern", {"route": "lead-queue", "lender_ref": "Wells Fargo"}),
        ("a lower-case lender letter", {"route": "lead-queue", "lender_ref": "Competitor a"}),
        ("an assignee email", {"route": "lead-queue", "state": "IL", "assignee": "lo@summit.example"}),
        ("an assignee alone", {"route": "lead-queue", "assignee": "me"}),
        ("no slot at all", {"route": "lead-queue"}),
        ("another route", {"route": "home", "state": "IL"}),
    ],
)
def test_the_closed_model_refuses_anything_outside_it(label: str, body: dict[str, object]) -> None:
    response = client.post(V1_ROUTE, json=body, headers=HEADERS)

    assert response.status_code == 422, label
    # The app-wide handler strips the echoed input: a refused value never comes back.
    for value in body.values():
        if isinstance(value, str) and len(value) > 4 and value != "lead-queue":
            assert value not in response.text, label


def test_the_route_needs_a_json_body_and_an_actor() -> None:
    not_json = client.post(V1_ROUTE, content="state=IL", headers={**HEADERS, "Content-Type": "text/plain"})
    anonymous = client.post(
        V1_ROUTE,
        json={"route": "lead-queue", "state": "IL"},
        headers={"X-Forwarded-Email": "", "X-Forwarded-User": "", "X-Forwarded-Groups": ""},
    )

    assert not_json.status_code == 415
    assert anonymous.status_code == 401


def test_the_deprecated_alias_renders_the_same_prompt() -> None:
    body = {"route": "lead-queue", "state": "OK", "segment_codes": ["itm", "permit"], "segment_mode": "all"}

    canonical = client.post(V1_ROUTE, json=body, headers=HEADERS)
    alias = client.post(ROUTE, json=body, headers=HEADERS)

    assert canonical.status_code == alias.status_code == 200
    assert canonical.json() == alias.json()
    assert canonical.json() == {
        "prompt": (
            "How many borrowers in Oklahoma are in the prime refi candidates segment and the "
            "heloc intent segment, and what is the average rate spread?"
        ),
        "withheld": None,
        "omitted": [],
    }


# --- (ii) the battery -------------------------------------------------------------


def test_the_battery_bites_on_a_protected_class_prompt() -> None:
    """Non-vacuity: the same matcher set refuses a fair-lending prompt."""

    assert genie_context_battery_hit("Which states have the most elderly borrowers?") == "protected"


_BATTERY_SECONDS: dict[str, float] = {}


@pytest.mark.parametrize("state", sorted(USPS_STATE_CODES))
def test_every_rendering_for_a_state_passes_the_real_prompt_guards(state: str) -> None:
    started = time.perf_counter()
    contexts = _contexts_for_state(state)
    failures: dict[str, str] = {}
    for ctx in contexts:
        response = render_genie_context_scope(ctx)
        key = ctx.model_dump_json(exclude_defaults=True)
        if response.prompt is None:
            failures[key] = f"withheld:{response.withheld}"
            continue
        if hit := genie_context_battery_hit(response.prompt):
            failures[key] = f"hit:{hit}"
            continue
        assert response.withheld is None and response.omitted == []
        _assert_governed_place(ctx, response.prompt)
    _BATTERY_SECONDS[state] = time.perf_counter() - started
    # Non-vacuity: every state renders itself and every templated segment.
    assert len(contexts) >= 1 + len(_templated_segments())
    assert failures == {}


def test_the_county_grain_covers_every_admitted_shipped_county() -> None:
    counties = _counties()
    admitted = [row for row in counties if not _excluded_county(row[2])]
    assert len(counties) > 3_000
    # The 13 governed-term collisions withhold a handful of geometries, no more.
    assert 0 < len(counties) - len(admitted) < 60


@pytest.mark.parametrize("fips", sorted({fips for fips, _s, name in _counties() if _withheld_county(fips, name)}))
def test_an_excluded_county_is_withheld_never_rendered(fips: str) -> None:
    response = render_genie_context_scope(GenieContext(route="lead-queue", county_fips=fips))

    assert (response.prompt, response.withheld) == (None, "excluded_value")


def test_the_withheld_sample_cities_stay_outside_the_governed_place_set() -> None:
    """The honest list: each withheld city is one the place gate refuses today."""

    for city in CITY_SAMPLE:
        key = f"{city}~{CITY_SAMPLE[city]}"
        response = render_genie_context_scope(GenieContext(route="lead-queue", city=key))
        phrase = f"{city.title()}, {CITY_SAMPLE[city]}"
        if city in CITY_SAMPLE_WITHHELD:
            assert (response.prompt, response.withheld) == (None, "place_unavailable"), city
            assert not is_governed_analytics_location(phrase), f"{city} is admitted now; render it"
        else:
            assert response.prompt is not None, city


@pytest.mark.parametrize("state", ["IL", "TX"])
def test_a_state_beside_a_zip_stays_in_the_question(state: str) -> None:
    """The map's ZIP drill sends both; a mismatched pair must never ask about the ZIP alone."""

    assert render_genie_context_scope(GenieContext(route="lead-queue", state=state, zip="60601")).prompt == (
        f"How many borrowers are in ZIP code 60601, {state}, and what is the average rate spread?"
    )
    paired = render_genie_context_scope(
        GenieContext(route="lead-queue", state=state, zip="60601", segment_codes=["itm", "equity"], segment_mode="all")
    )
    assert paired.prompt == (
        f"How many borrowers in ZIP code 60601, {state} are in the prime refi candidates segment and the "
        "home equity candidate segment, and what is the average rate spread?"
    )
    # Non-vacuity: the ZIP alone still renders without a state.
    assert render_genie_context_scope(GenieContext(route="lead-queue", zip="60601")).prompt == (
        "How many borrowers are in ZIP code 60601, and what is the average rate spread?"
    )


def test_a_cold_city_dimension_withholds_the_city_slot() -> None:
    _reset_governed_place_dimension_for_tests(GovernedPlaceDimensionResolver(dimension_reader=lambda: []))
    get_governed_place_dimension().known_place_values()

    response = render_genie_context_scope(GenieContext(route="lead-queue", city="KENT~WA"))

    assert (response.prompt, response.withheld) == (None, "place_unavailable")


def test_each_excluded_value_is_still_refused() -> None:
    """The exclusion maps stay honest: drop an entry once the guards accept it."""

    for code in SEGMENTS_WITHOUT_TEMPLATES:
        response = render_genie_context_scope(GenieContext(route="lead-queue", state="IL", segment_codes=[code]))
        assert (response.prompt, response.withheld) == (None, "excluded_value")
        phrase = phase_one._segment_registry()[code].lower()
        prompt = SEGMENT_TEMPLATE.format(scope="in Illinois", segments=f"the {phrase} segment")
        assert genie_context_battery_hit(prompt) is not None, f"{code} is answerable now"
    for fips, reason in COUNTY_FIPS_EXCLUSIONS.items():
        assert reason
        response = render_genie_context_scope(GenieContext(route="lead-queue", county_fips=fips))
        assert response.withheld == "excluded_value"
        name = county_name_for_fips(fips)
        assert name is not None
        place = f"{name} County, {STATE_FIPS_TO_USPS[fips[:2]]}"
        assert genie_context_battery_hit(PLACE_TEMPLATE.format(place=place)) is not None, (
            f"{fips} is answerable now; remove it from COUNTY_FIPS_EXCLUSIONS"
        )
    for ref, reason in LENDER_REF_EXCLUSIONS.items():
        assert reason
        response = render_genie_context_scope(GenieContext(route="lead-queue", state="IL", lender_ref=ref))
        assert response.withheld == "excluded_value"


def test_zz_record_the_battery_wall_time() -> None:
    """Reported in the lane PR; never a skip, never a threshold."""

    total = sum(_BATTERY_SECONDS.values())
    print(f"genie context battery: {len(_BATTERY_SECONDS)} states in this worker, {total:.1f}s")


# --- (iii) parity -----------------------------------------------------------------


def test_the_matcher_set_is_the_phase_one_battery() -> None:
    assert GENIE_CONTEXT_PROMPT_MATCHERS == phase_one._PROMPT_MATCHERS


def test_segment_phrases_match_the_frontend_registry() -> None:
    registry = phase_one._segment_registry()
    excluded = set(phase_one._string_map("segmentsWithoutTemplates"))
    assert set(get_args(GenieContextSegmentCode)) == set(registry)
    assert excluded == SEGMENTS_WITHOUT_TEMPLATES
    assert dict(SEGMENT_PHRASES) == phase_one._segment_phrases()


def test_state_vocabularies_match_the_phase_one_templates_and_usps() -> None:
    assert dict(STATE_NAME_BY_CODE) == phase_one._string_map("stateNames")
    assert set(get_args(GenieContextStateCode)) == set(USPS_STATE_CODES)
    assert set(STATE_FIPS_TO_USPS.values()) == set(USPS_STATE_CODES)
    assert len(STATE_FIPS_TO_USPS) == 51


def test_the_fips_table_covers_every_shipped_county_prefix() -> None:
    payload = json.loads(COUNTIES_PATH.read_text(encoding="utf-8"))
    prefixes = {str(geometry["id"])[:2] for geometry in payload["objects"]["counties"]["geometries"]}
    assert prefixes <= set(STATE_FIPS_TO_USPS)


# --- (iv) the runtime battery withholds, and its event names no value -------------


def test_a_matcher_hit_withholds_the_prompt_and_logs_no_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def always(_prompt: str) -> object:
        return "matched"

    patched = (("protected", always), *GENIE_CONTEXT_PROMPT_MATCHERS[1:])
    monkeypatch.setattr(scope_module, "GENIE_CONTEXT_PROMPT_MATCHERS", patched)
    ctx = GenieContext(route="lead-queue", state="OK", county_fips="40143", segment_codes=["itm"])
    caplog.set_level(logging.INFO, logger=scope_module.__name__)

    response = client.post(V1_ROUTE, json=ctx.model_dump(mode="json"), headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"prompt": None, "withheld": "refused", "omitted": []}
    events = [r for r in caplog.records if getattr(r, "mip_event", None) == "genie_context_scope_withheld"]
    assert len(events) == 1
    extras = getattr(events[0], "mip_extras", {})
    assert extras == {"reason": "refused", "slots": ["segment_codes", "state", "county_fips"], "matcher": "protected"}
    logged = json.dumps(extras) + events[0].getMessage()
    for value in ("Oklahoma", "Tulsa", "40143", "itm", "prime refi", "borrowers"):
        assert value not in logged


# --- (v) render-only: no borrower read, no write -----------------------------------


class _ExplodingLakebase:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the context-scope route touched Lakebase ({name})")


def test_the_route_writes_no_audit_row_and_reads_no_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.services.databricks_sql as databricks_sql

    store = InMemoryAuditStore()

    def no_warehouse() -> Any:
        raise AssertionError("the context-scope route touched the warehouse")

    monkeypatch.setattr(databricks_sql, "get_sql_client", no_warehouse)
    monkeypatch.setitem(app.dependency_overrides, get_audit_store, lambda: store)
    monkeypatch.setitem(app.dependency_overrides, get_lakebase_client, _ExplodingLakebase)

    for body in (
        {"route": "lead-queue", "state": "IL", "segment_codes": ["itm"]},
        {"route": "lead-queue", "county_fips": "17031", "segment_codes": ["itm", "equity"], "segment_mode": "all"},
        {"route": "lead-queue", "city": "KENT~WA", "assignee": "me"},
        {"route": "lead-queue", "zip": "60601", "lender_ref": "Competitor B"},
    ):
        response = client.post(V1_ROUTE, json=body, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["prompt"]

    assert store.list(limit=10) == []


# --- (vi) the scope rides in the prompt of record ----------------------------------


class _SubmitGenieClient:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, str | None]] = []

    def submit_message(self, question: str, conversation_id: str | None = None) -> tuple[str, str]:
        self.submitted.append((question, conversation_id))
        return "conv-scope-1", "msg-scope-1"


class _AuditCapture:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def write(self, **kwargs: Any) -> None:
        self.writes.append(kwargs)


class _UnusedRepo:
    def respond(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("submit must not answer synchronously on the live path")


def test_a_rendered_prompt_is_the_hashed_prompt_of_record(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = GenieContext(route="lead-queue", zip="60601", segment_codes=["itm", "equity"], segment_mode="all")
    rendered = client.post(V1_ROUTE, json=ctx.model_dump(mode="json"), headers=HEADERS).json()
    prompt = rendered["prompt"]
    assert prompt and "ZIP code 60601" in prompt and "home equity candidate" in prompt

    genie, audit = _SubmitGenieClient(), _AuditCapture()
    monkeypatch.setattr(genie_api, "get_genie_client", lambda: genie)
    monkeypatch.setitem(app.dependency_overrides, get_genie_answer_repository, _UnusedRepo)
    monkeypatch.setitem(app.dependency_overrides, get_audit_store, lambda: audit)
    monkeypatch.setitem(app.dependency_overrides, get_lakebase_client, FakeJobLakebase)

    response = client.post("/api/v1/genie/message/submit", json={"question": prompt}, headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["question_hash"] == genie_question_hash(prompt)
    assert genie.submitted == [(prompt, None)]
    submitted = [w for w in audit.writes if w["action"] == "genie.message_submitted"]
    assert len(submitted) == 1
    payload = submitted[0]["payload_json"]
    assert payload["question_hash"] == genie_question_hash(prompt)
    assert submitted[0]["event_type"] == "RUN_GENIE"
    # The scope is in the hash, never a field or a text of its own.
    assert not {"context", "scope", "zip", "segment_codes"} & set(payload)
    assert "60601" not in json.dumps(payload)


# --- (vii) the assignee is named, never rendered -----------------------------------


def test_an_assignee_filter_is_omitted_and_never_rendered() -> None:
    response = client.post(
        V1_ROUTE,
        json={"route": "lead-queue", "state": "TX", "segment_codes": ["itm"], "assignee": "me"},
        headers=HEADERS,
    )

    body = response.json()
    assert response.status_code == 200
    assert body["omitted"] == ["assignee"]
    assert body["withheld"] is None
    assert body["prompt"] == (
        "How many borrowers in Texas are in the prime refi candidates segment, and what is the "
        "average rate spread?"
    )
    assert not re.search(r"\b(?:assign\w*|me|my|mine|owner|loan officer)\b", body["prompt"], re.IGNORECASE)
