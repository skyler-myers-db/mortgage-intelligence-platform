"""Fresh-connection finalization for one-use Lakebase bootstrap identities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def finalize_bootstrap_identity(
    client: Any,
    *,
    account_client: Any,
    connect: Callable[..., Any],
    instance_name: str,
    database_name: str,
    target_application_id: str,
    bootstrap_application_id: str,
    bootstrap_scim_id: str,
    bootstrap_display_name: str,
    bootstrap_external_id: str,
    expected_executor: str,
    retain_evidence: bool,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> list[str]:
    """Recover or quarantine through a new deployer connection under the lock."""

    from tools.databricks.lakebase_oauth_role_bootstrap import (
        _connection_kwargs,
        _workspace_database_identity,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_credentials import (
        emergency_quarantine_verified_bootstrap_credentials,
        quarantine_bootstrap_identity,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
        assert_bootstrap_lock_held,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
        cleanup_executor_identity,
    )
    from tools.databricks.lakebase_oauth_role_recovery import (
        recover_stale_bootstrap_identities,
    )

    errors: list[str] = []
    try:
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
    except Exception as exc:
        retain_evidence = True
        errors.append(f"bootstrap advisory lock: {type(exc).__name__}: {exc}")

    if errors:
        if bootstrap_scim_id:
            try:
                emergency_quarantine_verified_bootstrap_credentials(
                    client,
                    account_client=account_client,
                    service_principal_id=bootstrap_scim_id,
                    application_id=bootstrap_application_id,
                    display_name=bootstrap_display_name,
                    external_id=bootstrap_external_id,
                )
            except Exception as exc:
                errors.append(f"bootstrap credential quarantine: {type(exc).__name__}: {exc}")
        return errors

    try:
        database_user, accepted_users = _workspace_database_identity(client)
        kwargs = _connection_kwargs(
            client,
            instance_name=instance_name,
            database_name=database_name,
            database_user=database_user,
            autocommit=True,
        )
        with connect(**kwargs) as connection, connection.cursor() as cursor:
            executor = cleanup_executor_identity(
                cursor,
                excluded_application_id=bootstrap_application_id,
                expected_executor=expected_executor,
            )
            if executor not in accepted_users:
                raise RuntimeError(
                    "fresh Lakebase bootstrap cleanup authenticated as the wrong identity"
                )
            assert_bootstrap_lock_held(
                bootstrap_lock_cursor,
                lock_key=bootstrap_lock_key,
            )
            if retain_evidence and bootstrap_application_id and bootstrap_scim_id:
                quarantine_bootstrap_identity(
                    client,
                    cursor,
                    service_principal_id=bootstrap_scim_id,
                    application_id=bootstrap_application_id,
                    display_name=bootstrap_display_name,
                    expected_executor=expected_executor,
                    instance_name=instance_name,
                    database_name=database_name,
                    target_application_id=target_application_id,
                    external_id=bootstrap_external_id,
                    allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                    bootstrap_lock_cursor=bootstrap_lock_cursor,
                    bootstrap_lock_key=bootstrap_lock_key,
                )
            else:
                recover_stale_bootstrap_identities(
                    client,
                    cursor,
                    account_client=account_client,
                    instance_name=instance_name,
                    database_name=database_name,
                    target_application_id=target_application_id,
                    allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                    bootstrap_lock_cursor=bootstrap_lock_cursor,
                    bootstrap_lock_key=bootstrap_lock_key,
                    expected_executor=expected_executor,
                )
    except Exception as exc:  # noqa: BLE001 - preserve residue and original failure
        errors.append(f"fresh bootstrap finalization: {type(exc).__name__}: {exc}")
        try:
            emergency_quarantine_verified_bootstrap_credentials(
                client,
                account_client=account_client,
                service_principal_id=bootstrap_scim_id,
                application_id=bootstrap_application_id,
                display_name=bootstrap_display_name,
                external_id=bootstrap_external_id,
            )
        except Exception as quarantine_error:
            errors.append(
                "bootstrap credential quarantine: "
                f"{type(quarantine_error).__name__}: {quarantine_error}"
            )
    return errors
