"""Fresh read-only classifier for commit-ambiguous target creation."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal

from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
    assert_bootstrap_lock_held,
)
from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
    cleanup_executor_identity,
)
from tools.databricks.lakebase_oauth_role_bootstrap_target import (
    assert_residual_target_contract,
    prove_target_absent,
    quarantine_residual_target_identity,
)

ResidualState = Literal["exact", "absent", "indeterminate"]


def classify_residual_target(
    client: Any,
    *,
    connect: Callable[..., Any],
    instance_name: str,
    database_name: str,
    application_id: str,
    service_principal_id: str,
    allowed_creator_roles: frozenset[str],
    expected_executor: str,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
    attempts: int = 7,
    required_observations: int = 3,
) -> ResidualState:
    """Classify only stable exact success or stable cross-plane absence."""

    from tools.databricks.lakebase_oauth_role_bootstrap import (
        _connection_kwargs,
        _workspace_database_identity,
    )

    stable_state: ResidualState = "indeterminate"
    stable_count = 0
    for attempt in range(attempts):
        observed: ResidualState = "indeterminate"
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
                    excluded_application_id=application_id,
                    expected_executor=expected_executor,
                )
                if executor not in accepted_users:
                    raise RuntimeError(
                        "fresh Lakebase reconciliation authenticated as the wrong identity"
                    )
                assert_bootstrap_lock_held(
                    bootstrap_lock_cursor,
                    lock_key=bootstrap_lock_key,
                )
                try:
                    assert_residual_target_contract(
                        client,
                        cursor,
                        instance_name=instance_name,
                        application_id=application_id,
                        service_principal_id=service_principal_id,
                        allowed_creator_roles=allowed_creator_roles,
                        expected_executor=expected_executor,
                        terminate_sessions=False,
                    )
                except Exception:
                    try:
                        prove_target_absent(
                            client,
                            cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                            expected_executor=expected_executor,
                            attempts=1,
                            required_absence=1,
                        )
                    except Exception:
                        observed = "indeterminate"
                    else:
                        observed = "absent"
                else:
                    observed = "exact"
                    if stable_state == "exact" and stable_count + 1 >= required_observations:
                        assert_residual_target_contract(
                            client,
                            cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                            service_principal_id=service_principal_id,
                            allowed_creator_roles=allowed_creator_roles,
                            expected_executor=expected_executor,
                        )
        except Exception:
            observed = "indeterminate"

        if observed in {"exact", "absent"} and observed == stable_state:
            stable_count += 1
        elif observed in {"exact", "absent"}:
            stable_state = observed
            stable_count = 1
        else:
            stable_state = "indeterminate"
            stable_count = 0
        if stable_count >= required_observations:
            return stable_state
        if attempt + 1 < attempts:
            time.sleep(1)
    return "indeterminate"


def quarantine_indeterminate_target(
    client: Any,
    *,
    connect: Callable[..., Any],
    instance_name: str,
    database_name: str,
    application_id: str,
    service_principal_id: str,
    expected_executor: str,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> None:
    """Best-effort fail-closed fence after classification remains ambiguous."""

    from tools.databricks.lakebase_oauth_role_bootstrap import (
        _connection_kwargs,
        _workspace_database_identity,
    )

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
            excluded_application_id=application_id,
            expected_executor=expected_executor,
        )
        if executor not in accepted_users:
            raise RuntimeError("fresh Lakebase quarantine authenticated as the wrong identity")
        quarantine_residual_target_identity(
            client,
            cursor,
            instance_name=instance_name,
            application_id=application_id,
            service_principal_id=service_principal_id,
            expected_executor=expected_executor,
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
