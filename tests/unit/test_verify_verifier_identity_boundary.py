from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import PermissionDenied
from databricks.sdk.service.apps import ComputeState

from tests.fixtures.oauth_credential_session import (
    install_in_memory_credential_mutation_session,
)
from tools.databricks import oauth_credential_creation
from tools.databricks import verifier_customer_resource_denial as customer_denial
from tools.databricks import verify_verifier_identity_boundary as boundary

verify_boundary = boundary.verify_boundary


@pytest.fixture(autouse=True)
def _disable_credential_inventory_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        oauth_credential_creation,
        "_STABILITY_INTERVAL_SECONDS",
        0,
    )
    install_in_memory_credential_mutation_session(
        monkeypatch,
        oauth_credential_creation,
    )


def _verifier_main_args(
    account_host: str = "https://accounts.cloud.databricks.com",
    *,
    include_attested_mode: bool = True,
) -> list[str]:
    args = [
        "--expected-application-id",
        "verifier-client-id",
        "--account-host",
        account_host,
        "--account-id",
        "account-id",
        "--app-name",
        "mip-app",
        "--app-url",
        "https://mip-app.databricksapps.com",
        "--protected-service-principal-id",
        "app-scim",
        "--warehouse-id",
        "warehouse-id",
        "--relation-prefix",
        "mip.audit.gateway",
        "--endpoint",
        "gateway",
    ]
    if include_attested_mode:
        args.append("--allow-attested-app-401")
    return args


@pytest.mark.parametrize(
    "account_host",
    (
        "https://user@accounts.cloud.databricks.com",
        "https://accounts.cloud.databricks.com:443",
    ),
)
def test_verifier_main_rejects_account_origin_before_constructing_clients(
    monkeypatch: pytest.MonkeyPatch,
    account_host: str,
) -> None:
    monkeypatch.setattr(
        boundary,
        "WorkspaceClient",
        lambda: pytest.fail("workspace client constructed before host validation"),
    )

    with pytest.raises(RuntimeError, match="reviewed Databricks account origin"):
        boundary.main(_verifier_main_args(account_host))


def test_verifier_main_rejects_non_attested_mode_before_constructing_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_DISCOVERY_URL", "https://attacker.invalid")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "verifier-client-id")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "verifier-secret")
    monkeypatch.setattr(
        boundary,
        "WorkspaceClient",
        lambda: pytest.fail("workspace client constructed in unsafe mode"),
    )

    with pytest.raises(RuntimeError, match="dual-authority App attestation mode"):
        boundary.main(_verifier_main_args(include_attested_mode=False))


