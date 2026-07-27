from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import unquote

import pytest
from databricks.sdk.errors import PermissionDenied, ResourceDoesNotExist
from databricks.sdk.service.apps import ComputeState

from tools.databricks import verify_agent_proxy_identity_boundary as boundary
from tools.databricks.agent_proxy_capability_group_access import (
    managed_agent_proxy_group_external_id,
    managed_agent_proxy_group_name,
)
from tools.databricks.serving_query_group_access import (
    managed_query_group_external_id,
    managed_query_group_name,
)
from tools.databricks.verify_agent_proxy_identity_boundary import (
    AgentProxyBoundaryInventory,
    _expect_denied,
    _is_denied,
    _verify_target_supervisor_query,
    _verify_warehouse_denial,
    collect_admin_inventory,
    verify_boundary,
)

PROXY_ID = "proxy-client"
TARGET_WAREHOUSE = "warehouse-target"
TARGET_SUPERVISOR = "supervisor-target"
TARGET_GENIE = "genie-target"
PROXY_QUERY_GROUP = managed_query_group_name(
    endpoint_id="gateway-id",
    application_id=PROXY_ID,
)


def _proxy_main_args(
    account_host: str = "https://accounts.cloud.databricks.com",
) -> list[str]:
    return [
        "--expected-application-id",
        PROXY_ID,
        "--account-host",
        account_host,
        "--account-id",
        "account-id",
        "--app-name",
        "mip-app",
        "--app-url",
        "https://mip-app.databricksapps.com",
        "--lakebase-instance",
        "lakebase-target",
        "--warehouse-id",
        TARGET_WAREHOUSE,
        "--supervisor-id",
        TARGET_SUPERVISOR,
        "--supervisor-endpoint",
        "gateway",
        "--supervisor-endpoint-id",
        "gateway-id",
        "--genie-space-id",
        TARGET_GENIE,
        "--allow-attested-app-401",
    ]


@pytest.mark.parametrize(
    "account_host",
    (
        "https://accounts.cloud.databricks.com.evil.example",
        "https://accounts.cloud.databricks.com/path",
    ),
)
def test_proxy_main_rejects_account_origin_before_constructing_clients(
    monkeypatch: pytest.MonkeyPatch,
    account_host: str,
) -> None:
    monkeypatch.setattr(
        boundary,
        "WorkspaceClient",
        lambda: pytest.fail("workspace client constructed before host validation"),
    )

    with pytest.raises(RuntimeError, match="reviewed Databricks account origin"):
        boundary.main(_proxy_main_args(account_host))


def test_proxy_main_rejects_workspace_origin_before_admin_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", PROXY_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", "proxy-secret")
    monkeypatch.setattr(
        boundary,
        "WorkspaceClient",
        lambda: SimpleNamespace(config=SimpleNamespace(host="https://attacker.invalid")),
    )
    monkeypatch.setattr(
        boundary,
        "collect_admin_inventory",
        lambda *_args, **_kwargs: pytest.fail(
            "admin inventory called before workspace host validation"
        ),
    )

    with pytest.raises(RuntimeError, match="reviewed HTTPS Databricks origin"):
        boundary.main(_proxy_main_args())


def test_main_scrubs_deployer_aliases_before_exact_proxy_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "pat")
    monkeypatch.setenv("DATABRICKS_TOKEN", "admin-token")
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_CLIENT_SECRET", raising=False)
    monkeypatch.setenv(
        "MIP_DEPLOYER_DATABRICKS_HOST",
        "https://workspace.cloud.databricks.com",
    )
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_TOKEN", "deployer-token")
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_PROFILE", "DEFAULT")
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", PROXY_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", "proxy-secret")
    auth_at_construction: list[tuple[str, str, str, str]] = []
    clients: list[object] = []

    def workspace_client() -> object:
        auth_at_construction.append(
            (
                os.environ.get("DATABRICKS_AUTH_TYPE", ""),
                os.environ.get("DATABRICKS_TOKEN", ""),
                os.environ.get("DATABRICKS_CLIENT_ID", ""),
                os.environ.get("MIP_DEPLOYER_DATABRICKS_TOKEN", ""),
            )
        )
        client = SimpleNamespace(
            config=SimpleNamespace(host="https://workspace.cloud.databricks.com")
        )
        clients.append(client)
        return client

    account_args: dict[str, object] = {}
    account = object()

    def account_client(**kwargs: object) -> object:
        account_args.update(kwargs)
        return account

    observed: dict[str, object] = {}

    def verify(**kwargs: object) -> None:
        observed.update(kwargs)

    monkeypatch.setattr(boundary, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(boundary, "AccountClient", account_client)
    monkeypatch.setattr(boundary, "collect_admin_inventory", lambda *_args, **_kwargs: _inventory())
    monkeypatch.setattr(boundary, "verify_boundary", verify)

    assert boundary.main(_proxy_main_args()) == 0

    assert auth_at_construction == [
        ("pat", "admin-token", "", "deployer-token"),
        ("oauth-m2m", "", PROXY_ID, ""),
    ]
    assert account_args["host"] == "https://accounts.cloud.databricks.com"
    assert account_args["client_id"] == PROXY_ID
    assert account_args["client_secret"] == "proxy-secret"
    assert observed["workspace"] is clients[1]
    assert observed["account"] is account
    assert observed["admin_workspace"] is clients[0]
    assert observed["allow_attested_app_401"] is True
    assert "DATABRICKS_AGENT_PROXY_CLIENT_SECRET" not in os.environ
    assert "MIP_DEPLOYER_DATABRICKS_HOST" not in os.environ
    assert "MIP_DEPLOYER_DATABRICKS_PROFILE" not in os.environ


def _denied(*_args: object, **_kwargs: object) -> object:
    raise PermissionDenied("permission denied")


class _SupervisorApi:
    def __init__(
        self,
        *,
        expose_target: bool = False,
        expose_non_target: bool = False,
        target_query_succeeds: bool = True,
        target_query_has_payload: bool = True,
        target_query_failures: int = 0,
        target_query_error: BaseException | None = None,
        target_query_response: object | None = None,
        target_genie_permissions_succeeds: bool = False,
    ) -> None:
        self.expose_target = expose_target
        self.expose_non_target = expose_non_target
        self.target_query_succeeds = target_query_succeeds
        self.target_query_has_payload = target_query_has_payload
        self.target_query_failures = target_query_failures
        self.target_query_error = target_query_error
        self.target_query_response = target_query_response
        self.target_genie_permissions_succeeds = target_genie_permissions_succeeds
        self.target_query_calls = 0
        self.paths: list[str] = []

    def do(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
    ) -> object:
        self.paths.append(path)
        if (
            method == "GET"
            and path == f"/api/2.0/permissions/genie/{TARGET_GENIE}"
            and self.target_genie_permissions_succeeds
        ):
            return {"access_control_list": []}
        if method == "POST" and path == "/serving-endpoints/responses":
            self.target_query_calls += 1
            if self.target_query_calls <= self.target_query_failures:
                raise self.target_query_error or TimeoutError("scaling from zero")
            if not self.target_query_succeeds:
                raise RuntimeError("target query returned an unrelated provider failure")
            assert body is not None
            assert body["model"] == "gateway"
            if self.target_query_response is not None:
                return self.target_query_response
            return {
                "id": "response-target",
                "object": "response",
                "model": "gateway",
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "output": (
                    [
                        {
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "ready"}],
                        }
                    ]
                    if self.target_query_has_payload
                    else []
                ),
            }
        assert method == "GET"
        identifier = unquote(path.rsplit("/", 1)[-1])
        if identifier == TARGET_SUPERVISOR and self.expose_target:
            return {"supervisor_agent_id": identifier}
        if identifier != TARGET_SUPERVISOR and self.expose_non_target:
            return {"supervisor_agent_id": identifier}
        return _denied()


