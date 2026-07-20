"""Tests for fail-closed Lakebase OAuth role convergence."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from tools.databricks import converge_lakebase_oauth_role as role_convergence
from tools.databricks import lakebase_oauth_role_recovery as role_recovery


@pytest.fixture(autouse=True)
def _stub_ephemeral_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
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


class _Cursor:
    def __init__(self, state: _State) -> None:
        self.state = state
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
        if rendered == "SELECT current_user":
            self._one = ("deployer@example.com",)
        elif "FROM pg_roles" in rendered and "rolreplication" in rendered:
            self._one = self.state.profile
        elif rendered.startswith("SELECT 1 FROM pg_auth_members"):
            self._one = (1,) if self.state.relationships else None
        elif "FROM pg_auth_members" in rendered:
            self._all = list(self.state.relationships)
        elif "FROM pg_shdepend" in rendered:
            self._all = list(self.state.dependencies)
        elif "FROM pg_database WHERE datname" in rendered:
            self._one = (42,)
        elif "FROM pg_roles role" in rendered and "pg_shseclabel" in rendered:
            self._all = [
                ("databricks_auth", "id=service-principal-scim-id,type=service_principal")
            ]
        elif "to_regprocedure" in rendered:
            self._one = (self.state.function_exists,)
        elif rendered.startswith("CREATE EXTENSION"):
            self.state.function_exists = True

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._all


class _Connection:
    def __init__(self, state: _State) -> None:
        self.state = state

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self.state)


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
        state.deleted += 1
        state.profile = None

    client.database.get_database_instance_role.side_effect = get_role
    client.database.delete_database_instance_role.side_effect = delete_role
    return client


def _converge(
    state: _State,
    *,
    repair: bool = False,
) -> role_convergence.RoleConvergenceResult:
    return role_convergence.converge_role(
        _client(state),
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id="service-principal-id",
        role_contract="app",
        repair_legacy_replication=repair,
        app_name="mip-app",
        connect=lambda **_kwargs: _Connection(state),
    )


def test_exact_login_only_role_is_idempotent() -> None:
    state = _State(
        role_convergence.SAFE_OAUTH_PROFILE,
        dependencies=[(42, "pg_class", 0, "a", "mip_app", "campaigns", "", "r")],
    )

    result = _converge(state)

    assert result == role_convergence.RoleConvergenceResult(False, False)
    assert state.deleted == 0


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
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id="service-principal-id",
            role_contract="app",
            repair_legacy_replication=False,
            connect=lambda **_kwargs: _Connection(state),
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
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id="service-principal-id",
            role_contract="app",
            repair_legacy_replication=True,
            app_name="mip-app",
            connect=lambda **_kwargs: _Connection(state),
        )

    assert state.deleted == 0


def test_running_app_is_stopped_and_identity_pinned_before_legacy_repair() -> None:
    state = _State(role_convergence.LEGACY_API_OAUTH_PROFILE, app_state="RUNNING")
    client = _client(state)

    result = role_convergence.converge_role(
        client,
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id="service-principal-id",
        role_contract="app",
        repair_legacy_replication=True,
        app_name="mip-app",
        stop_app_for_mutation=True,
        connect=lambda **_kwargs: _Connection(state),
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
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id="service-principal-id",
            role_contract="app",
            repair_legacy_replication=True,
            app_name="mip-app",
            stop_app_for_mutation=True,
            connect=lambda **_kwargs: _Connection(state),
        )

    assert state.deleted == 0


def test_stop_for_mutation_rejects_app_scim_identity_drift() -> None:
    state = _State(role_convergence.LEGACY_API_OAUTH_PROFILE, app_state="RUNNING")
    client = _client(state)
    client.apps.get.return_value.service_principal_id = "other-scim-id"

    with pytest.raises(RuntimeError, match="does not match"):
        role_convergence.converge_role(
            client,
            instance_name="mip-app-state",
            database_name="mip_app_state",
            application_id="service-principal-id",
            role_contract="app",
            repair_legacy_replication=True,
            app_name="mip-app",
            stop_app_for_mutation=True,
            connect=lambda **_kwargs: _Connection(state),
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
        dependencies=[
            (42, "pg_class", 0, dependency_kind, "mip_app", "campaigns", "", "r")
        ],
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
        create_relationships=[
            ("service-principal-id", "other-identity", True, False, False)
        ],
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
    client = MagicMock()
    client.service_principals.list.side_effect = lambda **_kwargs: iter([])
    client.database.list_database_instances.side_effect = lambda: iter([])

    role_convergence.recover_role_bootstrap(
        client,
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id="service-principal-id",
        connect=lambda **_kwargs: pytest.fail("clean workspace attempted a DB connection"),
    )

    assert client.database.list_database_instances.call_count == 3
    client.database.get_database_instance.assert_not_called()


def test_recovery_only_instance_present_database_absent_never_blocks_bundle_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingDatabaseError(RuntimeError):
        sqlstate = "3D000"

    monkeypatch.setattr(role_recovery.time, "sleep", lambda _seconds: None)
    client = MagicMock()
    client.service_principals.list.side_effect = lambda **_kwargs: iter([])
    client.database.list_database_instances.side_effect = lambda: iter(
        [SimpleNamespace(name="mip-app-state")]
    )
    client.database.get_database_instance.return_value = SimpleNamespace(
        read_write_dns="instance.database.cloud.databricks.com"
    )
    client.database.generate_database_credential.return_value = SimpleNamespace(token="token")
    client.current_user.me.return_value = SimpleNamespace(
        application_id="deployment-service-principal",
        user_name=None,
    )
    connection_attempts = 0

    def connect(**_kwargs: Any) -> Any:
        nonlocal connection_attempts
        connection_attempts += 1
        raise MissingDatabaseError("database does not exist")

    role_convergence.recover_role_bootstrap(
        client,
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id="service-principal-id",
        connect=connect,
    )

    assert connection_attempts == 3
    assert client.database.list_database_instances.call_count == 1
