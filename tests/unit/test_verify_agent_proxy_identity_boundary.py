from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.errors import PermissionDenied, ResourceDoesNotExist

from tools.databricks.verify_agent_proxy_identity_boundary import (
    AgentProxyBoundaryInventory,
    _expect_denied,
    _is_denied,
    _verify_warehouse_denial,
    verify_boundary,
)

PROXY_ID = "proxy-client"
TARGET_WAREHOUSE = "warehouse-target"
TARGET_SUPERVISOR = "supervisor-target"
TARGET_GENIE = "genie-target"


def _denied(*_args: object, **_kwargs: object) -> object:
    raise PermissionDenied("permission denied")


class _SupervisorApi:
    def __init__(self, *, expose_non_target: bool = False) -> None:
        self.expose_non_target = expose_non_target

    def do(self, _method: str, path: str) -> object:
        identifier = path.rsplit("/", 1)[-1]
        if identifier == TARGET_SUPERVISOR or self.expose_non_target:
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


def _workspace(
    *,
    app_admin_succeeds: bool = False,
    expose_non_target_supervisor: bool = False,
    expose_non_target_genie: bool = False,
    lakebase_succeeds: bool = False,
    metastore_admin_succeeds: bool = False,
    serving_metadata_succeeds: bool = False,
    warehouse_succeeds: bool = False,
    warehouse_metadata_succeeds: bool = False,
    secret_listing_succeeds: bool = False,
    secret_scope_succeeds: bool = False,
) -> object:
    secret_list = (lambda *_args, **_kwargs: iter(())) if secret_listing_succeeds else _denied
    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(application_id=PROXY_ID, user_name=PROXY_ID)
        ),
        config=SimpleNamespace(authenticate=lambda: {"Authorization": "Bearer proxy"}),
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
        api_client=_SupervisorApi(expose_non_target=expose_non_target_supervisor),
        genie=_Genie(expose_non_target=expose_non_target_genie),
        serving_endpoints=SimpleNamespace(
            get=(lambda *_args: object()) if serving_metadata_succeeds else _denied
        ),
    )


def _account(*, admin_succeeds: bool = False) -> object:
    operation = (lambda **_kwargs: iter(())) if admin_succeeds else _denied
    return SimpleNamespace(service_principals=SimpleNamespace(list=operation))


def _verify(
    workspace: object,
    *,
    account: object | None = None,
    app_status: int = 403,
) -> None:
    verify_boundary(
        workspace=workspace,
        account=account or _account(),
        inventory=_inventory(),
        expected_application_id=PROXY_ID,
        app_name="mip-app",
        warehouse_id=TARGET_WAREHOUSE,
        supervisor_id=TARGET_SUPERVISOR,
        genie_space_id=TARGET_GENIE,
        http_get=lambda *_args, **_kwargs: SimpleNamespace(status_code=app_status),
    )


def test_proxy_boundary_proves_target_only_supervisor_and_genie_access() -> None:
    _verify(_workspace())


def test_proxy_boundary_rejects_hidden_non_target_supervisor_access() -> None:
    with pytest.raises(RuntimeError, match="non-target Supervisor"):
        _verify(_workspace(expose_non_target_supervisor=True))


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


def test_proxy_boundary_rejects_app_authentication_failure() -> None:
    with pytest.raises(RuntimeError, match="App denial"):
        _verify(_workspace(), app_status=401)


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
