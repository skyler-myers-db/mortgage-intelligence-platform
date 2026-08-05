from __future__ import annotations

import base64
from copy import deepcopy
from types import SimpleNamespace

import pytest
from databricks.sdk.service.workspace import AclPermission

from tools.databricks.agent_proxy_secret_scope import MARKER_KEY, validated_scope_binding
from tools.databricks.provision_agent_proxy_secret import (
    cleanup_signed_blue_agent_proxy,
    credential_key,
    provision_agent_proxy_secret,
    retire_signed_blue_agent_proxy_credentials,
    retire_signed_blue_agent_proxy_secrets,
    secret_reference,
)

APP_NAME = "mip-app-pr105"
SCOPE = f"{APP_NAME}-agent-proxy"
RUNTIME_ID = "runtime-client"
PROXY_ID = "proxy-client"


class _Secrets:
    def __init__(self) -> None:
        self.scopes: set[str] = set()
        self.acls: dict[str, dict[str, str]] = {}
        self.keys: dict[str, dict[str, str]] = {}
        self.deleted: list[tuple[str, str]] = []
        self.create_calls: list[dict[str, str]] = []
        self.deleted_acls: list[tuple[str, str]] = []

    def list_scopes(self):
        return [SimpleNamespace(name=name) for name in sorted(self.scopes)]

    def create_scope(self, *, scope: str) -> None:
        self.create_calls.append({"scope": scope})
        self.scopes.add(scope)
        self.acls[scope] = {"admin@example.com": "MANAGE"}
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

    def delete_acl(self, scope: str, principal: str) -> None:
        self.deleted_acls.append((scope, principal))
        self.acls[scope].pop(principal)

    def put_secret(self, *, scope: str, key: str, string_value: str) -> None:
        self.keys[scope][key] = string_value

    def get_secret(self, scope: str, key: str):
        value = self.keys[scope][key].encode()
        return SimpleNamespace(value=base64.b64encode(value).decode())

    def list_secrets(self, *, scope: str):
        return [SimpleNamespace(key=key) for key in sorted(self.keys[scope])]

    def delete_secret(self, *, scope: str, key: str) -> None:
        self.deleted.append((scope, key))
        self.keys[scope].pop(key, None)


def _owned_scope(secrets: _Secrets, *, credential_ids: tuple[str, ...]) -> None:
    binding = validated_scope_binding(
        app_name=APP_NAME,
        scope=SCOPE,
        runtime_application_id=RUNTIME_ID,
        proxy_application_id=PROXY_ID,
    )
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {"admins": "MANAGE", RUNTIME_ID: "READ"}
    secrets.keys[SCOPE] = {
        MARKER_KEY: binding.canonical_json(),
        **{credential_key(value): value * 40 for value in credential_ids},
    }


def test_provisions_versioned_reference_and_exact_acl_without_proxy_read() -> None:
    secrets = _Secrets()
    workspace = SimpleNamespace(
        secrets=secrets,
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name="admin@example.com",
                application_id="",
            )
        ),
    )

    reference = provision_agent_proxy_secret(
        workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        runtime_application_id=RUNTIME_ID,
        proxy_application_id=PROXY_ID,
        credential_id="credential-123",
        client_secret="s" * 48,
    )

    assert reference == f"{{{{secrets/{SCOPE}/oauth-client-secret-credential-123}}}}"
    assert secrets.acls[SCOPE] == {
        "admins": "MANAGE",
        RUNTIME_ID: "READ",
    }
    assert PROXY_ID not in secrets.acls[SCOPE]
    assert secrets.create_calls == [{"scope": SCOPE}]
    assert secrets.deleted_acls == [(SCOPE, "admin@example.com")]
    assert set(secrets.keys[SCOPE]) == {
        MARKER_KEY,
        "oauth-client-secret-credential-123",
    }


def test_accepts_only_safe_empty_admins_only_interrupted_initialization() -> None:
    secrets = _Secrets()
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {"admins": "MANAGE"}
    secrets.keys[SCOPE] = {}

    provision_agent_proxy_secret(
        SimpleNamespace(secrets=secrets),
        app_name=APP_NAME,
        scope=SCOPE,
        runtime_application_id=RUNTIME_ID,
        proxy_application_id=PROXY_ID,
        credential_id="green",
        client_secret="g" * 48,
    )

    assert secrets.acls[SCOPE] == {"admins": "MANAGE", RUNTIME_ID: "READ"}
    assert set(secrets.keys[SCOPE]) == {MARKER_KEY, credential_key("green")}


