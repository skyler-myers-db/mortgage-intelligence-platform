"""Deployment contract for strict Lakebase runtime-role grants."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.schemas import lender_identity
from jobs import lakebase_migrate


@pytest.fixture(autouse=True)
def _default_to_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABRICKS_BUNDLE_TARGET", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", raising=False)


class _Cursor:
    def __init__(
        self,
        *,
        fetchall_results: list[list[tuple[Any, ...]]],
        fetchone_results: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._fetchall_results = list(fetchall_results)
        self._fetchone_results = list(fetchone_results or [])

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object, params: Any = None) -> None:
        self.executed.append((str(statement), params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        if not self._fetchall_results:
            raise AssertionError("unexpected fetchall")
        return self._fetchall_results.pop(0)

    def fetchone(self) -> tuple[Any, ...]:
        if not self._fetchone_results:
            raise AssertionError("unexpected fetchone")
        return self._fetchone_results.pop(0)


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.closed = False
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> _Cursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class _FailingCursor(_Cursor):
    def __init__(self, *, fail_when: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fail_when = fail_when

    def execute(self, statement: object, params: Any = None) -> None:
        super().execute(statement, params)
        if self._fail_when in str(statement):
            raise RuntimeError("injected database failure")


class _SqlStateError(RuntimeError):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _RejectingProbeCursor:
    def __init__(self, *, reject: bool = True, sqlstate: str = "42501") -> None:
        self.reject = reject
        self.sqlstate = sqlstate
        self.executed: list[tuple[str, Any]] = []

    def execute(self, statement: object, params: Any = None) -> None:
        rendered = str(statement)
        self.executed.append((rendered, params))
        if rendered == "FORBIDDEN" and self.reject:
            raise _SqlStateError(self.sqlstate)


def _table_rows(*, add: str | None = None) -> list[tuple[str]]:
    tables = set(lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES)
    if add is not None:
        tables.add(add)
    return [(table,) for table in sorted(tables)]


def _table_privilege_rows(
    *,
    add: tuple[str, str] | None = None,
) -> list[tuple[str, str]]:
    rows = [
        (table, privilege)
        for table, privileges in lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES.items()
        for privilege in privileges
    ]
    if add is not None:
        rows.append(add)
    return sorted(rows)


def _sequence_rows() -> list[tuple[str]]:
    return [(sequence,) for sequence in sorted(lakebase_migrate._APP_ROLE_SEQUENCE_PRIVILEGES)]


def _sequence_privilege_rows() -> list[tuple[str, str]]:
    return sorted(
        (sequence, privilege)
        for sequence, privileges in lakebase_migrate._APP_ROLE_SEQUENCE_PRIVILEGES.items()
        for privilege in privileges
    )


def _verifier_table_privilege_rows() -> list[tuple[str, str]]:
    return [
        ("ai_gateway_proof_ledger", privilege)
        for privilege in lakebase_migrate._AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES
    ]


def _all_table_inventory_rows() -> list[tuple[str, str]]:
    return [
        *[("mip_app", table) for (table,) in _table_rows()],
        ("analytics", "borrower_export"),
        ("public", "borrower_export_view"),
    ]


def _all_sequence_inventory_rows() -> list[tuple[str, str]]:
    return [
        *[("mip_app", sequence) for (sequence,) in _sequence_rows()],
        ("analytics", "export_sequence"),
    ]


def _routine_inventory_rows() -> list[tuple[str, str, str, str, str, bool, str]]:
    rows = [
        (
            "FUNCTION",
            f'"mip_app"."{name}"({arguments})',
            "mip_app",
            name,
            arguments,
            False,
            "migration-owner",
        )
        for name, arguments in sorted(lakebase_migrate._APP_ROLE_ROUTINE_PRIVILEGES)
    ]
    return [
        *rows,
        (
            "FUNCTION",
            '"public"."databricks_create_role"(text, text)',
            "public",
            "databricks_create_role",
            "text, text",
            False,
            "cloud_admin",
        ),
        (
            "FUNCTION",
            '"public"."exfiltrate"()',
            "public",
            "exfiltrate",
            "",
            True,
            "migration-owner",
        ),
        (
            "FUNCTION",
            '"analytics"."score"(integer)',
            "analytics",
            "score",
            "integer",
            False,
            "migration-owner",
        ),
        (
            "FUNCTION",
            '"analytics"."score"(text)',
            "analytics",
            "score",
            "text",
            False,
            "migration-owner",
        ),
    ]


def _app_routine_privilege_rows() -> list[tuple[str, str, str, str, bool, str, bool]]:
    return sorted(
        ("mip_app", name, arguments, "f", False, "migration-owner", True)
        for (name, arguments), privileges in lakebase_migrate._APP_ROLE_ROUTINE_PRIVILEGES.items()
        if "EXECUTE" in privileges
    )


def _trigger_inventory_rows() -> list[tuple[Any, ...]]:
    return sorted(
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
            "migration-owner",
            "migration-owner",
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
        ) in lakebase_migrate._APP_TRIGGER_CONTRACT.items()
    )


def _managed_event_trigger_rows(
    *,
    function_acl: list[str] | None = None,
) -> list[tuple[Any, ...]]:
    return [
        (
            name,
            contract.event,
            contract.enabled,
            list(contract.tags or ()),
            contract.event_owner,
            contract.function_schema,
            contract.function_name,
            contract.function_arguments,
            contract.function_kind,
            contract.function_return_type,
            contract.function_security_definer,
            contract.function_owner,
            contract.function_language,
            contract.function_volatility,
            contract.function_parallel_safety,
            contract.function_leakproof,
            contract.function_strict,
            contract.function_config,
            contract.function_binary,
            function_acl,
            contract.function_source_sha256,
            contract.function_source_bytes,
        )
        for name, contract in sorted(lakebase_migrate._MANAGED_EVENT_TRIGGER_CONTRACT.items())
    ]


def _oauth_role_function_rows(
    *,
    function_acl: list[str] | None = None,
) -> list[tuple[Any, ...]]:
    return [
        (
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
            "databricks_writer_16538",
            lakebase_migrate._MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256,
            lakebase_migrate._MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES,
            function_acl,
            16538,
        )
    ]


def _managed_provider_public_view_rows() -> list[tuple[Any, ...]]:
    return [
        (
            name,
            "v",
            "cloud_admin",
            False,
            False,
            None,
            source_sha256,
            source_bytes,
            ["SELECT"],
            False,
        )
        for name, (
            source_sha256,
            source_bytes,
        ) in sorted(lakebase_migrate._MANAGED_PROVIDER_PUBLIC_VIEW_CONTRACT.items())
    ]


def _hostile_event_trigger_row() -> tuple[Any, ...]:
    return (
        "trg_ddl_exfiltrate",
        "ddl_command_start",
        "O",
        [],
        "attacker-owner",
        "public",
        "ddl_exfiltrate",
        "",
        "f",
        "event_trigger",
        True,
        "attacker-owner",
        "plpgsql",
        "v",
        "u",
        False,
        False,
        None,
        None,
        None,
        "0" * 64,
        1,
    )


def test_managed_event_trigger_inventory_accepts_complete_exact_contract() -> None:
    cursor = _Cursor(fetchall_results=[_managed_event_trigger_rows()])

    lakebase_migrate._postflight_event_trigger_inventory(
        cursor,
        "app-role",
        principal_label="schema preflight",
    )

    query = cursor.executed[0][0]
    assert "event_trigger.evttags" in query
    assert "COALESCE(event_trigger.evttags" not in query
    assert "function_proc.proconfig" in query
    assert "function_proc.probin" in query
    assert "function_proc.proacl::text[]" in query
    assert "sha256(convert_to(function_proc.prosrc, 'UTF8'))" in query


def test_absent_managed_event_triggers_require_explicit_local_test_seam() -> None:
    cursor = _Cursor(fetchall_results=[[]])
    with pytest.raises(RuntimeError, match=r"event-trigger.*missing=.*on_create_schema"):
        lakebase_migrate._postflight_event_trigger_inventory(
            cursor,
            "app-role",
            principal_label="schema preflight",
        )

    local_cursor = _Cursor(fetchall_results=[[]])
    lakebase_migrate._postflight_event_trigger_inventory(
        local_cursor,
        "app-role",
        principal_label="local PostgreSQL",
        allow_absent_managed=True,
    )


@pytest.mark.parametrize(
    "rows",
    (
        _managed_event_trigger_rows()[:1],
        [*_managed_event_trigger_rows(), _hostile_event_trigger_row()],
    ),
    ids=("partial-managed-set", "managed-plus-hostile"),
)
def test_managed_event_trigger_inventory_rejects_partial_or_extra_rows(
    rows: list[tuple[Any, ...]],
) -> None:
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match="event-trigger inventory mismatch"):
        lakebase_migrate._postflight_event_trigger_inventory(
            cursor,
            "app-role",
            principal_label="schema preflight",
        )


@pytest.mark.parametrize(
    ("field_index", "mutated_value"),
    (
        (1, "ddl_command_start"),
        (2, "D"),
        (3, None),
        (3, []),
        (3, ["CREATE SCHEMA", "CREATE SCHEMA"]),
        (3, ["CREATE SCHEMA", "DROP TABLE"]),
        (4, "attacker-owner"),
        (5, "attacker"),
        (6, "grant_usage_and_exfiltrate"),
        (7, "text"),
        (8, "p"),
        (9, "void"),
        (10, True),
        (11, "attacker-owner"),
        (12, "internal"),
        (13, "i"),
        (14, "s"),
        (15, True),
        (16, True),
        (17, ["search_path=attacker"]),
        (18, "$libdir/attacker"),
        (20, "1" * 64),
        (21, 1245),
    ),
)
def test_managed_event_trigger_inventory_rejects_every_shape_or_source_drift(
    field_index: int,
    mutated_value: object,
) -> None:
    rows = _managed_event_trigger_rows()
    row = list(rows[0])
    row[field_index] = mutated_value
    rows[0] = tuple(row)
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match=r"event-trigger.*drifted=.*on_create_schema"):
        lakebase_migrate._postflight_event_trigger_inventory(
            cursor,
            "app-role",
            principal_label="schema preflight",
        )


@pytest.mark.parametrize(
    "function_acl",
    (None, ["cloud_admin=X/cloud_admin"]),
    ids=("first-install-null", "owner-only-after-public-revoke"),
)
def test_managed_event_trigger_inventory_accepts_only_reviewed_function_acls(
    function_acl: list[str] | None,
) -> None:
    cursor = _Cursor(
        fetchall_results=[_managed_event_trigger_rows(function_acl=function_acl)]
    )

    lakebase_migrate._postflight_event_trigger_inventory(
        cursor,
        "app-role",
        principal_label="ACL postflight",
    )


@pytest.mark.parametrize(
    "function_acl",
    (
        [],
        ["=X/cloud_admin", "cloud_admin=X/cloud_admin"],
        ["app-role=X/cloud_admin", "cloud_admin=X/cloud_admin"],
    ),
)
def test_managed_event_trigger_inventory_rejects_unreviewed_function_acls(
    function_acl: list[str],
) -> None:
    cursor = _Cursor(
        fetchall_results=[_managed_event_trigger_rows(function_acl=function_acl)]
    )

    with pytest.raises(RuntimeError, match=r"forbidden_acls=.*on_create_schema"):
        lakebase_migrate._postflight_event_trigger_inventory(
            cursor,
            "app-role",
            principal_label="ACL postflight",
        )


def test_provider_schema_boundary_accepts_exact_owned_inaccessible_namespace() -> None:
    cursor = _Cursor(
        fetchall_results=[
            [("__db_system", "databricks_control_plane", 16538)],
            [("pg_class",)],
            [],
            [],
            _managed_provider_public_view_rows(),
            [],
        ]
    )

    lakebase_migrate._postflight_provider_schema_boundary(
        cursor,
        ("app-role", "verifier-role"),
        principal_label="ACL preflight",
    )

    statements = [statement for statement, _params in cursor.executed]
    assert "pg_database" in statements[0]
    assert "pg_depend" in statements[1]
    for catalog in (
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
    ):
        assert catalog in statements[2]
    assert "has_schema_privilege" in statements[3]
    assert "has_table_privilege" in statements[3]
    assert "has_column_privilege" in statements[3]
    assert "has_sequence_privilege" in statements[3]
    assert "has_function_privilege" in statements[3]
    assert "pg_get_viewdef" in statements[4]
    assert "has_table_privilege" in statements[5]
    assert not any("GRANT " in statement or "REVOKE " in statement for statement in statements)


def test_absent_provider_schema_requires_explicit_local_test_seam() -> None:
    cursor = _Cursor(fetchall_results=[[]])
    with pytest.raises(RuntimeError, match="provider-plane schema inventory mismatch"):
        lakebase_migrate._postflight_provider_schema_boundary(
            cursor,
            ("app-role",),
            principal_label="schema preflight",
        )

    local_cursor = _Cursor(fetchall_results=[])
    lakebase_migrate._postflight_provider_schema_boundary(
        local_cursor,
        ("app-role",),
        principal_label="local PostgreSQL",
        allow_absent_provider_schema=True,
    )
    assert local_cursor.executed == []


@pytest.mark.parametrize(
    "schema_rows",
    (
        [("__db_system", "attacker-owner", 16538)],
        [
            ("__db_system", "databricks_control_plane", 16538),
            ("__db_system", "databricks_control_plane", 16538),
        ],
    ),
    ids=("attacker-owned", "duplicate"),
)
def test_provider_schema_boundary_rejects_owner_or_inventory_drift(
    schema_rows: list[tuple[str, str, int]],
) -> None:
    cursor = _Cursor(fetchall_results=[schema_rows])
    with pytest.raises(RuntimeError, match="provider-plane schema"):
        lakebase_migrate._postflight_provider_schema_boundary(
            cursor,
            ("app-role",),
            principal_label="schema preflight",
        )


def test_provider_schema_boundary_rejects_unreviewed_object_catalog() -> None:
    cursor = _Cursor(
        fetchall_results=[
            [("__db_system", "databricks_control_plane", 16538)],
            [("pg_class",), ("pg_future_object",)],
        ]
    )

    with pytest.raises(RuntimeError, match=r"object catalog.*pg_future_object"):
        lakebase_migrate._postflight_provider_schema_boundary(
            cursor,
            ("app-role",),
            principal_label="schema preflight",
        )


def test_provider_schema_boundary_rejects_unreviewed_object_owner() -> None:
    cursor = _Cursor(
        fetchall_results=[
            [("__db_system", "databricks_control_plane", 16538)],
            [("pg_class",)],
            [("relation", "hidden_payload", "app-role")],
        ]
    )
    with pytest.raises(RuntimeError, match=r"object ownership.*hidden_payload"):
        lakebase_migrate._postflight_provider_schema_boundary(
            cursor,
            ("app-role",),
            principal_label="schema preflight",
        )


@pytest.mark.parametrize(
    "capability",
    (
        ("app-role", "schema", "__db_system", "USAGE"),
        ("app-role", "relation", "hidden_table", "SELECT"),
        ("app-role", "column", "hidden_table.value", "UPDATE"),
        ("verifier-role", "sequence", "hidden_seq", "USAGE"),
        ("verifier-role", "routine", "hidden()", "EXECUTE"),
    ),
)
def test_provider_schema_boundary_rejects_every_runtime_capability(
    capability: tuple[str, str, str, str],
) -> None:
    cursor = _Cursor(
        fetchall_results=[
            [("__db_system", "databricks_control_plane", 16538)],
            [("pg_class",)],
            [],
            [capability],
        ]
    )
    with pytest.raises(RuntimeError, match="provider-plane access mismatch"):
        lakebase_migrate._postflight_provider_schema_boundary(
            cursor,
            ("app-role", "verifier-role"),
            principal_label="ACL postflight",
        )


def _schema_hook_row(
    hook_kind: str,
    *,
    schema: str = "mip_app",
    table: str = "campaigns",
    hook_name: str = "hostile_hook",
    expression: str = "attacker.exfiltrate()",
    dependency_kind: str | None = "routine",
    dependency_schema: str | None = "attacker",
    dependency_name: str | None = "exfiltrate",
    dependency_arguments: str = "",
    security_definer: bool = True,
    owned_by_executor: bool = False,
) -> tuple[Any, ...]:
    return (
        hook_kind,
        schema,
        table,
        hook_name,
        expression,
        dependency_kind,
        dependency_schema,
        dependency_name,
        dependency_arguments,
        security_definer,
        owned_by_executor,
    )


def _safe_role_security_row(role: str) -> tuple[str, bool, bool, bool, bool, bool, bool, bool]:
    return (role, False, False, False, False, False, True, True)


def _successful_cursor(
    role: str,
    verifier_role: str | None = None,
    *,
    column_acl_rows: list[tuple[str, str, str, str]] | None = None,
    trigger_rows: list[tuple[Any, ...]] | None = None,
    acl_event_preflight_rows: list[tuple[Any, ...]] | None = None,
    acl_event_postflight_rows: list[tuple[Any, ...]] | None = None,
) -> _Cursor:
    fetchall_results: list[list[tuple[Any, ...]]] = [[(role,)]]
    if verifier_role is not None:
        fetchall_results.append([(verifier_role,)])
    fetchall_results.extend(
        [
            [("analytics",), ("mip_app",), ("public",)],
            _all_table_inventory_rows(),
            _all_sequence_inventory_rows(),
            _routine_inventory_rows(),
            list(column_acl_rows or []),
            [
                (role, 'owner-"quoted"', "analytics", "r"),
                *(
                    [(verifier_role, "global-owner", None, "S")]
                    if verifier_role is not None
                    else []
                ),
            ],
            list(acl_event_preflight_rows or []),
            [(role,)],
            _table_rows(),
            _table_privilege_rows(),
            [],
            _sequence_rows(),
            _sequence_privilege_rows(),
            [],
            [("mip_app", "USAGE"), ("public", "USAGE")],
            [],
            [],
            _app_routine_privilege_rows(),
            [_safe_role_security_row(role)],
            [],
            [],
            [],
            list(trigger_rows if trigger_rows is not None else _trigger_inventory_rows()),
        ]
    )
    fetchone_results = [
        ("mip_app_state",),
        (True, False, False, True, False),
    ]
    if verifier_role is not None:
        fetchall_results.extend(
            [
                [(verifier_role,)],
                [("mip_app", "USAGE"), ("public", "USAGE")],
                _table_rows(),
                _verifier_table_privilege_rows(),
                [],
                [],
                _sequence_rows(),
                [],
                [],
                [],
                [_safe_role_security_row(verifier_role)],
                [],
                [],
                [],
                _trigger_inventory_rows(),
            ]
        )
        fetchone_results.append((True, False, False, True, False))
    fetchall_results.append(list(acl_event_postflight_rows or []))
    return _Cursor(
        fetchall_results=fetchall_results,
        fetchone_results=fetchone_results,
    )


def test_resolve_app_role_uses_authoritative_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    class _Apps:
        def get(self, name: str) -> object:
            requested.append(name)
            return SimpleNamespace(
                service_principal_client_id="sp-client-id",
                service_principal_name="wrong-display-name",
                service_principal_id=12345,
            )

    monkeypatch.setenv("MIP_APP_NAME", "customer-mip")
    client = SimpleNamespace(apps=_Apps())

    assert lakebase_migrate._resolve_app_role(client) == "sp-client-id"
    assert requested == ["customer-mip"]


def test_resolve_app_role_explicit_name_overrides_ambient_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []
    monkeypatch.setenv("MIP_APP_NAME", "wrong-ambient-app")
    client = SimpleNamespace(
        apps=SimpleNamespace(
            get=lambda name: (
                requested.append(name)
                or SimpleNamespace(service_principal_client_id="staging-app-client-id")
            )
        )
    )

    assert (
        lakebase_migrate._resolve_app_role(client, app_name="mip-app-pr105-staging")
        == "staging-app-client-id"
    )
    assert requested == ["mip-app-pr105-staging"]


def test_schema_and_seed_run_in_one_rollback_capable_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _FailingCursor(fetchall_results=[[], [], []], fail_when="SEED")
    connection = _Connection(cursor)
    connect_kwargs: dict[str, Any] = {}

    import psycopg

    def _connect(**kwargs: Any) -> _Connection:
        connect_kwargs.update(kwargs)
        return connection

    monkeypatch.setattr(psycopg, "connect", _connect)

    with pytest.raises(RuntimeError, match="injected database failure"):
        lakebase_migrate._run_transaction(
            ("SCHEMA", "SEED"),
            {},
            app_role="app-role",
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    assert connect_kwargs["autocommit"] is False
    assert [statement for statement, _params in cursor.executed][-2:] == ["SCHEMA", "SEED"]
    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_integrity_probe_and_exact_trigger_postflight_run_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _Cursor(fetchall_results=[[], [], [], _trigger_inventory_rows(), []])
    connection = _Connection(cursor)
    probe_calls: list[tuple[dict[str, str], object]] = []

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    def _record_probe(kwargs: dict[str, str], *, connection: object) -> None:
        probe_calls.append((kwargs, connection))
        cursor.executed.append(("OUTREACH-INTEGRITY-PROBE", None))

    monkeypatch.setattr(
        lakebase_migrate,
        "_run_outreach_integrity_probe",
        _record_probe,
    )

    lakebase_migrate._run_transaction(
        ("SCHEMA", "SEED", "POST-SEED"),
        {"host": "test"},
        app_role="app-role",
        verify_outreach_integrity=True,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )

    assert probe_calls == [({"host": "test"}, connection)]
    statements = [statement for statement, _params in cursor.executed]
    assert statements[-3] == "OUTREACH-INTEGRITY-PROBE"
    assert "FROM pg_trigger trigger" in statements[-2]
    assert "FROM pg_event_trigger event_trigger" in statements[-1]
    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert connection.closed is True


def test_shape_correct_existing_trigger_is_locked_and_quarantined_before_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_trigger = _trigger_inventory_rows()[0]
    trigger_key = existing_trigger[:3]
    cursor = _Cursor(
        fetchall_results=[[], [], [existing_trigger], _trigger_inventory_rows(), []],
    )
    connection = _Connection(cursor)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    lakebase_migrate._run_transaction(
        ("SCHEMA",),
        {},
        app_role="app-role",
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )

    statements = [statement for statement, _params in cursor.executed]
    table_schema, table_name, trigger_name = trigger_key
    assert statements[3] == (f'LOCK TABLE "{table_schema}"."{table_name}" IN ACCESS EXCLUSIVE MODE')
    assert statements[4] == (f'DROP TRIGGER "{trigger_name}" ON "{table_schema}"."{table_name}"')
    assert "IF EXISTS" not in statements[4]
    assert statements[5] == "SCHEMA"
    assert "FROM pg_trigger trigger" in statements[6]
    assert "FROM pg_event_trigger event_trigger" in statements[7]
    assert connection.commit_count == 1
    assert connection.rollback_count == 0


def test_quarantined_reviewed_trigger_is_transactionally_restored_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_trigger = _trigger_inventory_rows()[0]
    cursor = _FailingCursor(
        fetchall_results=[[], [], [existing_trigger]],
        fail_when="SCHEMA",
    )
    connection = _Connection(cursor)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match="injected database failure"):
        lakebase_migrate._run_transaction(
            ("SCHEMA",),
            {},
            app_role="app-role",
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    statements = [statement for statement, _params in cursor.executed]
    assert statements[3].startswith("LOCK TABLE ")
    assert statements[4].startswith("DROP TRIGGER ")
    assert statements[5] == "SCHEMA"
    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_hostile_existing_trigger_aborts_before_schema_or_seed_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile_rows = _trigger_inventory_rows()
    hostile_rows.append(
        (
            "public",
            "borrower_export_view",
            "trg_exfiltrate",
            "O",
            23,
            0,
            True,
            True,
            "public",
            "exfiltrate",
            "",
            "f",
            "trigger",
            True,
            "attacker-owner",
            "migration-owner",
            False,
            False,
            False,
            False,
            True,
            True,
            True,
            True,
            True,
        )
    )
    cursor = _Cursor(fetchall_results=[[], [], hostile_rows])
    connection = _Connection(cursor)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match=r"schema preflight.*exfiltrate.*True"):
        lakebase_migrate._run_transaction(
            ("SCHEMA", "SEED"),
            {},
            app_role="app-role",
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    statements = [statement for statement, _params in cursor.executed]
    assert len(statements) == 3
    assert "FROM pg_attrdef attribute_default" in statements[0]
    assert "FROM pg_event_trigger event_trigger" in statements[1]
    assert "FROM pg_trigger trigger" in statements[2]
    assert "SCHEMA" not in statements
    assert "SEED" not in statements
    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.closed is True


@pytest.mark.parametrize(
    "hostile_hook",
    [
        _schema_hook_row("column_default"),
        _schema_hook_row("generated_expression", dependency_kind=None),
        _schema_hook_row("constraint_expression"),
        _schema_hook_row("rewrite_rule", dependency_kind=None),
        _schema_hook_row("row_policy", dependency_kind=None),
        _schema_hook_row("index_expression"),
        _schema_hook_row("index_predicate"),
    ],
    ids=[
        "default",
        "generated",
        "constraint",
        "rewrite-rule",
        "row-policy",
        "index-expression",
        "index-predicate",
    ],
)
def test_hostile_executable_hook_aborts_as_first_catalog_gate(
    monkeypatch: pytest.MonkeyPatch,
    hostile_hook: tuple[Any, ...],
) -> None:
    cursor = _Cursor(fetchall_results=[[hostile_hook]])
    connection = _Connection(cursor)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match="executable-hook inventory mismatch"):
        lakebase_migrate._run_transaction(
            ("SCHEMA", "SEED"),
            {},
            app_role="app-role",
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    statements = [statement for statement, _params in cursor.executed]
    assert len(statements) == 1
    assert "FROM pg_attrdef attribute_default" in statements[0]
    assert "FROM pg_event_trigger event_trigger" not in statements[0]
    assert "FROM pg_trigger trigger" not in statements[0]
    assert "SCHEMA" not in statements
    assert "SEED" not in statements
    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_executable_hook_query_avoids_reserved_collation_alias() -> None:
    cursor = _Cursor(fetchall_results=[[]])

    assert lakebase_migrate._preflight_executable_schema_hooks(cursor) == set()

    query = cursor.executed[0][0]
    assert "LEFT JOIN pg_collation collation_object" in query
    assert "collation_object.collname" in query
    assert "collation_object.collowner" in query
    assert "collation_object.collnamespace" in query
    assert "LEFT JOIN pg_collation collation\n" not in query
    assert "current_user::regrole" not in query
    assert "WHERE rolname = current_user" in query
    assert "CROSS JOIN current_executor" in query
    assert query.count("namespace.nspname <> '__db_system'") == 6


def test_schema_hook_lexing_ignores_literals_and_sql_grouping_keywords() -> None:
    constraint = (
        "CHECK (competitor_lender_label IS NULL OR "
        "competitor_lender_label ~ '^Competitor ([A-Z]|Other)$'::text)"
    )
    predicate = (
        "((request_id IS NOT NULL) AND "
        "(event_type = ANY (ARRAY['ADMIN_OPERATION_RUN'::text])))"
    )

    assert lakebase_migrate._schema_hook_function_calls(constraint) == {"check"}
    assert lakebase_migrate._schema_hook_function_calls(predicate) == {"and", "any"}


@pytest.mark.parametrize(
    ("function_name", "signature"),
    [
        ("pg_read_file", "text, bigint, bigint, boolean"),
        ("current_setting", "text"),
        ("set_config", "text, text, boolean"),
        ("lo_import", "text"),
    ],
)
def test_executable_hook_rejects_unreviewed_privileged_pg_catalog_routines(
    function_name: str,
    signature: str,
) -> None:
    row = _schema_hook_row(
        "column_default",
        table="schema_migrations",
        hook_name="applied_at",
        expression=f"{function_name}('governance-secret')",
        dependency_schema="pg_catalog",
        dependency_name=function_name,
        dependency_arguments=signature,
        security_definer=False,
    )
    cursor = _Cursor(fetchall_results=[[row]])

    with pytest.raises(RuntimeError, match=rf"unreviewed_function_call.*{function_name}"):
        lakebase_migrate._preflight_executable_schema_hooks(cursor)


@pytest.mark.parametrize(
    ("dependency_schema", "operator_name", "signature"),
    [
        ("attacker", "===", "text, text"),
        ("pg_catalog", "@@@", "text, text"),
    ],
)
def test_executable_hook_rejects_unreviewed_operator_dependencies(
    dependency_schema: str,
    operator_name: str,
    signature: str,
) -> None:
    row = _schema_hook_row(
        "constraint_expression",
        hook_name="hostile_operator_check",
        expression=f"CHECK ((status {operator_name} 'draft'::text))",
        dependency_kind="operator",
        dependency_schema=dependency_schema,
        dependency_name=operator_name,
        dependency_arguments=signature,
        security_definer=False,
    )

    with pytest.raises(RuntimeError, match=rf"executable-hook.*{re.escape(operator_name)}"):
        lakebase_migrate._preflight_executable_schema_hooks(
            _Cursor(fetchall_results=[[row]])
        )


def test_executable_hook_binds_nextval_to_reviewed_audit_sequence() -> None:
    expression = lakebase_migrate._AUDIT_SEQUENCE_DEFAULT_EXPRESSION
    rows = [
        _schema_hook_row(
            "column_default",
            table="action_audit",
            hook_name="audit_sequence",
            expression=expression,
            dependency_schema="pg_catalog",
            dependency_name="nextval",
            dependency_arguments="regclass",
            security_definer=False,
        ),
        _schema_hook_row(
            "column_default",
            table="action_audit",
            hook_name="audit_sequence",
            expression=expression,
            dependency_kind="relation",
            dependency_schema="mip_app",
            dependency_name="action_audit",
            security_definer=False,
        ),
        _schema_hook_row(
            "column_default",
            table="action_audit",
            hook_name="audit_sequence",
            expression=expression,
            dependency_kind="relation",
            dependency_schema="mip_app",
            dependency_name="action_audit_audit_sequence_seq",
            security_definer=False,
            owned_by_executor=True,
        ),
    ]

    assert lakebase_migrate._preflight_executable_schema_hooks(
        _Cursor(fetchall_results=[rows])
    ) == set()

    hostile_rows = [list(row) for row in rows]
    hostile_rows[2][4] = "nextval('attacker.sequence'::regclass)"
    hostile_rows[2][7] = "attacker_sequence"
    with pytest.raises(RuntimeError, match="audit_sequence_default_contract_mismatch"):
        lakebase_migrate._preflight_executable_schema_hooks(
            _Cursor(fetchall_results=[[tuple(row) for row in hostile_rows]])
        )


def test_reviewed_campaign_constraints_are_quarantined_by_exact_dependency() -> None:
    rows = [
        _schema_hook_row(
            "constraint_expression",
            table=table,
            hook_name=constraint,
            expression=f"CHECK (mip_app.{next(iter(dependencies))[0]}(criteria))",
            dependency_schema="mip_app",
            dependency_name=next(iter(dependencies))[0],
            dependency_arguments=next(iter(dependencies))[1],
            security_definer=False,
            owned_by_executor=True,
        )
        for (
            _schema,
            table,
            constraint,
        ), dependencies in lakebase_migrate._QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT.items()
    ]
    cursor = _Cursor(fetchall_results=[rows])

    reviewed = lakebase_migrate._preflight_executable_schema_hooks(cursor)
    assert reviewed == set(lakebase_migrate._QUARANTINED_CONSTRAINT_ROUTINE_CONTRACT)

    quarantine_cursor = _Cursor(fetchall_results=[])
    lakebase_migrate._quarantine_reviewed_constraints(quarantine_cursor, reviewed)
    statements = [statement for statement, _params in quarantine_cursor.executed]
    assert statements[0] == 'LOCK TABLE "mip_app"."campaigns" IN ACCESS EXCLUSIVE MODE'
    assert len([statement for statement in statements if "DROP CONSTRAINT" in statement]) == 6


def test_exact_trigger_postflight_failure_rolls_back_schema_and_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete_postflight = _trigger_inventory_rows()[:-1]
    cursor = _Cursor(fetchall_results=[[], [], [], incomplete_postflight])
    connection = _Connection(cursor)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match="schema postflight.*missing"):
        lakebase_migrate._run_transaction(
            ("SCHEMA", "SEED"),
            {},
            app_role="app-role",
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    statements = [statement for statement, _params in cursor.executed]
    assert statements[3:5] == ["SCHEMA", "SEED"]
    assert "FROM pg_trigger trigger" in statements[-1]
    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_hostile_event_trigger_aborts_before_row_trigger_quarantine_or_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _Cursor(fetchall_results=[[], [_hostile_event_trigger_row()]])
    connection = _Connection(cursor)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match=r"event-trigger inventory mismatch.*exfiltrate"):
        lakebase_migrate._run_transaction(
            ("SCHEMA", "SEED"),
            {},
            app_role="app-role",
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    statements = [statement for statement, _params in cursor.executed]
    assert len(statements) == 2
    assert "FROM pg_attrdef attribute_default" in statements[0]
    assert "FROM pg_event_trigger event_trigger" in statements[1]
    assert "FROM pg_trigger trigger" not in statements[1]
    assert "SCHEMA" not in statements
    assert "SEED" not in statements
    assert connection.commit_count == 0
    assert connection.rollback_count == 1


def test_event_trigger_postflight_failure_rolls_back_schema_and_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _Cursor(
        fetchall_results=[
            [],
            [],
            [],
            _trigger_inventory_rows(),
            [_hostile_event_trigger_row()],
        ]
    )
    connection = _Connection(cursor)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match=r"schema postflight.*event-trigger.*exfiltrate"):
        lakebase_migrate._run_transaction(
            ("SCHEMA", "SEED"),
            {},
            app_role="app-role",
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    statements = [statement for statement, _params in cursor.executed]
    assert statements[3:5] == ["SCHEMA", "SEED"]
    assert "FROM pg_trigger trigger" in statements[-2]
    assert "FROM pg_event_trigger event_trigger" in statements[-1]
    assert connection.commit_count == 0
    assert connection.rollback_count == 1


def test_expected_database_rejection_recovers_outer_transaction() -> None:
    cursor = _RejectingProbeCursor()

    lakebase_migrate._expect_database_rejection(
        cursor,
        savepoint="probe_guard",
        statement="FORBIDDEN",
        expected_sqlstates=("42501",),
    )

    assert [statement for statement, _params in cursor.executed] == [
        "SAVEPOINT probe_guard",
        "FORBIDDEN",
        "ROLLBACK TO SAVEPOINT probe_guard",
        "RELEASE SAVEPOINT probe_guard",
    ]


def test_expected_database_rejection_fails_when_guard_accepts_mutation() -> None:
    cursor = _RejectingProbeCursor(reject=False)

    with pytest.raises(RuntimeError, match="accepted a forbidden mutation"):
        lakebase_migrate._expect_database_rejection(
            cursor,
            savepoint="probe_guard",
            statement="FORBIDDEN",
            expected_sqlstates=("42501",),
        )


def test_main_runs_seed_and_integrity_probe_in_schema_transaction_before_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda: {"host": "test"})
    monkeypatch.setattr(lakebase_migrate, "_repo_root", lambda: Path("."))

    def _preflight_roles(
        _kwargs: dict[str, str],
        **_options: object,
    ) -> tuple[str, None]:
        calls.append("role-preflight")
        return "app-role", None

    monkeypatch.setattr(
        lakebase_migrate,
        "_preflight_database_roles",
        _preflight_roles,
    )

    def _run_transaction(
        sql_texts: tuple[str, ...],
        _kwargs: dict[str, str],
        *,
        app_role: str,
        ai_gateway_verifier_role: str | None,
        verify_outreach_integrity: bool,
    ) -> None:
        assert len(sql_texts) == 4
        assert "CREATE TABLE IF NOT EXISTS mip_app.schema_migrations" in sql_texts[0]
        assert "INSERT INTO mip_app.campaign_message_variants" in sql_texts[1]
        assert "configured tenant disclosure postflight failed" in sql_texts[2]
        assert "VALIDATE CONSTRAINT approvals_campaign_variant_channel_fkey" in sql_texts[3]
        assert app_role == "app-role"
        assert ai_gateway_verifier_role is None
        assert verify_outreach_integrity is True
        calls.append("schema-seed-post-seed-integrity")

    monkeypatch.setattr(lakebase_migrate, "_run_transaction", _run_transaction)

    def _apply_grants(
        _kwargs: dict[str, str],
        *,
        resolved_roles: tuple[str, str | None],
    ) -> None:
        assert resolved_roles == ("app-role", None)
        calls.append("grants")

    monkeypatch.setattr(lakebase_migrate, "_apply_app_role_grants", _apply_grants)

    lakebase_migrate.main()

    assert calls == ["role-preflight", "schema-seed-post-seed-integrity", "grants"]


@pytest.mark.parametrize(
    "fetchall_results",
    ([[]], [[("app-role",)], []]),
    ids=("app", "ai-gateway-verifier"),
)
def test_unknown_runtime_role_aborts_main_before_schema_statement_or_commit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fetchall_results: list[list[tuple[str, ...]]],
) -> None:
    cursor = _Cursor(fetchall_results=fetchall_results)
    connection = _Connection(cursor)
    connect_kwargs: dict[str, Any] = {}

    import psycopg

    def _connect(**kwargs: Any) -> _Connection:
        connect_kwargs.update(kwargs)
        return connection

    monkeypatch.setattr(psycopg, "connect", _connect)
    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda: {"host": "test"})
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: "app-role")
    monkeypatch.setenv("MIP_LAKEBASE_APP_ROLE_WAIT_TIMEOUT_S", "0")
    transaction_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        lakebase_migrate,
        "_run_transaction",
        lambda sql_texts, *_args, **_kwargs: transaction_calls.append(sql_texts),
    )

    with pytest.raises(SystemExit) as exc_info:
        lakebase_migrate.main(
            ai_gateway_verifier_client_id="verifier-role",
            require_ai_gateway_verifier=True,
        )

    assert exc_info.value.code == 2
    assert transaction_calls == []
    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.closed is True
    assert connect_kwargs["autocommit"] is False
    assert cursor.executed[0] == ("SET TRANSACTION READ ONLY", None)
    assert all(
        statement == "SELECT rolname FROM pg_roles WHERE rolname = %s"
        for statement, _params in cursor.executed[1:]
    )
    assert capsys.readouterr().err == (
        "[lakebase-migrate] runtime-role preflight failed; verify App and verifier "
        "workspace identities and Lakebase role visibility before schema mutation.\n"
    )


def test_role_preflight_returns_exact_roles_without_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _Cursor(
        fetchall_results=[
            [("app-role",)],
            [("verifier-role",)],
        ]
    )
    connection = _Connection(cursor)
    connect_kwargs: dict[str, Any] = {}

    import psycopg

    def _connect(**kwargs: Any) -> _Connection:
        connect_kwargs.update(kwargs)
        return connection

    monkeypatch.setattr(psycopg, "connect", _connect)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: "app-role")

    roles = lakebase_migrate._preflight_database_roles(
        {"host": "test"},
        ai_gateway_verifier_client_id="verifier-role",
        require_ai_gateway_verifier=True,
        role_wait_timeout_s=0,
        role_wait_interval_s=1,
    )

    assert roles == ("app-role", "verifier-role")
    assert connect_kwargs == {"host": "test", "autocommit": False}
    assert cursor.executed == [
        ("SET TRANSACTION READ ONLY", None),
        (
            "SELECT rolname FROM pg_roles WHERE rolname = %s",
            ("app-role",),
        ),
        (
            "SELECT rolname FROM pg_roles WHERE rolname = %s",
            ("verifier-role",),
        ),
    ]
    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_campaign_variant_schema_preserves_legacy_rows_but_blocks_new_operator_copy() -> None:
    schema = (Path(__file__).parents[2] / "lakebase" / "schema.sql").read_text()
    pre_seed, post_seed = lakebase_migrate._split_schema_sql(schema)

    assert "DROP CONSTRAINT IF EXISTS campaign_message_variants_server_owned_proof_chk" in pre_seed
    assert "ADD CONSTRAINT campaign_message_variants_server_owned_proof_chk" in post_seed
    assert "generation_mode IN ('supervisor', 'reviewed_fallback')" in post_seed
    assert "provenance_copy_hash IS NOT NULL" in post_seed
    assert "provenance_criteria_fingerprint IS NOT NULL" in post_seed
    assert "provenance_token_digest IS NOT NULL" in post_seed
    assert "NOT VALID" in post_seed
    assert "ALTER COLUMN generation_mode DROP DEFAULT" in post_seed


def test_tenant_disclosure_seed_is_immutable_convergent_and_postflighted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        lender_identity._REVIEWED_PUBLIC_LENDER_IDENTITIES,
        "O'Brien Mortgage",
        frozenset({"7654321", "7654322"}),
    )
    sql = lakebase_migrate._tenant_disclosure_seed_sql(
        lender_name="O'Brien Mortgage",
        lender_nmls_id="7654321",
        tenant_id="",
    )

    assert "tenant_id = 'o_brien_mortgage'" in sql
    assert "O''Brien Mortgage" in sql
    assert "SET active = FALSE" in sql
    assert "DO UPDATE SET\n    body" not in sql
    assert "WHERE target.body = EXCLUDED.body" in sql
    assert "configured tenant disclosure postflight failed" in sql
    assert sql.count("'o_brien_mortgage-reviewed-generic-v1-") == 6

    changed_identity = lakebase_migrate._tenant_disclosure_seed_sql(
        lender_name="O'Brien Mortgage",
        lender_nmls_id="7654322",
        tenant_id="",
    )
    first_version = re.search(r"o_brien_mortgage-reviewed-generic-v1-[0-9a-f]{16}", sql)
    changed_version = re.search(
        r"o_brien_mortgage-reviewed-generic-v1-[0-9a-f]{16}", changed_identity
    )
    assert first_version is not None and changed_version is not None
    assert first_version.group() != changed_version.group()


def test_customer_disclosure_identity_cannot_reuse_summit_or_demo_nmls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="required when mip_lender_name"):
        lakebase_migrate._tenant_disclosure_seed_sql(
            lender_name="Acme Mortgage",
            lender_nmls_id="",
            tenant_id="",
        )
    monkeypatch.setitem(
        lender_identity._REVIEWED_PUBLIC_LENDER_IDENTITIES,
        "Acme Mortgage",
        frozenset({"7654321"}),
    )
    with pytest.raises(ValueError, match="reserved for the Summit Mortgage"):
        lakebase_migrate._tenant_disclosure_seed_sql(
            lender_name="Acme Mortgage",
            lender_nmls_id="7654321",
            tenant_id="summit",
        )


@pytest.mark.parametrize(
    "app",
    [
        SimpleNamespace(service_principal_client_id=None),
        SimpleNamespace(service_principal_client_id="   "),
    ],
)
def test_resolve_app_role_fails_closed_when_client_id_is_missing(app: object) -> None:
    client = SimpleNamespace(apps=SimpleNamespace(get=lambda _name: app))

    with pytest.raises(RuntimeError, match="missing service_principal_client_id"):
        lakebase_migrate._resolve_app_role(client)


def test_resolve_app_role_fails_closed_on_apps_lookup_error() -> None:
    def _raise(_name: str) -> object:
        raise OSError("workspace unavailable")

    client = SimpleNamespace(apps=SimpleNamespace(get=_raise))

    with pytest.raises(RuntimeError, match="Databricks Apps lookup failed"):
        lakebase_migrate._resolve_app_role(client)


def test_resolve_ai_gateway_verifier_role_uses_explicit_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "  verifier-client-id  ")

    assert lakebase_migrate._resolve_ai_gateway_verifier_role() == "verifier-client-id"


def test_explicit_verifier_argument_overrides_ambient_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "wrong-ambient-verifier")

    assert (
        lakebase_migrate._resolve_ai_gateway_verifier_role(
            "  exact-remote-verifier  ",
            required=True,
        )
        == "exact-remote-verifier"
    )


@pytest.mark.parametrize("value", ["", "00000000PLACEHOLDER", "<verifier-client-id>"])
def test_required_remote_verifier_rejects_missing_or_placeholder_identity(value: str) -> None:
    with pytest.raises(RuntimeError, match="MIP_AI_GATEWAY_VERIFIER_CLIENT_ID"):
        lakebase_migrate._resolve_ai_gateway_verifier_role(value, required=True)


def test_required_remote_verifier_fails_before_connection_or_schema_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_attempted = False

    def _resolve_connection(**_kwargs: object) -> dict[str, str]:
        nonlocal connection_attempted
        connection_attempted = True
        return {"host": "must-not-be-used"}

    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", _resolve_connection)

    with pytest.raises(SystemExit) as exc_info:
        lakebase_migrate.main(
            ai_gateway_verifier_client_id="00000000PLACEHOLDER",
            require_ai_gateway_verifier=True,
        )

    assert exc_info.value.code == 2
    assert connection_attempted is False


@pytest.mark.parametrize("app_env", ["local", "test", "dev", "sandbox"])
def test_missing_verifier_role_is_allowed_in_dev_and_test(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)

    assert lakebase_migrate._resolve_ai_gateway_verifier_role() is None


@pytest.mark.parametrize("app_env", ["prod", "production", "customer"])
def test_missing_verifier_role_fails_closed_outside_dev_test(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)

    with pytest.raises(RuntimeError, match="MIP_AI_GATEWAY_VERIFIER_CLIENT_ID is required"):
        lakebase_migrate._resolve_ai_gateway_verifier_role()


def test_prod_bundle_target_requires_verifier_even_with_test_app_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABRICKS_BUNDLE_TARGET", "prod")

    with pytest.raises(RuntimeError, match="MIP_AI_GATEWAY_VERIFIER_CLIENT_ID is required"):
        lakebase_migrate._resolve_ai_gateway_verifier_role()


def test_apply_grants_uses_exact_quoted_role_and_strict_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = 'sp-client-"quoted"'
    cursor = _successful_cursor(role)
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    lakebase_migrate._apply_app_role_grants(
        {},
        role_wait_timeout_s=0,
        role_wait_interval_s=1,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )

    assert connection.closed is True
    assert connection.commit_count == 2
    assert connection.rollback_count == 0
    assert cursor.executed[0] == (
        "SELECT rolname FROM pg_roles WHERE rolname = %s",
        (role,),
    )
    statements = [statement for statement, _params in cursor.executed]
    grant_statements = [statement for statement in statements if statement.startswith("GRANT ")]
    revoke_indexes = [
        index
        for index, statement in enumerate(statements)
        if statement.startswith("REVOKE ") or " REVOKE " in statement
    ]
    first_grant = min(
        index for index, statement in enumerate(statements) if statement.startswith("GRANT ")
    )

    assert max(revoke_indexes) < first_grant
    assert all(
        "ANY(" not in statement
        for statement in statements
        if statement.startswith(("GRANT ", "REVOKE ", "ALTER DEFAULT PRIVILEGES "))
    )
    assert all(" ON ALL " not in statement for statement in statements)
    assert all("DELETE" not in statement for statement in grant_statements)
    assert not any("DEFAULT PRIVILEGES" in statement for statement in grant_statements)
    assert any(statement.endswith('TO "sp-client-""quoted"""') for statement in grant_statements)
    assert (
        'GRANT SELECT, INSERT ON TABLE "mip_app"."action_audit" ' 'TO "sp-client-""quoted"""'
    ) in grant_statements
    assert (
        'GRANT SELECT, INSERT ON TABLE "mip_app"."generated_outreach_drafts" '
        'TO "sp-client-""quoted"""'
    ) in grant_statements
    assert (
        'GRANT SELECT, INSERT ON TABLE "mip_app"."campaign_message_variants" '
        'TO "sp-client-""quoted"""'
    ) in grant_statements
    assert (
        'GRANT SELECT ON TABLE "mip_app"."ai_gateway_proof_ledger" ' 'TO "sp-client-""quoted"""'
    ) in grant_statements
    assert not any(
        'TABLE "mip_app"."ai_gateway_proof_ledger"' in statement
        and ("INSERT" in statement or "UPDATE" in statement)
        for statement in grant_statements
    )
    assert (
        'GRANT USAGE ON SEQUENCE "mip_app"."action_audit_audit_sequence_seq" '
        'TO "sp-client-""quoted"""'
    ) in grant_statements
    assert 'REVOKE CREATE ON DATABASE "mip_app_state" FROM "sp-client-""quoted"""' in statements
    assert 'REVOKE TEMPORARY ON DATABASE "mip_app_state" FROM PUBLIC' in statements
    assert 'REVOKE TEMPORARY ON DATABASE "mip_app_state" FROM "sp-client-""quoted"""' in statements
    assert (
        'ALTER DEFAULT PRIVILEGES FOR ROLE "owner-""quoted""" IN SCHEMA "analytics" '
        'REVOKE ALL PRIVILEGES ON TABLES FROM "sp-client-""quoted"""'
    ) in statements
    assert ('REVOKE ALL PRIVILEGES ON SCHEMA "public" FROM "sp-client-""quoted"""') in statements
    assert ('REVOKE ALL PRIVILEGES ON SCHEMA "analytics" FROM "sp-client-""quoted"""') in statements
    assert (
        'REVOKE ALL PRIVILEGES ON TABLE "analytics"."borrower_export" '
        'FROM "sp-client-""quoted"""'
    ) in statements
    assert (
        'REVOKE ALL PRIVILEGES ON TABLE "public"."borrower_export_view" '
        'FROM "sp-client-""quoted"""'
    ) in statements
    assert (
        'REVOKE ALL PRIVILEGES ON SEQUENCE "analytics"."export_sequence" '
        'FROM "sp-client-""quoted"""'
    ) in statements
    assert 'REVOKE ALL PRIVILEGES ON FUNCTION "public"."exfiltrate"() FROM PUBLIC' in statements
    assert not any(
        '"public"."databricks_create_role"' in statement
        and statement.startswith("REVOKE ")
        for statement in statements
    )
    assert (
        'REVOKE ALL PRIVILEGES ON FUNCTION "analytics"."score"(integer) '
        'FROM "sp-client-""quoted"""'
    ) in statements
    assert (
        'REVOKE ALL PRIVILEGES ON FUNCTION "analytics"."score"(text) ' 'FROM "sp-client-""quoted"""'
    ) in statements
    assert (
        'ALTER DEFAULT PRIVILEGES FOR ROLE "migration-owner" '
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    ) in statements
    assert any(
        statement.startswith('GRANT EXECUTE ON FUNCTION "mip_app".')
        and statement.endswith('TO "sp-client-""quoted"""')
        for statement in grant_statements
    )
    assert not any(
        "enforce_campaign_json_contract" in statement and statement.startswith("GRANT EXECUTE")
        for statement in statements
    )


