"""Least-privilege Lakebase ACL and role-security postflight helpers."""

from __future__ import annotations

from jobs.lakebase_migration_contracts import (
    _APP_ROLE_OPTIONAL_BASELINE_SCHEMA_PRIVILEGES,
    _COLUMN_PRIVILEGE_NAMES,
    _MANAGED_OAUTH_ROLE_ATTRIBUTE_NAMES,
    _MANAGED_OAUTH_ROLE_ATTRIBUTE_PROFILE,
    _MANAGED_PROVIDER_PUBLIC_ROUTINE_IDENTITIES,
    _SCHEMA_PRIVILEGE_NAMES,
)


def _postflight_effective_schema_privileges(
    cur: object,
    role: str,
    *,
    principal_label: str,
) -> None:
    """Fail closed on inherited or PUBLIC schema access outside the matrix."""

    # ``has_schema_privilege`` reports effective access, including grants
    # inherited through PUBLIC or another role. Direct reconciliation cannot
    # safely alter those unrelated principals, so the deploy must fail instead.
    # ``public.USAGE`` is the sole optional baseline; system schemas are
    # excluded deliberately.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, privilege.name
        FROM pg_namespace n
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND has_schema_privilege(%s, n.oid, privilege.name)
        ORDER BY n.nspname, privilege.name
        """,
        (list(_SCHEMA_PRIVILEGE_NAMES), role),
    )
    schema_privileges = set(cur.fetchall())  # type: ignore[attr-defined]
    allowed_schema_privileges = {
        ("mip_app", "USAGE"),
        *(_APP_ROLE_OPTIONAL_BASELINE_SCHEMA_PRIVILEGES & schema_privileges),
    }
    forbidden_schema_privileges = sorted(schema_privileges - allowed_schema_privileges)
    if forbidden_schema_privileges:
        raise RuntimeError(
            f"Lakebase {principal_label} has forbidden effective privileges on other schemas: "
            f"{forbidden_schema_privileges}"
        )


def _postflight_role_security(
    cur: object,
    role: str,
    *,
    principal_label: str,
) -> None:
    """Reject parent-role membership and security-sensitive role attributes.

    ``rolinherit`` and ``rolcanlogin`` are deliberately observed but not used
    as authorization signals: Lakebase OAuth-projected identities may vary on
    those flags. Every direct or recursive parent-role membership is forbidden,
    regardless of whether PostgreSQL currently exposes it through ``USAGE``,
    ``SET``, or an ADMIN-option path. This keeps later role-option changes from
    silently widening either runtime principal.
    """

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            rolname,
            rolsuper,
            rolcreaterole,
            rolcreatedb,
            rolreplication,
            rolbypassrls,
            rolinherit,
            rolcanlogin
        FROM pg_roles
        WHERE rolname = %s
        """,
        (role,),
    )
    rows = list(cur.fetchall())  # type: ignore[attr-defined]
    if len(rows) != 1 or rows[0][0] != role:
        raise RuntimeError(
            f"Lakebase {principal_label} security postflight could not verify exact role {role!r}"
        )
    actual_profile = tuple(bool(value) for value in rows[0][1:8])
    if actual_profile != _MANAGED_OAUTH_ROLE_ATTRIBUTE_PROFILE:
        drifted_attributes = [
            attribute
            for attribute, actual, expected in zip(
                _MANAGED_OAUTH_ROLE_ATTRIBUTE_NAMES,
                actual_profile,
                _MANAGED_OAUTH_ROLE_ATTRIBUTE_PROFILE,
                strict=True,
            )
            if actual != expected
        ]
        raise RuntimeError(
            f"Lakebase {principal_label} managed OAuth role attributes mismatch: "
            f"drifted={drifted_attributes}"
        )

    # pg_auth_members supplies the recursive membership graph. pg_has_role
    # records the current effective USAGE and SET interpretations while the
    # path accumulator records ADMIN option on any traversed edge. The policy
    # rejects every returned parent, including a membership whose current
    # option tuple happens to be entirely false.
    cur.execute(  # type: ignore[attr-defined]
        """
        WITH RECURSIVE membership(
            parent_oid,
            depth,
            path,
            inherited_usage,
            settable,
            admin_option_path
        ) AS (
            SELECT
                m.roleid,
                1,
                ARRAY[m.member, m.roleid]::oid[],
                pg_has_role(%s, m.roleid, 'USAGE'),
                pg_has_role(%s, m.roleid, 'SET'),
                m.admin_option
            FROM pg_auth_members m
            JOIN pg_roles member ON member.oid = m.member
            WHERE member.rolname = %s
            UNION
            SELECT
                parent.roleid,
                membership.depth + 1,
                membership.path || parent.roleid,
                pg_has_role(%s, parent.roleid, 'USAGE'),
                pg_has_role(%s, parent.roleid, 'SET'),
                membership.admin_option_path OR parent.admin_option
            FROM membership
            JOIN pg_auth_members parent ON parent.member = membership.parent_oid
            WHERE parent.roleid <> ALL(membership.path)
        )
        SELECT
            parent.rolname,
            bool_or(membership.inherited_usage),
            bool_or(membership.settable),
            bool_or(membership.admin_option_path),
            min(membership.depth)
        FROM membership
        JOIN pg_roles parent ON parent.oid = membership.parent_oid
        WHERE parent.rolname <> %s
        GROUP BY parent.rolname
        ORDER BY parent.rolname
        """,
        (role, role, role, role, role, role),
    )
    parent_roles = list(cur.fetchall())  # type: ignore[attr-defined]
    if parent_roles:
        raise RuntimeError(
            f"Lakebase {principal_label} has forbidden parent-role membership "
            "(role, inherited_usage, settable, admin_option_path, depth): "
            f"{parent_roles}"
        )

    # The inverse graph is equally security-sensitive: any direct or recursive
    # member of the runtime role can inherit it or SET ROLE into it now or
    # after an option change. Reject every descendant edge, even when the
    # current USAGE/SET/ADMIN tuple is false, so app and verifier identities
    # cannot become privilege-bearing group roles for another principal.
    cur.execute(  # type: ignore[attr-defined]
        """
        WITH RECURSIVE target AS (
            SELECT oid
            FROM pg_roles
            WHERE rolname = %s
        ), membership(
            member_oid,
            depth,
            path,
            admin_option_path
        ) AS (
            SELECT
                direct.member,
                1,
                ARRAY[direct.roleid, direct.member]::oid[],
                direct.admin_option
            FROM pg_auth_members direct
            JOIN target ON target.oid = direct.roleid
            UNION
            SELECT
                child.member,
                membership.depth + 1,
                membership.path || child.member,
                membership.admin_option_path OR child.admin_option
            FROM membership
            JOIN pg_auth_members child ON child.roleid = membership.member_oid
            WHERE child.member <> ALL(membership.path)
        )
        SELECT
            member.rolname,
            bool_or(pg_has_role(member.oid, target.oid, 'USAGE')),
            bool_or(pg_has_role(member.oid, target.oid, 'SET')),
            bool_or(membership.admin_option_path),
            min(membership.depth)
        FROM membership
        JOIN pg_roles member ON member.oid = membership.member_oid
        CROSS JOIN target
        WHERE member.rolname <> %s
        GROUP BY member.rolname
        ORDER BY member.rolname
        """,
        (role, role),
    )
    delegated_members = list(cur.fetchall())  # type: ignore[attr-defined]
    if delegated_members:
        raise RuntimeError(
            f"Lakebase {principal_label} has forbidden direct/recursive role delegates "
            "(role, inherited_usage, settable, admin_option_path, depth): "
            f"{delegated_members}"
        )


