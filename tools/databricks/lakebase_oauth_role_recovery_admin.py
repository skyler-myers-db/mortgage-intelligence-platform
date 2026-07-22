"""Canonical admin-database guards for absent-target recovery."""

from __future__ import annotations

from typing import Any

from tools.databricks.lakebase_oauth_role_bootstrap import read_profile
from tools.databricks.lakebase_oauth_role_bootstrap_contract import _control_plane_role


class TargetDatabaseReappearedError(RuntimeError):
    """The target database returned while admin-database recovery held the lock."""


def assert_admin_database_target_absent(cursor: Any, database_name: str) -> None:
    cursor.execute("SELECT current_database()")
    if cursor.fetchone() != ("databricks_postgres",):
        raise RuntimeError("Lakebase bootstrap admin recovery is on the wrong database")
    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
    if cursor.fetchone() is not None:
        raise TargetDatabaseReappearedError(
            "target Lakebase database reappeared during admin recovery"
        )


def role_exists_on_either_plane(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    application_id: str,
) -> bool:
    return read_profile(cursor, application_id) is not None or (
        _control_plane_role(
            client,
            instance_name=instance_name,
            application_id=application_id,
        )
        is not None
    )
