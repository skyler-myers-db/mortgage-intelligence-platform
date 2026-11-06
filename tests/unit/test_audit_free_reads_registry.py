"""The frontend's reviewed audit-free read list is audit-free on the server.

``frontend/src/lib/auditFreeReads.ts`` lists, per HTTP method, the reads a
recovery may complete after the network lost the body of their answer (PR
#326): a 200 whose connection dropped mid-transfer. The server answered that
read, so an audited one has already written its row, and re-reading it on a
reconnect would write a second with no act behind it. A method and path
missing from the list count as audited (fail-closed), so this pins only that
nothing audited is ever ON it.

The pin is behavioural first. Every entry is called on the mounted app with
every audit channel recording:

- the audit store, installed both as the dependency override and as the
  ``get_audit_store()`` singleton, so a body-level factory call is seen too;
- the Lakebase client, installed both ways and wrapped so every statement is
  recorded: ``INSERT INTO mip_app.action_audit`` written as raw SQL by a store
  (saved views, workspace, sales state, loan officers and the rest) is a write
  the store never sees;
- the statement logs of the fake Lakebase clients themselves (the suite's
  shared fake and a probe's own stand-in), scanned for the same INSERT from
  where they stood before the request: a service built over the raw fake
  (the home summary's KPI deltas) never goes through the wrapper;
- every background task the request queues (the VIEW_* writes run in one).

The call must answer 2xx (it ran to its end), and record none of the three.
Controls prove each channel is seen: GET /api/leads (the store, in a
background task), POST /api/workspace/saved-views (raw SQL, invisible to the
dependency walk), two throwaway routes that write through the factory
singletons from their bodies, and GET /api/home/summary with a ledger INSERT
added to its KPI-delta read, which runs over the raw shared fake.

Two structural checks stay beside it:

- the dependency walk: no entry takes a background task anywhere in its
  dependency tree, and none takes the audit store except the reviewed ledger
  reads, which only list it (the probe proves they write nothing);
- the matcher: for every entry and every mounted route of the same method
  whose templates unify segment by segment, the app serves the unifying path
  with an entry. So no route outside the list serves a path the frontend's
  matcher would call audit-free.

Residual: an entry runs on the suite's baseline fakes wherever
``tests/conftest.py`` installs one, and a probe's ``setup`` may install more.
Those are the repositories (portfolio and campaigns, analytics, segments,
leads, borrowers, geo), the geo assignment overlay, and the admin rules,
asset metadata and home summary services. So the production code behind
them is not run: a write added there (the Lakebase-backed campaign
repository behind /portfolio and /campaigns among them, whose create and
status SQL already writes ledger rows) is outside the probe, and the walk
and review cover it.
"""

from __future__ import annotations

import re
import secrets
import typing
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette import background as starlette_background
from starlette.routing import Match

import backend.services.audit_store as audit_store_module
import backend.services.lakebase as lakebase_module
import backend.services.saved_view_store as saved_view_store_module
import backend.services.workspace_store as workspace_store_module
from backend.main import app
from backend.services.asset_metadata import AssetMetadataService, get_asset_metadata_service
from backend.services.audit_store import AuditStore, get_audit_store
from backend.services.databricks_jobs import get_job_operations
from backend.services.home_attribution import get_home_attribution_service
from backend.services.kpi_deltas import KpiDeltaService
from backend.services.lakebase import get_lakebase_client
from backend.services.repositories import (
    get_rate_sensitivity_repository,
    get_rate_window_repository,
    get_segment_combination_repository,
)
from backend.services.repositories.factory import get_lead_facet_repository
from backend.services.workspace_store import get_workspace_store
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.test_admin_operations import _FakeOps
from tests.unit.test_analytics_rate_window_api import _StubRateWindowRepo
from tests.unit.test_asset_freshness_api import _ReadinessSql
from tests.unit.test_genie_history_api import _Lakebase as _GenieHistoryLakebase
from tests.unit.test_genie_history_api import _response as _genie_response
from tests.unit.test_genie_history_api import _turn_row
from tests.unit.test_geo_rate_sensitivity import _StubRepo as _RateSensitivityRepo
from tests.unit.test_home_attribution import _FakeSql as _AttributionSql
from tests.unit.test_home_attribution import _recent_baseline
from tests.unit.test_home_attribution import _service as _attribution_service
from tests.unit.test_leads_count_facets import _FacetRepo
from tests.unit.test_segment_combinations_api import _StubRepo as _CombinationRepo

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_FREE_TS = REPO_ROOT / "frontend" / "src" / "lib" / "auditFreeReads.ts"

