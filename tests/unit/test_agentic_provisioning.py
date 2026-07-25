from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service.database import (
    NewPipelineSpec,
    SyncedTableSchedulingPolicy,
    SyncedTableSpec,
)
from databricks.sdk.service.serving import (
    ServingEndpointAccessControlResponse,
    ServingEndpointPermission,
    ServingEndpointPermissionLevel,
    ServingEndpointPermissions,
)

from tools.databricks import (
    agentic_resource_contract,
    export_gateway_runtime_contract,
    provision_agentic_resources,
)
from tools.databricks.agentic_env_file import merge_agentic_env_values
from tools.databricks.agentic_supervisor_endpoint import (
    managed_query_supervisor_replacement_name,
    supervisor_endpoint_requires_managed_query_rotation,
)
from tools.databricks.provision_agentic_resources import (
    ProvisionedResources,
    SupervisorAgentBinding,
)
from tools.databricks.serving_query_group_access import (
    managed_query_group_external_id,
    managed_query_group_name,
)
from tools.databricks.supervisor_agent_contract import (
    RUNTIME_REPLACEMENT_SUFFIX,
    supervisor_replacement_name,
)

_PROXY_CLIENT_ID = "proxy-client"
_PROXY_CREDENTIAL_ID = "proxy-credential"
_PROXY_SECRET_REFERENCE = "{{secrets/mip-agent-proxy/oauth-client-secret-proxy-credential}}"
_PROXY_ARGS = [
    "--reviewed-function-owner",
    "reviewed-owner",
    "--proxy-caller-application-id",
    _PROXY_CLIENT_ID,
    "--proxy-caller-credential-id",
    _PROXY_CREDENTIAL_ID,
    "--proxy-caller-secret-reference",
    _PROXY_SECRET_REFERENCE,
]


def _assert_single_writer() -> None:
    return None


class _SupervisorEndpoints:
    def __init__(
        self,
        permissions: dict[str, ServingEndpointPermissions] | None = None,
    ) -> None:
        self.permissions = permissions or {}
        self.permission_reads: list[str] = []

    def get(self, endpoint_name: str) -> object:
        return SimpleNamespace(id=f"{endpoint_name}-id", creator="runtime-client")

    def get_permissions(self, endpoint_id: str) -> ServingEndpointPermissions:
        self.permission_reads.append(endpoint_id)
        return self.permissions.get(
            endpoint_id,
            ServingEndpointPermissions(access_control_list=[]),
        )


