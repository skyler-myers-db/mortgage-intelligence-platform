from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from databricks.sdk.errors import PermissionDenied, ResourceDoesNotExist
from databricks.sdk.service.apps import ComputeState

from tools.databricks import verify_agent_proxy_identity_boundary as boundary
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
        lambda: SimpleNamespace(
            config=SimpleNamespace(host="https://attacker.invalid")
        ),
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
    ) -> None:
        self.expose_target = expose_target
        self.expose_non_target = expose_non_target
        self.target_query_succeeds = target_query_succeeds
        self.target_query_has_payload = target_query_has_payload
        self.target_query_failures = target_query_failures
        self.target_query_error = target_query_error
        self.target_query_response = target_query_response
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
        if method == "POST" and path == "/serving-endpoints/responses":
            self.target_query_calls += 1
            if self.target_query_calls <= self.target_query_failures:
                raise self.target_query_error or TimeoutError("scaling from zero")
            if not self.target_query_succeeds:
                return _denied()
            assert body is not None
            assert body["model"] == "gateway"
            if self.target_query_response is not None:
                return self.target_query_response
            return {
                "id": "response-target",
                "model": "gateway",
                "status": "completed",
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
    )


def _admin_inventory_workspace(*, supervisor_endpoint: str = "gateway") -> object:
    return SimpleNamespace(
        apps=SimpleNamespace(
            list=lambda: iter((SimpleNamespace(name="mip-app"),)),
            get=lambda _name: SimpleNamespace(
                url="https://mip-app.databricksapps.com"
            ),
        ),
        metastores=SimpleNamespace(
            current=lambda: SimpleNamespace(metastore_id="metastore-id")
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter((SimpleNamespace(id="proxy-scim"),))
        ),
        secrets=SimpleNamespace(
            list_scopes=lambda: iter((SimpleNamespace(name="proxy-scope"),))
        ),
        database=SimpleNamespace(
            list_database_instances=lambda: iter(
                (SimpleNamespace(name="lakebase-target"),)
            )
        ),
        warehouses=SimpleNamespace(
            list=lambda: iter((SimpleNamespace(id=TARGET_WAREHOUSE),))
        ),
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "supervisor_agent_id": TARGET_SUPERVISOR,
                "endpoint_name": supervisor_endpoint,
            }
        ),
        serving_endpoints=SimpleNamespace(
            list=lambda: iter((SimpleNamespace(name="gateway"),))
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
        genie_space_id=TARGET_GENIE,
    )

    assert inventory.supervisor_ids == (TARGET_SUPERVISOR,)
    assert inventory.serving_endpoint_names == ("gateway",)


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
            genie_space_id=TARGET_GENIE,
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
) -> object:
    secret_list = (lambda *_args, **_kwargs: iter(())) if secret_listing_succeeds else _denied
    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(application_id=PROXY_ID, user_name=PROXY_ID)
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
            list_secrets=(
                (lambda **_kwargs: iter(())) if secret_scope_succeeds else _denied
            )
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
        ),
        genie=_Genie(expose_non_target=expose_non_target_genie),
        serving_endpoints=SimpleNamespace(
            get=(lambda *_args: object()) if serving_metadata_succeeds else _denied
        ),
    )


def _account(*, admin_succeeds: bool = False) -> object:
    operation = (lambda **_kwargs: iter(())) if admin_succeeds else _denied
    return SimpleNamespace(service_principals=SimpleNamespace(list=operation))


def _admin_workspace(*, state: object = ComputeState.STOPPED) -> object:
    permission = SimpleNamespace(permission_level="CAN_MANAGE", inherited=True)
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
        app_name="mip-app",
        warehouse_id=TARGET_WAREHOUSE,
        supervisor_id=TARGET_SUPERVISOR,
        supervisor_endpoint="gateway",
        genie_space_id=TARGET_GENIE,
        admin_workspace=admin_workspace,
        allow_attested_app_401=allow_attested_app_401,
        http_get=http_get,
    )


def test_proxy_boundary_proves_target_query_while_denying_definition_metadata() -> None:
    _verify(_workspace())


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

    assert (
        "/api/2.1/supervisor-agents/supervisor%2Fother"
        in workspace.api_client.paths
    )


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
    api = _SupervisorApi(target_query_succeeds=False)
    workspace = SimpleNamespace(api_client=api)

    with pytest.raises(RuntimeError, match="target Supervisor query was inconclusive"):
        _verify_target_supervisor_query(
            workspace,
            supervisor_endpoint="gateway",
            sleep=lambda _seconds: None,
        )

    assert api.target_query_calls == 1


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
            list=lambda **_kwargs: (_ for _ in ()).throw(
                ResourceDoesNotExist("account not found")
            )
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
            list=lambda **_kwargs: (_ for _ in ()).throw(
                _AuthenticationFailure("token expired")
            )
        )
    )

    with pytest.raises(RuntimeError, match="inconclusive"):
        _verify(_workspace(), account=account, app_status=401)
