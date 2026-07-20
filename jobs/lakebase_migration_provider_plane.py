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
