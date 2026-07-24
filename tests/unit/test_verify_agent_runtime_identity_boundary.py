from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import PermissionDenied
from databricks.sdk.service.apps import ComputeState

from tools.databricks import verify_agent_runtime_identity_boundary as boundary


def _runtime_main_args(*, include_attested_mode: bool = True) -> list[str]:
    args = [
        "--expected-application-id",
        "runtime-client",
        "--app-name",
        "mip-app",
        "--app-url",
        "https://mip-app.databricksapps.com",
        "--protected-service-principal-id",
        "app-scim",
        "--warehouse-id",
        "warehouse-id",
    ]
    if include_attested_mode:
        args.append("--allow-attested-app-401")
    return args


def test_runtime_main_rejects_non_attested_mode_before_constructing_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_DISCOVERY_URL", "https://attacker.invalid")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "runtime-client")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "runtime-secret")
    monkeypatch.setattr(
        boundary,
        "WorkspaceClient",
        lambda: pytest.fail("workspace client constructed in unsafe mode"),
    )

    with pytest.raises(RuntimeError, match="dual-authority App attestation mode"):
        boundary.main(_runtime_main_args(include_attested_mode=False))


def test_main_captures_admin_then_binds_exact_runtime_m2m(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "pat")
    monkeypatch.setenv("DATABRICKS_TOKEN", "admin-token")
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_ID", "runtime-client")
    monkeypatch.setenv("DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", "runtime-secret")
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

    observed: dict[str, object] = {}

    def verify(workspace: object, **kwargs: object) -> None:
        observed["workspace"] = workspace
        observed.update(kwargs)

    monkeypatch.setattr(boundary, "WorkspaceClient", workspace_client)
    monkeypatch.setattr(boundary, "verify_boundary", verify)

    assert boundary.main(_runtime_main_args()) == 0

    assert auth_at_construction == [
        ("pat", "admin-token", ""),
        ("oauth-m2m", "", "runtime-client"),
    ]
    assert observed["workspace"] is clients[1]
    assert observed["admin_workspace"] is clients[0]
    assert observed["allow_attested_app_401"] is True
    assert "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET" not in os.environ


def _workspace(*, sql_state: str = "FAILED", sql_error: object = "PERMISSION_DENIED") -> object:
    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name="runtime-client",
                display_name="mip-agent-runtime-ci-sp",
            )
        ),
        config=SimpleNamespace(
            host="https://workspace.cloud.databricks.com",
            authenticate=lambda: {"Authorization": "Bearer runtime"},
        ),
        apps=SimpleNamespace(
            get_permissions=lambda _name: (_ for _ in ()).throw(PermissionDenied("denied"))
        ),
        service_principal_secrets_proxy=SimpleNamespace(
            list=lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionDenied("denied"))
        ),
        statement_execution=SimpleNamespace(
            execute_statement=lambda **_kwargs: SimpleNamespace(
                status=SimpleNamespace(
                    state=sql_state,
                    error=sql_error,
                )
            )
        ),
    )


def _http_get(app_status: int, *, identity_status: int = 200):
    def get(url: str, **_kwargs: object) -> object:
        if url.endswith("/api/2.0/preview/scim/v2/Me"):
            return SimpleNamespace(
                status_code=identity_status,
                json=lambda: {
                    "id": "runtime-scim",
                    "userName": "runtime-client",
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
                        id="runtime-scim",
                        application_id="runtime-client",
                        display_name="mip-agent-runtime-ci-sp",
                    ),
                )
            )
        ),
    )


def test_runtime_boundary_proves_app_admin_and_warehouse_denials() -> None:
    boundary.verify_boundary(
        _workspace(),
        expected_application_id="runtime-client",
        app_name="mip-app",
        app_url="https://mip-app.databricksapps.com",
        protected_service_principal_id="app-scim-id",
        warehouse_id="warehouse-id",
        http_get=_http_get(403),
    )


