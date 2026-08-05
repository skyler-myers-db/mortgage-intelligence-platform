"""Shared SQL and control-plane identity checks for Lakebase OAuth roles."""

from __future__ import annotations

from typing import Any


def read_profile(cursor: Any, application_id: str) -> tuple[bool, ...] | None:
    cursor.execute(
        """
        SELECT rolsuper,
               rolcreaterole,
               rolcreatedb,
               rolreplication,
               rolbypassrls,
               rolinherit,
               rolcanlogin
        FROM pg_roles
        WHERE rolname = %s
        """,
        (application_id,),
    )
    row = cursor.fetchone()
    return tuple(row) if row is not None else None


def assert_oauth_security_label(
    cursor: Any,
    *,
    application_id: str,
    service_principal_id: str,
) -> None:
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
    expected = [("databricks_auth", f"id={service_principal_id},type=service_principal")]
    if cursor.fetchall() != expected:
        raise RuntimeError(f"Lakebase role {application_id!r} has an invalid OAuth security label")


def assert_service_principal_metadata(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
) -> None:
    role = client.database.get_database_instance_role(instance_name, application_id)
    value = getattr(role, "identity_type", None)
    identity_type = str(getattr(value, "value", value) or "")
    if identity_type != "SERVICE_PRINCIPAL":
        raise RuntimeError(
            f"Lakebase role {application_id!r} has identity_type={identity_type or 'absent'!r}; "
            "only a SERVICE_PRINCIPAL OAuth role is permitted"
        )


def resolve_service_principal_id(client: Any, application_id: str) -> str:
    principals = [
        principal
        for principal in client.service_principals.list(
            filter=f'applicationId eq "{application_id}"'
        )
        if str(getattr(principal, "application_id", "") or "") == application_id
    ]
    if len(principals) != 1:
        raise RuntimeError(
            f"expected one exact Databricks service principal for {application_id!r}; "
            f"found {len(principals)}"
        )
    principal_id = str(getattr(principals[0], "id", "") or "").strip()
    if not principal_id:
        raise RuntimeError("Databricks service principal has no immutable SCIM id")
    return principal_id