def _postflight_direct_column_privileges(
    cur: object,
    role: str,
    *,
    principal_label: str,
) -> None:
    """Reject explicit PUBLIC or principal ACL entries on user-table columns."""

    # Table ACL reconciliation does not remove attacl entries. Parent-role
    # column grants are impossible after the membership postflight; this query
    # therefore closes the two remaining effective sources: PUBLIC and a
    # direct grant to the runtime principal.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            CASE WHEN e.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
            n.nspname,
            c.relname,
            a.attname,
            e.privilege_type
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        CROSS JOIN LATERAL aclexplode(a.attacl) e
        LEFT JOIN pg_roles grantee ON grantee.oid = e.grantee
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND a.attnum > 0
          AND NOT a.attisdropped
          AND (e.grantee = 0 OR grantee.rolname = %s)
        ORDER BY n.nspname, c.relname, a.attnum, e.privilege_type
        """,
        (role,),
    )
    column_privileges = list(cur.fetchall())  # type: ignore[attr-defined]
    if column_privileges:
        raise RuntimeError(
            f"Lakebase {principal_label} retains forbidden direct/PUBLIC "
            f"column privileges: {column_privileges}"
        )


def _postflight_effective_column_only_privileges(
    cur: object,
    role: str,
    *,
    principal_label: str,
) -> None:
    """Reject effective column capability not backed by a table privilege."""

    # The attacl inventory above proves the expected direct/PUBLIC sources were
    # removed. This independent effective inquiry also catches any capability
    # PostgreSQL resolves through an unexpected path or catalog representation.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND has_any_column_privilege(%s, c.oid, privilege.name)
          AND NOT has_table_privilege(%s, c.oid, privilege.name)
        ORDER BY n.nspname, c.relname, privilege.name
        """,
        (list(_COLUMN_PRIVILEGE_NAMES), role, role),
    )
    column_only_privileges = list(cur.fetchall())  # type: ignore[attr-defined]
    if column_only_privileges:
        raise RuntimeError(
            f"Lakebase {principal_label} has forbidden effective column-only "
            f"privileges: {column_only_privileges}"
        )


