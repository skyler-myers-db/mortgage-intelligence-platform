"""Tests for fail-closed Lakebase OAuth role convergence."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from databricks.sdk.errors import NotFound

from jobs.lakebase_migration_contracts import _MANAGED_EVENT_TRIGGER_CONTRACT
from tools.databricks import converge_lakebase_oauth_role as role_convergence
from tools.databricks import converge_lakebase_oauth_role_recovery as convergence_recovery
from tools.databricks import lakebase_oauth_role_bootstrap_lock as bootstrap_lock
from tools.databricks import lakebase_oauth_role_bootstrap_target as bootstrap_target
from tools.databricks import lakebase_oauth_role_recovery as role_recovery
from tools.databricks import lakebase_oauth_role_recovery_absent as absent_recovery


@pytest.fixture(autouse=True)
def _stub_ephemeral_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convergence_recovery.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bootstrap_lock.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bootstrap_target.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(role_recovery.time, "sleep", lambda _seconds: None)

    def bootstrap(_client: Any, cursor: Any, **_kwargs: Any) -> None:
        state = cursor.state
        state.executed.append("EPHEMERAL_BOOTSTRAP")
        if state.create_profile != role_convergence.SAFE_OAUTH_PROFILE:
            raise RuntimeError("databricks_create_role returned unsafe role attributes")
        if state.create_relationships:
            raise RuntimeError("unreviewed bootstrap membership")
        state.profile = role_convergence.SAFE_OAUTH_PROFILE
        state.relationships = []

    monkeypatch.setattr(role_convergence, "_create_login_only_role", bootstrap)
    monkeypatch.setattr(
        role_convergence,
        "_recover_stale_bootstrap_identities",
        lambda *_args, **_kwargs: None,
    )


class _State:
    def __init__(
        self,
        profile: tuple[bool, ...] | None,
        *,
        relationships: list[tuple[str, str]] | None = None,
        dependencies: list[tuple[Any, ...]] | None = None,
        identity_type: str = "SERVICE_PRINCIPAL",
        create_profile: tuple[bool, ...] = role_convergence.SAFE_OAUTH_PROFILE,
        create_relationships: list[tuple[Any, ...]] | None = None,
        app_state: str = "STOPPED",
    ) -> None:
        self.profile = profile
        self.relationships = relationships or []
        self.dependencies = dependencies or []
        self.identity_type = identity_type
        self.create_profile = create_profile
        self.create_relationships = create_relationships
        self.app_state = app_state
        self.function_exists = True
        self.deleted = 0
        self.executed: list[str] = []
        self.advisory_lock_held = False
        self.advisory_backend_pid = 6001
        self.sessions: list[int] = []
        self.target_database_present = True
        self.delete_failures = 0
        self.settings: tuple[Any, ...] = (-1, None, "********", None)
        self.database_settings: list[tuple[Any, ...]] = []


class _Cursor:
    def __init__(self, state: _State, *, database_name: str) -> None:
        self.state = state
        self.database_name = database_name
        self._one: tuple[Any, ...] | None = None
        self._all: list[tuple[Any, ...]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: Any, _params: object = None) -> None:
        rendered_query = query.as_string() if hasattr(query, "as_string") else str(query)
        rendered = " ".join(rendered_query.split())
        self.state.executed.append(rendered)
        self._one = None
        self._all = []
        if rendered.startswith("REVOKE "):
            self.state.dependencies = []
            return
        if "FROM pg_event_trigger event_trigger" in rendered:
            self._all = [
                (
                    name,
                    contract.event,
                    contract.enabled,
                    contract.tags,
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
                    None,
                    contract.function_source_sha256,
                    contract.function_source_bytes,
                )
                for name, contract in sorted(_MANAGED_EVENT_TRIGGER_CONTRACT.items())
            ]
        elif rendered == "SELECT current_user, session_user":
            self._one = ("deployer@example.com", "deployer@example.com")
        elif rendered == "SELECT current_database()":
            self._one = (self.database_name,)
        elif rendered == "SELECT pg_backend_pid()":
            self._one = (self.state.advisory_backend_pid,)
        elif rendered == "SELECT pg_try_advisory_lock(%s)":
            self.state.advisory_lock_held = True
            self._one = (True,)
        elif rendered == "SELECT pg_advisory_unlock(%s)":
            released = self.state.advisory_lock_held
            self.state.advisory_lock_held = False
            self._one = (released,)
        elif "FROM pg_locks" in rendered:
            self._one = (1 if self.state.advisory_lock_held else 0,)
        elif "FROM pg_roles" in rendered and "rolreplication" in rendered:
            self._one = self.state.profile
        elif rendered.startswith("SELECT oid, rolname FROM pg_roles"):
            self._all = [(5102, "service-principal-id")] if self.state.profile is not None else []
        elif rendered == "SELECT oid FROM pg_roles WHERE rolname = %s":
            self._all = [(5102,)] if self.state.profile is not None else []
        elif "SELECT rolconnlimit, rolvaliduntil, rolpassword, rolconfig" in rendered:
            self._all = [self.state.settings] if self.state.profile else []
        elif "FROM pg_db_role_setting setting" in rendered:
            self._all = list(self.state.database_settings)
        elif "FROM pg_stat_activity" in rendered:
            self._all = [(pid, 5102, "service-principal-id") for pid in self.state.sessions]
        elif rendered == "SELECT pg_terminate_backend(%s)":
            pid = int(_params[0])
            if pid in self.state.sessions:
                self.state.sessions.remove(pid)
            self._one = (True,)
        elif rendered.startswith("SELECT 1 FROM pg_auth_members"):
            self._one = (1,) if self.state.relationships else None
        elif "FROM pg_auth_members" in rendered:
            self._all = list(self.state.relationships)
        elif "FROM pg_shdepend" in rendered:
            self._all = list(self.state.dependencies)
        elif rendered == "SELECT oid FROM pg_database WHERE datname = current_database()":
            self._one = (42,)
        elif rendered == "SELECT 1 FROM pg_database WHERE datname = %s":
            self._one = (1,) if self.state.target_database_present else None
        elif "FROM pg_database WHERE datname" in rendered:
            self._one = (42,)
        elif "FROM pg_roles role" in rendered and "pg_shseclabel" in rendered:
            self._all = [("databricks_auth", "id=service-principal-scim-id,type=service_principal")]
        elif "to_regprocedure" in rendered:
            self._one = (self.state.function_exists,)
        elif rendered.startswith("CREATE EXTENSION"):
            self.state.function_exists = True
        elif rendered.startswith("ALTER ROLE") and rendered.endswith("NOLOGIN"):
            assert self.state.profile is not None
            self.state.profile = (*self.state.profile[:-1], False)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._all


class _Transaction:
    def __init__(self, state: _State) -> None:
        self.state = state

    def __enter__(self) -> _Transaction:
        self.state.executed.append("BEGIN_TRANSACTION")
        return self

    def __exit__(self, *args: object) -> None:
        self.state.executed.append(
            "ROLLBACK_TRANSACTION" if args and args[0] is not None else "COMMIT_TRANSACTION"
        )
        return None


class _Connection:
    def __init__(self, state: _State, *, database_name: str = "mip_app_state") -> None:
        self.state = state
        self.database_name = database_name

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self.state, database_name=self.database_name)

    def transaction(self) -> _Transaction:
        return _Transaction(self.state)


def _client(state: _State) -> MagicMock:
    client = MagicMock()
    client.database.get_database_instance.return_value = SimpleNamespace(
        read_write_dns="reviewed.database.example"
    )
    client.database.generate_database_credential.return_value = SimpleNamespace(
        token="not-a-real-token"
    )
    client.current_user.me.return_value = SimpleNamespace(
        application_id=None,
        user_name="deployer@example.com",
    )
    client.apps.get.return_value = SimpleNamespace(
        id="app-id",
        service_principal_client_id="service-principal-id",
        service_principal_id="service-principal-scim-id",
        compute_status=SimpleNamespace(state=state.app_state),
    )
    client.apps.list.return_value = iter(
        [
            SimpleNamespace(
                name="mip-app",
                id="app-id",
                service_principal_client_id="service-principal-id",
                service_principal_id="service-principal-scim-id",
                compute_status=SimpleNamespace(state=state.app_state),
            )
        ]
    )

    def stop_app(_name: str) -> None:
        state.app_state = "STOPPED"
        client.apps.get.return_value.compute_status.state = "STOPPED"

    client.apps.stop.side_effect = stop_app
    client.service_principals.list.return_value = iter(
        [
            SimpleNamespace(
                id="service-principal-scim-id",
                application_id="service-principal-id",
            )
        ]
    )

    def get_role(_instance: str, _application_id: str) -> SimpleNamespace:
        if state.profile is None:
            raise RuntimeError("role absent")
        return SimpleNamespace(identity_type=state.identity_type)

    def delete_role(_instance: str, _application_id: str) -> None:
        state.executed.append("CONTROL_PLANE_DELETE")
        if state.delete_failures:
            state.delete_failures -= 1
            raise RuntimeError("injected control-plane delete failure")
        state.deleted += 1
        state.profile = None
        state.relationships = []
        state.dependencies = []
        state.sessions = []

    def list_roles(_instance: str) -> Any:
        if state.profile is None:
            return iter([])
        return iter(
            [
                SimpleNamespace(
                    name="service-principal-id",
                    identity_type=SimpleNamespace(value=state.identity_type),
                )
            ]
        )

    client.database.get_database_instance_role.side_effect = get_role
    client.database.list_database_instance_roles.side_effect = list_roles
    client.database.delete_database_instance_role.side_effect = delete_role
    return client


def _converge(
    state: _State,
    *,
    repair: bool = False,
) -> role_convergence.RoleConvergenceResult:
    return role_convergence.converge_role(
        _client(state),
        account_client=MagicMock(),
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id="service-principal-id",
        role_contract="app",
        repair_legacy_replication=repair,
        app_name="mip-app",
        connect=lambda **_kwargs: _Connection(state),
        allow_absent_provider_schema=True,
    )


def test_exact_login_only_role_is_idempotent() -> None:
    state = _State(
        role_convergence.SAFE_OAUTH_PROFILE,
        dependencies=[(42, "pg_class", 0, "a", "mip_app", "campaigns", "", "r")],
    )

    result = _converge(state)

    assert result == role_convergence.RoleConvergenceResult(False, False)
    assert state.deleted == 0


@pytest.mark.parametrize(
    "settings",
    [
        (1, None, "********", None),
        (-1, "2027-01-01", "********", None),
        (-1, None, "unexpected", None),
        (-1, None, "********", ["search_path=public"]),
    ],
)
def test_existing_safe_role_setting_drift_is_never_accepted(
    settings: tuple[Any, ...],
) -> None:
    state = _State(role_convergence.SAFE_OAUTH_PROFILE)
    state.settings = settings

    with pytest.raises(RuntimeError, match="setting contract drifted"):
        _converge(state)

    assert state.deleted == 0


def test_existing_safe_role_database_scoped_setting_is_never_accepted() -> None:
    state = _State(role_convergence.SAFE_OAUTH_PROFILE)
    state.database_settings = [(42, 5102, ["statement_timeout=0"])]

    with pytest.raises(RuntimeError, match="database-scoped role settings"):
        _converge(state)

    assert state.deleted == 0


def test_safe_role_still_runs_public_schema_quarantine_before_early_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _State(role_convergence.SAFE_OAUTH_PROFILE)
    calls: list[tuple[tuple[str, ...], str]] = []

    def close_boundary(_cursor: Any, roles: tuple[str, ...], **kwargs: Any) -> None:
        calls.append((roles, str(kwargs["principal_label"])))

    monkeypatch.setattr(
        role_convergence,
        "_close_public_schema_boundary",
        close_boundary,
    )

    result = _converge(state)

    assert result == role_convergence.RoleConvergenceResult(False, False)
    assert calls == [(("service-principal-id",), "OAuth role convergence quarantine")]
    event_preflight = next(
        index
        for index, statement in enumerate(state.executed)
        if "FROM pg_event_trigger event_trigger" in statement
    )
    assert event_preflight < state.executed.index("BEGIN_TRANSACTION")
    assert state.executed.index("BEGIN_TRANSACTION") < state.executed.index("COMMIT_TRANSACTION")


def test_exact_reviewed_routine_acl_is_idempotent() -> None:
    state = _State(
        role_convergence.SAFE_OAUTH_PROFILE,
        dependencies=[
            (
                42,
                "pg_proc",
                0,
                "a",
                "mip_app",
                "campaign_holdout_is_reviewed",
                "jsonb",
                "",
            )
        ],
    )

    result = _converge(state)

    assert result == role_convergence.RoleConvergenceResult(False, False)


def test_named_routine_argument_rendering_is_not_accepted_as_contract_identity() -> None:
    state = _State(
        role_convergence.LEGACY_API_OAUTH_PROFILE,
        dependencies=[
            (
                42,
                "pg_proc",
                0,
                "a",
                "mip_app",
                "campaign_holdout_is_reviewed",
                "document jsonb",
                "",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="unreviewed ACL or shared dependencies"):
        _converge(state, repair=True)


def test_absent_role_uses_documented_sql_creation_path() -> None:
    state = _State(None)

    result = _converge(state)

    assert result == role_convergence.RoleConvergenceResult(True, False)
    assert state.profile == role_convergence.SAFE_OAUTH_PROFILE
    assert state.deleted == 0
    assert "EPHEMERAL_BOOTSTRAP" in state.executed


@pytest.mark.parametrize("app_state", ["RUNNING", "STARTING", "absent"])
def test_app_role_creation_requires_stopped_app(app_state: str) -> None:
    state = _State(None, app_state=app_state)

    with pytest.raises(RuntimeError, match="must be STOPPED"):
        _converge(state)

    assert state.profile is None


def test_app_role_creation_requires_exact_app_name() -> None:
    state = _State(None)

    with pytest.raises(RuntimeError, match="exact Databricks App name"):
        role_convergence.converge_role(
            _client(state),
            account_client=MagicMock(),
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id="service-principal-id",
            role_contract="app",
            repair_legacy_replication=False,
            connect=lambda **_kwargs: _Connection(state),
            allow_absent_provider_schema=True,
        )

    assert state.profile is None


def test_exact_legacy_api_profile_is_replaced_only_at_repair_boundary() -> None:
    state = _State(
        role_convergence.LEGACY_API_OAUTH_PROFILE,
        dependencies=[(42, "pg_class", 0, "a", "mip_app", "campaigns", "", "r")],
    )

    result = _converge(state, repair=True)

    assert result == role_convergence.RoleConvergenceResult(False, True)
    assert state.deleted == 1
    assert state.profile == role_convergence.SAFE_OAUTH_PROFILE


def test_reviewed_acl_revocation_precedes_control_plane_role_deletion() -> None:
    state = _State(
        role_convergence.LEGACY_API_OAUTH_PROFILE,
        dependencies=[(42, "pg_class", 0, "a", "mip_app", "campaigns", "", "r")],
    )

    _converge(state, repair=True)

    first_revoke = next(
        index for index, statement in enumerate(state.executed) if statement.startswith("REVOKE ")
    )
    assert first_revoke < state.executed.index("CONTROL_PLANE_DELETE")


def test_app_role_replacement_requires_stopped_app() -> None:
    state = _State(role_convergence.LEGACY_API_OAUTH_PROFILE, app_state="RUNNING")

    with pytest.raises(RuntimeError, match="must be STOPPED"):
        _converge(state, repair=True)

    assert state.deleted == 0


def test_stopped_app_precondition_rejects_wrong_target_identity_without_stop() -> None:
    state = _State(role_convergence.LEGACY_API_OAUTH_PROFILE, app_state="STOPPED")
    client = _client(state)
    client.apps.get.return_value.service_principal_client_id = "other-client"

    with pytest.raises(RuntimeError, match="does not match"):
        role_convergence.converge_role(
            client,
            account_client=MagicMock(),
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id="service-principal-id",
            role_contract="app",
            repair_legacy_replication=True,
            app_name="mip-app",
            connect=lambda **_kwargs: _Connection(state),
            allow_absent_provider_schema=True,
        )

    assert state.deleted == 0


def test_running_app_is_stopped_and_identity_pinned_before_legacy_repair() -> None:
    state = _State(role_convergence.LEGACY_API_OAUTH_PROFILE, app_state="RUNNING")
    client = _client(state)

    result = role_convergence.converge_role(
        client,
        account_client=MagicMock(),
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id="service-principal-id",
        role_contract="app",
        repair_legacy_replication=True,
        app_name="mip-app",
        stop_app_for_mutation=True,
        connect=lambda **_kwargs: _Connection(state),
        allow_absent_provider_schema=True,
    )

    assert result == role_convergence.RoleConvergenceResult(False, True)
    client.apps.stop.assert_called_once_with("mip-app")
    assert state.deleted == 1


def test_stop_for_mutation_rejects_app_identity_drift() -> None:
    state = _State(role_convergence.LEGACY_API_OAUTH_PROFILE, app_state="RUNNING")
    client = _client(state)
    client.apps.get.return_value.service_principal_client_id = "other-client"

    with pytest.raises(RuntimeError, match="does not match"):
        role_convergence.converge_role(
            client,
            account_client=MagicMock(),
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id="service-principal-id",
            role_contract="app",
            repair_legacy_replication=True,
            app_name="mip-app",
            stop_app_for_mutation=True,
            connect=lambda **_kwargs: _Connection(state),
            allow_absent_provider_schema=True,
        )

    assert state.deleted == 0


def test_stop_for_mutation_rejects_app_scim_identity_drift() -> None:
    state = _State(role_convergence.LEGACY_API_OAUTH_PROFILE, app_state="RUNNING")
    client = _client(state)
    client.apps.get.return_value.service_principal_id = "other-scim-id"

    with pytest.raises(RuntimeError, match="does not match"):
        role_convergence.converge_role(
            client,
            account_client=MagicMock(),
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id="service-principal-id",
            role_contract="app",
            repair_legacy_replication=True,
            app_name="mip-app",
            stop_app_for_mutation=True,
            connect=lambda **_kwargs: _Connection(state),
            allow_absent_provider_schema=True,
        )

    assert state.deleted == 0


def test_legacy_replication_profile_fails_without_explicit_repair() -> None:
    state = _State(role_convergence.LEGACY_API_OAUTH_PROFILE)

    with pytest.raises(RuntimeError, match="--repair-legacy-replication"):
        _converge(state)

    assert state.deleted == 0


@pytest.mark.parametrize("dependency_kind", ["o", "r", "p", "x"])
def test_non_acl_dependency_rejects_replacement(dependency_kind: str) -> None:
    state = _State(
        role_convergence.LEGACY_API_OAUTH_PROFILE,
        dependencies=[(42, "pg_class", 0, dependency_kind, "mip_app", "campaigns", "", "r")],
    )

    with pytest.raises(RuntimeError, match="unreviewed ACL or shared dependencies"):
        _converge(state, repair=True)

    assert state.deleted == 0


def test_cross_database_acl_rejects_replacement() -> None:
    state = _State(
        role_convergence.LEGACY_API_OAUTH_PROFILE,
        dependencies=[(99, "pg_class", 0, "a", "", "", "", "")],
    )

    with pytest.raises(RuntimeError, match="unreviewed ACL or shared dependencies"):
        _converge(state, repair=True)

    assert state.deleted == 0


def test_unreviewed_target_database_object_rejects_replacement() -> None:
    state = _State(
        role_convergence.LEGACY_API_OAUTH_PROFILE,
        dependencies=[(42, "pg_class", 0, "a", "public", "provider_view", "", "v")],
    )

    with pytest.raises(RuntimeError, match="unreviewed ACL or shared dependencies"):
        _converge(state, repair=True)

    assert state.deleted == 0


def test_role_relationship_rejects_replacement() -> None:
    state = _State(
        role_convergence.LEGACY_API_OAUTH_PROFILE,
        relationships=[("parent", "service-principal-id")],
    )

    with pytest.raises(RuntimeError, match="role relationships"):
        _converge(state, repair=True)

    assert state.deleted == 0


def test_unreviewed_attribute_drift_rejects_without_deletion() -> None:
    state = _State((False, False, True, False, False, True, True))

    with pytest.raises(RuntimeError, match="unreviewed attributes"):
        _converge(state, repair=True)

    assert state.deleted == 0


def test_non_service_principal_metadata_rejects_safe_role() -> None:
    state = _State(role_convergence.SAFE_OAUTH_PROFILE, identity_type="USER")

    with pytest.raises(RuntimeError, match="SERVICE_PRINCIPAL"):
        _converge(state)


def test_sql_creation_must_produce_exact_safe_profile() -> None:
    state = _State(None, create_profile=role_convergence.LEGACY_API_OAUTH_PROFILE)

    with pytest.raises(RuntimeError, match="unsafe role attributes"):
        _converge(state)


def test_bootstrap_rejects_unreviewed_creator_membership_shape() -> None:
    state = _State(
        None,
        create_relationships=[("service-principal-id", "other-identity", True, False, False)],
    )

    with pytest.raises(RuntimeError, match="unreviewed bootstrap membership"):
        _converge(state)

    assert state.profile is None


def test_safe_profile_with_exact_sticky_creator_membership_is_replaced() -> None:
    state = _State(
        role_convergence.SAFE_OAUTH_PROFILE,
        relationships=[
            (
                "service-principal-id",
                "deployer@example.com",
                True,
                False,
                False,
                "cloud_admin",
            )
        ],
    )

    result = _converge(state, repair=True)

    assert result == role_convergence.RoleConvergenceResult(False, True)
    assert state.deleted == 1
    assert state.relationships == []


def test_interrupted_bootstrap_recovery_precedes_safe_role_relationship_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _State(
        role_convergence.SAFE_OAUTH_PROFILE,
        relationships=[
            (
                "service-principal-id",
                "stale-bootstrap-application-id",
                True,
                False,
                False,
                "cloud_admin",
            )
        ],
    )
    called = False

    def recover(_client: Any, cursor: Any, **_kwargs: Any) -> None:
        nonlocal called
        called = True
        cursor.state.relationships = []

    monkeypatch.setattr(role_convergence, "_recover_stale_bootstrap_identities", recover)

    result = _converge(state)

    assert called is True
    assert result == role_convergence.RoleConvergenceResult(False, False)


def test_recovery_only_clean_workspace_never_opens_a_database_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(role_recovery.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(absent_recovery, "_ABSENCE_STABILITY_SECONDS", 0.0)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "test-signing-key")
    client = MagicMock()
    client.service_principals.list.side_effect = lambda **_kwargs: iter([])
    client.database.list_database_instances.side_effect = lambda: iter([])
    client.database.get_database_instance.side_effect = NotFound("absent")

    role_convergence.recover_role_bootstrap(
        client,
        account_client=MagicMock(),
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id="service-principal-id",
        connect=lambda **_kwargs: pytest.fail("clean workspace attempted a DB connection"),
    )

    assert client.database.get_database_instance.call_count == 2
    client.database.list_database_instances.assert_not_called()


def test_recovery_only_database_absent_uses_canonical_admin_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingDatabaseError(RuntimeError):
        sqlstate = "3D000"

    monkeypatch.setattr(role_recovery.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(absent_recovery, "_ABSENCE_STABILITY_SECONDS", 0.0)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "test-signing-key")
    client = MagicMock()
    client.service_principals.list.side_effect = lambda **_kwargs: iter([])
    client.database.list_database_instances.side_effect = lambda: iter(
        [SimpleNamespace(name="mip-app-state")]
    )
    client.database.get_database_instance.return_value = SimpleNamespace(
        name="mip-app-state",
        read_write_dns="instance.database.cloud.databricks.com",
    )
    client.database.generate_database_credential.return_value = SimpleNamespace(token="token")
    client.current_user.me.return_value = SimpleNamespace(
        application_id=None,
        user_name="deployer@example.com",
    )
    connection_attempts = 0
    state = _State(None)
    state.target_database_present = False

    def connect(**kwargs: Any) -> Any:
        nonlocal connection_attempts
        connection_attempts += 1
        if kwargs["dbname"] == "databricks_postgres":
            return _Connection(state, database_name="databricks_postgres")
        raise MissingDatabaseError("database does not exist")

    role_convergence.recover_role_bootstrap(
        client,
        account_client=MagicMock(),
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id="service-principal-id",
        connect=connect,
    )

    assert connection_attempts == 4
    assert client.database.get_database_instance.call_count == 3
    assert all(
        call.args == ("mip-app-state",)
        for call in client.database.get_database_instance.call_args_list
    )
    client.database.list_database_instances.assert_not_called()