def test_acl_reconciliation_rolls_back_on_mid_grant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "app-role"
    cursor = _FailingCursor(
        fetchall_results=[
            [(role,)],
            [("mip_app",), ("public",)],
            [],
            [],
            _routine_inventory_rows(),
            [],
            [],
            [],
            [],
        ],
        fetchone_results=[("mip_app_state",)],
        fail_when="GRANT USAGE ON SCHEMA",
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match="injected database failure"):
        lakebase_migrate._apply_app_role_grants(
            {},
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    # The first commit only ends read-only role discovery. All ACL mutations
    # are in the second transaction and are rolled back together.
    assert connection.commit_count == 1
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_event_trigger_aborts_before_first_acl_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "app-role"
    cursor = _successful_cursor(
        role,
        acl_event_preflight_rows=[_hostile_event_trigger_row()],
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match=r"ACL preflight.*event-trigger.*exfiltrate"):
        lakebase_migrate._apply_app_role_grants(
            {},
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    statements = [statement for statement, _params in cursor.executed]
    assert "FROM pg_event_trigger event_trigger" in statements[-1]
    assert not any(
        statement.startswith(("GRANT ", "REVOKE ", "ALTER DEFAULT PRIVILEGES "))
        for statement in statements
    )
    assert connection.commit_count == 1
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_event_trigger_acl_postflight_failure_rolls_back_all_acl_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "app-role"
    cursor = _successful_cursor(
        role,
        acl_event_postflight_rows=[_hostile_event_trigger_row()],
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match=r"ACL postflight.*event-trigger.*exfiltrate"):
        lakebase_migrate._apply_app_role_grants(
            {},
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    statements = [statement for statement, _params in cursor.executed]
    assert any(statement.startswith("REVOKE ") for statement in statements)
    assert any(statement.startswith("GRANT ") for statement in statements)
    assert "FROM pg_trigger trigger" in statements[-2]
    assert "FROM pg_event_trigger event_trigger" in statements[-1]
    assert connection.commit_count == 1
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_unreviewed_trigger_aborts_acl_transaction_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "app-role"
    cursor = _successful_cursor(role, trigger_rows=_trigger_inventory_rows()[:-1])
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match="global trigger inventory mismatch"):
        lakebase_migrate._apply_app_role_grants(
            {},
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    assert connection.commit_count == 1
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_apply_grants_reconciles_and_postflights_isolated_verifier_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "app-role"
    verifier_role = 'verifier-"quoted"'
    cursor = _successful_cursor(role, verifier_role)
    connection = _Connection(cursor)
    monkeypatch.setattr(
        lakebase_migrate,
        "_resolve_app_role",
        lambda: pytest.fail("grant reconciliation must reuse the preflight app role"),
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "wrong-ambient-verifier")

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    lakebase_migrate._apply_app_role_grants(
        {},
        resolved_roles=(role, verifier_role),
        role_wait_timeout_s=0,
        role_wait_interval_s=1,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )

    statements = [statement for statement, _params in cursor.executed]
    verifier_identifier = '"verifier-""quoted"""'
    verifier_grants = [
        statement
        for statement in statements
        if statement.startswith("GRANT ") and statement.endswith(f"TO {verifier_identifier}")
    ]
    assert verifier_grants == [
        f'GRANT USAGE ON SCHEMA "mip_app" TO {verifier_identifier}',
        "GRANT SELECT, INSERT, UPDATE ON TABLE "
        f'"mip_app"."ai_gateway_proof_ledger" TO {verifier_identifier}',
    ]
    assert not any(
        " ON SEQUENCE " in statement or " ON TABLES " in statement for statement in verifier_grants
    )
    assert (
        'ALTER DEFAULT PRIVILEGES FOR ROLE "global-owner" '
        'REVOKE ALL PRIVILEGES ON SEQUENCES FROM "verifier-""quoted"""'
    ) in statements
    assert 'REVOKE TEMPORARY ON DATABASE "mip_app_state" FROM "verifier-""quoted"""' in statements
    assert connection.commit_count == 2
    assert connection.rollback_count == 0


def test_apply_grants_revokes_public_app_and_verifier_column_acls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "app-role"
    verifier_role = 'verifier-"quoted"'
    cursor = _successful_cursor(
        role,
        verifier_role,
        column_acl_rows=[
            ("PUBLIC", "public", "borrower_export_view", "borrower id"),
            (role, "mip_app", "campaigns", "criteria"),
            (verifier_role, "mip_app", "ai_gateway_proof_ledger", "proof_payload"),
        ],
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", verifier_role)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    lakebase_migrate._apply_app_role_grants(
        {},
        role_wait_timeout_s=0,
        role_wait_interval_s=1,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )

    statements = [statement for statement, _params in cursor.executed]
    assert (
        'REVOKE ALL PRIVILEGES ("borrower id") ON TABLE '
        '"public"."borrower_export_view" FROM PUBLIC'
    ) in statements
    assert (
        'REVOKE ALL PRIVILEGES ("criteria") ON TABLE ' '"mip_app"."campaigns" FROM "app-role"'
    ) in statements
    assert (
        'REVOKE ALL PRIVILEGES ("proof_payload") ON TABLE '
        '"mip_app"."ai_gateway_proof_ledger" FROM "verifier-""quoted"""'
    ) in statements


def test_app_and_verifier_roles_must_be_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: "same-role")
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "same-role")

    with pytest.raises(RuntimeError, match="must identify a role distinct"):
        lakebase_migrate._apply_app_role_grants(
            {},
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )


def test_missing_exact_app_role_is_a_deployment_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "authoritative-client-id"
    cursor = _Cursor(fetchall_results=[[]])
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)
    monkeypatch.setattr(lakebase_migrate.time, "monotonic", lambda: 10.0)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match="authoritative app role not found"):
        lakebase_migrate._apply_app_role_grants(
            {},
            role_wait_timeout_s=0,
            role_wait_interval_s=1,
            allow_absent_managed_event_triggers=True,
            allow_absent_provider_schema=True,
        )

    assert cursor.executed == [
        (
            "SELECT rolname FROM pg_roles WHERE rolname = %s",
            (role,),
        )
    ]
    assert connection.closed is True
    assert connection.rollback_count == 1


