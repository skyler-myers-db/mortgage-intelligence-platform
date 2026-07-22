"""Fail-closed runtime proof for the Lakebase OAuth identity boundary."""

from __future__ import annotations

import ssl
from collections.abc import Callable
from typing import Any

import psycopg
from pg8000.dbapi import DatabaseError as Pg8000DatabaseError
from pg8000.dbapi import connect as pg8000_connect

SAFE_OAUTH_PROFILE = (False, False, False, False, False, True, True)
_PRIVILEGE_SQLSTATE = "42501"


class LakebaseIdentityGateError(RuntimeError):
    """Raised when a runtime identity cannot prove the exact safe boundary."""


def _is_explicit_replication_denial(exc: BaseException) -> bool:
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    if not sqlstate and isinstance(exc, Pg8000DatabaseError) and len(exc.args) == 1:
        fields = exc.args[0]
        if isinstance(fields, dict):
            sqlstate = str(fields.get("C") or "")
    # Only a PostgreSQL ErrorResponse with insufficient_privilege is proof.
    # Client-side OperationalError text can contain "permission denied" for
    # sockets, proxies, TLS files, or firewalls and must remain inconclusive.
    return sqlstate == _PRIVILEGE_SQLSTATE


def _structured_replication_connect(**kwargs: Any) -> Any:
    """Open a replication startup probe that retains server ErrorResponse fields."""

    expected_keys = {
        "host",
        "port",
        "dbname",
        "user",
        "password",
        "sslmode",
        "connect_timeout",
        "replication",
    }
    text_keys = ("host", "dbname", "user", "password")
    if (
        set(kwargs) != expected_keys
        or any(not isinstance(kwargs[key], str) or not kwargs[key] for key in text_keys)
        or kwargs["port"] != 5432
        or kwargs["sslmode"] != "require"
        or kwargs["connect_timeout"] != 15
        or kwargs["replication"] != "database"
    ):
        raise LakebaseIdentityGateError(
            "Lakebase structured replication probe is misconfigured"
        )
    return pg8000_connect(
        host=kwargs["host"],
        port=kwargs["port"],
        database=kwargs["dbname"],
        user=kwargs["user"],
        password=kwargs["password"],
        replication=kwargs["replication"],
        timeout=float(kwargs["connect_timeout"]),
        ssl_context=ssl.create_default_context(),
    )


def _connection_kwargs(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sslmode: str,
) -> dict[str, Any]:
    if not all((host, database, user, password)):
        raise LakebaseIdentityGateError("Lakebase identity gate has incomplete credentials")
    return {
        "host": host,
        "port": port,
        "dbname": database,
        "user": user,
        "password": password,
        "sslmode": sslmode,
        "connect_timeout": 15,
    }


