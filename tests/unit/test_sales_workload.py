"""GET /api/sales/workload: the Sales ops per-officer workload (audit critic-10 item 6c).

W5e w5-analytics item 10 (W5d w5-sales-loop CUT 2, ruled default (h)):
manager / admin only, scoped by the manager's visible loan officers, one
Lakebase read, never the warehouse, AUDIT-FREE, ``private, no-store``.
The route runs against a stub store through a dependency override; the
store's single statement is pinned with a recording client.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import audit_store as audit_store_module
from backend.services import databricks_sql
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import LakebaseError
from backend.services.sales_state import SalesStateStore, get_sales_state_store
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

MANAGER = "sm01@summit.example"
ADMIN = "admin01@summit.example"
LOAN_OFFICER = "lo01@summit.example"
OFF_ROSTER = "sm09@summit.example"

ROWS: list[dict[str, Any]] = [
    {
        "email": "lo01@summit.example",
        "display_label": "Summit LO 01",
        "region": "Midwest",
        "capacity_per_day": 25,
        "open_count": 14,
        "stale_count": 3,
        "assigned_count": 9,
        "worked_count": 7,
        "previous_assigned_count": 6,
        "previous_worked_count": 7,
    },
    {
        "email": "lo02@summit.example",
        "display_label": "Summit LO 02",
        "region": None,
        "capacity_per_day": 20,
        "open_count": 0,
        "stale_count": 0,
        "assigned_count": 0,
        "worked_count": 0,
        "previous_assigned_count": 2,
        "previous_worked_count": 1,
    },
    {
        "email": "lo07@summit.example",
        "display_label": "Summit LO 07",
        "region": "West",
        "capacity_per_day": 30,
        "open_count": 5,
        "stale_count": 0,
        "assigned_count": 4,
        "worked_count": 4,
        "previous_assigned_count": 0,
        "previous_worked_count": 0,
    },
]


class _Store:
    """The three store calls the route makes; records what it was asked."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def require_manager_actor(self, actor: str) -> None:
        if actor == LOAN_OFFICER:
            raise PermissionError("sales manager or admin access required")
        if actor not in {MANAGER, ADMIN}:
            raise KeyError(actor)

    def visible_lo_emails(self, *, actor: str) -> set[str] | None:
        return None if actor == ADMIN else {"lo01@summit.example", "lo02@summit.example"}

    def workload(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fail:
            raise LakebaseError("down")
        self.calls.append(kwargs)
        visible = kwargs["visible_lo_emails"]
        return [row for row in ROWS if visible is None or row["email"] in visible]


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Store]:
    stub = _Store()
    previous = app.dependency_overrides.get(get_sales_state_store)
    app.dependency_overrides[get_sales_state_store] = lambda: stub
    audit = InMemoryAuditStore()
    previous_audit = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: audit
    monkeypatch.setattr(audit_store_module, "_AUDIT_STORE", audit)

    def no_warehouse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the workload read must never touch the warehouse")

    monkeypatch.setattr(databricks_sql, "get_sql_client", no_warehouse)
    stub.audit = audit  # type: ignore[attr-defined]
    try:
        yield stub
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_sales_state_store, None)
        else:
            app.dependency_overrides[get_sales_state_store] = previous
        if previous_audit is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous_audit


def _get(query: str, actor: str = MANAGER, path: str = "/api/v1/sales/workload") -> Any:
    return TestClient(app).get(f"{path}?{query}", headers={"X-Forwarded-Email": actor})


def test_a_manager_reads_every_count_for_both_windows_scoped_to_their_officers(store: _Store) -> None:
    response = _get("from=2026-07-08&to=2026-07-14")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from_date"] == "2026-07-08"
    assert body["to_date"] == "2026-07-14"
    # The same span immediately before the period.
    assert body["previous_from_date"] == "2026-07-01"
    assert body["previous_to_date"] == "2026-07-07"
    assert body["stale_days"] == 7
    assert body["rows"] == ROWS[:2]
    assert store.calls == [
        {
            "from_date": "2026-07-08",
            "to_date": "2026-07-14",
            "previous_from": "2026-07-01",
            "previous_to": "2026-07-07",
            "stale_days": 7,
            "visible_lo_emails": {"lo01@summit.example", "lo02@summit.example"},
        }
    ]
    assert response.headers["cache-control"] == "private, no-store"