def test_main_captures_admin_then_binds_exact_verifier_m2m(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "pat")
    monkeypatch.setenv("DATABRICKS_TOKEN", "admin-token")
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DATABRICKS_VERIFIER_CLIENT_ID", "verifier-client-id")
    monkeypatch.setenv("DATABRICKS_VERIFIER_CLIENT_SECRET", "verifier-secret")
    auth_at_construction: list[tuple[str, str, str]] = []
    clients: list[object] = []

    def workspace_client() -> object:
        auth_at_construction.append(
            (
                os.environ.get("DATABRICKS_AUTH_TYPE", ""),
                os.environ.get("DATABRICKS_TOKEN", ""),
                os.environ.get("DATABRICKS_CLIENT_ID", ""),
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

    binding = boundary.boundary_probes.ManagedWorkspaceGroupBinding(
        id="managed-group-id",
        name="managed-group",
        external_id="mip:serving-query:managed",
        resource_type="WorkspaceGroup",
    )

    def collect_groups(
        workspace: object, **_kwargs: object
    ) -> tuple[boundary.boundary_probes.ManagedWorkspaceGroupBinding, ...]:
        observed["managed_inventory_workspace"] = workspace
        observed["managed_inventory_auth"] = (
            os.environ.get("DATABRICKS_AUTH_TYPE"),
            os.environ.get("DATABRICKS_TOKEN"),
        )
        return (binding,)

    monkeypatch.setattr(boundary, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(boundary, "AccountClient", account_client)
    monkeypatch.setattr(boundary, "verify_boundary", verify)
    monkeypatch.setattr(
        boundary,
        "collect_managed_proxy_workspace_groups",
        collect_groups,
    )

    assert boundary.main(_verifier_main_args()) == 0

    assert auth_at_construction == [
        ("pat", "admin-token", ""),
        ("oauth-m2m", "", "verifier-client-id"),
    ]
    assert account_args["host"] == "https://accounts.cloud.databricks.com"
    assert account_args["client_id"] == "verifier-client-id"
    assert account_args["client_secret"] == "verifier-secret"
    assert observed["workspace"] is clients[1]
    assert observed["account"] is account
    assert observed["admin_workspace"] is clients[0]
    assert observed["allow_attested_app_401"] is True
    assert observed["preserved_endpoints"] == ()
    assert observed["account_id"] == "account-id"
    assert observed["managed_query_group_bindings"] == (binding,)
    assert observed["managed_inventory_workspace"] is clients[0]
    assert observed["managed_inventory_auth"] == ("pat", "admin-token")
    assert "DATABRICKS_VERIFIER_CLIENT_SECRET" not in os.environ


@pytest.mark.parametrize(
    "message",
    (
        "retry after 403 seconds",
        "job 401 is still running",
        "request token 1403 expired",
    ),
)
def test_verifier_boundary_rejects_incidental_status_numbers(message: str) -> None:
    with pytest.raises(RuntimeError, match="inconclusive"):
        boundary._expect_denied(
            "verifier denial",
            lambda: (_ for _ in ()).throw(RuntimeError(message)),
        )


@pytest.mark.parametrize(
    "error",
    (
        {"status_code": 403},
        {"error_code": "PERMISSION_DENIED"},
        SimpleNamespace(response=SimpleNamespace(status_code=403)),
    ),
)
def test_verifier_boundary_accepts_structured_denial_evidence(error: object) -> None:
    assert boundary._is_denied(error)


@pytest.mark.parametrize(
    "error",
    (
        {"status_code": 401},
        {"http_status_code": "401"},
        {"error_code": "UNAUTHENTICATED"},
        {"code": "UNAUTHORIZED"},
        SimpleNamespace(response=SimpleNamespace(status_code=401)),
    ),
)
def test_verifier_boundary_rejects_authentication_failure(error: object) -> None:
    assert not boundary._is_denied(error)


def _sql_response(state: str, *, rows: list[list[object]] | None = None, error: str = ""):
    return SimpleNamespace(
        status=SimpleNamespace(state=state, error=error),
        result=SimpleNamespace(data_array=rows or []),
    )


class _Statements:
    def __init__(
        self,
        *,
        extra_relation: bool = False,
        extra_relation_name: str = "mip.gold.borrower_360",
        extra_catalog_privilege: bool = False,
        extra_schema_privilege: bool = False,
        owner_group_member: bool = False,
        metastore_privilege: bool = False,
        non_target_catalog_privilege: bool = False,
        non_target_schema_privilege: bool = False,
        non_target_owner: bool = False,
        hidden_group_table_modify: bool = False,
        missing_target_table_select: bool = False,
    ) -> None:
        self.extra_relation = extra_relation
        self.extra_relation_name = extra_relation_name
        self.extra_catalog_privilege = extra_catalog_privilege
        self.extra_schema_privilege = extra_schema_privilege
        self.owner_group_member = owner_group_member
        self.metastore_privilege = metastore_privilege
        self.non_target_catalog_privilege = non_target_catalog_privilege
        self.non_target_schema_privilege = non_target_schema_privilege
        self.non_target_owner = non_target_owner
        self.hidden_group_table_modify = hidden_group_table_modify
        self.missing_target_table_select = missing_target_table_select
        self.calls: list[tuple[str, str]] = []

    def execute_statement(self, *, statement: str, warehouse_id: str, **_kwargs: object):
        self.calls.append((warehouse_id, statement))
        if "system.information_schema.metastore_privileges" in statement:
            rows = (
                [["metastore-id", "CREATE CATALOG", "verifier-client-id", "false"]]
                if self.metastore_privilege
                else []
            )
            return _sql_response("SUCCEEDED", rows=rows)
        if "system.information_schema.catalog_privileges" in statement:
            rows = [["mip", "USE CATALOG", "verifier-client-id", "false"]]
            if self.extra_catalog_privilege:
                rows.append(["mip", "CREATE SCHEMA", "verifier-client-id", "false"])
            if self.non_target_catalog_privilege:
                rows.append(["empty_catalog", "CREATE SCHEMA", "hidden-group", "true"])
            return _sql_response("SUCCEEDED", rows=rows)
        if "system.information_schema.schema_privileges" in statement:
            rows = [["mip", "audit", "USE SCHEMA", "verifier-client-id", "false"]]
            if self.extra_schema_privilege:
                rows.append(["mip", "audit", "CREATE TABLE", "verifier-client-id", "false"])
            if self.non_target_schema_privilege:
                rows.append(
                    ["empty_catalog", "empty_schema", "CREATE TABLE", "hidden-group", "true"]
                )
            return _sql_response("SUCCEEDED", rows=rows)
        if "system.information_schema.table_privileges" in statement:
            rows = []
            if not self.missing_target_table_select:
                rows.append(
                    [
                        "mip",
                        "audit",
                        "mip_agent_gateway_growth_agent_payload",
                        "SELECT",
                        "verifier-client-id",
                        "false",
                    ]
                )
            if self.hidden_group_table_modify:
                rows.append(
                    [
                        "mip",
                        "audit",
                        "mip_agent_gateway_growth_agent_payload",
                        "MODIFY",
                        "hidden-account-group",
                        "true",
                    ]
                )
            return _sql_response("SUCCEEDED", rows=rows)
        if "SELECT object_kind, catalog_name" in statement:
            rows = [
                ["CATALOG", "mip", None, "platform-owner", "false"],
                ["SCHEMA", "mip", "audit", "platform-owner", "false"],
            ]
            if self.non_target_owner:
                rows.append(["SCHEMA", "empty_catalog", "empty_schema", "hidden-group", "true"])
            return _sql_response("SUCCEEDED", rows=rows)
        if statement.startswith("SHOW GRANTS") and " ON CATALOG " in statement:
            rows = [["verifier-client-id", "USE CATALOG", "CATALOG", "mip"]]
            if self.extra_catalog_privilege:
                rows.append(["verifier-client-id", "CREATE SCHEMA", "CATALOG", "mip"])
            return _sql_response("SUCCEEDED", rows=rows)
        if statement.startswith("SHOW GRANTS") and " ON SCHEMA " in statement:
            rows = [["verifier-client-id", "USE SCHEMA", "SCHEMA", "mip.audit"]]
            if self.extra_schema_privilege:
                rows.append(["verifier-client-id", "CREATE TABLE", "SCHEMA", "mip.audit"])
            return _sql_response("SUCCEEDED", rows=rows)
        if "system.information_schema.catalogs AS c" in statement:
            return _sql_response(
                "SUCCEEDED",
                rows=[
                    [
                        "platform-owner",
                        "platform-owner",
                        str(self.owner_group_member).lower(),
                        "false",
                    ]
                ],
            )
        rows = [["mip", "audit", "mip_agent_gateway_growth_agent_payload"]]
        if self.extra_relation:
            rows.append(self.extra_relation_name.split("."))
        return _sql_response("SUCCEEDED", rows=rows)


class _DeniedAccountPrincipals:
    def list(self, **_kwargs: object):
        raise PermissionDenied("account administrator required")


class _Serving:
    def __init__(self, *, extra_access: bool = False, target_admin: bool = False) -> None:
        self.extra_access = extra_access
        self.target_admin = target_admin

    def list(self):
        return iter([SimpleNamespace(name="outer"), SimpleNamespace(name="other")])

    def get(self, endpoint: str):
        if endpoint == "other" and not self.extra_access:
            raise PermissionDenied("endpoint permission required")
        return SimpleNamespace(
            name=endpoint,
            id=f"{endpoint}-id",
            task="agent_v1_responses",
        )

    def get_permissions(self, _endpoint_id: str):
        if not self.target_admin:
            raise PermissionDenied("endpoint manager permission required")
        return SimpleNamespace(access_control_list=[])


class _Warehouses:
    def __init__(self, *, extra_access: bool = False, target_admin: bool = False) -> None:
        self.extra_access = extra_access
        self.target_admin = target_admin

    def list(self):
        return iter([SimpleNamespace(id="target-warehouse"), SimpleNamespace(id="other-warehouse")])

    def get(self, warehouse_id: str):
        if warehouse_id == "other-warehouse" and not self.extra_access:
            raise PermissionDenied("warehouse permission required")
        return SimpleNamespace(id=warehouse_id)

    def get_permissions(self, _warehouse_id: str):
        if not self.target_admin:
            raise PermissionDenied("warehouse manager permission required")
        return SimpleNamespace(access_control_list=[])


def _gateway_response(endpoint: str) -> dict[str, object]:
    return {
        "id": f"response-{endpoint}",
        "object": "response",
        "model": endpoint,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "ready"}],
            }
        ],
    }


