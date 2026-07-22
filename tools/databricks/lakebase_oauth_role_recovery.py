"""Deterministic recovery for one-use privileged Lakebase bootstrap identities."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from tools.databricks.lakebase_oauth_role_bootstrap import read_profile
from tools.databricks.lakebase_oauth_role_bootstrap_admission import (
    fence_bootstrap_role_admission,
)
from tools.databricks.lakebase_oauth_role_bootstrap_contract import (
    _assert_bootstrap_role_contract,
    _assert_no_bootstrap_acl_dependencies,
    _control_plane_role,
    bootstrap_oauth_label_service_principal_id,
)
from tools.databricks.lakebase_oauth_role_bootstrap_credentials import (
    assert_workspace_mutation_lease as _assert_workspace_mutation_lease,
)
from tools.databricks.lakebase_oauth_role_bootstrap_legacy_acl import (
    cleanup_legacy_acl_dependencies,
)
from tools.databricks.lakebase_oauth_role_bootstrap_principal import (
    assert_bootstrap_principal_contract as _assert_bootstrap_principal_contract,
)
from tools.databricks.lakebase_oauth_role_bootstrap_principal import (
    exact_bootstrap_principals as _exact_bootstrap_principals,
)
from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
    SessionFence,
    cleanup_executor_identity,
    drain_post_delete_sessions,
    prove_post_delete_session_absence,
    terminate_bootstrap_sessions,
)
from tools.databricks.lakebase_oauth_role_bootstrap_wrapper import (
    _event_trigger_preflight,
    cleanup_wrapper,
)
from tools.databricks.lakebase_oauth_role_recovery_absent import (
    commented_bootstrap_roles as _commented_bootstrap_roles,
)
from tools.databricks.lakebase_oauth_role_recovery_admin import (
    TargetDatabaseReappearedError,
    assert_admin_database_target_absent,
    role_exists_on_either_plane,
)
from tools.databricks.lakebase_oauth_role_recovery_identity import (
    AccountPrincipalCleanupError,
    CredentialCleanupError,
    prove_deleted_bootstrap_principal_absent,
    revoke_credentials_and_retire_account_principal,
)
from tools.databricks.lakebase_oauth_role_recovery_marker import (
    bootstrap_identity_contract as _bootstrap_identity_contract,
)
from tools.databricks.lakebase_oauth_role_recovery_marker import (
    marker_signing_key as _marker_signing_key,
)
from tools.databricks.lakebase_oauth_role_tombstone import (
    delete_orphan_tombstone as _delete_orphan_tombstone,
)
from tools.databricks.lakebase_oauth_role_tombstone import (
    orphan_tombstones as _orphan_tombstones,
)
from tools.databricks.lakebase_oauth_role_tombstone import (
    upgrade_v2_orphan_tombstone as _upgrade_v2_orphan_tombstone,
)
from tools.databricks.lakebase_oauth_role_tombstone_migration import (
    migrate_v2_tombstones_before_role_cleanup,
)


def _fence_and_drain_bootstrap_role(
    client: Any,
    deployer_cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    application_id: str,
    display_name: str,
    target_application_id: str,
    external_id: str,
    service_principal_id: str | None,
    expected_executor: str,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    allow_unlocked_recovery_for_tests: bool,
    signed_tombstone_authority: bool = False,
) -> SessionFence:
    """Prove immutable authority, then drain sessions for a provider-owned role."""

    sql_present = read_profile(deployer_cursor, application_id) is not None
    if sql_present:
        fence_bootstrap_role_admission(
            client,
            deployer_cursor,
            instance_name=instance_name,
            database_name=database_name,
            application_id=application_id,
            display_name=display_name,
            target_application_id=target_application_id,
            external_id=external_id,
            service_principal_id=service_principal_id,
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
            signed_tombstone_authority=signed_tombstone_authority,
        )
    fence = terminate_bootstrap_sessions(
        deployer_cursor,
        application_id=application_id,
        expected_executor=expected_executor,
    )
    return fence


def _delete_bootstrap_role(
    client: Any,
    deployer_cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    application_id: str,
    display_name: str,
    target_application_id: str,
    external_id: str,
    service_principal_id: str | None,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    allow_unlocked_recovery_for_tests: bool,
    expected_executor: str,
    admin_database_recovery: bool,
    signed_tombstone_authority: bool = False,
    attempts: int = 15,
) -> None:
    """Delete a possibly ambiguous role creation and prove stable absence."""

    absent_observations = 0
    last_error: Exception | None = None
    last_delete_fence: SessionFence | None = None
    for attempt in range(attempts):
        sql_present = read_profile(deployer_cursor, application_id) is not None
        present = sql_present
        try:
            present = (
                present
                or _control_plane_role(
                    client,
                    instance_name=instance_name,
                    application_id=application_id,
                )
                is not None
            )
        except Exception as exc:  # noqa: BLE001 - absence must remain conclusive
            last_error = exc
            present = True
        if present:
            absent_observations = 0
            try:
                if sql_present:
                    _fence_and_drain_bootstrap_role(
                        client,
                        deployer_cursor,
                        instance_name=instance_name,
                        database_name=database_name,
                        application_id=application_id,
                        display_name=display_name,
                        target_application_id=target_application_id,
                        external_id=external_id,
                        service_principal_id=service_principal_id,
                        expected_executor=expected_executor,
                        allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                        bootstrap_lock_cursor=bootstrap_lock_cursor,
                        bootstrap_lock_key=bootstrap_lock_key,
                        allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                        signed_tombstone_authority=signed_tombstone_authority,
                    )
                else:
                    terminate_bootstrap_sessions(
                        deployer_cursor,
                        application_id=application_id,
                        expected_executor=expected_executor,
                    )
                if not _assert_bootstrap_role_contract(
                    client,
                    deployer_cursor,
                    instance_name=instance_name,
                    database_name=database_name,
                    application_id=application_id,
                    target_application_id=target_application_id,
                    external_id=external_id,
                    service_principal_id=service_principal_id,
                    expected_executor=expected_executor,
                    allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                    target_database_absent=admin_database_recovery,
                    signed_tombstone_authority=signed_tombstone_authority,
                ):
                    raise RuntimeError(
                        "temporary Lakebase bootstrap role disappeared before deletion"
                    )
                if sql_present:
                    if admin_database_recovery:
                        _assert_no_bootstrap_acl_dependencies(
                            deployer_cursor,
                            application_id=application_id,
                        )
                    else:
                        cleanup_wrapper(
                            deployer_cursor,
                            instance_name=instance_name,
                            database_name=database_name,
                            target_application_id=target_application_id,
                            bootstrap_application_id=application_id,
                            expected_executor=expected_executor,
                            allow_absent_managed_event_triggers=(
                                allow_absent_managed_event_triggers
                            ),
                            bootstrap_lock_cursor=bootstrap_lock_cursor,
                            bootstrap_lock_key=bootstrap_lock_key,
                            allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                        )
                        cleanup_legacy_acl_dependencies(
                            deployer_cursor,
                            database_name=database_name,
                            application_id=application_id,
                            expected_executor=expected_executor,
                            allow_absent_managed_event_triggers=(
                                allow_absent_managed_event_triggers
                            ),
                        )
                    last_delete_fence = terminate_bootstrap_sessions(
                        deployer_cursor,
                        application_id=application_id,
                        expected_executor=expected_executor,
                    )
                else:
                    last_delete_fence = terminate_bootstrap_sessions(
                        deployer_cursor,
                        application_id=application_id,
                        expected_executor=expected_executor,
                    )
                _event_trigger_preflight(
                    deployer_cursor,
                    principal_label="bootstrap role DROP",
                    allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                )
                if bootstrap_lock_cursor is not None and bootstrap_lock_key is not None:
                    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
                        assert_bootstrap_lock_held,
                    )

                    assert_bootstrap_lock_held(
                        bootstrap_lock_cursor,
                        lock_key=bootstrap_lock_key,
                    )
                elif not allow_unlocked_recovery_for_tests:
                    raise RuntimeError(
                        "temporary Lakebase bootstrap DROP lacks canonical advisory lock"
                    )
                if admin_database_recovery:
                    assert_admin_database_target_absent(
                        deployer_cursor,
                        database_name,
                    )
                client.database.delete_database_instance_role(instance_name, application_id)
                last_delete_fence = drain_post_delete_sessions(
                    deployer_cursor,
                    application_id=application_id,
                    fence=last_delete_fence,
                )
                prove_post_delete_session_absence(
                    deployer_cursor,
                    application_id=application_id,
                    fence=last_delete_fence,
                )
                last_error = None
            except TargetDatabaseReappearedError:
                raise
            except Exception as exc:  # noqa: BLE001 - retry ambiguous deletion
                last_error = exc
        else:
            if last_delete_fence is not None:
                prove_post_delete_session_absence(
                    deployer_cursor,
                    application_id=application_id,
                    fence=last_delete_fence,
                )
            absent_observations += 1
            if absent_observations >= 3:
                return
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error is not None else ""
    raise RuntimeError(f"temporary Lakebase bootstrap role cleanup did not converge{detail}")


def recover_bootstrap_principals_for_absent_instance(
    client: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    marker_signing_key: str | None = None,
    resource_absence_probe: Callable[[], bool] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> bool:
    from tools.databricks.lakebase_oauth_role_recovery_absent import (
        recover_absent_instance_principals,
    )

    return recover_absent_instance_principals(
        client,
        account_client=account_client,
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=target_application_id,
        marker_signing_key=marker_signing_key,
        resource_absence_probe=resource_absence_probe,
        monotonic=monotonic,
        sleep=sleep,
    )


def recover_stale_bootstrap_identities(
    client: Any,
    deployer_cursor: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    marker_signing_key: str | None = None,
    attempts: int = 15,
    allow_absent_managed_event_triggers: bool = False,
    bootstrap_lock_cursor: Any | None = None,
    bootstrap_lock_key: Any | None = None,
    allow_unlocked_recovery_for_tests: bool = False,
    expected_executor: str | None = None,
    admin_database_recovery: bool = False,
) -> None:
    """Recover deterministic one-use creators left by interruption or ambiguity."""

    display_name, external_id = _bootstrap_identity_contract(
        instance_name=instance_name,
        database_name=database_name,
        application_id=target_application_id,
    )
    if (bootstrap_lock_cursor is None) != (bootstrap_lock_key is None):
        raise ValueError("bootstrap lock cursor and key must be supplied together")
    expected_executor = cleanup_executor_identity(
        deployer_cursor,
        excluded_application_id=target_application_id,
        expected_executor=expected_executor,
    )
    marker_signing_key = marker_signing_key or _marker_signing_key()
    required_absence = 3
    absence_observations = 0
    for attempt in range(attempts):
        if admin_database_recovery:
            assert_admin_database_target_absent(deployer_cursor, database_name)
        principals = _exact_bootstrap_principals(
            client,
            display_name=display_name,
            external_id=external_id,
            account_client=account_client,
        )

        resolved_principals = [
            _assert_bootstrap_principal_contract(
                client,
                principal,
                display_name=display_name,
                external_id=external_id,
                account_client=account_client,
            )
            for principal in principals
        ]
        if any(
            application_id == target_application_id for _, application_id in resolved_principals
        ):
            raise RuntimeError("target runtime identity is never a bootstrap principal")
        principal_states: list[tuple[str, str, bool, bool, bool, list[str]]] = []
        for principal_id, application_id in resolved_principals:
            principal_errors: list[str] = []
            credential_cleanup_succeeded = False
            account_cleanup_succeeded = False
            session_cleanup_succeeded = False
            # Keep signed immutable two-id authority durable before the first
            # credential or account-plane write.
            _assert_workspace_mutation_lease(
                bootstrap_lock_cursor,
                bootstrap_lock_key,
                allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
            )
            _upgrade_v2_orphan_tombstone(
                client,
                account_client=account_client,
                base_external_id=external_id,
                application_id=application_id,
                principal_id=principal_id,
                signing_key=str(marker_signing_key or ""),
                bootstrap_lock_cursor=bootstrap_lock_cursor,
                bootstrap_lock_key=bootstrap_lock_key,
                allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
            )
            try:
                revoke_credentials_and_retire_account_principal(
                    client,
                    account_client,
                    principal_id=principal_id,
                    application_id=application_id,
                    bootstrap_reservation_name=display_name,
                    ownership_marker=external_id,
                    allow_workspace_absence=True,
                    bootstrap_lock_cursor=bootstrap_lock_cursor,
                    bootstrap_lock_key=bootstrap_lock_key,
                    allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                )
                credential_cleanup_succeeded = True
                account_cleanup_succeeded = True
            except CredentialCleanupError as exc:
                principal_errors.append(f"credential cleanup: {type(exc).__name__}: {exc}")
            except AccountPrincipalCleanupError as exc:
                credential_cleanup_succeeded = True
                principal_errors.append(f"account principal cleanup: {type(exc).__name__}: {exc}")
            try:
                _fence_and_drain_bootstrap_role(
                    client,
                    deployer_cursor,
                    instance_name=instance_name,
                    database_name=database_name,
                    application_id=application_id,
                    display_name=display_name,
                    target_application_id=target_application_id,
                    external_id=external_id,
                    service_principal_id=principal_id,
                    expected_executor=expected_executor,
                    allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                    bootstrap_lock_cursor=bootstrap_lock_cursor,
                    bootstrap_lock_key=bootstrap_lock_key,
                    allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                    signed_tombstone_authority=True,
                )
                session_cleanup_succeeded = True
            except Exception as exc:  # noqa: BLE001 - retain quarantined marker
                principal_errors.append(f"session cleanup: {type(exc).__name__}: {exc}")
            principal_states.append(
                (
                    principal_id,
                    application_id,
                    credential_cleanup_succeeded,
                    account_cleanup_succeeded,
                    session_cleanup_succeeded,
                    principal_errors,
                )
            )

        inventory_errors: list[str] = []
        try:
            orphan_tombstones = _orphan_tombstones(
                client,
                base_external_id=external_id,
                account_client=account_client,
            )
        except Exception as exc:  # noqa: BLE001 - principals still require cleanup
            orphan_tombstones = []
            inventory_errors.append(f"orphan marker inventory: {type(exc).__name__}: {exc}")
        try:
            commented_roles = _commented_bootstrap_roles(deployer_cursor, external_id)
        except Exception as exc:  # noqa: BLE001 - principals still require cleanup
            commented_roles = []
            inventory_errors.append(f"database marker inventory: {type(exc).__name__}: {exc}")

        if orphan_tombstones and not inventory_errors:
            orphan_tombstones = migrate_v2_tombstones_before_role_cleanup(
                client,
                deployer_cursor,
                account_client,
                orphan_tombstones,
                external_id,
                display_name,
                str(marker_signing_key or ""),
                {state[1]: state[0] for state in principal_states},
                bootstrap_lock_cursor,
                bootstrap_lock_key,
                allow_unlocked_recovery_for_tests,
            )

        if (
            not principals
            and not orphan_tombstones
            and not commented_roles
            and not inventory_errors
        ):
            if not admin_database_recovery:
                cleanup_wrapper(
                    deployer_cursor,
                    instance_name=instance_name,
                    database_name=database_name,
                    target_application_id=target_application_id,
                    bootstrap_application_id=None,
                    expected_executor=expected_executor,
                    allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                    bootstrap_lock_cursor=bootstrap_lock_cursor,
                    bootstrap_lock_key=bootstrap_lock_key,
                    allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                )
            absence_observations += 1
            if absence_observations >= required_absence:
                return
        else:
            absence_observations = 0
            handled_roles: set[str] = set()
            cleanup_groups: list[str] = list(inventory_errors)
            for (
                tombstone_id,
                application_id,
                _tombstone_display_name,
                _tombstone_external_id,
                tombstone_principal_id,
            ) in orphan_tombstones:
                if application_id == target_application_id:
                    cleanup_groups.append(
                        "orphan marker contract: target runtime identity is never a "
                        "bootstrap role"
                    )
                    continue
                handled_roles.add(application_id)
                tombstone_errors: list[str] = []
                role_present = False
                role_contract_error: Exception | None = None
                matching_principal = next(
                    (state for state in principal_states if state[1] == application_id),
                    None,
                )
                recovery_principal_id: str | None = (
                    matching_principal[0]
                    if matching_principal is not None
                    else tombstone_principal_id
                )
                identity_conflicted = False
                if (
                    recovery_principal_id is not None
                    and tombstone_principal_id is not None
                    and recovery_principal_id != tombstone_principal_id
                ):
                    tombstone_errors.append(
                        "orphan principal identity: signed tombstone conflicts with exact "
                        "principal inventory"
                    )
                    recovery_principal_id = None
                    identity_conflicted = True
                credential_cleanup_succeeded = bool(
                    matching_principal is not None and matching_principal[2]
                )
                account_cleanup_succeeded = bool(
                    matching_principal is not None and matching_principal[3]
                )
                session_cleanup_succeeded = bool(
                    matching_principal is None or matching_principal[4]
                )
                if (
                    not identity_conflicted
                    and read_profile(deployer_cursor, application_id) is not None
                ):
                    try:
                        label_principal_id = bootstrap_oauth_label_service_principal_id(
                            deployer_cursor,
                            application_id,
                        )
                        if (
                            recovery_principal_id is not None
                            and recovery_principal_id != label_principal_id
                        ):
                            raise RuntimeError(
                                "signed tombstone conflicts with the OAuth role label"
                            )
                        recovery_principal_id = label_principal_id
                    except Exception as exc:  # noqa: BLE001 - retain conflicting evidence
                        tombstone_errors.append(
                            "orphan principal identity: " f"{type(exc).__name__}: {exc}"
                        )
                        recovery_principal_id = None
                        identity_conflicted = True
                if matching_principal is None and recovery_principal_id is not None:
                    try:
                        revoke_credentials_and_retire_account_principal(
                            client,
                            account_client,
                            principal_id=recovery_principal_id,
                            application_id=application_id,
                            bootstrap_reservation_name=display_name,
                            ownership_marker=external_id,
                            allow_workspace_absence=True,
                            bootstrap_lock_cursor=bootstrap_lock_cursor,
                            bootstrap_lock_key=bootstrap_lock_key,
                            allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                        )
                        credential_cleanup_succeeded = True
                        account_cleanup_succeeded = True
                    except (CredentialCleanupError, AccountPrincipalCleanupError) as exc:
                        try:
                            prove_deleted_bootstrap_principal_absent(
                                client,
                                account_client,
                                principal_id=recovery_principal_id,
                                application_id=application_id,
                            )
                            credential_cleanup_succeeded = True
                            account_cleanup_succeeded = True
                        except Exception as reconcile_exc:  # noqa: BLE001 - retain evidence
                            tombstone_errors.append(
                                "orphan credential/account cleanup: "
                                f"{type(exc).__name__}: {exc}; reconciliation: "
                                f"{type(reconcile_exc).__name__}: {reconcile_exc}"
                            )
                if matching_principal is None and recovery_principal_id is None:
                    tombstone_errors.append(
                        "orphan account principal cleanup: legacy v2 tombstone lacks the "
                        "immutable original SCIM id"
                    )
                if matching_principal is None:
                    try:
                        _fence_and_drain_bootstrap_role(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            database_name=database_name,
                            application_id=application_id,
                            display_name=display_name,
                            target_application_id=target_application_id,
                            external_id=external_id,
                            service_principal_id=recovery_principal_id,
                            expected_executor=expected_executor,
                            allow_absent_managed_event_triggers=(
                                allow_absent_managed_event_triggers
                            ),
                            bootstrap_lock_cursor=bootstrap_lock_cursor,
                            bootstrap_lock_key=bootstrap_lock_key,
                            allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                            signed_tombstone_authority=True,
                        )
                    except Exception as exc:
                        session_cleanup_succeeded = False
                        role_contract_error = exc
                        tombstone_errors.append(
                            "orphan session cleanup: " f"{type(exc).__name__}: {exc}"
                        )
                elif not session_cleanup_succeeded:
                    tombstone_errors.append(
                        "orphan session cleanup: exact SCIM recovery session fence failed"
                    )
                try:
                    role_present = _assert_bootstrap_role_contract(
                        client,
                        deployer_cursor,
                        instance_name=instance_name,
                        database_name=database_name,
                        application_id=application_id,
                        target_application_id=target_application_id,
                        external_id=external_id,
                        service_principal_id=recovery_principal_id,
                        expected_executor=expected_executor,
                        allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                        target_database_absent=admin_database_recovery,
                        signed_tombstone_authority=True,
                    )
                except Exception as exc:
                    role_contract_error = exc
                    tombstone_errors.append(
                        "orphan database role contract: " f"{type(exc).__name__}: {exc}"
                    )
                    try:
                        role_present = role_exists_on_either_plane(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                        )
                    except Exception:  # inventory failure is never absence
                        role_present = True
                role_cleanup_succeeded = not role_present and role_contract_error is None
                if (
                    role_present
                    and role_contract_error is None
                    and session_cleanup_succeeded
                    and credential_cleanup_succeeded
                    and account_cleanup_succeeded
                ):
                    try:
                        _delete_bootstrap_role(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            database_name=database_name,
                            application_id=application_id,
                            display_name=display_name,
                            target_application_id=target_application_id,
                            external_id=external_id,
                            service_principal_id=recovery_principal_id,
                            allow_absent_managed_event_triggers=(
                                allow_absent_managed_event_triggers
                            ),
                            bootstrap_lock_cursor=bootstrap_lock_cursor,
                            bootstrap_lock_key=bootstrap_lock_key,
                            allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                            expected_executor=expected_executor,
                            admin_database_recovery=admin_database_recovery,
                            signed_tombstone_authority=True,
                        )
                        role_cleanup_succeeded = True
                    except TargetDatabaseReappearedError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - retain durable marker
                        tombstone_errors.append(
                            f"orphan database role cleanup: {type(exc).__name__}: {exc}"
                        )
                if (
                    role_cleanup_succeeded
                    and credential_cleanup_succeeded
                    and account_cleanup_succeeded
                ):
                    try:
                        _assert_workspace_mutation_lease(
                            bootstrap_lock_cursor,
                            bootstrap_lock_key,
                            allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                        )
                        _delete_orphan_tombstone(
                            client,
                            account_client=account_client,
                            tombstone_id=tombstone_id,
                            base_external_id=external_id,
                            bootstrap_lock_cursor=bootstrap_lock_cursor,
                            bootstrap_lock_key=bootstrap_lock_key,
                            allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                        )
                    except Exception as exc:  # noqa: BLE001 - aggregate marker cleanup
                        tombstone_errors.append(
                            f"orphan marker cleanup: {type(exc).__name__}: {exc}"
                        )
                elif not credential_cleanup_succeeded:
                    tombstone_errors.append(
                        "orphan marker cleanup: retained because credential cleanup " "was unproven"
                    )
                elif not account_cleanup_succeeded:
                    tombstone_errors.append(
                        "orphan marker cleanup: retained because account principal cleanup "
                        "was unproven"
                    )
                else:
                    tombstone_errors.append(
                        "orphan marker cleanup: retained because database role deletion "
                        "was unproven"
                    )
                cleanup_groups.extend(tombstone_errors)

            for (
                principal_id,
                application_id,
                credential_cleanup_succeeded,
                account_cleanup_succeeded,
                session_cleanup_succeeded,
                cleanup_errors,
            ) in principal_states:
                handled_roles.add(application_id)
                role_present = False
                role_contract_error: Exception | None = None
                try:
                    role_present = _assert_bootstrap_role_contract(
                        client,
                        deployer_cursor,
                        instance_name=instance_name,
                        database_name=database_name,
                        application_id=application_id,
                        target_application_id=target_application_id,
                        external_id=external_id,
                        service_principal_id=principal_id,
                        expected_executor=expected_executor,
                        allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                        target_database_absent=admin_database_recovery,
                        signed_tombstone_authority=True,
                    )
                except Exception as exc:  # noqa: BLE001 - continue independent cleanup
                    role_contract_error = exc
                    cleanup_errors.append("database role contract: " f"{type(exc).__name__}: {exc}")
                    try:
                        role_present = role_exists_on_either_plane(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            application_id=application_id,
                        )
                    except Exception as inventory_exc:  # noqa: BLE001
                        role_present = True
                        cleanup_errors.append(
                            "database role inventory: "
                            f"{type(inventory_exc).__name__}: {inventory_exc}"
                        )
                role_cleanup_succeeded = (
                    not role_present and role_contract_error is None and session_cleanup_succeeded
                )
                if (
                    role_present
                    and role_contract_error is None
                    and session_cleanup_succeeded
                    and credential_cleanup_succeeded
                    and account_cleanup_succeeded
                ):
                    try:
                        _delete_bootstrap_role(
                            client,
                            deployer_cursor,
                            instance_name=instance_name,
                            database_name=database_name,
                            application_id=application_id,
                            display_name=display_name,
                            target_application_id=target_application_id,
                            external_id=external_id,
                            service_principal_id=principal_id,
                            allow_absent_managed_event_triggers=(
                                allow_absent_managed_event_triggers
                            ),
                            bootstrap_lock_cursor=bootstrap_lock_cursor,
                            bootstrap_lock_key=bootstrap_lock_key,
                            allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                            expected_executor=expected_executor,
                            admin_database_recovery=admin_database_recovery,
                            signed_tombstone_authority=True,
                        )
                        role_cleanup_succeeded = True
                    except TargetDatabaseReappearedError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - continue independent cleanup
                        cleanup_errors.append(f"database role cleanup: {type(exc).__name__}: {exc}")
                if not credential_cleanup_succeeded:
                    cleanup_errors.append(
                        "principal cleanup: retained exact signed identity because credential "
                        "cleanup was unproven"
                    )
                elif not account_cleanup_succeeded:
                    cleanup_errors.append(
                        "principal cleanup: retained exact signed identity because account "
                        "principal cleanup was unproven"
                    )
                elif not role_cleanup_succeeded:
                    cleanup_errors.append(
                        "account principal cleanup: completed before database role deletion; "
                        "signed tombstone and OAuth role label retained"
                    )
                cleanup_groups.extend(cleanup_errors)

            for application_id in commented_roles:
                if application_id in handled_roles:
                    continue
                session_contract_valid = True
                try:
                    _fence_and_drain_bootstrap_role(
                        client,
                        deployer_cursor,
                        instance_name=instance_name,
                        database_name=database_name,
                        application_id=application_id,
                        display_name=display_name,
                        target_application_id=target_application_id,
                        external_id=external_id,
                        service_principal_id=None,
                        expected_executor=expected_executor,
                        allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                        bootstrap_lock_cursor=bootstrap_lock_cursor,
                        bootstrap_lock_key=bootstrap_lock_key,
                        allow_unlocked_recovery_for_tests=(allow_unlocked_recovery_for_tests),
                    )
                except Exception as exc:
                    session_contract_valid = False
                    cleanup_groups.append(
                        "commented database role session cleanup: " f"{type(exc).__name__}: {exc}"
                    )
                if session_contract_valid:
                    cleanup_groups.append(
                        "commented database role cleanup: retained because a legacy comment "
                        "does not prove secret or account-principal cleanup"
                    )
            if cleanup_groups:
                raise RuntimeError(
                    f"temporary Lakebase bootstrap cleanup was incomplete: {cleanup_groups!r}"
                )
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("temporary Lakebase bootstrap principal cleanup did not converge")
