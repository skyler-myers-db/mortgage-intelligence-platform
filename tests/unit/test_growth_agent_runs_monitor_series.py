"""Growth Agent watchlist run series (audit 2026-09-21 ``wow-ai-4`` backend).

The 2026_10_01 block tags each watchlist-driven run with its ``monitor_id`` at
INSERT and records a new watchlist's first run as the monitor's
``seed_run_id``. The static pins keep the block additive and the run ledger
append-only (the real-Postgres re-run proof is
``tests/integration/test_growth_agent_runs_postgres.py``); the route tests
pin that Run now, run-due, run-due-all and a re-save of an existing name all
INSERT the run with its ``monitor_id``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import backend.services.rbac as rbac_module
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)

_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
_MARKER = "-- Growth Agent watchlist series ---"

_FINALIZE_ONLY_TRIGGERS = """CREATE TRIGGER trg_growth_agent_runs_finalize_only
    BEFORE UPDATE ON mip_app.growth_agent_runs
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_audit_event_finalize_only();

CREATE TRIGGER trg_growth_agent_runs_no_remove
    BEFORE DELETE OR TRUNCATE ON mip_app.growth_agent_runs
    FOR EACH STATEMENT
    EXECUTE FUNCTION mip_app.prevent_outreach_evidence_mutation();"""

_FINALIZE_ONLY_FUNCTION = """    IF (to_jsonb(NEW) - 'audit_event_id')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'audit_event_id')
       OR OLD.audit_event_id IS NOT NULL
       OR NEW.audit_event_id IS NULL THEN"""


def _series_block() -> str:
    # Up to its own version row: later waves append blocks after it.
    start = _SCHEMA.index(_MARKER)
    end_marker = "ON CONFLICT (version) DO NOTHING;"
    return _SCHEMA[start : _SCHEMA.index(end_marker, start) + len(end_marker)]


def test_the_series_block_is_versioned_once_and_closes_on_its_own_version_row() -> None:
    block = _series_block()
    assert _SCHEMA.count(_MARKER) == 1
    assert block.count("INSERT INTO mip_app.schema_migrations") == 1
    # The block's last statement is ITS version row, so the bound above never
    # stops at another block's ON CONFLICT row or swallows a later block.
    statements = [s.strip() for s in re.sub(r"--[^\n]*", "", block).split(";") if s.strip()]
    assert statements[-1].startswith("INSERT INTO mip_app.schema_migrations")
    assert "'2026_10_01_growth_agent_watchlist_series'" in statements[-1]


def test_the_series_block_never_updates_a_run_and_adds_no_object() -> None:
    block = _series_block()
    statements = [s.strip() for s in re.sub(r"--[^\n]*", "", block).split(";") if s.strip()]
    updates = [s for s in statements if s.upper().startswith("UPDATE")]
    assert updates and all("mip_app.growth_agent_monitors" in s for s in updates)
    assert not re.search(r"UPDATE\s+mip_app\.growth_agent_runs", block)
    for forbidden in ("CREATE TABLE", "CREATE TRIGGER", "CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", "CHECK (", "DROP "):
        assert forbidden not in block, forbidden
    # No stored expression calls a function: the only index predicate is IS NOT NULL.
    index = next(s for s in statements if s.startswith("CREATE INDEX"))
    assert index.endswith("WHERE monitor_id IS NOT NULL")
    assert "ADD COLUMN IF NOT EXISTS monitor_id UUID" in block
    assert "ADD COLUMN IF NOT EXISTS seed_run_id UUID" in block


def test_the_run_ledger_trigger_contract_is_byte_identical() -> None:
    assert _SCHEMA.count(_FINALIZE_ONLY_TRIGGERS) == 1
    assert _SCHEMA.count(_FINALIZE_ONLY_FUNCTION) == 1
    assert _SCHEMA.index(_FINALIZE_ONLY_TRIGGERS) < _SCHEMA.index(_MARKER)


# --------------------------------------------------------------------------
# Write path: every watchlist-driven run is INSERTed with its monitor_id.
# --------------------------------------------------------------------------

ACTOR = "operator@example.com"
ADMIN_HEADERS = {"X-Forwarded-Email": "admin@example.com", "X-Forwarded-Groups": "mip-admin"}


def _monitor_row(*, actor: str = ACTOR, name: str = "IL Refi Watch", stale: bool = False) -> dict[str, Any]:
    updated = datetime.now(UTC) - timedelta(days=2 if stale else 0)
    return {
        "monitor_id": uuid4(),
        "actor_email": actor,
        "workflow_id": "daily_refi_brief",
        "name": name,
        "cadence": "daily",
        "status": "active",
        "criteria": {
            "states": ["IL"],
            "lead_queue_filters": {
                "segment_codes": ["itm"],
                "segment_mode": "any",
                "states": ["IL"],
                "portfolio_criteria": {"marketing_eligibility": "Eligible only", "states": ["IL"]},
            },
            "marketing_eligibility": "Eligible only",
            "workflow_id": "daily_refi_brief",
        },
        "route": "/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL",
        "actionable_total": 1,
        "source_assets": ["mip.gold.borrower_360"],
        "last_run_id": uuid4(),
        "seed_run_id": None,
        "created_at": updated - timedelta(days=1),
        "updated_at": updated,
    }


def _post(
    lakebase: _FakeLakebaseClient, path: str, body: dict[str, Any], headers: dict[str, str] | None = None
) -> Any:
    client = _client(_FakeSqlClient(), lakebase)
    try:
        return client.post(path, json=body, headers=headers or {"X-Forwarded-Email": ACTOR})
    finally:
        _clear_overrides()


def _inserted_monitor_ids(lakebase: _FakeLakebaseClient) -> list[Any]:
    return [
        params.get("monitor_id")
        for sql, params in lakebase.executes
        if "INSERT INTO mip_app.growth_agent_runs" in sql
    ]


def test_run_now_inserts_the_run_with_its_monitor_id() -> None:
    lakebase = _FakeLakebaseClient()
    monitor = _monitor_row()
    lakebase.monitors.append(monitor)
    response = _post(lakebase, f"/api/growth-agent/monitors/{monitor['monitor_id']}/run", {})
    assert response.status_code == 200, response.text
    assert _inserted_monitor_ids(lakebase) == [str(monitor["monitor_id"])]


def test_run_due_inserts_each_run_with_its_monitor_id() -> None:
    lakebase = _FakeLakebaseClient()
    monitor = _monitor_row(stale=True)
    lakebase.monitors.append(monitor)
    response = _post(lakebase, "/api/growth-agent/monitors/run-due", {"channels": ["slack"]})
    assert response.status_code == 200, response.text
    assert _inserted_monitor_ids(lakebase) == [str(monitor["monitor_id"])]


def test_run_due_all_inserts_each_owners_run_with_its_monitor_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rbac_module.settings, "admin_emails", "")
    monkeypatch.setattr(rbac_module.settings, "admin_group_name", "mip-admin")
    lakebase = _FakeLakebaseClient()
    monitors = [_monitor_row(actor=actor, stale=True) for actor in ("owner-a@example.com", "owner-b@example.com")]
    lakebase.monitors.extend(monitors)
    response = _post(lakebase, "/api/growth-agent/monitors/run-due-all", {"channels": ["slack"]}, ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    assert sorted(_inserted_monitor_ids(lakebase)) == sorted(str(row["monitor_id"]) for row in monitors)


def test_a_resave_of_an_existing_name_joins_that_watchlists_series() -> None:
    lakebase = _FakeLakebaseClient()
    monitor = _monitor_row()
    lakebase.monitors.append(monitor)
    response = _post(
        lakebase,
        "/api/growth-agent/workflows/daily_refi_brief/run",
        {"states": ["IL"], "save_monitor": True, "monitor_name": "IL Refi Watch"},
    )
    assert response.status_code == 200, response.text
    assert _inserted_monitor_ids(lakebase) == [str(monitor["monitor_id"])]
    assert len(lakebase.monitors) == 1


def test_another_actors_watchlist_of_the_same_name_never_tags_the_run() -> None:
    lakebase = _FakeLakebaseClient()
    foreign = _monitor_row(actor="someone.else@example.com")
    lakebase.monitors.append(foreign)
    response = _post(
        lakebase,
        "/api/growth-agent/workflows/daily_refi_brief/run",
        {"states": ["IL"], "save_monitor": True, "monitor_name": "IL Refi Watch"},
    )
    assert response.status_code == 200, response.text
    # The caller's first save of that name starts its own series (untagged seed).
    assert _inserted_monitor_ids(lakebase) == [None]
    assert [row["actor_email"] for row in lakebase.monitors] == ["someone.else@example.com", ACTOR]


def test_a_new_watchlists_first_run_is_untagged_and_becomes_its_seed() -> None:
    lakebase = _FakeLakebaseClient()
    response = _post(
        lakebase,
        "/api/growth-agent/workflows/daily_refi_brief/run",
        {"states": ["IL"], "save_monitor": True, "monitor_name": "IL Refi Watch"},
    )
    assert response.status_code == 200, response.text
    assert _inserted_monitor_ids(lakebase) == [None]
    [monitor] = lakebase.monitors
    assert monitor["seed_run_id"] == lakebase.runs[0]["run_id"] == monitor["last_run_id"]
    upsert_sql = next(sql for sql, _ in lakebase.executes if "INSERT INTO mip_app.growth_agent_monitors" in sql)
    assert "seed_run_id = COALESCE(mip_app.growth_agent_monitors.seed_run_id, EXCLUDED.seed_run_id)" in upsert_sql


def test_a_one_off_run_is_untagged_and_never_looks_up_a_watchlist() -> None:
    lakebase = _FakeLakebaseClient()
    lakebase.monitors.append(_monitor_row(name="Daily Refi Opportunity Brief"))
    response = _post(lakebase, "/api/growth-agent/workflows/daily_refi_brief/run", {"states": ["IL"]})
    assert response.status_code == 200, response.text
    assert _inserted_monitor_ids(lakebase) == [None]
    assert not any("SELECT monitor_id\nFROM" in sql for sql, _ in lakebase.executes)
