"""Contracts for distinct operator/admin/verifier M2M provisioning."""

from __future__ import annotations

import importlib.util
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from databricks.sdk.errors import ResourceDoesNotExist
from databricks.sdk.service.database import (
    DatabaseInstanceRole,
    DatabaseInstanceRoleIdentityType,
)
from databricks.sdk.service.serving import (
    ServingEndpointAccessControlResponse,
    ServingEndpointPermission,
    ServingEndpointPermissionLevel,
    ServingEndpointPermissions,
)
from databricks.sdk.service.sql import (
    WarehouseAccessControlResponse,
    WarehousePermission,
    WarehousePermissionLevel,
    WarehousePermissions,
)

from tools.databricks import oauth_credential_creation
from tools.databricks.agent_proxy_credential_bundle import (
    canonical_agent_proxy_credential_bundle,
    parse_agent_proxy_credential_bundle,
)
from tools.databricks.agent_proxy_credential_bundle import (
    main as agent_proxy_bundle_main,
)
from tools.databricks.serving_query_group_access import (
    managed_query_group_external_id,
    managed_query_group_name,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_PMO_PATH = REPO_ROOT / "tools" / "databricks" / "provision_m2m_oauth.py"
_MODNAME = "mip_provision_m2m_oauth"
_spec = importlib.util.spec_from_file_location(_MODNAME, _PMO_PATH)
assert _spec is not None and _spec.loader is not None
pmo = importlib.util.module_from_spec(_spec)
sys.modules[_MODNAME] = pmo
_spec.loader.exec_module(pmo)  # type: ignore[union-attr]

_MINT_PATH = REPO_ROOT / "tools" / "oauth_m2m_mint.py"
_MINT_MODNAME = "mip_oauth_m2m_mint"
_mint_spec = importlib.util.spec_from_file_location(_MINT_MODNAME, _MINT_PATH)
assert _mint_spec is not None and _mint_spec.loader is not None
mint = importlib.util.module_from_spec(_mint_spec)
sys.modules[_MINT_MODNAME] = mint
_mint_spec.loader.exec_module(mint)  # type: ignore[union-attr]

_CANONICAL_GH_REPO = pmo.CANONICAL_GH_REPO


class _CredentialIntent:
    def __init__(
        self,
        *,
        session: _CredentialSession,
        before_ids: frozenset[str],
    ) -> None:
        self.session = session
        self.before_ids = before_ids
        self.principal_id = session.principal_id
        self.fence = session.fence
        self.sink_path = ""
        self.observed_credential_id = ""
        self.retirement_mode = session.retirement_mode

    def __call__(self) -> None:
        self.session()

    def quarantine(self, error: BaseException) -> None:
        recorder = getattr(self.fence, "quarantine", None)
        if callable(recorder):
            recorder(error)

    def observe(
        self,
        *,
        credential_id: str,
        observed_ids: frozenset[str],
    ) -> None:
        assert observed_ids == self.before_ids | {credential_id}
        self.observed_credential_id = credential_id

    def arm_sink(self, **_kwargs: object) -> None:
        self.sink_path = "test-sink-attempt"

    def acknowledge_delivery(
        self,
        *,
        acknowledged_ids: frozenset[str],
    ) -> None:
        assert acknowledged_ids == self.before_ids | {self.observed_credential_id}

    def resolve(self, **_kwargs: object) -> None:
        self.session.released = True


class _CredentialSession:
    def __init__(
        self,
        fence: object,
        *,
        principal_id: str,
        retirement_mode: str,
    ) -> None:
        self.fence = fence
        self.principal_id = principal_id
        self.retirement_mode = retirement_mode
        self.released = False

    def __call__(self) -> None:
        if not self.released:
            self.fence()  # type: ignore[operator]

    def quarantine(self, error: BaseException) -> None:
        recorder = getattr(self.fence, "quarantine", None)
        if callable(recorder):
            recorder(error)

    def persist_intent(
        self,
        *,
        before_ids: frozenset[str],
    ) -> _CredentialIntent:
        return _CredentialIntent(session=self, before_ids=before_ids)

    def abort_before_intent(self) -> None:
        self.released = True


@pytest.fixture(autouse=True)
def _clear_role_client_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pmo._credential_mutation,
        "credential_source_git_sha",
        lambda _repo_root: "a" * 40,
    )
    monkeypatch.setattr(
        oauth_credential_creation,
        "_STABILITY_INTERVAL_SECONDS",
        0,
    )
    monkeypatch.setattr(
        oauth_credential_creation,
        "begin_credential_mutation_session",
        lambda fence, *, label, principal_id, context: _CredentialSession(
            fence,
            principal_id=principal_id,
            retirement_mode=context.retirement_mode,
        ),
    )
    monkeypatch.setattr(
        pmo,
        "_confirm_gh_secrets",
        lambda _repo, _names: None,
    )
    for defaults in pmo.IDENTITY_DEFAULTS.values():
        monkeypatch.delenv(defaults.client_id_secret_name, raising=False)


def _sp(
    display_name: str = "mip-nightly-ci-sp",
    *,
    sp_id: str = "1234",
    application_id: str = "app-id-abc",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=sp_id,
        application_id=application_id,
        display_name=display_name,
    )


def _make_client(
    *,
    existing_sp: SimpleNamespace | None = None,
    create_returns: SimpleNamespace | None = None,
    groups: list[SimpleNamespace] | None = None,
    lakebase_roles: list[SimpleNamespace] | None = None,
    mint_secret_value: str = "dose_fake_secret_value",
    resource_access: bool = True,
    managed_serving_access: bool = False,
) -> MagicMock:
    client = MagicMock(name="WorkspaceClient")
    state = {"deleted": False}

    def list_service_principals(**_kwargs: object):
        if existing_sp is not None:
            return iter([existing_sp])
        if create_returns is not None and client.service_principals.create.called and not state[
            "deleted"
        ]:
            return iter([create_returns])
        return iter([])

    client.service_principals.list.side_effect = list_service_principals
    client.service_principals.create.return_value = create_returns
    client.service_principals.get.return_value = existing_sp or create_returns

    def delete_service_principal(_sp_id: str) -> None:
        state["deleted"] = True
        client.service_principals.get.return_value = None
        client.service_principals.get.side_effect = ResourceDoesNotExist("missing")

    client.service_principals.delete.side_effect = delete_service_principal
    client.service_principal_secrets_proxy.create.return_value = SimpleNamespace(
        id="secret-id-xyz",
        secret=mint_secret_value,
    )
    credentials: dict[str, object] = {}

    def create_credential(**_kwargs: object) -> object:
        response = client.service_principal_secrets_proxy.create.return_value
        credential_id = str(getattr(response, "id", "") or "")
        if credential_id:
            credentials[credential_id] = response
        return response

    def list_credentials(_sp_id: str) -> object:
        return iter(credentials.values())

    def delete_credential(_sp_id: str, credential_id: str) -> None:
        credentials.pop(credential_id, None)

    client.service_principal_secrets_proxy.create.side_effect = create_credential
    client.service_principal_secrets_proxy.list.side_effect = list_credentials
    client.service_principal_secrets_proxy.delete.side_effect = delete_credential
    group_values = list(groups or [])
    client.database.list_database_instances.return_value = iter(
        [SimpleNamespace(name="mip-app-state")]
    )
    client.database.list_database_instance_roles.side_effect = lambda _name: iter(
        lakebase_roles or []
    )
    client.serving_endpoints.get.return_value = SimpleNamespace(
        id="mip-gateway-endpoint-id",
        name="mip-agent-gateway",
    )
    client.serving_endpoints.list.side_effect = lambda: iter(
        [SimpleNamespace(name="mip-agent-gateway")]
    )
    endpoint_principal = (
        getattr(existing_sp, "application_id", None)
        or getattr(create_returns, "application_id", None)
        or "app-id-abc"
    )
    endpoint_sp_id = str(
        getattr(existing_sp, "id", None) or getattr(create_returns, "id", None) or "1234"
    )
    endpoint_group_name = managed_query_group_name(
        endpoint_id="mip-gateway-endpoint-id",
        application_id=endpoint_principal,
    )
    if resource_access and managed_serving_access:
        group_values.append(
            SimpleNamespace(
                id="mip-gateway-query-group-id",
                display_name=endpoint_group_name,
                external_id=managed_query_group_external_id(
                    endpoint_id="mip-gateway-endpoint-id",
                    application_id=endpoint_principal,
                ),
                members=[SimpleNamespace(value=endpoint_sp_id)],
            )
        )
    groups_by_id = {str(group.id): group for group in group_values if getattr(group, "id", None)}

    def list_groups(**kwargs: object):
        filter_value = str(kwargs.get("filter") or "")
        if not filter_value:
            return iter(group_values)
        expected_name = filter_value.removeprefix("displayName eq '").removesuffix("'")
        return iter(
            group for group in group_values if group.display_name == expected_name
        )

    def create_group(*, display_name: str, external_id: str | None = None) -> object:
        group = SimpleNamespace(
            id=f"created-group-{len(group_values) + 1}",
            display_name=display_name,
            external_id=external_id,
            members=[],
        )
        group_values.append(group)
        groups_by_id[group.id] = group
        return group

    def patch_group(*, id: str, operations: list[object], **_kwargs: object) -> None:
        group = groups_by_id[id]
        operation = operations[0]
        operation_value = getattr(operation, "value", None)
        operation_path = getattr(operation, "path", None)
        op = str(getattr(getattr(operation, "op", None), "value", getattr(operation, "op", "")))
        if op == "add":
            for item in operation_value["members"]:
                group.members.append(SimpleNamespace(value=item["value"]))
        elif op == "remove":
            member_id = str(operation_path).split('"')[1]
            group.members = [
                member for member in group.members if member.value != member_id
            ]
        else:
            raise AssertionError(f"unexpected group patch {op!r}")

    client.groups.list.side_effect = list_groups
    client.groups.get.side_effect = lambda group_id: groups_by_id[str(group_id)]
    if managed_serving_access:
        client.groups.create.side_effect = create_group
        client.groups.patch.side_effect = patch_group
    endpoint_acl = (
        [
            ServingEndpointAccessControlResponse(
                group_name=endpoint_group_name,
                all_permissions=[
                    ServingEndpointPermission(
                        inherited=False,
                        permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
                    )
                ],
            )
        ]
        if resource_access and managed_serving_access
        else []
    )
    client.serving_endpoints.get_permissions.side_effect = lambda _endpoint_id: (
        ServingEndpointPermissions(access_control_list=list(endpoint_acl))
    )

    def update_endpoint_permissions(
        _endpoint_id: str,
        *,
        access_control_list: list[object],
    ) -> None:
        for request in access_control_list:
            group_name = getattr(request, "group_name", None)
            principal_name = getattr(request, "service_principal_name", None)
            endpoint_acl[:] = [
                entry
                for entry in endpoint_acl
                if (
                    getattr(entry, "group_name", None) != group_name
                    if group_name
                    else getattr(entry, "service_principal_name", None) != principal_name
                )
            ]
            endpoint_acl.append(
                ServingEndpointAccessControlResponse(
                    group_name=group_name,
                    service_principal_name=principal_name,
                    all_permissions=[
                        ServingEndpointPermission(
                            inherited=False,
                            permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
                        )
                    ],
                )
            )

    client.serving_endpoints.update_permissions.side_effect = update_endpoint_permissions
    client.apps.list.return_value = iter([SimpleNamespace(name="mip-app")])
    client.apps.get.return_value = SimpleNamespace(
        url="https://mip-app-live.aws.databricksapps.com"
    )
    client.apps.get_permissions.return_value = SimpleNamespace(access_control_list=[])
    client.warehouses.list.return_value = iter([SimpleNamespace(id="warehouse-123")])
    warehouse_principal = endpoint_principal
    client.warehouses.get_permissions.return_value = WarehousePermissions(
        access_control_list=(
            [
                WarehouseAccessControlResponse(
                    service_principal_name=warehouse_principal,
                    all_permissions=[
                        WarehousePermission(
                            inherited=False,
                            permission_level=WarehousePermissionLevel.CAN_USE,
                        )
                    ],
                )
            ]
            if resource_access
            else []
        )
    )
    return client


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (
            "git@github.com:acme-bank/mortgage.intelligence-platform.git",
            "acme-bank/mortgage.intelligence-platform",
        ),
        (
            "https://github.com/acme-bank/mortgage.intelligence-platform.git",
            "acme-bank/mortgage.intelligence-platform",
        ),
        (
            "https://github.com/acme-bank/mortgage.intelligence-platform",
            "acme-bank/mortgage.intelligence-platform",
        ),
    ],
)
def test_infer_gh_repo_preserves_dotted_repository_names(
    origin: str,
    expected: str,
) -> None:
    with patch.object(
        pmo.subprocess,
        "run",
        return_value=SimpleNamespace(stdout=origin),
    ):
        assert pmo._infer_gh_repo() == expected


