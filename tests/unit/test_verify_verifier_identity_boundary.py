from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import PermissionDenied
from databricks.sdk.service.apps import ComputeState

from tools.databricks import verify_verifier_identity_boundary as boundary

verify_boundary = boundary.verify_boundary


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

    monkeypatch.setattr(boundary, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(boundary, "AccountClient", account_client)
    monkeypatch.setattr(boundary, "verify_boundary", verify)

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
        return SimpleNamespace(name=endpoint, id=f"{endpoint}-id")

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
    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name="verifier-client-id")
        ),
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
        config=SimpleNamespace(
            host="https://workspace.cloud.databricks.com",
            authenticate=lambda: {"Authorization": "Bearer redacted"},
        ),
    )


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
    )


def _verify(**overrides: object) -> None:
    kwargs: dict[str, object] = {
        "workspace": _workspace(),
        "account": SimpleNamespace(service_principals=_DeniedAccountPrincipals()),
        "expected_application_id": "verifier-client-id",
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
            list=lambda **_kwargs: (_ for _ in ()).throw(
                _AuthenticationFailure("token expired")
            )
        )
    )

    with pytest.raises(RuntimeError, match="inconclusive"):
        _verify(account=account, http_get=lambda *_args, **_kwargs: SimpleNamespace(status_code=401))


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