# The audited GET reads the app makes (visual.ts AUDITED_READS), which the
# dependency walk must classify as audited: a control that it is not vacuous.
KNOWN_AUDITED_GETS = {
    "/api/leads",
    "/api/borrowers/{borrower_id}",
    "/api/borrowers/{borrower_id}/proof",
    "/api/audit/events",
    "/api/audit/events/page",
    "/api/audit/rollups",
    "/api/audit/facets",
    "/api/audit/count",
    "/api/audit/refusal-reports",
    "/api/audit/refusal-reports/{report_id}/question",
}

# Entries that take the audit store only to LIST it (a ledger read that writes
# no row). The walk cannot tell a reader from a writer; the probe below can.
LEDGER_READ_ENTRIES = {
    ("GET", "/api/admin/operations"),
    ("GET", "/api/audit/my-events"),
}

ACTOR = {"X-Forwarded-Email": "operator@summit.example"}
BORROWER = "B-48291"
PORTFOLIO = "11111111-1111-4111-8111-111111111111"
LOAN_OFFICER_ID = "55555555-5555-4555-8555-555555555501"
PLAN_REQUEST_ID = "5a1e0000-0000-4000-8000-0000000000bb"
SALES_WINDOW = "from=2026-07-14&to=2026-07-14"
AUDIT_INSERT = re.compile(r"\bINSERT\s+INTO\s+(?:mip_app\s*\.\s*)?action_audit\b", re.IGNORECASE)


# -- the registry -------------------------------------------------------------


def _array(source: str, name: str) -> list[str]:
    match = re.search(
        rf"export const {name}: readonly string\[\] = \[(.*?)\];",
        source,
        re.DOTALL,
    )
    assert match, f"{name} not found in auditFreeReads.ts"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return re.findall(r"'([^']+)'", body)


def _registry() -> list[tuple[str, str]]:
    source = AUDIT_FREE_TS.read_text(encoding="utf-8")
    return [("GET", path) for path in _array(source, "AUDIT_FREE_GET_ROUTES")] + [
        ("POST", path) for path in _array(source, "AUDIT_FREE_POST_READS")
    ]


def _logical(path: str) -> str:
    return re.sub(r"^/api/v1(?=/|$)", "/api", path)