class _Genie:
    def __init__(self, *, expose_non_target: bool = False) -> None:
        self.expose_non_target = expose_non_target

    def get_space(self, identifier: str) -> object:
        if identifier == TARGET_GENIE or self.expose_non_target:
            return SimpleNamespace(space_id=identifier)
        return _denied()


class _Statements:
    def __init__(
        self,
        *,
        succeeds: bool = False,
        error: object = "PERMISSION_DENIED",
    ) -> None:
        self.succeeds = succeeds
        self.error = error

    def execute_statement(self, **_kwargs: object) -> object:
        return SimpleNamespace(
            status=SimpleNamespace(
                state="SUCCEEDED" if self.succeeds else "FAILED",
                error=None if self.succeeds else self.error,
            )
        )


def _inventory() -> AgentProxyBoundaryInventory:
    query_binding = boundary.ManagedWorkspaceGroupBinding(
        id="managed-query-group-id",
        name=PROXY_QUERY_GROUP,
        external_id=managed_query_group_external_id(
            endpoint_id="gateway-id",
            application_id=PROXY_ID,
        ),
        resource_type="WorkspaceGroup",
    )
    supervisor_group_name = managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id=TARGET_SUPERVISOR,
        application_id=PROXY_ID,
    )
    supervisor_binding = boundary.ManagedWorkspaceGroupBinding(
        id="managed-supervisor-group-id",
        name=supervisor_group_name,
        external_id=managed_agent_proxy_group_external_id(
            resource_kind="supervisor",
            resource_id=TARGET_SUPERVISOR,
            application_id=PROXY_ID,
        ),
        resource_type="WorkspaceGroup",
    )
    genie_group_name = managed_agent_proxy_group_name(
        resource_kind="genie",
        resource_id=TARGET_GENIE,
        application_id=PROXY_ID,
    )
    genie_binding = boundary.ManagedWorkspaceGroupBinding(
        id="managed-genie-group-id",
        name=genie_group_name,
        external_id=managed_agent_proxy_group_external_id(
            resource_kind="genie",
            resource_id=TARGET_GENIE,
            application_id=PROXY_ID,
        ),
        resource_type="WorkspaceGroup",
    )
    return AgentProxyBoundaryInventory(
        app_url="https://mip-app.databricksapps.com",
        app_names=("mip-app", "unrelated-app"),
        app_urls=(
            "https://mip-app.databricksapps.com",
            "https://unrelated-app.databricksapps.com",
        ),
        metastore_id="metastore-id",
        secret_scope_names=("mip-agent-proxy", "unrelated-scope"),
        service_principal_ids=("proxy-scim", "runtime-scim"),
        lakebase_instances=("lakebase-target", "lakebase-other"),
        warehouse_ids=(TARGET_WAREHOUSE, "warehouse-other"),
        supervisor_ids=(TARGET_SUPERVISOR, "supervisor-other"),
        genie_space_ids=(TARGET_GENIE, "genie-other"),
        serving_endpoint_names=("gateway", "unrelated-endpoint"),
        foundation_endpoint_names=(),
        managed_query_group_ids=(
            "managed-query-group-id",
            "managed-supervisor-group-id",
            "managed-genie-group-id",
        ),
        reviewed_supervisor_bindings=((TARGET_SUPERVISOR, "gateway", "gateway-id"),),
        reviewed_query_group_bindings=(
            (
                "gateway-id",
                PROXY_QUERY_GROUP,
                "managed-query-group-id",
                managed_query_group_external_id(
                    endpoint_id="gateway-id",
                    application_id=PROXY_ID,
                ),
            ),
        ),
        reviewed_capability_group_bindings=(
            (
                "supervisor",
                TARGET_SUPERVISOR,
                supervisor_group_name,
                supervisor_binding.id,
                supervisor_binding.external_id,
            ),
            (
                "genie",
                TARGET_GENIE,
                genie_group_name,
                genie_binding.id,
                genie_binding.external_id,
            ),
        ),
        managed_query_group_bindings=(
            query_binding,
            supervisor_binding,
            genie_binding,
        ),
    )