class _GatewayApi:
    def __init__(self, *, response: object | None = None, error: BaseException | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def do(self, method: str, path: str, *, body: dict[str, object]) -> object:
        self.calls.append((method, path, body))
        if self.error is not None:
            raise self.error
        endpoint = str(body.get("model") or "")
        return self.response if self.response is not None else _gateway_response(endpoint)


def _workspace(
    *,
    extra_relation: bool = False,
    extra_relation_name: str = "mip.gold.borrower_360",
    app_permissions_denied: bool = True,
    metastore_admin: bool = False,
    endpoint_extra_access: bool = False,
    warehouse_extra_access: bool = False,
    endpoint_target_admin: bool = False,
    warehouse_target_admin: bool = False,
    extra_catalog_privilege: bool = False,
    extra_schema_privilege: bool = False,
    owner_group_member: bool = False,
    metastore_privilege: bool = False,
    non_target_catalog_privilege: bool = False,
    non_target_schema_privilege: bool = False,
    non_target_owner: bool = False,
    hidden_group_table_modify: bool = False,
    missing_target_table_select: bool = False,
    group_manager_succeeds: bool = False,
    gateway_response: object | None = None,
    gateway_error: BaseException | None = None,
):
    apps = SimpleNamespace()
    if app_permissions_denied:
        apps.get_permissions = lambda _name: (_ for _ in ()).throw(
            PermissionDenied("workspace admin required")
        )
    else:
        apps.get_permissions = lambda _name: SimpleNamespace(access_control_list=[])
    metastores = SimpleNamespace(
        current=lambda: SimpleNamespace(metastore_id="metastore-id"),
        get=(
            (lambda _id: SimpleNamespace(metastore_id="metastore-id"))
            if metastore_admin
            else lambda _id: (_ for _ in ()).throw(PermissionDenied("metastore admin required"))
        ),
    )
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="verifier-client-id")),
        apps=apps,
        metastores=metastores,
        service_principal_secrets_proxy=SimpleNamespace(
            list=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                PermissionDenied("service principal manager required")
            )
        ),
        statement_execution=_Statements(
            extra_relation=extra_relation,
            extra_relation_name=extra_relation_name,
            extra_catalog_privilege=extra_catalog_privilege,
            extra_schema_privilege=extra_schema_privilege,
            owner_group_member=owner_group_member,
            metastore_privilege=metastore_privilege,
            non_target_catalog_privilege=non_target_catalog_privilege,
            non_target_schema_privilege=non_target_schema_privilege,
            non_target_owner=non_target_owner,
            hidden_group_table_modify=hidden_group_table_modify,
            missing_target_table_select=missing_target_table_select,
        ),
        serving_endpoints=_Serving(
            extra_access=endpoint_extra_access,
            target_admin=endpoint_target_admin,
        ),
        warehouses=_Warehouses(
            extra_access=warehouse_extra_access,
            target_admin=warehouse_target_admin,
        ),
        groups=SimpleNamespace(
            patch=(
                (lambda **_kwargs: object())
                if group_manager_succeeds
                else lambda **_kwargs: (_ for _ in ()).throw(
                    PermissionDenied("workspace group manager required")
                )
            )
        ),
        api_client=_GatewayApi(response=gateway_response, error=gateway_error),
        config=SimpleNamespace(
            host="https://workspace.cloud.databricks.com",
            authenticate=lambda: {"Authorization": "Bearer redacted"},
        ),
    )
    return workspace


def _http_get(app_status: int, *, identity_status: int = 200):
    def get(url: str, **_kwargs: object) -> object:
        if url.endswith("/api/2.0/preview/scim/v2/Me"):
            return SimpleNamespace(
                status_code=identity_status,
                json=lambda: {
                    "id": "verifier-scim-id",
                    "userName": "verifier-client-id",
                },
            )
        if "/api/2.0/permissions/apps/" in url:
            return SimpleNamespace(status_code=403)
        return SimpleNamespace(status_code=app_status)

    return get