def _api_routes() -> dict[tuple[str, str], list[APIRoute]]:
    routes: dict[tuple[str, str], list[APIRoute]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        for method in route.methods:
            routes.setdefault((method, _logical(route.path)), []).append(route)
    return routes


# -- the dependency walk ------------------------------------------------------


def _returns_audit_store(call: Callable[..., object]) -> bool:
    if call is get_audit_store:
        return True
    try:
        hints = typing.get_type_hints(call)
    except Exception:  # noqa: BLE001 - an unresolvable hint is not the store
        return False
    return hints.get("return") is AuditStore


def _takes_background_tasks(dependant: Dependant) -> bool:
    if dependant.background_tasks_param_name:
        return True
    return any(_takes_background_tasks(sub) for sub in dependant.dependencies)


def _takes_audit_store(dependant: Dependant) -> bool:
    for sub in dependant.dependencies:
        if sub.call is not None and _returns_audit_store(sub.call):
            return True
        if _takes_audit_store(sub):
            return True
    return False


def _audit_capable(dependant: Dependant) -> bool:
    """The shape of a route that writes through the store: the store or a background task."""

    return _takes_background_tasks(dependant) or _takes_audit_store(dependant)


# -- the behavioural probe ----------------------------------------------------


@dataclass
class _Sinks:
    """What one request wrote: store writes, raw ledger INSERTs, background tasks."""

    store_writes: list[str] = field(default_factory=list)
    ledger_inserts: list[str] = field(default_factory=list)
    background_tasks: list[str] = field(default_factory=list)

    # Ledger INSERTs found in a fake client's own statement log: a write
    # through a client the service captured, which the wrapper never saw.
    logged_inserts: list[str] = field(default_factory=list)

    def total(self) -> int:
        return (
            len(self.store_writes)
            + len(self.ledger_inserts)
            + len(self.background_tasks)
            + len(self.logged_inserts)
        )


class _RecordingAuditStore:
    """An in-memory audit store that records every write it is asked for."""

    def __init__(self, sinks: _Sinks) -> None:
        self._inner = InMemoryAuditStore()
        self._sinks = sinks

    def write(self, **kwargs: Any) -> Any:
        self._sinks.store_writes.append(str(kwargs.get("event_type") or kwargs.get("action")))
        return self._inner.write(**kwargs)

    def list(self, limit: int = 50, **kwargs: Any) -> Any:
        return self._inner.list(limit, **kwargs)

    def event_count(self) -> int:
        return len(self._inner._events)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _ledger_sql(values: typing.Iterable[Any]) -> list[str]:
    return [
        " ".join(value.split())[:120]
        for value in values
        if isinstance(value, str) and AUDIT_INSERT.search(value)
    ]


def _note_sql(sinks: _Sinks, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    sinks.ledger_inserts.extend(_ledger_sql((*args, *kwargs.values())))


# The statement logs a fake Lakebase client keeps (tests/conftest.py
# _FakeLakebaseClient): each entry is a tuple whose first item is the SQL.
_SQL_LOGS = ("executes", "fetchones", "fetchalls")


def _sql_log_marks(*clients: Any) -> list[tuple[list[Any], int]]:
    """Where each fake client's statement log stands now (one mark per log)."""

    marks: dict[int, tuple[list[Any], int]] = {}
    for client in clients:
        for name in _SQL_LOGS:
            log = getattr(client, name, None)
            if isinstance(log, list):
                marks.setdefault(id(log), (log, len(log)))
    return list(marks.values())


def _logged_ledger_sql(marks: list[tuple[list[Any], int]]) -> list[str]:
    """The ledger INSERTs the marked logs gained, whichever client the caller held."""

    found: list[str] = []
    for log, start in marks:
        for entry in log[start:]:
            found.extend(_ledger_sql(entry if isinstance(entry, tuple) else (entry,)))
    return found


class _RecordingCursor:
    def __init__(self, inner: Any, sinks: _Sinks) -> None:
        self._inner = inner
        self._sinks = sinks

    def __enter__(self) -> _RecordingCursor:
        if hasattr(self._inner, "__enter__"):
            self._inner.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        if hasattr(self._inner, "__exit__"):
            self._inner.__exit__(*exc)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        _note_sql(self._sinks, args, kwargs)
        return self._inner.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingConnection:
    def __init__(self, inner: Any, sinks: _Sinks) -> None:
        self._inner = inner
        self._sinks = sinks

    def cursor(self, *args: Any, **kwargs: Any) -> _RecordingCursor:
        return _RecordingCursor(self._inner.cursor(*args, **kwargs), self._sinks)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        _note_sql(self._sinks, args, kwargs)
        return self._inner.execute(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingLakebase:
    """Wraps a fake Lakebase client: every SQL string any method is given is scanned."""

    def __init__(self, inner: Any, sinks: _Sinks) -> None:
        self._inner = inner
        self._sinks = sinks

    @contextmanager
    def transaction(self) -> Iterator[_RecordingConnection]:
        with self._inner.transaction() as conn:
            yield _RecordingConnection(conn, self._sinks)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def recorded(*args: Any, **kwargs: Any) -> Any:
            _note_sql(self._sinks, args, kwargs)
            return attr(*args, **kwargs)

        return recorded


@dataclass(frozen=True)
class _Probe:
    """How to call one entry so it runs to its end."""

    path: str
    query: str = ""
    json: dict[str, Any] | None = None
    # Installs the fakes the route needs beyond the suite's baseline; it is
    # given the Lakebase client the request will read through.
    setup: Callable[[pytest.MonkeyPatch, FastAPI, Any], None] | None = None
    # A Lakebase stand-in that serves the rows the route reads.
    lakebase: Callable[[], Any] | None = None


def _override(
    dependency: Callable[..., Any], value: object
) -> Callable[[pytest.MonkeyPatch, FastAPI, Any], None]:
    def install(monkeypatch: pytest.MonkeyPatch, target: FastAPI, _lakebase: Any) -> None:
        monkeypatch.setitem(target.dependency_overrides, dependency, lambda: value)

    return install


def _asset_service() -> AssetMetadataService:
    return AssetMetadataService(_ReadinessSql())


def _genie_history() -> _GenieHistoryLakebase:
    answer = _genie_response(message_id="msg-1", question="How many borrowers are in the money?")
    return _GenieHistoryLakebase(
        turn_rows=[_turn_row("How many borrowers are in the money?", answer)]
    )


_SSE_PROBE_ID = secrets.token_hex(8)
_SSE_BOUND = "events=1&interval_ms=100"


def _ran_sse_probe(monkeypatch: pytest.MonkeyPatch, target: FastAPI, _lakebase: Any) -> None:
    TestClient(target).get(f"/api/v1/admin/sse-probe?probe_id={_SSE_PROBE_ID}&{_SSE_BOUND}")


def _assigned(monkeypatch: pytest.MonkeyPatch, target: FastAPI, lakebase: Any) -> None:
    """An active assignment of BORROWER in the suite's fake Lakebase (reset after each test)."""

    now = datetime.now(UTC)
    lakebase.assignments.append(
        {
            "assignment_id": uuid4(),
            "borrower_id": BORROWER,
            "assigned_to_email": "lo01@summit.example",
            "assigned_by": "operator@summit.example",
            "assigned_at": now,
            "expires_at": None,
            "released_at": None,
            "strategy": "manual",
            "request_id": None,
            "assignment_scope": "single",
            "loan_officer_id": LOAN_OFFICER_ID,
            "status": "assigned",
            "status_updated_at": now,
        }
    )


PROBES: dict[tuple[str, str], _Probe] = {
    ("GET", "/api/admin/assets/{asset_key}/metadata"): _Probe(
        "/api/admin/assets/lead_population/metadata"
    ),
    ("GET", "/api/admin/operations"): _Probe(
        "/api/admin/operations", setup=_override(get_job_operations, _FakeOps())
    ),
    ("GET", "/api/admin/sse-probe"): _Probe(
        "/api/admin/sse-probe", query=f"probe_id={secrets.token_hex(8)}&{_SSE_BOUND}"
    ),
    ("GET", "/api/admin/sse-probe/{probe_id}"): _Probe(
        f"/api/admin/sse-probe/{_SSE_PROBE_ID}", setup=_ran_sse_probe
    ),
    ("GET", "/api/analytics/funnel/loan-officers/{loan_officer_id}"): _Probe(
        f"/api/analytics/funnel/loan-officers/{LOAN_OFFICER_ID}"
    ),
    ("GET", "/api/analytics/rate-window"): _Probe(
        "/api/analytics/rate-window",
        setup=_override(get_rate_window_repository, _StubRateWindowRepo()),
    ),
    ("GET", "/api/assets/{asset_key}/freshness"): _Probe(
        "/api/assets/portfolio_headline_metric_view/freshness",
        setup=_override(get_asset_metadata_service, _asset_service()),
    ),
    ("GET", "/api/borrowers/search"): _Probe("/api/borrowers/search", query="q=Chicago"),
    ("GET", "/api/borrowers/{borrower_id}/decisions"): _Probe(
        f"/api/borrowers/{BORROWER}/decisions"
    ),
    ("GET", "/api/borrowers/{borrower_id}/evidence"): _Probe(f"/api/borrowers/{BORROWER}/evidence"),
    ("GET", "/api/borrowers/{borrower_id}/lifecycle"): _Probe(
        f"/api/borrowers/{BORROWER}/lifecycle"
    ),
    ("GET", "/api/campaigns/{campaign_id}"): _Probe(f"/api/campaigns/{PORTFOLIO}"),
    ("GET", "/api/genie/sessions/{conversation_id}"): _Probe(
        "/api/genie/sessions/conv-1", lakebase=_genie_history
    ),
    ("GET", "/api/geo/county-rollups"): _Probe("/api/geo/county-rollups", query="state=IL"),
    ("GET", "/api/geo/rate-sensitivity"): _Probe(
        "/api/geo/rate-sensitivity",
        setup=_override(get_rate_sensitivity_repository, _RateSensitivityRepo()),
    ),
    ("GET", "/api/geo/zip-rollups"): _Probe("/api/geo/zip-rollups", query="state=IL"),
    ("GET", "/api/growth-agent/agent/plan/progress"): _Probe(
        "/api/growth-agent/agent/plan/progress", query=f"request_id={PLAN_REQUEST_ID}"
    ),
    ("GET", "/api/home/summary/attribution"): _Probe(
        "/api/home/summary/attribution",
        query=f"measure=refi_economics_screen&baseline={_recent_baseline()}",
        setup=_override(get_home_attribution_service, _attribution_service(_AttributionSql())),
    ),
    ("GET", "/api/kpi-proof"): _Probe("/api/kpi-proof", query="kpi=home.in_the_money"),
    ("GET", "/api/leads/facets"): _Probe(
        "/api/leads/facets",
        query="dimension=state",
        setup=_override(get_lead_facet_repository, _FacetRepo()),
    ),
    ("GET", "/api/leads/{borrower_id}/assignment"): _Probe(
        f"/api/leads/{BORROWER}/assignment", setup=_assigned
    ),
    ("GET", "/api/portfolio/{portfolio_id}"): _Probe(f"/api/portfolio/{PORTFOLIO}"),
    ("GET", "/api/sales/campaign-performance"): _Probe(
        "/api/sales/campaign-performance", query=SALES_WINDOW
    ),
    ("GET", "/api/sales/conversion"): _Probe("/api/sales/conversion", query=SALES_WINDOW),
    ("GET", "/api/sales/outcomes/summary"): _Probe(
        "/api/sales/outcomes/summary", query=SALES_WINDOW
    ),
    ("GET", "/api/sales/workload"): _Probe("/api/sales/workload", query=SALES_WINDOW),
    ("GET", "/api/segments/combinations"): _Probe(
        "/api/segments/combinations",
        setup=_override(get_segment_combination_repository, _CombinationRepo()),
    ),
    ("POST", "/api/genie/start"): _Probe("/api/genie/start", json={"context": {}}),
    ("POST", "/api/portfolio/campaign-recommendation"): _Probe(
        "/api/portfolio/campaign-recommendation", json={"criteria": {}}
    ),
    ("POST", "/api/portfolio/preview"): _Probe(
        "/api/portfolio/preview", json={"criteria": {"marketing_eligibility": "Any"}}
    ),
}


@dataclass(frozen=True)
class _Outcome:
    status: int
    body: str
    sinks: _Sinks


def _call(
    target: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    probe: _Probe,
) -> _Outcome:
    """One request with every audit channel recording, and nothing live reachable."""

    # No live workspace: a factory that slipped past the fakes fails, never connects.
    monkeypatch.setenv(
        "DATABRICKS_CONFIG_FILE", str(REPO_ROOT / "tests" / "fixtures" / "no-such-databrickscfg")
    )
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    sinks = _Sinks()
    store = _RecordingAuditStore(sinks)
    # The suite's shared fake (tests/conftest.py, reset around every test) unless the probe serves its own.
    shared = app.dependency_overrides[get_lakebase_client]()
    inner = probe.lakebase() if probe.lakebase is not None else shared
    lakebase = _RecordingLakebase(inner, sinks)
    for owner in {id(app): app, id(target): target}.values():
        monkeypatch.setitem(owner.dependency_overrides, get_audit_store, lambda: store)
        monkeypatch.setitem(owner.dependency_overrides, get_lakebase_client, lambda: lakebase)
        # The production store over the recording client: its raw ledger SQL is seen.
        if get_workspace_store in owner.dependency_overrides:
            monkeypatch.delitem(owner.dependency_overrides, get_workspace_store)
    monkeypatch.setattr(audit_store_module, "_AUDIT_STORE", store)
    monkeypatch.setattr(lakebase_module, "_CLIENT", lakebase)
    monkeypatch.setattr(saved_view_store_module, "_SAVED_VIEW_STORE", None)
    monkeypatch.setattr(workspace_store_module, "_WORKSPACE_STORE", None)
    if probe.setup is not None:
        probe.setup(monkeypatch, target, inner)
    sinks.store_writes.clear()
    sinks.ledger_inserts.clear()
    # A service may hold the raw fake (conftest builds the home summary over
    # it), so the fakes' own logs are read too, from here on.
    marks = _sql_log_marks(shared, inner)

    real_init = starlette_background.BackgroundTask.__init__

    def recording_init(self: Any, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        sinks.background_tasks.append(getattr(func, "__qualname__", repr(func)))
        real_init(self, func, *args, **kwargs)

    monkeypatch.setattr(starlette_background.BackgroundTask, "__init__", recording_init)
    url = probe.path + (f"?{probe.query}" if probe.query else "")
    client = TestClient(target, raise_server_exceptions=False)
    if method == "GET":
        response = client.get(url, headers=ACTOR)
    else:
        response = client.request(
            method, url, headers=ACTOR, json=probe.json if probe.json is not None else {}
        )
    monkeypatch.setattr(starlette_background.BackgroundTask, "__init__", real_init)
    sinks.logged_inserts.extend(_logged_ledger_sql(marks))
    # A write through any other method of the store still lands in its ledger.
    unrecorded = store.event_count() - len(sinks.store_writes)
    sinks.store_writes.extend(["<written without write()>"] * max(0, unrecorded))
    return _Outcome(response.status_code, response.text[:300], sinks)


def _entry_probe(method: str, path: str) -> _Probe:
    return PROBES.get((method, path), _Probe(path))


# -- tests --------------------------------------------------------------------


def test_the_registry_parses_to_unique_api_paths() -> None:
    entries = _registry()
    assert len(entries) > 20, "the audit-free list parsed (nearly) empty"
    assert len(entries) == len(set(entries))
    assert {method for method, _ in entries} == {"GET", "POST"}
    assert all(path.startswith("/api/") and not path.startswith("/api/v1/") for _, path in entries)


def test_every_probe_names_an_entry() -> None:
    entries = set(_registry())
    assert entries >= set(PROBES), sorted(set(PROBES) - entries)
    assert entries >= LEDGER_READ_ENTRIES, sorted(LEDGER_READ_ENTRIES - entries)


@pytest.mark.parametrize(("method", "path"), _registry(), ids=lambda value: str(value))
def test_every_entry_answers_and_writes_no_audit_row(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    outcome = _call(app, monkeypatch, method, _entry_probe(method, path))
    assert 200 <= outcome.status < 300, (
        f"{method} {path} answered {outcome.status}, so the probe never ran it to its end: "
        f"give it a probe in PROBES. {outcome.body}"
    )
    assert outcome.sinks.total() == 0, (
        f"{method} {path} wrote audit evidence ({outcome.sinks}): a recovery must never "
        "re-read its answer on its own, so it cannot be on the audit-free list"
    )


def test_the_probe_sees_an_audit_store_write_in_a_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = _call(app, monkeypatch, "GET", _Probe("/api/leads", query="limit=5"))
    assert outcome.status == 200, outcome.body
    assert "VIEW_LEADS" in outcome.sinks.store_writes
    assert outcome.sinks.background_tasks


def test_the_probe_sees_a_raw_sql_ledger_write_the_walk_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    [route] = [
        r
        for r in _api_routes()[("POST", "/api/workspace/saved-views")]
        if r.path.startswith("/api/v1/")
    ]
    assert not _audit_capable(
        route.dependant
    ), "the control needs a writer the dependency walk misses"
    outcome = _call(
        app,
        monkeypatch,
        "POST",
        _Probe(
            "/api/workspace/saved-views",
            json={"name": "Probe view", "params": "?state=IL&sort=score&dir=desc"},
        ),
    )
    assert outcome.sinks.ledger_inserts, outcome
    assert not outcome.sinks.store_writes


def test_the_probe_sees_body_level_writes_through_the_factory_singletons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The two shapes the walk cannot see, on a throwaway app: a body that calls
    # the audit-store factory, and one that writes ledger SQL through the
    # Lakebase factory. Neither takes a dependency the walk could flag.
    synthetic = FastAPI()

    @synthetic.get("/api/v1/probe/store")
    def body_store_write() -> dict[str, str]:
        audit_store_module.get_audit_store().write(
            actor="probe@summit.example", action="probe.view", entity_type="probe", entity_id="p-1"
        )
        return {"ok": "store"}

    @synthetic.get("/api/v1/probe/sql")
    def body_sql_write() -> dict[str, str]:
        lakebase_module.get_lakebase_client().execute(
            "INSERT INTO mip_app.action_audit (event_type) VALUES (%(event_type)s)",
            {"event_type": "PROBE"},
        )
        return {"ok": "sql"}

    store = _call(synthetic, monkeypatch, "GET", _Probe("/api/v1/probe/store"))
    sql = _call(synthetic, monkeypatch, "GET", _Probe("/api/v1/probe/sql"))
    assert store.status == 200 and store.sinks.store_writes, store
    assert sql.status == 200 and sql.sinks.ledger_inserts, sql


def test_the_probe_sees_a_ledger_write_through_a_client_a_service_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GET /api/home/summary is an entry, and the suite builds its service over
    # the RAW shared fake (tests/conftest.py: KpiDeltaService(lakebase_client=
    # <the fake>)), so neither the override nor the factory singleton the
    # wrapper is installed as ever serves it. A ledger INSERT added to its
    # previous-visit read is still seen, in the fake's own statement log.
    original = KpiDeltaService.previous_visit

    def previous_visit_with_a_ledger_write(
        self: KpiDeltaService, actor_email: str, *, before: datetime
    ) -> datetime | None:
        self._lakebase().execute(
            "INSERT INTO mip_app.action_audit (actor_email, event_type) "
            "VALUES (%(actor_email)s, 'VIEW_HOME_SUMMARY')",
            {"actor_email": actor_email},
        )
        return original(self, actor_email, before=before)

    monkeypatch.setattr(KpiDeltaService, "previous_visit", previous_visit_with_a_ledger_write)
    outcome = _call(app, monkeypatch, "GET", _entry_probe("GET", "/api/home/summary"))
    assert outcome.status == 200, outcome.body
    assert outcome.sinks.logged_inserts, outcome
    # The wrapper alone would have passed it: the write never went through it.
    assert not outcome.sinks.ledger_inserts, outcome


def test_the_walk_classifies_every_known_audited_read_as_audited() -> None:
    routes = _api_routes()
    for path in sorted(KNOWN_AUDITED_GETS):
        assert ("GET", path) in routes, f"{path} is not a mounted GET route"
        assert all(_audit_capable(route.dependant) for route in routes[("GET", path)]), path


def test_every_entry_is_mounted_and_takes_no_background_task_or_writing_store() -> None:
    routes = _api_routes()
    for method, path in _registry():
        assert (method, path) in routes, f"{method} {path} is not a mounted route"
        assert path not in KNOWN_AUDITED_GETS
        for route in routes[(method, path)]:
            assert not _takes_background_tasks(
                route.dependant
            ), f"{method} {route.path} takes a background task: it can write an audit row"
            takes_store = _takes_audit_store(route.dependant)
            if (method, path) in LEDGER_READ_ENTRIES:
                assert (
                    takes_store
                ), f"{method} {path} no longer reads the ledger: drop it from LEDGER_READ_ENTRIES"
            else:
                assert not takes_store, (
                    f"{method} {route.path} takes the audit store: review it, and list it in "
                    "LEDGER_READ_ENTRIES only when it reads the ledger and the probe shows no write"
                )


# -- the matcher --------------------------------------------------------------

_UNIFIER_SLOT = "zz-unifier-slot"


def _is_slot(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _is_path_slot(segment: str) -> bool:
    return _is_slot(segment) and segment.endswith(":path}")


def _concrete(segments: list[str]) -> list[str]:
    return [_UNIFIER_SLOT if _is_slot(segment) else segment for segment in segments]


def _unifier(left: str, right: str) -> str | None:
    """A path both templates match (each ``{slot}`` is one segment; ``{x:path}`` the rest), or None."""

    a, b = left.strip("/").split("/"), right.strip("/").split("/")
    out: list[str] = []
    for index in range(max(len(a), len(b))):
        x = a[index] if index < len(a) else None
        y = b[index] if index < len(b) else None
        if x is not None and _is_path_slot(x):
            return "/" + "/".join(out + (_concrete(b[index:]) or [_UNIFIER_SLOT]))
        if y is not None and _is_path_slot(y):
            return "/" + "/".join(out + (_concrete(a[index:]) or [_UNIFIER_SLOT]))
        if x is None or y is None:
            return None
        if _is_slot(x) and _is_slot(y):
            out.append(_UNIFIER_SLOT)
        elif _is_slot(x):
            out.append(y)
        elif _is_slot(y) or x == y:
            out.append(x)
        else:
            return None
    return "/" + "/".join(out)


def _served_by(method: str, path: str) -> object | None:
    scope = {
        "type": "http",
        "path": path,
        "method": method,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route
    return None


def test_the_frontend_matcher_unifies_templates_as_the_server_routes_them() -> None:
    assert (
        _unifier("/api/borrowers/{borrower_id}", "/api/borrowers/search") == "/api/borrowers/search"
    )
    assert (
        _unifier("/api/portfolio/{portfolio_id}", "/api/portfolio/preview")
        == "/api/portfolio/preview"
    )
    assert _unifier("/api/a/{x}", "/api/{y}/b") == "/api/a/b"
    assert _unifier("/api/a/{x}", "/api/a/{y}/c") is None
    assert _unifier("/api/a", "/api/b") is None
    assert (
        _unifier("/{full_path:path}", "/api/leads/{borrower_id}") == f"/api/leads/{_UNIFIER_SLOT}"
    )


def test_no_route_outside_the_list_serves_a_path_an_entry_matches() -> None:
    entries = set(_registry())
    mounted: list[tuple[str, str]] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            mounted.extend((method, route.path) for method in route.methods)
    failures: list[str] = []
    for method, entry in sorted(entries):
        for other_method, template in mounted:
            if other_method != method:
                continue
            unified = _unifier(entry, _logical(template))
            if unified is None:
                continue
            for prefix in ("/api/v1", "/api"):
                concrete = re.sub(r"^/api(?=/|$)", prefix, unified)
                served = _served_by(method, concrete)
                if served is None:
                    continue
                if isinstance(served, APIRoute) and (method, _logical(served.path)) in entries:
                    continue
                label = served.path if isinstance(served, APIRoute) else repr(served)
                failures.append(
                    f"{method} {concrete} matches {entry} but the app serves it with {label}"
                )
    assert not failures, "\n".join(sorted(set(failures)))