def _admin_inventory_workspace(
    *,
    supervisor_endpoint: str = "gateway",
    managed_groups: tuple[object, ...] | None = None,
) -> object:
    groups = managed_groups or tuple(
        SimpleNamespace(
            id=group_id,
            display_name=name,
            external_id=external_id,
            meta=SimpleNamespace(resource_type="WorkspaceGroup"),
        )
        for group_id, name, external_id in (
            (
                "managed-query-group-id",
                PROXY_QUERY_GROUP,
                managed_query_group_external_id(
                    endpoint_id="gateway-id",
                    application_id=PROXY_ID,
                ),
            ),
            (
                "managed-supervisor-group-id",
                managed_agent_proxy_group_name(
                    resource_kind="supervisor",
                    resource_id=TARGET_SUPERVISOR,
                    application_id=PROXY_ID,
                ),
                managed_agent_proxy_group_external_id(
                    resource_kind="supervisor",
                    resource_id=TARGET_SUPERVISOR,
                    application_id=PROXY_ID,
                ),
            ),
            (
                "managed-genie-group-id",
                managed_agent_proxy_group_name(
                    resource_kind="genie",
                    resource_id=TARGET_GENIE,
                    application_id=PROXY_ID,
                ),
                managed_agent_proxy_group_external_id(
                    resource_kind="genie",
                    resource_id=TARGET_GENIE,
                    application_id=PROXY_ID,
                ),
            ),
        )
    )
    for group in groups:
        if not hasattr(group, "meta"):
            group.meta = SimpleNamespace(resource_type="WorkspaceGroup")
    by_id = {str(group.id): group for group in groups}
    return SimpleNamespace(
        apps=SimpleNamespace(
            list=lambda: iter((SimpleNamespace(name="mip-app"),)),
            get=lambda _name: SimpleNamespace(url="https://mip-app.databricksapps.com"),
        ),
        metastores=SimpleNamespace(current=lambda: SimpleNamespace(metastore_id="metastore-id")),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter((SimpleNamespace(id="proxy-scim"),))
        ),
        secrets=SimpleNamespace(list_scopes=lambda: iter((SimpleNamespace(name="proxy-scope"),))),
        database=SimpleNamespace(
            list_database_instances=lambda: iter((SimpleNamespace(name="lakebase-target"),))
        ),
        warehouses=SimpleNamespace(list=lambda: iter((SimpleNamespace(id=TARGET_WAREHOUSE),))),
        groups=SimpleNamespace(
            list=lambda **_kwargs: iter(groups),
            get=lambda group_id: by_id[group_id],
        ),
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "supervisor_agent_id": TARGET_SUPERVISOR,
                "endpoint_name": supervisor_endpoint,
            }
        ),
        serving_endpoints=SimpleNamespace(
            list=lambda: iter((SimpleNamespace(name="gateway"),)),
            get=lambda name: SimpleNamespace(name=name, id=f"{name}-id"),
        ),
    )


def test_admin_inventory_binds_target_supervisor_id_to_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boundary, "_supervisor_agents", lambda _workspace: {TARGET_SUPERVISOR: ""})
    monkeypatch.setattr(boundary, "_genie_spaces", lambda _workspace: {TARGET_GENIE: ""})

    inventory = collect_admin_inventory(
        _admin_inventory_workspace(),
        app_name="mip-app",
        app_url="https://mip-app.databricksapps.com",
        lakebase_instance="lakebase-target",
        warehouse_id=TARGET_WAREHOUSE,
        supervisor_id=TARGET_SUPERVISOR,
        supervisor_endpoint="gateway",
        supervisor_endpoint_id="gateway-id",
        genie_space_id=TARGET_GENIE,
        expected_application_id=PROXY_ID,
    )

    assert inventory.supervisor_ids == (TARGET_SUPERVISOR,)
    assert inventory.serving_endpoint_names == ("gateway",)
    assert inventory.reviewed_supervisor_bindings == ((TARGET_SUPERVISOR, "gateway", "gateway-id"),)


def test_admin_inventory_rejects_supervisor_id_endpoint_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boundary, "_supervisor_agents", lambda _workspace: {TARGET_SUPERVISOR: ""})
    monkeypatch.setattr(boundary, "_genie_spaces", lambda _workspace: {TARGET_GENIE: ""})

    with pytest.raises(RuntimeError, match="ID and endpoint binding drifted"):
        collect_admin_inventory(
            _admin_inventory_workspace(supervisor_endpoint="different-endpoint"),
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            lakebase_instance="lakebase-target",
            warehouse_id=TARGET_WAREHOUSE,
            supervisor_id=TARGET_SUPERVISOR,
            supervisor_endpoint="gateway",
            supervisor_endpoint_id="gateway-id",
            genie_space_id=TARGET_GENIE,
            expected_application_id=PROXY_ID,
        )


def test_admin_inventory_rejects_same_name_endpoint_id_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boundary, "_supervisor_agents", lambda _workspace: {TARGET_SUPERVISOR: ""})
    monkeypatch.setattr(boundary, "_genie_spaces", lambda _workspace: {TARGET_GENIE: ""})

    with pytest.raises(RuntimeError, match="endpoint identity drifted"):
        collect_admin_inventory(
            _admin_inventory_workspace(),
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            lakebase_instance="lakebase-target",
            warehouse_id=TARGET_WAREHOUSE,
            supervisor_id=TARGET_SUPERVISOR,
            supervisor_endpoint="gateway",
            supervisor_endpoint_id="replacement-id",
            genie_space_id=TARGET_GENIE,
            expected_application_id=PROXY_ID,
        )


def test_admin_inventory_rejects_managed_group_wrong_external_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        boundary, "_supervisor_agents", lambda _workspace: {TARGET_SUPERVISOR: ""}
    )
    monkeypatch.setattr(
        boundary, "_genie_spaces", lambda _workspace: {TARGET_GENIE: ""}
    )
    group = SimpleNamespace(
        id="managed-query-group-id",
        display_name=PROXY_QUERY_GROUP,
        external_id="mip:serving-query:wrong",
    )

    with pytest.raises(RuntimeError, match="group contract drifted"):
        collect_admin_inventory(
            _admin_inventory_workspace(managed_groups=(group,)),
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            lakebase_instance="lakebase-target",
            warehouse_id=TARGET_WAREHOUSE,
            supervisor_id=TARGET_SUPERVISOR,
            supervisor_endpoint="gateway",
            supervisor_endpoint_id="gateway-id",
            genie_space_id=TARGET_GENIE,
            expected_application_id=PROXY_ID,
        )