def test_resumes_request_issuer_only_interrupted_scope_creation() -> None:
    secrets = _Secrets()
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {"admin@example.com": "MANAGE"}
    secrets.keys[SCOPE] = {}
    workspace = SimpleNamespace(
        secrets=secrets,
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="admin@example.com", application_id="")
        ),
    )

    provision_agent_proxy_secret(
        workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        runtime_application_id=RUNTIME_ID,
        proxy_application_id=PROXY_ID,
        credential_id="green",
        client_secret="g" * 48,
    )

    assert secrets.acls[SCOPE] == {"admins": "MANAGE", RUNTIME_ID: "READ"}
    assert secrets.deleted_acls == [(SCOPE, "admin@example.com")]


def test_resumes_marker_write_before_request_issuer_acl_cleanup() -> None:
    secrets = _Secrets()
    binding = validated_scope_binding(
        app_name=APP_NAME,
        scope=SCOPE,
        runtime_application_id=RUNTIME_ID,
        proxy_application_id=PROXY_ID,
    )
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {
        "admins": "MANAGE",
        "admin@example.com": "MANAGE",
    }
    secrets.keys[SCOPE] = {MARKER_KEY: binding.canonical_json()}
    workspace = SimpleNamespace(
        secrets=secrets,
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="admin@example.com", application_id="")
        ),
    )

    provision_agent_proxy_secret(
        workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        runtime_application_id=RUNTIME_ID,
        proxy_application_id=PROXY_ID,
        credential_id="green",
        client_secret="g" * 48,
    )

    assert secrets.acls[SCOPE] == {"admins": "MANAGE", RUNTIME_ID: "READ"}
    assert secrets.deleted_acls == [(SCOPE, "admin@example.com")]


def test_rejects_disjoint_foreign_creator_acl_without_mutation() -> None:
    secrets = _Secrets()
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {"attacker@example.com": "MANAGE"}
    secrets.keys[SCOPE] = {}
    before = (deepcopy(secrets.acls), deepcopy(secrets.keys))
    workspace = SimpleNamespace(
        secrets=secrets,
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="admin@example.com", application_id="")
        ),
    )

    with pytest.raises(RuntimeError, match="lacks ownership proof"):
        provision_agent_proxy_secret(
            workspace,
            app_name=APP_NAME,
            scope=SCOPE,
            runtime_application_id=RUNTIME_ID,
            proxy_application_id=PROXY_ID,
            credential_id="green",
            client_secret="g" * 48,
        )

    assert (secrets.acls, secrets.keys) == before
    assert secrets.deleted_acls == []


def test_rejects_foreign_existing_scope_without_mutation() -> None:
    secrets = _Secrets()
    secrets.scopes.add(SCOPE)
    secrets.acls[SCOPE] = {
        "admins": "MANAGE",
        RUNTIME_ID: "READ",
        PROXY_ID: "READ",
    }
    secrets.keys[SCOPE] = {"oauth-client-secret-blue": "b" * 48}
    before = (deepcopy(secrets.acls), deepcopy(secrets.keys))

    with pytest.raises(RuntimeError, match="lacks ownership proof"):
        provision_agent_proxy_secret(
            SimpleNamespace(secrets=secrets),
            app_name=APP_NAME,
            scope=SCOPE,
            runtime_application_id=RUNTIME_ID,
            proxy_application_id=PROXY_ID,
            credential_id="green",
            client_secret="g" * 48,
        )

    assert (secrets.acls, secrets.keys) == before
    assert secrets.deleted == []


def test_rejects_non_deterministic_scope_before_provider_mutation() -> None:
    workspace = SimpleNamespace(
        secrets=SimpleNamespace(
            list_scopes=lambda: pytest.fail("provider must not be called"),
        )
    )
    with pytest.raises(ValueError, match="deterministic App-bound scope"):
        provision_agent_proxy_secret(
            workspace,
            app_name=APP_NAME,
            scope="shared-agent-proxy",
            runtime_application_id=RUNTIME_ID,
            proxy_application_id=PROXY_ID,
            credential_id="green",
            client_secret="g" * 48,
        )


