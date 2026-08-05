"""Exact catalog contract for the target-bound Lakebase bootstrap wrapper."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from psycopg import sql as psql

_WRAPPER_FUNCTION = "create_target_role"
_WRAPPER_OWNER = "pg_database_owner"


@dataclass(frozen=True)
class WrapperFunctionFingerprint:
    """Caller-invariant identity for one already-canonical wrapper function."""

    catalog: tuple[Any, ...]


def canonical_wrapper_definition(
    *,
    schema_name: str,
    target_application_id: str,
    bootstrap_application_id: str,
) -> str:
    """Return PostgreSQL's exact deparse of the reviewed SQL-standard body."""

    target = psql.Literal(target_application_id).as_string()
    bootstrap = psql.Literal(bootstrap_application_id).as_string()
    return (
        f"CREATE OR REPLACE FUNCTION {schema_name}.{_WRAPPER_FUNCTION}()\n"
        " RETURNS text\n"
        " LANGUAGE sql\n"
        " SET search_path TO 'pg_catalog'\n"
        " SET createrole_self_grant TO ''\n"
        "BEGIN ATOMIC\n"
        f" RETURN ( SELECT databricks_create_role({target}::text, "
        "'SERVICE_PRINCIPAL'::text) AS databricks_create_role\n"
        f"           WHERE ((CURRENT_USER = {bootstrap}::name) AND "
        f"(SESSION_USER = {bootstrap}::name)));\n"
        "END\n"
    )


def _provider_function_dependency_contract(
    cursor: Any,
    *,
    function_oid: int,
) -> None:
    cursor.execute(
        """
        SELECT dependency.classid::regclass::text,
               dependency.objid,
               dependency.refclassid::regclass::text,
               referenced_namespace.nspname,
               referenced.proname,
               oidvectortypes(referenced.proargtypes),
               dependency.deptype
        FROM pg_depend dependency
        JOIN pg_proc referenced
          ON dependency.refclassid = 'pg_proc'::regclass
         AND referenced.oid = dependency.refobjid
        JOIN pg_namespace referenced_namespace
          ON referenced_namespace.oid = referenced.pronamespace
        WHERE dependency.classid = 'pg_proc'::regclass
          AND dependency.objid = %s
          AND dependency.refclassid = 'pg_proc'::regclass
        ORDER BY 1, 2, 3, 4, 5, 6, 7
        """,
        (function_oid,),
    )
    expected = [
        (
            "pg_proc",
            function_oid,
            "pg_proc",
            "public",
            "databricks_create_role",
            "text, text",
            "n",
        )
    ]
    if list(cursor.fetchall()) != expected:
        raise RuntimeError("temporary Lakebase bootstrap wrapper provider dependency drifted")


def wrapper_function_contract(
    cursor: Any,
    *,
    schema_name: str,
    target_application_id: str,
    bootstrap_application_id: str,
    allow_bootstrap_execute: bool,
    expected_fingerprint: WrapperFunctionFingerprint | None = None,
) -> tuple[int, str, WrapperFunctionFingerprint]:
    cursor.execute(
        """
        SELECT routine.oid,
               namespace.nspname,
               routine.proname,
               routine.prokind,
               oidvectortypes(routine.proargtypes),
               routine.pronargs,
               routine.pronargdefaults,
               routine.provariadic,
               routine.proallargtypes,
               routine.proargmodes,
               routine.proargnames,
               pg_get_function_result(routine.oid),
               owner.rolname,
               language.lanname,
               routine.provolatile,
               routine.proparallel,
               routine.prosecdef,
               routine.proleakproof,
               routine.proisstrict,
               routine.proconfig,
               routine.prosrc,
               routine.prosqlbody IS NOT NULL,
               routine.xmin::text,
               encode(sha256(convert_to(routine.prosqlbody::text, 'UTF8')), 'hex'),
               octet_length(convert_to(routine.prosqlbody::text, 'UTF8')),
               pg_get_functiondef(routine.oid),
               encode(sha256(convert_to(pg_get_functiondef(routine.oid), 'UTF8')), 'hex'),
               octet_length(convert_to(pg_get_functiondef(routine.oid), 'UTF8'))
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        JOIN pg_roles owner ON owner.oid = routine.proowner
        JOIN pg_language language ON language.oid = routine.prolang
        WHERE namespace.nspname = %s
        ORDER BY routine.proname, oidvectortypes(routine.proargtypes)
        """,
        (schema_name,),
    )
    rows = list(cursor.fetchall())
    if len(rows) != 1:
        raise RuntimeError("temporary Lakebase bootstrap wrapper function inventory drifted")
    row = rows[0]
    invariant = list(row[:25])
    invariant[19] = tuple(sorted(str(item) for item in (invariant[19] or ())))
    fingerprint = WrapperFunctionFingerprint(catalog=tuple(invariant))
    actual_shape = list(row[1:22])
    actual_shape[18] = tuple(sorted(str(item) for item in (actual_shape[18] or ())))
    expected_shape = (
        schema_name,
        _WRAPPER_FUNCTION,
        "f",
        "",
        0,
        0,
        0,
        None,
        None,
        None,
        "text",
        _WRAPPER_OWNER,
        "sql",
        "v",
        "u",
        False,
        False,
        False,
        ("createrole_self_grant=", "search_path=pg_catalog"),
        "",
        True,
    )
    if tuple(actual_shape) != expected_shape:
        raise RuntimeError("temporary Lakebase bootstrap wrapper function contract drifted")
    if expected_fingerprint is None:
        definition = canonical_wrapper_definition(
            schema_name=schema_name,
            target_application_id=target_application_id,
            bootstrap_application_id=bootstrap_application_id,
        )
        definition_bytes = definition.encode()
        expected_definition = (
            definition,
            hashlib.sha256(definition_bytes).hexdigest(),
            len(definition_bytes),
        )
        if tuple(row[25:]) != expected_definition:
            raise RuntimeError("temporary Lakebase bootstrap wrapper function contract drifted")
    elif fingerprint != expected_fingerprint:
        raise RuntimeError("temporary Lakebase bootstrap wrapper function changed after publication")
    function_oid, owner = int(row[0]), str(row[12])

    cursor.execute(
        """
        SELECT CASE WHEN acl.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
               acl.privilege_type,
               acl.is_grantable,
               grantor.rolname
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        CROSS JOIN LATERAL aclexplode(routine.proacl) acl
        LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
        JOIN pg_roles grantor ON grantor.oid = acl.grantor
        WHERE namespace.nspname = %s
          AND routine.proname = %s
          AND oidvectortypes(routine.proargtypes) = ''
        ORDER BY 1, 2, 3, 4
        """,
        (schema_name, _WRAPPER_FUNCTION),
    )
    actual_acl = {tuple(item) for item in cursor.fetchall()}
    expected_acl = {(owner, "EXECUTE", False, owner)}
    if allow_bootstrap_execute:
        expected_acl.add((bootstrap_application_id, "EXECUTE", False, owner))
    if actual_acl != expected_acl:
        raise RuntimeError("temporary Lakebase bootstrap wrapper function ACL drifted")
    _provider_function_dependency_contract(cursor, function_oid=function_oid)
    return function_oid, owner, fingerprint
