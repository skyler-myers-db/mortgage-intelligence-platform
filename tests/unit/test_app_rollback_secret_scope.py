from __future__ import annotations

import base64
from copy import deepcopy
from types import SimpleNamespace

import pytest
from databricks.sdk.service.workspace import AclPermission

from tools.databricks import app_rollback_secret_scope as rollback_scope
from tools.databricks.app_rollback_secret_scope import (
    MARKER_KEY,
    AppRollbackScopeBinding,
    assert_owned_app_rollback_scope,
    ensure_owned_app_rollback_scope,
    expected_app_rollback_scope,
)

APP_NAME = "mip-app"
SCOPE = "mip-app-rollback"


class _Secrets:
    def __init__(self) -> None:
        self.scopes: set[str] = set()
        self.acls: dict[str, dict[str, str]] = {}
        self.keys: dict[str, dict[str, str]] = {}

    def list_scopes(self):
        return [SimpleNamespace(name=name) for name in sorted(self.scopes)]

    def create_scope(self, *, scope: str) -> None:
        self.scopes.add(scope)
        self.acls[scope] = {"deployer@example.com": "MANAGE"}
        self.keys[scope] = {}

    def list_acls(self, *, scope: str):
        return [
            SimpleNamespace(principal=principal, permission=permission)
            for principal, permission in sorted(self.acls[scope].items())
        ]

    def put_acl(
        self,
        *,
        scope: str,
        principal: str,
        permission: AclPermission,
    ) -> None:
        assert isinstance(permission, AclPermission)
        self.acls[scope][principal] = permission.value

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.keys[scope][key] = string_value

    def get_secret(self, scope: str, key: str):
        return SimpleNamespace(
            value=base64.b64encode(self.keys[scope][key].encode()).decode()
        )

    def list_secrets(self, *, scope: str):
        return [SimpleNamespace(key=key) for key in sorted(self.keys[scope])]


def _workspace(
    secrets: _Secrets,
    *,
    user_name: str = "deployer@example.com",
) -> object:
    return SimpleNamespace(
        secrets=secrets,
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name=user_name,
                application_id="",
            )
        ),
    )


def test_fresh_scope_is_bound_to_app_and_exact_deployer_admin_acl() -> None:
    secrets = _Secrets()

    binding = ensure_owned_app_rollback_scope(
        _workspace(secrets),
        app_name=APP_NAME,
        scope=SCOPE,
    )

    assert binding.deployer_principal == "deployer@example.com"
    assert secrets.acls[SCOPE] == {
        "admins": "MANAGE",
        "deployer@example.com": "MANAGE",
    }
    assert set(secrets.keys[SCOPE]) == {MARKER_KEY}


def test_staging_suffix_scope_name_is_deterministic() -> None:
    assert (
        expected_app_rollback_scope("mip-app-pr105-staging")
        == "mip-app-rollback-pr105-staging"
    )


@pytest.mark.parametrize("with_admins", [False, True])
def test_safe_unmarked_request_issuer_initialization_is_resumable(
    with_admins: bool,
) -> None:
    secrets = _Secrets()
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {"deployer@example.com": "MANAGE"}
    if with_admins:
        secrets.acls[SCOPE]["admins"] = "MANAGE"
    secrets.keys[SCOPE] = {}

    ensure_owned_app_rollback_scope(
        _workspace(secrets),
        app_name=APP_NAME,
        scope=SCOPE,
    )

    assert secrets.acls[SCOPE] == {
        "admins": "MANAGE",
        "deployer@example.com": "MANAGE",
    }
    assert set(secrets.keys[SCOPE]) == {MARKER_KEY}


def test_signed_legacy_v5_scope_is_adopted_only_after_record_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = _Secrets()
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {"deployer@example.com": "MANAGE"}
    secrets.keys[SCOPE] = {"app-last-good-v5-mip-app": "signed-legacy-record"}
    validated: list[tuple[str, str]] = []
    monkeypatch.setattr(
        rollback_scope,
        "_assert_valid_signed_legacy_record",
        lambda _workspace, *, app_name, scope: validated.append((app_name, scope)),
    )

    ensure_owned_app_rollback_scope(
        _workspace(secrets),
        app_name=APP_NAME,
        scope=SCOPE,
    )

    assert validated == [(APP_NAME, SCOPE)]
    assert set(secrets.keys[SCOPE]) == {
        MARKER_KEY,
        "app-last-good-v5-mip-app",
    }


