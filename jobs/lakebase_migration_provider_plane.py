"""Databricks-owned Lakebase namespace and runtime-role boundary proofs."""

from __future__ import annotations

from collections.abc import Sequence

from jobs.lakebase_migration_contracts import (
    _COLUMN_PRIVILEGE_NAMES,
    _MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT,
    _PROVIDER_DATABASE_WRITER_ROLE_PREFIX,
    _PROVIDER_SCHEMA_NAME,
    _PROVIDER_SCHEMA_OWNER,
    _SCHEMA_PRIVILEGE_NAMES,
    _SEQUENCE_PRIVILEGE_NAMES,
    _TABLE_PRIVILEGE_NAMES,
)


def _postflight_public_schema_boundary(
    cur: object,
    target_roles: Sequence[str],
    *,
    principal_label: str,
    allow_legacy_public_usage: bool,
    allow_absent_provider_schema: bool = False,
    allow_empty_target_roles: bool = False,
) -> None:
    """Bind the managed ``public`` schema ACL and runtime lookup boundary.

    Lakebase installs provider-owned routines in ``public``. Some protected
    routines reject ACL mutation even from a workspace admin, so the dedicated
    application database removes PUBLIC schema lookup instead of pretending it
    can rewrite every provider object. The preflight accepts the one legacy
    PUBLIC-USAGE row only so the same transaction can remove it; postflight
    requires the hardened ACL exactly.
    """

    if allow_absent_provider_schema:
        return
    roles = tuple(dict.fromkeys(str(role) for role in target_roles if role))
    if not roles and not allow_empty_target_roles:
        raise RuntimeError(
            f"Lakebase {principal_label} public-schema postflight has no target roles"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT namespace.nspname, owner.rolname, database_object.oid
        FROM pg_namespace namespace
        JOIN pg_roles owner ON owner.oid = namespace.nspowner
        CROSS JOIN pg_database database_object
        WHERE namespace.nspname = 'public'
          AND database_object.datname = current_database()
        """
    )
    schema_rows = list(cur.fetchall())  # type: ignore[attr-defined]
    if len(schema_rows) != 1:
        raise RuntimeError(
            f"Lakebase {principal_label} public-schema inventory mismatch: "
            f"rows={len(schema_rows)}"
        )
    schema_name, schema_owner, database_oid = schema_rows[0]
    if schema_name != "public" or schema_owner != "pg_database_owner":
        raise RuntimeError(
            f"Lakebase {principal_label} public-schema ownership mismatch: "
            f"schema={schema_name!r}, owner={schema_owner!r}"
        )
    writer_role = f"{_PROVIDER_DATABASE_WRITER_ROLE_PREFIX}{int(database_oid)}"

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            CASE WHEN acl.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
            acl.privilege_type,
            acl.is_grantable,
            grantor.rolname
        FROM pg_namespace namespace
        CROSS JOIN LATERAL aclexplode(namespace.nspacl) acl
        LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
        JOIN pg_roles grantor ON grantor.oid = acl.grantor
        WHERE namespace.nspname = 'public'
        ORDER BY 1, 2, 3, 4
        """
    )
    actual_acl = {
        (str(grantee), str(privilege), bool(grantable), str(grantor))
        for grantee, privilege, grantable, grantor in cur.fetchall()  # type: ignore[attr-defined]
    }
    expected_acl = {
        ("pg_database_owner", "CREATE", False, "pg_database_owner"),
        ("pg_database_owner", "USAGE", False, "pg_database_owner"),
        ("databricks_superuser", "CREATE", True, "pg_database_owner"),
        ("databricks_superuser", "USAGE", True, "pg_database_owner"),
        (writer_role, "CREATE", False, "pg_database_owner"),
        (writer_role, "USAGE", False, "pg_database_owner"),
    }
    legacy_public_row = ("PUBLIC", "USAGE", False, "pg_database_owner")
    accepted_acls = {frozenset(expected_acl)}
    if allow_legacy_public_usage:
        accepted_acls.add(frozenset({*expected_acl, legacy_public_row}))
    if frozenset(actual_acl) not in accepted_acls:
        raise RuntimeError(
            f"Lakebase {principal_label} public-schema ACL mismatch: "
            f"unexpected={sorted(actual_acl - expected_acl)}, "
            f"missing={sorted(expected_acl - actual_acl)}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT target.role_name, privilege.name
        FROM unnest(%s::text[]) target(role_name)
        CROSS JOIN unnest(%s::text[]) privilege(name)
        JOIN pg_namespace namespace ON namespace.nspname = 'public'
        WHERE has_schema_privilege(target.role_name, namespace.oid, privilege.name)
        ORDER BY target.role_name, privilege.name
        """,
        (list(roles), list(_SCHEMA_PRIVILEGE_NAMES)),
    )
    actual_capabilities = {
        (str(role), str(privilege))
        for role, privilege in cur.fetchall()  # type: ignore[attr-defined]
    }
    expected_capabilities = (
        {(role, "USAGE") for role in roles} if legacy_public_row in actual_acl else set()
    )
    if actual_capabilities != expected_capabilities:
        raise RuntimeError(
            f"Lakebase {principal_label} public-schema runtime access mismatch: "
            f"actual={sorted(actual_capabilities)}, "
            f"expected={sorted(expected_capabilities)}"
        )

    # Provider objects retain Lakebase-managed PUBLIC ACLs behind the closed
    # schema boundary. A target-specific grant on any public object would be a
    # dormant privilege edge that could reactivate if schema access ever
    # drifted, so inventory it independently of effective callability.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            grantee.rolname,
            routine.proname,
            oidvectortypes(routine.proargtypes),
            acl.privilege_type,
            acl.is_grantable,
            grantor.rolname
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        CROSS JOIN LATERAL aclexplode(routine.proacl) acl
        JOIN pg_roles grantee ON grantee.oid = acl.grantee
        JOIN pg_roles grantor ON grantor.oid = acl.grantor
        WHERE namespace.nspname = 'public'
          AND grantee.rolname = ANY(%s::text[])
        ORDER BY grantee.rolname, routine.proname,
                 oidvectortypes(routine.proargtypes), acl.privilege_type
        """,
        (list(roles),),
    )
    direct_provider_acl = list(cur.fetchall())  # type: ignore[attr-defined]
    if direct_provider_acl:
        raise RuntimeError(
            f"Lakebase {principal_label} public-routine direct ACL mismatch: "
            f"unexpected={direct_provider_acl}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        WITH provider_relation AS (
            SELECT relation.oid, relation.relname, relation.relacl
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'public'
        ), direct_acl AS (
            SELECT
                'relation' AS object_kind,
                grantee.rolname AS grantee_name,
                relation.relname AS relation_name,
                NULL::name AS column_name,
                acl.privilege_type,
                acl.is_grantable,
                grantor.rolname AS grantor_name
            FROM provider_relation relation
            CROSS JOIN LATERAL aclexplode(relation.relacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            JOIN pg_roles grantor ON grantor.oid = acl.grantor
            WHERE grantee.rolname = ANY(%s::text[])

            UNION ALL

            SELECT
                'column',
                grantee.rolname,
                relation.relname,
                attribute.attname,
                acl.privilege_type,
                acl.is_grantable,
                grantor.rolname
            FROM provider_relation relation
            JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
            CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            JOIN pg_roles grantor ON grantor.oid = acl.grantor
            WHERE attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND grantee.rolname = ANY(%s::text[])
        )
        SELECT
            object_kind,
            grantee_name,
            relation_name,
            column_name,
            privilege_type,
            is_grantable,
            grantor_name
        FROM direct_acl
        ORDER BY object_kind, grantee_name, relation_name, column_name,
                 privilege_type, grantor_name
        """,
        (list(roles), list(roles)),
    )
    direct_provider_relation_acl = list(cur.fetchall())  # type: ignore[attr-defined]
    if direct_provider_relation_acl:
        raise RuntimeError(
            f"Lakebase {principal_label} public-relation direct ACL mismatch: "
            f"unexpected={direct_provider_relation_acl}"
        )

    # Reject every target-specific default edge plus the managed provider's
    # global PUBLIC defaults. Do not attempt unauthorized owner-plane repair.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            CASE WHEN acl.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
            default_acl.defaclobjtype,
            acl.privilege_type,
            acl.is_grantable,
            grantor.rolname
        FROM pg_default_acl default_acl
        JOIN pg_roles owner ON owner.oid = default_acl.defaclrole
        LEFT JOIN pg_namespace namespace ON namespace.oid = default_acl.defaclnamespace
        CROSS JOIN LATERAL aclexplode(default_acl.defaclacl) acl
        LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
        JOIN pg_roles grantor ON grantor.oid = acl.grantor
        WHERE grantee.rolname = ANY(%s::text[])
           OR (
               owner.rolname = 'cloud_admin'
               AND default_acl.defaclnamespace = 0
               AND acl.grantee = 0
           )
        ORDER BY grantee.rolname, default_acl.defaclobjtype,
                 acl.privilege_type, grantor.rolname
        """,
        (list(roles),),
    )
    direct_provider_default_acl = list(cur.fetchall())  # type: ignore[attr-defined]
    if direct_provider_default_acl:
        raise RuntimeError(
            f"Lakebase {principal_label} public default-ACL mismatch: "
            f"unexpected={direct_provider_default_acl}"
        )