@pytest.mark.parametrize("duplicate_field", ("id", "name"))
def test_admin_inventory_rejects_duplicate_managed_group_identity(
    monkeypatch: pytest.MonkeyPatch,
    duplicate_field: str,
) -> None:
    monkeypatch.setattr(
        boundary, "_supervisor_agents", lambda _workspace: {TARGET_SUPERVISOR: ""}
    )
    monkeypatch.setattr(
        boundary, "_genie_spaces", lambda _workspace: {TARGET_GENIE: ""}
    )
    expected_external = managed_query_group_external_id(
        endpoint_id="gateway-id",
        application_id=PROXY_ID,
    )
    first = SimpleNamespace(
        id="managed-query-group-id",
        display_name=PROXY_QUERY_GROUP,
        external_id=expected_external,
    )
    second = SimpleNamespace(
        id=("managed-query-group-id" if duplicate_field == "id" else "other-id"),
        display_name=(
            PROXY_QUERY_GROUP if duplicate_field == "name" else f"{PROXY_QUERY_GROUP}-other"
        ),
        external_id=f"{expected_external}:other",
    )

    with pytest.raises(RuntimeError, match="ambiguous|drifted"):
        collect_admin_inventory(
            _admin_inventory_workspace(managed_groups=(first, second)),
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            lakebase_instance="lakebase-target",
            warehouse_id=TARGET_WAREHOUSE,
            supervisor_id=TARGET_SUPERVISOR,
            supervisor_endpoint="gateway",
            supervisor_endpoint_id="gateway-id",
            genie_space_id=TARGET_GENIE,
            expected_application_id=PROXY_ID,
        )


def _workspace(
    *,
    app_admin_succeeds: bool = False,
    expose_target_supervisor: bool = False,
    expose_non_target_supervisor: bool = False,
    expose_non_target_genie: bool = False,
    lakebase_succeeds: bool = False,
    metastore_admin_succeeds: bool = False,
    serving_metadata_succeeds: bool = False,
    warehouse_succeeds: bool = False,
    warehouse_metadata_succeeds: bool = False,
    secret_listing_succeeds: bool = False,
    secret_scope_succeeds: bool = False,
    target_supervisor_query_succeeds: bool = True,
    target_supervisor_query_has_payload: bool = True,
    target_supervisor_query_response: object | None = None,
    target_serving_permissions_succeeds: bool = False,
    target_genie_permissions_succeeds: bool = False,
    foundation_metadata_succeeds: bool = False,
    group_manager_succeeds: bool = False,
) -> Any:
    secret_list = (lambda *_args, **_kwargs: iter(())) if secret_listing_succeeds else _denied

    def endpoint_metadata(name: str) -> object:
        if name == "gateway":
            return SimpleNamespace(name=name, id="gateway-id")
        if name == "foundation" and foundation_metadata_succeeds:
            return SimpleNamespace(
                id=None,
                creator=None,
                config=SimpleNamespace(
                    served_entities=[
                        SimpleNamespace(
                            foundation_model=SimpleNamespace(name="system.ai.databricks-gpt")
                        )
                    ]
                ),
            )
        if serving_metadata_succeeds:
            return SimpleNamespace(name=name, id=f"{name}-id")
        return _denied()

    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                application_id=PROXY_ID,
                user_name=PROXY_ID,
                groups=[
                    SimpleNamespace(
                        value="managed-query-group-id",
                        display=PROXY_QUERY_GROUP,
                    ),
                    SimpleNamespace(
                        value="managed-supervisor-group-id",
                        display=managed_agent_proxy_group_name(
                            resource_kind="supervisor",
                            resource_id=TARGET_SUPERVISOR,
                            application_id=PROXY_ID,
                        ),
                    ),
                    SimpleNamespace(
                        value="managed-genie-group-id",
                        display=managed_agent_proxy_group_name(
                            resource_kind="genie",
                            resource_id=TARGET_GENIE,
                            application_id=PROXY_ID,
                        ),
                    ),
                ],
            )
        ),
        config=SimpleNamespace(
            host="https://workspace.cloud.databricks.com",
            authenticate=lambda: {"Authorization": "Bearer proxy"},
        ),
        apps=SimpleNamespace(
            get_permissions=(lambda *_args: object()) if app_admin_succeeds else _denied
        ),
        metastores=SimpleNamespace(
            get=(lambda *_args: object()) if metastore_admin_succeeds else _denied
        ),
        service_principal_secrets_proxy=SimpleNamespace(list=secret_list),
        secrets=SimpleNamespace(
            list_secrets=((lambda **_kwargs: iter(())) if secret_scope_succeeds else _denied)
        ),
        database=SimpleNamespace(
            list_database_instance_roles=(
                (lambda *_args, **_kwargs: iter(())) if lakebase_succeeds else _denied
            )
        ),
        warehouses=SimpleNamespace(
            get=(lambda *_args: object()) if warehouse_metadata_succeeds else _denied
        ),
        statement_execution=_Statements(succeeds=warehouse_succeeds),
        api_client=_SupervisorApi(
            expose_target=expose_target_supervisor,
            expose_non_target=expose_non_target_supervisor,
            target_query_succeeds=target_supervisor_query_succeeds,
            target_query_has_payload=target_supervisor_query_has_payload,
            target_query_response=target_supervisor_query_response,
            target_genie_permissions_succeeds=target_genie_permissions_succeeds,
        ),
        genie=_Genie(expose_non_target=expose_non_target_genie),
        groups=SimpleNamespace(
            patch=(lambda **_kwargs: object()) if group_manager_succeeds else _denied
        ),
        serving_endpoints=SimpleNamespace(
            get=endpoint_metadata,
            get_permissions=(
                (lambda *_args: object()) if target_serving_permissions_succeeds else _denied
            ),
        ),
    )


def _account(*, admin_succeeds: bool = False) -> object:
    operation = (lambda **_kwargs: iter(())) if admin_succeeds else _denied
    return SimpleNamespace(service_principals=SimpleNamespace(list=operation))


def _admin_workspace(*, state: object = ComputeState.STOPPED) -> object:
    permission = SimpleNamespace(permission_level="CAN_MANAGE", inherited=True)
    managed_groups = {
        group.id: group
        for group in _admin_inventory_workspace().groups.list()
    }
    return SimpleNamespace(
        apps=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                id="app-id",
                name="mip-app",
                url="https://mip-app.databricksapps.com",
                service_principal_client_id="app-client",
                service_principal_id="app-scim",
                compute_status=SimpleNamespace(state=state),
                active_deployment=SimpleNamespace(deployment_id="active"),
                pending_deployment=None,
            ),
            get_permissions=lambda _name: SimpleNamespace(
                access_control_list=[
                    SimpleNamespace(
                        service_principal_name=None,
                        group_name="admins",
                        user_name=None,
                        all_permissions=[permission],
                    )
                ]
            ),
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                (
                    SimpleNamespace(
                        id="proxy-scim",
                        application_id=PROXY_ID,
                        display_name="mip-agent-supervisor-proxy-ci-sp",
                    ),
                )
            )
        ),
        groups=SimpleNamespace(
            get=lambda group_id: managed_groups[group_id]
        ),
    )