class _SupervisorApi:
    def do(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert method == "GET"
        rows = provision_agentic_resources._supervisor_agents()
        if path == "/api/2.1/supervisor-agents":
            assert query == {"page_size": 100}
            return {"supervisor_agents": [dict(row) for row in rows]}
        supervisor_id = path.rsplit("/", 1)[-1]
        matches = [
            row for row in rows if str(row.get("supervisor_agent_id") or "") == supervisor_id
        ]
        assert len(matches) == 1
        return dict(matches[0])


def _supervisor_workspace(
    permissions: dict[str, ServingEndpointPermissions] | None = None,
) -> object:
    endpoint_permissions = permissions or {}
    groups: dict[str, object] = {}
    principals: dict[str, object] = {}
    for endpoint_id, endpoint_acl in endpoint_permissions.items():
        for application_id in ("proxy-client", "app-client", "verifier-client"):
            name = managed_query_group_name(
                endpoint_id=endpoint_id,
                application_id=application_id,
            )
            if not any(
                str(getattr(entry, "group_name", "") or "") == name
                for entry in (endpoint_acl.access_control_list or [])
            ):
                continue
            scim_id = f"{application_id}-scim"
            group_id = f"{endpoint_id}-{application_id}-group"
            groups[group_id] = SimpleNamespace(
                id=group_id,
                display_name=name,
                external_id=managed_query_group_external_id(
                    endpoint_id=endpoint_id,
                    application_id=application_id,
                ),
                members=[SimpleNamespace(value=scim_id)],
            )
            principals[scim_id] = SimpleNamespace(
                id=scim_id,
                application_id=application_id,
            )

    def list_groups(*, filter: str) -> list[object]:
        match = re.fullmatch(r"displayName eq '([^']+)'", filter)
        assert match
        return [
            group
            for group in groups.values()
            if str(getattr(group, "display_name", "")) == match.group(1)
        ]

    return SimpleNamespace(
        api_client=_SupervisorApi(),
        serving_endpoints=_SupervisorEndpoints(endpoint_permissions),
        groups=SimpleNamespace(
            list=list_groups,
            get=lambda group_id: groups[group_id],
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: list(principals.values()),
            get=lambda principal_id: principals[principal_id],
        ),
    )


def _serving_permission(
    principal: str,
    level: str,
) -> ServingEndpointAccessControlResponse:
    return ServingEndpointAccessControlResponse(
        service_principal_name=principal,
        all_permissions=[
            ServingEndpointPermission(
                inherited=False,
                permission_level=ServingEndpointPermissionLevel(level),
            )
        ],
    )


def _serving_group_permission(
    group_name: str,
    level: str,
) -> ServingEndpointAccessControlResponse:
    return ServingEndpointAccessControlResponse(
        group_name=group_name,
        all_permissions=[
            ServingEndpointPermission(
                inherited=False,
                permission_level=ServingEndpointPermissionLevel(level),
            )
        ],
    )


def _serving_user_permission(
    user_name: str,
    level: str,
) -> ServingEndpointAccessControlResponse:
    return ServingEndpointAccessControlResponse(
        user_name=user_name,
        all_permissions=[
            ServingEndpointPermission(
                inherited=False,
                permission_level=ServingEndpointPermissionLevel(level),
            )
        ],
    )


def test_capture_reviewed_function_owner_exports_authenticated_deployer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    out_env = tmp_path / "agentic.env"
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)
    monkeypatch.setattr(
        agentic_resource_contract,
        "authenticated_reviewed_function_owner",
        lambda _workspace, *, catalog: (
            "reviewed-owner" if catalog == "mip" else pytest.fail("wrong catalog")
        ),
    )

    assert (
        provision_agentic_resources.main(
            [
                "--skip-sync",
                "--skip-supervisor",
                "--skip-gateway",
                "--capture-reviewed-function-owner",
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )
    assert (
        "MIP_REVIEWED_FUNCTION_OWNER=reviewed-owner"
        in out_env.read_text(encoding="utf-8").splitlines()
    )


def test_capture_reviewed_function_owner_rejects_configured_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)
    monkeypatch.setattr(
        agentic_resource_contract,
        "authenticated_reviewed_function_owner",
        lambda _workspace, *, catalog: "reviewed-owner",
    )

    with pytest.raises(
        RuntimeError,
        match="differs from the authenticated deployer",
    ):
        provision_agentic_resources.main(
            [
                "--skip-sync",
                "--skip-supervisor",
                "--skip-gateway",
                "--capture-reviewed-function-owner",
                "--reviewed-function-owner",
                "unreviewed-owner",
            ]
        )


def test_split_provisioning_merge_preserves_replaced_supervisor_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "agentic.env"
    supervisor = ProvisionedResources(
        lakebase_sync_catalog="sync",
        lakebase_sync_schema="app",
        lakebase_sync_tables=("source_readiness",),
        agent_supervisor_id="green-supervisor",
        agent_supervisor_name="Mortgage Growth Agent",
        agent_serving_endpoint="green-supervisor-endpoint",
        agent_supervisor_endpoint="green-supervisor-endpoint",
        agent_supervisor_endpoint_id="green-supervisor-endpoint-id",
        replaced_supervisor_id="blue-supervisor",
        replaced_supervisor_endpoint="blue-supervisor-endpoint",
        replaced_supervisor_creator="runtime-client",
        replaced_supervisor_create_time="2026-07-20T00:00:00Z",
        agent_runtime_application_id="runtime-client",
        reviewed_function_owner="reviewed-owner",
    )
    gateway = ProvisionedResources(
        lakebase_sync_catalog="sync",
        lakebase_sync_schema="app",
        lakebase_sync_tables=("source_readiness",),
        agent_supervisor_id="green-supervisor",
        agent_supervisor_name="Mortgage Growth Agent",
        agent_serving_endpoint="green-gateway",
        agent_supervisor_endpoint="green-supervisor-endpoint",
        ai_gateway_endpoint="green-gateway",
        ai_gateway_inference_table="mip.audit.green_inference",
        ai_gateway_agent_model="mip.audit.proxy",
        ai_gateway_agent_model_version=7,
        agent_runtime_application_id="runtime-client",
        agent_proxy_application_id=_PROXY_CLIENT_ID,
        agent_proxy_credential_id=_PROXY_CREDENTIAL_ID,
        agent_proxy_secret_reference=_PROXY_SECRET_REFERENCE,
        reviewed_function_owner="reviewed-owner",
    )

    provision_agentic_resources.write_agentic_env(path, supervisor)
    merge_agentic_env_values(
        path,
        {"MIP_AGENT_PROXY_SECRET_REFERENCE": _PROXY_SECRET_REFERENCE},
    )
    provision_agentic_resources.write_agentic_env(path, gateway, merge=True)
    monkeypatch.setattr(
        export_gateway_runtime_contract,
        "WorkspaceClient",
        lambda: object(),
    )
    monkeypatch.setattr(
        export_gateway_runtime_contract,
        "MlflowClient",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        export_gateway_runtime_contract,
        "resolve_contract",
        lambda *_args, **_kwargs: {
            "MIP_AGENT_SERVING_ENDPOINT": "verified-gateway",
            "MIP_AGENT_SUPERVISOR_ID": "green-supervisor",
            "MIP_AGENT_PROXY_SECRET_REFERENCE": _PROXY_SECRET_REFERENCE,
        },
    )
    assert (
        export_gateway_runtime_contract.main(
            [
                "--shell-env",
                str(path),
                "--genie-space-id",
                "space-123",
                "--runtime-application-id",
                "runtime-client",
                "--reviewed-function-owner",
                "reviewed-owner",
                "--proxy-caller-application-id",
                _PROXY_CLIENT_ID,
                "--proxy-caller-credential-id",
                _PROXY_CREDENTIAL_ID,
                "--proxy-caller-secret-reference",
                _PROXY_SECRET_REFERENCE,
            ]
        )
        == 0
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    keys = [line.split("=", 1)[0] for line in lines]
    assert len(keys) == len(set(keys))
    assert sum(line.startswith("MIP_AGENT_PROXY_SECRET_REFERENCE=") for line in lines) == 1
    values = dict(line.split("=", 1) for line in lines)
    assert values["MIP_AGENT_SERVING_ENDPOINT"] == "verified-gateway"
    assert values["MIP_REPLACED_AGENT_SUPERVISOR_ID"] == "blue-supervisor"
    assert values["MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"] == "blue-supervisor-endpoint"
    assert values["MIP_REPLACED_AGENT_SUPERVISOR_CREATOR"] == "runtime-client"


@pytest.mark.parametrize("canonical_present", [True, False])
def test_hashed_and_legacy_supervisor_collision_fails_before_selection(
    monkeypatch: pytest.MonkeyPatch,
    canonical_present: bool,
) -> None:
    display_name = "Mortgage Growth Agent"
    replacement_name = supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    agents = [
        {
            "supervisor_agent_id": "hashed-id",
            "display_name": replacement_name,
            "endpoint_name": "mas-hashed",
            "creator": "runtime-client",
        },
        {
            "supervisor_agent_id": "legacy-id",
            "display_name": (f"{display_name}{RUNTIME_REPLACEMENT_SUFFIX}"),
            "endpoint_name": "mas-legacy",
            "creator": "runtime-client",
        },
    ]
    if canonical_present:
        agents.insert(
            0,
            {
                "supervisor_agent_id": "canonical-id",
                "display_name": display_name,
                "endpoint_name": "mas-canonical",
                "creator": "runtime-client",
            },
        )
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: agents)
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("collision must fail before contract selection")
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("collision must not create or inspect a selected agent")
        ),
    )

    with pytest.raises(RuntimeError, match="contract-hashed and legacy runtime"):
        provision_agentic_resources.ensure_supervisor_agent(
            _supervisor_workspace(),
            display_name=display_name,
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            assert_single_writer=_assert_single_writer,
        )


def test_provisioned_resource_contract_is_reexported_and_preserves_sync_names() -> None:
    resources = ProvisionedResources(
        lakebase_sync_catalog="customer.app_state",
        lakebase_sync_schema="customer_sync",
        lakebase_sync_tables=("source_status_v2", "daily_funnel_v2"),
    )

    assert resources.env_lines() == [
        "MIP_LAKEBASE_SYNC=1",
        "MIP_LAKEBASE_SYNC_CATALOG=customer.app_state",
        "MIP_LAKEBASE_SYNC_SCHEMA=customer_sync",
        "MIP_LAKEBASE_SYNC_TABLES=source_status_v2,daily_funnel_v2",
    ]


def test_creator_mismatch_builds_blue_green_replacement_without_touching_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement_name = supervisor_replacement_name(
        "Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
    )
    old = {
        "supervisor_agent_id": "old-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-old",
        "creator": "skyler@entrada.ai",
        "create_time": "old-time",
    }
    calls: list[tuple[list[str], object | None]] = []
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [old])
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )

    def run(args: list[str], *, input_json: object | None = None) -> dict[str, str]:
        calls.append((args, input_json))
        if args[:2] == ["supervisor-agents", "create-supervisor-agent"]:
            assert isinstance(input_json, dict)
            assert input_json["display_name"] == replacement_name
            return {
                "supervisor_agent_id": "new-id",
                "endpoint_name": "mas-new",
            }
        return {
            "supervisor_agent_id": "new-id",
            "endpoint_name": "mas-new",
            "creator": "runtime-client",
        }

    monkeypatch.setattr(provision_agentic_resources, "_run", run)

    with pytest.raises(RuntimeError, match="signed prepare/create/claim/complete"):
        provision_agentic_resources.ensure_supervisor_agent(
            _supervisor_workspace(),
            display_name="Mortgage Growth Agent",
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            expected_query_application_id="proxy-client",
            assert_single_writer=_assert_single_writer,
        )
    assert calls == []


