"""Failure-atomic reconciliation of Lakebase runtime ACLs."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from jobs.lakebase_migration_contracts import (
    _AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES,
    _APP_ROLE_ROUTINE_PRIVILEGES,
    _APP_ROLE_SEQUENCE_PRIVILEGES,
    _APP_ROLE_TABLE_PRIVILEGES,
    _MANAGED_PROVIDER_PUBLIC_ROUTINE_IDENTITIES,
    _MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT,
)
from jobs.lakebase_migration_postflight import (
    _postflight_ai_gateway_verifier_grants,
    _postflight_app_role_grants,
)
from jobs.lakebase_migration_provider_plane import _postflight_provider_schema_boundary
from jobs.lakebase_migration_roles import (
    _resolve_ai_gateway_verifier_role,
    _resolve_app_role,
)
from jobs.lakebase_migration_schema_hooks import (
    _postflight_event_trigger_inventory,
    _postflight_oauth_role_function_contract,
)

_ResolvedDatabaseRoles = tuple[str, str | None]


def _resolve_database_roles(
    *,
    app_name: str | None,
    _resolve_app_role_fn: Callable[..., str],
    _resolve_verifier_role_fn: Callable[[], str | None],
) -> _ResolvedDatabaseRoles:
    role = (
        _resolve_app_role_fn()
        if app_name is None
        else _resolve_app_role_fn(app_name=app_name)
    )
    verifier_role = _resolve_verifier_role_fn()
    if verifier_role == role:
        raise RuntimeError(
            "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID must identify a role distinct "
            "from the Databricks App runtime role"
        )
    return role, verifier_role


def _role_wait_settings(
    *,
    role_wait_timeout_s: float | None,
    role_wait_interval_s: float | None,
) -> tuple[float, float]:
    timeout_s = (
        float(os.environ.get("MIP_LAKEBASE_APP_ROLE_WAIT_TIMEOUT_S", "120"))
        if role_wait_timeout_s is None
        else role_wait_timeout_s
    )
    interval_s = (
        float(os.environ.get("MIP_LAKEBASE_APP_ROLE_WAIT_INTERVAL_S", "5"))
        if role_wait_interval_s is None
        else role_wait_interval_s
    )
    if timeout_s < 0 or interval_s <= 0:
        raise ValueError("Lakebase app-role wait settings must be timeout >= 0 and interval > 0")
    return timeout_s, interval_s


def _await_authoritative_database_roles(
    cur: object,
    roles: _ResolvedDatabaseRoles,
    *,
    timeout_s: float,
    interval_s: float,
) -> None:
    app_role, verifier_role = roles
    required_roles = [("app", app_role)]
    if verifier_role is not None:
        required_roles.append(("AI Gateway verifier", verifier_role))
    for role_label, database_role in required_roles:
        deadline = time.monotonic() + timeout_s
        while True:
            cur.execute(  # type: ignore[attr-defined]
                "SELECT rolname FROM pg_roles WHERE rolname = %s",
                (database_role,),
            )
            present = cur.fetchall()  # type: ignore[attr-defined]
            if present == [(database_role,)]:
                break
            if present:
                raise RuntimeError(
                    "Lakebase role lookup returned a non-exact identity for "
                    f"{database_role!r}: {present}"
                )
            now = time.monotonic()
            if now >= deadline:
                raise RuntimeError(
                    f"authoritative {role_label} role not found in pg_roles "
                    f"before the Lakebase grant timeout: {database_role!r}"
                )
            wait_s = min(interval_s, max(0.0, deadline - now))
            print(
                f"[lakebase-migrate] authoritative {role_label} database role "
                f"not visible yet; retrying in {wait_s:g}s"
            )
            time.sleep(wait_s)


def _preflight_database_roles(
    conn_kwargs: dict,
    *,
    app_name: str | None = None,
    role_wait_timeout_s: float | None = None,
    role_wait_interval_s: float | None = None,
    _resolve_app_role_fn: Callable[..., str] = _resolve_app_role,
    _resolve_verifier_role_fn: Callable[[], str | None] = _resolve_ai_gateway_verifier_role,
) -> _ResolvedDatabaseRoles:
    """Resolve and prove runtime roles exist without opening a mutable transaction."""

    import psycopg

    roles = _resolve_database_roles(
        app_name=app_name,
        _resolve_app_role_fn=_resolve_app_role_fn,
        _resolve_verifier_role_fn=_resolve_verifier_role_fn,
    )
    timeout_s, interval_s = _role_wait_settings(
        role_wait_timeout_s=role_wait_timeout_s,
        role_wait_interval_s=role_wait_interval_s,
    )
    conn = psycopg.connect(**conn_kwargs, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            _await_authoritative_database_roles(
                cur,
                roles,
                timeout_s=timeout_s,
                interval_s=interval_s,
            )
    finally:
        conn.rollback()
        conn.close()
    return roles


def _apply_app_role_grants(
    conn_kwargs: dict,
    *,
    app_name: str | None = None,
    resolved_roles: _ResolvedDatabaseRoles | None = None,
    role_wait_timeout_s: float | None = None,
    role_wait_interval_s: float | None = None,
    allow_absent_managed_event_triggers: bool = False,
    allow_absent_provider_schema: bool = False,
    _resolve_app_role_fn: Callable[..., str] = _resolve_app_role,
    _resolve_verifier_role_fn: Callable[[], str | None] = _resolve_ai_gateway_verifier_role,
) -> None:
    import psycopg
    from psycopg import sql as psql

    roles = resolved_roles or _resolve_database_roles(
        app_name=app_name,
        _resolve_app_role_fn=_resolve_app_role_fn,
        _resolve_verifier_role_fn=_resolve_verifier_role_fn,
    )
    role, verifier_role = roles
    if verifier_role == role:
        raise RuntimeError(
            "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID must identify a role distinct "
            "from the Databricks App runtime role"
        )
    timeout_s, interval_s = _role_wait_settings(
        role_wait_timeout_s=role_wait_timeout_s,
        role_wait_interval_s=role_wait_interval_s,
    )

    conn = psycopg.connect(**conn_kwargs, autocommit=False)
    try:
        with conn.cursor() as cur:
            _await_authoritative_database_roles(
                cur,
                roles,
                timeout_s=timeout_s,
                interval_s=interval_s,
            )

            # End the read-only role-discovery transaction before starting the
            # failure-atomic ACL reconciliation transaction.
            conn.commit()

            cur.execute("SELECT current_database()")
            database_row = cur.fetchone()
            if database_row is None:
                raise RuntimeError("Lakebase current database lookup returned no row")
            database_name = str(database_row[0])
            target_roles = tuple(
                target_role
                for target_role in (role, verifier_role)
                if target_role is not None
            )
            _postflight_provider_schema_boundary(
                cur,
                target_roles,
                principal_label="ACL preflight",
                allow_absent_provider_schema=allow_absent_provider_schema,
            )
            _postflight_oauth_role_function_contract(
                cur,
                principal_label="ACL preflight",
                allow_absent_managed=allow_absent_managed_event_triggers,
            )

            role_identifier = psql.Identifier(role).as_string()
            verifier_role_identifier = (
                psql.Identifier(verifier_role).as_string() if verifier_role is not None else None
            )
            database_identifier = psql.Identifier(database_name).as_string()
            schema_identifier = psql.Identifier("mip_app").as_string()
            table_identifiers = {
                table: psql.Identifier("mip_app", table).as_string()
                for table in _APP_ROLE_TABLE_PRIVILEGES
            }
            sequence_identifiers = {
                sequence: psql.Identifier("mip_app", sequence).as_string()
                for sequence in _APP_ROLE_SEQUENCE_PRIVILEGES
            }
            cur.execute(
                """
                SELECT n.nspname
                FROM pg_namespace n
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname !~ '^pg_'
                  AND n.nspname <> '__db_system'
                ORDER BY n.nspname
                """
            )
            all_schema_identifiers = [
                psql.Identifier(str(row[0])).as_string() for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT n.nspname, c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname !~ '^pg_'
                  AND n.nspname <> '__db_system'
                  AND NOT (
                      n.nspname = 'public'
                      AND c.relname = ANY(%s::text[])
                  )
                ORDER BY n.nspname, c.relname
                """,
                (sorted(_MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT),),
            )
            all_table_identifiers = [
                psql.Identifier(str(row[0]), str(row[1])).as_string() for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT n.nspname, c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'S'
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname !~ '^pg_'
                  AND n.nspname <> '__db_system'
                ORDER BY n.nspname, c.relname
                """
            )
            all_sequence_identifiers = [
                psql.Identifier(str(row[0]), str(row[1])).as_string() for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT
                    CASE WHEN p.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
                    format(
                        '%I.%I(%s)',
                        n.nspname,
                        p.proname,
                        oidvectortypes(p.proargtypes)
                    ),
                    n.nspname,
                    p.proname,
                    oidvectortypes(p.proargtypes),
                    p.prosecdef,
                    owner.rolname
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                JOIN pg_roles owner ON owner.oid = p.proowner
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname !~ '^pg_'
                  AND n.nspname <> '__db_system'
                ORDER BY
                    n.nspname,
                    p.proname,
                    oidvectortypes(p.proargtypes)
                """
            )
            all_routines = [
                (
                    str(object_kind),
                    str(identity),
                    str(schema),
                    str(name),
                    str(arguments),
                    bool(security_definer),
                    str(owner),
                )
                for (
                    object_kind,
                    identity,
                    schema,
                    name,
                    arguments,
                    security_definer,
                    owner,
                ) in cur.fetchall()
            ]
            actual_mip_app_routines = {
                (name, arguments)
                for (
                    _kind,
                    _identity,
                    schema,
                    name,
                    arguments,
                    _security_definer,
                    _owner,
                ) in all_routines
                if schema == "mip_app"
            }
            expected_mip_app_routines = set(_APP_ROLE_ROUTINE_PRIVILEGES)
            if actual_mip_app_routines != expected_mip_app_routines:
                raise RuntimeError(
                    "Lakebase routine inventory mismatch: "
                    f"missing={sorted(expected_mip_app_routines - actual_mip_app_routines)}, "
                    f"unexpected={sorted(actual_mip_app_routines - expected_mip_app_routines)}"
                )
            unsafe_reviewed_routines = sorted(
                (name, arguments)
                for (
                    _kind,
                    _identity,
                    schema,
                    name,
                    arguments,
                    security_definer,
                    _owner,
                ) in all_routines
                if schema == "mip_app"
                and security_definer
                and (name, arguments) in _APP_ROLE_ROUTINE_PRIVILEGES
            )
            if unsafe_reviewed_routines:
                raise RuntimeError(
                    "Lakebase reviewed routines must remain SECURITY INVOKER: "
                    f"{unsafe_reviewed_routines}"
                )
            routine_creator_identifiers = sorted(
                {
                    psql.Identifier(owner).as_string()
                    for (
                        _kind,
                        _identity,
                        schema,
                        _name,
                        _arguments,
                        _security_definer,
                        owner,
                    ) in all_routines
                    if schema == "mip_app"
                }
            )
            target_roles = [role]
            if verifier_role is not None:
                target_roles.append(verifier_role)
            role_placeholders = ", ".join("%s" for _target_role in target_roles)
            target_role_identifiers = {
                role: role_identifier,
                **(
                    {verifier_role: verifier_role_identifier}
                    if verifier_role is not None and verifier_role_identifier is not None
                    else {}
                ),
            }
            cur.execute(
                f"""
                SELECT DISTINCT
                    CASE WHEN e.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
                    n.nspname,
                    c.relname,
                    a.attname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid
                CROSS JOIN LATERAL aclexplode(a.attacl) e
                LEFT JOIN pg_roles grantee ON grantee.oid = e.grantee
                WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname !~ '^pg_'
                  AND n.nspname <> '__db_system'
                  AND NOT (
                      n.nspname = 'public'
                      AND c.relname = ANY(%s::text[])
                  )
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                  AND (
                      e.grantee = 0
                      OR grantee.rolname IN ({role_placeholders})
                  )
                ORDER BY n.nspname, c.relname, a.attname
                """,
                (sorted(_MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT), *target_roles),
            )
            column_acl_revokes = [
                (
                    (
                        "PUBLIC"
                        if str(grantee) == "PUBLIC"
                        else target_role_identifiers[str(grantee)]
                    ),
                    psql.Identifier(str(schema), str(table)).as_string(),
                    psql.Identifier(str(column)).as_string(),
                )
                for grantee, schema, table, column in cur.fetchall()
            ]
            cur.execute(
                f"""
                SELECT
                    CASE WHEN e.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
                    owner.rolname,
                    n.nspname,
                    d.defaclobjtype
                FROM pg_default_acl d
                JOIN pg_roles owner ON owner.oid = d.defaclrole
                LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
                CROSS JOIN LATERAL aclexplode(d.defaclacl) e
                LEFT JOIN pg_roles grantee ON grantee.oid = e.grantee
                WHERE d.defaclobjtype IN ('r', 'S', 'f')
                  AND (
                      grantee.rolname IN ({role_placeholders})
                  )
                  AND (
                      d.defaclnamespace = 0
                      OR (
                          n.nspname NOT IN ('pg_catalog', 'information_schema')
                          AND n.nspname !~ '^pg_'
                          AND n.nspname <> '__db_system'
                      )
                )
                GROUP BY
                    CASE WHEN e.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
                    owner.rolname,
                    n.nspname,
                    d.defaclobjtype
                ORDER BY
                    CASE WHEN e.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
                    owner.rolname,
                    n.nspname,
                    d.defaclobjtype
                """,
                tuple(target_roles),
            )
            default_acl_revokes = [
                (
                    (
                        "PUBLIC"
                        if str(grantee) == "PUBLIC"
                        else target_role_identifiers[str(grantee)]
                    ),
                    psql.Identifier(str(owner)).as_string(),
                    (
                        psql.Identifier(str(default_schema)).as_string()
                        if default_schema is not None
                        else None
                    ),
                    (
                        "TABLES"
                        if object_type == "r"
                        else "SEQUENCES"
                        if object_type == "S"
                        else "FUNCTIONS"
                    ),
                )
                for grantee, owner, default_schema, object_type in cur.fetchall()
            ]

            # GRANT, REVOKE, and ALTER DEFAULT PRIVILEGES are DDL and can fire
            # PostgreSQL event triggers. Prove the exact managed contract
            # before the first ACL mutation rather than relying on the later
            # row-trigger inventory, which cannot see pg_event_trigger.
            _postflight_event_trigger_inventory(
                cur,
                role,
                principal_label="ACL preflight",
                allow_absent_managed=allow_absent_managed_event_triggers,
            )

            # Remove prior broad/direct/default access before adding the exact
            # current matrix. All revokes run before the first grant.
            # This is a dedicated application-state database: PUBLIC and both
            # runtime identities must be unable to create temporary relations.
            cur.execute(f"REVOKE TEMPORARY ON DATABASE {database_identifier} FROM PUBLIC")
            cur.execute(f"REVOKE CREATE ON DATABASE {database_identifier} FROM {role_identifier}")
            cur.execute(
                f"REVOKE TEMPORARY ON DATABASE {database_identifier} FROM {role_identifier}"
            )
            for existing_schema_identifier in all_schema_identifiers:
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON SCHEMA {existing_schema_identifier} "
                    f"FROM {role_identifier}"
                )
            for existing_table_identifier in all_table_identifiers:
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON TABLE {existing_table_identifier} "
                    f"FROM {role_identifier}"
                )
            for target_identifier, table_identifier, column_identifier in column_acl_revokes:
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ({column_identifier}) ON TABLE "
                    f"{table_identifier} FROM {target_identifier}"
                )
            for existing_sequence_identifier in all_sequence_identifiers:
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON SEQUENCE {existing_sequence_identifier} "
                    f"FROM {role_identifier}"
                )
            for (
                routine_kind,
                routine_identity,
                routine_schema,
                _routine_name,
                _routine_arguments,
                _security_definer,
                routine_owner,
            ) in all_routines:
                if (
                    (routine_schema, _routine_name, _routine_arguments)
                    in _MANAGED_PROVIDER_PUBLIC_ROUTINE_IDENTITIES
                    and routine_owner == "cloud_admin"
                ):
                    continue
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON {routine_kind} {routine_identity} "
                    f"FROM {role_identifier}"
                )
                # Lakebase is an isolated app-state database.  Its reviewed
                # policy deliberately removes PostgreSQL's built-in PUBLIC
                # EXECUTE default from every user routine so a SECURITY
                # DEFINER helper cannot become an ambient privilege tunnel.
                cur.execute(
                    f"REVOKE ALL PRIVILEGES ON {routine_kind} {routine_identity} FROM PUBLIC"
                )
            if verifier_role_identifier is not None:
                cur.execute(
                    f"REVOKE CREATE ON DATABASE {database_identifier} "
                    f"FROM {verifier_role_identifier}"
                )
                cur.execute(
                    f"REVOKE TEMPORARY ON DATABASE {database_identifier} "
                    f"FROM {verifier_role_identifier}"
                )
                for existing_schema_identifier in all_schema_identifiers:
                    cur.execute(
                        f"REVOKE ALL PRIVILEGES ON SCHEMA {existing_schema_identifier} "
                        f"FROM {verifier_role_identifier}"
                    )
                for existing_table_identifier in all_table_identifiers:
                    cur.execute(
                        f"REVOKE ALL PRIVILEGES ON TABLE {existing_table_identifier} "
                        f"FROM {verifier_role_identifier}"
                    )
                for existing_sequence_identifier in all_sequence_identifiers:
                    cur.execute(
                        f"REVOKE ALL PRIVILEGES ON SEQUENCE {existing_sequence_identifier} "
                        f"FROM {verifier_role_identifier}"
                    )
                for (
                    routine_kind,
                    routine_identity,
                    routine_schema,
                    _routine_name,
                    _routine_arguments,
                    _security_definer,
                    routine_owner,
                ) in all_routines:
                    if (
                        (routine_schema, _routine_name, _routine_arguments)
                        in _MANAGED_PROVIDER_PUBLIC_ROUTINE_IDENTITIES
                        and routine_owner == "cloud_admin"
                    ):
                        continue
                    cur.execute(
                        f"REVOKE ALL PRIVILEGES ON {routine_kind} {routine_identity} "
                        f"FROM {verifier_role_identifier}"
                    )

            for creator_identifier in routine_creator_identifiers:
                cur.execute(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {creator_identifier} "
                    "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
                )

            for (
                target_identifier,
                owner_identifier,
                default_schema_identifier,
                object_kind,
            ) in default_acl_revokes:
                in_schema = (
                    f" IN SCHEMA {default_schema_identifier}"
                    if default_schema_identifier is not None
                    else ""
                )
                cur.execute(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_identifier}{in_schema} "
                    f"REVOKE ALL PRIVILEGES ON {object_kind} FROM {target_identifier}"
                )

            cur.execute(f"GRANT USAGE ON SCHEMA {schema_identifier} TO {role_identifier}")
            for table, privileges in _APP_ROLE_TABLE_PRIVILEGES.items():
                if not privileges:
                    continue
                cur.execute(
                    f"GRANT {', '.join(privileges)} ON TABLE "
                    f"{table_identifiers[table]} TO {role_identifier}"
                )
            for sequence, privileges in _APP_ROLE_SEQUENCE_PRIVILEGES.items():
                cur.execute(
                    f"GRANT {', '.join(privileges)} ON SEQUENCE "
                    f"{sequence_identifiers[sequence]} TO {role_identifier}"
                )
            routine_identifiers = {
                (name, arguments): identity
                for (
                    _kind,
                    identity,
                    schema,
                    name,
                    arguments,
                    _security_definer,
                    _owner,
                ) in all_routines
                if schema == "mip_app"
            }
            for routine, privileges in _APP_ROLE_ROUTINE_PRIVILEGES.items():
                if not privileges:
                    continue
                cur.execute(
                    f"GRANT {', '.join(privileges)} ON FUNCTION "
                    f"{routine_identifiers[routine]} TO {role_identifier}"
                )
            if verifier_role_identifier is not None:
                cur.execute(
                    f"GRANT USAGE ON SCHEMA {schema_identifier} " f"TO {verifier_role_identifier}"
                )
                cur.execute(
                    "GRANT "
                    f"{', '.join(_AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES)} "
                    f"ON TABLE {table_identifiers['ai_gateway_proof_ledger']} "
                    f"TO {verifier_role_identifier}"
                )

            _postflight_app_role_grants(cur, role)
            if verifier_role is not None:
                _postflight_ai_gateway_verifier_grants(cur, verifier_role)
            _postflight_event_trigger_inventory(
                cur,
                role,
                principal_label="ACL postflight",
                allow_absent_managed=allow_absent_managed_event_triggers,
            )
            _postflight_oauth_role_function_contract(
                cur,
                principal_label="ACL postflight",
                allow_absent_managed=allow_absent_managed_event_triggers,
            )
            _postflight_provider_schema_boundary(
                cur,
                target_roles,
                principal_label="ACL postflight",
                allow_absent_provider_schema=allow_absent_provider_schema,
            )
            conn.commit()
            verifier_summary = (
                f"; AI Gateway verifier grants applied to {verifier_role!r}"
                if verifier_role is not None
                else "; verifier role omitted for dev/test"
            )
            print(f"[lakebase-migrate] app-role grants applied to {role!r}" f"{verifier_summary}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