def test_postflight_rejects_unreviewed_new_table() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(add="unreviewed_runtime_state"),
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="unexpected=.*unreviewed_runtime_state"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_postflight_rejects_non_exact_role() -> None:
    cursor = _Cursor(fetchall_results=[[("different-role",)]])

    with pytest.raises(RuntimeError, match="could not verify exact role"):
        lakebase_migrate._postflight_app_role_grants(cursor, "app-role")


def test_postflight_rejects_effective_database_create() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[[(role,)]],
        fetchone_results=[(True, True, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="database_create=True"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


@pytest.mark.parametrize(
    ("postflight", "role", "principal_label"),
    [
        (lakebase_migrate._postflight_app_role_grants, "app-role", "app-role"),
        (
            lakebase_migrate._postflight_ai_gateway_verifier_grants,
            "verifier-role",
            "AI Gateway verifier",
        ),
    ],
)
def test_postflight_rejects_effective_database_temporary(
    postflight: Any,
    role: str,
    principal_label: str,
) -> None:
    cursor = _Cursor(
        fetchall_results=[[(role,)]],
        fetchone_results=[(True, False, True, True, False)],
    )

    with pytest.raises(RuntimeError, match=rf"{principal_label}.*database_temporary=True"):
        postflight(cursor, role)


def test_postflight_rejects_effective_delete_privilege() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(add=("campaigns", "DELETE")),
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="forbidden DELETE"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_postflight_rejects_update_on_immutable_evidence_table() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(add=("generated_outreach_drafts", "UPDATE")),
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="table privilege postflight failed"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_verifier_postflight_rejects_access_to_any_other_table() -> None:
    role = "verifier-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            [("mip_app", "USAGE"), ("public", "USAGE")],
            _table_rows(),
            _verifier_table_privilege_rows() + [("campaigns", "SELECT")],
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="verifier table privilege postflight failed"):
        lakebase_migrate._postflight_ai_gateway_verifier_grants(cursor, role)


def test_postflight_rejects_missing_required_sequence_usage() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(),
            [],
            _sequence_rows(),
            [],
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="sequence privilege postflight failed"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


@pytest.mark.parametrize("object_type", ["r", "S", "f"])
def test_postflight_rejects_future_object_default_privilege(object_type: str) -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(),
            [],
            _sequence_rows(),
            _sequence_privilege_rows(),
            [
                (
                    "external-owner",
                    "analytics",
                    object_type,
                    "app-role",
                    (
                        "SELECT"
                        if object_type == "r"
                        else "USAGE"
                        if object_type == "S"
                        else "EXECUTE"
                    ),
                )
            ],
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="future table/sequence/routine default"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


@pytest.mark.parametrize(
    "attribute_index, attribute_name, expected_value",
    [
        (index, name, expected)
        for index, (name, expected) in enumerate(
            zip(
                lakebase_migrate._MANAGED_OAUTH_ROLE_ATTRIBUTE_NAMES,
                lakebase_migrate._MANAGED_OAUTH_ROLE_ATTRIBUTE_PROFILE,
                strict=True,
            ),
            start=1,
        )
    ],
)
def test_role_security_rejects_every_managed_oauth_role_attribute_drift(
    attribute_index: int,
    attribute_name: str,
    expected_value: bool,
) -> None:
    role = "app-role"
    row = list(_safe_role_security_row(role))
    row[attribute_index] = not expected_value
    cursor = _Cursor(fetchall_results=[[tuple(row)]])

    with pytest.raises(RuntimeError, match=attribute_name):
        lakebase_migrate._postflight_role_security(
            cursor,
            role,
            principal_label="app role",
        )


@pytest.mark.parametrize(
    ("role", "parent_membership"),
    [
        ("app-role", ("direct-reader", True, False, False, 1)),
        ("app-role", ("recursive-setter", False, True, False, 2)),
        ("verifier-role", ("admin-delegator", False, False, True, 1)),
        ("verifier-role", ("dormant-parent", False, False, False, 2)),
    ],
)
def test_role_security_rejects_every_parent_membership(
    role: str,
    parent_membership: tuple[str, bool, bool, bool, int],
) -> None:
    cursor = _Cursor(
        fetchall_results=[
            [_safe_role_security_row(role)],
            [parent_membership],
        ]
    )

    with pytest.raises(RuntimeError, match=rf"parent-role membership.*{parent_membership[0]}"):
        lakebase_migrate._postflight_role_security(
            cursor,
            role,
            principal_label="runtime principal",
        )

    membership_query = cursor.executed[1][0]
    assert "WITH RECURSIVE membership" in membership_query
    assert "pg_auth_members" in membership_query
    assert "pg_has_role(%s, parent.roleid, 'USAGE')" in membership_query
    assert "pg_has_role(%s, parent.roleid, 'SET')" in membership_query
    assert "admin_option_path" in membership_query


def test_role_security_rejects_inherit_or_login_profile_variation() -> None:
    role = "verifier-role"
    row = list(_safe_role_security_row(role))
    row[6] = False
    row[7] = False
    cursor = _Cursor(fetchall_results=[[tuple(row)], [], []])

    with pytest.raises(RuntimeError, match=r"rolinherit.*rolcanlogin"):
        lakebase_migrate._postflight_role_security(
            cursor,
            role,
            principal_label="AI Gateway verifier",
        )


@pytest.mark.parametrize(
    ("role", "delegated_member"),
    [
        ("app-role", ("direct-app-delegate", True, True, False, 1)),
        ("app-role", ("recursive-app-delegate", True, False, False, 2)),
        ("verifier-role", ("direct-verifier-delegate", False, True, True, 1)),
        ("verifier-role", ("dormant-recursive-delegate", False, False, False, 3)),
    ],
)
def test_role_security_rejects_direct_and_recursive_role_delegates(
    role: str,
    delegated_member: tuple[str, bool, bool, bool, int],
) -> None:
    cursor = _Cursor(
        fetchall_results=[
            [_safe_role_security_row(role)],
            [],
            [delegated_member],
        ]
    )

    with pytest.raises(RuntimeError, match=rf"role delegates.*{delegated_member[0]}"):
        lakebase_migrate._postflight_role_security(
            cursor,
            role,
            principal_label="runtime principal",
        )

    delegate_query = cursor.executed[2][0]
    assert "JOIN target ON target.oid = direct.roleid" in delegate_query
    assert "JOIN pg_auth_members child ON child.roleid = membership.member_oid" in delegate_query
    assert "pg_has_role(member.oid, target.oid, 'USAGE')" in delegate_query
    assert "pg_has_role(member.oid, target.oid, 'SET')" in delegate_query


@pytest.mark.parametrize(
    ("role", "grantee", "principal_label"),
    [
        ("app-role", "PUBLIC", "app role"),
        ("verifier-role", "verifier-role", "AI Gateway verifier"),
    ],
)
def test_column_postflight_rejects_public_and_direct_acl_entries(
    role: str,
    grantee: str,
    principal_label: str,
) -> None:
    cursor = _Cursor(
        fetchall_results=[
            [(grantee, "analytics", "borrower_export", "ssn", "SELECT")],
        ]
    )

    with pytest.raises(RuntimeError, match=rf"{principal_label}.*column privileges.*ssn"):
        lakebase_migrate._postflight_direct_column_privileges(
            cursor,
            role,
            principal_label=principal_label,
        )

    query = cursor.executed[0][0]
    assert "aclexplode(a.attacl)" in query
    assert "e.grantee = 0 OR grantee.rolname = %s" in query
    assert "c.relkind IN ('r', 'p', 'v', 'm', 'f')" in query
    assert "n.nspname NOT IN ('pg_catalog', 'information_schema')" in query


@pytest.mark.parametrize(
    ("role", "principal_label"),
    [
        ("app-role", "app role"),
        ("verifier-role", "AI Gateway verifier"),
    ],
)
def test_column_postflight_rejects_effective_column_only_capability(
    role: str,
    principal_label: str,
) -> None:
    cursor = _Cursor(
        fetchall_results=[
            [("analytics", "borrower_export", "REFERENCES")],
        ]
    )

    with pytest.raises(RuntimeError, match=rf"{principal_label}.*column-only.*REFERENCES"):
        lakebase_migrate._postflight_effective_column_only_privileges(
            cursor,
            role,
            principal_label=principal_label,
        )

    query, params = cursor.executed[0]
    assert "has_any_column_privilege(%s, c.oid, privilege.name)" in query
    assert "NOT has_table_privilege(%s, c.oid, privilege.name)" in query
    assert "c.relkind IN ('r', 'p', 'v', 'm', 'f')" in query
    assert params == (list(lakebase_migrate._COLUMN_PRIVILEGE_NAMES), role, role)


def test_trigger_contract_matches_every_schema_trigger_exactly() -> None:
    schema = Path("lakebase/schema.sql").read_text(encoding="utf-8")
    trigger_pattern = re.compile(
        r"CREATE TRIGGER\s+(?P<trigger>[a-z_]+)\s+"
        r"BEFORE\s+(?P<events>[A-Z ]+?)\s+"
        r"ON\s+(?P<table_schema>[a-z_]+)\.(?P<table_name>[a-z_]+)\s+"
        r"FOR EACH\s+(?P<level>ROW|STATEMENT)\s+"
        r"EXECUTE FUNCTION\s+"
        r"(?P<function_schema>[a-z_]+)\.(?P<function_name>[a-z_]+)"
        r"\((?P<function_arguments>[^)]*)\);",
        re.MULTILINE,
    )
    event_bits = {"INSERT": 4, "DELETE": 8, "UPDATE": 16, "TRUNCATE": 32}
    actual: dict[tuple[str, str, str], tuple[str, str, str, int]] = {}
    for match in trigger_pattern.finditer(schema):
        trigger_type = 2 + (1 if match.group("level") == "ROW" else 0)
        trigger_type += sum(event_bits[event] for event in match.group("events").split(" OR "))
        key = (
            match.group("table_schema"),
            match.group("table_name"),
            match.group("trigger"),
        )
        actual[key] = (
            match.group("function_schema"),
            match.group("function_name"),
            match.group("function_arguments"),
            trigger_type,
        )

    assert actual == lakebase_migrate._APP_TRIGGER_CONTRACT
    assert len(actual) == 14


def test_trigger_postflight_rejects_extra_public_security_definer_trigger() -> None:
    rows = _trigger_inventory_rows()
    rows.append(
        (
            "public",
            "borrower_export_view",
            "trg_exfiltrate",
            "O",
            23,
            0,
            True,
            True,
            "public",
            "exfiltrate",
            "",
            "f",
            "trigger",
            True,
            "attacker-owner",
            "migration-owner",
            False,
            False,
            False,
            False,
            True,
            True,
            True,
            True,
            True,
        )
    )
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match=r"trigger inventory mismatch.*exfiltrate.*True"):
        lakebase_migrate._postflight_trigger_inventory(
            cursor,
            "app-role",
            principal_label="app role",
        )

    query = cursor.executed[0][0]
    assert "FROM pg_trigger trigger" in query
    assert "NOT trigger.tgisinternal" in query
    assert "table_class.relkind IN ('r', 'p', 'v', 'm', 'f')" in query
    assert "function_proc.prosecdef" in query
    assert "function_owner.rolname = %s" in query