def _postflight_effective_routine_privileges(
    cur: object,
    role: str,
    *,
    principal_label: str,
    expected: dict[tuple[str, str], tuple[str, ...]],
) -> None:
    """Verify the exact callable routine surface across all user schemas."""

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            n.nspname,
            p.proname,
            oidvectortypes(p.proargtypes),
            p.prokind,
            p.prosecdef,
            owner.rolname,
            EXISTS (
                SELECT 1
                FROM aclexplode(p.proacl) direct_acl
                JOIN pg_roles direct_grantee ON direct_grantee.oid = direct_acl.grantee
                WHERE direct_grantee.rolname = %s
                  AND direct_acl.privilege_type = 'EXECUTE'
            )
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_roles owner ON owner.oid = p.proowner
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND has_function_privilege(%s, p.oid, 'EXECUTE')
        ORDER BY n.nspname, p.proname, oidvectortypes(p.proargtypes)
        """,
        (role, role),
    )
    actual_rows = list(cur.fetchall())  # type: ignore[attr-defined]
    provider_rows = {
        (
            str(schema),
            str(name),
            str(arguments),
        )
        for schema, name, arguments, _kind, security_definer, owner, direct_grant in actual_rows
        if (str(schema), str(name), str(arguments))
        in _MANAGED_PROVIDER_PUBLIC_ROUTINE_IDENTITIES
        and str(_kind) == "f"
        and str(schema) == "public"
        and str(owner) == "cloud_admin"
        and not bool(security_definer)
        and not bool(direct_grant)
    }
    actual = {
        (
            str(schema),
            str(name),
            str(arguments),
            str(kind),
            bool(security_definer),
            str(owner) == role,
            bool(direct_grant),
        )
        for schema, name, arguments, kind, security_definer, owner, direct_grant in actual_rows
        if (str(schema), str(name), str(arguments)) not in provider_rows
    }
    expected_rows = {
        ("mip_app", name, arguments, "f", False, False, True)
        for (name, arguments), privileges in expected.items()
        if "EXECUTE" in privileges
    }
    if actual != expected_rows:
        raise RuntimeError(
            f"Lakebase {principal_label} routine EXECUTE postflight failed: "
            f"actual={sorted(actual)}, expected={sorted(expected_rows)}"
        )


def _postflight_effective_default_privileges(
    cur: object,
    role: str,
    *,
    principal_label: str,
) -> None:
    """Reject effective future table, sequence, or routine grants."""

    # pg_default_acl exposes direct ACL entries. pg_has_role(..., 'USAGE')
    # expands entries granted to roles whose privileges this principal
    # inherits, while grantee OID zero identifies PUBLIC. PostgreSQL cannot
    # revoke either safely from only this principal, so postflight reports
    # them after all direct target-role entries have been reconciled.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            owner.rolname,
            COALESCE(n.nspname, '<global>'),
            d.defaclobjtype,
            CASE WHEN e.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
            e.privilege_type
        FROM pg_default_acl d
        JOIN pg_roles owner ON owner.oid = d.defaclrole
        LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
        CROSS JOIN LATERAL aclexplode(d.defaclacl) e
        LEFT JOIN pg_roles grantee ON grantee.oid = e.grantee
        WHERE d.defaclobjtype IN ('r', 'S', 'f')
          AND (
              d.defaclnamespace = 0
              OR (
                  n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname !~ '^pg_'
              )
          )
          AND CASE
              WHEN e.grantee = 0 THEN TRUE
              WHEN grantee.rolname = %s THEN TRUE
              ELSE pg_has_role(%s, e.grantee, 'USAGE')
          END
        ORDER BY
            owner.rolname,
            COALESCE(n.nspname, '<global>'),
            d.defaclobjtype,
            grantee.rolname,
            e.privilege_type
        """,
        (role, role),
    )
    default_privileges = list(cur.fetchall())  # type: ignore[attr-defined]
    if default_privileges:
        raise RuntimeError(
            f"Lakebase {principal_label} retains forbidden effective future "
            "table/sequence/routine default privileges: "
            f"{sorted(default_privileges)}"
        )