def _close_public_schema_boundary(
    cur: object,
    target_roles: Sequence[str],
    *,
    principal_label: str,
    allow_absent_provider_schema: bool = False,
    allow_empty_target_roles: bool = False,
) -> None:
    """Close provider-object lookup in one exact, idempotent transaction."""

    _postflight_public_schema_boundary(
        cur,
        target_roles,
        principal_label=f"{principal_label} preflight",
        allow_legacy_public_usage=True,
        allow_absent_provider_schema=allow_absent_provider_schema,
        allow_empty_target_roles=allow_empty_target_roles,
    )
    if not allow_absent_provider_schema:
        cur.execute(  # type: ignore[attr-defined]
            """
            SELECT
                current_user,
                database_owner.rolname,
                pg_has_role(current_user, 'pg_database_owner', 'SET')
            FROM pg_database database_object
            JOIN pg_roles database_owner ON database_owner.oid = database_object.datdba
            WHERE database_object.datname = current_database()
            """
        )
        authority_rows = list(cur.fetchall())  # type: ignore[attr-defined]
        if len(authority_rows) != 1 or not bool(authority_rows[0][2]):
            raise RuntimeError(
                f"Lakebase {principal_label} public-schema closure authority mismatch"
            )
        # Lakebase records the reviewed PUBLIC-USAGE row as granted by the
        # pg_database_owner pseudo-role. The database owner has SET authority,
        # but an ambient REVOKE is a silent no-op against that grantor edge.
        # Assume only this transaction-local pseudo-role for the exact revoke.
        cur.execute("SET LOCAL ROLE pg_database_owner")  # type: ignore[attr-defined]
        cur.execute(  # type: ignore[attr-defined]
            "REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC"
        )
        cur.execute("RESET ROLE")  # type: ignore[attr-defined]
    _postflight_public_schema_boundary(
        cur,
        target_roles,
        principal_label=f"{principal_label} postflight",
        allow_legacy_public_usage=False,
        allow_absent_provider_schema=allow_absent_provider_schema,
        allow_empty_target_roles=allow_empty_target_roles,
    )