def test_trigger_postflight_rejects_missing_reviewed_trigger() -> None:
    rows = _trigger_inventory_rows()
    missing_trigger = rows.pop()[2]
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match=rf"trigger inventory mismatch.*{missing_trigger}"):
        lakebase_migrate._postflight_trigger_inventory(
            cursor,
            "verifier-role",
            principal_label="AI Gateway verifier",
        )


def test_trigger_postflight_rejects_security_definer_rewrite() -> None:
    rows = _trigger_inventory_rows()
    reviewed = list(rows[0])
    reviewed[13] = True
    rows[0] = tuple(reviewed)
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match=r"trigger inventory mismatch.*True"):
        lakebase_migrate._postflight_trigger_inventory(
            cursor,
            "app-role",
            principal_label="app role",
        )


def test_trigger_postflight_rejects_shared_arbitrary_owner() -> None:
    rows = _trigger_inventory_rows()
    reviewed = list(rows[0])
    reviewed[14] = "attacker-owner"
    reviewed[15] = "attacker-owner"
    reviewed[16] = True
    reviewed[17] = False
    reviewed[18] = False
    rows[0] = tuple(reviewed)
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match=r"trigger inventory mismatch.*attacker-owner"):
        lakebase_migrate._postflight_trigger_inventory(
            cursor,
            "app-role",
            principal_label="app role",
        )

    query = cursor.executed[0][0]
    assert "function_owner.oid = executor_role.oid" in query
    assert "table_owner.oid = executor_role.oid" in query
    assert "JOIN pg_roles executor_role ON executor_role.rolname = current_user" in query
    assert "current_user::regrole" not in query
    assert "function_owner.oid = table_owner.oid" in query


