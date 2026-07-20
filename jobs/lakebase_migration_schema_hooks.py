"""Executable schema-hook quarantine and trigger inventory proofs."""

from __future__ import annotations

from jobs.lakebase_migration_contracts import (
    _APP_TRIGGER_CONTRACT,
    _AUDIT_SEQUENCE_DEFAULT_EXPRESSION,
    _AUDIT_SEQUENCE_DEFAULT_KEY,
    _MANAGED_EVENT_TRIGGER_CONTRACT,
    _MANAGED_EVENT_TRIGGER_FUNCTION_ACLS,
    _MANAGED_OAUTH_ROLE_FUNCTION_ACLS,
    _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES,
    _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256,
    _QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT,
    _SAFE_SCHEMA_HOOK_FUNCTION_NAMES,
    _SAFE_SCHEMA_HOOK_PG_CATALOG_OPERATORS,
    _SAFE_SCHEMA_HOOK_PG_CATALOG_ROUTINES,
    _SQL_FUNCTION_CALL_RE,
    _SQL_STRING_LITERAL_RE,
    _ManagedEventTriggerContractRow,
)


def _postflight_oauth_role_function_contract(
    cur: object,
    *,
    principal_label: str,
    allow_absent_managed: bool = False,
) -> None:
    """Pin the provider-owned OAuth role primitive at every mutation gate."""

    if allow_absent_managed:
        # Explicit vanilla-PostgreSQL integration seam. Production never sets
        # this flag and therefore cannot skip the provider primitive proof.
        return
    cur.execute(  # type: ignore[attr-defined]
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
               CASE WHEN routine.proacl IS NULL THEN NULL ELSE routine.proacl::text[] END,
               database_object.oid
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
        WHERE routine.oid = to_regprocedure('public.databricks_create_role(text,text)')
          AND database_object.datname = current_database()
        """
    )
    rows = list(cur.fetchall())  # type: ignore[attr-defined]
    if len(rows) != 1:
        raise RuntimeError(
            f"Lakebase {principal_label} OAuth role-function inventory mismatch: "
            f"rows={len(rows)}"
        )
    row = tuple(rows[0])
    database_oid = int(row[-1])
    function_config = None if row[12] is None else tuple(str(item) for item in row[12])
    function_acl = None if row[21] is None else tuple(sorted(str(item) for item in row[21]))
    if function_acl not in _MANAGED_OAUTH_ROLE_FUNCTION_ACLS:
        raise RuntimeError(
            f"Lakebase {principal_label} OAuth role-function contract drifted"
        )
    actual = (*row[:12], function_config, *row[13:21], function_acl)
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
        f"databricks_writer_{database_oid}",
        _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256,
        _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES,
        function_acl,
    )
    if actual != expected:
        raise RuntimeError(
            f"Lakebase {principal_label} OAuth role-function contract drifted"
        )


def _schema_hook_function_calls(expression: object) -> set[str]:
    """Return normalized function-like identifiers outside SQL literals."""

    rendered = _SQL_STRING_LITERAL_RE.sub("''", str(expression or ""))
    return {
        match.group("name").strip('"').lower() for match in _SQL_FUNCTION_CALL_RE.finditer(rendered)
    }


def _preflight_executable_schema_hooks(
    cur: object,
) -> set[tuple[str, str, str]]:
    """Reject preserved catalog expressions that can execute unreviewed code.

    ``CREATE TABLE IF NOT EXISTS`` does not replace defaults, generated
    expressions, constraints, rules, policies, or expression indexes on an
    existing deployment. PostgreSQL records user-defined routine/operator/type
    dependencies for those expression trees in ``pg_depend``. Inventory the
    complete executable surface before schema.sql can run any DML and fail
    closed on every non-system dependency. Module 0 intentionally has no
    generated columns, user rewrite rules, or row policies.

    Six campaign CHECK constraints legitimately call reviewed ``mip_app``
    validators. They must match the exact code-owned dependency contract and
    be owned by the migration executor; the caller drops them under table lock
    before schema.sql and the post-seed suffix recreates them.
    """

    cur.execute(  # type: ignore[attr-defined]
        """
        WITH executable_hook AS (
            SELECT
                CASE
                    WHEN attribute.attgenerated <> '' THEN 'generated_expression'
                    ELSE 'column_default'
                END AS hook_kind,
                namespace.nspname AS object_schema,
                relation.relname AS table_name,
                attribute.attname AS hook_name,
                pg_get_expr(attribute_default.adbin, attribute_default.adrelid) AS expression,
                'pg_attrdef'::regclass AS catalog_class,
                attribute_default.oid AS catalog_oid
            FROM pg_attrdef attribute_default
            JOIN pg_class relation ON relation.oid = attribute_default.adrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            JOIN pg_attribute attribute
              ON attribute.attrelid = relation.oid
             AND attribute.attnum = attribute_default.adnum
            WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
              AND namespace.nspname !~ '^pg_'
              AND namespace.nspname <> '__db_system'
              AND relation.relkind IN ('r', 'p')

            UNION ALL

            SELECT
                'constraint_expression',
                namespace.nspname,
                COALESCE(relation.relname, '<domain>'),
                constraint_object.conname,
                pg_get_constraintdef(constraint_object.oid, TRUE),
                'pg_constraint'::regclass,
                constraint_object.oid
            FROM pg_constraint constraint_object
            JOIN pg_namespace namespace ON namespace.oid = constraint_object.connamespace
            LEFT JOIN pg_class relation ON relation.oid = constraint_object.conrelid
            WHERE constraint_object.conbin IS NOT NULL
              AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
              AND namespace.nspname !~ '^pg_'
              AND namespace.nspname <> '__db_system'

            UNION ALL

            SELECT
                'rewrite_rule',
                namespace.nspname,
                relation.relname,
                rewrite_rule.rulename,
                pg_get_ruledef(rewrite_rule.oid, TRUE),
                'pg_rewrite'::regclass,
                rewrite_rule.oid
            FROM pg_rewrite rewrite_rule
            JOIN pg_class relation ON relation.oid = rewrite_rule.ev_class
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
              AND namespace.nspname !~ '^pg_'
              AND namespace.nspname <> '__db_system'
              AND NOT (
                  rewrite_rule.rulename = '_RETURN'
                  AND relation.relkind IN ('v', 'm')
              )

            UNION ALL

            SELECT
                'row_policy',
                namespace.nspname,
                relation.relname,
                policy.polname,
                concat_ws(
                    ' ',
                    pg_get_expr(policy.polqual, policy.polrelid),
                    pg_get_expr(policy.polwithcheck, policy.polrelid)
                ),
                'pg_policy'::regclass,
                policy.oid
            FROM pg_policy policy
            JOIN pg_class relation ON relation.oid = policy.polrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
              AND namespace.nspname !~ '^pg_'
              AND namespace.nspname <> '__db_system'

            UNION ALL

            SELECT
                'index_expression',
                namespace.nspname,
                table_relation.relname,
                index_relation.relname,
                pg_get_expr(index_metadata.indexprs, index_metadata.indrelid),
                'pg_class'::regclass,
                index_relation.oid
            FROM pg_index index_metadata
            JOIN pg_class index_relation ON index_relation.oid = index_metadata.indexrelid
            JOIN pg_class table_relation ON table_relation.oid = index_metadata.indrelid
            JOIN pg_namespace namespace ON namespace.oid = table_relation.relnamespace
            WHERE index_metadata.indexprs IS NOT NULL
              AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
              AND namespace.nspname !~ '^pg_'
              AND namespace.nspname <> '__db_system'

            UNION ALL

            SELECT
                'index_predicate',
                namespace.nspname,
                table_relation.relname,
                index_relation.relname,
                pg_get_expr(index_metadata.indpred, index_metadata.indrelid),
                'pg_class'::regclass,
                index_relation.oid
            FROM pg_index index_metadata
            JOIN pg_class index_relation ON index_relation.oid = index_metadata.indexrelid
            JOIN pg_class table_relation ON table_relation.oid = index_metadata.indrelid
            JOIN pg_namespace namespace ON namespace.oid = table_relation.relnamespace
            WHERE index_metadata.indpred IS NOT NULL
              AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
              AND namespace.nspname !~ '^pg_'
              AND namespace.nspname <> '__db_system'
        ), relevant_dependency AS (
            SELECT
                hook.*,
                dependency.refclassid,
                dependency.refobjid
            FROM executable_hook hook
            LEFT JOIN pg_depend dependency
              ON dependency.classid = hook.catalog_class
             AND dependency.objid = hook.catalog_oid
             AND dependency.refclassid IN (
                 'pg_proc'::regclass,
                 'pg_operator'::regclass,
                 'pg_type'::regclass,
                 'pg_collation'::regclass,
                 'pg_class'::regclass
             )
        ), current_executor AS (
            SELECT oid
            FROM pg_roles
            WHERE rolname = current_user
        )
        SELECT
            dependency.hook_kind,
            dependency.object_schema,
            dependency.table_name,
            dependency.hook_name,
            dependency.expression,
            CASE dependency.refclassid
                WHEN 'pg_proc'::regclass THEN 'routine'
                WHEN 'pg_operator'::regclass THEN 'operator'
                WHEN 'pg_type'::regclass THEN 'type'
                WHEN 'pg_collation'::regclass THEN 'collation'
                WHEN 'pg_class'::regclass THEN 'relation'
            END AS dependency_kind,
            COALESCE(
                routine_namespace.nspname,
                operator_namespace.nspname,
                type_namespace.nspname,
                collation_namespace.nspname,
                relation_dependency_namespace.nspname
            ) AS dependency_schema,
            COALESCE(
                routine.proname,
                operator.oprname,
                type_object.typname,
                collation_object.collname,
                relation_dependency.relname
            ) AS dependency_name,
            CASE dependency.refclassid
                WHEN 'pg_proc'::regclass THEN oidvectortypes(routine.proargtypes)
                WHEN 'pg_operator'::regclass THEN concat_ws(
                    ', ',
                    NULLIF(format_type(operator.oprleft, NULL), '-'),
                    NULLIF(format_type(operator.oprright, NULL), '-')
                )
                ELSE ''
            END AS dependency_arguments,
            COALESCE(routine.prosecdef, FALSE) AS dependency_security_definer,
            CASE dependency.refclassid
                WHEN 'pg_proc'::regclass THEN routine.proowner = current_executor.oid
                WHEN 'pg_operator'::regclass THEN operator.oprowner = current_executor.oid
                WHEN 'pg_type'::regclass THEN type_object.typowner = current_executor.oid
                WHEN 'pg_collation'::regclass THEN collation_object.collowner = current_executor.oid
                WHEN 'pg_class'::regclass THEN relation_dependency.relowner = current_executor.oid
                ELSE FALSE
            END AS dependency_owned_by_executor
        FROM relevant_dependency dependency
        CROSS JOIN current_executor
        LEFT JOIN pg_proc routine
          ON dependency.refclassid = 'pg_proc'::regclass
         AND routine.oid = dependency.refobjid
        LEFT JOIN pg_namespace routine_namespace
          ON routine_namespace.oid = routine.pronamespace
        LEFT JOIN pg_operator operator
          ON dependency.refclassid = 'pg_operator'::regclass
         AND operator.oid = dependency.refobjid
        LEFT JOIN pg_namespace operator_namespace
          ON operator_namespace.oid = operator.oprnamespace
        LEFT JOIN pg_type type_object
          ON dependency.refclassid = 'pg_type'::regclass
         AND type_object.oid = dependency.refobjid
        LEFT JOIN pg_namespace type_namespace
          ON type_namespace.oid = type_object.typnamespace
        LEFT JOIN pg_collation collation_object
          ON dependency.refclassid = 'pg_collation'::regclass
         AND collation_object.oid = dependency.refobjid
        LEFT JOIN pg_namespace collation_namespace
          ON collation_namespace.oid = collation_object.collnamespace
        LEFT JOIN pg_class relation_dependency
          ON dependency.refclassid = 'pg_class'::regclass
         AND relation_dependency.oid = dependency.refobjid
        LEFT JOIN pg_namespace relation_dependency_namespace
          ON relation_dependency_namespace.oid = relation_dependency.relnamespace
        ORDER BY
            dependency.hook_kind,
            dependency.object_schema,
            dependency.table_name,
            dependency.hook_name,
            dependency_kind,
            dependency_schema,
            dependency_name,
            dependency_arguments
        """
    )
    rows = list(cur.fetchall())  # type: ignore[attr-defined]
    unexpected: list[tuple[object, ...]] = []
    reviewed_dependencies: dict[
        tuple[str, str, str],
        set[tuple[str, str]],
    ] = {}
    present_reviewed_constraint_keys: set[tuple[str, str, str]] = set()
    present_audit_sequence_default = False
    audit_sequence_expressions: set[str] = set()
    audit_sequence_relation_dependencies: set[tuple[str, str]] = set()

    for row in rows:
        (
            hook_kind,
            object_schema,
            table_name,
            hook_name,
            expression,
            dependency_kind,
            dependency_schema,
            dependency_name,
            dependency_arguments,
            dependency_security_definer,
            dependency_owned_by_executor,
        ) = row
        key = (str(object_schema), str(table_name), str(hook_name))
        kind = str(hook_kind)
        if kind == "constraint_expression" and (key in _QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT):
            present_reviewed_constraint_keys.add(key)
        if kind == "column_default" and key == _AUDIT_SEQUENCE_DEFAULT_KEY:
            present_audit_sequence_default = True
            audit_sequence_expressions.add(str(expression))

        function_calls = _schema_hook_function_calls(expression)
        unreviewed_function_calls = sorted(function_calls - _SAFE_SCHEMA_HOOK_FUNCTION_NAMES)
        if unreviewed_function_calls:
            unexpected.append(
                (
                    "unreviewed_function_call",
                    kind,
                    *key,
                    str(expression),
                    unreviewed_function_calls,
                )
            )
            continue

        # No Module 0 migration defines these ambient execution surfaces. View
        # _RETURN rules are excluded in SQL because they are structural, not
        # DML rewrite hooks.
        if kind in {"generated_expression", "rewrite_rule", "row_policy"}:
            unexpected.append(row)
            continue

        if dependency_kind is None:
            continue
        dependency = (
            str(dependency_name),
            str(dependency_arguments or ""),
        )
        dependency_namespace = str(dependency_schema or "")

        expected_routines = _QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT.get(key)
        if (
            kind == "constraint_expression"
            and str(dependency_kind) == "routine"
            and expected_routines is not None
            and dependency in expected_routines
            and not bool(dependency_security_definer)
            and bool(dependency_owned_by_executor)
        ):
            reviewed_dependencies.setdefault(key, set()).add(dependency)
            continue

        dependency_type = str(dependency_kind)
        if dependency_namespace == "pg_catalog":
            if dependency_type == "routine" and dependency in _SAFE_SCHEMA_HOOK_PG_CATALOG_ROUTINES:
                continue
            if (
                dependency_type == "operator"
                and dependency in _SAFE_SCHEMA_HOOK_PG_CATALOG_OPERATORS
            ):
                continue
            if dependency_type in {"type", "collation"}:
                continue

        if dependency_type == "relation":
            relation = (dependency_namespace, str(dependency_name))
            if relation == (str(object_schema), str(table_name)):
                # Structural column/table dependency of a CHECK or expression
                # index. It names the governed object itself, not code.
                continue
            if key == _AUDIT_SEQUENCE_DEFAULT_KEY:
                audit_sequence_relation_dependencies.add(relation)
                if relation == (
                    "mip_app",
                    "action_audit_audit_sequence_seq",
                ):
                    continue

        # Any non-system routine, operator, type, or collation can execute
        # code selected outside the reviewed migration. Keep the full row in
        # the exception so operators can audit the object and dependency.
        unexpected.append(row)

    for key in present_reviewed_constraint_keys:
        actual = reviewed_dependencies.get(key, set())
        expected = set(_QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT[key])
        if actual != expected:
            unexpected.append(("constraint_dependency_mismatch", *key, actual, expected))

    if present_audit_sequence_default and (
        audit_sequence_expressions != {_AUDIT_SEQUENCE_DEFAULT_EXPRESSION}
        or audit_sequence_relation_dependencies != {("mip_app", "action_audit_audit_sequence_seq")}
    ):
        unexpected.append(
            (
                "audit_sequence_default_contract_mismatch",
                _AUDIT_SEQUENCE_DEFAULT_KEY,
                audit_sequence_expressions,
                audit_sequence_relation_dependencies,
            )
        )

    if unexpected:
        raise RuntimeError(
            "Lakebase schema preflight executable-hook inventory mismatch: "
            f"unexpected={unexpected}"
        )
    return present_reviewed_constraint_keys


def _postflight_trigger_inventory(
    cur: object,
    role: str,
    *,
    principal_label: str,
    allow_missing_reviewed: bool = False,
) -> set[tuple[str, str, str]]:
    """Verify every existing user trigger against the code-owned contract.

    ``allow_missing_reviewed`` is reserved for the read-only preflight that
    runs before first-install DDL. It still rejects every unexpected trigger
    or unsafe rewrite; only absent reviewed triggers are tolerated. All
    postflights require exact equality.
    """

    # Trigger execution does not require the caller to retain EXECUTE on the
    # function. Therefore routine ACL reconciliation alone cannot neutralize an
    # unexpected trigger, especially one backed by SECURITY DEFINER code.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            table_namespace.nspname,
            table_class.relname,
            trigger.tgname,
            trigger.tgenabled,
            trigger.tgtype::integer,
            trigger.tgnargs::integer,
            trigger.tgqual IS NULL,
            trigger.tgconstraint = 0,
            function_namespace.nspname,
            function_proc.proname,
            oidvectortypes(function_proc.proargtypes),
            function_proc.prokind,
            format_type(function_proc.prorettype, NULL),
            function_proc.prosecdef,
            function_owner.rolname,
            table_owner.rolname,
            function_owner.oid = table_owner.oid,
            function_owner.oid = executor_role.oid,
            table_owner.oid = executor_role.oid,
            function_owner.rolname = %s OR table_owner.rolname = %s,
            trigger.tgattr = ''::int2vector,
            trigger.tgnewtable IS NULL,
            trigger.tgoldtable IS NULL,
            NOT trigger.tgdeferrable,
            NOT trigger.tginitdeferred
        FROM pg_trigger trigger
        JOIN pg_class table_class ON table_class.oid = trigger.tgrelid
        JOIN pg_namespace table_namespace ON table_namespace.oid = table_class.relnamespace
        JOIN pg_roles table_owner ON table_owner.oid = table_class.relowner
        JOIN pg_proc function_proc ON function_proc.oid = trigger.tgfoid
        JOIN pg_namespace function_namespace
          ON function_namespace.oid = function_proc.pronamespace
        JOIN pg_roles function_owner ON function_owner.oid = function_proc.proowner
        JOIN pg_roles executor_role ON executor_role.rolname = current_user
        WHERE NOT trigger.tgisinternal
          AND table_class.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND table_namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          AND table_namespace.nspname !~ '^pg_'
        ORDER BY table_namespace.nspname, table_class.relname, trigger.tgname
        """,
        (role, role),
    )
    raw_rows = list(cur.fetchall())  # type: ignore[attr-defined]
    actual = {
        (
            str(table_schema),
            str(table_name),
            str(trigger_name),
            str(enabled),
            int(trigger_type),
            int(argument_count),
            bool(without_when_clause),
            bool(without_constraint),
            str(function_schema),
            str(function_name),
            str(function_arguments),
            str(function_kind),
            str(return_type),
            bool(security_definer),
            bool(owners_match),
            bool(function_owned_by_executor),
            bool(table_owned_by_executor),
            bool(owned_by_principal),
            bool(without_update_columns),
            bool(without_new_transition_table),
            bool(without_old_transition_table),
            bool(not_deferrable),
            bool(not_initially_deferred),
        )
        for (
            table_schema,
            table_name,
            trigger_name,
            enabled,
            trigger_type,
            argument_count,
            without_when_clause,
            without_constraint,
            function_schema,
            function_name,
            function_arguments,
            function_kind,
            return_type,
            security_definer,
            _function_owner,
            _table_owner,
            owners_match,
            function_owned_by_executor,
            table_owned_by_executor,
            owned_by_principal,
            without_update_columns,
            without_new_transition_table,
            without_old_transition_table,
            not_deferrable,
            not_initially_deferred,
        ) in raw_rows
    }
    expected = {
        (
            table_schema,
            table_name,
            trigger_name,
            "O",
            trigger_type,
            0,
            True,
            True,
            function_schema,
            function_name,
            function_arguments,
            "f",
            "trigger",
            False,
            True,
            True,
            True,
            False,
            True,
            True,
            True,
            True,
            True,
        )
        for (
            table_schema,
            table_name,
            trigger_name,
        ), (
            function_schema,
            function_name,
            function_arguments,
            trigger_type,
        ) in _APP_TRIGGER_CONTRACT.items()
    }
    mismatch = bool(actual - expected) if allow_missing_reviewed else actual != expected
    if mismatch:
        raise RuntimeError(
            f"Lakebase {principal_label} global trigger inventory mismatch: "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}, owners="
            f"{sorted((row[0], row[1], row[2], row[14], row[15]) for row in raw_rows)}"
        )
    return {
        (str(table_schema), str(table_name), str(trigger_name))
        for table_schema, table_name, trigger_name, *_remaining_fields in raw_rows
    }