def test_invalid_legacy_v5_scope_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = _Secrets()
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {"deployer@example.com": "MANAGE"}
    secrets.keys[SCOPE] = {"app-last-good-v5-mip-app": "invalid"}
    monkeypatch.setattr(
        rollback_scope,
        "_assert_valid_signed_legacy_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("legacy App rollback record is invalid")
        ),
    )

    with pytest.raises(RuntimeError, match="legacy App rollback record is invalid"):
        ensure_owned_app_rollback_scope(
            _workspace(secrets),
            app_name=APP_NAME,
            scope=SCOPE,
        )

    assert MARKER_KEY not in secrets.keys[SCOPE]


def test_existing_owned_scope_allows_only_same_app_record_keys() -> None:
    secrets = _Secrets()
    workspace = _workspace(secrets)
    ensure_owned_app_rollback_scope(workspace, app_name=APP_NAME, scope=SCOPE)
    secrets.keys[SCOPE]["app-last-good-v5-mip-app"] = "legacy"
    secrets.keys[SCOPE]["app-last-good-v6-mip-app"] = "current"

    binding = assert_owned_app_rollback_scope(
        workspace,
        app_name=APP_NAME,
        scope=SCOPE,
    )

    assert binding.app_name == APP_NAME


def test_foreign_unmarked_existing_scope_is_refused_without_mutation() -> None:
    secrets = _Secrets()
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {"attacker@example.com": "MANAGE"}
    secrets.keys[SCOPE] = {}
    before = (deepcopy(secrets.acls), deepcopy(secrets.keys))

    with pytest.raises(RuntimeError, match="current request issuer"):
        ensure_owned_app_rollback_scope(
            _workspace(secrets),
            app_name=APP_NAME,
            scope=SCOPE,
        )

    assert (secrets.acls, secrets.keys) == before


def test_attacker_squatted_marker_and_acl_are_not_trusted_by_real_deployer() -> None:
    secrets = _Secrets()
    binding = AppRollbackScopeBinding(
        app_name=APP_NAME,
        scope=SCOPE,
        deployer_principal="attacker@example.com",
    )
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {
        "admins": "MANAGE",
        "attacker@example.com": "MANAGE",
    }
    secrets.keys[SCOPE] = {MARKER_KEY: binding.canonical_json()}

    with pytest.raises(RuntimeError, match="current deployment issuer"):
        assert_owned_app_rollback_scope(
            _workspace(secrets, user_name="deployer@example.com"),
            app_name=APP_NAME,
            scope=SCOPE,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda secrets: secrets.acls[SCOPE].update({"attacker": "READ"}),
        lambda secrets: secrets.keys[SCOPE].update({"other-app-secret": "value"}),
        lambda secrets: secrets.keys[SCOPE].update(
            {"app-last-good-v6-other-app": "value"}
        ),
    ],
    ids=["foreign-acl", "foreign-key", "other-app-record"],
)
def test_owned_scope_rejects_acl_or_key_inventory_drift(mutate) -> None:
    secrets = _Secrets()
    workspace = _workspace(secrets)
    ensure_owned_app_rollback_scope(workspace, app_name=APP_NAME, scope=SCOPE)
    mutate(secrets)

    with pytest.raises(RuntimeError, match="ownership proof|unreviewed key"):
        assert_owned_app_rollback_scope(
            workspace,
            app_name=APP_NAME,
            scope=SCOPE,
        )


def test_deterministic_scope_name_is_required_before_provider_calls() -> None:
    assert expected_app_rollback_scope(APP_NAME) == SCOPE
    workspace = SimpleNamespace(
        secrets=SimpleNamespace(
            list_scopes=lambda: pytest.fail("provider must not be called")
        )
    )

    with pytest.raises(ValueError, match="deterministic App-bound"):
        ensure_owned_app_rollback_scope(
            workspace,
            app_name=APP_NAME,
            scope="shared-rollback",
        )