@pytest.mark.parametrize(
    ("field_index", "catalog_expression"),
    [
        (20, "trigger.tgattr = ''::int2vector"),
        (21, "trigger.tgnewtable IS NULL"),
        (22, "trigger.tgoldtable IS NULL"),
        (23, "NOT trigger.tgdeferrable"),
        (24, "NOT trigger.tginitdeferred"),
    ],
)
def test_trigger_postflight_rejects_column_transition_and_deferred_variants(
    field_index: int,
    catalog_expression: str,
) -> None:
    rows = _trigger_inventory_rows()
    hostile = list(rows[0])
    hostile[field_index] = False
    rows[0] = tuple(hostile)
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match="trigger inventory mismatch"):
        lakebase_migrate._postflight_trigger_inventory(
            cursor,
            "app-role",
            principal_label="app role",
        )

    assert catalog_expression in cursor.executed[0][0]


def test_trigger_postflight_rejects_runtime_owned_reviewed_function() -> None:
    rows = _trigger_inventory_rows()
    reviewed = list(rows[0])
    reviewed[14] = "app-role"
    reviewed[15] = "app-role"
    reviewed[17] = False
    reviewed[18] = False
    reviewed[19] = True
    rows[0] = tuple(reviewed)
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match=r"trigger inventory mismatch.*True"):
        lakebase_migrate._postflight_trigger_inventory(
            cursor,
            "app-role",
            principal_label="app role",
        )


