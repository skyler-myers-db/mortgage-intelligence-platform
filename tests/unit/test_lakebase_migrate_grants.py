"""Deployment contract for strict Lakebase runtime-role grants."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

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


def _successful_cursor(role: str, verifier_role: str | None = None) -> _Cursor:
    fetchall_results: list[list[tuple[Any, ...]]] = [[(role,)]]
    if verifier_role is not None:
        fetchall_results.append([(verifier_role,)])
    fetchall_results.extend(
        [
            [(role,)],
            _table_rows(),
            _table_privilege_rows(),
            _sequence_rows(),
            _sequence_privilege_rows(),
            [],
        ]
    )
    fetchone_results = [
        ("mip_app_state",),
        (True, False, True, False),
    ]
    if verifier_role is not None:
        fetchall_results.extend(
            [
                [(verifier_role,)],
                _table_rows(),
                _verifier_table_privilege_rows(),
                [],
                _sequence_rows(),
                [],
                [],
            ]
        )
        fetchone_results.append((True, False, True, False))
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


def test_schema_and_seed_run_in_one_rollback_capable_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _FailingCursor(fetchall_results=[], fail_when="SEED")
    connection = _Connection(cursor)
    connect_kwargs: dict[str, Any] = {}

    import psycopg

    def _connect(**kwargs: Any) -> _Connection:
        connect_kwargs.update(kwargs)
        return connection

    monkeypatch.setattr(psycopg, "connect", _connect)

    with pytest.raises(RuntimeError, match="injected database failure"):
        lakebase_migrate._run_transaction(("SCHEMA", "SEED"), {})

    assert connect_kwargs["autocommit"] is False
    assert [statement for statement, _params in cursor.executed] == ["SCHEMA", "SEED"]
    assert connection.commit_count == 0
    assert connection.rollback_count == 1
    assert connection.closed is True


def test_integrity_probe_uses_schema_transaction_connection_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _Cursor(fetchall_results=[])
    connection = _Connection(cursor)
    probe_calls: list[tuple[dict[str, str], object]] = []

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(
        lakebase_migrate,
        "_run_outreach_integrity_probe",
        lambda kwargs, *, connection: probe_calls.append((kwargs, connection)),
    )

    lakebase_migrate._run_transaction(
        ("SCHEMA", "SEED", "POST-SEED"),
        {"host": "test"},
        verify_outreach_integrity=True,
    )

    assert probe_calls == [({"host": "test"}, connection)]
    assert connection.commit_count == 1
    assert connection.rollback_count == 0
    assert connection.closed is True


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

    def _run_transaction(
        sql_texts: tuple[str, ...],
        _kwargs: dict[str, str],
        *,
        verify_outreach_integrity: bool,
    ) -> None:
        assert len(sql_texts) == 3
        assert "CREATE TABLE IF NOT EXISTS mip_app.schema_migrations" in sql_texts[0]
        assert "INSERT INTO mip_app.campaign_message_variants" in sql_texts[1]
        assert "VALIDATE CONSTRAINT approvals_campaign_variant_channel_fkey" in sql_texts[2]
        assert verify_outreach_integrity is True
        calls.append("schema-seed-post-seed-integrity")

    monkeypatch.setattr(lakebase_migrate, "_run_transaction", _run_transaction)
    monkeypatch.setattr(
        lakebase_migrate,
        "_apply_app_role_grants",
        lambda _kwargs: calls.append("grants"),
    )

    lakebase_migrate.main()

    assert calls == ["schema-seed-post-seed-integrity", "grants"]


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

    lakebase_migrate._apply_app_role_grants({}, role_wait_timeout_s=0, role_wait_interval_s=1)

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
    assert all("ANY(" not in statement for statement in statements)
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
    assert (
        'ALTER DEFAULT PRIVILEGES IN SCHEMA "mip_app" '
        'REVOKE ALL PRIVILEGES ON TABLES FROM "sp-client-""quoted"""'
    ) in statements
    assert (
        'ALTER DEFAULT PRIVILEGES IN SCHEMA "mip_app" '
        'REVOKE ALL PRIVILEGES ON SEQUENCES FROM "sp-client-""quoted"""'
    ) in statements