def _verify(
    workspace: object,
    *,
    account: object | None = None,
    app_status: int = 403,
    unrelated_app_status: int = 403,
    admin_workspace: object | None = None,
    allow_attested_app_401: bool = False,
    inventory: AgentProxyBoundaryInventory | None = None,
) -> None:
    def http_get(url: str, **_kwargs: object) -> object:
        if url.endswith("/api/2.0/preview/scim/v2/Me"):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "id": "proxy-scim",
                    "userName": PROXY_ID,
                },
            )
        if "/api/2.0/permissions/apps/" in url:
            return SimpleNamespace(status_code=403)
        if url.startswith("https://unrelated-app."):
            return SimpleNamespace(status_code=unrelated_app_status)
        return SimpleNamespace(status_code=app_status)

    verify_boundary(
        workspace=workspace,
        account=account or _account(),
        inventory=inventory or _inventory(),
        expected_application_id=PROXY_ID,
        account_id="account-id",
        app_name="mip-app",
        warehouse_id=TARGET_WAREHOUSE,
        supervisor_id=TARGET_SUPERVISOR,
        supervisor_endpoint="gateway",
        supervisor_endpoint_id="gateway-id",
        genie_space_id=TARGET_GENIE,
        admin_workspace=admin_workspace,
        allow_attested_app_401=allow_attested_app_401,
        http_get=http_get,
    )


def test_proxy_boundary_proves_target_query_while_denying_definition_metadata() -> None:
    _verify(_workspace())


def test_proxy_boundary_rejects_target_serving_permission_administration() -> None:
    with pytest.raises(RuntimeError, match="target serving endpoint permission administration"):
        _verify(_workspace(target_serving_permissions_succeeds=True))


def test_proxy_boundary_rejects_target_genie_permission_administration() -> None:
    with pytest.raises(RuntimeError, match="target Genie permission administration"):
        _verify(_workspace(target_genie_permissions_succeeds=True))


def test_target_query_boundary_rejects_hidden_non_target_genie_access() -> None:
    with pytest.raises(RuntimeError, match="non-target Genie"):
        boundary.verify_target_query_boundary(
            workspace=_workspace(expose_non_target_genie=True),
            inventory=_inventory(),
            expected_application_id=PROXY_ID,
            account_id="account-id",
            supervisor_id=TARGET_SUPERVISOR,
            supervisor_endpoint="gateway",
            supervisor_endpoint_id="gateway-id",
            genie_space_id=TARGET_GENIE,
        )


def test_proxy_boundary_rejects_managed_query_group_manager_authority() -> None:
    with pytest.raises(RuntimeError, match="managed serving-query group administration"):
        _verify(_workspace(group_manager_succeeds=True))


def test_managed_query_group_probe_binds_workspace_group_and_same_name() -> None:
    observed: list[dict[str, object]] = []

    def denied(**kwargs: object) -> object:
        observed.append(kwargs)
        raise PermissionDenied("denied")

    binding = boundary.ManagedWorkspaceGroupBinding(
        id="group-one",
        name="managed-one",
        external_id="mip:serving-query:one",
        resource_type="WorkspaceGroup",
    )
    boundary.verify_managed_query_group_administration_denied(
        SimpleNamespace(groups=SimpleNamespace(patch=denied)),
        group_bindings=(binding,),
    )

    assert observed[0]["id"] == "group-one"
    operation = cast(list[object], observed[0]["operations"])[0]
    assert operation.path == "displayName"  # type: ignore[attr-defined]
    assert operation.value == "managed-one"  # type: ignore[attr-defined]


def test_proxy_boundary_accepts_only_authenticated_system_ai_foundation_metadata() -> None:
    inventory = replace(
        _inventory(),
        serving_endpoint_names=("foundation", "gateway", "unrelated-endpoint"),
        foundation_endpoint_names=("foundation",),
    )

    _verify(
        _workspace(foundation_metadata_succeeds=True),
        inventory=inventory,
    )


def test_proxy_boundary_rejects_visible_endpoint_misclassified_as_foundation() -> None:
    inventory = replace(
        _inventory(),
        serving_endpoint_names=("foundation", "gateway", "unrelated-endpoint"),
        foundation_endpoint_names=("foundation",),
    )

    with pytest.raises(RuntimeError, match="not a Databricks system.ai foundation endpoint"):
        _verify(
            _workspace(serving_metadata_succeeds=True),
            inventory=inventory,
        )


def test_proxy_boundary_rejects_live_null_model() -> None:
    with pytest.raises(RuntimeError, match="exact terminal Agent Responses"):
        _verify(
            _workspace(
                target_supervisor_query_response={
                    "id": "response-target",
                    "object": "response",
                    "model": None,
                    "status": "completed",
                    "error": None,
                    "incomplete_details": None,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ready"}],
                        }
                    ],
                }
            )
        )


def test_proxy_boundary_accepts_missing_item_status() -> None:
    _verify(
        _workspace(
            target_supervisor_query_response={
                "id": "response-target",
                "object": "response",
                "model": "gateway",
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ready"}],
                    }
                ],
            }
        )
    )


def test_proxy_boundary_rejects_target_supervisor_definition_access() -> None:
    with pytest.raises(RuntimeError, match="Supervisor definition metadata"):
        _verify(_workspace(expose_target_supervisor=True))


def test_proxy_boundary_rejects_hidden_non_target_supervisor_access() -> None:
    with pytest.raises(RuntimeError, match="Supervisor definition metadata"):
        _verify(_workspace(expose_non_target_supervisor=True))


def test_proxy_boundary_url_encodes_opaque_supervisor_ids() -> None:
    workspace = _workspace()
    opaque_id = "supervisor/other"
    inventory = replace(
        _inventory(),
        supervisor_ids=(TARGET_SUPERVISOR, opaque_id),
    )

    _verify(workspace, inventory=inventory)

    assert "/api/2.1/supervisor-agents/supervisor%2Fother" in workspace.api_client.paths


