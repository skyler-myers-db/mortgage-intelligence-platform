"""Fail-closed reconciliation for a target created by the one-use bootstrap."""

from __future__ import annotations

import time
from typing import Any

from tools.databricks.lakebase_oauth_role_bootstrap import (
    SAFE_OAUTH_PROFILE,
    assert_oauth_security_label,
    read_profile,
)
from tools.databricks.lakebase_oauth_role_bootstrap_contract import _control_plane_role
from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
    assert_bootstrap_lock_held,
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
)


def _target_relationships(cursor: Any, application_id: str) -> list[tuple[Any, ...]]:
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
        ORDER BY parent.rolname, member.rolname, grantor.rolname
        """,
        (application_id, application_id),
    )
    return list(cursor.fetchall())


def _target_shared_dependencies(cursor: Any, application_id: str) -> list[tuple[Any, ...]]:
    cursor.execute(
        """
        SELECT dependency.dbid,
               dependency.classid::regclass::text,
               dependency.objid,
               dependency.objsubid,
               dependency.deptype
        FROM pg_shdepend dependency
        WHERE dependency.refclassid = 'pg_authid'::regclass
          AND dependency.refobjid = (
              SELECT oid FROM pg_roles WHERE rolname = %s
          )
        ORDER BY 1, 2, 3, 4, 5
        """,
        (application_id,),
    )
    return list(cursor.fetchall())


def _assert_target_role_settings(cursor: Any, application_id: str) -> None:
    cursor.execute(
        """
        SELECT rolconnlimit, rolvaliduntil, rolpassword, rolconfig
        FROM pg_roles
        WHERE rolname = %s
        """,
        (application_id,),
    )
    rows = list(cursor.fetchall())
    if rows != [(-1, None, "********", None)]:
        raise RuntimeError("new Lakebase target role setting contract drifted")
    cursor.execute(
        """
        SELECT setting.setdatabase, setting.setrole, setting.setconfig
        FROM pg_db_role_setting setting
        WHERE setting.setrole = (
            SELECT oid FROM pg_roles WHERE rolname = %s
        )
        ORDER BY setting.setdatabase, setting.setrole
        """,
        (application_id,),
    )
    if cursor.fetchall():
        raise RuntimeError("new Lakebase target has database-scoped role settings")


def _target_control_role(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
) -> Any | None:
    role = _control_plane_role(
        client,
        instance_name=instance_name,
        application_id=application_id,
    )
    if role is not None:
        identity_type = getattr(role, "identity_type", None)
        if str(getattr(identity_type, "value", identity_type) or "") != "SERVICE_PRINCIPAL":
            raise RuntimeError("new Lakebase target control-plane identity type drifted")
    return role


def prove_target_absent(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    application_id: str,
    expected_executor: str,
    previous_fence: SessionFence | None = None,
    attempts: int = 15,
    required_absence: int = 3,
) -> None:
    """Require stable absence on SQL and control planes before or after bootstrap."""

    absence = 0
    for attempt in range(attempts):
        cleanup_executor_identity(
            cursor,
            excluded_application_id=application_id,
            expected_executor=expected_executor,
        )
        if previous_fence is None:
            cursor.execute(
                "SELECT oid, rolname FROM pg_roles WHERE rolname = %s ORDER BY oid",
                (application_id,),
            )
        else:
            cursor.execute(
                """
                SELECT oid, rolname
                FROM pg_roles
                WHERE rolname = %s OR oid = %s
                ORDER BY oid
                """,
                (application_id, previous_fence.role_oid),
            )
        role_rows = list(cursor.fetchall())
        control_absent = (
            _target_control_role(
                client,
                instance_name=instance_name,
                application_id=application_id,
            )
            is None
        )
        relationships = _target_relationships(cursor, application_id)
        dependencies = _target_shared_dependencies(cursor, application_id)
        observed_pids = sorted(previous_fence.observed_pids) if previous_fence is not None else []
        observed_oid = previous_fence.role_oid if previous_fence is not None else None
        cursor.execute(
            """
            SELECT pid, usesysid, usename
            FROM pg_stat_activity
            WHERE usesysid = %s
               OR usename = %s
               OR pid = ANY(%s::integer[])
            ORDER BY pid
            """,
            (observed_oid, application_id, observed_pids),
        )
        sessions = list(cursor.fetchall())
        if (
            not role_rows
            and control_absent
            and not relationships
            and not dependencies
            and not sessions
        ):
            absence += 1
            if absence >= required_absence:
                return
        else:
            absence = 0
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("new Lakebase target absence did not converge across both planes")


def assert_residual_target_contract(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    application_id: str,
    service_principal_id: str,
    allowed_creator_roles: frozenset[str],
    expected_executor: str,
    expected_profile: tuple[bool, ...] = SAFE_OAUTH_PROFILE,
    terminate_sessions: bool = True,
) -> None:
    """Prove a residual target is exactly the bounded bootstrap product."""

    cleanup_executor_identity(
        cursor,
        excluded_application_id=application_id,
        expected_executor=expected_executor,
    )
    if (
        _target_control_role(
            client,
            instance_name=instance_name,
            application_id=application_id,
        )
        is None
    ):
        raise RuntimeError("new Lakebase target control-plane identity is absent")
    if read_profile(cursor, application_id) != expected_profile:
        raise RuntimeError("new Lakebase target role profile drifted")
    _assert_target_role_settings(cursor, application_id)
    assert_oauth_security_label(
        cursor,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    if _target_shared_dependencies(cursor, application_id):
        raise RuntimeError("new Lakebase target has ownership or ACL dependencies")

    relationships = _target_relationships(cursor, application_id)
    allowed = {
        (application_id, creator, True, False, False, "cloud_admin")
        for creator in allowed_creator_roles
    }
    if len(relationships) > 1 or not set(relationships) <= allowed:
        raise RuntimeError("new Lakebase target has unreviewed role relationships")
    if terminate_sessions:
        terminate_bootstrap_sessions(
            cursor,
            application_id=application_id,
            expected_executor=expected_executor,
        )


def quarantine_residual_target_identity(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    application_id: str,
    service_principal_id: str,
    expected_executor: str,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> None:
    """Drain a just-created indeterminate target without destructive mutation."""

    cleanup_executor_identity(
        cursor,
        excluded_application_id=application_id,
        expected_executor=expected_executor,
    )
    if (
        _target_control_role(
            client,
            instance_name=instance_name,
            application_id=application_id,
        )
        is None
    ):
        raise RuntimeError("new Lakebase target control-plane identity is absent")
    profile = read_profile(cursor, application_id)
    if profile is None:
        raise RuntimeError("new Lakebase target SQL identity is absent")
    assert_oauth_security_label(
        cursor,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    assert_bootstrap_lock_held(
        bootstrap_lock_cursor,
        lock_key=bootstrap_lock_key,
    )
    terminate_bootstrap_sessions(
        cursor,
        application_id=application_id,
        expected_executor=expected_executor,
    )


def fence_residual_target_for_delete(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    application_id: str,
    service_principal_id: str,
    allowed_creator_roles: frozenset[str],
    expected_executor: str,
    expected_profile: tuple[bool, ...],
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> SessionFence:
    """Prove and drain an exact provider-owned target before deletion."""

    assert_residual_target_contract(
        client,
        cursor,
        instance_name=instance_name,
        application_id=application_id,
        service_principal_id=service_principal_id,
        allowed_creator_roles=allowed_creator_roles,
        expected_executor=expected_executor,
        expected_profile=expected_profile,
    )
    assert_bootstrap_lock_held(
        bootstrap_lock_cursor,
        lock_key=bootstrap_lock_key,
    )
    return terminate_bootstrap_sessions(
        cursor,
        application_id=application_id,
        expected_executor=expected_executor,
    )


def delete_fenced_target_role(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    application_id: str,
    service_principal_id: str,
    allowed_creator_roles: frozenset[str],
    expected_executor: str,
    expected_profile: tuple[bool, ...],
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> None:
    """Delete an exact quiesced target through the provider API."""

    fence = fence_residual_target_for_delete(
        client,
        cursor,
        instance_name=instance_name,
        application_id=application_id,
        service_principal_id=service_principal_id,
        allowed_creator_roles=allowed_creator_roles,
        expected_executor=expected_executor,
        expected_profile=expected_profile,
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        bootstrap_lock_cursor=bootstrap_lock_cursor,
        bootstrap_lock_key=bootstrap_lock_key,
    )
    assert_bootstrap_lock_held(
        bootstrap_lock_cursor,
        lock_key=bootstrap_lock_key,
    )
    _event_trigger_preflight(
        cursor,
        principal_label="OAuth target role control-plane DROP",
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    assert_bootstrap_lock_held(
        bootstrap_lock_cursor,
        lock_key=bootstrap_lock_key,
    )
    delete_error: Exception | None = None
    try:
        client.database.delete_database_instance_role(instance_name, application_id)
    except Exception as exc:  # noqa: BLE001 - reconcile a commit-ambiguous DROP
        delete_error = exc
    try:
        fence = drain_post_delete_sessions(
            cursor,
            application_id=application_id,
            fence=fence,
        )
        prove_target_absent(
            client,
            cursor,
            instance_name=instance_name,
            application_id=application_id,
            expected_executor=expected_executor,
            previous_fence=fence,
        )
        prove_post_delete_session_absence(
            cursor,
            application_id=application_id,
            fence=fence,
        )
    except Exception as absence_error:
        if delete_error is not None:
            raise RuntimeError(
                "provider-owned Lakebase target role deletion did not converge; "
                "the exact reviewed role was retained"
            ) from delete_error
        raise absence_error
