"""Tests for the one-use Lakebase OAuth role bootstrap identity."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from tools.databricks import lakebase_oauth_role_bootstrap as bootstrap
from tools.databricks import lakebase_oauth_role_recovery as recovery
from tools.databricks import lakebase_oauth_role_tombstone as tombstone

_TARGET = "target-service-principal"
_TARGET_SCIM_ID = "target-service-principal-scim-id"
_CREATOR = "bootstrap-application-id"
_CREATOR_SCIM_ID = "bootstrap-scim-id"
_INSTANCE = "mip-app-state"
_DATABASE = "mip_app_state"
_SIGNING_KEY = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
_VERIFY_KEY = derive_gateway_proof_verify_key(_SIGNING_KEY)
_ATTACKER_SIGNING_KEY = base64.urlsafe_b64encode(b"a" * 32).decode().rstrip("=")
_ATTACKER_VERIFY_KEY = derive_gateway_proof_verify_key(_ATTACKER_SIGNING_KEY)
_DISPLAY_NAME, _EXTERNAL_ID = recovery._bootstrap_identity_contract(
    instance_name=_INSTANCE,
    database_name=_DATABASE,
    application_id=_TARGET,
)


@pytest.fixture(autouse=True)
def _no_recovery_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(recovery.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS", raising=False)


class _State:
    def __init__(self) -> None:
        self.target_profile: tuple[bool, ...] | None = None
        self.target_relationships: list[tuple[Any, ...]] = []
        self.function_exists = True
        self.function_source_sha256 = bootstrap._ROLE_FUNCTION_SOURCE_SHA256
        self.function_acl: tuple[str, ...] | None = None
        self.created_creator_role = False
        self.creator_profile: tuple[bool, ...] | None = None
        self.creator_control_plane_present = False
        self.deleted_creator_role = False
        self.deleted_creator_sp = False
        self.fail_creator_role_delete = False
        self.fail_secret_list = False
        self.fail_secret_delete = False
        self.fail_creator_sp_delete = False
        self.fail_marker_inventory = False
        self.fail_role_comment = False
        self.create_sp_commit_then_error = False
        self.bootstrap_sp_present = False
        self.bootstrap_sp_active = True
        self.bootstrap_secrets: list[str] = []
        self.orphan_tombstones: dict[str, SimpleNamespace] = {}
        self.creator_role_comment: str | None = None
        self.creator_database_privileges: list[str] = []
        self.creator_dependencies: list[tuple[Any, ...]] = []
        self.create_profile = bootstrap.SAFE_OAUTH_PROFILE
        self.create_relationships = [
            (_TARGET, _CREATOR, True, False, False, "cloud_admin")
        ]
        self.deployer_statements: list[str] = []


def _render(query: Any) -> str:
    value = query.as_string() if hasattr(query, "as_string") else str(query)
    return " ".join(value.split())


class _DeployerCursor:
    def __init__(self, state: _State) -> None:
        self.state = state
        self._one: tuple[Any, ...] | None = None
        self._all: list[tuple[Any, ...]] = []

    def execute(self, query: Any, params: object = None) -> None:
        rendered = _render(query)
        self.state.deployer_statements.append(rendered)
        self._one = None
        self._all = []
        if "JOIN pg_depend extension_membership" in rendered:
            self._one = (
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
                    "databricks_writer_42",
                    self.state.function_source_sha256,
                    bootstrap._ROLE_FUNCTION_SOURCE_BYTES,
                    self.state.function_acl,
                )
                if self.state.function_exists
                else None
            )
        elif rendered == "SELECT oid FROM pg_database WHERE datname = current_database()":
            self._one = (42,)
        elif "rolreplication" in rendered and "FROM pg_roles" in rendered:
            role_name = params[0] if isinstance(params, tuple) else None
            self._one = (
                self.state.creator_profile
                if role_name == _CREATOR
                else self.state.target_profile
            )
        elif "pg_shseclabel" in rendered:
            role_name = params[0] if isinstance(params, tuple) else None
            scim_id = _CREATOR_SCIM_ID if role_name == _CREATOR else _TARGET_SCIM_ID
            self._all = [
                ("databricks_auth", f"id={scim_id},type=service_principal")
            ]
        elif rendered.startswith("SELECT 1 FROM pg_auth_members"):
            self._one = (1,) if self.state.target_relationships else None
        elif "FROM pg_auth_members membership" in rendered:
            self._all = list(self.state.target_relationships)
        elif "CROSS JOIN aclexplode(database_object.datacl)" in rendered:
            self._one = (list(self.state.creator_database_privileges),)
        elif "FROM pg_shdepend dependency" in rendered:
            self._all = list(self.state.creator_dependencies)
        elif rendered.startswith("SELECT shobj_description"):
            self._one = (self.state.creator_role_comment,)
        elif "WHERE shobj_description(role.oid" in rendered:
            if self.state.fail_marker_inventory:
                raise RuntimeError("injected marker-inventory failure")
            self._all = [(_CREATOR,)] if self.state.creator_role_comment == _EXTERNAL_ID else []
        elif rendered.startswith("COMMENT ON ROLE"):
            if self.state.fail_role_comment:
                raise RuntimeError("injected role-comment visibility failure")
            self.state.creator_role_comment = _EXTERNAL_ID
        elif rendered.startswith("GRANT CREATE ON DATABASE"):
            self.state.creator_database_privileges = ["CREATE"]
            self.state.creator_dependencies = [(0, "pg_database", 0, "a", _DATABASE)]
        elif rendered == (
            "GRANT EXECUTE ON FUNCTION public.databricks_create_role(text,text) TO PUBLIC"
        ):
            self.state.function_acl = (
                "=X/cloud_admin",
                "cloud_admin=X/cloud_admin",
            )

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._all


class _BootstrapCursor:
    def __init__(self, state: _State) -> None:
        self.state = state
        self._one: tuple[Any, ...] | None = None
        self._all: list[tuple[Any, ...]] = []

    def __enter__(self) -> _BootstrapCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: Any, _params: object = None) -> None:
        rendered = _render(query)
        self._one = None
        self._all = []
        if rendered == "SELECT current_user":
            self._one = (_CREATOR,)
        elif "databricks_create_role" in rendered:
            self.state.target_profile = self.state.create_profile
            self.state.target_relationships = list(self.state.create_relationships)
        elif "rolreplication" in rendered and "FROM pg_roles" in rendered:
            self._one = self.state.target_profile
        elif "pg_shseclabel" in rendered:
            self._all = [
                ("databricks_auth", f"id={_TARGET_SCIM_ID},type=service_principal")
            ]
        elif "FROM pg_auth_members" in rendered:
            self._all = list(self.state.target_relationships)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._all


class _BootstrapConnection:
    def __init__(self, state: _State) -> None:
        self.state = state

    def __enter__(self) -> _BootstrapConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _BootstrapCursor:
        return _BootstrapCursor(self.state)


def _client(state: _State, *, secret: str = "one-use-secret") -> MagicMock:
    client = MagicMock()
    client.config.host = "https://reviewed.cloud.databricks.com"

    def principal() -> SimpleNamespace:
        return SimpleNamespace(
            id=_CREATOR_SCIM_ID,
            application_id=_CREATOR,
            display_name=_DISPLAY_NAME,
            external_id=_EXTERNAL_ID,
            active=state.bootstrap_sp_active,
            groups=[],
            roles=[],
            entitlements=[],
        )

    def create_sp(**kwargs: Any) -> SimpleNamespace:
        if kwargs.get("active") is False:
            marker_id = f"orphan-marker-{len(state.orphan_tombstones) + 1}"
            marker = SimpleNamespace(
                id=marker_id,
                application_id=f"orphan-marker-app-{len(state.orphan_tombstones) + 1}",
                display_name=kwargs["display_name"],
                external_id=kwargs["external_id"],
                active=False,
                groups=[],
                roles=[],
                entitlements=[],
            )
            state.orphan_tombstones[marker_id] = marker
            return marker
        assert kwargs == {
            "display_name": _DISPLAY_NAME,
            "external_id": _EXTERNAL_ID,
            "active": True,
        }
        state.bootstrap_sp_present = True
        if state.create_sp_commit_then_error:
            raise RuntimeError("ambiguous service-principal create")
        return principal()

    client.service_principals.create.side_effect = create_sp
    def list_principals(**kwargs: Any) -> Any:
        items = [principal()] if state.bootstrap_sp_present else []
        items.extend(state.orphan_tombstones.values())
        filter_expr = str(kwargs.get("filter") or "")
        if filter_expr.startswith("displayName eq '"):
            expected = filter_expr.removeprefix("displayName eq '").removesuffix("'")
            items = [item for item in items if item.display_name == expected]
        elif filter_expr.startswith("externalId eq '"):
            expected = filter_expr.removeprefix("externalId eq '").removesuffix("'")
            items = [item for item in items if item.external_id == expected]
        return iter(items)

    client.service_principals.list.side_effect = list_principals

    def get_principal(principal_id: str) -> SimpleNamespace:
        if principal_id in state.orphan_tombstones:
            return state.orphan_tombstones[principal_id]
        return principal()

    client.service_principals.get.side_effect = get_principal

    def patch_sp(*, id: str, operations: list[Any], schemas: list[Any]) -> None:
        assert id == _CREATOR_SCIM_ID
        assert len(operations) == 1
        assert str(getattr(operations[0], "path", "")) == "active"
        assert getattr(operations[0], "value", None) is False
        assert len(schemas) == 1
        state.bootstrap_sp_active = False

    client.service_principals.patch.side_effect = patch_sp

    def create_secret(_id: str) -> SimpleNamespace:
        state.bootstrap_secrets = ["secret-id"]
        return SimpleNamespace(id="secret-id", secret=secret)

    def list_secrets(_id: str) -> Any:
        if _id in state.orphan_tombstones:
            return iter([])
        if state.fail_secret_list:
            raise RuntimeError("injected secret-list failure")
        return iter(SimpleNamespace(id=value) for value in state.bootstrap_secrets)

    def delete_secret(_sp_id: str, secret_id: str) -> None:
        if state.fail_secret_delete:
            raise RuntimeError("injected secret-delete failure")
        state.bootstrap_secrets.remove(secret_id)

    client.service_principal_secrets_proxy.create.side_effect = create_secret
    client.service_principal_secrets_proxy.list.side_effect = list_secrets
    client.service_principal_secrets_proxy.delete.side_effect = delete_secret
    client.apps.list.return_value = iter([])

    def create_role(_instance: str, role: Any) -> None:
        assert role.name == _CREATOR
        assert role.attributes.createrole is True
        assert role.attributes.createdb is False
        assert role.attributes.bypassrls is False
        state.created_creator_role = True
        state.creator_control_plane_present = True
        state.creator_profile = bootstrap._BOOTSTRAP_API_PROFILE

    def delete_role(_instance: str, role_name: str) -> None:
        assert role_name == _CREATOR
        if state.fail_creator_role_delete:
            raise RuntimeError("injected creator-role cleanup failure")
        state.deleted_creator_role = True
        state.creator_control_plane_present = False
        state.creator_profile = None
        state.target_relationships = []
        state.creator_role_comment = None
        state.creator_database_privileges = []
        state.creator_dependencies = []

    def delete_sp(sp_id: str) -> None:
        if sp_id in state.orphan_tombstones:
            state.orphan_tombstones.pop(sp_id)
            return
        assert sp_id == _CREATOR_SCIM_ID
        if state.fail_creator_sp_delete:
            raise RuntimeError("injected creator-SP cleanup failure")
        state.deleted_creator_sp = True
        state.bootstrap_sp_present = False

    client.database.create_database_instance_role.side_effect = create_role
    def list_roles(_name: str) -> Any:
        roles = [
            SimpleNamespace(
                name=_CREATOR,
                identity_type=SimpleNamespace(value="SERVICE_PRINCIPAL"),
            )
        ] if state.creator_control_plane_present else []
        if state.target_profile is not None:
            roles.append(
                SimpleNamespace(
                    name=_TARGET,
                    identity_type=SimpleNamespace(value="SERVICE_PRINCIPAL"),
                )
            )
        return iter(roles)

    client.database.list_database_instance_roles.side_effect = list_roles
    client.database.delete_database_instance_role.side_effect = delete_role
    client.service_principals.delete.side_effect = delete_sp
    return client


def _workspace_factory(**kwargs: Any) -> SimpleNamespace:
    assert kwargs == {
        "host": "https://reviewed.cloud.databricks.com",
        "client_id": _CREATOR,
        "client_secret": "one-use-secret",
        "auth_type": "oauth-m2m",
    }
    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(application_id=_CREATOR, user_name=_CREATOR)
        ),
        database=SimpleNamespace(
            get_database_instance=lambda _name: SimpleNamespace(
                read_write_dns="reviewed.database.example"
            ),
            generate_database_credential=lambda **_kwargs: SimpleNamespace(
                token="database-token"
            ),
        ),
    )


def _run(state: _State, *, secret: str = "one-use-secret") -> None:
    bootstrap.create_login_only_role(
        _client(state, secret=secret),
        _DeployerCursor(state),
        instance_name="mip-app-state",
        database_name="mip_app_state",
        application_id=_TARGET,
        service_principal_id=_TARGET_SCIM_ID,
        connect=lambda **_kwargs: _BootstrapConnection(state),
        workspace_client_factory=_workspace_factory,
    )


def _seed_stale_creator(state: _State) -> None:
    state.bootstrap_sp_present = True
    state.bootstrap_sp_active = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.created_creator_role = True
    state.creator_control_plane_present = True
    state.creator_profile = bootstrap._BOOTSTRAP_API_PROFILE
    state.creator_role_comment = _EXTERNAL_ID
    state.creator_database_privileges = ["CREATE"]
    state.creator_dependencies = [(0, "pg_database", 0, "a", _DATABASE)]
    state.target_profile = bootstrap.SAFE_OAUTH_PROFILE
    state.target_relationships = [
        (_TARGET, _CREATOR, True, False, False, "cloud_admin")
    ]


def test_one_use_creator_leaves_safe_role_and_empty_membership_graph() -> None:
    state = _State()

    _run(state)

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    assert state.target_relationships == []
    assert state.created_creator_role is True
    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True
    assert any(statement.startswith("GRANT CREATE ON DATABASE") for statement in state.deployer_statements)


def test_missing_role_function_fails_after_stale_identity_recovery() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.function_exists = False

    with pytest.raises(RuntimeError, match="function contract is absent"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.creator_profile is None
    assert state.target_relationships == []


def test_role_function_metadata_drift_fails_before_identity_creation() -> None:
    state = _State()
    state.function_source_sha256 = "0" * 64

    with pytest.raises(RuntimeError, match="function contract drifted"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.created_creator_role is False


def test_unsafe_sql_created_profile_fails_and_cleans_creator() -> None:
    state = _State()
    state.create_profile = bootstrap.LEGACY_API_OAUTH_PROFILE

    with pytest.raises(RuntimeError, match="unsafe role attributes"):
        _run(state)

    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True


def test_unreviewed_bootstrap_membership_fails_and_cleans_creator() -> None:
    state = _State()
    state.create_relationships = [
        (_TARGET, "other-identity", True, False, False, "cloud_admin")
    ]

    with pytest.raises(RuntimeError, match="unreviewed bootstrap membership"):
        _run(state)

    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True


def test_creator_role_cleanup_failure_is_release_blocking() -> None:
    state = _State()
    state.fail_creator_role_delete = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        _run(state)

    assert state.deleted_creator_sp is False
    assert state.bootstrap_sp_active is False
    assert state.bootstrap_sp_present is True
    assert state.target_relationships
    assert state.bootstrap_secrets == []
    assert state.creator_role_comment == _EXTERNAL_ID


def test_profile_drift_with_unproven_role_deletion_retains_inactive_marker() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.creator_profile = bootstrap.LEGACY_API_OAUTH_PROFILE
    state.fail_creator_role_delete = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.bootstrap_sp_present is True
    assert state.bootstrap_sp_active is False
    assert state.bootstrap_secrets == []
    assert state.creator_role_comment == _EXTERNAL_ID


def test_missing_one_use_secret_fails_before_database_role_creation() -> None:
    state = _State()

    with pytest.raises(RuntimeError, match="credential was not returned"):
        _run(state, secret="")

    assert state.created_creator_role is False
    assert state.deleted_creator_sp is True


def test_stale_creator_is_recovered_before_another_bootstrap() -> None:
    state = _State()
    _seed_stale_creator(state)

    recovery.recover_stale_bootstrap_identities(
        _client(state),
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.bootstrap_sp_present is False
    assert state.creator_profile is None
    assert state.bootstrap_secrets == []
    assert state.target_relationships == []


def test_ambiguous_principal_create_is_recovered() -> None:
    state = _State()
    state.create_sp_commit_then_error = True

    with pytest.raises(RuntimeError, match="ambiguous service-principal create"):
        _run(state)

    assert state.bootstrap_sp_present is False
    assert state.creator_profile is None


@pytest.mark.parametrize("failure", ["list", "delete"])
def test_credential_cleanup_failure_still_removes_role_and_principal(failure: str) -> None:
    state = _State()
    if failure == "list":
        state.fail_secret_list = True
    else:
        state.fail_secret_delete = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        _run(state)

    assert state.creator_profile is None
    assert state.bootstrap_sp_present is False


def test_principal_delete_failure_still_revokes_credentials_and_role() -> None:
    state = _State()
    state.fail_creator_sp_delete = True

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        _run(state)

    assert state.creator_profile is None
    assert state.bootstrap_secrets == []
    assert state.bootstrap_sp_active is False


def test_reserved_display_name_with_wrong_external_marker_is_never_deleted() -> None:
    state = _State()
    client = _client(state)
    conflicting = SimpleNamespace(
        id="unrelated-sp",
        application_id="unrelated-app",
        display_name=_DISPLAY_NAME,
        external_id="urn:someone-else",
    )
    client.service_principals.list.side_effect = lambda **_kwargs: iter([conflicting])

    with pytest.raises(RuntimeError, match="marker is ambiguous"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    client.service_principals.delete.assert_not_called()


def test_delayed_workspace_marker_visibility_cannot_create_a_duplicate() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    client = _client(state)
    candidate = SimpleNamespace(
        id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_DISPLAY_NAME,
        external_id=_EXTERNAL_ID,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    calls = 0

    def delayed_list(**_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return iter([] if calls <= 4 else [candidate] if state.bootstrap_sp_present else [])

    client.service_principals.list.side_effect = delayed_list

    recovery.recover_stale_bootstrap_identities(
        client,
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert calls >= 10
    assert state.deleted_creator_sp is True
    assert state.bootstrap_secrets == []


def test_marker_inventory_failure_quarantines_credentials_before_failing() -> None:
    state = _State()
    _seed_stale_creator(state)
    state.fail_marker_inventory = True

    with pytest.raises(RuntimeError, match="database marker inventory"):
        recovery.recover_stale_bootstrap_identities(
            _client(state),
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.bootstrap_sp_active is False
    assert state.bootstrap_secrets == []
    assert state.creator_profile is None
    assert state.bootstrap_sp_present is False


def test_control_plane_only_role_is_deleted_after_sql_projection_disappears() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.creator_control_plane_present = True

    recovery.recover_stale_bootstrap_identities(
        _client(state),
        _DeployerCursor(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True
    assert state.creator_control_plane_present is False


def test_absent_instance_cleanup_aggregates_duplicate_principal_failures() -> None:
    principals = {
        "sp-a": SimpleNamespace(
            id="sp-a",
            application_id="app-a",
            display_name=_DISPLAY_NAME,
            external_id=_EXTERNAL_ID,
            active=True,
            groups=[],
            roles=[],
            entitlements=[],
        ),
        "sp-b": SimpleNamespace(
            id="sp-b",
            application_id="app-b",
            display_name=_DISPLAY_NAME,
            external_id=_EXTERNAL_ID,
            active=True,
            groups=[],
            roles=[],
            entitlements=[],
        ),
    }
    client = MagicMock()
    client.service_principals.list.side_effect = lambda **_kwargs: iter(principals.values())
    client.service_principals.get.side_effect = lambda principal_id: principals[principal_id]
    client.apps.list.return_value = iter([])
    client.service_principal_secrets_proxy.list.side_effect = lambda _id: iter([])
    client.database.list_database_instances.return_value = iter([])
    updated: list[str] = []
    deleted: list[str] = []

    def update(*, id: str, operations: list[Any], schemas: list[Any]) -> None:
        updated.append(id)
        assert operations and schemas
        principals[id].active = False
        if id == "sp-a":
            raise RuntimeError("injected credential quarantine failure")

    def delete(principal_id: str) -> None:
        deleted.append(principal_id)
        principals.pop(principal_id, None)

    client.service_principals.patch.side_effect = update
    client.service_principals.delete.side_effect = delete

    with pytest.raises(RuntimeError, match="credential cleanup"):
        recovery.recover_bootstrap_principals_for_absent_instance(
            client,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert set(updated) == {"sp-a", "sp-b"}
    assert set(deleted) == {"sp-a", "sp-b"}
    assert principals == {}


def test_absent_database_cleanup_removes_control_plane_role_before_principal() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.creator_control_plane_present = True

    recovery.recover_bootstrap_principals_for_absent_instance(
        _client(state),
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        marker_signing_key=_SIGNING_KEY,
        resource_absence_probe=lambda: True,
        recover_control_plane_roles=True,
    )

    assert state.deleted_creator_role is True
    assert state.deleted_creator_sp is True
    assert state.creator_control_plane_present is False
    assert state.bootstrap_sp_present is False


def test_absent_database_cleanup_never_deletes_target_role_from_signed_marker() -> None:
    state = _State()
    state.target_profile = bootstrap.SAFE_OAUTH_PROFILE
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_TARGET,
        signing_key=_SIGNING_KEY,
    )
    state.orphan_tombstones["signed-target-marker"] = SimpleNamespace(
        id="signed-target-marker",
        application_id="signed-target-marker-application",
        display_name=display_name,
        external_id=external_id,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = _client(state)

    with pytest.raises(RuntimeError, match="target runtime identity"):
        recovery.recover_bootstrap_principals_for_absent_instance(
            client,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            marker_signing_key=_SIGNING_KEY,
            resource_absence_probe=lambda: True,
            recover_control_plane_roles=True,
        )

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    client.database.delete_database_instance_role.assert_not_called()


def test_triple_failure_persists_orphan_handle_and_retry_removes_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.creator_control_plane_present = True
    state.fail_secret_list = True
    state.fail_creator_role_delete = True
    state.fail_role_comment = True
    client = _client(state)
    cursor = _DeployerCursor(state)

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_stale_bootstrap_identities(
            client,
            cursor,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.bootstrap_sp_present is False
    assert state.creator_control_plane_present is True
    assert len(state.orphan_tombstones) == 1
    marker = next(iter(state.orphan_tombstones.values()))
    assert marker.active is False
    assert marker.groups == marker.roles == marker.entitlements == []

    state.fail_secret_list = False
    state.fail_creator_role_delete = False
    state.fail_role_comment = False
    recovery.recover_stale_bootstrap_identities(
        client,
        cursor,
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
    )

    assert state.creator_control_plane_present is False
    assert state.orphan_tombstones == {}


def test_signed_orphan_marker_survives_proof_key_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_signing_key = base64.urlsafe_b64encode(b"o" * 32).decode().rstrip("=")
    old_verify_key = derive_gateway_proof_verify_key(old_signing_key)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", old_verify_key)
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        signing_key=old_signing_key,
    )
    state = _State()
    state.orphan_tombstones["old-key-marker"] = SimpleNamespace(
        id="old-key-marker",
        application_id="old-key-marker-application",
        display_name=display_name,
        external_id=external_id,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", old_verify_key)

    markers = tombstone.orphan_tombstones(
        _client(state),
        base_external_id=_EXTERNAL_ID,
    )

    assert [(marker[0], marker[1]) for marker in markers] == [
        ("old-key-marker", _CREATOR)
    ]


def test_absent_database_dual_failure_persists_tombstone_then_retry_converges() -> None:
    state = _State()
    state.bootstrap_sp_present = True
    state.bootstrap_secrets = ["stale-secret-id"]
    state.creator_control_plane_present = True
    state.fail_secret_list = True
    state.fail_creator_role_delete = True
    client = _client(state)

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        recovery.recover_bootstrap_principals_for_absent_instance(
            client,
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
            resource_absence_probe=lambda: True,
            recover_control_plane_roles=True,
        )

    assert state.bootstrap_sp_present is False
    assert state.creator_control_plane_present is True
    assert len(state.orphan_tombstones) == 1

    state.fail_secret_list = False
    state.fail_creator_role_delete = False
    recovery.recover_bootstrap_principals_for_absent_instance(
        client,
        instance_name=_INSTANCE,
        database_name=_DATABASE,
        target_application_id=_TARGET,
        resource_absence_probe=lambda: True,
        recover_control_plane_roles=True,
    )

    assert state.creator_control_plane_present is False
    assert state.orphan_tombstones == {}


def test_scim_external_markers_remain_within_documented_limit() -> None:
    assert len(_EXTERNAL_ID) <= 64
    _display, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        signing_key=_SIGNING_KEY,
    )
    assert len(_display) <= 255
    assert len(external_id) <= 64


def test_legacy_owner_only_role_function_acl_is_repaired_after_metadata_proof() -> None:
    state = _State()
    state.function_acl = ("cloud_admin=X/cloud_admin",)
    cursor = _DeployerCursor(state)

    bootstrap._assert_role_function_contract(cursor)

    assert state.function_acl == ("=X/cloud_admin", "cloud_admin=X/cloud_admin")
    assert cursor.state.deployer_statements.count(
        "GRANT EXECUTE ON FUNCTION public.databricks_create_role(text,text) TO PUBLIC"
    ) == 1


def test_role_function_acl_repair_rejects_unreviewed_grantee() -> None:
    state = _State()
    state.function_acl = ("attacker=X/cloud_admin", "cloud_admin=X/cloud_admin")
    cursor = _DeployerCursor(state)

    with pytest.raises(RuntimeError, match="role-creation function contract drifted"):
        bootstrap._assert_role_function_contract(cursor)

    assert not any(statement.startswith("GRANT EXECUTE") for statement in cursor.state.deployer_statements)


def test_bootstrap_principal_rejects_unreviewed_group_membership() -> None:
    principal = SimpleNamespace(id=_CREATOR_SCIM_ID)
    exact = SimpleNamespace(
        id=_CREATOR_SCIM_ID,
        application_id=_CREATOR,
        display_name=_DISPLAY_NAME,
        external_id=_EXTERNAL_ID,
        groups=[SimpleNamespace(display="users", value="users")],
        roles=[],
        entitlements=[],
    )
    client = MagicMock()
    client.service_principals.get.return_value = exact
    client.apps.list.return_value = iter([])

    with pytest.raises(RuntimeError, match="principal contract drifted"):
        recovery._assert_bootstrap_principal_contract(
            client,
            principal,
            display_name=_DISPLAY_NAME,
            external_id=_EXTERNAL_ID,
        )


def test_forged_orphan_marker_cannot_delete_target_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    state.target_profile = bootstrap.SAFE_OAUTH_PROFILE
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _ATTACKER_VERIFY_KEY)
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_TARGET,
        signing_key=_ATTACKER_SIGNING_KEY,
    )
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", _VERIFY_KEY)
    state.orphan_tombstones["forged-marker"] = SimpleNamespace(
        id="forged-marker",
        application_id="forged-marker-application",
        display_name=display_name,
        external_id=external_id,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = _client(state)

    with pytest.raises(RuntimeError, match="orphan marker inventory"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    client.database.delete_database_instance_role.assert_not_called()


def test_signed_orphan_marker_can_never_name_target_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    state.target_profile = bootstrap.SAFE_OAUTH_PROFILE
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_TARGET,
        signing_key=_SIGNING_KEY,
    )
    state.orphan_tombstones["signed-target-marker"] = SimpleNamespace(
        id="signed-target-marker",
        application_id="signed-target-marker-application",
        display_name=display_name,
        external_id=external_id,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = _client(state)

    with pytest.raises(RuntimeError, match="target runtime identity"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    assert state.target_profile == bootstrap.SAFE_OAUTH_PROFILE
    client.database.delete_database_instance_role.assert_not_called()


def test_malformed_signed_marker_cannot_authorize_role_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        signing_key=_SIGNING_KEY,
    )
    state.orphan_tombstones["malformed-marker"] = SimpleNamespace(
        id="malformed-marker",
        application_id="malformed-marker-application",
        display_name=f"{display_name}:injected",
        external_id=external_id,
        active=False,
        groups=[],
        roles=[],
        entitlements=[],
    )
    client = _client(state)

    with pytest.raises(RuntimeError, match="orphan marker inventory"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    client.database.delete_database_instance_role.assert_not_called()


def test_duplicate_signed_markers_cannot_authorize_role_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", _SIGNING_KEY)
    state = _State()
    display_name, external_id = tombstone.orphan_tombstone_contract(
        base_external_id=_EXTERNAL_ID,
        application_id=_CREATOR,
        signing_key=_SIGNING_KEY,
    )
    for suffix in ("a", "b"):
        marker_id = f"duplicate-marker-{suffix}"
        state.orphan_tombstones[marker_id] = SimpleNamespace(
            id=marker_id,
            application_id=f"duplicate-marker-application-{suffix}",
            display_name=display_name,
            external_id=external_id,
            active=False,
            groups=[],
            roles=[],
            entitlements=[],
        )
    client = _client(state)

    with pytest.raises(RuntimeError, match="orphan marker inventory"):
        recovery.recover_stale_bootstrap_identities(
            client,
            _DeployerCursor(state),
            instance_name=_INSTANCE,
            database_name=_DATABASE,
            target_application_id=_TARGET,
        )

    client.database.delete_database_instance_role.assert_not_called()