@pytest.mark.parametrize(
    "origin",
    [
        "https://evilgithub.com/acme-bank/mortgage.intelligence-platform.git",
        "https://notgithub.com/github.com/acme-bank/mortgage.intelligence-platform.git",
        "git@github.com.evil:acme-bank/mortgage.intelligence-platform.git",
        "https://github.com/acme-bank/mortgage.intelligence-platform/tree/main",
    ],
)
def test_infer_gh_repo_rejects_non_github_or_non_repository_origins(origin: str) -> None:
    with patch.object(
        pmo.subprocess,
        "run",
        return_value=SimpleNamespace(stdout=origin),
    ):
        assert pmo._infer_gh_repo() is None


def test_reserved_service_principal_name_must_resolve_uniquely() -> None:
    client = MagicMock()
    client.service_principals.list.return_value = iter(
        [
            _sp("mip-nightly-ci-sp", sp_id="attacker-scim", application_id="attacker"),
            _sp("mip-nightly-ci-sp", sp_id="reviewed-scim", application_id="reviewed"),
        ]
    )

    with pytest.raises(SystemExit, match="Multiple service principals"):
        pmo._find_existing_sp(client, "mip-nightly-ci-sp")


@pytest.mark.parametrize(
    "identity_role",
    ("normal", "operator2", "admin", "release_probe", "verifier", "agent_proxy"),
)
def test_credential_lease_writer_is_always_reserved_agent_runtime(
    identity_role: str,
) -> None:
    client = MagicMock()
    runtime = _sp(
        pmo.IDENTITY_DEFAULTS["agent_runtime"].sp_name,
        application_id="runtime-writer-application-id",
    )
    client.service_principals.list.return_value = iter([runtime])

    writer = pmo._credential_mutation.credential_lease_writer_application_id(
        client,
        target=_sp(application_id="target-application-id"),
        identity_role=identity_role,
        find_existing_sp=pmo._find_existing_sp,
    )

    assert writer == "runtime-writer-application-id"


@pytest.mark.parametrize(
    ("identity_role", "expected"),
    (
        ("normal", "immediate"),
        ("operator2", "immediate"),
        ("admin", "immediate"),
        ("release_probe", "immediate"),
        ("verifier", "immediate"),
        ("agent_runtime", "immediate"),
        ("agent_proxy", "signed_app_cutover"),
    ),
)
def test_identity_role_owns_exact_credential_retirement_mode(
    identity_role: pmo.IdentityRole,
    expected: str,
) -> None:
    assert (
        pmo._credential_mutation.credential_retirement_mode(identity_role)
        == expected
    )


@pytest.mark.parametrize(
    ("identity_role", "expected_live"),
    (
        ("normal", {"green"}),
        ("agent_proxy", {"signed-blue", "green"}),
    ),
)
def test_delivered_rotation_applies_role_owned_retirement_policy(
    identity_role: pmo.IdentityRole,
    expected_live: set[str],
) -> None:
    live = {"signed-blue"}
    deleted: list[str] = []
    writes: list[str] = []

    def create(**_kwargs: object) -> object:
        live.add("green")
        return SimpleNamespace(id="green", secret="one-shot")

    def delete(_sp_id: str, credential_id: str) -> None:
        live.remove(credential_id)
        deleted.append(credential_id)

    credentials = SimpleNamespace(
        list=lambda _sp_id: (
            SimpleNamespace(id=value) for value in sorted(live)
        ),
        create=create,
        delete=delete,
    )
    client = SimpleNamespace(service_principal_secrets_proxy=credentials)

    result = pmo._credential_mutation.mint_and_deliver_oauth_credential(
        client=client,
        sp_id="proxy-scim-id",
        client_id="proxy-application-id",
        identity_role=identity_role,
        app_name="mip-app",
        writer_application_id="runtime-writer",
        source_git_sha="a" * 40,
        boundary_factory=lambda *_args, **_kwargs: nullcontext(
            lambda: None
        ),
        secret_writer=lambda _repo, name, _value: writes.append(name),
        sink_acknowledger=lambda *_args: None,
        secret_invalidator=lambda *_args: pytest.fail(
            "successful sink must not be invalidated"
        ),
        diagnostic=lambda _message: None,
        error_factory=lambda error, **_kwargs: error,
        gh_repo=_CANONICAL_GH_REPO,
        client_id_secret_name=(
            "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"
            if identity_role == "agent_proxy"
            else "DATABRICKS_CLIENT_ID"
        ),
        client_secret_secret_name=(
            "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"
            if identity_role == "agent_proxy"
            else "DATABRICKS_CLIENT_SECRET"
        ),
        credential_id_secret_name=None,
        app_url_secret_name=None,
        app_url=None,
        atomic_credential_bundle=identity_role == "agent_proxy",
    )

    assert result == "green"
    assert live == expected_live
    assert deleted == (
        [] if identity_role == "agent_proxy" else ["signed-blue"]
    )
    assert writes


def test_agent_runtime_credential_lease_uses_its_own_reserved_identity() -> None:
    client = MagicMock()
    runtime = _sp(
        pmo.IDENTITY_DEFAULTS["agent_runtime"].sp_name,
        application_id="runtime-writer-application-id",
    )

    writer = pmo._credential_mutation.credential_lease_writer_application_id(
        client,
        target=runtime,
        identity_role="agent_runtime",
        find_existing_sp=pmo._find_existing_sp,
    )

    assert writer == "runtime-writer-application-id"
    client.service_principals.list.assert_not_called()


def test_credential_mint_fails_before_boundary_when_runtime_writer_is_absent() -> None:
    client = MagicMock()
    client.service_principals.list.return_value = iter([])

    with pytest.raises(SystemExit, match="Bootstrap the agent_runtime identity first"):
        pmo._credential_mutation.credential_lease_writer_application_id(
            client,
            target=_sp(application_id="target-application-id"),
            identity_role="normal",
            find_existing_sp=pmo._find_existing_sp,
        )


def test_app_name_resolution_prefers_reviewed_deployment_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_APP_NAME", "mip-app-pr105-staging")
    monkeypatch.setenv("BUNDLE_VAR_app_name", "wrong-lower-precedence")

    assert pmo._load_app_name_from_bundle() == "mip-app-pr105-staging"


def test_app_name_resolution_accepts_bundle_variable_without_mip_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIP_APP_NAME", raising=False)
    monkeypatch.setenv("BUNDLE_VAR_app_name", "mip-app-customer")

    assert pmo._load_app_name_from_bundle() == "mip-app-customer"


def _provision(client: MagicMock, **overrides: object):
    kwargs: dict[str, object] = {
        "sp_name": "mip-nightly-ci-sp",
        "expected_application_id": None,
        "app_name": "mip-app",
        "grant_can_use": True,
        "group_name": None,
        "create_group": False,
        "lakebase_instance": None,
        "gateway_endpoint": None,
        "warehouse_id": None,
        "gh_repo": _CANONICAL_GH_REPO,
        "set_gh_secrets": True,
        "mint_secret": True,
        "rotate": False,
        "app_url": "https://mip-app-test.aws.databricksapps.com",
        "client_id_secret_name": "DATABRICKS_CLIENT_ID",
        "client_secret_secret_name": "DATABRICKS_CLIENT_SECRET",
        "app_url_secret_name": "MIP_APP_URL",
        "identity_role": "normal",
        "client_factory": lambda: client,
        "credential_boundary_factory": (
            lambda *_args, **_kwargs: nullcontext(lambda: None)
        ),
        "credential_writer_application_id": "deployment-writer-client",
        "gateway_mutation_assertion": lambda: None,
    }
    kwargs.update(overrides)
    role_defaults = pmo.IDENTITY_DEFAULTS[kwargs["identity_role"]]
    if "sp_name" not in overrides:
        kwargs["sp_name"] = role_defaults.sp_name
    if "client_id_secret_name" not in overrides:
        kwargs["client_id_secret_name"] = role_defaults.client_id_secret_name
    if "client_secret_secret_name" not in overrides:
        kwargs["client_secret_secret_name"] = role_defaults.client_secret_secret_name
    if "app_url_secret_name" not in overrides:
        kwargs["app_url_secret_name"] = role_defaults.app_url_secret_name
    if "credential_id_secret_name" not in overrides:
        kwargs["credential_id_secret_name"] = role_defaults.credential_id_secret_name
    with patch.object(pmo, "_gh_available", return_value=True):
        return pmo.provision(**kwargs)