def test_routine_postflight_rejects_public_security_definer_execution() -> None:
    cursor = _Cursor(
        fetchall_results=[
            [("public", "exfiltrate", "", "f", True, "attacker-owner", False)],
        ]
    )

    with pytest.raises(RuntimeError, match="exfiltrate.*True"):
        lakebase_migrate._postflight_effective_routine_privileges(
            cursor,
            "app-role",
            principal_label="app role",
            expected=lakebase_migrate._APP_ROLE_ROUTINE_PRIVILEGES,
        )


def test_routine_postflight_accepts_provider_owned_public_security_invoker() -> None:
    cursor = _Cursor(
        fetchall_results=[
            [
                (
                    "public",
                    "databricks_create_role",
                    "text, text",
                    "f",
                    False,
                    "cloud_admin",
                    False,
                )
            ]
        ]
    )

    lakebase_migrate._postflight_effective_routine_privileges(
        cursor,
        "verifier-role",
        principal_label="AI Gateway verifier",
        expected={},
    )


def test_routine_postflight_rejects_unreviewed_cloud_admin_public_routine() -> None:
    cursor = _Cursor(
        fetchall_results=[
            [("public", "unexpected_provider_helper", "", "f", False, "cloud_admin", False)]
        ]
    )

    with pytest.raises(RuntimeError, match="unexpected_provider_helper"):
        lakebase_migrate._postflight_effective_routine_privileges(
            cursor,
            "app-role",
            principal_label="app role",
            expected={},
        )