def test_acl_reconciliation_rolls_back_on_mid_grant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = "app-role"
    cursor = _FailingCursor(
        fetchall_results=[[(role,)]],
        fetchone_results=[("mip_app_state",)],
        fail_when="GRANT USAGE ON SCHEMA",
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    with pytest.raises(RuntimeError, match="injected database failure"):
        lakebase_migrate._apply_app_role_grants({}, role_wait_timeout_s=0, role_wait_interval_s=1)

    # The first commit only ends read-only role discovery. All ACL mutations
    # are in the second transaction and are rolled back together.
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
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: role)
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", verifier_role)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)

    lakebase_migrate._apply_app_role_grants(
        {},
        role_wait_timeout_s=0,
        role_wait_interval_s=1,
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
    assert connection.commit_count == 2
    assert connection.rollback_count == 0


def test_app_and_verifier_roles_must_be_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: "same-role")
    monkeypatch.setenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "same-role")

    with pytest.raises(RuntimeError, match="must identify a role distinct"):
        lakebase_migrate._apply_app_role_grants({})


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
        lakebase_migrate._apply_app_role_grants({}, role_wait_timeout_s=0, role_wait_interval_s=1)

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
        fetchone_results=[(True, False, True, False)],
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
        fetchone_results=[(True, True, True, False)],
    )

    with pytest.raises(RuntimeError, match="database_create=True"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_postflight_rejects_effective_delete_privilege() -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(add=("campaigns", "DELETE")),
        ],
        fetchone_results=[(True, False, True, False)],
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
        fetchone_results=[(True, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="table privilege postflight failed"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_verifier_postflight_rejects_access_to_any_other_table() -> None:
    role = "verifier-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _verifier_table_privilege_rows() + [("campaigns", "SELECT")],
        ],
        fetchone_results=[(True, False, True, False)],
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
            _sequence_rows(),
            [],
        ],
        fetchone_results=[(True, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="sequence privilege postflight failed"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


@pytest.mark.parametrize("object_type", ["r", "S"])
def test_postflight_rejects_future_object_default_privilege(object_type: str) -> None:
    role = "app-role"
    cursor = _Cursor(
        fetchall_results=[
            [(role,)],
            _table_rows(),
            _table_privilege_rows(),
            _sequence_rows(),
            _sequence_privilege_rows(),
            [(object_type, "SELECT" if object_type == "r" else "USAGE")],
        ],
        fetchone_results=[(True, False, True, False)],
    )

    with pytest.raises(RuntimeError, match="future table/sequence default"):
        lakebase_migrate._postflight_app_role_grants(cursor, role)


def test_lakebase_grant_docs_match_strict_automated_contract() -> None:
    grants_doc = Path("docs/security/GRANTS.md").read_text(encoding="utf-8")
    lakebase_section = grants_doc.split("## 6. Schema `mip_app` (Lakebase Postgres — required)", 1)[
        1
    ].split("## 7. Cotality Delta Share", 1)[0]

    assert "service_principal_client_id" in lakebase_section
    assert (
        'REVOKE CREATE ON DATABASE mip_app_state FROM "service-principal-client-id"'
        in lakebase_section
    )
    assert "GRANT USAGE ON ALL SEQUENCES" not in lakebase_section
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" not in lakebase_section
    assert "GRANT USAGE ON SEQUENCE mip_app.action_audit_audit_sequence_seq" in (lakebase_section)
    assert "REVOKE ALL PRIVILEGES ON SEQUENCES" in lakebase_section
    assert "REVOKE ALL PRIVILEGES ON TABLES" in lakebase_section
    for table, privileges in lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES.items():
        assert f"REVOKE ALL PRIVILEGES ON TABLE mip_app.{table}" in lakebase_section
        if table == "ai_gateway_proof_ledger":
            # The deploy/runtime matrix is authoritative for this scoped
            # backend change; the governance-doc owner updates its SQL mirror.
            continue
        if privileges:
            privilege_list = ", ".join(privileges)
            assert f"GRANT {privilege_list} ON TABLE mip_app.{table}" in lakebase_section


def test_ai_gateway_proof_ledger_is_runtime_select_only() -> None:
    assert lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES["ai_gateway_proof_ledger"] == ("SELECT",)


def test_privilege_matrix_inventory_matches_schema_tables_exactly() -> None:
    schema = Path("lakebase/schema.sql").read_text(encoding="utf-8")
    schema_tables = set(
        re.findall(r"^CREATE TABLE IF NOT EXISTS mip_app\.([a-z_]+)", schema, re.MULTILINE)
    )

    assert schema_tables == set(lakebase_migrate._APP_ROLE_TABLE_PRIVILEGES)