def test_runtime_owned_contract_drift_builds_green_without_mutating_live_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = {
        "supervisor_agent_id": "old-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-old",
        "creator": "runtime-client",
        "create_time": "old-time",
    }
    replacement_name = supervisor_replacement_name(
        "Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
    )
    mutated_tools: list[str] = []
    calls: list[tuple[list[str], object | None]] = []
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [old])

    def exact(supervisor_id: str, **_kwargs: object) -> None:
        if supervisor_id == "old-id":
            raise provision_agentic_resources.SupervisorContractDrift("tool drift")

    monkeypatch.setattr(provision_agentic_resources, "assert_exact_supervisor_contract", exact)
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda supervisor_id, **_kwargs: mutated_tools.append(supervisor_id),
    )

    def run(args: list[str], *, input_json: object | None = None) -> dict[str, str]:
        calls.append((args, input_json))
        if args[:2] == ["supervisor-agents", "create-supervisor-agent"]:
            assert isinstance(input_json, dict)
            assert input_json["display_name"] == replacement_name
            return {"supervisor_agent_id": "new-id", "endpoint_name": "mas-new"}
        return {
            "supervisor_agent_id": "new-id",
            "endpoint_name": "mas-new",
            "creator": "runtime-client",
        }

    monkeypatch.setattr(provision_agentic_resources, "_run", run)

    with pytest.raises(RuntimeError, match="signed prepare/create/claim/complete"):
        provision_agentic_resources.ensure_supervisor_agent(
            _supervisor_workspace(),
            display_name="Mortgage Growth Agent",
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            expected_query_application_id="proxy-client",
            assert_single_writer=_assert_single_writer,
        )
    assert mutated_tools == []
    assert calls == []


def test_exact_runtime_supervisor_is_reused_without_tool_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = {
        "supervisor_agent_id": "canonical-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-canonical",
        "creator": "runtime-client",
        "create_time": "old-time",
    }
    workspace = _supervisor_workspace(
        {
            "mas-canonical-id": ServingEndpointPermissions(
                access_control_list=[
                    _serving_permission("runtime-client", "CAN_MANAGE"),
                    _serving_group_permission("admins", "CAN_MANAGE"),
                ]
            )
        }
    )
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [canonical])
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mutate tools")),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create")),
    )

    assert provision_agentic_resources.ensure_supervisor_agent(
        workspace,
        display_name="Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
        expected_creator_application_id="runtime-client",
        assert_single_writer=_assert_single_writer,
    ) == SupervisorAgentBinding(
        supervisor_id="canonical-id",
        display_name="Mortgage Growth Agent",
        endpoint="mas-canonical",
    )


def test_supervisor_binding_rejects_name_handoff_after_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = {
        "supervisor_agent_id": "selected-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "selected-endpoint",
        "creator": "runtime-client",
    }
    renamed = {
        **selected,
        "display_name": "renamed-away",
    }
    intruder = {
        "supervisor_agent_id": "intruder-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "intruder-endpoint",
        "creator": "runtime-client",
    }
    reads = 0

    def inventories() -> list[dict[str, str]]:
        nonlocal reads
        reads += 1
        return [selected] if reads == 1 else [renamed, intruder]

    monkeypatch.setattr(
        provision_agentic_resources,
        "_supervisor_agents",
        inventories,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="inventory tuple changed"):
        provision_agentic_resources.ensure_supervisor_agent(
            _supervisor_workspace(),
            display_name="Mortgage Growth Agent",
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            assert_single_writer=_assert_single_writer,
        )


def test_fresh_supervisor_uses_canonical_name_without_replacement_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], object | None]] = []
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [])
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )

    def run(args: list[str], *, input_json: object | None = None) -> dict[str, str]:
        calls.append((args, input_json))
        if args[:2] == ["supervisor-agents", "create-supervisor-agent"]:
            assert isinstance(input_json, dict)
            assert input_json["display_name"] == "Mortgage Growth Agent"
            return {"supervisor_agent_id": "fresh-id", "endpoint_name": "mas-fresh"}
        return {
            "supervisor_agent_id": "fresh-id",
            "endpoint_name": "mas-fresh",
            "creator": "runtime-client",
        }

    monkeypatch.setattr(provision_agentic_resources, "_run", run)

    with pytest.raises(RuntimeError, match="signed prepare/create/claim/complete"):
        provision_agentic_resources.ensure_supervisor_agent(
            _supervisor_workspace(),
            display_name="Mortgage Growth Agent",
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            assert_single_writer=_assert_single_writer,
        )
    assert calls == []


def test_outer_only_query_access_reuses_exact_managed_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = {
        "supervisor_agent_id": "canonical-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-canonical",
        "creator": "runtime-client",
    }
    workspace = _supervisor_workspace(
        {
            "mas-canonical-id": ServingEndpointPermissions(
                access_control_list=[
                    _serving_permission("runtime-client", "CAN_MANAGE"),
                    _serving_group_permission(
                        managed_query_group_name(
                            endpoint_id="mas-canonical-id",
                            application_id="proxy-client",
                        ),
                        "CAN_QUERY",
                    ),
                ]
            ),
            "outer-gateway-id": ServingEndpointPermissions(
                access_control_list=[_serving_permission("app-client", "CAN_QUERY")]
            ),
        }
    )
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [canonical])
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("exact Supervisor must not mutate"),
    )

    binding = provision_agentic_resources.ensure_supervisor_agent(
        workspace,
        display_name="Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
        expected_creator_application_id="runtime-client",
        expected_query_application_id="proxy-client",
        assert_single_writer=_assert_single_writer,
    )

    assert binding.supervisor_id == "canonical-id"
    assert workspace.serving_endpoints.permission_reads == ["mas-canonical-id"]


def test_completed_redeploy_reuses_supervisor_with_proxy_and_empty_app_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", raising=False)
    endpoint_id = "mas-canonical-id"
    canonical = {
        "supervisor_agent_id": "canonical-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-canonical",
        "creator": "runtime-client",
    }
    app_group_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id="app-client",
    )
    workspace = _supervisor_workspace(
        {
            endpoint_id: ServingEndpointPermissions(
                access_control_list=[
                    _serving_permission("runtime-client", "CAN_MANAGE"),
                    _serving_group_permission(
                        managed_query_group_name(
                            endpoint_id=endpoint_id,
                            application_id="proxy-client",
                        ),
                        "CAN_QUERY",
                    ),
                    _serving_group_permission(app_group_name, "CAN_QUERY"),
                ]
            )
        }
    )
    app_group = workspace.groups.list(filter=f"displayName eq '{app_group_name}'")[0]
    app_group.members = []
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [canonical])
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_a, **_kw: pytest.fail("completed redeploy must reuse the Supervisor"),
    )

    for _attempt in range(2):
        binding = provision_agentic_resources.ensure_supervisor_agent(
            workspace,
            display_name="Mortgage Growth Agent",
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            expected_query_application_id="proxy-client",
            approved_query_application_ids=("app-client",),
            assert_single_writer=_assert_single_writer,
        )
        assert binding.supervisor_id == "canonical-id"