@pytest.mark.parametrize(
    ("scope", "credential_id"),
    [
        ("scope/escape", "credential"),
        ("scope", "../credential"),
        ("scope", ""),
    ],
)
def test_rejects_invalid_reference_components(scope: str, credential_id: str) -> None:
    with pytest.raises(ValueError):
        secret_reference(scope=scope, credential_id=credential_id)


def test_reference_key_is_bound_to_credential_id() -> None:
    assert credential_key("credential.123") == "oauth-client-secret-credential.123"


def test_provision_rejects_casefold_equivalent_runtime_and_proxy_identities() -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        provision_agent_proxy_secret(
            SimpleNamespace(secrets=_Secrets()),
            app_name=APP_NAME,
            scope=SCOPE,
            runtime_application_id="Runtime-Client",
            proxy_application_id="runtime-client",
            credential_id="green",
            client_secret="g" * 48,
        )


def test_secret_retirement_deletes_only_explicit_signed_blue_versions() -> None:
    secrets = _Secrets()
    _owned_scope(
        secrets,
        credential_ids=("blue", "green", "older"),
    )

    retire_signed_blue_agent_proxy_secrets(
        SimpleNamespace(secrets=secrets),
        app_name=APP_NAME,
        scope=SCOPE,
        runtime_application_id=RUNTIME_ID,
        proxy_application_id=PROXY_ID,
        retained_credential_id="green",
        retired_credential_ids=("blue", "older"),
    )

    assert set(secrets.keys[SCOPE]) == {
        MARKER_KEY,
        credential_key("green"),
    }
    assert secrets.deleted == [
        (SCOPE, credential_key("blue")),
        (SCOPE, credential_key("older")),
    ]


def test_secret_retirement_rejects_untracked_version_without_deleting() -> None:
    secrets = _Secrets()
    _owned_scope(
        secrets,
        credential_ids=("blue", "green", "concurrent"),
    )

    with pytest.raises(RuntimeError, match="untracked credential version"):
        retire_signed_blue_agent_proxy_secrets(
            SimpleNamespace(secrets=secrets),
            app_name=APP_NAME,
            scope=SCOPE,
            runtime_application_id=RUNTIME_ID,
            proxy_application_id=PROXY_ID,
            retained_credential_id="green",
            retired_credential_ids=("blue",),
        )

    assert secrets.deleted == []


def test_secret_retirement_rejects_unreviewed_key_without_deleting() -> None:
    secrets = _Secrets()
    _owned_scope(secrets, credential_ids=("green",))
    secrets.keys[SCOPE]["unreviewed-key"] = "value"

    with pytest.raises(RuntimeError, match="unreviewed key"):
        retire_signed_blue_agent_proxy_secrets(
            SimpleNamespace(secrets=secrets),
            app_name=APP_NAME,
            scope=SCOPE,
            runtime_application_id=RUNTIME_ID,
            proxy_application_id=PROXY_ID,
            retained_credential_id="green",
            retired_credential_ids=("blue",),
        )

    assert secrets.deleted == []


class _CredentialProxy:
    def __init__(self, ids: set[str]) -> None:
        self.ids = ids
        self.deleted: list[tuple[str, str]] = []

    def list(self, service_principal_id: str):
        assert service_principal_id == "proxy-scim"
        return iter(SimpleNamespace(id=value) for value in sorted(self.ids))

    def delete(self, service_principal_id: str, credential_id: str) -> None:
        self.deleted.append((service_principal_id, credential_id))
        self.ids.remove(credential_id)


def _credential_workspace(credentials: _CredentialProxy) -> object:
    return SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter([SimpleNamespace(id="proxy-scim", application_id=PROXY_ID)])
        ),
        service_principal_secrets_proxy=credentials,
    )


def test_oauth_retirement_deletes_only_signed_blue_credentials() -> None:
    credentials = _CredentialProxy({"blue", "green", "older"})

    retire_signed_blue_agent_proxy_credentials(
        _credential_workspace(credentials),
        proxy_application_id=PROXY_ID,
        retained_credential_id="green",
        retired_credential_ids=("blue", "older"),
    )

    assert credentials.ids == {"green"}
    assert credentials.deleted == [
        ("proxy-scim", "blue"),
        ("proxy-scim", "older"),
    ]