@pytest.mark.parametrize(
    "source_error",
    (
        "standalone credential mutation requires a clean tracked worktree",
        "standalone credential mutation requires a clean untracked worktree",
        "configured credential mutation source Git SHA does not match HEAD",
    ),
)
def test_source_attestation_fails_before_any_workspace_client_call(
    monkeypatch: pytest.MonkeyPatch,
    source_error: str,
) -> None:
    client = MagicMock()
    monkeypatch.setattr(
        pmo._credential_mutation,
        "credential_source_git_sha",
        lambda _repo_root: (_ for _ in ()).throw(
            RuntimeError(source_error)
        ),
    )

    with pytest.raises(RuntimeError, match=source_error):
        _provision(client)

    assert client.mock_calls == []


def test_help_exposes_role_group_and_secret_name_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        pmo.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for option in (
        "--identity-role",
        "--pre-app-bootstrap",
        "--create-group",
        "--client-id-secret-name",
        "--client-secret-secret-name",
        "--warehouse-id",
        "--no-mint-secret",
    ):
        assert option in out


def test_role_contract_helpers_remain_reexported_for_callers() -> None:
    assert pmo._validate_app_access_contract is pmo.validate_app_access_contract
    assert pmo._validate_provisioning_contract is pmo.validate_provisioning_contract