def _admin_workspace() -> object:
    managed_group = SimpleNamespace(
        id="managed-group-id",
        display_name="managed-group",
        external_id="mip:serving-query:managed",
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )
    return SimpleNamespace(
        apps=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                id="app-id",
                name="mip-app",
                url="https://mip-app.databricksapps.com",
                service_principal_client_id="app-client",
                service_principal_id="app-scim",
                compute_status=SimpleNamespace(state=ComputeState.ACTIVE),
                active_deployment=SimpleNamespace(deployment_id="active"),
                pending_deployment=None,
            ),
            get_permissions=lambda _name: SimpleNamespace(
                access_control_list=[
                    SimpleNamespace(
                        service_principal_name=None,
                        group_name="release-probes",
                        user_name=None,
                        all_permissions=[
                            SimpleNamespace(
                                permission_level="CAN_USE",
                                inherited=False,
                            )
                        ],
                    )
                ]
            ),
        ),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                (
                    SimpleNamespace(
                        id="verifier-scim-id",
                        application_id="verifier-client-id",
                        display_name="mip-ai-gateway-verifier-ci-sp",
                    ),
                )
            )
        ),
        groups=SimpleNamespace(
            get=lambda group_id: managed_group
            if group_id == "managed-group-id"
            else pytest.fail(group_id)
        ),
    )


def _verify(**overrides: object) -> None:
    kwargs: dict[str, object] = {
        "workspace": _workspace(),
        "account": SimpleNamespace(service_principals=_DeniedAccountPrincipals()),
        "expected_application_id": "verifier-client-id",
        "account_id": "account-id",
        "managed_query_group_bindings": (
            boundary.boundary_probes.ManagedWorkspaceGroupBinding(
                id="managed-group-id",
                name="managed-group",
                external_id="mip:serving-query:managed",
                resource_type="WorkspaceGroup",
            ),
        ),
        "app_name": "mip-app",
        "app_url": "https://mip-app.databricksapps.com",
        "protected_service_principal_id": "protected-scim-id",
        "warehouse_id": "target-warehouse",
        "relation_prefix": "mip.audit.mip_agent_gateway_growth_agent",
        "endpoint": "outer",
        "http_get": _http_get(403),
    }
    kwargs.update(overrides)
    verify_boundary(**kwargs)


def test_effective_boundary_accepts_targets_and_all_expected_denials() -> None:
    _verify()


def test_effective_boundary_executes_exact_gateway_responses_path() -> None:
    workspace = _workspace()

    _verify(workspace=workspace)

    assert len(workspace.api_client.calls) == 2
    for method, path, body in workspace.api_client.calls:
        assert (method, path) == ("POST", "/serving-endpoints/responses")
        assert body["model"] == "outer"
        assert body["stream"] is False
        assert body["max_output_tokens"] == 64
        assert str(body["client_request_id"]).startswith(
            ("mip-warmup-", "mip-verifier-boundary-")
        )


@pytest.mark.parametrize(
    "response",
    (
        {"status": "completed", "output": "ready"},
        {
            "id": "response-outer",
            "object": "response",
            "model": "wrong-gateway",
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
        },
        {
            "id": "response-outer",
            "object": "response",
            "model": "",
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
        },
        {
            "id": "response-outer",
            "object": "response",
            "model": "outer",
            "status": "failed",
            "error": {"message": "failed"},
            "incomplete_details": None,
            "output": [],
        },
        {
            "id": "response-outer",
            "object": "response",
            "model": "outer",
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "output": [{"type": "message", "role": "assistant", "content": []}],
        },
    ),
)
def test_rejects_malformed_or_wrong_endpoint_gateway_payload(response: object) -> None:
    with pytest.raises(RuntimeError, match="exact terminal Gateway Responses payload"):
        _verify(workspace=_workspace(gateway_response=response))


def test_rejects_gateway_authorization_denial_as_failed_positive_proof() -> None:
    with pytest.raises(RuntimeError, match="Gateway query outer was inconclusive"):
        _verify(
            workspace=_workspace(
                gateway_error=PermissionDenied("serving endpoint query denied")
            )
        )


def test_rejects_gateway_authentication_failure_as_failed_positive_proof() -> None:
    error = RuntimeError("expired verifier credential")
    error.status_code = 401  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="Gateway query outer was inconclusive"):
        _verify(workspace=_workspace(gateway_error=error))


def test_gateway_warmup_never_retries_authorization_denial_as_cold_start() -> None:
    error = PermissionDenied("temporarily unavailable: permission denied")

    with pytest.raises(RuntimeError, match="Gateway query outer was inconclusive"):
        boundary.boundary_probes.prove_exact_gateway_responses_execution(
            _workspace(gateway_error=error),
            endpoint="outer",
            sleep=lambda _seconds: pytest.fail("authorization denial was retried"),
        )


def test_rejects_managed_query_group_administration_capability() -> None:
    with pytest.raises(
        RuntimeError,
        match="managed serving-query group administration managed-group-id.*succeeded",
    ):
        _verify(workspace=_workspace(group_manager_succeeds=True))


def test_rejects_provider_401_without_admin_attestation() -> None:
    with pytest.raises(RuntimeError, match="uncorroborated status=401"):
        _verify(http_get=_http_get(401))


def test_accepts_active_401_with_admin_attestation() -> None:
    _verify(
        http_get=_http_get(401),
        admin_workspace=_admin_workspace(),
        allow_attested_app_401=True,
    )


def test_rejects_bare_401_without_exact_bearer_identity() -> None:
    with pytest.raises(RuntimeError, match="preflight identity proof"):
        _verify(http_get=_http_get(401, identity_status=401))