def test_an_admin_sees_every_loan_officer_and_a_chosen_stale_window(store: _Store) -> None:
    response = _get("from=2026-07-14&to=2026-07-14&stale_days=14", actor=ADMIN, path="/api/sales/workload")

    assert response.status_code == 200, response.text
    assert [row["email"] for row in response.json()["rows"]] == [row["email"] for row in ROWS]
    assert response.json()["previous_from_date"] == response.json()["previous_to_date"] == "2026-07-13"
    assert store.calls[0]["stale_days"] == 14
    assert store.calls[0]["visible_lo_emails"] is None


@pytest.mark.parametrize("actor", [LOAN_OFFICER, OFF_ROSTER], ids=["loan-officer", "manager-off-the-roster"])
def test_a_loan_officer_or_an_identity_off_the_roster_is_a_403(store: _Store, actor: str) -> None:
    response = _get("from=2026-07-08&to=2026-07-14", actor=actor)

    assert response.status_code == 403
    assert store.calls == []
    assert "@" not in response.text


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-07-14&to=2026-07-08",
        "from=2026-01-01&to=2026-07-14",
        "from=2026-07-08&to=2026-07-14&stale_days=0",
        "from=2026-07-08&to=2026-07-14&stale_days=31",
        "from=2026-07-08",
        "to=2026-07-14",
        "from=not-a-date&to=2026-07-14",
    ],
)
def test_a_reversed_over_long_or_malformed_window_is_a_422(store: _Store, query: str) -> None:
    response = _get(query)

    assert response.status_code == 422
    assert store.calls == []


def test_a_92_day_window_is_accepted(store: _Store) -> None:
    assert _get("from=2026-04-14&to=2026-07-14").status_code == 200


def test_a_lakebase_failure_is_a_503_with_the_governed_detail(store: _Store) -> None:
    store.fail = True
    response = _get("from=2026-07-08&to=2026-07-14")

    assert response.status_code == 503
    assert "down" not in response.text


def test_the_read_writes_no_audit_row(store: _Store) -> None:
    for actor in (MANAGER, ADMIN, LOAN_OFFICER):
        _get("from=2026-07-08&to=2026-07-14", actor=actor)
    assert store.audit.list(limit=100) == []  # type: ignore[attr-defined]


class _RecordingClient:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    def fetchall(self, sql: str, params: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.calls.append((sql, dict(params or {}), limit))
        return list(self.rows)


def test_the_store_runs_one_lakebase_statement_scoped_to_the_visible_officers() -> None:
    client = _RecordingClient([{**ROWS[0], "open_count": "14", "capacity_per_day": None}])
    rows = SalesStateStore(client).workload(  # type: ignore[arg-type]
        from_date="2026-07-08",
        to_date="2026-07-14",
        previous_from="2026-07-01",
        previous_to="2026-07-07",
        stale_days=7,
        visible_lo_emails={"lo02@summit.example", "lo01@summit.example"},
    )

    assert len(client.calls) == 1
    sql, params, limit = client.calls[0]
    assert params == {
        "from_date": "2026-07-08",
        "to_date": "2026-07-14",
        "previous_from": "2026-07-01",
        "previous_to": "2026-07-07",
        "stale_days": 7,
        "lo_emails": ["lo01@summit.example", "lo02@summit.example"],
    }
    assert "AND email = ANY(%(lo_emails)s)" in sql
    assert "role = 'loan_officer'" in sql and "active = true" in sql
    assert "released_at IS NULL" in sql
    assert "make_interval(days => %(stale_days)s)" in sql
    assert "NOT EXISTS" in sql and "d.occurred_at >= a.assigned_at" in sql
    for relation in ("mip_app.sales_team", "mip_app.lead_assignments", "mip_app.call_dispositions"):
        assert relation in sql
    assert "mip.gold" not in sql and "mip.silver" not in sql
    assert limit == 200
    assert rows[0]["open_count"] == 14
    assert rows[0]["capacity_per_day"] == 0

    admin = _RecordingClient()
    SalesStateStore(admin).workload(  # type: ignore[arg-type]
        from_date="2026-07-08",
        to_date="2026-07-14",
        previous_from="2026-07-01",
        previous_to="2026-07-07",
        stale_days=7,
        visible_lo_emails=None,
    )
    assert "ANY(%(lo_emails)s)" not in admin.calls[0][0]