def test_oauth_retirement_rejects_untracked_credential_without_deleting() -> None:
    credentials = _CredentialProxy({"green", "concurrent"})

    with pytest.raises(RuntimeError, match="untracked OAuth credential"):
        retire_signed_blue_agent_proxy_credentials(
            _credential_workspace(credentials),
            proxy_application_id=PROXY_ID,
            retained_credential_id="green",
            retired_credential_ids=("green",),
        )

    assert credentials.ids == {"green", "concurrent"}
    assert credentials.deleted == []


def test_oauth_retirement_never_deletes_retained_credential() -> None:
    credentials = _CredentialProxy({"green"})

    retire_signed_blue_agent_proxy_credentials(
        _credential_workspace(credentials),
        proxy_application_id=PROXY_ID,
        retained_credential_id="green",
        retired_credential_ids=("green",),
    )

    assert credentials.ids == {"green"}
    assert credentials.deleted == []


def test_oauth_retirement_rejects_missing_retained_credential() -> None:
    credentials = _CredentialProxy({"blue"})

    with pytest.raises(RuntimeError, match="credential inventory is invalid"):
        retire_signed_blue_agent_proxy_credentials(
            _credential_workspace(credentials),
            proxy_application_id=PROXY_ID,
            retained_credential_id="green",
            retired_credential_ids=("blue",),
        )

    assert credentials.deleted == []


def test_interrupted_combined_retirement_is_idempotent_before_journal_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = _Secrets()
    _owned_scope(secrets, credential_ids=("blue", "green"))
    credentials = _CredentialProxy({"blue", "green"})
    workspace = _credential_workspace(credentials)
    workspace.secrets = secrets
    completion_calls: list[str] = []

    from tools.databricks import app_deployment_rollback as rollback

    monkeypatch.setattr(
        rollback,
        "assert_proxy_credential_retirement",
        lambda *_args, **_kwargs: {},
    )

    def interrupt_before_completion(*_args: object, **_kwargs: object) -> None:
        completion_calls.append("interrupted")
        raise RuntimeError("simulated process loss before journal completion")

    monkeypatch.setattr(
        rollback,
        "complete_proxy_credential_retirement",
        interrupt_before_completion,
    )

    with pytest.raises(RuntimeError, match="simulated process loss"):
        cleanup_signed_blue_agent_proxy(
            workspace,
            app_name=APP_NAME,
            scope=SCOPE,
            rollback_scope="rollback-scope",
            runtime_application_id=RUNTIME_ID,
            proxy_application_id=PROXY_ID,
            retained_credential_id="green",
            retired_credential_ids=("blue",),
        )

    assert credentials.ids == {"green"}
    assert set(secrets.keys[SCOPE]) == {MARKER_KEY, credential_key("green")}

    monkeypatch.setattr(
        rollback,
        "complete_proxy_credential_retirement",
        lambda *_args, **_kwargs: completion_calls.append("completed"),
    )
    cleanup_signed_blue_agent_proxy(
        workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        rollback_scope="rollback-scope",
        runtime_application_id=RUNTIME_ID,
        proxy_application_id=PROXY_ID,
        retained_credential_id="green",
        retired_credential_ids=("blue",),
    )

    assert completion_calls == ["interrupted", "completed"]
    assert credentials.ids == {"green"}
    assert set(secrets.keys[SCOPE]) == {MARKER_KEY, credential_key("green")}


@pytest.mark.parametrize("retired_ids", [("concurrent",), ()])
def test_combined_retirement_rejects_unsigned_ids_before_deletion(
    monkeypatch: pytest.MonkeyPatch,
    retired_ids: tuple[str, ...],
) -> None:
    secrets = _Secrets()
    _owned_scope(secrets, credential_ids=("green", "concurrent"))
    credentials = _CredentialProxy({"green", "concurrent"})
    workspace = _credential_workspace(credentials)
    workspace.secrets = secrets

    from tools.databricks import app_deployment_rollback as rollback

    monkeypatch.setattr(
        rollback,
        "assert_proxy_credential_retirement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("does not match the signed retirement journal")
        ),
    )

    with pytest.raises(RuntimeError, match="signed retirement journal"):
        cleanup_signed_blue_agent_proxy(
            workspace,
            app_name=APP_NAME,
            scope=SCOPE,
            rollback_scope="rollback-scope",
            runtime_application_id=RUNTIME_ID,
            proxy_application_id=PROXY_ID,
            retained_credential_id="green",
            retired_credential_ids=retired_ids,
        )

    assert credentials.deleted == []
    assert secrets.deleted == []