def _postflight_event_trigger_inventory(
    cur: object,
    role: str,
    *,
    principal_label: str,
    allow_absent_managed: bool = False,
) -> None:
    """Require the exact Databricks-managed DDL event-trigger inventory."""

    # pg_trigger does not include DDL event triggers. These provider-plane
    # hooks execute on schema and ACL DDL, so bind their complete identity,
    # execution attributes, raw function-body digest, and byte length. Keep
    # NULL evttags distinct from an empty tag list: NULL means every command.
    # The role parameter remains part of the stable helper interface used by
    # all four schema/ACL gates; event-trigger ownership is pinned separately
    # to cloud_admin and never delegated to a runtime role.
    del role
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            event_trigger.evtname,
            event_trigger.evtevent,
            event_trigger.evtenabled,
            event_trigger.evttags,
            event_owner.rolname,
            function_namespace.nspname,
            function_proc.proname,
            oidvectortypes(function_proc.proargtypes),
            function_proc.prokind,
            format_type(function_proc.prorettype, NULL),
            function_proc.prosecdef,
            function_owner.rolname,
            function_language.lanname,
            function_proc.provolatile,
            function_proc.proparallel,
            function_proc.proleakproof,
            function_proc.proisstrict,
            function_proc.proconfig,
            function_proc.probin,
            CASE
                WHEN function_proc.proacl IS NULL THEN NULL
                ELSE function_proc.proacl::text[]
            END,
            encode(sha256(convert_to(function_proc.prosrc, 'UTF8')), 'hex'),
            octet_length(convert_to(function_proc.prosrc, 'UTF8'))
        FROM pg_event_trigger event_trigger
        JOIN pg_roles event_owner ON event_owner.oid = event_trigger.evtowner
        JOIN pg_proc function_proc ON function_proc.oid = event_trigger.evtfoid
        JOIN pg_namespace function_namespace
          ON function_namespace.oid = function_proc.pronamespace
        JOIN pg_roles function_owner ON function_owner.oid = function_proc.proowner
        JOIN pg_language function_language ON function_language.oid = function_proc.prolang
        ORDER BY event_trigger.evtname
        """
    )
    rows = list(cur.fetchall())  # type: ignore[attr-defined]
    if not rows:
        if allow_absent_managed:
            return
        raise RuntimeError(
            f"Lakebase {principal_label} global event-trigger inventory mismatch: "
            f"missing={sorted(_MANAGED_EVENT_TRIGGER_CONTRACT)}, unexpected=[]"
        )

    actual: dict[str, _ManagedEventTriggerContractRow] = {}
    actual_acls: dict[str, tuple[str, ...] | None] = {}
    duplicate_names: set[str] = set()
    for row in rows:
        name = str(row[0])
        if name in actual:
            duplicate_names.add(name)
        tags = None if row[3] is None else tuple(sorted(str(tag) for tag in row[3]))
        function_config = None if row[17] is None else tuple(str(item) for item in row[17])
        function_binary = None if row[18] is None else str(row[18])
        function_acl = None if row[19] is None else tuple(sorted(str(item) for item in row[19]))
        actual[name] = _ManagedEventTriggerContractRow(
            event=str(row[1]),
            enabled=str(row[2]),
            tags=tags,
            event_owner=str(row[4]),
            function_schema=str(row[5]),
            function_name=str(row[6]),
            function_arguments=str(row[7]),
            function_kind=str(row[8]),
            function_return_type=str(row[9]),
            function_security_definer=bool(row[10]),
            function_owner=str(row[11]),
            function_language=str(row[12]),
            function_volatility=str(row[13]),
            function_parallel_safety=str(row[14]),
            function_leakproof=bool(row[15]),
            function_strict=bool(row[16]),
            function_config=function_config,
            function_binary=function_binary,
            function_source_sha256=str(row[20]),
            function_source_bytes=int(row[21]),
        )
        actual_acls[name] = function_acl

    expected_names = set(_MANAGED_EVENT_TRIGGER_CONTRACT)
    actual_names = set(actual)
    drifted = sorted(
        name
        for name in expected_names & actual_names
        if actual[name] != _MANAGED_EVENT_TRIGGER_CONTRACT[name]
    )
    forbidden_acls = sorted(
        name
        for name in expected_names & actual_names
        if actual_acls[name] not in _MANAGED_EVENT_TRIGGER_FUNCTION_ACLS
    )
    if (
        expected_names != actual_names
        or duplicate_names
        or drifted
        or forbidden_acls
    ):
        raise RuntimeError(
            f"Lakebase {principal_label} global event-trigger inventory mismatch: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"unexpected={sorted(actual_names - expected_names)}, "
            f"duplicates={sorted(duplicate_names)}, drifted={drifted}, "
            f"forbidden_acls={forbidden_acls}"
        )


def _quarantine_existing_reviewed_triggers(
    cur: object,
    trigger_keys: set[tuple[str, str, str]],
) -> None:
    """Lock affected tables and remove reviewed triggers until schema recreates them."""

    from psycopg import sql as psql

    # The preflight proves every key is from the static reviewed contract.
    # Identifier composition remains mandatory so even a future reviewed name
    # containing punctuation cannot become executable SQL. Explicit table
    # locks make the trigger-free migration window durable until commit or
    # rollback; DROP is deliberately not IF EXISTS because the inventory is
    # the source of truth and concurrent disappearance must fail closed.
    for table_schema, table_name in sorted(
        {(table_schema, table_name) for table_schema, table_name, _trigger in trigger_keys}
    ):
        cur.execute(  # type: ignore[attr-defined]
            psql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE")
            .format(psql.Identifier(table_schema, table_name))
            .as_string()
        )
    for table_schema, table_name, trigger_name in sorted(trigger_keys):
        cur.execute(  # type: ignore[attr-defined]
            psql.SQL("DROP TRIGGER {} ON {}")
            .format(
                psql.Identifier(trigger_name),
                psql.Identifier(table_schema, table_name),
            )
            .as_string()
        )


def _quarantine_reviewed_constraints(
    cur: object,
    constraint_keys: set[tuple[str, str, str]],
) -> None:
    """Remove reviewed custom-code CHECKs until schema.sql recreates them."""

    from psycopg import sql as psql

    # The executable-hook preflight proves both exact identity and exact
    # custom-routine dependency. Locks keep the constraint-free interval
    # private to this transaction; rollback restores every dropped constraint.
    for table_schema, table_name in sorted(
        {(schema, table) for schema, table, _constraint in constraint_keys}
    ):
        cur.execute(  # type: ignore[attr-defined]
            psql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE")
            .format(psql.Identifier(table_schema, table_name))
            .as_string()
        )
    for table_schema, table_name, constraint_name in sorted(constraint_keys):
        cur.execute(  # type: ignore[attr-defined]
            psql.SQL("ALTER TABLE {} DROP CONSTRAINT {}")
            .format(
                psql.Identifier(table_schema, table_name),
                psql.Identifier(constraint_name),
            )
            .as_string()
        )