def test_rejects_account_admin_api_access() -> None:
    account = SimpleNamespace(
        service_principals=SimpleNamespace(list=lambda **_kwargs: iter([object()]))
    )
    with pytest.raises(RuntimeError, match="account administrator.*unexpectedly succeeded"):
        _verify(account=account)


class _AuthenticationFailure(RuntimeError):
    status_code = 401


def test_rejects_expired_control_plane_authentication() -> None:
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: (_ for _ in ()).throw(_AuthenticationFailure("token expired"))
        )
    )

    with pytest.raises(RuntimeError, match="inconclusive"):
        _verify(
            account=account, http_get=lambda *_args, **_kwargs: SimpleNamespace(status_code=401)
        )


def test_rejects_metastore_admin_get_access() -> None:
    with pytest.raises(RuntimeError, match="metastore administrator.*unexpectedly succeeded"):
        _verify(workspace=_workspace(metastore_admin=True))


def test_rejects_any_non_target_uc_relation_visibility() -> None:
    with pytest.raises(RuntimeError, match="non-target UC relations.*mip.gold.borrower_360"):
        _verify(workspace=_workspace(extra_relation=True))


def test_rejects_campaign_treatment_snapshot_visibility() -> None:
    with pytest.raises(
        RuntimeError,
        match="non-target UC relations.*mip.audit.campaign_treatment_snapshot",
    ):
        _verify(
            workspace=_workspace(
                extra_relation=True,
                extra_relation_name="mip.audit.campaign_treatment_snapshot",
            )
        )


def test_rejects_saturated_uc_visibility_result() -> None:
    workspace = _workspace()
    workspace.statement_execution.execute_statement = lambda **_kwargs: _sql_response(
        "SUCCEEDED",
        rows=[["mip", "audit", f"mip_agent_gateway_growth_agent_{index}"] for index in range(1001)],
    )
    with pytest.raises(RuntimeError, match="saturated its fail-closed relation limit"):
        _verify(workspace=workspace)


def test_rejects_effective_catalog_create_privilege() -> None:
    with pytest.raises(RuntimeError, match="unexpected effective catalog privileges"):
        _verify(workspace=_workspace(extra_catalog_privilege=True))


def test_rejects_effective_schema_create_privilege() -> None:
    with pytest.raises(RuntimeError, match="unexpected effective schema privileges"):
        _verify(workspace=_workspace(extra_schema_privilege=True))


def test_rejects_effective_uc_ownership_through_hidden_account_group() -> None:
    with pytest.raises(RuntimeError, match="effective owner"):
        _verify(workspace=_workspace(owner_group_member=True))


def test_rejects_effective_metastore_create_catalog_privilege() -> None:
    with pytest.raises(RuntimeError, match="unexpected effective metastore.*CREATE CATALOG"):
        _verify(workspace=_workspace(metastore_privilege=True))


def test_rejects_privilege_on_empty_non_target_catalog() -> None:
    with pytest.raises(RuntimeError, match="non-target catalog empty_catalog.*CREATE SCHEMA"):
        _verify(workspace=_workspace(non_target_catalog_privilege=True))


def test_rejects_privilege_on_empty_non_target_schema() -> None:
    with pytest.raises(
        RuntimeError,
        match=r"non-target schema empty_catalog\.empty_schema.*CREATE TABLE",
    ):
        _verify(workspace=_workspace(non_target_schema_privilege=True))


def test_rejects_ownership_of_empty_non_target_schema_through_group() -> None:
    with pytest.raises(RuntimeError, match=r"effective owner.*empty_catalog\.empty_schema"):
        _verify(workspace=_workspace(non_target_owner=True))


def test_rejects_target_table_modify_through_hidden_account_group() -> None:
    with pytest.raises(RuntimeError, match="unexpected effective target table privilege: MODIFY"):
        _verify(workspace=_workspace(hidden_group_table_modify=True))


def test_requires_explicit_effective_select_on_every_visible_target_table() -> None:
    with pytest.raises(RuntimeError, match="missing an explicit effective SELECT"):
        _verify(workspace=_workspace(missing_target_table_select=True))


@pytest.mark.parametrize(
    ("statement_marker", "rows", "message"),
    [
        (
            "system.information_schema.metastore_privileges",
            [["metastore-id", "CREATE CATALOG", "hidden-group", "unknown"]],
            "invalid boolean",
        ),
        (
            "system.information_schema.catalog_privileges",
            [["mip", "", "verifier-client-id", "false"]],
            "catalog grants returned an empty value",
        ),
        (
            "system.information_schema.schema_privileges",
            [["mip", "", "USE SCHEMA", "verifier-client-id", "false"]],
            "schema grants returned an empty value",
        ),
        (
            "SELECT object_kind, catalog_name",
            [["TABLE", "mip", "audit", "platform-owner", "false"]],
            "ownership returned an invalid container",
        ),
        (
            "SELECT object_kind, catalog_name",
            [["CATALOG", "mip", "audit", "platform-owner", "false"]],
            "ownership returned an invalid container",
        ),
        (
            "SELECT object_kind, catalog_name",
            [["SCHEMA", "mip", "audit", "", "false"]],
            "ownership returned an invalid container",
        ),
    ],
)
def test_rejects_malformed_global_container_rows(
    statement_marker: str,
    rows: list[list[object]],
    message: str,
) -> None:
    workspace = _workspace()
    original_execute = workspace.statement_execution.execute_statement

    def execute_statement(**kwargs: object):
        if statement_marker in str(kwargs.get("statement") or ""):
            return _sql_response("SUCCEEDED", rows=rows)
        return original_execute(**kwargs)

    workspace.statement_execution.execute_statement = execute_statement
    with pytest.raises(RuntimeError, match=message):
        _verify(workspace=workspace)


