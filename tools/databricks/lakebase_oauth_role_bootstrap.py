"""One-use privileged bootstrap for a LOGIN-only Lakebase OAuth role."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql as psql

from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from jobs.lakebase_migration_contracts import (
    _MANAGED_OAUTH_ROLE_FUNCTION_ACLS,
    _MANAGED_OAUTH_ROLE_FUNCTION_OWNER_ONLY_ACL,
    _MANAGED_OAUTH_ROLE_FUNCTION_PUBLIC_ACLS,
    _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES,
    _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256,
)
from jobs.lakebase_migration_provider_plane import _postflight_public_schema_boundary
from tools.databricks.lakebase_oauth_role_profile import (
    assert_oauth_security_label,
    read_profile,
)

SAFE_OAUTH_PROFILE = (False, False, False, False, False, True, True)
LEGACY_API_OAUTH_PROFILE = (False, False, False, True, False, True, True)
_BOOTSTRAP_API_PROFILE = (False, True, False, True, False, True, True)
_ROLE_FUNCTION_SOURCE_SHA256 = _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256
_ROLE_FUNCTION_SOURCE_BYTES = _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES
_SDK_HTTP_TIMEOUT_SECONDS = 30
_SDK_RETRY_TIMEOUT_SECONDS = 30
_CONTROL_CLIENT_ID_ENV = "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID"
_CONTROL_CLIENT_SECRET_ENV = "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET"


def _bounded_bootstrap_workspace_client(**kwargs: Any) -> WorkspaceClient:
    """Build the one-use M2M client with finite control-plane retries."""

    return WorkspaceClient(
        config=Config(
            **kwargs,
            http_timeout_seconds=_SDK_HTTP_TIMEOUT_SECONDS,
            retry_timeout_seconds=_SDK_RETRY_TIMEOUT_SECONDS,
        )
    )


def _connection_kwargs(
    client: Any,
    *,
    instance_name: str,
    database_name: str,
    database_user: str,
    autocommit: bool,
) -> dict[str, Any]:
    instance = client.database.get_database_instance(instance_name)
    host = str(getattr(instance, "read_write_dns", "") or "").strip()
    if not host:
        raise RuntimeError(f"Lakebase instance {instance_name!r} has no read_write_dns")
    credential = client.database.generate_database_credential(
        instance_names=[instance_name],
        request_id=str(uuid.uuid4()),
    )
    token = str(getattr(credential, "token", "") or "")
    if not token:
        raise RuntimeError("Lakebase credential response contained no token")
    return {
        "host": host,
        "port": 5432,
        "dbname": database_name,
        "user": database_user,
        "password": token,
        "sslmode": "require",
        "connect_timeout": 15,
        # Provider role creation and its exact SQL postflight are one unit.
        # A caller may override nothing here: commit is always explicit below.
        "autocommit": autocommit,
    }


def _workspace_database_identity(client: Any) -> tuple[str, set[str]]:
    identity = client.current_user.me()
    ordered = [
        str(getattr(identity, field, "") or "").strip()
        for field in ("application_id", "user_name")
        if str(getattr(identity, field, "") or "").strip()
    ]
    if not ordered:
        raise RuntimeError("current Databricks identity has no database login name")
    return ordered[0], set(ordered)


def _assert_exact_bootstrap_membership(
    cursor: Any,
    *,
    application_id: str,
    bootstrap_application_id: str,
) -> None:
    cursor.execute(
        """
        SELECT parent.rolname,
               member.rolname,
               membership.admin_option,
               membership.inherit_option,
               membership.set_option,
               grantor.rolname
        FROM pg_auth_members membership
        JOIN pg_roles parent ON parent.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        JOIN pg_roles grantor ON grantor.oid = membership.grantor
        WHERE membership.roleid = (SELECT oid FROM pg_roles WHERE rolname = %s)
           OR membership.member = (SELECT oid FROM pg_roles WHERE rolname = %s)
        ORDER BY parent.rolname, member.rolname
        """,
        (application_id, application_id),
    )
    expected = [
        (
            application_id,
            bootstrap_application_id,
            True,
            False,
            False,
            "cloud_admin",
        )
    ]
    # The provider extension's attribution may use session_user or may create
    # no creator edge. Observe the live graph. Only the disposable bootstrap
    # edge (or no edge) can become a successful committed target.
    actual = cursor.fetchall()
    if actual not in ([], expected):
        raise RuntimeError(
            "databricks_create_role returned an unreviewed bootstrap membership: " f"{actual!r}"
        )


def _assert_no_relationships(
    cursor: Any,
    application_id: str,
    *,
    attempts: int = 15,
) -> None:
    for attempt in range(attempts):
        cursor.execute(
            """
            SELECT 1
            FROM pg_auth_members membership
            WHERE membership.roleid = (SELECT oid FROM pg_roles WHERE rolname = %s)
               OR membership.member = (SELECT oid FROM pg_roles WHERE rolname = %s)
            LIMIT 1
            """,
            (application_id, application_id),
        )
        if cursor.fetchone() is None:
            return
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("Lakebase bootstrap membership survived creator deletion")


def _assert_role_function_contract(
    deployer_cursor: Any,
) -> None:
    """Pin the provider-owned C primitive before a privileged caller executes it."""

    deployer_cursor.execute(
        """
        SELECT namespace.nspname,
               routine.proname,
               oidvectortypes(routine.proargtypes),
               routine.prokind,
               pg_get_function_result(routine.oid),
               routine_owner.rolname,
               language.lanname,
               routine.provolatile,
               routine.proparallel,
               routine.proleakproof,
               routine.proisstrict,
               routine.prosecdef,
               routine.proconfig,
               routine.probin,
               extension.extname,
               extension.extversion,
               extension.extrelocatable,
               extension_namespace.nspname,
               extension_owner.rolname,
               encode(sha256(convert_to(routine.prosrc, 'UTF8')), 'hex'),
               octet_length(convert_to(routine.prosrc, 'UTF8')),
               routine.proacl
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        JOIN pg_roles routine_owner ON routine_owner.oid = routine.proowner
        JOIN pg_language language ON language.oid = routine.prolang
        JOIN pg_depend extension_membership
          ON extension_membership.classid = 'pg_proc'::regclass
         AND extension_membership.objid = routine.oid
         AND extension_membership.objsubid = 0
         AND extension_membership.deptype = 'e'
        JOIN pg_extension extension ON extension.oid = extension_membership.refobjid
        JOIN pg_namespace extension_namespace ON extension_namespace.oid = extension.extnamespace
        JOIN pg_roles extension_owner ON extension_owner.oid = extension.extowner
        CROSS JOIN pg_database database_object
        WHERE namespace.nspname = 'public'
          AND routine.proname = 'databricks_create_role'
          AND routine.proargtypes = '25 25'::oidvector
          AND database_object.datname = current_database()
        """
    )
    expected = (
        "public",
        "databricks_create_role",
        "text, text",
        "f",
        "text",
        "cloud_admin",
        "c",
        "v",
        "s",
        False,
        True,
        False,
        None,
        "$libdir/databricks_auth",
        "databricks_auth",
        "1.0",
        True,
        "public",
        None,
        _ROLE_FUNCTION_SOURCE_SHA256,
        _ROLE_FUNCTION_SOURCE_BYTES,
        None,
    )
    row = deployer_cursor.fetchone()
    if row is None:
        raise RuntimeError("Databricks OAuth role-creation function contract is absent")
    function_acl = None if row[-1] is None else tuple(sorted(str(item) for item in row[-1]))
    if function_acl not in _MANAGED_OAUTH_ROLE_FUNCTION_ACLS:
        raise RuntimeError("Databricks OAuth role-creation function contract drifted")
    actual = (*tuple(row[:-1]), function_acl)
    expected_writer = f"databricks_writer_{int(actual_database_oid(deployer_cursor))}"
    expected = (*expected[:18], expected_writer, *expected[19:])
    expected = (*expected[:-1], function_acl)
    if actual != expected:
        raise RuntimeError("Databricks OAuth role-creation function contract drifted")
    if (
        function_acl == _MANAGED_OAUTH_ROLE_FUNCTION_OWNER_ONLY_ACL
        or function_acl not in _MANAGED_OAUTH_ROLE_FUNCTION_PUBLIC_ACLS
    ):
        raise RuntimeError(
            "Databricks OAuth role-creation function is not executable by bootstrap identities"
        )


def actual_database_oid(cursor: Any) -> int:
    cursor.execute("SELECT oid FROM pg_database WHERE datname = current_database()")
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("current Lakebase database identity is absent")
    return int(row[0])


def _wait_for_profile(
    cursor: Any,
    *,
    application_id: str,
    expected: tuple[bool, ...] | None,
    attempts: int = 15,
) -> None:
    actual: tuple[bool, ...] | None = None
    for attempt in range(attempts):
        actual = read_profile(cursor, application_id)
        if actual == expected:
            return
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError(f"temporary Lakebase bootstrap role profile did not converge: {actual!r}")


def _create_login_only_role_locked(
    client: Any,
    deployer_cursor: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    application_id: str,
    service_principal_id: str,
    connect: Callable[..., Any] = psycopg.connect,
    workspace_client_factory: Callable[..., Any] | None = None,
    allow_absent_managed_event_triggers: bool = False,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
    expected_executor: str,
) -> None:
    """Create a safe role via a one-use creator, then delete the creator.

    ``databricks_create_role`` grants its caller an ADMIN-only relationship to
    the new role, and that grant is recorded as coming from ``cloud_admin``.
    PostgreSQL will not let the caller revoke it.  Deleting a purpose-created
    caller role through the control plane removes the relationship without
    retaining a reusable privileged identity.
    """
    from databricks.sdk.service.database import (
        DatabaseInstanceRole,
        DatabaseInstanceRoleAttributes,
        DatabaseInstanceRoleIdentityType,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_cleanup import (
        finalize_bootstrap_identity,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
        assert_bootstrap_lock_held,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
        assert_exact_session_identity,
        cleanup_executor_identity,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_target import (
        assert_residual_target_contract,
        prove_target_absent,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_wrapper import (
        _event_trigger_preflight,
        assert_wrapper_contract,
        create_wrapper,
    )
    from tools.databricks.lakebase_oauth_role_recovery import (
        _assert_bootstrap_principal_contract,
        _assert_bootstrap_role_contract,
        _bootstrap_identity_contract,
        _marker_signing_key,
        recover_stale_bootstrap_identities,
    )
    from tools.databricks.lakebase_oauth_role_scim_marker import (
        bootstrap_principal_display_name,
    )
    from tools.databricks.lakebase_oauth_role_tombstone import (
        ensure_orphan_tombstone,
    )

    if workspace_client_factory is None:
        workspace_client_factory = _bounded_bootstrap_workspace_client

    bootstrap_reservation_name, bootstrap_external_id = _bootstrap_identity_contract(
        instance_name=instance_name,
        database_name=database_name,
        application_id=application_id,
    )
    marker_signing_key = _marker_signing_key()
    if marker_signing_key is None:
        raise RuntimeError(
            "MIP_AI_GATEWAY_PROOF_SIGNING_KEY is required for Lakebase bootstrap markers"
        )
    bootstrap_name = bootstrap_principal_display_name(
        reservation_name=bootstrap_reservation_name,
        ownership_marker=bootstrap_external_id,
        signing_key=marker_signing_key,
    )
    control_application_id = os.environ.get(_CONTROL_CLIENT_ID_ENV, "").strip()
    control_client_secret = os.environ.get(_CONTROL_CLIENT_SECRET_ENV, "").strip()
    if not control_application_id or not control_client_secret:
        raise RuntimeError(
            "fresh OAuth-M2M Lakebase bootstrap control credentials are required"
        )
    if control_application_id == application_id:
        raise RuntimeError(
            "Lakebase bootstrap control identity must be distinct from the target"
        )
    bootstrap_sp: Any | None = None
    bootstrap_application_id = ""
    bootstrap_scim_id = ""
    create_attempted = False
    provider_invocation_attempted = False
    provider_commit_attempted = False
    provider_commit_completed = False
    retain_bootstrap_evidence = False
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []
    provider_transaction_diagnostics: list[str] = []
    try:
        recover_stale_bootstrap_identities(
            client,
            deployer_cursor,
            account_client=account_client,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=application_id,
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            expected_executor=expected_executor,
        )
        _postflight_public_schema_boundary(
            deployer_cursor,
            (),
            principal_label="one-use OAuth bootstrap",
            allow_legacy_public_usage=False,
            allow_empty_target_roles=True,
        )
        _assert_role_function_contract(deployer_cursor)
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        prove_target_absent(
            client,
            deployer_cursor,
            instance_name=instance_name,
            application_id=application_id,
            expected_executor=expected_executor,
        )
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        create_attempted = True
        bootstrap_sp = client.service_principals.create(
            display_name=bootstrap_name,
            active=True,
        )
        bootstrap_application_id = str(getattr(bootstrap_sp, "application_id", "") or "").strip()
        bootstrap_scim_id = str(getattr(bootstrap_sp, "id", "") or "").strip()
        if not bootstrap_application_id or not bootstrap_scim_id:
            raise RuntimeError("temporary Lakebase bootstrap principal has incomplete identity")
        if bootstrap_application_id == control_application_id:
            raise RuntimeError(
                "Lakebase bootstrap control identity collided with the one-use principal"
            )
        verified_scim_id, verified_application_id = _assert_bootstrap_principal_contract(
            client,
            bootstrap_sp,
            display_name=bootstrap_name,
            external_id=bootstrap_external_id,
        )
        if (
            verified_scim_id != bootstrap_scim_id
            or verified_application_id != bootstrap_application_id
        ):
            raise RuntimeError("temporary Lakebase bootstrap creation identity changed")
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        # Persist signed, immutable two-id recovery authority before either
        # credentials or the provider-owned SQL role can exist. Databricks may
        # hide a deactivated SCIM principal from GET while its secret proxy and
        # Lakebase role remain addressable.
        ensure_orphan_tombstone(
            client,
            base_external_id=bootstrap_external_id,
            application_id=bootstrap_application_id,
            principal_id=bootstrap_scim_id,
            signing_key=marker_signing_key,
        )
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        _event_trigger_preflight(
            deployer_cursor,
            principal_label="bootstrap role CREATE",
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        )
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        client.database.create_database_instance_role(
            instance_name,
            DatabaseInstanceRole(
                name=bootstrap_application_id,
                identity_type=DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL,
                attributes=DatabaseInstanceRoleAttributes(
                    bypassrls=False,
                    createdb=False,
                    createrole=True,
                ),
            ),
        )
        _wait_for_profile(
            deployer_cursor,
            application_id=bootstrap_application_id,
            expected=_BOOTSTRAP_API_PROFILE,
        )
        if not _assert_bootstrap_role_contract(
            client,
            deployer_cursor,
            instance_name=instance_name,
            database_name=database_name,
            application_id=bootstrap_application_id,
            target_application_id=application_id,
            external_id=bootstrap_external_id,
            service_principal_id=bootstrap_scim_id,
            expected_executor=expected_executor,
            expected_privileges=frozenset(),
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        ):
            raise RuntimeError("temporary Lakebase bootstrap role disappeared after creation")
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        wrapper_schema, wrapper_function_fingerprint = create_wrapper(
            deployer_cursor,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=application_id,
            bootstrap_application_id=bootstrap_application_id,
            expected_executor=expected_executor,
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
        from tools.databricks.lakebase_oauth_role_bootstrap_orchestration import (
            execute_admitted_provider_bootstrap,
        )
        from tools.databricks.lakebase_oauth_role_bootstrap_provider_boundary import (
            assert_provider_boundary_contract,
        )

        def presecret_contract() -> None:
            assert_bootstrap_lock_held(
                bootstrap_lock_cursor,
                lock_key=bootstrap_lock_key,
            )
            if not _assert_bootstrap_role_contract(
                client,
                deployer_cursor,
                instance_name=instance_name,
                database_name=database_name,
                application_id=bootstrap_application_id,
                target_application_id=application_id,
                external_id=bootstrap_external_id,
                service_principal_id=bootstrap_scim_id,
                expected_executor=expected_executor,
                expected_privileges=frozenset({"USAGE", "EXECUTE"}),
                allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
            ):
                raise RuntimeError("temporary Lakebase bootstrap role disappeared before auth")
            _assert_role_function_contract(deployer_cursor)
            assert_wrapper_contract(
                deployer_cursor,
                instance_name=instance_name,
                database_name=database_name,
                target_application_id=application_id,
                bootstrap_application_id=bootstrap_application_id,
                expected_executor=expected_executor,
                expected_privileges=frozenset({"USAGE", "EXECUTE"}),
            )

        def positive_control() -> None:
            control_kwargs = _connection_kwargs(
                client,
                instance_name=instance_name,
                database_name=database_name,
                database_user=expected_executor,
                autocommit=True,
            )
            control_connection = connect(**control_kwargs)
            control_cursor: Any | None = None
            try:
                control_cursor = control_connection.cursor()
                cleanup_executor_identity(
                    control_cursor,
                    excluded_application_id=bootstrap_application_id,
                    expected_executor=expected_executor,
                )
                control_cursor.execute("SELECT 1")
                if control_cursor.fetchone() != (1,):
                    raise RuntimeError("fresh Lakebase deployer control query failed")
            finally:
                if control_cursor is not None and callable(getattr(control_cursor, "close", None)):
                    control_cursor.close()
                if callable(getattr(control_connection, "close", None)):
                    control_connection.close()

        def provider_boundary_contract(cursor: Any, *, before_commit: bool) -> None:
            assert_provider_boundary_contract(
                client,
                deployer_cursor,
                cursor,
                instance_name=instance_name,
                database_name=database_name,
                target_application_id=application_id,
                bootstrap_application_id=bootstrap_application_id,
                bootstrap_service_principal_id=bootstrap_scim_id,
                bootstrap_external_id=bootstrap_external_id,
                expected_executor=expected_executor,
                expected_function_fingerprint=wrapper_function_fingerprint,
                allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
                bootstrap_lock_cursor=bootstrap_lock_cursor,
                bootstrap_lock_key=bootstrap_lock_key,
                require_target_absence=not before_commit,
            )

        def mark_provider_invocation() -> None:
            nonlocal provider_invocation_attempted
            provider_invocation_attempted = True

        def mark_provider_commit() -> None:
            nonlocal provider_commit_attempted
            provider_commit_attempted = True

        def mark_provider_commit_completed() -> None:
            nonlocal provider_commit_completed
            provider_commit_completed = True

        def invoke_provider(cursor: Any) -> None:
            cursor.execute(
                psql.SQL("SELECT {}.{}()").format(
                    psql.Identifier(wrapper_schema),
                    psql.Identifier("create_target_role"),
                )
            )

        def validate_provider_result(cursor: Any) -> None:
            assert_exact_session_identity(
                cursor,
                application_id=bootstrap_application_id,
            )
            if read_profile(cursor, application_id) != SAFE_OAUTH_PROFILE:
                raise RuntimeError("databricks_create_role returned unsafe role attributes")
            assert_oauth_security_label(
                cursor,
                application_id=application_id,
                service_principal_id=service_principal_id,
            )
            _assert_exact_bootstrap_membership(
                cursor,
                application_id=application_id,
                bootstrap_application_id=bootstrap_application_id,
            )

        execute_admitted_provider_bootstrap(
            client,
            account_client,
            deployer_cursor,
            workspace_client_factory=workspace_client_factory,
            connect=connect,
            workspace_host=client.config.host,
            instance_name=instance_name,
            database_name=database_name,
            bootstrap_application_id=bootstrap_application_id,
            bootstrap_scim_id=bootstrap_scim_id,
            bootstrap_display_name=bootstrap_name,
            bootstrap_reservation_name=bootstrap_reservation_name,
            bootstrap_external_id=bootstrap_external_id,
            control_application_id=control_application_id,
            control_client_secret=control_client_secret,
            expected_executor=expected_executor,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            presecret_contract=presecret_contract,
            positive_control=positive_control,
            preinvoke_contract=lambda cursor: provider_boundary_contract(
                cursor, before_commit=False
            ),
            precommit_contract=lambda cursor: provider_boundary_contract(
                cursor, before_commit=True
            ),
            mark_provider_invocation=mark_provider_invocation,
            mark_provider_commit=mark_provider_commit,
            mark_provider_commit_completed=mark_provider_commit_completed,
            invoke_provider=invoke_provider,
            validate_provider_result=validate_provider_result,
            transaction_diagnostics=provider_transaction_diagnostics,
            # Resolve at the call boundary so deterministic tests can replace
            # this module's clock without weakening the production wait.
            sleep=time.sleep,
        )

        # Do not infer provider attribution from SECURITY INVOKER alone. Re-read
        # the committed product through the deployer and accept only no creator
        # edge or the disposable bootstrap edge. Any other state is quarantined.
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        assert_residual_target_contract(
            client,
            deployer_cursor,
            instance_name=instance_name,
            application_id=application_id,
            service_principal_id=service_principal_id,
            allowed_creator_roles=frozenset({bootstrap_application_id}),
            expected_executor=expected_executor,
        )

    except BaseException as exc:
        primary_error = exc
        if provider_invocation_attempted:
            try:
                from tools.databricks.lakebase_oauth_role_bootstrap_reconcile import (
                    classify_residual_target,
                    quarantine_indeterminate_target,
                )

                residual_state = classify_residual_target(
                    client,
                    connect=connect,
                    instance_name=instance_name,
                    database_name=database_name,
                    application_id=application_id,
                    service_principal_id=service_principal_id,
                    allowed_creator_roles=frozenset({bootstrap_application_id}),
                    expected_executor=expected_executor,
                    bootstrap_lock_cursor=bootstrap_lock_cursor,
                    bootstrap_lock_key=bootstrap_lock_key,
                )
                if (
                    residual_state == "exact"
                    and provider_commit_attempted
                    and not provider_commit_completed
                ):
                    primary_error = None
                elif residual_state == "exact":
                    retain_bootstrap_evidence = True
                    cleanup_errors.append(
                        "target reconciliation: exact target cannot erase a lifecycle failure"
                    )
                elif residual_state == "indeterminate":
                    retain_bootstrap_evidence = True
                    cleanup_errors.extend(provider_transaction_diagnostics)
                    quarantine_indeterminate_target(
                        client,
                        connect=connect,
                        instance_name=instance_name,
                        database_name=database_name,
                        application_id=application_id,
                        service_principal_id=service_principal_id,
                        expected_executor=expected_executor,
                        allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                        bootstrap_lock_cursor=bootstrap_lock_cursor,
                        bootstrap_lock_key=bootstrap_lock_key,
                    )
                    cleanup_errors.append(
                        "target reconciliation: residual state was not stably exact or absent"
                    )
            except Exception as compensation_error:
                retain_bootstrap_evidence = True
                cleanup_errors.append(
                    "target reconciliation: "
                    f"{type(compensation_error).__name__}: {compensation_error}"
                )
    finally:
        if create_attempted:
            cleanup_errors.extend(
                finalize_bootstrap_identity(
                    client,
                    account_client=account_client,
                    connect=connect,
                    instance_name=instance_name,
                    database_name=database_name,
                    target_application_id=application_id,
                    bootstrap_application_id=bootstrap_application_id,
                    bootstrap_scim_id=bootstrap_scim_id,
                    bootstrap_display_name=bootstrap_name,
                    bootstrap_external_id=bootstrap_external_id,
                    expected_executor=expected_executor,
                    retain_evidence=retain_bootstrap_evidence,
                    allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                    bootstrap_lock_cursor=bootstrap_lock_cursor,
                    bootstrap_lock_key=bootstrap_lock_key,
                )
            )

    if primary_error is not None:
        if cleanup_errors:
            raise RuntimeError(
                f"Lakebase role bootstrap failed and cleanup was incomplete: {cleanup_errors!r}"
            ) from primary_error
        raise primary_error
    if cleanup_errors:
        raise RuntimeError(f"Lakebase role bootstrap cleanup was incomplete: {cleanup_errors!r}")
    if read_profile(deployer_cursor, application_id) != SAFE_OAUTH_PROFILE:
        raise RuntimeError("safe Lakebase role did not persist after bootstrap cleanup")
    assert_oauth_security_label(
        deployer_cursor,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    _assert_no_relationships(deployer_cursor, application_id)


def create_login_only_role(
    client: Any,
    deployer_cursor: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    application_id: str,
    service_principal_id: str,
    connect: Callable[..., Any] = psycopg.connect,
    workspace_client_factory: Callable[..., Any] | None = None,
    allow_absent_managed_event_triggers: bool = False,
    bootstrap_lock_cursor: Any | None = None,
    bootstrap_lock_key: Any | None = None,
) -> None:
    """Serialize one target on the instance-wide canonical admin database."""

    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
        acquire_bootstrap_lock,
        release_bootstrap_lock,
    )
    from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
        cleanup_executor_identity,
    )

    if not application_id or application_id != application_id.strip():
        raise ValueError("application_id must be non-empty canonical text")
    if (bootstrap_lock_cursor is None) != (bootstrap_lock_key is None):
        raise ValueError("bootstrap lock cursor and key must be supplied together")
    if bootstrap_lock_cursor is not None and bootstrap_lock_key is not None:
        from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
            assert_bootstrap_lock_held,
        )

        _database_user, accepted_users = _workspace_database_identity(client)
        executor = cleanup_executor_identity(
            deployer_cursor,
            excluded_application_id=application_id,
        )
        if executor not in accepted_users:
            raise RuntimeError("Lakebase deployer authenticated as the wrong identity")
        cleanup_executor_identity(
            bootstrap_lock_cursor,
            excluded_application_id=application_id,
            expected_executor=executor,
        )
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        _create_login_only_role_locked(
            client,
            deployer_cursor,
            account_client=account_client,
            instance_name=instance_name,
            database_name=database_name,
            application_id=application_id,
            service_principal_id=service_principal_id,
            connect=connect,
            workspace_client_factory=workspace_client_factory,
            allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            expected_executor=executor,
        )
        return

    database_user, accepted_users = _workspace_database_identity(client)
    lock_kwargs = _connection_kwargs(
        client,
        instance_name=instance_name,
        database_name="databricks_postgres",
        database_user=database_user,
        autocommit=True,
    )
    with connect(**lock_kwargs) as lock_connection, lock_connection.cursor() as lock_cursor:
        executor = cleanup_executor_identity(
            lock_cursor,
            excluded_application_id=application_id,
        )
        if executor not in accepted_users:
            raise RuntimeError("canonical Lakebase lock authenticated as the wrong identity")
        cleanup_executor_identity(
            deployer_cursor,
            excluded_application_id=application_id,
            expected_executor=executor,
        )
        lock_key = acquire_bootstrap_lock(
            lock_cursor,
            instance_name=instance_name,
            target_application_id=application_id,
        )
        try:
            _create_login_only_role_locked(
                client,
                deployer_cursor,
                account_client=account_client,
                instance_name=instance_name,
                database_name=database_name,
                application_id=application_id,
                service_principal_id=service_principal_id,
                connect=connect,
                workspace_client_factory=workspace_client_factory,
                allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
                bootstrap_lock_cursor=lock_cursor,
                bootstrap_lock_key=lock_key,
                expected_executor=executor,
            )
        finally:
            release_bootstrap_lock(lock_cursor, lock_key=lock_key)