def test_proxy_boundary_rejects_failed_target_supervisor_query() -> None:
    with pytest.raises(RuntimeError, match="target Supervisor query was inconclusive"):
        _verify(_workspace(target_supervisor_query_succeeds=False))


def test_proxy_boundary_rejects_empty_target_supervisor_response() -> None:
    with pytest.raises(RuntimeError, match="exact terminal Agent Responses payload"):
        _verify(_workspace(target_supervisor_query_has_payload=False))


@pytest.mark.parametrize(
    "response",
    (
        {"id": "response-target", "model": "gateway", "status": "completed", "output": ["ready"]},
        {
            "id": "response-target",
            "model": "gateway",
            "status": "completed",
            "output": [{"message": "ready"}],
        },
        {
            "id": "response-target",
            "model": "other-endpoint",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ready"}],
                }
            ],
        },
        {
            "id": "response-target",
            "model": "gateway",
            "status": "failed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ready"}],
                }
            ],
        },
        {
            "id": "response-target",
            "model": "gateway",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ready"}],
                }
            ],
        },
        {
            "model": "gateway",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ready"}],
                }
            ],
        },
        {"contents": object()},
    ),
)
def test_proxy_boundary_rejects_malformed_target_supervisor_response(
    response: object,
) -> None:
    with pytest.raises(RuntimeError, match="exact terminal Agent Responses payload"):
        _verify(_workspace(target_supervisor_query_response=response))


def test_target_supervisor_query_waits_through_cold_start() -> None:
    api = _SupervisorApi(
        target_query_failures=1,
        target_query_error=TimeoutError("scaling from zero"),
    )
    workspace = SimpleNamespace(api_client=api)

    _verify_target_supervisor_query(
        workspace,
        supervisor_endpoint="gateway",
        sleep=lambda _seconds: None,
    )

    assert api.target_query_calls == 3


def test_target_supervisor_query_does_not_retry_non_cold_error() -> None:
    api = _SupervisorApi(
        target_query_failures=1,
        target_query_error=ValueError("unrelated provider contract failure"),
    )
    workspace = SimpleNamespace(api_client=api)

    with pytest.raises(RuntimeError, match="target Supervisor query was inconclusive"):
        _verify_target_supervisor_query(
            workspace,
            supervisor_endpoint="gateway",
            sleep=lambda _seconds: None,
        )

    assert api.target_query_calls == 1


def test_target_supervisor_query_retries_permission_propagation() -> None:
    api = _SupervisorApi(
        target_query_failures=1,
        target_query_error=PermissionDenied("permission denied while grant propagates"),
    )
    workspace = SimpleNamespace(api_client=api)

    _verify_target_supervisor_query(
        workspace,
        supervisor_endpoint="gateway",
        sleep=lambda _seconds: None,
        clock=lambda: 0.0,
    )

    assert api.target_query_calls == 3


def test_proxy_boundary_rejects_effective_warehouse_execution() -> None:
    with pytest.raises(RuntimeError, match="unexpectedly executed SQL"):
        _verify(_workspace(warehouse_succeeds=True))


def test_proxy_boundary_rejects_effective_service_principal_secret_access() -> None:
    with pytest.raises(RuntimeError, match="secret listing"):
        _verify(_workspace(secret_listing_succeeds=True))


def test_proxy_boundary_rejects_effective_secret_scope_read_access() -> None:
    with pytest.raises(RuntimeError, match="secret-scope key inventory"):
        _verify(_workspace(secret_scope_succeeds=True))


@pytest.mark.parametrize(
    ("workspace", "account", "message"),
    [
        (_workspace(app_admin_succeeds=True), _account(), "App permission"),
        (_workspace(metastore_admin_succeeds=True), _account(), "metastore administrator"),
        (_workspace(lakebase_succeeds=True), _account(), "Lakebase role inventory"),
        (_workspace(warehouse_metadata_succeeds=True), _account(), "warehouse metadata"),
        (_workspace(expose_non_target_genie=True), _account(), "non-target Genie"),
        (_workspace(serving_metadata_succeeds=True), _account(), "serving endpoint metadata"),
        (_workspace(), _account(admin_succeeds=True), "account administrator"),
    ],
)
def test_proxy_boundary_rejects_each_hidden_non_uc_authority(
    workspace: object,
    account: object,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _verify(workspace, account=account)


def test_proxy_boundary_rejects_effective_app_use() -> None:
    with pytest.raises(RuntimeError, match="App denial"):
        _verify(_workspace(), app_status=200)


def test_proxy_boundary_rejects_uncorroborated_provider_401() -> None:
    with pytest.raises(RuntimeError, match="uncorroborated status=401"):
        _verify(_workspace(), app_status=401)


@pytest.mark.parametrize("state", (ComputeState.ACTIVE, ComputeState.STOPPED))
def test_proxy_boundary_accepts_target_401_with_admin_attestation(
    state: object,
) -> None:
    _verify(
        _workspace(),
        app_status=401,
        admin_workspace=_admin_workspace(state=state),
        allow_attested_app_401=True,
    )


def test_proxy_boundary_keeps_unrelated_apps_403_only() -> None:
    with pytest.raises(RuntimeError, match="uncorroborated status=401"):
        _verify(
            _workspace(),
            app_status=401,
            unrelated_app_status=401,
            admin_workspace=_admin_workspace(),
            allow_attested_app_401=True,
        )


def test_proxy_boundary_requires_admin_authority_for_attested_401_mode() -> None:
    with pytest.raises(RuntimeError, match="attestation authority is absent"):
        _verify(
            _workspace(),
            allow_attested_app_401=True,
        )


def test_proxy_boundary_rejects_not_found_as_account_authorization_proof() -> None:
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: (_ for _ in ()).throw(ResourceDoesNotExist("account not found"))
        )
    )

    with pytest.raises(RuntimeError, match="account administrator.*inconclusive"):
        _verify(_workspace(), account=account)


def test_proxy_boundary_rejects_wrong_authenticated_identity() -> None:
    workspace = _workspace()
    workspace.current_user.me = lambda: SimpleNamespace(
        application_id="wrong-client",
        user_name="wrong-client",
    )

    with pytest.raises(RuntimeError, match="does not match"):
        _verify(workspace)


