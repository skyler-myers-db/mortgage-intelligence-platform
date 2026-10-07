"""GET /leads server paging under the VIEW_LEADS ruling (D-audit-reads-a, tables-02).

Driven through the REAL route and the REAL Databricks repository over an
in-memory SQLite copy of gold (tests/fixtures/sqlite_lead_warehouse), so the
cursor the route mints is resumed by the keyset SQL the App sends. Pinned:

* exactly ONE VIEW_LEADS row per served 2xx, grouped by a 32-hex view_id and
  ordered by page_index; the union of two pages' rendered ids is the first
  1,000 rows of one ORDER BY; an empty page and the assignment early return
  write one row each (the approval-request one is pinned in
  test_leads_approval_request_scope.py);
* page 1+ never runs the count, the identity proof or the handoff checks;
* the preamble runs on every page (an actor who lost admin gets 403);
* every 422 logs its reason only, never the cursor; a refreshed row is 409;
  409, 422 and 503 write nothing;
* no cursor at an exact multiple of 500, none past page index 9, none for a
  non-page-size limit; a deployment with no secret serves page 0 unpaged and
  answers a cursor 503.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.config.settings import settings
from backend.main import app
from backend.schemas.lead import LeadSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.services import lead_query_resolution, lead_view_cursor
from backend.services.audit_metadata_value_policy import validate_lead_view_values
from backend.services.audit_store import get_audit_store
from backend.services.growth_agent_runtime import cohort_fingerprint
from backend.services.lead_cohort_replay import CohortReplay
from backend.services.lead_view_cursor import encode_lead_cursor
from backend.services.observability import StructuredFormatter
from backend.services.pii_redaction import redact_lead_row
from backend.services.repositories import get_lead_repository
from backend.services.repositories.databricks_lead_cohorts import (
    LeadCohortFilters,
    issue_growth_agent_handoff,
    normalise_lead_queue_handoff_filters,
)
from backend.services.repositories.databricks_lead_order import LEAD_SORTS, LeadPage
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from backend.services.resilience import TTLCache
from backend.services.sales_state import get_sales_state_store
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.fixtures.reviewed_approval import reviewed_approval
from tests.fixtures.sqlite_lead_warehouse import SqliteLeadWarehouse, gold_lead_row, lead_id

ACTOR = "lo.one@summit-mortgage.example"
HEADERS = {"X-Forwarded-Email": ACTOR, "X-Forwarded-Groups": ""}
ADMIN_HEADERS = {"X-Forwarded-Email": ACTOR, "X-Forwarded-Groups": "mip-admin"}
KEY = SecretStr("lead-paging-key-0123456789abcdef")
client = TestClient(app)


def _override(dep: Any, value: object) -> Any:
    prior = app.dependency_overrides.get(dep)
    app.dependency_overrides[dep] = lambda: value
    return prior


def _restore(dep: Any, prior: Any) -> None:
    if prior is None:
        app.dependency_overrides.pop(dep, None)
    else:
        app.dependency_overrides[dep] = prior


@pytest.fixture(autouse=True)
def _keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", KEY)
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", None)


@pytest.fixture
def audit() -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    prior = _override(get_audit_store, store)
    yield store
    _restore(get_audit_store, prior)


def _wire(rows: int) -> tuple[SqliteLeadWarehouse, DatabricksLeadRepository, Any]:
    warehouse = SqliteLeadWarehouse([gold_lead_row(index) for index in range(rows)])
    repo = DatabricksLeadRepository(warehouse, cache_ttl_s=0.0)  # type: ignore[arg-type]
    return warehouse, repo, _override(get_lead_repository, repo)


@pytest.fixture
def gold() -> Iterator[tuple[SqliteLeadWarehouse, DatabricksLeadRepository]]:
    warehouse, repo, prior = _wire(1_050)
    yield warehouse, repo
    _restore(get_lead_repository, prior)


def _rows(audit: InMemoryAuditStore) -> list[dict[str, Any]]:
    events = [event for event in audit.list(limit=100) if event.event_type == "VIEW_LEADS"]
    return [dict(event.payload_json or {}) for event in sorted(events, key=lambda event: event.audit_sequence or 0)]


def _ids(response: Any) -> list[str]:
    return [lead["borrower_id"] for lead in response.json()]


def _payload_of(cursor: str) -> dict[str, Any]:
    encoded = cursor.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


@pytest.mark.parametrize(("sort", "sort_dir"), [("rank", "desc"), ("equity", "desc"), ("rate", "asc")])
def test_two_pages_are_two_rows_of_one_view_and_the_first_thousand_of_one_order(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository],
    audit: InMemoryAuditStore,
    sort: str,
    sort_dir: str,
) -> None:
    _, repo = gold
    params = {"sort": sort, "sort_dir": sort_dir}

    first = client.get("/api/leads", params=params, headers=HEADERS)
    assert first.status_code == 200, first.text
    cursor = first.headers["X-Next-Cursor"]
    second = client.get("/api/leads", params={**params, "cursor": cursor}, headers=HEADERS)
    assert second.status_code == 200, second.text

    view_id = first.headers["X-Lead-View-Id"]
    assert re.fullmatch(r"[0-9a-f]{32}", view_id)
    assert (first.headers["X-Page-Index"], second.headers["X-Page-Index"]) == ("0", "1")
    assert second.headers["X-Lead-View-Id"] == view_id
    assert second.headers["X-Total-Matching"] == first.headers["X-Total-Matching"]
    page0, page1 = _rows(audit)
    assert (page0["view_id"], page1["view_id"]) == (view_id, view_id)
    assert (page0["page_index"], page1["page_index"]) == (0, 1)
    assert page0["rendered_borrower_ids"] == _ids(first)
    assert page1["rendered_borrower_ids"] == _ids(second)
    assert not set(page0["rendered_borrower_ids"]) & set(page1["rendered_borrower_ids"])
    whole = repo.list_page(
        None,
        None,
        limit=1_000,
        sort=sort,
        sort_dir=sort_dir,
        portfolio_criteria=PortfolioCriteria(marketing_eligibility="Eligible only"),
    )
    assert page0["rendered_borrower_ids"] + page1["rendered_borrower_ids"] == [
        lead.borrower_id for lead in whole.leads
    ]
    assert page0["sort"] == sort
    assert ("sort_dir" in page0) is (sort != "rank")
    for row in (page0, page1):
        assert row["total_matching"] == 1_050
        assert re.fullmatch(r"[0-9a-f]{64}", row["filter_fingerprint"])
        assert row["source_refreshed_at"] == "2026-09-29T06:00:00Z"
        assert validate_lead_view_values(row) == []


def test_page_one_runs_no_count_and_no_identity(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository], audit: InMemoryAuditStore
) -> None:
    warehouse, _ = gold
    first = client.get("/api/leads", headers=HEADERS)
    statements_before = len(warehouse.statements)

    second = client.get("/api/leads", params={"cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)

    assert second.status_code == 200, second.text
    page_one = warehouse.statements[statements_before:]
    assert len(page_one) == 1 and "LIMIT 501" in page_one[0] and "lead_after_id" in page_one[0]
    assert len(_rows(audit)) == 2


def test_an_exact_multiple_of_the_page_size_mints_no_cursor(audit: InMemoryAuditStore) -> None:
    _, _, prior = _wire(1_000)
    try:
        first = client.get("/api/leads", headers=HEADERS)
        second = client.get("/api/leads", params={"cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)
    finally:
        _restore(get_lead_repository, prior)

    assert len(second.json()) == 500
    assert "X-Next-Cursor" not in second.headers
    assert "X-Lead-Paging" not in second.headers


def test_page_index_nine_mints_no_tenth_cursor(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository], audit: InMemoryAuditStore
) -> None:
    first = client.get("/api/leads", headers=HEADERS)
    payload = _payload_of(first.headers["X-Next-Cursor"])
    ninth = encode_lead_cursor(
        actor=ACTOR,
        view_id=payload["view"],
        page=9,
        fp=payload["fp"],
        refreshed=payload["refreshed"],
        total=payload["total"],
        handoff=None,
        handoff_expires_at=None,
        after=tuple(payload["after"]),
    )

    response = client.get("/api/leads", params={"cursor": ninth}, headers=HEADERS)

    assert response.status_code == 200, response.text
    assert response.headers["X-Page-Index"] == "9"
    assert "X-Next-Cursor" not in response.headers


def test_a_non_page_size_limit_keeps_todays_semantics(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository], audit: InMemoryAuditStore
) -> None:
    response = client.get("/api/leads", params={"limit": 25}, headers=HEADERS)

    assert response.status_code == 200
    assert len(response.json()) == 25
    assert "X-Next-Cursor" not in response.headers and "X-Lead-Paging" not in response.headers
    assert response.headers["X-Truncated-At"] == "25"
    (row,) = _rows(audit)
    assert row["page_index"] == 0 and row["limit"] == 25


def _reject_cases(first_cursor: str) -> dict[str, tuple[dict[str, str], dict[str, str]]]:
    encoded, signature = first_cursor.split(".")
    raw = bytearray(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    raw[raw.index(b'"page":1')+7] = ord("2")
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=") + "." + signature
    other = {"X-Forwarded-Email": "lo.two@summit-mortgage.example", "X-Forwarded-Groups": ""}
    return {
        "signature": ({"cursor": tampered}, HEADERS),
        "signature-actor": ({"cursor": first_cursor}, other),
        "filters": ({"cursor": first_cursor, "state": "TX"}, HEADERS),
        "filters-sort": ({"cursor": first_cursor, "sort": "equity"}, HEADERS),
        "malformed": ({"cursor": "not-a-cursor"}, HEADERS),
    }


@pytest.mark.parametrize("case", ["signature", "signature-actor", "filters", "filters-sort", "malformed"])
def test_each_refused_cursor_is_422_logs_its_reason_only_and_writes_nothing(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository],
    audit: InMemoryAuditStore,
    caplog: pytest.LogCaptureFixture,
    case: str,
) -> None:
    first = client.get("/api/leads", headers=HEADERS)
    cursor = first.headers["X-Next-Cursor"]
    params, headers = _reject_cases(cursor)[case]
    caplog.set_level(logging.INFO)

    response = client.get("/api/leads", params=params, headers=headers)

    assert response.status_code == 422
    assert response.json()["detail"] == "lead_view_cursor_invalid"
    assert len(_rows(audit)) == 1  # page 0 only
    rejected = [record for record in caplog.records if getattr(record, "mip_event", None) == "lead_cursor_rejected"]
    assert len(rejected) == 1
    assert rejected[0].mip_extras == {"reason": case.split("-")[0]}  # type: ignore[attr-defined]
    # The App's own lines (the test client's httpx logger is not the App's).
    for record in caplog.records:
        if record.name.startswith(("backend", "mip")):
            logged = repr(record.__dict__)
            assert cursor not in logged and params["cursor"] not in logged


def test_an_access_line_never_carries_a_cursor() -> None:
    cursor = "eyJ2IjoxfQ.c2lnbmF0dXJl"
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", f"/api/leads?sort=equity&cursor={cursor}", "1.1", 200),
        None,
    )

    line = StructuredFormatter().format(record)

    assert cursor not in line
    assert "cursor=<redacted>" in line and "sort=equity" in line


def test_an_expired_cursor_and_page_ten_are_refused(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository],
    audit: InMemoryAuditStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = client.get("/api/leads", headers=HEADERS)
    payload = _payload_of(first.headers["X-Next-Cursor"])
    tenth = encode_lead_cursor(
        actor=ACTOR,
        view_id=payload["view"],
        page=10,
        fp=payload["fp"],
        refreshed=payload["refreshed"],
        total=payload["total"],
        handoff=None,
        handoff_expires_at=None,
        after=tuple(payload["after"]),
    )
    assert client.get("/api/leads", params={"cursor": tenth}, headers=HEADERS).status_code == 422
    monkeypatch.setattr(lead_view_cursor.time, "time", lambda: payload["exp"] + 1)
    expired = client.get("/api/leads", params={"cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)

    assert expired.status_code == 422
    assert len(_rows(audit)) == 1


def test_a_cursor_signed_with_the_previous_secret_is_served(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository],
    audit: InMemoryAuditStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = client.get("/api/leads", headers=HEADERS)
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", SecretStr("rotated-key-0123456789abcdef0123"))
    monkeypatch.setattr(settings, "mip_genie_action_secret_previous", KEY)

    second = client.get("/api/leads", params={"cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)

    assert second.status_code == 200, second.text
    assert second.headers["X-Lead-View-Id"] == first.headers["X-Lead-View-Id"]


def test_a_row_of_a_later_refresh_is_409_and_writes_nothing(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository], audit: InMemoryAuditStore
) -> None:
    warehouse, _ = gold
    first = client.get("/api/leads", headers=HEADERS)
    warehouse.set_refreshed_at("2026-09-30T06:00:00Z")

    second = client.get("/api/leads", params={"cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)

    assert second.status_code == 409
    assert second.json()["detail"] == "The queue refreshed since this view loaded"
    assert len(_rows(audit)) == 1


NEW_REFRESH = "2026-09-30T06:00:00Z"


class _Clock:
    """A TTLCache clock the test moves, so one page's entry can expire before another's."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _cached_gold(clock: _Clock) -> tuple[SqliteLeadWarehouse, Any]:
    # The production page cache (MIP_CACHE_TTL_S defaults to 300 s), not the
    # cache-off repository every other paging test reads through.
    warehouse = SqliteLeadWarehouse([gold_lead_row(index) for index in range(1_050)])
    repo = DatabricksLeadRepository(warehouse, cache=TTLCache(now=clock), cache_ttl_s=300.0)  # type: ignore[arg-type]
    return warehouse, _override(get_lead_repository, repo)