def test_oauth_role_function_contract_accepts_exact_provider_primitive() -> None:
    cursor = _Cursor(fetchall_results=[_oauth_role_function_rows()])

    lakebase_migrate._postflight_oauth_role_function_contract(
        cursor,
        principal_label="schema preflight",
    )

    query = cursor.executed[0][0]
    assert "JOIN pg_depend extension_membership" in query
    assert "sha256(convert_to(routine.prosrc, 'UTF8'))" in query
    assert "routine.proacl" in query


@pytest.mark.parametrize(
    "function_acl",
    (
        ["cloud_admin=X/cloud_admin"],
        ["cloud_admin=X/cloud_admin", "=X/cloud_admin"],
    ),
)
def test_oauth_role_function_contract_accepts_reviewed_upgrade_acl_states(
    function_acl: list[str],
) -> None:
    cursor = _Cursor(
        fetchall_results=[_oauth_role_function_rows(function_acl=function_acl)]
    )

    lakebase_migrate._postflight_oauth_role_function_contract(
        cursor,
        principal_label="schema preflight",
    )


def test_oauth_role_function_contract_rejects_unreviewed_acl_grantee() -> None:
    cursor = _Cursor(
        fetchall_results=[
            _oauth_role_function_rows(
                function_acl=["attacker=X/cloud_admin", "cloud_admin=X/cloud_admin"]
            )
        ]
    )

    with pytest.raises(RuntimeError, match="OAuth role-function contract drifted"):
        lakebase_migrate._postflight_oauth_role_function_contract(
            cursor,
            principal_label="schema preflight",
        )