def test_rejects_non_target_endpoint_metadata_access() -> None:
    with pytest.raises(RuntimeError, match="non-target serving endpoint.*unexpectedly"):
        _verify(workspace=_workspace(endpoint_extra_access=True))


def test_rejects_reviewed_gateway_task_protocol_drift_before_invocation() -> None:
    workspace = _workspace()
    original_get = workspace.serving_endpoints.get

    def get(endpoint: str) -> object:
        details = original_get(endpoint)
        if endpoint == "outer":
            details.task = "llm/v1/chat"
        return details

    workspace.serving_endpoints.get = get
    with pytest.raises(RuntimeError, match="Agent Responses protocol drifted"):
        _verify(workspace=workspace)
    assert workspace.api_client.calls == []


def test_accepts_reviewed_signed_blue_endpoint_during_cutover() -> None:
    workspace = _workspace(endpoint_extra_access=True)
    _verify(
        workspace=workspace,
        preserved_endpoints=("other",),
    )
    queried = [str(body["model"]) for _method, _path, body in workspace.api_client.calls]
    assert queried == ["other", "other", "outer", "outer"]


def test_rejects_non_target_warehouse_metadata_access() -> None:
    with pytest.raises(RuntimeError, match="non-target warehouse.*unexpectedly"):
        _verify(workspace=_workspace(warehouse_extra_access=True))


def test_rejects_target_endpoint_permission_administration() -> None:
    with pytest.raises(RuntimeError, match="target serving endpoint.*unexpectedly"):
        _verify(workspace=_workspace(endpoint_target_admin=True))


def test_rejects_target_warehouse_permission_administration() -> None:
    with pytest.raises(RuntimeError, match="target SQL warehouse.*unexpectedly"):
        _verify(workspace=_workspace(warehouse_target_admin=True))


def test_rejects_actual_app_http_access() -> None:
    with pytest.raises(RuntimeError, match="App HTTP denial probe.*status=200"):
        _verify(http_get=_http_get(200))


def test_rejects_workspace_app_permission_administration() -> None:
    with pytest.raises(RuntimeError, match="App permission-administration.*unexpectedly"):
        _verify(workspace=_workspace(app_permissions_denied=False))


def _managed_group_admin_workspace(
    *,
    duplicate: bool = False,
    contract_drift: bool = False,
    retired_contract_drift: bool = False,
) -> object:
    prefix = boundary.boundary_probes.MANAGED_QUERY_GROUP_PREFIX
    external_prefix = boundary.boundary_probes.MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX
    managed_name = f"{prefix}{'a' * 20}-{'b' * 20}"
    managed = SimpleNamespace(id="managed-id", display_name=managed_name)
    retired_endpoint_id = "retired-endpoint-id"
    retired_name = boundary.boundary_probes.managed_query_group_name(
        endpoint_id=retired_endpoint_id,
        application_id="verifier-client-id",
    )
    retired_external_id = boundary.boundary_probes.managed_query_group_external_id(
        endpoint_id=retired_endpoint_id,
        application_id="verifier-client-id",
    )
    summaries = [
        managed,
        SimpleNamespace(id="retired-managed-id", display_name=retired_name),
        SimpleNamespace(id="ordinary-id", display_name="ordinary"),
    ]
    if duplicate:
        summaries.append(
            SimpleNamespace(id="managed-id-2", display_name=managed.display_name)
        )

    def get_group(group_id: str) -> object:
        if group_id == "retired-managed-id":
            return SimpleNamespace(
                id=group_id,
                display_name=retired_name,
                external_id=(
                    f"{external_prefix}{'Z' * 43}"
                    if retired_contract_drift
                    else retired_external_id
                ),
                members=[],
                meta=SimpleNamespace(resource_type="WorkspaceGroup"),
            )
        return SimpleNamespace(
            id=group_id,
            display_name=(
                "drifted-name" if contract_drift else managed_name
            ),
            external_id=f"{external_prefix}{'A' * 43}",
            members=[
                SimpleNamespace(value="verifier-scim-id"),
                SimpleNamespace(value="other-scim-id"),
            ],
            meta=SimpleNamespace(resource_type="WorkspaceGroup"),
        )

    return SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [
                    SimpleNamespace(
                        id="verifier-scim-id",
                        application_id="verifier-client-id",
                    )
                ]
            )
        ),
        groups=SimpleNamespace(
            list=lambda **_kwargs: iter(summaries),
            get=get_group,
        ),
        serving_endpoints=SimpleNamespace(
            list=lambda: iter([SimpleNamespace(name="retired-gateway")]),
            get=lambda name: SimpleNamespace(
                name=name,
                id=retired_endpoint_id,
                task="agent_v1_responses",
            ),
        ),
    )


def test_admin_inventory_includes_empty_endpoint_bound_verifier_group() -> None:
    workspace = _managed_group_admin_workspace()

    group_ids = boundary.boundary_probes.collect_attached_managed_query_group_ids(
        workspace,
        expected_application_id="verifier-client-id",
    )

    assert group_ids == ("managed-id", "retired-managed-id")