def verify_lakebase_oauth_identity(
    *,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sslmode: str,
    expected_application_id: str,
    expected_service_principal_id: str,
    connect: Callable[..., Any] = psycopg.connect,
    replication_connect: Callable[..., Any] = _structured_replication_connect,
) -> None:
    """Prove normal access and replication denial for one exact OAuth role."""
    kwargs = _connection_kwargs(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        sslmode=sslmode,
    )
    if user != expected_application_id:
        raise LakebaseIdentityGateError("Lakebase database user is not the expected application id")

    try:
        with connect(**kwargs) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user,
                       session_user,
                       role.rolname,
                       role.rolsuper,
                       role.rolcreaterole,
                       role.rolcreatedb,
                       role.rolreplication,
                       role.rolbypassrls,
                       role.rolinherit,
                       role.rolcanlogin
                FROM pg_roles role
                WHERE role.rolname = current_user
                """
            )
            row = cursor.fetchone()
            if row is None or tuple(str(value) for value in row[:3]) != (
                expected_application_id,
                expected_application_id,
                expected_application_id,
            ):
                raise LakebaseIdentityGateError("Lakebase current_user identity mismatch")
            if tuple(row[3:]) != SAFE_OAUTH_PROFILE:
                raise LakebaseIdentityGateError("Lakebase OAuth role has unsafe attributes")

            cursor.execute(
                """
                SELECT role.rolconnlimit,
                       role.rolvaliduntil,
                       role.rolpassword,
                       role.rolconfig
                FROM pg_roles role
                WHERE role.rolname = current_user
                """
            )
            if cursor.fetchone() != (-1, None, "********", None):
                raise LakebaseIdentityGateError("Lakebase OAuth role has unsafe settings")
            cursor.execute(
                """
                SELECT setting.setdatabase, setting.setrole, setting.setconfig
                FROM pg_db_role_setting setting
                JOIN pg_roles role ON role.oid = setting.setrole
                WHERE role.rolname = current_user
                ORDER BY setting.setdatabase, setting.setrole
                """
            )
            if cursor.fetchall():
                raise LakebaseIdentityGateError("Lakebase OAuth role has database-scoped settings")

            cursor.execute(
                """
                SELECT label.provider, label.label
                FROM pg_roles role
                LEFT JOIN pg_shseclabel label
                  ON label.classoid = 'pg_authid'::regclass
                 AND label.objoid = role.oid
                WHERE role.rolname = current_user
                ORDER BY label.provider, label.label
                """
            )
            expected_label = [
                (
                    "databricks_auth",
                    f"id={expected_service_principal_id},type=service_principal",
                )
            ]
            if cursor.fetchall() != expected_label:
                raise LakebaseIdentityGateError("Lakebase OAuth security label mismatch")

            cursor.execute(
                """
                SELECT 1
                FROM pg_auth_members membership
                JOIN pg_roles runtime_role ON runtime_role.rolname = current_user
                WHERE membership.roleid = runtime_role.oid
                   OR membership.member = runtime_role.oid
                LIMIT 1
                """
            )
            if cursor.fetchone() is not None:
                raise LakebaseIdentityGateError("Lakebase OAuth role has a role relationship")

            cursor.execute(
                """
                SELECT has_schema_privilege(current_user, 'public', 'USAGE'),
                       has_schema_privilege(current_user, 'public', 'CREATE')
                """
            )
            if cursor.fetchone() != (False, False):
                raise LakebaseIdentityGateError(
                    "Lakebase OAuth role has unsafe public schema privileges"
                )
    except LakebaseIdentityGateError:
        raise
    except Exception as exc:  # noqa: BLE001 - psycopg subclasses vary by server path
        raise LakebaseIdentityGateError("Lakebase normal identity proof failed") from exc

    replication_kwargs = dict(kwargs)
    replication_kwargs["replication"] = "database"
    try:
        replication_connection = replication_connect(**replication_kwargs)
    except Exception as exc:  # noqa: BLE001 - denial can occur during startup packet
        if _is_explicit_replication_denial(exc):
            return
        raise LakebaseIdentityGateError("Lakebase replication denial was inconclusive") from exc

    try:
        with replication_connection as connection, connection.cursor() as cursor:
            try:
                cursor.execute("IDENTIFY_SYSTEM")
            except Exception as exc:  # noqa: BLE001 - command denial is the expected path
                if _is_explicit_replication_denial(exc):
                    return
                raise LakebaseIdentityGateError(
                    "Lakebase replication command denial was inconclusive"
                ) from exc
            raise LakebaseIdentityGateError(
                "Lakebase replication protocol unexpectedly accepted IDENTIFY_SYSTEM"
            )
    finally:
        close = getattr(replication_connection, "close", None)
        if callable(close):
            close()


def verify_app_lakebase_identity_at_startup() -> None:
    """Run the exact proof under the ambient Databricks App identity."""
    from databricks.sdk import WorkspaceClient

    from backend.services.lakebase import _resolve_lakebase_connection_params

    host, port, database, user, password, sslmode, password_provider = (
        _resolve_lakebase_connection_params()
    )
    workspace = WorkspaceClient()
    me = workspace.current_user.me()
    authenticated_ids = {
        str(getattr(me, field, "") or "").strip() for field in ("application_id", "user_name")
    }
    if user not in authenticated_ids:
        raise LakebaseIdentityGateError(
            "ambient Databricks App identity does not match the Lakebase database user"
        )
    service_principal_id = str(getattr(me, "id", "") or "").strip()
    if not service_principal_id:
        raise LakebaseIdentityGateError("ambient Databricks App identity has no immutable id")
    effective_password = password_provider() if password_provider is not None else password
    verify_lakebase_oauth_identity(
        host=host,
        port=port,
        database=database,
        user=user,
        password=effective_password,
        sslmode=sslmode,
        expected_application_id=user,
        expected_service_principal_id=service_principal_id,
    )