def test_oauth_role_function_contract_rejects_extension_or_body_drift() -> None:
    row = list(_oauth_role_function_rows()[0])
    row[19] = "0" * 64
    cursor = _Cursor(fetchall_results=[[tuple(row)]])

    with pytest.raises(RuntimeError, match="OAuth role-function contract drifted"):
        lakebase_migrate._postflight_oauth_role_function_contract(
            cursor,
            principal_label="ACL preflight",
        )


@pytest.mark.parametrize(
    ("security_definer", "direct_grant"),
    ((True, False), (False, True)),
)
def test_routine_postflight_rejects_privileged_or_direct_provider_execution(
    security_definer: bool,
    direct_grant: bool,
) -> None:
    cursor = _Cursor(
        fetchall_results=[
            [
                (
                    "public",
                    "databricks_create_role",
                    "text, text",
                    "f",
                    security_definer,
                    "cloud_admin",
                    direct_grant,
                )
            ]
        ]
    )

    with pytest.raises(RuntimeError, match="routine EXECUTE postflight"):
        lakebase_migrate._postflight_effective_routine_privileges(
            cursor,
            "app-role",
            principal_label="app role",
            expected={},
        )


def test_verifier_routine_postflight_rejects_any_execute() -> None:
    cursor = _Cursor(
        fetchall_results=[
            [
                (
                    "mip_app",
                    "campaign_holdout_is_reviewed",
                    "jsonb",
                    "f",
                    False,
                    "migration-owner",
                    True,
                )
            ],
        ]
    )

    with pytest.raises(RuntimeError, match="verifier.*routine EXECUTE"):
        lakebase_migrate._postflight_effective_routine_privileges(
            cursor,
            "verifier-role",
            principal_label="AI Gateway verifier",
            expected={},
        )


def test_app_routine_postflight_rejects_runtime_owned_validator() -> None:
    rows = _app_routine_privilege_rows()
    rows[0] = (*rows[0][:-2], "app-role", rows[0][-1])
    cursor = _Cursor(fetchall_results=[rows])

    with pytest.raises(RuntimeError, match="routine EXECUTE postflight.*True"):
        lakebase_migrate._postflight_effective_routine_privileges(
            cursor,
            "app-role",
            principal_label="app role",
            expected=lakebase_migrate._APP_ROLE_ROUTINE_PRIVILEGES,
        )


def test_verifier_postflight_rejects_inherited_create_on_external_schema() -> None:
    role = "verifier-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            [
                ("analytics", "CREATE"),
                ("mip_app", "USAGE"),
                ("public", "USAGE"),
            ],
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="AI Gateway verifier.*analytics.*CREATE"):
        lakebase_migrate._postflight_ai_gateway_verifier_grants(cursor, role)


def test_verifier_postflight_rejects_inherited_external_default_select() -> None:
    role = "verifier-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            [("mip_app", "USAGE"), ("public", "USAGE")],
            _table_rows(),
            _verifier_table_privilege_rows(),
            [],
            [],
            _sequence_rows(),
            [],
            [("external-owner", "analytics", "r", "reporting-role", "SELECT")],
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="effective future.*analytics.*SELECT"):
        lakebase_migrate._postflight_ai_gateway_verifier_grants(cursor, role)


def test_postflight_rejects_effective_create_on_public_schema() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(),
            [],
            _sequence_rows(),
            _sequence_privilege_rows(),
            [],
            [("mip_app", "USAGE"), ("public", "CREATE"), ("public", "USAGE")],
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="other schemas.*public.*CREATE"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_postflight_rejects_effective_select_on_table_in_other_schema() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(),
            [],
            _sequence_rows(),
            _sequence_privilege_rows(),
            [],
            [("mip_app", "USAGE"), ("public", "USAGE")],
            [("analytics", "borrower_export", "SELECT")],
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="other tables.*analytics.*SELECT"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_postflight_rejects_effective_select_on_public_view() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(),
            [],
            _sequence_rows(),
            _sequence_privilege_rows(),
            [],
            [("mip_app", "USAGE"), ("public", "USAGE")],
            [("public", "borrower_export_view", "SELECT")],
        ],
        fetchone_results=[(True, False, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="other tables.*public.*borrower_export_view.*SELECT"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_acl_catalog_queries_cover_all_table_like_relation_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "app-role"
    cursor = _successful_cursor(role)
    connection = _Connection(cursor)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)
    lakebase_migrate._apply_app_role_grants(
        {},
        role_wait_timeout_s=0,
        role_wait_interval_s=1,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
    )

    relation_queries = [
        statement for statement, _params in cursor.executed if "c.relkind IN" in statement
    ]
    assert relation_queries
    assert all("('r', 'p', 'v', 'm', 'f')" in statement for statement in relation_queries)


def test_lakebase_grant_docs_match_strict_automated_contract() -> None:
    grants_doc = Path("docs/security/GRANTS.md").read_text(encoding="utf-8")
    lakebase_section = grants_doc.split("## 6. Schema `mip_app` (Lakebase Postgres — required)", 1)[
        1
    ].split("## 7. Cotality Delta Share", 1)[0]

    assert "service_principal_client_id" in lakebase_section
    assert ".venv/bin/python -m jobs.lakebase_migrate" in lakebase_section
    assert "only supported path for an externally" in lakebase_section
    assert "Do not copy a static GRANT list" in lakebase_section
    assert "recursive" in lakebase_section
    assert "parent membership" in lakebase_section
    assert "ADMIN-option paths" in lakebase_section
    assert "table-column ACL" in lakebase_section
    assert "TEMPORARY=false" in lakebase_section
    assert "overloaded routine" in lakebase_section
    assert "PUBLIC `EXECUTE`" in lakebase_section
    assert "every non-internal trigger" in lakebase_section
    assert "Revoking function `EXECUTE` is not treated as sufficient" in lakebase_section
    assert "ACCESS EXCLUSIVE" in lakebase_section
    assert "pg_event_trigger" in lakebase_section
    assert "complete\nthree-row Databricks-managed event-trigger contract" in lakebase_section
    assert "raw UTF-8 `prosrc`\nSHA-256" in lakebase_section
    assert "vanilla-PostgreSQL integration fixture" in lakebase_section
    assert "provider roles' recursive memberships" in lakebase_section
    assert "The separate ACL transaction repeats" in lakebase_section
    assert "resources.apps.mip_app.resources" in lakebase_section
    assert "rollback-capable ACL transaction" in lakebase_section
    assert "](../../databricks.yml) lines" not in grants_doc
    assert "GRANT USAGE ON ALL SEQUENCES" not in lakebase_section
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" not in lakebase_section
    assert 'REVOKE CREATE ON DATABASE mip_app_state FROM "service-principal-client-id"' not in (
        lakebase_section
    )


def test_ai_gateway_proof_ledger_is_runtime_select_only() -> None:
    assert lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES["ai_gateway_proof_ledger"] == ("SELECT",)


def test_privilege_matrix_inventory_matches_schema_tables_exactly() -> None:
    schema = Path("lakebase/schema.sql").read_text(encoding="utf-8")
    schema_tables = set(
        re.findall(r"^CREATE TABLE IF NOT EXISTS mip_app\.([a-z_]+)", schema, re.MULTILINE)
    )

    assert schema_tables == set(lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES)