@pytest.mark.parametrize(
    ("workspace", "message"),
    (
        (_managed_group_admin_workspace(duplicate=True), "inventory is ambiguous"),
        (_managed_group_admin_workspace(contract_drift=True), "contract drifted"),
        (
            _managed_group_admin_workspace(retired_contract_drift=True),
            "deterministic contract drifted",
        ),
    ),
)
def test_admin_inventory_rejects_ambiguous_or_drifted_managed_group(
    workspace: object,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        boundary.boundary_probes.collect_attached_managed_query_group_ids(
            workspace,
            expected_application_id="verifier-client-id",
        )


def test_credential_probes_every_attached_managed_group_administration_path() -> None:
    calls: list[dict[str, object]] = []

    def denied(**kwargs: object) -> object:
        calls.append(kwargs)
        raise PermissionDenied("group manager permission required")

    bindings = tuple(
        boundary.boundary_probes.ManagedWorkspaceGroupBinding(
            id=group_id,
            name=f"managed-{group_id}",
            external_id=f"mip:serving-query:{group_id}",
            resource_type="WorkspaceGroup",
        )
        for group_id in ("group-b", "group-a")
    )
    boundary.boundary_probes.verify_managed_query_group_administration_denied(
        SimpleNamespace(groups=SimpleNamespace(patch=denied)),
        group_bindings=bindings,
    )

    assert [call["id"] for call in calls] == ["group-b", "group-a"]


def test_managed_group_authentication_failure_is_inconclusive_not_denial() -> None:
    error = RuntimeError("expired group probe credential")
    error.status_code = 401  # type: ignore[attr-defined]
    workspace = SimpleNamespace(
        groups=SimpleNamespace(
            patch=lambda **_kwargs: (_ for _ in ()).throw(error)
        )
    )
    binding = boundary.boundary_probes.ManagedWorkspaceGroupBinding(
        id="group-id",
        name="managed-group",
        external_id="mip:serving-query:group",
        resource_type="WorkspaceGroup",
    )

    with pytest.raises(RuntimeError, match="administration group-id was inconclusive"):
        boundary.boundary_probes.verify_managed_query_group_administration_denied(
            workspace,
            group_bindings=(binding,),
        )


def test_target_credential_proves_hidden_parent_and_group_admin_denial() -> None:
    deleted: list[tuple[str, str]] = []
    rule_calls: list[dict[str, object]] = []
    factory_kwargs: dict[str, object] = {}
    live_credentials: set[str] = set()

    def create_credential(principal_id: str, **kwargs: object) -> object:
        assert (principal_id, kwargs) == ("app-scim", {"lifetime": "300s"})
        live_credentials.add("secret-id")
        return SimpleNamespace(id="secret-id", secret="one-use-secret")

    def delete_credential(principal_id: str, secret_id: str) -> None:
        live_credentials.remove(secret_id)
        deleted.append((principal_id, secret_id))

    account = SimpleNamespace(
        service_principal_secrets=SimpleNamespace(
            list=lambda principal_id: (
                SimpleNamespace(id=credential_id)
                for credential_id in sorted(live_credentials)
            ),
            create=create_credential,
            delete=delete_credential,
        )
    )

    def denied(**kwargs: object) -> object:
        rule_calls.append(kwargs)
        raise PermissionDenied("group manager permission required")

    target_workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda method, path, **kwargs: (
                {
                    "id": "app-scim",
                    "userName": "app-client",
                    "groups": [
                        {
                            "value": "hidden-parent-id",
                            "display": "hidden-account-parent",
                        }
                    ],
                }
                if (
                    method,
                    path,
                    kwargs,
                )
                == (
                    "GET",
                    "/api/2.0/preview/scim/v2/Me",
                    {
                        "query": {"attributes": "id,userName,groups"},
                        "headers": {"Accept": "application/json"},
                    },
                )
                else (_ for _ in ()).throw(
                    AssertionError((method, path, kwargs))
                )
            )
        ),
        groups=SimpleNamespace(patch=denied),
    )

    def workspace_factory(**kwargs: object) -> object:
        factory_kwargs.update(kwargs)
        return target_workspace

    groups = (
        boundary.boundary_probes
        .probe_target_managed_query_group_administration_boundary(
            account,
            account_sp_id="app-scim",
            application_id="app-client",
            expected_workspace_scim_id="app-scim",
            workspace_host="https://workspace.cloud.databricks.com",
            account_id="account-id",
            group_bindings=(
                boundary.boundary_probes.ManagedWorkspaceGroupBinding(
                    id="managed-group-id",
                    name="managed-group",
                    external_id="mip:serving-query:managed",
                    resource_type="WorkspaceGroup",
                ),
            ),
            assert_single_writer=lambda: None,
            workspace_factory=workspace_factory,
        )
    )

    assert groups == {"hidden-parent-id": "hidden-account-parent"}
    assert factory_kwargs == {
        "host": "https://workspace.cloud.databricks.com",
        "client_id": "app-client",
        "client_secret": "one-use-secret",
        "auth_type": "oauth-m2m",
    }
    assert [call["id"] for call in rule_calls] == ["managed-group-id"]
    assert deleted == [("app-scim", "secret-id")]


def test_target_credential_admin_capability_fails_and_deletes_secret() -> None:
    deleted: list[tuple[str, str]] = []
    live_credentials: set[str] = set()

    def create_credential(*_args: object, **_kwargs: object) -> object:
        live_credentials.add("secret-id")
        return SimpleNamespace(
            id="secret-id",
            secret="one-use-secret",
        )

    def delete_credential(principal_id: str, secret_id: str) -> None:
        live_credentials.remove(secret_id)
        deleted.append((principal_id, secret_id))

    account = SimpleNamespace(
        service_principal_secrets=SimpleNamespace(
            list=lambda _principal_id: (
                SimpleNamespace(id=credential_id)
                for credential_id in sorted(live_credentials)
            ),
            create=create_credential,
            delete=delete_credential,
        )
    )
    target_workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "id": "app-scim",
                "userName": "app-client",
                "groups": [],
            }
        ),
        groups=SimpleNamespace(patch=lambda **_kwargs: SimpleNamespace()),
    )

    with pytest.raises(RuntimeError, match="unexpectedly succeeded"):
        (
            boundary.boundary_probes
            .probe_target_managed_query_group_administration_boundary(
                account,
                account_sp_id="app-scim",
                application_id="app-client",
                expected_workspace_scim_id="app-scim",
                workspace_host="https://workspace.cloud.databricks.com",
                account_id="account-id",
                group_bindings=(
                    boundary.boundary_probes.ManagedWorkspaceGroupBinding(
                        id="managed-group-id",
                        name="managed-group",
                        external_id="mip:serving-query:managed",
                        resource_type="WorkspaceGroup",
                    ),
                ),
                assert_single_writer=lambda: None,
                workspace_factory=lambda **_kwargs: target_workspace,
            )
        )

    assert deleted == [("app-scim", "secret-id")]


