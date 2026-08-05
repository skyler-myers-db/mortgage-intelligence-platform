"""Exact SQL contract for a one-use Lakebase OAuth role creator."""

from __future__ import annotations

import re
from typing import Any

from tools.databricks.lakebase_oauth_role_bootstrap import (
    _BOOTSTRAP_API_PROFILE,
    assert_oauth_security_label,
    read_profile,
)
from tools.databricks.lakebase_oauth_role_bootstrap_wrapper import (
    assert_wrapper_contract,
    wrapper_schema_name,
)
from tools.databricks.lakebase_oauth_role_scim_marker import (
    assert_bootstrap_principal_display_name,
    assert_scim_external_id_unset,
)


def _control_plane_role(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
) -> Any | None:
    roles = [
        role
        for role in client.database.list_database_instance_roles(instance_name)
        if str(getattr(role, "name", "") or "") == application_id
    ]
    if len(roles) > 1:
        raise RuntimeError("temporary Lakebase bootstrap role inventory is ambiguous")
    return roles[0] if roles else None


def _bootstrap_role_relationships(
    cursor: Any,
    application_id: str,
) -> list[tuple[Any, ...]]:
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
    return list(cursor.fetchall())


def _role_ownership_marker(cursor: Any, application_id: str) -> str | None:
    cursor.execute(
        "SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = %s",
        (application_id,),
    )
    row = cursor.fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _assert_no_bootstrap_acl_dependencies(
    cursor: Any,
    *,
    application_id: str,
) -> None:
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
    if cursor.fetchall():
        raise RuntimeError("temporary Lakebase bootstrap dependency drifted")


def _assert_bootstrap_role_settings(cursor: Any, application_id: str) -> None:
    cursor.execute(
        """
        SELECT rolconnlimit, rolvaliduntil, rolpassword, rolconfig
        FROM pg_roles
        WHERE rolname = %s
        """,
        (application_id,),
    )
    if list(cursor.fetchall()) != [(-1, None, "********", None)]:
        raise RuntimeError("temporary Lakebase bootstrap role setting contract drifted")
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
        raise RuntimeError("temporary Lakebase bootstrap has database-scoped settings")


def _assert_bootstrap_oauth_label(
    cursor: Any,
    *,
    application_id: str,
    service_principal_id: str | None,
) -> None:
    if service_principal_id is not None:
        assert_oauth_security_label(
            cursor,
            application_id=application_id,
            service_principal_id=service_principal_id,
        )
        return
    cursor.execute(
        """
        SELECT label.provider, label.label
        FROM pg_roles role
        LEFT JOIN pg_shseclabel label
          ON label.classoid = 'pg_authid'::regclass
         AND label.objoid = role.oid
        WHERE role.rolname = %s
        ORDER BY label.provider, label.label
        """,
        (application_id,),
    )
    rows = list(cursor.fetchall())
    if (
        len(rows) != 1
        or rows[0][0] != "databricks_auth"
        or not re.fullmatch(
            r"id=[^,\s]+,type=service_principal",
            str(rows[0][1] or ""),
        )
    ):
        raise RuntimeError("temporary Lakebase bootstrap OAuth label drifted")


def bootstrap_oauth_label_service_principal_id(
    cursor: Any,
    application_id: str,
) -> str:
    """Return the immutable SCIM id from one exact provider OAuth label."""

    cursor.execute(
        """
        SELECT label.provider, label.label
        FROM pg_roles role
        LEFT JOIN pg_shseclabel label
          ON label.classoid = 'pg_authid'::regclass
         AND label.objoid = role.oid
        WHERE role.rolname = %s
        ORDER BY label.provider, label.label
        """,
        (application_id,),
    )
    rows = list(cursor.fetchall())
    if len(rows) != 1 or rows[0][0] != "databricks_auth":
        raise RuntimeError("temporary Lakebase bootstrap OAuth label drifted")
    match = re.fullmatch(
        r"id=(?P<service_principal_id>[^,\s]+),type=service_principal",
        str(rows[0][1] or ""),
    )
    if match is None:
        raise RuntimeError("temporary Lakebase bootstrap OAuth label drifted")
    return match.group("service_principal_id")


