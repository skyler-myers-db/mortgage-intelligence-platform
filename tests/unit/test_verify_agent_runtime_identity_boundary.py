from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.errors import PermissionDenied

from tools.databricks import verify_agent_runtime_identity_boundary as boundary


def _workspace(*, sql_state: str = "FAILED", sql_error: object = "PERMISSION_DENIED") -> object:
    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name="runtime-client",
                display_name="mip-agent-runtime-ci-sp",
            )
        ),
        config=SimpleNamespace(authenticate=lambda: {"Authorization": "Bearer runtime"}),
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


def test_runtime_boundary_proves_app_admin_and_warehouse_denials() -> None:
    boundary.verify_boundary(
        _workspace(),
        expected_application_id="runtime-client",
        app_name="mip-app",
        app_url="https://mip-app.example",
        protected_service_principal_id="app-scim-id",
        warehouse_id="warehouse-id",
        http_get=lambda *_args, **_kwargs: SimpleNamespace(status_code=403),
    )


def test_runtime_boundary_rejects_successful_warehouse_query() -> None:
    with pytest.raises(RuntimeError, match="unexpectedly executed SQL"):
        boundary.verify_boundary(
            _workspace(sql_state="SUCCEEDED", sql_error=None),
            expected_application_id="runtime-client",
            app_name="mip-app",
            app_url="https://mip-app.example",
            protected_service_principal_id="app-scim-id",
            warehouse_id="warehouse-id",
            http_get=lambda *_args, **_kwargs: SimpleNamespace(status_code=403),
        )


def test_runtime_boundary_rejects_app_http_access() -> None:
    with pytest.raises(RuntimeError, match="App denial probe unexpectedly returned"):
        boundary.verify_boundary(
            _workspace(),
            expected_application_id="runtime-client",
            app_name="mip-app",
            app_url="https://mip-app.example",
            protected_service_principal_id="app-scim-id",
            warehouse_id="warehouse-id",
            http_get=lambda *_args, **_kwargs: SimpleNamespace(status_code=200),
        )
