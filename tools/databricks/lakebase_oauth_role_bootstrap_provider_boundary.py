"""Exact provider-call and pre-commit boundaries for one-use Lakebase bootstrap."""

from __future__ import annotations

from typing import Any


def assert_provider_boundary_contract(
    client: Any,
    deployer_cursor: Any,
    bootstrap_cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    bootstrap_application_id: str,
    bootstrap_service_principal_id: str,
    bootstrap_external_id: str,
    expected_executor: str,
    expected_function_fingerprint: Any,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
    require_target_absence: bool,
) -> None:
    """Reprove all immutable inputs around the sole provider transaction."""

    from tools.databricks.lakebase_oauth_role_bootstrap import (
        _assert_role_function_contract,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
        assert_bootstrap_lock_held,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_target import (
        prove_target_absent,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_wrapper import (
        _event_trigger_preflight,
        assert_wrapper_contract,
    )
    from tools.databricks.lakebase_oauth_role_recovery import (
        _assert_bootstrap_role_contract,
    )

    assert_bootstrap_lock_held(bootstrap_lock_cursor, lock_key=bootstrap_lock_key)
    if not _assert_bootstrap_role_contract(
        client,
        deployer_cursor,
        instance_name=instance_name,
        database_name=database_name,
        application_id=bootstrap_application_id,
        target_application_id=target_application_id,
        external_id=bootstrap_external_id,
        service_principal_id=bootstrap_service_principal_id,
        expected_executor=expected_executor,
        expected_privileges=frozenset({"USAGE", "EXECUTE"}),
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        signed_tombstone_authority=True,
    ):
        boundary = "invoke" if require_target_absence else "commit"
        raise RuntimeError(
            f"temporary Lakebase bootstrap role disappeared before {boundary}"
        )
    _assert_role_function_contract(bootstrap_cursor)
    assert_wrapper_contract(
        bootstrap_cursor,
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=target_application_id,
        bootstrap_application_id=bootstrap_application_id,
        expected_executor=bootstrap_application_id,
        expected_privileges=frozenset({"USAGE", "EXECUTE"}),
        expected_function_fingerprint=expected_function_fingerprint,
    )
    _event_trigger_preflight(
        bootstrap_cursor,
        principal_label=(
            "target-bound bootstrap wrapper invocation"
            if require_target_absence
            else "target-bound bootstrap wrapper commit"
        ),
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    if require_target_absence:
        prove_target_absent(
            client,
            deployer_cursor,
            instance_name=instance_name,
            application_id=target_application_id,
            expected_executor=expected_executor,
        )