@pytest.mark.parametrize(
    "entry",
    (
        _serving_group_permission("workspace-users", "CAN_QUERY"),
        _serving_group_permission(
            managed_query_group_name(
                endpoint_id="another-endpoint-id",
                application_id="proxy-client",
            ),
            "CAN_QUERY",
        ),
        _serving_user_permission("operator@example.com", "CAN_MANAGE"),
    ),
)
def test_direct_non_runtime_principal_requires_managed_query_rotation(
    entry: ServingEndpointAccessControlResponse,
) -> None:
    workspace = _supervisor_workspace(
        {
            "mas-canonical-id": ServingEndpointPermissions(
                access_control_list=[
                    _serving_permission("runtime-client", "CAN_MANAGE"),
                    entry,
                ]
            )
        }
    )

    assert supervisor_endpoint_requires_managed_query_rotation(
        workspace,
        endpoint_name="mas-canonical",
        runtime_application_id="runtime-client",
        managed_query_application_id="proxy-client",
    )


def test_proxy_direct_query_rotates_to_mq1_and_preserves_complete_blue_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = {
        "supervisor_agent_id": "blue-id",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": "mas-blue",
        "creator": "runtime-client",
        "create_time": "blue-time",
    }
    replacement_name = managed_query_supervisor_replacement_name(
        "Mortgage Growth Agent",
        genie_space_id="space-123",
        catalog="mip",
    )
    workspace = _supervisor_workspace(
        {
            "mas-blue-id": ServingEndpointPermissions(
                access_control_list=[
                    _serving_permission("runtime-client", "CAN_MANAGE"),
                    _serving_permission("proxy-client", "CAN_QUERY"),
                ]
            )
        }
    )
    calls: list[tuple[list[str], object | None]] = []
    monkeypatch.setattr(provision_agentic_resources, "_supervisor_agents", lambda: [canonical])
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda *_args, **_kwargs: None,
    )

    def run(args: list[str], *, input_json: object | None = None) -> dict[str, str]:
        calls.append((args, input_json))
        if args[:2] == ["supervisor-agents", "create-supervisor-agent"]:
            assert isinstance(input_json, dict)
            assert input_json["display_name"] == replacement_name
            return {"supervisor_agent_id": "green-id", "endpoint_name": "mas-green"}
        return {
            "supervisor_agent_id": "green-id",
            "endpoint_name": "mas-green",
            "creator": "runtime-client",
        }

    monkeypatch.setattr(provision_agentic_resources, "_run", run)

    with pytest.raises(RuntimeError, match="signed prepare/create/claim/complete"):
        provision_agentic_resources.ensure_supervisor_agent(
            workspace,
            display_name="Mortgage Growth Agent",
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            assert_single_writer=_assert_single_writer,
        )
    assert workspace.serving_endpoints.permissions["mas-blue-id"].access_control_list
    assert calls == []


def test_mq1_retry_reuses_safe_candidate_and_preserves_blue_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display_name = "Mortgage Growth Agent"
    replacement_name = managed_query_supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    canonical = {
        "supervisor_agent_id": "blue-id",
        "display_name": display_name,
        "endpoint_name": "mas-blue",
        "creator": "runtime-client",
        "create_time": "blue-time",
    }
    candidate = {
        "supervisor_agent_id": "green-id",
        "display_name": replacement_name,
        "endpoint_name": "mas-green",
        "creator": "runtime-client",
    }
    workspace = _supervisor_workspace(
        {
            "mas-blue-id": ServingEndpointPermissions(
                access_control_list=[_serving_permission("proxy-client", "CAN_QUERY")]
            ),
            "mas-green-id": ServingEndpointPermissions(
                access_control_list=[
                    _serving_permission("runtime-client", "CAN_MANAGE"),
                    _serving_group_permission(
                        managed_query_group_name(
                            endpoint_id="mas-green-id",
                            application_id="proxy-client",
                        ),
                        "CAN_QUERY",
                    ),
                ]
            ),
        }
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_supervisor_agents",
        lambda: [canonical, candidate],
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("safe candidate must be reused"),
    )

    binding = provision_agentic_resources.ensure_supervisor_agent(
        workspace,
        display_name=display_name,
        genie_space_id="space-123",
        catalog="mip",
        expected_creator_application_id="runtime-client",
        expected_query_application_id="proxy-client",
        assert_single_writer=_assert_single_writer,
    )

    assert binding.replaced_supervisor_id == "blue-id"
    assert binding.replaced_supervisor_endpoint == "mas-blue"
    assert binding.replaced_supervisor_creator == "runtime-client"
    assert binding.replaced_supervisor_create_time == "blue-time"
    assert binding.supervisor_id == "green-id"
    assert workspace.serving_endpoints.permission_reads == [
        "mas-blue-id",
        "mas-green-id",
    ]


def test_mq1_candidate_with_legacy_query_access_fails_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display_name = "Mortgage Growth Agent"
    replacement_name = managed_query_supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    canonical = {
        "supervisor_agent_id": "blue-id",
        "display_name": display_name,
        "endpoint_name": "mas-blue",
        "creator": "runtime-client",
    }
    candidate = {
        "supervisor_agent_id": "green-id",
        "display_name": replacement_name,
        "endpoint_name": "mas-green",
        "creator": "runtime-client",
    }
    workspace = _supervisor_workspace(
        {
            "mas-blue-id": ServingEndpointPermissions(
                access_control_list=[_serving_permission("proxy-client", "CAN_QUERY")]
            ),
            "mas-green-id": ServingEndpointPermissions(
                access_control_list=[_serving_permission("app-client", "CAN_QUERY")]
            ),
        }
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_supervisor_agents",
        lambda: [canonical, candidate],
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("unsafe candidate must not mutate"),
    )

    with pytest.raises(RuntimeError, match="retains legacy query access"):
        provision_agentic_resources.ensure_supervisor_agent(
            workspace,
            display_name=display_name,
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            assert_single_writer=_assert_single_writer,
        )