@pytest.mark.parametrize(
    "message",
    [
        "retry after 403 seconds",
        "job 401 is still running",
        "request token 1403 expired",
    ],
)
def test_incidental_status_digits_are_not_authorization_proof(message: str) -> None:
    error = RuntimeError(message)

    assert _is_denied(error, allow_hidden_resource=False) is False
    with pytest.raises(RuntimeError, match="inconclusive"):
        _expect_denied(
            "incidental-number probe",
            lambda: (_ for _ in ()).throw(error),
            allow_hidden_resource=False,
        )


@pytest.mark.parametrize(
    "error",
    [
        "retry after 403 seconds",
        SimpleNamespace(message="job 401 is still running"),
        {"message": "request token 1403 expired"},
    ],
)
def test_warehouse_incidental_status_digits_are_inconclusive(error: object) -> None:
    workspace = SimpleNamespace(statement_execution=_Statements(error=error))

    with pytest.raises(RuntimeError, match="warehouse denial was inconclusive"):
        _verify_warehouse_denial(workspace, warehouse_id=TARGET_WAREHOUSE)


@pytest.mark.parametrize(
    "error",
    [
        SimpleNamespace(status_code=403),
        {"error_code": "PERMISSION_DENIED"},
    ],
)
def test_warehouse_structured_denial_evidence_is_accepted(error: object) -> None:
    workspace = SimpleNamespace(statement_execution=_Statements(error=error))

    _verify_warehouse_denial(workspace, warehouse_id=TARGET_WAREHOUSE)


class _AuthenticationFailure(RuntimeError):
    status_code = 401


def test_proxy_boundary_rejects_expired_control_plane_authentication() -> None:
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: (_ for _ in ()).throw(_AuthenticationFailure("token expired"))
        )
    )

    with pytest.raises(RuntimeError, match="inconclusive"):
        _verify(_workspace(), account=account, app_status=401)


def _foundation_details() -> object:
    return SimpleNamespace(
        id=None,
        creator=None,
        config=SimpleNamespace(
            served_entities=[
                SimpleNamespace(foundation_model=SimpleNamespace(name="system.ai.databricks-gpt"))
            ]
        ),
    )


def _global_denial_inventory() -> boundary.AgentProxyCustomerResourceDenialInventory:
    binding = boundary.ManagedWorkspaceGroupBinding(
        id="managed-query-group-id",
        name="mip-serving-query-managed",
        external_id="mip:serving-query:managed",
        resource_type="WorkspaceGroup",
    )
    return boundary.AgentProxyCustomerResourceDenialInventory(
        supervisor_ids=("supervisor-hidden",),
        genie_space_ids=("genie-hidden",),
        serving_endpoints=(
            ("customer-endpoint", "customer-id", "agent_v1_responses", False),
            ("foundation", "", "", True),
        ),
        managed_query_group_ids=("managed-query-group-id",),
        managed_query_group_bindings=(binding,),
    )


def _global_denial_workspace(
    *,
    identity: str = PROXY_ID,
    successful_read: str = "",
    query_error: BaseException | None = None,
    foundation_details: object | None = None,
) -> Any:
    def api_do(method: str, path: str, **_kwargs: object) -> object:
        if method == "POST" and path == "/serving-endpoints/responses":
            if query_error is not None:
                raise query_error
            if successful_read == "query":
                return {"output": "hidden group access"}
            return _denied()
        if successful_read == "supervisor" and "supervisor-agents" in path:
            return {"supervisor_agent_id": "supervisor-hidden"}
        if successful_read == "genie-permission" and "/permissions/genie/" in path:
            return {"access_control_list": []}
        return _denied()

    def endpoint_get(name: str) -> object:
        if name == "foundation":
            return foundation_details or _foundation_details()
        if successful_read == "endpoint":
            return SimpleNamespace(name=name, id="customer-id")
        return _denied()

    def endpoint_permissions(_endpoint_id: str) -> object:
        if successful_read == "endpoint-permission":
            return {"access_control_list": []}
        return _denied()

    def genie_get(identifier: str) -> object:
        if successful_read == "genie":
            return SimpleNamespace(space_id=identifier)
        return _denied()

    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(application_id=identity, user_name=identity)
        ),
        api_client=SimpleNamespace(do=api_do),
        serving_endpoints=SimpleNamespace(
            get=endpoint_get,
            get_permissions=endpoint_permissions,
        ),
        genie=SimpleNamespace(get_space=genie_get),
        groups=SimpleNamespace(
            patch=(
                (lambda **_kwargs: object())
                if successful_read == "group-manager"
                else _denied
            )
        ),
    )


def test_global_denial_accepts_only_classified_foundation_metadata_baseline() -> None:
    boundary.verify_customer_resource_denial_boundary(
        workspace=_global_denial_workspace(),
        inventory=_global_denial_inventory(),
        expected_application_id=PROXY_ID,
        account_id="account-id",
    )


def test_global_denial_rejects_hidden_group_query_capability() -> None:
    with pytest.raises(RuntimeError, match="query capability.*unexpectedly succeeded"):
        boundary.verify_customer_resource_denial_boundary(
            workspace=_global_denial_workspace(successful_read="query"),
            inventory=_global_denial_inventory(),
            expected_application_id=PROXY_ID,
            account_id="account-id",
        )