def test_target_credential_commit_then_timeout_is_discovered_and_revoked() -> None:
    live_credentials: set[str] = set()
    deleted: list[tuple[str, str]] = []

    def create_credential(*_args: object, **_kwargs: object) -> object:
        live_credentials.add("committed-secret-id")
        raise TimeoutError("response lost after provider commit")

    def delete_credential(principal_id: str, secret_id: str) -> None:
        live_credentials.remove(secret_id)
        deleted.append((principal_id, secret_id))

    account = SimpleNamespace(
        service_principal_secrets=SimpleNamespace(
            list=lambda _principal_id: (
                SimpleNamespace(id=credential_id)
                for credential_id in sorted(live_credentials)
            ),
            create=create_credential,
            delete=delete_credential,
        )
    )

    with pytest.raises(TimeoutError, match="response lost"):
        boundary.boundary_probes.probe_target_managed_query_group_administration_boundary(
            account,
            account_sp_id="app-scim",
            application_id="app-client",
            expected_workspace_scim_id="app-scim",
            workspace_host="https://workspace.cloud.databricks.com",
            account_id="account-id",
            group_bindings=(),
            assert_single_writer=lambda: None,
            workspace_factory=lambda **_kwargs: pytest.fail(
                "workspace constructed after ambiguous credential create"
            ),
        )

    assert live_credentials == set()
    assert deleted == [("app-scim", "committed-secret-id")]


def _global_denied(*_args: object, **_kwargs: object) -> object:
    raise PermissionDenied("denied")


def _global_denial_workspace() -> object:
    return SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="verifier-client-id")),
        serving_endpoints=SimpleNamespace(
            get=_global_denied,
            get_permissions=_global_denied,
        ),
        genie=SimpleNamespace(get_space=_global_denied),
        api_client=SimpleNamespace(do=_global_denied),
    )


def _global_inventory() -> boundary.VerifierCustomerResourceDenialInventory:
    return boundary.VerifierCustomerResourceDenialInventory(
        serving_endpoints=(("customer-endpoint", "endpoint-id", "agent_v1_responses", False),),
        genie_space_ids=("genie-space",),
    )


def test_global_denial_probes_every_customer_serving_and_genie_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[object, str]] = []

    def denied_query(workspace: object, endpoint: str, **_kwargs: object) -> object:
        queries.append((workspace, endpoint))
        raise PermissionDenied("denied")

    workspace = _global_denial_workspace()
    monkeypatch.setattr(
        customer_denial,
        "query_serving_endpoint_with_proof",
        denied_query,
    )

    boundary.verify_customer_resource_denial_boundary(
        workspace=workspace,
        inventory=_global_inventory(),
        expected_application_id="verifier-client-id",
    )
    assert queries == [(workspace, "customer-endpoint")]


def test_global_denial_rejects_hidden_serving_query_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        customer_denial,
        "query_serving_endpoint_with_proof",
        lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(RuntimeError, match="query capability.*unexpectedly"):
        boundary.verify_customer_resource_denial_boundary(
            workspace=_global_denial_workspace(),
            inventory=_global_inventory(),
            expected_application_id="verifier-client-id",
        )


def test_global_denial_cli_captures_admin_inventory_before_binding_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin, verifier = object(), object()
    clients = iter((admin, verifier))
    observed: list[tuple[str, object]] = []
    inventory = _global_inventory()
    monkeypatch.setattr(boundary, "WorkspaceClient", lambda: next(clients))
    monkeypatch.setattr(
        boundary,
        "assert_workspace_admin_inventory_identity",
        lambda workspace, *, expected_principal: observed.append(
            (f"admin:{expected_principal}", workspace)
        ),
    )
    monkeypatch.setattr(
        boundary,
        "collect_admin_customer_resource_denial_inventory",
        lambda workspace: observed.append(("inventory", workspace)) or inventory,
    )
    monkeypatch.setattr(
        boundary,
        "bind_exact_workspace_m2m_auth",
        lambda **kwargs: (
            observed.append(("bind", kwargs["admin_workspace"])) or ("verifier-client-id", "secret")
        ),
    )
    monkeypatch.setattr(
        boundary,
        "verify_customer_resource_denial_boundary",
        lambda **kwargs: observed.append(("verify", kwargs["workspace"])),
    )

    assert (
        boundary.main(
            [
                "--expected-application-id",
                "verifier-client-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--customer-resource-denial",
            ]
        )
        == 0
    )
    assert observed == [
        ("admin:deployer@example.com", admin),
        ("inventory", admin),
        ("bind", admin),
        ("verify", verifier),
    ]
    output = capsys.readouterr().out
    assert "customer-created serving and Genie denial boundary" in output
    assert "foundation invocation not asserted" in output
    assert "global serving" not in output


def test_global_denial_cli_rejects_positive_target_arguments() -> None:
    with pytest.raises(SystemExit, match="target arguments"):
        boundary.main(
            [
                "--expected-application-id",
                "verifier-client-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--endpoint",
                "green-gateway",
                "--customer-resource-denial",
            ]
        )