def test_runtime_boundary_rejects_successful_warehouse_query() -> None:
    with pytest.raises(RuntimeError, match="unexpectedly executed SQL"):
        boundary.verify_boundary(
            _workspace(sql_state="SUCCEEDED", sql_error=None),
            expected_application_id="runtime-client",
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            protected_service_principal_id="app-scim-id",
            warehouse_id="warehouse-id",
            http_get=_http_get(403),
        )


def test_runtime_boundary_rejects_provider_401_without_admin_attestation() -> None:
    with pytest.raises(RuntimeError, match="uncorroborated status=401"):
        boundary.verify_boundary(
            _workspace(),
            expected_application_id="runtime-client",
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            protected_service_principal_id="app-scim-id",
            warehouse_id="warehouse-id",
            http_get=_http_get(401),
        )


def test_runtime_boundary_accepts_active_401_with_admin_attestation() -> None:
    boundary.verify_boundary(
        _workspace(),
        expected_application_id="runtime-client",
        app_name="mip-app",
        app_url="https://mip-app.databricksapps.com",
        protected_service_principal_id="app-scim-id",
        warehouse_id="warehouse-id",
        admin_workspace=_admin_workspace(),
        allow_attested_app_401=True,
        http_get=_http_get(401),
    )


@pytest.mark.parametrize("status_code", (200, 404))
def test_runtime_boundary_rejects_app_http_non_denial(status_code: int) -> None:
    with pytest.raises(RuntimeError, match="App denial probe unexpectedly returned"):
        boundary.verify_boundary(
            _workspace(),
            expected_application_id="runtime-client",
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            protected_service_principal_id="app-scim-id",
            warehouse_id="warehouse-id",
            http_get=_http_get(status_code),
        )


def test_runtime_boundary_rejects_bare_401_without_exact_bearer_identity() -> None:
    with pytest.raises(RuntimeError, match="preflight identity proof"):
        boundary.verify_boundary(
            _workspace(),
            expected_application_id="runtime-client",
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            protected_service_principal_id="app-scim-id",
            warehouse_id="warehouse-id",
            http_get=_http_get(401, identity_status=401),
        )


@pytest.mark.parametrize(
    "message",
    (
        "retry after 403 seconds",
        "job 401 is still running",
        "request token 1403 expired",
    ),
)
def test_runtime_boundary_rejects_incidental_status_numbers(message: str) -> None:
    with pytest.raises(RuntimeError, match="inconclusive"):
        boundary._expect_denied(
            "runtime denial",
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
def test_runtime_boundary_accepts_structured_denial_evidence(error: object) -> None:
    assert boundary._is_denied(error)


@pytest.mark.parametrize(
    "error",
    (
        "retry after 403 seconds",
        "job 401 is still running",
        "request token 1403 expired",
        {"status_code": 401},
        {"http_status_code": "401"},
        {"error_code": "UNAUTHENTICATED"},
        {"code": "UNAUTHORIZED"},
        SimpleNamespace(response=SimpleNamespace(status_code=401)),
    ),
)
def test_runtime_warehouse_rejects_non_authorization_evidence(error: object) -> None:
    with pytest.raises(RuntimeError, match="inconclusive"):
        boundary._verify_warehouse_denial(
            _workspace(sql_error=error),
            warehouse_id="warehouse-id",
        )


class _AuthenticationFailure(RuntimeError):
    status_code = 401


def test_runtime_boundary_rejects_expired_control_plane_authentication() -> None:
    workspace = _workspace(sql_error={"status_code": 401})
    workspace.apps.get_permissions = lambda _name: (_ for _ in ()).throw(
        _AuthenticationFailure("token expired")
    )
    workspace.service_principal_secrets_proxy.list = lambda *_args, **_kwargs: (
        _ for _ in ()
    ).throw(_AuthenticationFailure("token expired"))

    with pytest.raises(RuntimeError, match="inconclusive"):
        boundary.verify_boundary(
            workspace,
            expected_application_id="runtime-client",
            app_name="mip-app",
            app_url="https://mip-app.databricksapps.com",
            protected_service_principal_id="app-scim-id",
            warehouse_id="warehouse-id",
            http_get=lambda *_args, **_kwargs: SimpleNamespace(status_code=401),
        )