def test_fresh_retry_rotates_signed_blue_legacy_replacement_in_one_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display_name = "Mortgage Growth Agent"
    replacement_name = supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    managed_query_name = managed_query_supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    blue = {
        "supervisor_agent_id": "blue-id",
        "display_name": replacement_name,
        "endpoint_name": "mas-blue",
        "creator": "runtime-client",
        "create_time": "blue-time",
    }
    agents = [blue]
    workspace = _supervisor_workspace(
        {
            "mas-blue-id": ServingEndpointPermissions(
                access_control_list=[
                    _serving_permission("runtime-client", "CAN_MANAGE"),
                    _serving_permission("app-client", "CAN_QUERY"),
                ]
            )
        }
    )
    mutations: list[str] = []
    monkeypatch.setattr(
        provision_agentic_resources,
        "_supervisor_agents",
        lambda: agents,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_ensure_supervisor_tools",
        lambda *_args, **_kwargs: None,
    )

    def run_no_json(args: list[str], **_kwargs: object) -> str:
        assert args == [
            "supervisor-agents",
            "update-supervisor-agent",
            "supervisor-agents/blue-id",
            "display_name",
            display_name,
        ]
        mutations.append("rename-blue-canonical")
        blue["display_name"] = display_name
        return ""

    def run(args: list[str], *, input_json: object | None = None) -> dict[str, str]:
        if args[:2] == ["supervisor-agents", "create-supervisor-agent"]:
            assert isinstance(input_json, dict)
            assert input_json["display_name"] == managed_query_name
            mutations.append("create-mq1")
            return {
                "supervisor_agent_id": "green-id",
                "endpoint_name": "mas-green",
            }
        assert args[:2] == ["supervisor-agents", "get-supervisor-agent"]
        return {
            "supervisor_agent_id": "green-id",
            "display_name": managed_query_name,
            "endpoint_name": "mas-green",
            "creator": "runtime-client",
        }

    monkeypatch.setattr(provision_agentic_resources, "_run_no_json", run_no_json)
    monkeypatch.setattr(provision_agentic_resources, "_run", run)

    with pytest.raises(RuntimeError, match="signed prepare/create/claim/complete"):
        provision_agentic_resources.ensure_supervisor_agent(
            workspace,
            display_name=display_name,
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            expected_query_application_id="proxy-client",
            approved_query_application_ids=("app-client",),
            signed_blue_supervisor_pin={
                "supervisor_id": "blue-id",
                "endpoint": "mas-blue",
                "endpoint_id": "mas-blue-id",
                "creator": "runtime-client",
            },
            assert_single_writer=_assert_single_writer,
        )
    assert mutations == ["rename-blue-canonical"]


@pytest.mark.parametrize(
    ("pin_override", "message"),
    (
        ({"supervisor_id": "other-id"}, "differs from signed-blue identity"),
        ({"endpoint_id": "other-endpoint-id"}, "endpoint identity drifted"),
    ),
)
def test_signed_blue_legacy_replacement_drift_fails_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    pin_override: dict[str, str],
    message: str,
) -> None:
    display_name = "Mortgage Growth Agent"
    replacement_name = supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    candidate = {
        "supervisor_agent_id": "blue-id",
        "display_name": replacement_name,
        "endpoint_name": "mas-blue",
        "creator": "runtime-client",
        "create_time": "blue-time",
    }
    workspace = _supervisor_workspace(
        {
            "mas-blue-id": ServingEndpointPermissions(
                access_control_list=[_serving_permission("app-client", "CAN_QUERY")]
            )
        }
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_supervisor_agents",
        lambda: [candidate],
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run_no_json",
        lambda *_args, **_kwargs: pytest.fail("drifted signed blue must not mutate"),
    )
    pin = {
        "supervisor_id": "blue-id",
        "endpoint": "mas-blue",
        "endpoint_id": "mas-blue-id",
        "creator": "runtime-client",
    }
    pin.update(pin_override)

    with pytest.raises(RuntimeError, match=message):
        provision_agentic_resources.ensure_supervisor_agent(
            workspace,
            display_name=display_name,
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            expected_query_application_id="proxy-client",
            approved_query_application_ids=("app-client",),
            signed_blue_supervisor_pin=pin,
            assert_single_writer=_assert_single_writer,
        )


def test_signed_blue_legacy_replacement_name_ambiguity_fails_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display_name = "Mortgage Growth Agent"
    replacement_name = supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    candidates = [
        {
            "supervisor_agent_id": supervisor_id,
            "display_name": replacement_name,
            "endpoint_name": endpoint,
            "creator": "runtime-client",
        }
        for supervisor_id, endpoint in (
            ("blue-id", "mas-blue"),
            ("ambiguous-id", "mas-ambiguous"),
        )
    ]
    monkeypatch.setattr(
        provision_agentic_resources,
        "_supervisor_agents",
        lambda: candidates,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run_no_json",
        lambda *_args, **_kwargs: pytest.fail("ambiguous candidates must not mutate"),
    )

    with pytest.raises(RuntimeError, match="multiple Supervisor agents"):
        provision_agentic_resources.ensure_supervisor_agent(
            _supervisor_workspace(),
            display_name=display_name,
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            expected_query_application_id="proxy-client",
            signed_blue_supervisor_pin={
                "supervisor_id": "blue-id",
                "endpoint": "mas-blue",
                "endpoint_id": "mas-blue-id",
                "creator": "runtime-client",
            },
            assert_single_writer=_assert_single_writer,
        )


@pytest.mark.parametrize("drift", ("creator", "contract"))
def test_mq1_candidate_identity_or_contract_drift_fails_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    display_name = "Mortgage Growth Agent"
    replacement_name = managed_query_supervisor_replacement_name(
        display_name,
        genie_space_id="space-123",
        catalog="mip",
    )
    canonical = {
        "supervisor_agent_id": "blue-id",
        "display_name": display_name,
        "endpoint_name": "mas-blue",
        "creator": "runtime-client",
    }
    candidate = {
        "supervisor_agent_id": "green-id",
        "display_name": replacement_name,
        "endpoint_name": "mas-green",
        "creator": "human@example.com" if drift == "creator" else "runtime-client",
    }
    workspace = _supervisor_workspace(
        {
            "mas-blue-id": ServingEndpointPermissions(
                access_control_list=[_serving_permission("proxy-client", "CAN_QUERY")]
            ),
            "mas-green-id": ServingEndpointPermissions(access_control_list=[]),
        }
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_supervisor_agents",
        lambda: [canonical, candidate],
    )

    def exact(supervisor_id: str, **_kwargs: object) -> None:
        if drift == "contract" and supervisor_id == "green-id":
            raise provision_agentic_resources.SupervisorContractDrift("candidate drift")

    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_exact_supervisor_contract",
        exact,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("drifted candidate must not mutate"),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "creator .* is not dedicated agent runtime"
            if drift == "creator"
            else "immutable green Supervisor candidate drifted"
        ),
    ):
        provision_agentic_resources.ensure_supervisor_agent(
            workspace,
            display_name=display_name,
            genie_space_id="space-123",
            catalog="mip",
            expected_creator_application_id="runtime-client",
            assert_single_writer=_assert_single_writer,
        )


def test_supervisor_tool_convergence_rejects_unexpected_live_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def run(args: list[str], *, input_json: object | None = None) -> object:
        nonlocal calls
        _ = input_json
        calls += 1
        if args[1] == "list-tools":
            return [{"tool_id": "unreviewed_tool", "tool_type": "uc_function"}]
        return []

    monkeypatch.setattr(provision_agentic_resources, "_run", run)
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run_no_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mutate")),
    )

    with pytest.raises(RuntimeError, match="unexpected tools"):
        provision_agentic_resources._ensure_supervisor_tools(
            "supervisor-1",
            genie_space_id="space-123",
            catalog="mip",
            assert_single_writer=_assert_single_writer,
        )
    assert calls == 2