@pytest.mark.parametrize(
    ("role", "sp_name", "client_id_name", "client_secret_name"),
    [
        ("normal", "mip-nightly-ci-sp", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"),
        (
            "operator2",
            "mip-nightly-operator2-ci-sp",
            "DATABRICKS_OPERATOR2_CLIENT_ID",
            "DATABRICKS_OPERATOR2_CLIENT_SECRET",
        ),
        (
            "admin",
            "mip-nightly-admin-ci-sp",
            "DATABRICKS_ADMIN_CLIENT_ID",
            "DATABRICKS_ADMIN_CLIENT_SECRET",
        ),
        (
            "release_probe",
            "mip-release-probe-ci-sp",
            "DATABRICKS_RELEASE_PROBE_CLIENT_ID",
            "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET",
        ),
        (
            "verifier",
            "mip-ai-gateway-verifier-ci-sp",
            "DATABRICKS_VERIFIER_CLIENT_ID",
            "DATABRICKS_VERIFIER_CLIENT_SECRET",
        ),
        (
            "agent_runtime",
            "mip-agent-runtime-ci-sp",
            "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
            "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
        ),
        (
            "agent_proxy",
            "mip-agent-supervisor-proxy-ci-sp",
            "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
            "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
        ),
    ],
)
def test_role_defaults_are_distinct(
    role: str,
    sp_name: str,
    client_id_name: str,
    client_secret_name: str,
) -> None:
    defaults = pmo.IDENTITY_DEFAULTS[role]
    assert defaults.sp_name == sp_name
    assert defaults.client_id_secret_name == client_id_name
    assert defaults.client_secret_secret_name == client_secret_name


@pytest.mark.parametrize(
    "role",
    [
        "normal",
        "operator2",
        "admin",
        "release_probe",
        "verifier",
        "agent_runtime",
        "agent_proxy",
    ],
)
def test_pre_app_bootstrap_mints_only_role_owned_credentials_without_resource_calls(
    role: str,
) -> None:
    defaults = pmo.IDENTITY_DEFAULTS[role]
    new_sp = _sp(
        defaults.sp_name,
        sp_id=f"{role}-sp-id",
        application_id=f"{role}-application-id",
    )
    client = _make_client(create_returns=new_sp, resource_access=False)
    if role in {"admin", "release_probe"}:
        client.groups.create.return_value = SimpleNamespace(
            id="mip-admin-group-id",
            display_name=pmo.DEFAULT_ADMIN_GROUP,
            members=[],
        )

    with patch.object(pmo, "_set_gh_secret") as set_secret:
        result = _provision(
            client,
            identity_role=role,
            app_name="",
            grant_can_use=False,
            group_name=defaults.group_name,
            create_group=role in {"admin", "release_probe"},
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=defaults.app_url_secret_name,
            pre_app_bootstrap=True,
        )

    assert result.secret_minted is True
    assert result.secret_written_to_gh is True
    expected_writes = []
    if role != "agent_proxy":
        expected_writes.append(
            call(
                _CANONICAL_GH_REPO,
                defaults.client_id_secret_name,
                f"{role}-application-id",
            )
        )
    if defaults.credential_id_secret_name and role != "agent_proxy":
        expected_writes.append(
            call(
                _CANONICAL_GH_REPO,
                defaults.credential_id_secret_name,
                "secret-id-xyz",
            )
        )
    final_value = (
        canonical_agent_proxy_credential_bundle(
            client_id=f"{role}-application-id",
            credential_id="secret-id-xyz",
            client_secret="dose_fake_secret_value",
        )
        if role == "agent_proxy"
        else "dose_fake_secret_value"
    )
    expected_writes.append(
        call(_CANONICAL_GH_REPO, defaults.client_secret_secret_name, final_value)
    )
    assert set_secret.call_args_list == expected_writes
    client.apps.list.assert_called()
    client.database.list_database_instances.assert_called()
    client.serving_endpoints.list.assert_called()
    client.warehouses.list.assert_called()


@pytest.mark.parametrize("rotate", [False, True])
def test_pre_app_bootstrap_refuses_every_existing_identity(rotate: bool) -> None:
    existing = _sp()
    client = _make_client(existing_sp=existing)

    with pytest.raises(SystemExit, match="creation-only"):
        _provision(
            client,
            app_name="",
            grant_can_use=False,
            pre_app_bootstrap=True,
            rotate=rotate,
        )

    client.service_principal_secrets_proxy.list.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_not_called()
    assert client.apps.mock_calls == []
    assert client.database.mock_calls == []


def test_pre_app_bootstrap_refuses_existing_credentialless_identity() -> None:
    existing = _sp()
    client = _make_client(existing_sp=existing, resource_access=False)
    with (
        patch.object(pmo, "_set_gh_secret") as set_secret,
        pytest.raises(SystemExit, match="creation-only"),
    ):
        _provision(
            client,
            app_name="",
            grant_can_use=False,
            pre_app_bootstrap=True,
        )

    client.service_principal_secrets_proxy.list.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_not_called()
    set_secret.assert_not_called()


@pytest.mark.parametrize("stale_access", ["app", "lakebase", "warehouse", "serving"])
def test_pre_app_bootstrap_refuses_existing_identity_before_stale_resource_audit_or_mint(
    stale_access: str,
) -> None:
    existing = _sp()
    client = _make_client(
        existing_sp=existing,
        lakebase_roles=(
            [SimpleNamespace(name=existing.application_id)]
            if stale_access == "lakebase"
            else []
        ),
        resource_access=False,
    )
    client.service_principal_secrets_proxy.list.return_value = iter([])
    if stale_access == "app":
        client.apps.get_permissions.return_value = SimpleNamespace(
            access_control_list=[
                SimpleNamespace(
                    service_principal_name=existing.application_id,
                    display_name=existing.display_name,
                    group_name="",
                )
            ]
        )
    elif stale_access == "warehouse":
        client.warehouses.get_permissions.return_value = WarehousePermissions(
            access_control_list=[
                WarehouseAccessControlResponse(
                    service_principal_name=existing.application_id,
                    all_permissions=[
                        WarehousePermission(
                            inherited=False,
                            permission_level=WarehousePermissionLevel.CAN_USE,
                        )
                    ],
                )
            ]
        )
    elif stale_access == "serving":
        client.serving_endpoints.get_permissions.return_value = ServingEndpointPermissions(
            access_control_list=[
                ServingEndpointAccessControlResponse(
                    service_principal_name=existing.application_id,
                    all_permissions=[
                        ServingEndpointPermission(
                            inherited=False,
                            permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
                        )
                    ],
                )
            ]
        )

    with pytest.raises(SystemExit, match="creation-only"):
        _provision(
            client,
            app_name="",
            grant_can_use=False,
            pre_app_bootstrap=True,
        )

    client.service_principal_secrets_proxy.create.assert_not_called()


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("grant_can_use", True),
        ("lakebase_instance", "mip-app-state"),
        ("gateway_endpoint", "gateway"),
        ("warehouse_id", "warehouse"),
        ("revoke_gateway_endpoints", ("old-gateway",)),
        ("preserve_gateway_endpoints", ("signed-blue-gateway",)),
    ],
)
def test_pre_app_bootstrap_rejects_resource_scope_before_sdk_calls(
    option: str,
    value: object,
) -> None:
    client_factory = MagicMock()
    defaults = pmo.IDENTITY_DEFAULTS["normal"]
    kwargs: dict[str, object] = {
        "sp_name": defaults.sp_name,
        "expected_application_id": None,
        "app_name": "",
        "grant_can_use": False,
        "group_name": defaults.group_name,
        "create_group": False,
        "lakebase_instance": None,
        "gateway_endpoint": None,
        "warehouse_id": None,
        "gh_repo": _CANONICAL_GH_REPO,
        "set_gh_secrets": True,
        "mint_secret": True,
        "rotate": False,
        "app_url": "",
        "client_id_secret_name": defaults.client_id_secret_name,
        "client_secret_secret_name": defaults.client_secret_secret_name,
        "app_url_secret_name": defaults.app_url_secret_name,
        "identity_role": "normal",
        "client_factory": client_factory,
        "pre_app_bootstrap": True,
    }
    kwargs[option] = value

    with pytest.raises(SystemExit, match="forbids resource access"):
        pmo.provision(**kwargs)

    client_factory.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [
        ["--pre-app-bootstrap"],
        ["--pre-app-bootstrap", "--set-gh-secrets", "--no-mint-secret"],
        ["--pre-app-bootstrap", "--set-gh-secrets", "--app-name", "mip-app"],
        ["--pre-app-bootstrap", "--set-gh-secrets", "--app-url", "https://app.invalid"],
        ["--pre-app-bootstrap", "--set-gh-secrets", "--grant-can-use"],
        ["--pre-app-bootstrap", "--set-gh-secrets", "--lakebase-instance", "state"],
        [
            "--pre-app-bootstrap",
            "--set-gh-secrets",
            "--client-id-secret-name",
            "DATABRICKS_ADMIN_CLIENT_ID",
        ],
    ],
)
def test_pre_app_bootstrap_cli_rejects_missing_or_unsafe_arguments(
    argv: list[str],
) -> None:
    with (
        patch.object(pmo, "provision") as provision_call,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main([*argv, "--dry-run"])

    assert exc.value.code == 2
    provision_call.assert_not_called()


def test_pre_app_bootstrap_rejects_unreviewed_github_sink_before_sdk_calls() -> None:
    with (
        patch.object(pmo, "_reviewed_gh_repo", return_value=_CANONICAL_GH_REPO),
        patch.object(pmo, "provision") as provision_call,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main(
            [
                "--pre-app-bootstrap",
                "--set-gh-secrets",
                "--gh-repo",
                "attacker/repository",
                "--dry-run",
            ]
        )

    assert exc.value.code == 2
    provision_call.assert_not_called()


def test_pre_app_bootstrap_cli_forwards_credentials_only_contract() -> None:
    with (
        patch.object(pmo, "_reviewed_gh_repo", return_value=_CANONICAL_GH_REPO),
        patch.object(pmo, "provision", return_value=MagicMock()) as provision_call,
        patch.object(pmo, "_print_summary"),
    ):
        rc = pmo.main(
            [
                "--identity-role",
                "normal",
                "--pre-app-bootstrap",
                "--set-gh-secrets",
                "--gh-repo",
                _CANONICAL_GH_REPO,
            ]
        )

    assert rc == 0
    kwargs = provision_call.call_args.kwargs
    assert kwargs["pre_app_bootstrap"] is True
    assert kwargs["app_name"] == ""
    assert kwargs["grant_can_use"] is False
    assert kwargs["lakebase_instance"] is None
    assert kwargs["gateway_endpoint"] is None
    assert kwargs["preserve_gateway_endpoints"] == ()
    assert kwargs["warehouse_id"] is None
    assert kwargs["set_gh_secrets"] is True
    assert kwargs["mint_secret"] is True
    assert kwargs["gh_repo"] == _CANONICAL_GH_REPO


def test_dry_run_does_not_touch_sdk() -> None:
    with patch.object(pmo, "provision") as mock_provision:
        rc = pmo.main(["--identity-role", "admin", "--create-group", "--dry-run"])
    assert rc == 0
    mock_provision.assert_not_called()


@pytest.mark.parametrize("role", ["release_probe", "verifier"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
def test_cli_rejects_isolated_role_app_can_use_before_provision(
    role: str,
    dry_run: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--identity-role", role, "--grant-can-use"]
    if dry_run:
        argv.append("--dry-run")

    with (
        patch.object(pmo, "provision") as mock_provision,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main(argv)

    assert exc.value.code == 2
    assert f"{role} forbids Databricks App CAN_USE" in capsys.readouterr().err
    mock_provision.assert_not_called()


def test_cli_rejects_agent_runtime_app_or_verifier_resource_access(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as app_exc:
        pmo.main(["--identity-role", "agent_runtime", "--grant-can-use", "--dry-run"])
    assert app_exc.value.code == 2
    assert "agent_runtime forbids Databricks App CAN_USE" in capsys.readouterr().err

    for option in ("--lakebase-instance", "--gateway-endpoint", "--warehouse-id"):
        with pytest.raises(SystemExit) as resource_exc:
            pmo.main(
                ["--identity-role", "agent_runtime", option, "forbidden-resource", "--dry-run"]
            )
        assert resource_exc.value.code == 2


def test_agent_runtime_reaudit_rejects_preexisting_lakebase_role() -> None:
    runtime = _sp(
        "mip-agent-runtime-ci-sp",
        application_id="runtime-client",
    )
    client = _make_client(
        existing_sp=runtime,
        lakebase_roles=[SimpleNamespace(name="runtime-client")],
    )
    client.warehouses.get_permissions.return_value = WarehousePermissions(access_control_list=[])

    with pytest.raises(SystemExit, match="forbidden Lakebase role"):
        _provision(
            client,
            sp_name=runtime.display_name,
            expected_application_id="runtime-client",
            identity_role="agent_runtime",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
            client_id_secret_name="DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
            app_url_secret_name=None,
        )


def test_agent_runtime_reaudit_rejects_role_on_second_lakebase_instance() -> None:
    runtime = _sp(
        "mip-agent-runtime-ci-sp",
        application_id="runtime-client",
    )
    client = _make_client(existing_sp=runtime)
    client.database.list_database_instances.return_value = iter(
        [SimpleNamespace(name="mip-app-state"), SimpleNamespace(name="other-state")]
    )
    client.database.list_database_instance_roles.side_effect = lambda name: iter(
        [SimpleNamespace(name="runtime-client")] if name == "other-state" else []
    )
    client.warehouses.get_permissions.return_value = WarehousePermissions(access_control_list=[])

    with pytest.raises(SystemExit, match="on instance 'other-state'"):
        _provision(
            client,
            sp_name=runtime.display_name,
            expected_application_id="runtime-client",
            identity_role="agent_runtime",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    assert client.database.list_database_instance_roles.call_args_list == [
        call("mip-app-state"),
        call("other-state"),
    ]


def test_agent_runtime_reaudit_rejects_effective_warehouse_access() -> None:
    runtime = _sp(
        "mip-agent-runtime-ci-sp",
        application_id="runtime-client",
    )
    client = _make_client(existing_sp=runtime)

    with pytest.raises(RuntimeError, match="effective SQL warehouse access"):
        _provision(
            client,
            sp_name=runtime.display_name,
            expected_application_id="runtime-client",
            identity_role="agent_runtime",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
            client_id_secret_name="DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
            app_url_secret_name=None,
        )


@pytest.mark.parametrize(
    ("lakebase_roles", "message"),
    [
        ([SimpleNamespace(name="proxy-client")], "forbidden Lakebase role"),
        ([], "effective SQL warehouse access"),
    ],
    ids=["lakebase", "warehouse"],
)
def test_agent_proxy_reaudit_rejects_infrastructure_access(
    lakebase_roles: list[SimpleNamespace],
    message: str,
) -> None:
    proxy = _sp(
        "mip-agent-supervisor-proxy-ci-sp",
        application_id="proxy-client",
    )
    client = _make_client(existing_sp=proxy, lakebase_roles=lakebase_roles)
    if lakebase_roles:
        client.warehouses.get_permissions.return_value = WarehousePermissions(
            access_control_list=[]
        )

    with pytest.raises((SystemExit, RuntimeError), match=message):
        _provision(
            client,
            sp_name=proxy.display_name,
            expected_application_id="proxy-client",
            identity_role="agent_proxy",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )


@pytest.mark.parametrize(
    ("role", "role_args"),
    [
        pytest.param("normal", [], id="app-runtime"),
        pytest.param("operator2", [], id="second-operator"),
        pytest.param("admin", ["--create-group"], id="admin"),
    ],
)
def test_cli_allows_app_can_use_for_operator_and_admin_roles(
    role: str,
    role_args: list[str],
) -> None:
    assert pmo.main(["--identity-role", role, "--grant-can-use", "--dry-run", *role_args]) == 0


@pytest.mark.parametrize("option", ["--lakebase-instance", "--gateway-endpoint", "--warehouse-id"])
def test_verifier_grant_options_are_rejected_for_other_roles(option: str) -> None:
    with pytest.raises(SystemExit) as exc:
        pmo.main(["--identity-role", "normal", option, "unexpected-resource", "--dry-run"])
    assert exc.value.code == 2


def test_mint_output_file_is_mode_0600_and_secret_free_on_console(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    output = tmp_path / "bearer"
    mint._write_output("short-lived-secret", github_env_names=None, output_file=output)

    assert output.read_text(encoding="utf-8") == "short-lived-secret\n"
    assert output.stat().st_mode & 0o777 == 0o600
    captured = capsys.readouterr()
    assert "short-lived-secret" not in captured.out + captured.err


def test_mint_can_write_same_token_to_multiple_github_env_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github_env = tmp_path / "github-env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    mint._write_output(
        "short-lived-secret",
        github_env_names=["MIP_BEARER_TOKEN", "MIP_NON_ADMIN_BEARER_TOKEN"],
        output_file=None,
    )

    assert github_env.read_text(encoding="utf-8").splitlines() == [
        "MIP_BEARER_TOKEN=short-lived-secret",
        "MIP_NON_ADMIN_BEARER_TOKEN=short-lived-secret",
    ]
    assert capsys.readouterr().out == "::add-mask::short-lived-secret\n"


def test_mint_masks_output_file_token_in_github_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    output = tmp_path / "bearer"

    mint._write_output("cleanup-secret", github_env_names=None, output_file=output)

    assert output.read_text(encoding="utf-8") == "cleanup-secret\n"
    assert capsys.readouterr().out == "::add-mask::cleanup-secret\n"


def test_normal_happy_path_creates_grants_mints_and_uses_role_owned_secret_names() -> None:
    new_sp = _sp()
    client = _make_client(create_returns=new_sp, resource_access=False)

    with patch.object(pmo, "_set_gh_secret") as set_secret:
        result = _provision(client)

    client.service_principals.create.assert_called_once_with(
        display_name="mip-nightly-ci-sp",
        active=True,
    )
    client.apps.update_permissions.assert_called_once()
    app_acl = client.apps.update_permissions.call_args.kwargs["access_control_list"]
    assert app_acl[0].service_principal_name == "app-id-abc"
    assert getattr(app_acl[0].permission_level, "value", app_acl[0].permission_level) == "CAN_USE"
    client.service_principal_secrets_proxy.create.assert_called_once_with(
        service_principal_id="1234"
    )
    assert set_secret.call_args_list == [
        call(_CANONICAL_GH_REPO, "DATABRICKS_CLIENT_ID", "app-id-abc"),
        call(
            _CANONICAL_GH_REPO,
            "MIP_APP_URL",
            "https://mip-app-test.aws.databricksapps.com",
        ),
        call(_CANONICAL_GH_REPO, "DATABRICKS_CLIENT_SECRET", "dose_fake_secret_value"),
    ]
    assert result.created_sp is True
    assert result.secret_minted is True
    assert result.secret_written_to_gh is True
    assert not hasattr(result, "client_secret")


def test_agent_proxy_partial_secret_sink_failure_revokes_minted_credential() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    new_sp = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(create_returns=new_sp, resource_access=False)
    client.service_principal_secrets_proxy.list.return_value = iter([])

    with (
        patch.object(
            pmo,
            "_set_gh_secret",
            side_effect=RuntimeError("atomic bundle sink failed"),
        ) as set_secret,
        patch.object(pmo, "_invalidate_gh_secrets") as invalidate_secrets,
        pytest.raises(RuntimeError, match="atomic bundle sink failed"),
    ):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    assert set_secret.call_args_list == [
        call(
            _CANONICAL_GH_REPO,
            "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
            canonical_agent_proxy_credential_bundle(
                client_id="proxy-application-id",
                credential_id="secret-id-xyz",
                client_secret="dose_fake_secret_value",
            ),
        ),
    ]
    invalidate_secrets.assert_called_once_with(
        _CANONICAL_GH_REPO,
        frozenset({"DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"}),
    )
    client.service_principal_secrets_proxy.delete.assert_called_once_with(
        "proxy-sp-id",
        "secret-id-xyz",
    )
    assert client.service_principal_secrets_proxy.list.call_args_list == [
        call("proxy-sp-id")
    ] * 15
    client.service_principals.delete.assert_called_once_with("proxy-sp-id")


def test_pre_app_sink_failure_requires_proven_new_principal_cleanup() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    new_sp = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(create_returns=new_sp, resource_access=False)
    client.service_principal_secrets_proxy.list.return_value = iter([])
    client.service_principals.delete.side_effect = RuntimeError("delete denied")

    with (
        patch.object(
            pmo,
            "_set_gh_secret",
            side_effect=RuntimeError("atomic bundle sink failed"),
        ),
        patch.object(pmo, "_invalidate_gh_secrets"),
        pytest.raises(RuntimeError, match="manual security reconciliation"),
    ):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principal_secrets_proxy.delete.assert_called_once_with(
        "proxy-sp-id",
        "secret-id-xyz",
    )
    client.service_principals.delete.assert_called_once_with("proxy-sp-id")


def test_sink_invalidation_failure_restores_provider_but_stays_quarantined() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    new_sp = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(create_returns=new_sp, resource_access=False)
    client.service_principal_secrets_proxy.list.return_value = iter([])

    with (
        patch.object(
            pmo,
            "_set_gh_secret",
            side_effect=RuntimeError("atomic bundle sink failed"),
        ),
        patch.object(
            pmo,
            "_invalidate_gh_secrets",
            side_effect=RuntimeError("sink deletion unproven"),
        ),
        pytest.raises(
            pmo.CredentialMutationQuarantineError,
            match="GitHub sink invalidation is unproven",
        ),
    ):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principal_secrets_proxy.delete.assert_called_once_with(
        "proxy-sp-id",
        "secret-id-xyz",
    )
    client.service_principals.delete.assert_not_called()


def test_pre_app_ambiguous_mint_failure_quarantines_new_service_principal() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    new_sp = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(create_returns=new_sp, resource_access=False)
    client.service_principal_secrets_proxy.create.side_effect = RuntimeError(
        "timeout after provider commit"
    )

    with pytest.raises(
        pmo.CredentialMutationQuarantineError,
        match="no attributable credential",
    ):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principals.delete.assert_not_called()


def test_existing_identity_rotation_commit_then_timeout_revokes_discovered_secret() -> None:
    existing = _sp()
    client = _make_client(existing_sp=existing, resource_access=False)
    live_credentials: set[str] = set()
    deleted: list[tuple[str, str]] = []

    def list_credentials(_sp_id: str) -> object:
        return (
            SimpleNamespace(id=credential_id)
            for credential_id in sorted(live_credentials)
        )

    def commit_then_timeout(**_kwargs: object) -> object:
        live_credentials.add("committed-secret-id")
        raise TimeoutError("response lost after provider commit")

    def delete_credential(sp_id: str, credential_id: str) -> None:
        live_credentials.remove(credential_id)
        deleted.append((sp_id, credential_id))

    client.service_principal_secrets_proxy.list.side_effect = list_credentials
    client.service_principal_secrets_proxy.create.side_effect = commit_then_timeout
    client.service_principal_secrets_proxy.delete.side_effect = delete_credential

    with pytest.raises(SystemExit, match="response lost after provider commit"):
        _provision(client, rotate=True)

    assert live_credentials == set()
    assert deleted == [("1234", "committed-secret-id")]
    client.service_principals.delete.assert_not_called()


def test_pre_app_resource_audit_failure_deletes_new_service_principal() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    new_sp = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(create_returns=new_sp, resource_access=True)

    with pytest.raises(RuntimeError, match="effective SQL warehouse access"):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principal_secrets_proxy.create.assert_not_called()
    client.service_principals.delete.assert_called_once_with("proxy-sp-id")


def test_pre_app_terminal_credential_fence_preserves_delivered_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    created = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(create_returns=created, resource_access=False)
    monkeypatch.setattr(
        pmo._credential_mutation,
        "mint_and_deliver_oauth_credential",
        MagicMock(
            side_effect=pmo.CredentialMutationTerminalFenceError(
                "delivered credential is terminal but lease release is unproven"
            )
        ),
    )

    with pytest.raises(
        pmo.CredentialMutationTerminalFenceError,
        match="lease release is unproven",
    ):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principals.delete.assert_not_called()


def test_pre_app_ambiguous_create_never_deletes_name_only_identity() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    appeared = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(resource_access=False)
    client.service_principals.list.side_effect = [
        iter([]),
        iter([appeared]),
        iter([]),
    ]
    client.service_principals.create.side_effect = RuntimeError(
        "timeout after SCIM create commit"
    )

    with pytest.raises(RuntimeError, match="manual security reconciliation"):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principals.delete.assert_not_called()


def test_pre_app_ambiguous_create_without_visible_identity_requires_reconciliation() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    client = _make_client(resource_access=False)
    client.service_principals.create.side_effect = RuntimeError(
        "timeout after SCIM create request"
    )

    with pytest.raises(RuntimeError, match="manual security reconciliation"):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principals.delete.assert_not_called()


def test_pre_app_post_create_inventory_must_match_immutable_identity() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    created = _sp(
        defaults.sp_name,
        sp_id="created-id",
        application_id="proxy-application-id",
    )
    conflicting = _sp(
        defaults.sp_name,
        sp_id="conflicting-id",
        application_id="conflicting-application-id",
    )
    client = _make_client(create_returns=created, resource_access=False)
    client.service_principals.list.side_effect = [
        iter([]),
        iter([conflicting]),
        iter([]),
    ]

    with pytest.raises(RuntimeError, match="did not converge"):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principals.delete.assert_called_once_with("created-id")


def test_pre_app_cleanup_proves_immutable_service_principal_id_absent() -> None:
    survivor = _sp(
        "renamed-survivor",
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = MagicMock()
    client.service_principals.get.return_value = survivor

    with pytest.raises(RuntimeError, match="ID is still present"):
        pmo._delete_failed_pre_app_service_principal(
            client,
            sp_id="proxy-sp-id",
            sp_name="mip-agent-supervisor-proxy-ci-sp",
        )

    client.service_principals.get.assert_called_once_with("proxy-sp-id")


def test_pre_app_missing_credential_id_quarantines_new_service_principal() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    new_sp = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(create_returns=new_sp, resource_access=False)
    client.service_principal_secrets_proxy.create.return_value = SimpleNamespace(
        id=None,
        secret="s" * 48,
    )

    with pytest.raises(
        pmo.CredentialMutationQuarantineError,
        match="no attributable credential",
    ):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principals.delete.assert_not_called()


def test_pre_app_missing_credential_secret_restores_and_deletes_new_principal() -> None:
    defaults = pmo.IDENTITY_DEFAULTS["agent_proxy"]
    new_sp = _sp(
        defaults.sp_name,
        sp_id="proxy-sp-id",
        application_id="proxy-application-id",
    )
    client = _make_client(create_returns=new_sp, resource_access=False)
    client.service_principal_secrets_proxy.create.return_value = SimpleNamespace(
        id="credential-id",
        secret="",
    )

    with pytest.raises(SystemExit, match="incomplete or ambiguous"):
        _provision(
            client,
            identity_role="agent_proxy",
            app_name="",
            grant_can_use=False,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            app_url_secret_name=None,
            credential_id_secret_name=defaults.credential_id_secret_name,
            pre_app_bootstrap=True,
        )

    client.service_principal_secrets_proxy.delete.assert_called_once_with(
        "proxy-sp-id",
        "credential-id",
    )
    client.service_principals.delete.assert_called_once_with("proxy-sp-id")


def test_atomic_bundle_writer_failure_preserves_old_value_before_production_compensation() -> None:
    """The primitive is atomic; production separately clears every armed name."""

    old_bundle = canonical_agent_proxy_credential_bundle(
        client_id="proxy-application-id",
        credential_id="old-credential",
        client_secret="old-secret",
    )
    sinks = {"DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE": old_bundle}
    active = {"old-credential", "new-credential"}
    fail_bundle_write = True

    def writer(_repo: str, name: str, value: str) -> None:
        nonlocal fail_bundle_write
        assert name == "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"
        if fail_bundle_write:
            fail_bundle_write = False
            raise RuntimeError("atomic bundle sink failed")
        sinks[name] = value

    def revoke(*, credential_id: str) -> None:
        active.remove(credential_id)

    with pytest.raises(RuntimeError, match="atomic bundle sink failed"):
        pmo._credential_delivery.deliver_oauth_credential(
            writer=writer,
            revoker=revoke,
            gh_repo=_CANONICAL_GH_REPO,
            client_id_secret_name="DATABRICKS_AGENT_PROXY_CLIENT_ID",
            client_id="proxy-application-id",
            client_secret_secret_name="DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
            client_secret="new-secret",
            credential_id="new-credential",
            credential_id_secret_name="DATABRICKS_AGENT_PROXY_CREDENTIAL_ID",
            app_url_secret_name=None,
            app_url=None,
            atomic_credential_bundle=True,
        )

    assert active == {"old-credential"}
    assert (
        parse_agent_proxy_credential_bundle(
            sinks["DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"]
        ).credential_id
        == "old-credential"
    )
    assert set(sinks) == {"DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"}

    active.add("retry-credential")
    pmo._credential_delivery.deliver_oauth_credential(
        writer=writer,
        revoker=revoke,
        gh_repo=_CANONICAL_GH_REPO,
        client_id_secret_name="DATABRICKS_AGENT_PROXY_CLIENT_ID",
        client_id="proxy-application-id",
        client_secret_secret_name="DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
        client_secret="retry-secret",
        credential_id="retry-credential",
        credential_id_secret_name="DATABRICKS_AGENT_PROXY_CREDENTIAL_ID",
        app_url_secret_name=None,
        app_url=None,
        atomic_credential_bundle=True,
    )
    credential = parse_agent_proxy_credential_bundle(
        sinks["DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"]
    )
    assert credential.credential_id == "retry-credential"
    assert credential.client_secret == "retry-secret"
    assert active == {"old-credential", "retry-credential"}


def test_concurrent_agent_proxy_rotations_leave_one_coherent_bundle() -> None:
    sinks: dict[str, str] = {}
    nested = False

    def deliver(label: str) -> None:
        pmo._credential_delivery.deliver_oauth_credential(
            writer=writer,
            revoker=lambda **_kwargs: None,
            gh_repo=_CANONICAL_GH_REPO,
            client_id_secret_name="DATABRICKS_AGENT_PROXY_CLIENT_ID",
            client_id="proxy-client",
            client_secret_secret_name="DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
            client_secret=f"{label}-secret",
            credential_id=f"{label}-credential",
            credential_id_secret_name="DATABRICKS_AGENT_PROXY_CREDENTIAL_ID",
            app_url_secret_name=None,
            app_url=None,
            atomic_credential_bundle=True,
        )

    def writer(_repo: str, name: str, value: str) -> None:
        nonlocal nested
        assert name == "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"
        sinks[name] = value
        credential = parse_agent_proxy_credential_bundle(value)
        if credential.credential_id == "a-credential" and not nested:
            nested = True
            deliver("b")

    deliver("a")

    bundle = parse_agent_proxy_credential_bundle(sinks["DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"])
    assert bundle.credential_id == "b-credential"
    assert bundle.client_secret == "b-secret"
    assert set(sinks) == {"DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE"}


def test_agent_proxy_bundle_cli_emits_only_requested_canonical_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
        canonical_agent_proxy_credential_bundle(
            client_id="proxy-client",
            credential_id="proxy-credential",
            client_secret="proxy-secret",
        ),
    )

    assert agent_proxy_bundle_main(["public-fields"]) == 0
    assert capsys.readouterr().out == "proxy-client\tproxy-credential\n"
    assert agent_proxy_bundle_main(["all-fields"]) == 0
    assert capsys.readouterr().out == "proxy-client\tproxy-credential\tproxy-secret\n"


def test_normal_secret_sink_discovers_the_exact_live_app_url() -> None:
    new_sp = _sp()
    client = _make_client(create_returns=new_sp)

    with patch.object(pmo, "_set_gh_secret") as set_secret:
        _provision(client, app_name="mip-app-pr105-staging", app_url=None)

    client.apps.get.assert_called_once_with("mip-app-pr105-staging")
    assert (
        call(
            _CANONICAL_GH_REPO,
            "MIP_APP_URL",
            "https://mip-app-live.aws.databricksapps.com",
        )
        in set_secret.call_args_list
    )


def test_missing_live_app_url_fails_before_identity_mutation() -> None:
    client = _make_client(create_returns=_sp())
    client.apps.get.return_value = SimpleNamespace(url=None)

    with pytest.raises(SystemExit, match="returned no valid HTTPS URL"):
        _provision(client, app_name="mip-app-pr105-staging", app_url=None)

    client.service_principals.list.assert_not_called()
    client.service_principals.create.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [
        ["--identity-role", "normal", "--sp-name", "mip-nightly-admin-ci-sp"],
        [
            "--identity-role",
            "normal",
            "--client-id-secret-name",
            "DATABRICKS_ADMIN_CLIENT_ID",
        ],
        ["--identity-role", "normal", "--no-app-url-secret"],
    ],
    ids=["reserved-sp", "admin-secret-sink", "missing-owned-app-url-sink"],
)
def test_dry_run_rejects_cross_role_identity_binding_before_external_checks(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch.object(pmo, "_load_app_name_from_bundle") as load_app,
        patch.object(pmo, "_infer_gh_repo") as infer_repo,
        patch.object(pmo, "_gh_available") as gh_available,
        patch.object(pmo, "provision") as mock_provision,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main([*argv, "--dry-run"])

    error = capsys.readouterr().err
    assert exc.value.code == 2
    assert "bound to reserved service principal" in error or "role-owned" in error
    load_app.assert_not_called()
    infer_repo.assert_not_called()
    gh_available.assert_not_called()
    mock_provision.assert_not_called()


def test_direct_provision_rejects_client_id_reserved_for_another_role_before_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_ADMIN_CLIENT_ID", "admin-client-id")
    client_factory = MagicMock()

    with pytest.raises(SystemExit, match="reserved for the admin identity role"):
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            expected_application_id="admin-client-id",
            app_name="mip-app",
            grant_can_use=True,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            gh_repo=None,
            set_gh_secrets=False,
            mint_secret=False,
            rotate=False,
            app_url="https://mip-app-test.aws.databricksapps.com",
            client_id_secret_name="DATABRICKS_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_CLIENT_SECRET",
            app_url_secret_name="MIP_APP_URL",
            identity_role="normal",
            client_factory=client_factory,
        )

    client_factory.assert_not_called()


def test_dry_run_rejects_cross_role_client_id_before_external_checks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABRICKS_ADMIN_CLIENT_ID", "admin-client-id")

    with (
        patch.object(pmo, "_load_app_name_from_bundle") as load_app,
        patch.object(pmo, "_infer_gh_repo") as infer_repo,
        patch.object(pmo, "_gh_available") as gh_available,
        patch.object(pmo, "provision") as mock_provision,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main(
            [
                "--identity-role",
                "normal",
                "--expected-application-id",
                "admin-client-id",
                "--dry-run",
            ]
        )

    assert exc.value.code == 2
    assert "reserved for the admin identity role" in capsys.readouterr().err
    load_app.assert_not_called()
    infer_repo.assert_not_called()
    gh_available.assert_not_called()
    mock_provision.assert_not_called()


def test_admin_missing_group_fails_closed_without_create_group() -> None:
    admin_sp = _sp("mip-nightly-admin-ci-sp")
    client = _make_client(existing_sp=admin_sp)

    with pytest.raises(SystemExit, match="--create-group"):
        _provision(
            client,
            sp_name=admin_sp.display_name,
            group_name="mip-admin",
            identity_role="admin",
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.groups.create.assert_not_called()
    client.groups.patch.assert_not_called()


def test_admin_missing_group_fails_before_creating_service_principal() -> None:
    client = _make_client(create_returns=_sp("mip-nightly-admin-ci-sp"))

    with pytest.raises(SystemExit, match="--create-group"):
        _provision(
            client,
            sp_name="mip-nightly-admin-ci-sp",
            group_name="mip-admin",
            identity_role="admin",
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.service_principals.list.assert_not_called()
    client.service_principals.create.assert_not_called()


def test_admin_create_group_then_adds_membership() -> None:
    admin_sp = _sp("mip-nightly-admin-ci-sp", sp_id="admin-scim-id")
    created_group = SimpleNamespace(id="group-1", display_name="mip-admin", members=[])
    client = _make_client(existing_sp=admin_sp)
    client.groups.create.return_value = created_group

    result = _provision(
        client,
        sp_name=admin_sp.display_name,
        group_name="mip-admin",
        identity_role="admin",
        create_group=True,
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
    )

    client.groups.create.assert_called_once_with(display_name="mip-admin")
    client.groups.patch.assert_called_once()
    patch_kwargs = client.groups.patch.call_args.kwargs
    assert patch_kwargs["id"] == "group-1"
    operation = patch_kwargs["operations"][0]
    assert operation.value["members"][0]["value"] == "admin-scim-id"
    assert operation.as_dict()["value"] == {
        "members": [{"value": "admin-scim-id"}],
    }
    assert result.added_to_group is True


def test_admin_existing_membership_is_idempotent() -> None:
    admin_sp = _sp("mip-nightly-admin-ci-sp", sp_id="admin-scim-id")
    group = SimpleNamespace(
        id="group-1",
        display_name="mip-admin",
        members=[SimpleNamespace(value="admin-scim-id")],
    )
    client = _make_client(existing_sp=admin_sp, groups=[group])

    result = _provision(
        client,
        sp_name=admin_sp.display_name,
        group_name="mip-admin",
        identity_role="admin",
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
    )

    client.groups.create.assert_not_called()
    client.groups.patch.assert_not_called()
    assert result.added_to_group is False


def test_verifier_reuses_safely_bootstrapped_lakebase_role_without_admin_or_app_grants() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    client = _make_client(
        existing_sp=verifier,
        lakebase_roles=[
            DatabaseInstanceRole(
                name="verifier-application-id",
                identity_type=DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL,
            )
        ],
        managed_serving_access=True,
    )

    result = _provision(
        client,
        sp_name=verifier.display_name,
        grant_can_use=False,
        group_name=None,
        lakebase_instance="mip-app-state",
        gateway_endpoint="mip-agent-gateway",
        warehouse_id="warehouse-123",
        identity_role="verifier",
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
    )

    managed_group_lookup = call(
        filter=(
            "displayName eq "
            f"'{managed_query_group_name(endpoint_id='mip-gateway-endpoint-id', application_id='verifier-application-id')}'"
        )
    )
    assert client.groups.list.call_args_list[0] == call(attributes="id,displayName")
    assert client.groups.list.call_args_list[1:]
    assert all(
        group_call == managed_group_lookup
        for group_call in client.groups.list.call_args_list[1:]
    )
    client.groups.patch.assert_not_called()
    client.apps.update_permissions.assert_not_called()
    client.database.create_database_instance_role.assert_not_called()
    client.serving_endpoints.update_permissions.assert_not_called()
    client.serving_endpoints.get.assert_called_once_with("mip-agent-gateway")
    assert result.created_lakebase_role is False
    assert result.granted_can_query is True
    client.warehouses.update_permissions.assert_called_once()
    (warehouse_id,) = client.warehouses.update_permissions.call_args.args
    warehouse_acl = client.warehouses.update_permissions.call_args.kwargs["access_control_list"]
    assert warehouse_id == "warehouse-123"
    assert warehouse_acl[0].service_principal_name == "verifier-application-id"
    assert (
        getattr(warehouse_acl[0].permission_level, "value", warehouse_acl[0].permission_level)
        == "CAN_USE"
    )
    assert result.granted_warehouse_can_use is True


def test_verifier_grants_green_before_revoke_and_preserves_signed_blue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier, resource_access=False)
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pmo,
        "_reserved_gateway_endpoints",
        lambda _client: {"green-gateway", "signed-blue-gateway", "stale-gateway"},
    )
    monkeypatch.setattr(
        pmo,
        "_grant_can_query_on_endpoint",
        lambda _client, endpoint, *_args, **_kwargs: events.append(("grant", endpoint)),
    )
    monkeypatch.setattr(
        pmo,
        "_revoke_can_query_on_obsolete_endpoint",
        lambda _client, endpoint, *_args, **_kwargs: events.append(("revoke", endpoint)),
    )
    monkeypatch.setattr(
        pmo,
        "_grant_can_use_on_warehouse",
        lambda *_args, **_kwargs: None,
    )

    result = _provision(
        client,
        sp_name=verifier.display_name,
        grant_can_use=False,
        group_name=None,
        gateway_endpoint="green-gateway",
        warehouse_id="warehouse-123",
        identity_role="verifier",
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
        revoke_gateway_endpoints=("explicit-obsolete",),
        preserve_gateway_endpoints=("signed-blue-gateway",),
    )

    assert events == [
        ("grant", "green-gateway"),
        ("revoke", "explicit-obsolete"),
        ("revoke", "stale-gateway"),
    ]
    assert result.granted_can_query is True


def test_provision_rejects_verifier_app_can_use_before_client_or_any_mutation() -> None:
    client_factory = MagicMock()

    with pytest.raises(SystemExit, match="verifier forbids Databricks App CAN_USE"):
        pmo.provision(
            sp_name="mip-ai-gateway-verifier-ci-sp",
            expected_application_id=None,
            app_name="mip-app",
            grant_can_use=True,
            group_name=None,
            create_group=False,
            lakebase_instance="mip-app-state",
            gateway_endpoint="mip-agent-gateway",
            warehouse_id="warehouse-123",
            gh_repo=_CANONICAL_GH_REPO,
            set_gh_secrets=True,
            mint_secret=True,
            rotate=True,
            app_url="https://mip-app-test.aws.databricksapps.com",
            client_id_secret_name="DATABRICKS_VERIFIER_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_VERIFIER_CLIENT_SECRET",
            app_url_secret_name=None,
            identity_role="verifier",
            client_factory=client_factory,
        )

    client_factory.assert_not_called()


def test_expected_application_id_fails_before_creating_missing_principal() -> None:
    client = _make_client(create_returns=_sp(application_id="unexpected-id"))

    with pytest.raises(SystemExit, match="refusing to create"):
        _provision(
            client,
            expected_application_id="authoritative-id",
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.service_principals.create.assert_not_called()
    client.apps.update_permissions.assert_not_called()


def test_verifier_fails_closed_on_forbidden_direct_app_permission() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                service_principal_name="verifier-application-id",
                all_permissions=[SimpleNamespace(inherited=False)],
            )
        ]
    )

    with pytest.raises(SystemExit, match="forbidden direct Databricks App"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.warehouses.update_permissions.assert_not_called()


@pytest.mark.parametrize(
    ("identity_role", "display_name", "application_id"),
    [
        ("verifier", "mip-ai-gateway-verifier-ci-sp", "verifier-application-id"),
        ("agent_runtime", "mip-agent-runtime-ci-sp", "runtime-application-id"),
    ],
)
def test_isolated_role_fails_closed_on_permission_to_other_app(
    identity_role: str,
    display_name: str,
    application_id: str,
) -> None:
    principal = _sp(display_name, application_id=application_id)
    client = _make_client(existing_sp=principal)
    client.apps.list.return_value = iter(
        [SimpleNamespace(name="mip-app"), SimpleNamespace(name="unrelated-app")]
    )
    client.apps.get_permissions.side_effect = lambda app_name: SimpleNamespace(
        access_control_list=(
            [
                SimpleNamespace(
                    service_principal_name=application_id,
                    all_permissions=[SimpleNamespace(inherited=False)],
                )
            ]
            if app_name == "unrelated-app"
            else []
        )
    )

    with pytest.raises(SystemExit, match="permission on 'unrelated-app'"):
        _provision(
            client,
            sp_name=display_name,
            expected_application_id=application_id,
            identity_role=identity_role,
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    assert client.apps.get_permissions.call_args_list == [
        call("mip-app"),
        call("unrelated-app"),
    ]


def test_isolated_role_fails_closed_on_group_access_to_other_app() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    access_group = SimpleNamespace(
        id="broad-app-group",
        display_name="all-app-users",
        members=[SimpleNamespace(value="verifier-scim-id")],
    )
    client = _make_client(existing_sp=verifier, groups=[access_group])
    client.apps.list.return_value = iter(
        [SimpleNamespace(name="mip-app"), SimpleNamespace(name="unrelated-app")]
    )
    client.apps.get_permissions.side_effect = lambda app_name: SimpleNamespace(
        access_control_list=(
            [
                SimpleNamespace(
                    group_name="all-app-users",
                    all_permissions=[SimpleNamespace(inherited=False)],
                )
            ]
            if app_name == "unrelated-app"
            else []
        )
    )

    with pytest.raises(
        SystemExit,
        match="permission on 'unrelated-app' through group 'all-app-users'",
    ):
        _provision(
            client,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )


def test_verifier_fails_closed_on_inherited_effective_app_permission() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                service_principal_name="verifier-application-id",
                all_permissions=[SimpleNamespace(inherited=True)],
            )
        ]
    )

    with pytest.raises(SystemExit, match="including inherited/effective permissions"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.database.create_database_instance_role.assert_not_called()


def test_verifier_fails_closed_on_direct_group_app_permission() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    app_group = SimpleNamespace(
        id="app-group-id",
        display_name="mip-app-users",
        members=[SimpleNamespace(value="verifier-scim-id")],
    )
    client = _make_client(existing_sp=verifier, groups=[app_group])
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                group_name="mip-app-users",
                all_permissions=[SimpleNamespace(inherited=False)],
            )
        ]
    )

    with pytest.raises(SystemExit, match="through group 'mip-app-users'"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.database.create_database_instance_role.assert_not_called()


def test_verifier_fails_closed_on_nested_group_app_permission() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    child_group = SimpleNamespace(
        id="child-group-id",
        display_name="mip-verifier-automation",
        members=[SimpleNamespace(value="verifier-scim-id")],
    )
    app_group = SimpleNamespace(
        id="app-group-id",
        display_name="mip-app-users",
        members=[SimpleNamespace(value="child-group-id")],
    )
    client = _make_client(existing_sp=verifier, groups=[app_group, child_group])
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                group_name="mip-app-users",
                all_permissions=[SimpleNamespace(inherited=True)],
            )
        ]
    )

    with (
        patch.object(pmo, "_set_gh_secret") as set_secret,
        pytest.raises(SystemExit, match="through group 'mip-app-users'"),
    ):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            lakebase_instance="mip-app-state",
            gateway_endpoint="mip-agent-gateway",
            warehouse_id="warehouse-123",
            mint_secret=True,
            rotate=True,
        )

    client.database.create_database_instance_role.assert_not_called()
    client.serving_endpoints.get.assert_not_called()
    client.serving_endpoints.update_permissions.assert_not_called()
    client.warehouses.update_permissions.assert_not_called()
    client.apps.update_permissions.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_not_called()
    set_secret.assert_not_called()


