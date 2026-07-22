"""Canonical-lock recovery entrypoint for Lakebase OAuth bootstrap identities."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import psycopg


def _recover_locked(
    client: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    application_id: str,
    connect: Callable[..., Any],
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
    expected_executor: str,
    reappearance_retries: int = 3,
) -> None:
    from jobs.lakebase_migration_schema_hooks import (
        _postflight_event_trigger_inventory,
    )
    from tools.databricks import converge_lakebase_oauth_role as core
    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
        assert_bootstrap_lock_held,
    )
    from tools.databricks.lakebase_oauth_role_recovery import (
        recover_stale_bootstrap_identities,
    )
    from tools.databricks.lakebase_oauth_role_recovery_admin import (
        TargetDatabaseReappearedError,
        assert_admin_database_target_absent,
    )

    assert_bootstrap_lock_held(bootstrap_lock_cursor, lock_key=bootstrap_lock_key)
    target_kwargs, accepted_users = core._connection_kwargs(
        client,
        instance_name=instance_name,
        database_name=database_name,
    )
    target_absence = 0
    for attempt in range(3):
        try:
            with connect(**target_kwargs) as connection, connection.cursor() as cursor:
                if core._assert_connection_identity(cursor, accepted_users) != expected_executor:
                    raise RuntimeError("Lakebase recovery executor identity changed")
                assert_bootstrap_lock_held(
                    bootstrap_lock_cursor,
                    lock_key=bootstrap_lock_key,
                )
                _postflight_event_trigger_inventory(
                    cursor,
                    application_id,
                    principal_label="OAuth bootstrap recovery preflight",
                )
                recover_stale_bootstrap_identities(
                    client,
                    cursor,
                    account_client=account_client,
                    instance_name=instance_name,
                    database_name=database_name,
                    target_application_id=application_id,
                    bootstrap_lock_cursor=bootstrap_lock_cursor,
                    bootstrap_lock_key=bootstrap_lock_key,
                    expected_executor=expected_executor,
                )
                return
        except Exception as exc:
            if getattr(exc, "sqlstate", None) != "3D000":
                raise
            target_absence += 1
        if attempt + 1 < 3:
            time.sleep(1)
    if target_absence != 3:
        raise RuntimeError("target Lakebase database absence did not converge")

    try:
        for attempt in range(3):
            assert_bootstrap_lock_held(bootstrap_lock_cursor, lock_key=bootstrap_lock_key)
            assert_admin_database_target_absent(bootstrap_lock_cursor, database_name)
            if attempt < 2:
                time.sleep(1)
        _postflight_event_trigger_inventory(
            bootstrap_lock_cursor,
            application_id,
            principal_label="OAuth bootstrap admin-database recovery preflight",
        )
        recover_stale_bootstrap_identities(
            client,
            bootstrap_lock_cursor,
            account_client=account_client,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=application_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            expected_executor=expected_executor,
            admin_database_recovery=True,
        )
    except TargetDatabaseReappearedError:
        if reappearance_retries <= 0:
            raise RuntimeError(
                "target Lakebase database kept reappearing during admin recovery"
            ) from None
        _recover_locked(
            client,
            account_client=account_client,
            instance_name=instance_name,
            database_name=database_name,
            application_id=application_id,
            connect=connect,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            expected_executor=expected_executor,
            reappearance_retries=reappearance_retries - 1,
        )


def recover_role_bootstrap(
    client: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    application_id: str,
    connect: Callable[..., Any] = psycopg.connect,
) -> None:
    """Recover under the canonical instance-wide target lock."""

    from tools.databricks import converge_lakebase_oauth_role as core
    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
        acquire_bootstrap_lock,
        release_bootstrap_lock,
    )
    from tools.databricks.lakebase_oauth_role_recovery import (
        recover_bootstrap_principals_for_absent_instance,
    )

    application_id = application_id.strip()
    if not application_id:
        raise ValueError("application_id is required")
    if recover_bootstrap_principals_for_absent_instance(
        client,
        account_client=account_client,
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=application_id,
    ):
        return

    admin_kwargs, accepted_users = core._connection_kwargs(
        client,
        instance_name=instance_name,
        database_name="databricks_postgres",
    )
    with connect(**admin_kwargs) as lock_connection, lock_connection.cursor() as lock_cursor:
        expected_executor = core._assert_connection_identity(lock_cursor, accepted_users)
        lock_key = acquire_bootstrap_lock(
            lock_cursor,
            instance_name=instance_name,
            target_application_id=application_id,
        )
        try:
            _recover_locked(
                client,
                account_client=account_client,
                instance_name=instance_name,
                database_name=database_name,
                application_id=application_id,
                connect=connect,
                bootstrap_lock_cursor=lock_cursor,
                bootstrap_lock_key=lock_key,
                expected_executor=expected_executor,
            )
        finally:
            release_bootstrap_lock(lock_cursor, lock_key=lock_key)