def _postflight_no_pre_boundary_sessions(
    cur: object,
    target_roles: Sequence[str],
    *,
    allow_absent_provider_schema: bool = False,
) -> None:
    """Prove no target backend survived the committed schema closure.

    PostgreSQL schema revocation blocks future name lookup but cannot invalidate
    a plan already resolved in an existing backend. The public-schema closure
    is therefore committed first; a zero-session proof after that commit means
    every later target session necessarily starts behind the hardened boundary.
    """

    if allow_absent_provider_schema:
        return
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT usename, count(*)
        FROM pg_stat_activity
        WHERE usename = ANY(%s::text[])
        GROUP BY usename
        ORDER BY usename
        """,
        (list(target_roles),),
    )
    surviving_sessions = list(cur.fetchall())  # type: ignore[attr-defined]
    if surviving_sessions:
        raise RuntimeError(
            "Lakebase public-schema cutover has surviving pre-boundary target "
            f"sessions: {surviving_sessions}"
        )


def _postflight_provider_schema_boundary(
    cur: object,
    target_roles: Sequence[str],
    *,
    principal_label: str,
    allow_absent_provider_schema: bool = False,
) -> None:
    """Prove the namespace excluded from mutation is provider-owned and inaccessible."""

    if allow_absent_provider_schema:
        # Explicit vanilla-PostgreSQL integration seam. Production never sets
        # this flag and therefore cannot skip the ownership/access proof.
        return
    roles = tuple(dict.fromkeys(str(role) for role in target_roles if role))
    if not roles:
        raise RuntimeError(
            f"Lakebase {principal_label} provider-plane postflight has no target roles"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            namespace.nspname,
            namespace_owner.rolname,
            database_object.oid
        FROM pg_namespace namespace
        JOIN pg_roles namespace_owner ON namespace_owner.oid = namespace.nspowner
        CROSS JOIN pg_database database_object
        WHERE namespace.nspname = %s
          AND database_object.datname = current_database()
        """,
        (_PROVIDER_SCHEMA_NAME,),
    )
    schema_rows = list(cur.fetchall())  # type: ignore[attr-defined]
    if len(schema_rows) != 1:
        raise RuntimeError(
            f"Lakebase {principal_label} provider-plane schema inventory mismatch: "
            f"expected={_PROVIDER_SCHEMA_NAME!r}, rows={len(schema_rows)}"
        )
    schema_name, schema_owner, database_oid = schema_rows[0]
    expected_writer = f"{_PROVIDER_DATABASE_WRITER_ROLE_PREFIX}{int(database_oid)}"
    if schema_name != _PROVIDER_SCHEMA_NAME or schema_owner != _PROVIDER_SCHEMA_OWNER:
        raise RuntimeError(
            f"Lakebase {principal_label} provider-plane schema ownership mismatch: "
            f"schema={schema_name!r}, owner={schema_owner!r}"
        )

    # Every object with a direct namespace dependency must belong to one of
    # the explicitly inventoried owner-bearing catalogs below. A future
    # provider object class fails closed instead of disappearing behind the
    # whole-schema mutation exclusion.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT DISTINCT dependency.classid::regclass::text
        FROM pg_depend dependency
        WHERE dependency.refclassid = 'pg_namespace'::regclass
          AND dependency.refobjid = (
              SELECT oid FROM pg_namespace WHERE nspname = %s
          )
          AND dependency.deptype = 'n'
        ORDER BY 1
        """,
        (_PROVIDER_SCHEMA_NAME,),
    )
    object_catalogs = {str(row[0]) for row in cur.fetchall()}  # type: ignore[attr-defined]
    reviewed_catalogs = {
        "pg_class",
        "pg_proc",
        "pg_type",
        "pg_operator",
        "pg_collation",
        "pg_conversion",
        "pg_opclass",
        "pg_opfamily",
        "pg_statistic_ext",
        "pg_ts_config",
        "pg_ts_dict",
        "pg_extension",
    }
    if not object_catalogs or not object_catalogs <= reviewed_catalogs:
        raise RuntimeError(
            f"Lakebase {principal_label} provider-plane object catalog mismatch: "
            f"unexpected={sorted(object_catalogs - reviewed_catalogs)}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        WITH provider_object(object_kind, object_name, owner_oid) AS (
            SELECT 'relation', relation.relname, relation.relowner
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'routine', routine.proname, routine.proowner
            FROM pg_proc routine
            JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'type', type_object.typname, type_object.typowner
            FROM pg_type type_object
            JOIN pg_namespace namespace ON namespace.oid = type_object.typnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'operator', operator.oprname, operator.oprowner
            FROM pg_operator operator
            JOIN pg_namespace namespace ON namespace.oid = operator.oprnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'collation', collation_object.collname, collation_object.collowner
            FROM pg_collation collation_object
            JOIN pg_namespace namespace ON namespace.oid = collation_object.collnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'conversion', conversion.conname, conversion.conowner
            FROM pg_conversion conversion
            JOIN pg_namespace namespace ON namespace.oid = conversion.connamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'operator_class', operator_class.opcname, operator_class.opcowner
            FROM pg_opclass operator_class
            JOIN pg_namespace namespace ON namespace.oid = operator_class.opcnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'operator_family', operator_family.opfname, operator_family.opfowner
            FROM pg_opfamily operator_family
            JOIN pg_namespace namespace ON namespace.oid = operator_family.opfnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'extended_statistics', statistics.stxname, statistics.stxowner
            FROM pg_statistic_ext statistics
            JOIN pg_namespace namespace ON namespace.oid = statistics.stxnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'text_search_config', search_config.cfgname, search_config.cfgowner
            FROM pg_ts_config search_config
            JOIN pg_namespace namespace ON namespace.oid = search_config.cfgnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'text_search_dictionary', search_dictionary.dictname, search_dictionary.dictowner
            FROM pg_ts_dict search_dictionary
            JOIN pg_namespace namespace ON namespace.oid = search_dictionary.dictnamespace
            WHERE namespace.nspname = %s
            UNION ALL
            SELECT 'extension', extension.extname, extension.extowner
            FROM pg_extension extension
            JOIN pg_namespace namespace ON namespace.oid = extension.extnamespace
            WHERE namespace.nspname = %s
        )
        SELECT
            provider_object.object_kind,
            provider_object.object_name,
            object_owner.rolname
        FROM provider_object
        JOIN pg_roles object_owner ON object_owner.oid = provider_object.owner_oid
        WHERE object_owner.rolname <> ALL(%s::text[])
        ORDER BY provider_object.object_kind, provider_object.object_name
        """,
        (
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            [_PROVIDER_SCHEMA_OWNER, expected_writer],
        ),
    )
    unexpected_owners = list(cur.fetchall())  # type: ignore[attr-defined]
    if unexpected_owners:
        raise RuntimeError(
            f"Lakebase {principal_label} provider-plane object ownership mismatch: "
            f"unexpected={unexpected_owners}"
        )

    # Keep this independent effective-access audit over the namespace that is
    # excluded from mutation. It detects direct, PUBLIC, and inherited access,
    # including column-only capability.
    cur.execute(  # type: ignore[attr-defined]
        """
        WITH target_role(role_name) AS (
            SELECT unnest(%s::text[])
        ), provider_namespace AS (
            SELECT oid
            FROM pg_namespace
            WHERE nspname = %s
        ), capability(role_name, object_kind, object_name, privilege_name) AS (
            SELECT
                target.role_name,
                'schema',
                %s,
                privilege.name
            FROM target_role target
            CROSS JOIN provider_namespace namespace
            CROSS JOIN unnest(%s::text[]) AS privilege(name)
            WHERE has_schema_privilege(target.role_name, namespace.oid, privilege.name)
            UNION ALL
            SELECT
                target.role_name,
                'relation',
                relation.relname,
                privilege.name
            FROM target_role target
            CROSS JOIN pg_class relation
            JOIN provider_namespace namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN unnest(%s::text[]) AS privilege(name)
            WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND has_table_privilege(target.role_name, relation.oid, privilege.name)
            UNION ALL
            SELECT
                target.role_name,
                'column',
                relation.relname || '.' || attribute.attname,
                privilege.name
            FROM target_role target
            CROSS JOIN pg_class relation
            JOIN provider_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
            CROSS JOIN unnest(%s::text[]) AS privilege(name)
            WHERE relation.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND has_column_privilege(
                    target.role_name,
                    relation.oid,
                    attribute.attnum,
                    privilege.name
                  )
            UNION ALL
            SELECT
                target.role_name,
                'sequence',
                relation.relname,
                privilege.name
            FROM target_role target
            CROSS JOIN pg_class relation
            JOIN provider_namespace namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN unnest(%s::text[]) AS privilege(name)
            WHERE relation.relkind = 'S'
              AND has_sequence_privilege(target.role_name, relation.oid, privilege.name)
            UNION ALL
            SELECT
                target.role_name,
                'routine',
                routine.proname || '(' || oidvectortypes(routine.proargtypes) || ')',
                'EXECUTE'
            FROM target_role target
            CROSS JOIN pg_proc routine
            JOIN provider_namespace namespace ON namespace.oid = routine.pronamespace
            WHERE has_function_privilege(target.role_name, routine.oid, 'EXECUTE')
        )
        SELECT role_name, object_kind, object_name, privilege_name
        FROM capability
        ORDER BY role_name, object_kind, object_name, privilege_name
        """,
        (
            list(roles),
            _PROVIDER_SCHEMA_NAME,
            _PROVIDER_SCHEMA_NAME,
            list(_SCHEMA_PRIVILEGE_NAMES),
            list(_TABLE_PRIVILEGE_NAMES),
            list(_COLUMN_PRIVILEGE_NAMES),
            list(_SEQUENCE_PRIVILEGE_NAMES),
        ),
    )
    effective_capabilities = list(cur.fetchall())  # type: ignore[attr-defined]
    if effective_capabilities:
        raise RuntimeError(
            f"Lakebase {principal_label} provider-plane access mismatch: "
            f"unexpected={effective_capabilities}"
        )

    # Lakebase also exposes two cloud_admin-owned metadata views in public.
    # They run with view-owner privileges, so bind the raw view definitions
    # and allow only inherited PUBLIC SELECT (never a direct runtime grant).
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            relation.relname,
            relation.relkind,
            relation_owner.rolname,
            relation.relrowsecurity,
            relation.relforcerowsecurity,
            relation.reloptions,
            encode(
                sha256(convert_to(pg_get_viewdef(relation.oid, FALSE), 'UTF8')),
                'hex'
            ),
            octet_length(convert_to(pg_get_viewdef(relation.oid, FALSE), 'UTF8')),
            ARRAY(
                SELECT DISTINCT acl.privilege_type
                FROM aclexplode(relation.relacl) acl
                WHERE acl.grantee = 0
                ORDER BY acl.privilege_type
            ),
            EXISTS (
                SELECT 1
                FROM aclexplode(relation.relacl) acl
                JOIN pg_roles grantee ON grantee.oid = acl.grantee
                WHERE grantee.rolname = ANY(%s::text[])
            )
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_roles relation_owner ON relation_owner.oid = relation.relowner
        WHERE namespace.nspname = 'public'
          AND relation.relname = ANY(%s::text[])
        ORDER BY relation.relname
        """,
        (list(roles), sorted(_MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT)),
    )
    view_rows = list(cur.fetchall())  # type: ignore[attr-defined]
    actual_views: dict[str, tuple[str, int]] = {}
    drifted_views: set[str] = set()
    for row in view_rows:
        name = str(row[0])
        actual_views[name] = (str(row[6]), int(row[7]))
        if (
            row[1] != "v"
            or row[2] != "cloud_admin"
            or bool(row[3])
            or bool(row[4])
            or row[5] is not None
            or tuple(str(privilege) for privilege in row[8]) != ("SELECT",)
            or bool(row[9])
        ):
            drifted_views.add(name)
    expected_view_names = set(_MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT)
    actual_view_names = set(actual_views)
    drifted_views.update(
        name
        for name in expected_view_names & actual_view_names
        if actual_views[name] != _MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT[name]
    )
    if expected_view_names != actual_view_names or drifted_views:
        raise RuntimeError(
            f"Lakebase {principal_label} provider public-view inventory mismatch: "
            f"missing={sorted(expected_view_names - actual_view_names)}, "
            f"unexpected={sorted(actual_view_names - expected_view_names)}, "
            f"drifted={sorted(drifted_views)}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT target.role_name, relation.relname, privilege.name
        FROM unnest(%s::text[]) target(role_name)
        JOIN pg_namespace namespace ON namespace.nspname = 'public'
        JOIN pg_class relation ON relation.relnamespace = namespace.oid
        CROSS JOIN unnest(%s::text[]) privilege(name)
        WHERE relation.relname = ANY(%s::text[])
          AND has_table_privilege(target.role_name, relation.oid, privilege.name)
          AND privilege.name <> 'SELECT'
        ORDER BY target.role_name, relation.relname, privilege.name
        """,
        (
            list(roles),
            list(_TABLE_PRIVILEGE_NAMES),
            sorted(_MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT),
        ),
    )
    forbidden_view_capabilities = list(cur.fetchall())  # type: ignore[attr-defined]
    if forbidden_view_capabilities:
        raise RuntimeError(
            f"Lakebase {principal_label} provider public-view access mismatch: "
            f"unexpected={forbidden_view_capabilities}"
        )