@pytest.mark.parametrize(
    ("successful_read", "message"),
    (
        ("supervisor", "Supervisor definition metadata"),
        ("endpoint", "serving endpoint metadata"),
        ("endpoint-permission", "serving endpoint permission administration"),
        ("genie", "Genie space metadata"),
        ("genie-permission", "Genie permission administration"),
        ("group-manager", "managed serving-query group administration"),
    ),
)
def test_global_denial_rejects_hidden_group_reads(
    successful_read: str,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        boundary.verify_customer_resource_denial_boundary(
            workspace=_global_denial_workspace(successful_read=successful_read),
            inventory=_global_denial_inventory(),
            expected_application_id=PROXY_ID,
            account_id="account-id",
        )


def test_global_denial_rejects_authenticated_identity_mismatch() -> None:
    with pytest.raises(RuntimeError, match="does not match"):
        boundary.verify_customer_resource_denial_boundary(
            workspace=_global_denial_workspace(identity="different-proxy"),
            inventory=_global_denial_inventory(),
            expected_application_id=PROXY_ID,
            account_id="account-id",
        )


def test_global_denial_rejects_contradictory_identity_fields() -> None:
    workspace = _global_denial_workspace()
    workspace.current_user.me = lambda: SimpleNamespace(
        application_id="different-proxy",
        user_name=PROXY_ID,
    )

    with pytest.raises(RuntimeError, match="does not match"):
        boundary.verify_customer_resource_denial_boundary(
            workspace=workspace,
            inventory=_global_denial_inventory(),
            expected_application_id=PROXY_ID,
            account_id="account-id",
        )


def test_global_denial_rejects_inconclusive_query_error() -> None:
    with pytest.raises(RuntimeError, match="query capability.*inconclusive"):
        boundary.verify_customer_resource_denial_boundary(
            workspace=_global_denial_workspace(query_error=TimeoutError("provider timeout")),
            inventory=_global_denial_inventory(),
            expected_application_id=PROXY_ID,
            account_id="account-id",
        )


def test_global_denial_rejects_misclassified_foundation_endpoint() -> None:
    customer = SimpleNamespace(name="foundation", id="customer-id", creator="customer")
    with pytest.raises(RuntimeError, match="not a Databricks system.ai foundation endpoint"):
        boundary.verify_customer_resource_denial_boundary(
            workspace=_global_denial_workspace(foundation_details=customer),
            inventory=_global_denial_inventory(),
            expected_application_id=PROXY_ID,
            account_id="account-id",
        )


def test_global_denial_admin_inventory_is_target_free_and_classifies_foundation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = SimpleNamespace(
        id="managed-query-group-id",
        display_name="mip-serving-query-endpoint-proxy",
        external_id="mip:serving-query:group",
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    workspace = SimpleNamespace(
        groups=SimpleNamespace(
            list=lambda **_kwargs: iter((group,)),
            get=lambda group_id: group
            if group_id == "managed-query-group-id"
            else pytest.fail(group_id),
        ),
        serving_endpoints=SimpleNamespace(
            list=lambda: iter(
                (
                    SimpleNamespace(name="customer-endpoint"),
                    SimpleNamespace(name="foundation"),
                )
            ),
            get=lambda name: (
                _foundation_details()
                if name == "foundation"
                else SimpleNamespace(
                    name="customer-endpoint",
                    id="customer-id",
                    task="agent_v1_responses",
                )
            ),
        ),
    )
    monkeypatch.setattr(boundary, "_supervisor_agents", lambda _workspace: {})
    monkeypatch.setattr(boundary, "_genie_spaces", lambda _workspace: {})

    inventory = boundary.collect_admin_customer_resource_denial_inventory(workspace)

    assert inventory.supervisor_ids == ()
    assert inventory.genie_space_ids == ()
    assert inventory.serving_endpoints == (
        ("customer-endpoint", "customer-id", "agent_v1_responses", False),
        ("foundation", "", "", True),
    )
    assert inventory.managed_query_group_ids == ("managed-query-group-id",)


def test_global_denial_admin_inventory_rejects_unknown_query_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        serving_endpoints=SimpleNamespace(
            list=lambda: iter((SimpleNamespace(name="customer-endpoint"),)),
            get=lambda _name: SimpleNamespace(name="customer-endpoint", id="customer-id"),
        )
    )
    monkeypatch.setattr(boundary, "_supervisor_agents", lambda _workspace: {})
    monkeypatch.setattr(boundary, "_genie_spaces", lambda _workspace: {})

    with pytest.raises(RuntimeError, match="lacks identity or query protocol"):
        boundary.collect_admin_customer_resource_denial_inventory(workspace)


def test_global_denial_cli_requires_no_deployment_target(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_ID", PROXY_ID)
    monkeypatch.setenv("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", "proxy-secret")
    clients: list[object] = []

    def workspace_client() -> object:
        client = SimpleNamespace(
            config=SimpleNamespace(host="https://workspace.cloud.databricks.com")
        )
        clients.append(client)
        return client

    observed: dict[str, object] = {}
    monkeypatch.setattr(boundary, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(
        boundary,
        "assert_workspace_admin_inventory_identity",
        lambda workspace, *, expected_principal: observed.update(
            inventory_workspace=workspace,
            expected_principal=expected_principal,
        ),
    )
    monkeypatch.setattr(
        boundary,
        "collect_admin_customer_resource_denial_inventory",
        lambda workspace: (
            observed.update(admin_workspace=workspace) or _global_denial_inventory()
        ),
    )
    monkeypatch.setattr(
        boundary,
        "collect_admin_inventory",
        lambda *_args, **_kwargs: pytest.fail("positive target inventory was requested"),
    )
    monkeypatch.setattr(
        boundary,
        "verify_customer_resource_denial_boundary",
        lambda **kwargs: observed.update(kwargs),
    )
    monkeypatch.setattr(
        boundary,
        "AccountClient",
        lambda **_kwargs: pytest.fail("account client was constructed"),
    )

    assert (
        boundary.main(
            [
                "--expected-application-id",
                PROXY_ID,
                "--expected-inventory-principal",
                "reviewed-admin@example.com",
                "--account-id",
                "account-id",
                "--customer-resource-denial",
            ]
        )
        == 0
    )

    assert observed["inventory_workspace"] is clients[0]
    assert observed["expected_principal"] == "reviewed-admin@example.com"
    assert observed["admin_workspace"] is clients[0]
    assert observed["workspace"] is clients[1]
    assert observed["expected_application_id"] == PROXY_ID
    assert observed["account_id"] == "account-id"
    output = capsys.readouterr().out
    assert "customer-created serving" in output
    assert "foundation invocation not asserted" in output


def test_global_denial_cli_requires_reviewed_inventory_principal() -> None:
    with pytest.raises(SystemExit, match="expected-inventory-principal"):
        boundary.main(
            ["--expected-application-id", PROXY_ID, "--customer-resource-denial"]
        )


def test_global_denial_cli_requires_account_id() -> None:
    with pytest.raises(SystemExit, match="account-id"):
        boundary.main(
            [
                "--expected-application-id",
                PROXY_ID,
                "--expected-inventory-principal",
                "admin@example.com",
                "--customer-resource-denial",
            ]
        )