def test_the_restart_after_a_refresh_409_reads_the_new_refresh_under_the_page_cache(
    audit: InMemoryAuditStore,
) -> None:
    """Page 0 cached before a gold refresh never answers the restart (the 409 loop)."""

    clock = _Clock()
    warehouse, prior = _cached_gold(clock)
    try:
        first = client.get("/api/leads", headers=HEADERS)
        warehouse.set_refreshed_at(NEW_REFRESH)
        clock.now = 10.0

        refused = client.get("/api/leads", params={"cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)
        restart = client.get("/api/leads", headers=HEADERS)
        resumed = client.get("/api/leads", params={"cursor": restart.headers["X-Next-Cursor"]}, headers=HEADERS)
    finally:
        _restore(get_lead_repository, prior)

    assert first.headers["X-Data-Refreshed-At"] == "2026-09-29T06:00:00Z"
    assert refused.status_code == 409
    assert restart.status_code == 200
    assert restart.headers["X-Data-Refreshed-At"] == NEW_REFRESH, "the restart was served the cached old page 0"
    assert resumed.status_code == 200, resumed.text
    assert resumed.headers["X-Data-Refreshed-At"] == NEW_REFRESH
    assert [row["page_index"] for row in _rows(audit)] == [0, 0, 1]


def test_a_cached_later_page_of_the_old_refresh_is_not_served_behind_a_fresh_page_zero(
    audit: InMemoryAuditStore,
) -> None:
    """The mirror case: page 0 expired and was read fresh, page 1 is still cached from before."""

    clock = _Clock()
    warehouse, prior = _cached_gold(clock)
    try:
        first = client.get("/api/leads", headers=HEADERS)
        clock.now = 200.0
        old_second = client.get("/api/leads", params={"cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)
        warehouse.set_refreshed_at(NEW_REFRESH)
        clock.now = 350.0  # page 0's entry expired at 300 s, page 1's lives to 500 s

        fresh = client.get("/api/leads", headers=HEADERS)
        second = client.get("/api/leads", params={"cursor": fresh.headers["X-Next-Cursor"]}, headers=HEADERS)
    finally:
        _restore(get_lead_repository, prior)

    assert old_second.status_code == 200
    assert fresh.headers["X-Data-Refreshed-At"] == NEW_REFRESH
    assert second.status_code == 200, second.text
    assert second.headers["X-Data-Refreshed-At"] == NEW_REFRESH
    assert _ids(second) == _ids(old_second), "the same rows, read again from the new refresh"


def test_an_identity_page_zero_of_a_newer_refresh_retires_the_cached_later_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handoff / identity view reads page 0 uncached; its page 1 must not come from the old refresh."""

    clock = _Clock()
    warehouse = SqliteLeadWarehouse([gold_lead_row(index) for index in range(1_050)])
    repo = DatabricksLeadRepository(warehouse, cache=TTLCache(now=clock), cache_ttl_s=300.0)  # type: ignore[arg-type]
    first = repo.list_page(None, None, limit=500)
    old_second = repo.list_page(None, None, limit=500, after=first.last_keyset)
    warehouse.set_refreshed_at(NEW_REFRESH)

    def identity_rows(*_args: object, **_kwargs: object) -> tuple[list[dict[str, Any]], dict[str, str | int]]:
        # The identity statement is Databricks SQL; its ranked raw rows are these.
        raw = warehouse.execute(
            "SELECT -rank_overall AS __rank_order, * FROM lead_population "
            "ORDER BY __rank_order DESC, borrower_id ASC LIMIT 500"
        )
        return raw, {"total": 1_050, "cohort_digest": "b" * 64, "snapshot_id": "snapshot-1"}

    monkeypatch.setattr(repo._cohort_queries, "list_with_identity_rows", identity_rows)
    page_zero, _ = repo.list_with_identity_page(None, None, limit=500)
    second = repo.list_page(None, None, limit=500, after=page_zero.last_keyset)

    assert page_zero.last_keyset == first.last_keyset
    assert [lead.borrower_id for lead in second.leads] == [lead.borrower_id for lead in old_second.leads]
    assert {lead.row_refreshed_at.isoformat() for lead in second.leads if lead.row_refreshed_at} == {
        "2026-09-30T06:00:00+00:00"
    }, "page 1 came from the cached old refresh behind a fresh identity page 0"


def test_a_page_with_no_refresh_stamp_stays_cached() -> None:
    clock = _Clock()
    warehouse = SqliteLeadWarehouse([gold_lead_row(index, refreshed_at=None) for index in range(10)])
    repo = DatabricksLeadRepository(warehouse, cache=TTLCache(now=clock), cache_ttl_s=300.0)  # type: ignore[arg-type]

    repo.list_page(None, None, limit=5)
    statements = len(warehouse.statements)
    repo.list_page(None, None, limit=5)

    assert len(warehouse.statements) == statements, "an unstamped page cannot be superseded"


def test_no_secret_serves_page_zero_unpaged_and_refuses_a_cursor_503(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository],
    audit: InMemoryAuditStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = client.get("/api/leads", headers=HEADERS)
    monkeypatch.setattr(settings, "mip_genie_action_secret_current", None)
    monkeypatch.setattr(settings, "mip_genie_action_secret", None)
    monkeypatch.setattr(settings, "app_env", "sandbox")
    monkeypatch.setenv("MIP_COTALITY_ID_MASK_SECRET", "clip-mask-key-0123456789abcdef0123")

    page_zero = client.get("/api/leads", headers=HEADERS)
    paged = client.get("/api/leads", params={"cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)

    assert page_zero.status_code == 200
    assert page_zero.headers["X-Lead-Paging"] == "unavailable"
    assert "X-Next-Cursor" not in page_zero.headers
    assert paged.status_code == 503
    rows = _rows(audit)
    assert len(rows) == 2  # page 0 twice; the 503 wrote nothing
    assert "filter_fingerprint" not in rows[1]  # never a plain value


def test_an_empty_page_writes_one_row(audit: InMemoryAuditStore) -> None:
    _, _, prior = _wire(0)
    try:
        response = client.get("/api/leads", headers=HEADERS)
    finally:
        _restore(get_lead_repository, prior)

    assert response.status_code == 200 and response.json() == []
    (row,) = _rows(audit)
    assert row["rendered_borrower_ids"] == [] and row["page_index"] == 0
    assert row["view_id"] == response.headers["X-Lead-View-Id"]


class _Sales:
    _client = object()

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    def require_visible_assignee(self, *, actor: str, assigned_to_email: str, use_cache: bool = False) -> None:
        _ = (actor, assigned_to_email, use_cache)

    def borrower_ids_for_assignee(self, assigned_to_email: str | None) -> list[str] | None:
        _ = assigned_to_email
        return list(self.ids)

    def visible_lo_emails(self, *, actor: str) -> set[str]:
        raise KeyError(actor)


def test_the_assignment_early_return_writes_one_row(audit: InMemoryAuditStore) -> None:
    prior = _override(get_sales_state_store, _Sales([]))
    try:
        response = client.get("/api/leads", params={"assigned_to": "lo.two@summit.example"}, headers=HEADERS)
    finally:
        _restore(get_sales_state_store, prior)

    assert response.status_code == 200 and response.json() == []
    (row,) = _rows(audit)
    assert (row["rendered_borrower_ids"], row["page_index"], row["total_matching"]) == ([], 0, 0)
    assert row["assigned_to_email"] == "lo.two@summit.example"


def test_an_assignment_change_between_pages_is_not_refused(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository], audit: InMemoryAuditStore
) -> None:
    sales = _Sales([lead_id(index) for index in range(900)])
    prior = _override(get_sales_state_store, sales)
    try:
        params = {"assigned_to": "lo.two@summit.example"}
        first = client.get("/api/leads", params=params, headers=HEADERS)
        sales.ids = [lead_id(index) for index in range(950)]  # a lead was assigned meanwhile
        second = client.get("/api/leads", params={**params, "cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)
    finally:
        _restore(get_sales_state_store, prior)

    assert first.status_code == 200 and second.status_code == 200, second.text
    assert second.headers["X-Page-Index"] == "1"


def test_an_actor_who_lost_admin_gets_403_on_page_one(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository], audit: InMemoryAuditStore
) -> None:
    params = {"include_suppressed_for_analytics": "true"}
    first = client.get("/api/leads", params=params, headers=ADMIN_HEADERS)
    assert first.status_code == 200, first.text

    second = client.get("/api/leads", params={**params, "cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)

    assert second.status_code == 403
    assert len(_rows(audit)) == 1


def test_a_cohort_that_can_no_longer_be_replayed_fails_on_page_one(
    gold: tuple[SqliteLeadWarehouse, DatabricksLeadRepository],
    audit: InMemoryAuditStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def replay(cohort_id: str, *, actor: str) -> CohortReplay:
        calls.append(cohort_id)
        if len(calls) > 1:
            raise HTTPException(status_code=404, detail="Cohort not found")
        return CohortReplay(
            filters={"states": ["IL"]},
            stated_count=None,
            segment_mode="any",
            state_codes=["IL"],
            zip_codes=None,
            city_states=None,
            county_fipses=None,
            borrower_ids=None,
            segment_codes=None,
            county_fips=None,
            target_lender_ref=None,
            unreplayable_filters=[],
        )

    monkeypatch.setattr(lead_query_resolution, "resolve_cohort_replay", replay)
    params = {"cohort_id": "11111111-1111-4111-8111-111111111111"}
    first = client.get("/api/leads", params=params, headers=HEADERS)
    assert first.status_code == 200, first.text

    second = client.get("/api/leads", params={**params, "cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)

    assert second.status_code == 404
    assert len(calls) == 2  # the preamble re-ran on page 1
    assert len(_rows(audit)) == 1


def _handoff(total: int) -> str:
    return issue_growth_agent_handoff(
        actor=ACTOR,
        run_id="11111111-1111-4111-8111-111111111111",
        normalized_filters=normalise_lead_queue_handoff_filters(
            LeadCohortFilters(
                segment="itm", portfolio_criteria=PortfolioCriteria(marketing_eligibility="Eligible only")
            )
        ),
        cohort_fingerprint=cohort_fingerprint(cohort_digest="b" * 64, tool_result_hash="a" * 64),
        total=total,
        source_snapshot="snapshot-1",
        tool_result_hash="a" * 64,
    )


class _HandoffRepo:
    """Page 0 through the identity read, later pages by keyset; counts each call."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.leads = [LeadSummary(**redact_lead_row(gold_lead_row(index))) for index in range(1_000)]

    def list_with_identity_page(self, **_kwargs: object) -> tuple[LeadPage, dict[str, str | int]]:
        self.calls.append("identity")
        page = LeadPage(leads=self.leads[:500], has_more=True, last_keyset=(-50, self.leads[499].borrower_id))
        return page, {"total": 1_000, "cohort_digest": "b" * 64, "snapshot_id": "snapshot-1"}

    def list_page(self, **kwargs: object) -> LeadPage:
        self.calls.append(f"page:{kwargs.get('after')}")
        return LeadPage(leads=self.leads[500:], has_more=False, last_keyset=(-99, self.leads[-1].borrower_id))

    def count(self, **_kwargs: object) -> int:
        self.calls.append("count")
        return 1_000


def test_a_handoff_view_verifies_on_page_zero_only_and_echoes_its_provenance(
    audit: InMemoryAuditStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _HandoffRepo()
    prior = _override(get_lead_repository, repo)
    verified: list[str] = []
    real_verify = lead_query_resolution.verify_growth_agent_handoff

    def counting_verify(*args: Any, **kwargs: Any) -> Any:
        verified.append("verify")
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(lead_query_resolution, "verify_growth_agent_handoff", counting_verify)
    params = {"segment": "itm", "growth_handoff": _handoff(1_000), "include_identity_proof": "true"}
    try:
        first = client.get("/api/leads", params=params, headers=HEADERS)
        assert first.status_code == 200, first.text
        second = client.get("/api/leads", params={**params, "cursor": first.headers["X-Next-Cursor"]}, headers=HEADERS)
        swapped = client.get(
            "/api/leads",
            params={**params, "growth_handoff": _handoff(999), "cursor": first.headers["X-Next-Cursor"]},
            headers=HEADERS,
        )
    finally:
        _restore(get_lead_repository, prior)

    assert second.status_code == 200, second.text
    assert swapped.status_code == 422
    assert verified == ["verify"]
    assert repo.calls[0] == "identity" and repo.calls[1].startswith("page:")
    assert "count" not in repo.calls and repo.calls.count("identity") == 1
    page0, page1 = _rows(audit)
    for key in ("growth_agent_run_id", "growth_agent_cohort_fingerprint", "tool_result_hash"):
        assert page1[key] == page0[key]
    assert page1["page_index"] == 1 and page1["total_matching"] == 1_000


def test_the_audit_sort_vocabulary_is_the_order_builders() -> None:
    for sort in LEAD_SORTS:
        assert validate_lead_view_values({"sort": sort}) == []
    assert validate_lead_view_values({"sort": "relationship"})
    assert validate_lead_view_values({"view_id": "A" * 32})
    assert validate_lead_view_values({"page_index": 10})
    assert validate_lead_view_values({"page_index": True})
    assert validate_lead_view_values({"pages_loaded": 0})
    assert validate_lead_view_values({"sort_dir": "up"})
    assert validate_lead_view_values({"total_matching": -1})
    assert validate_lead_view_values({"declared_lead_view_id": "0" * 31})
    assert validate_lead_view_values(
        {"view_id": "0" * 32, "page_index": 9, "pages_loaded": 10, "sort_dir": "asc", "total_matching": 0}
    ) == []


# -- the decision-to-view join (client-declared) ------------------------------------------

VIEW = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def approver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "approver_identities", ACTOR)
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)


def test_approve_and_reject_record_the_declared_lead_view(
    audit: InMemoryAuditStore, approver: None
) -> None:
    approve = client.post(
        "/api/outreach/approve",
        json=reviewed_approval(client, "B-48291", headers=HEADERS, lead_view_id=VIEW),
        headers=HEADERS,
    )
    reject = client.post(
        "/api/outreach/reject",
        json={"borrower_id": "B-48294", "rationale_code": "low_intent", "lead_view_id": VIEW},
        headers=HEADERS,
    )

    assert approve.status_code == 200, approve.text
    assert reject.status_code == 200, reject.text
    rows = {event.event_type: event.payload_json for event in audit.list(limit=20)}
    assert rows["APPROVE"]["declared_lead_view_id"] == VIEW
    assert rows["OUTREACH_REJECT"]["declared_lead_view_id"] == VIEW


def test_a_decision_without_a_view_records_none_and_a_malformed_one_is_422(
    audit: InMemoryAuditStore, approver: None
) -> None:
    plain = client.post(
        "/api/outreach/reject", json={"borrower_id": "B-48294", "rationale_code": "low_intent"}, headers=HEADERS
    )
    bad = client.post(
        "/api/outreach/reject",
        json={"borrower_id": "B-48295", "rationale_code": "low_intent", "lead_view_id": "not-a-view"},
        headers=HEADERS,
    )

    assert plain.status_code == 200, plain.text
    assert "declared_lead_view_id" not in audit.list(limit=5)[0].payload_json
    assert bad.status_code == 422