def test_exact_supervisor_tools_are_idempotent_and_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "tool_id": tool_id,
            "tool_type": tool_type,
            "description": description,
            **body,
        }
        for tool_id, tool_type, description, body in (
            provision_agentic_resources._supervisor_tool_specs(
                genie_space_id="space-123",
                catalog="mip",
            )
        )
    ]
    calls: list[str] = []

    def run(args: list[str], *, input_json: object | None = None) -> object:
        assert input_json is None
        calls.append(args[1])
        if args[1] == "list-tools":
            return rows
        if args[1] == "list-examples":
            return []
        raise AssertionError(args)

    monkeypatch.setattr(provision_agentic_resources, "_run", run)
    monkeypatch.setattr(
        provision_agentic_resources,
        "_run_no_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mutate")),
    )

    provision_agentic_resources._ensure_supervisor_tools(
        "supervisor-1",
        genie_space_id="space-123",
        catalog="mip",
        assert_single_writer=_assert_single_writer,
    )

    assert calls == ["list-tools", "list-examples", "list-tools", "list-examples"]


def test_converge_app_gateway_permissions_grants_outer_and_revokes_bypasses(monkeypatch) -> None:
    grants: list[tuple[str, str]] = []
    revocations: list[tuple[str, str, bool]] = []
    workspace = type(
        "Workspace",
        (),
        {
            "apps": type(
                "Apps",
                (),
                {
                    "get": lambda _self, name: {
                        "name": name,
                        "service_principal_client_id": "app-sp-123",
                    }
                },
            )()
        },
    )()
    monkeypatch.setattr(
        provision_agentic_resources,
        "grant_direct_can_query",
        lambda _workspace, *, endpoint_name, service_principal: grants.append(
            (endpoint_name, service_principal)
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "revoke_direct_permissions",
        lambda _workspace, *, endpoint_name, service_principal, missing_ok: (
            revocations.append((endpoint_name, service_principal, missing_ok)) or True
        ),
    )

    provision_agentic_resources._converge_app_gateway_permissions(
        workspace,  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        gateway_endpoint="mip-growth-agent-gateway",
        supervisor_endpoint="mas-agent-endpoint",
        app_name="mip-app",
    )

    assert grants == [("mip-growth-agent-gateway", "app-sp-123")]
    assert set(revocations) == {
        ("mas-agent-endpoint", "app-sp-123", False),
        ("mip-agent-gateway", "app-sp-123", True),
    }


def test_ensure_synced_tables_uses_configured_source_catalog(monkeypatch) -> None:
    created_sources: list[str] = []

    class _Database:
        def get_synced_database_table(self, name: str) -> None:
            _ = name
            raise provision_agentic_resources.NotFound("missing")

        def create_synced_database_table(self, table: Any) -> None:
            created_sources.append(table.spec.source_table_full_name)

    class _Workspace:
        database = _Database()

    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_synced_table_online",
        lambda *_args, **_kwargs: None,
    )

    tables = provision_agentic_resources.ensure_synced_tables(
        _Workspace(),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        source_catalog="acme_mip",
        catalog="acme_app_state",
        schema="mip_sync",
        database_instance="mip-app-state",
        logical_database="mip_app_state",
        storage_catalog="acme_mip",
        storage_schema="app",
        timeout_s=1,
    )

    assert tables == ("source_readiness", "segment_population", "funnel_snapshot_daily")
    assert created_sources == [
        "acme_mip.gold.source_readiness",
        "acme_mip.gold.segment_population",
        "acme_mip.gold.funnel_snapshot_daily",
    ]


def test_ensure_synced_tables_reasserts_lease_immediately_before_create(monkeypatch) -> None:
    mutations: list[str] = []

    class _Database:
        def get_synced_database_table(self, _name: str) -> None:
            raise provision_agentic_resources.NotFound("missing")

        def create_synced_database_table(self, _table: Any) -> None:
            mutations.append("create")

    workspace = type("Workspace", (), {"database": _Database()})()

    with pytest.raises(RuntimeError, match="lease lost"):
        provision_agentic_resources.ensure_synced_tables(
            workspace,  # type: ignore[arg-type]
            assert_single_writer=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
            table_definitions=(provision_agentic_resources.DEFAULT_SYNC_TABLES[0],),
        )

    assert mutations == []


def test_ensure_synced_tables_validates_existing_source_catalog(monkeypatch) -> None:
    checked: list[str] = []

    class _ExistingTable:
        def __init__(self, source: str, keys: list[str]) -> None:
            self.database_instance_name = "mip-app-state"
            self.logical_database_name = "mip_app_state"
            self.effective_database_instance_name = "mip-app-state"
            self.effective_logical_database_name = "mip_app_state"
            self.spec = SyncedTableSpec(
                source_table_full_name=source,
                primary_key_columns=keys,
                scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
                new_pipeline_spec=NewPipelineSpec(storage_catalog="acme_mip", storage_schema="app"),
            )

    class _Database:
        def get_synced_database_table(self, name: str) -> _ExistingTable:
            table = name.rsplit(".", 1)[-1]
            checked.append(name)
            keys = {
                "source_readiness": ["source_name"],
                "segment_population": ["segment_code", "state"],
                "funnel_snapshot_daily": ["snapshot_date", "state", "segment_code"],
            }[table]
            return _ExistingTable(f"acme_mip.gold.{table}", keys)

        def create_synced_database_table(self, table: Any) -> None:
            raise AssertionError(f"existing synced table should not be recreated: {table}")

    class _Workspace:
        database = _Database()

    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_synced_table_online",
        lambda *_args, **_kwargs: None,
    )

    assert provision_agentic_resources.ensure_synced_tables(
        _Workspace(),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        source_catalog="acme_mip",
        catalog="acme_app_state",
        schema="mip_sync",
        database_instance="mip-app-state",
        logical_database="mip_app_state",
        storage_catalog="acme_mip",
        storage_schema="app",
        timeout_s=1,
    ) == ("source_readiness", "segment_population", "funnel_snapshot_daily")
    assert checked == [
        "acme_app_state.mip_sync.source_readiness",
        "acme_app_state.mip_sync.segment_population",
        "acme_app_state.mip_sync.funnel_snapshot_daily",
    ]


def test_ensure_synced_tables_rejects_existing_wrong_source_catalog(monkeypatch) -> None:
    class _ExistingTable:
        database_instance_name = "mip-app-state"
        logical_database_name = "mip_app_state"
        effective_database_instance_name = "mip-app-state"
        effective_logical_database_name = "mip_app_state"
        spec = SyncedTableSpec(
            source_table_full_name="mip.gold.source_readiness",
            primary_key_columns=["source_name"],
            scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
        )

    class _Database:
        def get_synced_database_table(self, name: str) -> _ExistingTable:
            _ = name
            return _ExistingTable()

        def create_synced_database_table(self, table: Any) -> None:
            raise AssertionError(f"stale existing table should not be recreated silently: {table}")

    class _Workspace:
        database = _Database()

    with pytest.raises(RuntimeError, match="expected acme_mip.gold.source_readiness"):
        provision_agentic_resources.ensure_synced_tables(
            _Workspace(),  # type: ignore[arg-type]
            assert_single_writer=lambda: None,
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
        )


