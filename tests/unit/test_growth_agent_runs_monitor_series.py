"""Growth Agent watchlist run series (audit 2026-09-21 ``wow-ai-4`` backend).

The 2026_10_01 block tags each watchlist-driven run with its ``monitor_id`` at
INSERT and records a new watchlist's first run as the monitor's
``seed_run_id``. These static pins keep the block additive and the run
ledger append-only; the real-Postgres re-run proof is
``tests/integration/test_growth_agent_runs_postgres.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

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
    return _SCHEMA[_SCHEMA.index(_MARKER):]


def test_the_series_block_is_the_last_block_and_is_versioned_once() -> None:
    block = _series_block()
    assert _SCHEMA.count(_MARKER) == 1
    assert block.count("INSERT INTO mip_app.schema_migrations") == 1
    assert "'2026_10_01_growth_agent_watchlist_series'" in block
    assert block.rstrip().endswith("ON CONFLICT (version) DO NOTHING;")


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