def test_release_probe_rechecks_app_isolation_after_group_membership_repair() -> None:
    release_probe = _sp(
        "mip-release-probe-ci-sp",
        sp_id="release-probe-scim-id",
        application_id="release-probe-application-id",
    )
    admin_group = SimpleNamespace(
        id="mip-admin-group-id",
        display_name="mip-admin",
        members=[],
    )
    client = _make_client(existing_sp=release_probe, groups=[admin_group])
    client.apps.list.side_effect = lambda: iter([SimpleNamespace(name="mip-app")])
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                group_name="mip-admin",
                all_permissions=[SimpleNamespace(inherited=False)],
            )
        ]
    )

    # The initial principal and target-group checks see no effective App
    # grant. Only the authoritative post-repair membership hydration exposes
    # the mip-admin group grant that provisioning must reject.
    release_probe_hydrations = 0

    def resolve_effective_groups(_client: object, *, sp_id: str) -> dict[str, str]:
        nonlocal release_probe_hydrations
        if sp_id != "release-probe-scim-id":
            return {}
        release_probe_hydrations += 1
        if release_probe_hydrations == 1:
            return {}
        return {"mip-admin-group-id": "mip-admin"}

    with (
        patch.object(
            pmo,
            "_resolve_effective_groups",
            side_effect=resolve_effective_groups,
        ),
        pytest.raises(SystemExit, match="through group 'mip-admin'"),
    ):
        _provision(
            client,
            sp_name=release_probe.display_name,
            expected_application_id=release_probe.application_id,
            identity_role="release_probe",
            group_name="mip-admin",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.groups.patch.assert_called_once()
    assert release_probe_hydrations == 2
    assert client.apps.get_permissions.call_count == 2
    client.service_principal_secrets_proxy.create.assert_not_called()


@pytest.mark.parametrize(
    ("identity_role", "display_name", "application_id"),
    [
        ("normal", "mip-nightly-ci-sp", "normal-application-id"),
        ("operator2", "mip-nightly-operator2-ci-sp", "operator2-application-id"),
        ("admin", "mip-nightly-admin-ci-sp", "admin-application-id"),
    ],
)
def test_runtime_user_role_rejects_effective_app_manager_group_before_grant_or_mint(
    identity_role: str,
    display_name: str,
    application_id: str,
) -> None:
    principal = _sp(
        display_name,
        sp_id=f"{identity_role}-scim-id",
        application_id=application_id,
    )
    manager_group = SimpleNamespace(
        id="release-managers-id",
        display_name="release-managers",
        members=[SimpleNamespace(value=f"{identity_role}-scim-id")],
    )
    groups = [manager_group]
    bound_group_name = pmo.IDENTITY_DEFAULTS[identity_role].group_name
    if bound_group_name:
        groups.append(
            SimpleNamespace(
                id=f"{bound_group_name}-id",
                display_name=bound_group_name,
                members=[SimpleNamespace(value=f"{identity_role}-scim-id")],
            )
        )
    client = _make_client(existing_sp=principal, groups=groups)
    client.apps.get_permissions.return_value = SimpleNamespace(
        access_control_list=[
            SimpleNamespace(
                group_name="release-managers",
                service_principal_name=None,
                display_name="release-managers",
                all_permissions=[SimpleNamespace(permission_level="CAN_MANAGE", inherited=False)],
            )
        ]
    )

    with pytest.raises(SystemExit, match="effective Databricks App CAN_MANAGE"):
        _provision(
            client,
            sp_name=display_name,
            expected_application_id=application_id,
            identity_role=identity_role,
            group_name=bound_group_name,
            grant_can_use=True,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.apps.update_permissions.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_not_called()


def test_non_admin_identity_fails_closed_on_admin_group_membership() -> None:
    normal = _sp(sp_id="normal-scim-id")
    admin_group = SimpleNamespace(
        id="group-1",
        display_name="mip-admin",
        members=[SimpleNamespace(value="normal-scim-id")],
    )
    client = _make_client(existing_sp=normal, groups=[admin_group])

    with pytest.raises(SystemExit, match="forbidden admin group"):
        _provision(
            client,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.apps.update_permissions.assert_not_called()


def test_verifier_fails_closed_on_nested_admin_group_membership() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    child_group = SimpleNamespace(
        id="child-group-id",
        display_name="mip-verifier-automation",
        members=[SimpleNamespace(value="verifier-scim-id")],
    )
    admin_group = SimpleNamespace(
        id="admin-group-id",
        display_name="mip-admin",
        members=[SimpleNamespace(value="child-group-id")],
    )
    client = _make_client(existing_sp=verifier, groups=[admin_group, child_group])

    with pytest.raises(SystemExit, match="direct or nested membership"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.apps.get_permissions.assert_not_called()
    client.database.create_database_instance_role.assert_not_called()


@pytest.mark.parametrize("group_name", ["admins", "Account Admins", "Metastore Admins"])
def test_verifier_rejects_visible_builtin_admin_group(group_name: str) -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    group = SimpleNamespace(
        id="builtin-admin-group",
        display_name=group_name,
        members=[SimpleNamespace(value="verifier-scim-id")],
    )
    client = _make_client(existing_sp=verifier, groups=[group])

    with pytest.raises(SystemExit, match="forbidden built-in administrator group"):
        _provision(
            client,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.apps.get_permissions.assert_not_called()


def test_verifier_rejects_direct_administrative_role() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    hydrated = SimpleNamespace(
        **vars(verifier),
        roles=[SimpleNamespace(value="service-principal-manager")],
        entitlements=[],
    )
    client = _make_client(existing_sp=verifier)
    client.service_principals.get.return_value = hydrated

    with pytest.raises(SystemExit, match="forbidden administrative role"):
        _provision(
            client,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )


def test_verifier_rejects_powerful_cluster_create_entitlement() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        sp_id="verifier-scim-id",
        application_id="verifier-application-id",
    )
    hydrated = SimpleNamespace(
        **vars(verifier),
        roles=[],
        entitlements=[SimpleNamespace(value="allow-cluster-create")],
    )
    client = _make_client(existing_sp=verifier)
    client.service_principals.get.return_value = hydrated

    with pytest.raises(SystemExit, match="forbidden powerful entitlement"):
        _provision(
            client,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )


def test_verifier_group_resolution_error_fails_closed_before_grants() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)
    client.groups.list.side_effect = RuntimeError("SCIM group resolution unavailable")

    with pytest.raises(SystemExit, match="resolve effective group memberships failed"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.apps.get_permissions.assert_not_called()
    client.database.create_database_instance_role.assert_not_called()


def test_verifier_app_permission_resolution_error_fails_closed_before_grants() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(existing_sp=verifier)
    client.apps.get_permissions.side_effect = RuntimeError("App ACL resolution unavailable")

    with pytest.raises(SystemExit, match="inspect app permissions failed"):
        _provision(
            client,
            sp_name=verifier.display_name,
            identity_role="verifier",
            grant_can_use=False,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.database.create_database_instance_role.assert_not_called()
    client.serving_endpoints.update_permissions.assert_not_called()


def test_non_admin_identity_hydrates_sparse_group_before_membership_check() -> None:
    normal = _sp(sp_id="normal-scim-id")
    sparse_group = SimpleNamespace(id="group-1", display_name="mip-admin")
    hydrated_group = SimpleNamespace(
        id="group-1",
        display_name="mip-admin",
        members=[SimpleNamespace(value="normal-scim-id")],
    )
    client = _make_client(existing_sp=normal, groups=[sparse_group])
    client.groups.get.side_effect = None
    client.groups.get.return_value = hydrated_group

    with pytest.raises(SystemExit, match="forbidden admin group"):
        _provision(
            client,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )

    client.groups.get.assert_called_once_with("group-1")
    client.apps.update_permissions.assert_not_called()


def test_verifier_gateway_grant_fails_closed_without_endpoint_id() -> None:
    client = _make_client()
    client.serving_endpoints.get.return_value = SimpleNamespace(
        id=None,
        name="mip-agent-gateway",
    )

    with pytest.raises(SystemExit, match="has no immutable id"):
        pmo._grant_can_query_on_endpoint(
            client,
            "mip-agent-gateway",
            "verifier-application-id",
            sp_id="verifier-scim-id",
            effective_group_names=set(),
            assert_single_writer=lambda: None,
        )

    client.serving_endpoints.update_permissions.assert_not_called()


def test_existing_verifier_lakebase_role_is_idempotent() -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(
        existing_sp=verifier,
        lakebase_roles=[
            DatabaseInstanceRole(
                name="verifier-application-id",
                identity_type=DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL,
            )
        ],
    )

    result = _provision(
        client,
        grant_can_use=False,
        lakebase_instance="mip-app-state",
        identity_role="verifier",
        mint_secret=False,
        set_gh_secrets=False,
        gh_repo=None,
    )

    client.database.create_database_instance_role.assert_not_called()
    assert result.created_lakebase_role is False


@pytest.mark.parametrize(
    "identity_type",
    [DatabaseInstanceRoleIdentityType.USER, DatabaseInstanceRoleIdentityType.GROUP, None],
    ids=["user", "group", "absent"],
)
def test_existing_verifier_lakebase_role_rejects_non_service_principal_identity_before_grants(
    identity_type: DatabaseInstanceRoleIdentityType | None,
) -> None:
    verifier = _sp(
        "mip-ai-gateway-verifier-ci-sp",
        application_id="verifier-application-id",
    )
    client = _make_client(
        existing_sp=verifier,
        lakebase_roles=[
            DatabaseInstanceRole(
                name="verifier-application-id",
                identity_type=identity_type,
            )
        ],
    )

    with pytest.raises(SystemExit, match="identity_type='SERVICE_PRINCIPAL'"):
        _provision(
            client,
            grant_can_use=False,
            lakebase_instance="mip-app-state",
            gateway_endpoint="mip-agent-gateway",
            warehouse_id="warehouse-123",
            identity_role="verifier",
            rotate=True,
        )

    client.database.create_database_instance_role.assert_not_called()
    client.serving_endpoints.update_permissions.assert_not_called()
    client.warehouses.update_permissions.assert_not_called()
    client.apps.update_permissions.assert_not_called()
    client.service_principal_secrets_proxy.create.assert_not_called()


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
def test_cli_rejects_github_repo_that_differs_from_reviewed_secret_sink(
    dry_run: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["--gh-repo", "attacker/unreviewed-repo"]
    if dry_run:
        argv.append("--dry-run")

    with (
        patch.object(pmo, "_load_app_name_from_bundle") as load_app,
        patch.object(pmo, "_infer_gh_repo", return_value=_CANONICAL_GH_REPO) as infer_repo,
        patch.object(pmo, "_gh_available") as gh_available,
        patch.object(pmo, "provision") as mock_provision,
        pytest.raises(SystemExit) as exc,
    ):
        pmo.main(argv)

    assert exc.value.code == 2
    assert "reviewed credential sink" in capsys.readouterr().err
    load_app.assert_called_once()
    infer_repo.assert_called_once()
    gh_available.assert_not_called()
    mock_provision.assert_not_called()


def test_cli_accepts_customer_fork_as_its_reviewed_origin() -> None:
    with (
        patch.object(pmo, "_load_app_name_from_bundle", return_value="mip-app"),
        patch.object(pmo, "_infer_gh_repo", return_value="acme-bank/mip"),
        patch.object(pmo, "provision") as mock_provision,
    ):
        assert pmo.main(["--dry-run"]) == 0

    mock_provision.assert_not_called()


def test_cli_allows_customer_repo_for_grant_only_reconciliation() -> None:
    with (
        patch.object(pmo, "_load_app_name_from_bundle", return_value="mip-app"),
        patch.object(pmo, "_infer_gh_repo", return_value=_CANONICAL_GH_REPO),
        patch.object(pmo, "provision") as mock_provision,
    ):
        assert (
            pmo.main(
                [
                    "--gh-repo",
                    "acme-bank/mip",
                    "--no-mint-secret",
                    "--dry-run",
                ]
            )
            == 0
        )

    mock_provision.assert_not_called()


def test_direct_provision_rejects_unreviewed_secret_sink_before_client_or_mint() -> None:
    client_factory = MagicMock()
    with (
        patch.object(pmo, "_infer_gh_repo", return_value=_CANONICAL_GH_REPO),
        patch.object(pmo, "_gh_available") as gh_available,
        patch.object(pmo, "_set_gh_secret") as set_secret,
        pytest.raises(SystemExit, match="reviewed credential sink"),
    ):
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            expected_application_id=None,
            app_name="mip-app",
            grant_can_use=True,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            gh_repo="attacker/unreviewed-repo",
            set_gh_secrets=True,
            mint_secret=True,
            rotate=True,
            app_url="https://example",
            client_id_secret_name="DATABRICKS_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_CLIENT_SECRET",
            app_url_secret_name="MIP_APP_URL",
            identity_role="normal",
            client_factory=client_factory,
        )

    client_factory.assert_not_called()
    gh_available.assert_not_called()
    set_secret.assert_not_called()


def test_live_mint_requires_authenticated_gh_before_sdk_mutation() -> None:
    client_factory = MagicMock()
    with (
        patch.object(pmo, "_gh_available", return_value=False),
        pytest.raises(SystemExit, match="authenticated gh CLI"),
    ):
        pmo.provision(
            sp_name="mip-nightly-ci-sp",
            expected_application_id=None,
            app_name="mip-app",
            grant_can_use=True,
            group_name=None,
            create_group=False,
            lakebase_instance=None,
            gateway_endpoint=None,
            warehouse_id=None,
            gh_repo=_CANONICAL_GH_REPO,
            set_gh_secrets=True,
            mint_secret=True,
            rotate=False,
            app_url="https://example",
            client_id_secret_name="DATABRICKS_CLIENT_ID",
            client_secret_secret_name="DATABRICKS_CLIENT_SECRET",
            app_url_secret_name="MIP_APP_URL",
            identity_role="normal",
            client_factory=client_factory,
        )
    client_factory.assert_not_called()


def test_set_gh_secret_uses_stdin_and_never_outputs_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch.object(pmo.subprocess, "run") as run:
        pmo._set_gh_secret("acme/repo", "DATABRICKS_CLIENT_SECRET", "s3cr3t")

    args, kwargs = run.call_args
    assert args[0] == [
        "gh",
        "secret",
        "set",
        "DATABRICKS_CLIENT_SECRET",
        "--repo",
        "acme/repo",
    ]
    assert kwargs["input"] == b"s3cr3t"
    assert "s3cr3t" not in " ".join(args[0])
    captured = capsys.readouterr()
    assert "s3cr3t" not in captured.out
    assert "s3cr3t" not in captured.err


def test_mint_missing_secret_fails_without_printing_response_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _make_client(create_returns=_sp(), mint_secret_value="")
    with (
        patch.object(pmo, "_set_gh_secret"),
        pytest.raises(SystemExit, match="incomplete or ambiguous"),
    ):
        _provision(client)
    captured = capsys.readouterr()
    assert "dose_fake_secret_value" not in captured.out + captured.err


def test_app_not_found_prompts_signed_command_of_record() -> None:
    client = _make_client(existing_sp=_sp())
    client.apps.update_permissions.side_effect = RuntimeError("App mip-app NOT FOUND")
    with pytest.raises(SystemExit, match=r"scripts/deploy\.sh -t dev"):
        _provision(
            client,
            mint_secret=False,
            set_gh_secrets=False,
            gh_repo=None,
        )