@pytest.mark.parametrize(
    ("database_instance", "logical_database", "message"),
    [
        ("wrong-instance", "mip_app_state", "wrong-instance; expected mip-app-state"),
        ("mip-app-state", "wrong_database", "wrong_database; expected mip_app_state"),
    ],
)
def test_ensure_synced_tables_rejects_existing_wrong_lakebase_target(
    monkeypatch,
    database_instance: str,
    logical_database: str,
    message: str,
) -> None:
    class _ExistingTable:
        spec = SyncedTableSpec(
            source_table_full_name="acme_mip.gold.source_readiness",
            primary_key_columns=["source_name"],
            scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
        )

        def __init__(self) -> None:
            self.database_instance_name = None
            self.logical_database_name = None
            self.effective_database_instance_name = database_instance
            self.effective_logical_database_name = logical_database

    class _Database:
        def get_synced_database_table(self, name: str) -> _ExistingTable:
            _ = name
            return _ExistingTable()

        def create_synced_database_table(self, table: Any) -> None:
            raise AssertionError(f"wrong-target table should not be recreated silently: {table}")

    class _Workspace:
        database = _Database()

    with pytest.raises(RuntimeError, match=re.escape(message)):
        provision_agentic_resources.ensure_synced_tables(
            _Workspace(),  # type: ignore[arg-type]
            assert_single_writer=lambda: None,
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
            table_definitions=(provision_agentic_resources.DEFAULT_SYNC_TABLES[0],),
        )


def test_existing_registered_catalog_uses_authoritative_effective_lakebase_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExistingTable:
        database_instance_name = None
        logical_database_name = None
        effective_database_instance_name = "mip-app-state"
        effective_logical_database_name = "mip_app_state"
        spec = SyncedTableSpec(
            source_table_full_name="acme_mip.gold.source_readiness",
            primary_key_columns=["source_name"],
            scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
        )

    class _Database:
        def get_synced_database_table(self, name: str) -> _ExistingTable:
            assert name == "acme_app_state.mip_sync.source_readiness"
            return _ExistingTable()

        def create_synced_database_table(self, table: Any) -> None:
            raise AssertionError(f"existing registered table must be reused: {table}")

    class _Workspace:
        database = _Database()

    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_synced_table_online",
        lambda *_args, **_kwargs: None,
    )

    assert provision_agentic_resources.ensure_synced_tables(
        _Workspace(),  # type: ignore[arg-type]
        assert_single_writer=lambda: None,
        source_catalog="acme_mip",
        catalog="acme_app_state",
        schema="mip_sync",
        database_instance="mip-app-state",
        logical_database="mip_app_state",
        storage_catalog="acme_mip",
        storage_schema="app",
        timeout_s=1,
        table_definitions=(provision_agentic_resources.DEFAULT_SYNC_TABLES[0],),
    ) == ("source_readiness",)


def test_existing_synced_table_rejects_configured_target_drift_even_when_effective_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExistingTable:
        database_instance_name = "wrong-configured-instance"
        logical_database_name = "mip_app_state"
        effective_database_instance_name = "mip-app-state"
        effective_logical_database_name = "mip_app_state"
        spec = SyncedTableSpec(
            source_table_full_name="acme_mip.gold.source_readiness",
            primary_key_columns=["source_name"],
            scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
        )

    class _Database:
        def get_synced_database_table(self, _name: str) -> _ExistingTable:
            return _ExistingTable()

    class _Workspace:
        database = _Database()

    with pytest.raises(RuntimeError, match="configured for Lakebase instance"):
        provision_agentic_resources.ensure_synced_tables(
            _Workspace(),  # type: ignore[arg-type]
            assert_single_writer=lambda: None,
            source_catalog="acme_mip",
            catalog="acme_app_state",
            schema="mip_sync",
            database_instance="mip-app-state",
            logical_database="mip_app_state",
            storage_catalog="acme_mip",
            storage_schema="app",
            timeout_s=1,
            table_definitions=(provision_agentic_resources.DEFAULT_SYNC_TABLES[0],),
        )


def test_converge_app_gateway_permissions_fails_without_app_identity() -> None:
    workspace = type(
        "Workspace",
        (),
        {"apps": type("Apps", (), {"get": lambda _self, _name: {}})()},
    )()

    with pytest.raises(RuntimeError, match="app service principal not found"):
        provision_agentic_resources._converge_app_gateway_permissions(
            workspace,  # type: ignore[arg-type]
            assert_single_writer=lambda: None,
            gateway_endpoint="mip-growth-agent-gateway",
            supervisor_endpoint="mas-agent-endpoint",
            app_name="mip-app",
        )


