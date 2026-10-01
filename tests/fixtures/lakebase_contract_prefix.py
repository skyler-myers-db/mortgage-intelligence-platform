"""The migration contract as it stood for an older prefix of lakebase/schema.sql.

Upgrade suites replay "the schema before block X" and then the full schema.
The postflight inside ``lakebase_migrate._run_transaction`` compares the live
trigger and routine inventory with the CURRENT contract, so a later block
that adds a trigger or a routine (2026_10_01_approval_requests was the first
post-seed block to do so) made every such prefix fail an inventory it never
had. A real older deploy ran with its own, smaller contract; this context
manager reproduces that by hiding, for the duration of one apply, exactly the
contract entries whose object the given SQL never creates. The full schema
hides nothing, so the production contract is still what every full apply is
checked against.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from jobs.lakebase_migration_contracts import (
    _APP_ROLE_ROUTINE_PRIVILEGES,
    _APP_TRIGGER_CONTRACT,
)


@contextmanager
def contract_as_of(schema_sql: str) -> Iterator[None]:
    """Hide the trigger and routine entries ``schema_sql`` does not create."""

    hidden_triggers = {
        key: value
        for key, value in _APP_TRIGGER_CONTRACT.items()
        if f"CREATE TRIGGER {key[2]}\n" not in schema_sql
    }
    hidden_routines = {
        key: value
        for key, value in _APP_ROLE_ROUTINE_PRIVILEGES.items()
        if f"FUNCTION mip_app.{key[0]}(" not in schema_sql
    }
    for trigger in hidden_triggers:
        del _APP_TRIGGER_CONTRACT[trigger]
    for routine in hidden_routines:
        del _APP_ROLE_ROUTINE_PRIVILEGES[routine]
    try:
        yield
    finally:
        _APP_TRIGGER_CONTRACT.update(hidden_triggers)
        _APP_ROLE_ROUTINE_PRIVILEGES.update(hidden_routines)