def assert_bootstrap_admission_identity(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    application_id: str,
    display_name: str,
    external_id: str,
    service_principal_id: str | None,
    signed_tombstone_authority: bool = False,
) -> bool:
    """Prove immutable identity markers before provider-owned role cleanup."""

    control_role = _control_plane_role(
        client,
        instance_name=instance_name,
        application_id=application_id,
    )
    profile = read_profile(cursor, application_id)
    if control_role is None and profile is None:
        return False
    if control_role is None or profile is None:
        raise RuntimeError("temporary Lakebase bootstrap admission identity drifted")
    identity_type = getattr(control_role, "identity_type", None)
    if str(getattr(identity_type, "value", identity_type) or "") != "SERVICE_PRINCIPAL":
        raise RuntimeError("temporary Lakebase bootstrap role identity type drifted")

    if service_principal_id is not None and not signed_tombstone_authority:
        exact = client.service_principals.get(service_principal_id)
        try:
            assert_bootstrap_principal_display_name(
                str(getattr(exact, "display_name", "") or ""),
                expected_name=display_name,
                ownership_marker=external_id,
            )
            assert_scim_external_id_unset(
                exact,
                label="temporary Lakebase bootstrap principal",
            )
        except RuntimeError as exc:
            raise RuntimeError("temporary Lakebase bootstrap principal contract drifted") from exc
        if (
            str(getattr(exact, "id", "") or "").strip() != service_principal_id
            or str(getattr(exact, "application_id", "") or "").strip() != application_id
            or any(getattr(exact, field, None) for field in ("groups", "roles", "entitlements"))
        ):
            raise RuntimeError("temporary Lakebase bootstrap principal contract drifted")
        if any(
            str(getattr(app, "service_principal_client_id", "") or "") == application_id
            for app in client.apps.list()
        ):
            raise RuntimeError("temporary Lakebase bootstrap principal is bound to an App")

    _assert_bootstrap_oauth_label(
        cursor,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    ownership_marker = _role_ownership_marker(cursor, application_id)
    allowed_markers = {external_id}
    if service_principal_id is not None or signed_tombstone_authority:
        allowed_markers.add(None)
    if ownership_marker not in allowed_markers:
        raise RuntimeError("temporary Lakebase bootstrap ownership marker drifted")
    return True


def _assert_bootstrap_role_contract(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    application_id: str,
    target_application_id: str,
    external_id: str,
    service_principal_id: str | None,
    expected_executor: str,
    expected_privileges: frozenset[str] | None = None,
    allow_absent_managed_event_triggers: bool = False,
    signed_tombstone_authority: bool = False,
    target_database_absent: bool = False,
) -> bool:
    control_role = _control_plane_role(
        client,
        instance_name=instance_name,
        application_id=application_id,
    )
    profile = read_profile(cursor, application_id)
    if control_role is None and profile is None:
        return False
    if control_role is None:
        raise RuntimeError("temporary Lakebase bootstrap role contract drifted")
    identity_type = getattr(control_role, "identity_type", None)
    if str(getattr(identity_type, "value", identity_type) or "") != "SERVICE_PRINCIPAL":
        raise RuntimeError("temporary Lakebase bootstrap role identity type drifted")
    if profile is None:
        raise RuntimeError("temporary Lakebase bootstrap SQL role is absent")
    _assert_bootstrap_role_settings(cursor, application_id)
    _assert_bootstrap_oauth_label(
        cursor,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )

    description = _role_ownership_marker(cursor, application_id)
    signed_scim_authority = service_principal_id is not None
    if description != external_id and not (
        description is None and (signed_scim_authority or signed_tombstone_authority)
    ):
        raise RuntimeError("temporary Lakebase bootstrap role ownership marker drifted")
    if profile != _BOOTSTRAP_API_PROFILE:
        raise RuntimeError("temporary Lakebase bootstrap role attribute profile drifted")

    relationships = _bootstrap_role_relationships(cursor, application_id)
    allowed_relationships = [
        (
            target_application_id,
            application_id,
            True,
            False,
            False,
            "cloud_admin",
        )
    ]
    if relationships not in ([], allowed_relationships):
        raise RuntimeError("temporary Lakebase bootstrap role relationship drifted")
    if target_database_absent:
        _assert_no_bootstrap_acl_dependencies(cursor, application_id=application_id)
    else:
        from tools.databricks.lakebase_oauth_role_bootstrap_recovery_contract import (
            assert_recoverable_bootstrap_dependencies,
        )

        assert_recoverable_bootstrap_dependencies(
            cursor,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=target_application_id,
            bootstrap_application_id=application_id,
            expected_executor=expected_executor,
        )
    if expected_privileges == frozenset():
        _assert_no_bootstrap_acl_dependencies(cursor, application_id=application_id)
        schema_name = wrapper_schema_name(
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=target_application_id,
        )
        cursor.execute("SELECT to_regnamespace(%s)", (schema_name,))
        if cursor.fetchone() != (None,):
            raise RuntimeError("temporary Lakebase bootstrap wrapper unexpectedly exists")
    elif expected_privileges is not None:
        assert_wrapper_contract(
            cursor,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=target_application_id,
            bootstrap_application_id=application_id,
            expected_executor=expected_executor,
            expected_privileges=expected_privileges,
        )
    return True