def test_main_defaults_ai_gateway_to_dedicated_endpoint(monkeypatch, tmp_path) -> None:
    out_env = tmp_path / "agentic.env"
    monkeypatch.setattr(
        provision_agentic_resources.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: _assert_single_writer,
    )

    monkeypatch.setattr(
        provision_agentic_resources,
        "WorkspaceClient",
        lambda: type(
            "Workspace",
            (),
            {
                "serving_endpoints": type(
                    "Endpoints",
                    (),
                    {
                        "get": lambda _self, _name: type(
                            "Endpoint",
                            (),
                            {"creator": "runtime", "id": "mip-supervisor-endpoint-id"},
                        )()
                    },
                )()
            },
        )(),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: ("source_readiness", "segment_population"),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda *_args, **_kwargs: SupervisorAgentBinding(
            "supervisor-1",
            "Mortgage Growth Agent",
            "mip-supervisor-endpoint",
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_current_runtime_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_runtime_creator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_serving_endpoint_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_unique_live_supervisor_binding",
        lambda *_args, **_kwargs: "mip-supervisor-endpoint-id",
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "verify_gateway_responses_agent",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "bind_gateway_runtime_resource_contract",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_converge_app_gateway_permissions",
        lambda *_args, **_kwargs: None,
    )

    created_endpoints: list[dict[str, object]] = []

    class _Deployment:
        endpoint = "mip-growth-agent-gateway"
        inference_table = "mip.audit.mip_agent_gateway_growth_agent"
        model_name = "mip.audit.mortgage_growth_supervisor_proxy"
        model_version = 7

    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_gateway_responses_agent",
        lambda *_args, **kwargs: (created_endpoints.append(kwargs) or _Deployment()),
    )

    assert (
        provision_agentic_resources.main(
            [
                "--genie-space-id",
                "space-123",
                "--expected-runtime-application-id",
                "runtime",
                "--deployment-lease-id",
                "lease-123",
                "--deployment-source-git-sha",
                "f" * 40,
                *_PROXY_ARGS,
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )

    assert created_endpoints == [
        {
            "endpoint": "mip-growth-agent-gateway",
            "endpoint_prefix": "mip-growth-agent-gateway",
            "supervisor_id": "supervisor-1",
            "upstream_endpoint": "mip-supervisor-endpoint",
            "model_name": "mip.audit.mortgage_growth_supervisor_proxy",
            "experiment_name": "mip-agent-runtime-gateway-proxy",
            "inference_catalog": "mip",
            "inference_schema": "audit",
            "inference_table_prefix": "mip_agent_gateway_growth_agent",
            "genie_space_id": "space-123",
            "expected_creator_application_id": "runtime",
            "proxy_caller_application_id": _PROXY_CLIENT_ID,
            "proxy_caller_credential_id": _PROXY_CREDENTIAL_ID,
            "proxy_caller_secret_reference": _PROXY_SECRET_REFERENCE,
            "approved_query_application_ids": (),
            "deployment_app_name": "mip-app",
            "deployment_lease_id": "lease-123",
            "deployment_source_git_sha": "f" * 40,
        }
    ]
    assert "MIP_AGENT_SERVING_ENDPOINT=mip-growth-agent-gateway" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AGENT_SUPERVISOR_ENDPOINT=mip-supervisor-endpoint" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AGENT_SUPERVISOR_ENDPOINT_ID=mip-supervisor-endpoint-id" in out_env.read_text(
        encoding="utf-8"
    )
    assert "MIP_AI_GATEWAY_ENDPOINT=mip-growth-agent-gateway" in out_env.read_text(encoding="utf-8")
    assert "MIP_AI_GATEWAY_AGENT_MODEL_VERSION=7" in out_env.read_text(encoding="utf-8")


def test_main_rejects_missing_lease_before_supervisor_or_sync_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: pytest.fail("sync must not run before lease validation"),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda *_args, **_kwargs: pytest.fail("Supervisor must not mutate before lease validation"),
    )

    with pytest.raises(ValueError, match="exact source SHA"):
        provision_agentic_resources.main(
            [
                "--genie-space-id",
                "space-123",
                "--expected-runtime-application-id",
                "runtime",
                "--out-env",
                str(tmp_path / "agentic.env"),
            ]
        )


def test_sync_only_main_rejects_lost_lease_before_sync_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)
    monkeypatch.setattr(
        provision_agentic_resources.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: pytest.fail("sync must not run after lease loss"),
    )

    with pytest.raises(RuntimeError, match="lease lost"):
        provision_agentic_resources.main(
            [
                "--skip-supervisor",
                "--skip-gateway",
                "--deployment-lease-id",
                "lease-123",
                "--deployment-source-git-sha",
                "f" * 40,
            ]
        )


def test_main_reasserts_lease_after_gateway_wait_before_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    checks = 0
    binding_calls = 0

    def check() -> None:
        nonlocal checks
        checks += 1
        if checks == 3:
            raise RuntimeError("lease disappeared during endpoint wait")

    workspace = type(
        "Workspace",
        (),
        {
            "serving_endpoints": type(
                "Endpoints",
                (),
                {
                    "get": lambda _self, _name: type(
                        "Endpoint",
                        (),
                        {"creator": "runtime", "id": "mip-supervisor-endpoint-id"},
                    )()
                },
            )()
        },
    )()
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        provision_agentic_resources.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: check,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_current_runtime_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_runtime_creator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda *_args, **_kwargs: SupervisorAgentBinding(
            "supervisor-1", "Mortgage Growth Agent", "mip-supervisor-endpoint"
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_serving_endpoint_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_unique_live_supervisor_binding",
        lambda *_args, **_kwargs: "mip-supervisor-endpoint-id",
    )

    class _Deployment:
        endpoint = "mip-growth-agent-gateway"
        inference_table = "mip.audit.mip_agent_gateway_growth_agent"
        model_name = "mip.audit.mortgage_growth_supervisor_proxy"
        model_version = 7

    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_gateway_responses_agent",
        lambda *_args, **_kwargs: _Deployment(),
    )

    def bind(*_args: object, **_kwargs: object) -> None:
        nonlocal binding_calls
        binding_calls += 1

    monkeypatch.setattr(
        provision_agentic_resources,
        "bind_gateway_runtime_resource_contract",
        bind,
    )

    with pytest.raises(RuntimeError, match="lease disappeared during endpoint wait"):
        provision_agentic_resources.main(
            [
                "--skip-sync",
                "--genie-space-id",
                "space-123",
                "--expected-runtime-application-id",
                "runtime",
                "--deployment-lease-id",
                "lease-123",
                "--deployment-source-git-sha",
                "f" * 40,
                *_PROXY_ARGS,
                "--out-env",
                str(tmp_path / "agentic.env"),
            ]
        )

    assert checks == 3
    assert binding_calls == 0


def test_isolated_skip_sync_child_rejects_unreviewed_sync_table_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    out_env = tmp_path / "agentic.env"
    monkeypatch.setattr(provision_agentic_resources, "WorkspaceClient", object)

    with pytest.raises(
        ValueError,
        match="names without reviewed source/key contracts: daily_funnel_v2, source_status_v2",
    ):
        provision_agentic_resources.main(
            [
                "--skip-sync",
                "--skip-supervisor",
                "--skip-gateway",
                "--lakebase-catalog",
                "acme_app_state",
                "--lakebase-schema",
                "acme_sync",
                "--lakebase-sync-tables",
                "source_status_v2,daily_funnel_v2",
                "--out-env",
                str(out_env),
            ]
        )
    assert not out_env.exists()


def test_main_rejects_gateway_equal_to_supervisor_before_proxy_mutation(monkeypatch) -> None:
    monkeypatch.setattr(
        provision_agentic_resources.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: _assert_single_writer,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "WorkspaceClient",
        lambda: type(
            "Workspace",
            (),
            {
                "serving_endpoints": type(
                    "Endpoints",
                    (),
                    {
                        "get": lambda _self, _name: type(
                            "Endpoint",
                            (),
                            {"creator": "runtime", "id": "same-endpoint-id"},
                        )()
                    },
                )()
            },
        )(),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_synced_tables",
        lambda *_args, **_kwargs: ("source_readiness",),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_supervisor_agent",
        lambda *_args, **_kwargs: SupervisorAgentBinding(
            "supervisor-1", "Mortgage Growth Agent", "same-endpoint"
        ),
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_current_runtime_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_runtime_creator",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "_wait_serving_endpoint_ready",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "assert_unique_live_supervisor_binding",
        lambda *_args, **_kwargs: "same-endpoint-id",
    )
    monkeypatch.setattr(
        provision_agentic_resources,
        "ensure_gateway_responses_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mutate")),
    )

    with pytest.raises(ValueError, match="self-recursive proxy"):
        provision_agentic_resources.main(
            [
                "--genie-space-id",
                "space-123",
                "--expected-runtime-application-id",
                "runtime",
                "--gateway-endpoint",
                "same-endpoint",
                "--deployment-lease-id",
                "lease-123",
                "--deployment-source-git-sha",
                "f" * 40,
                *_PROXY_ARGS,
            ]
        )
