#!/usr/bin/env python3
"""Prove the agent-proxy effective boundary with its own OAuth credential."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import requests

from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout
from tools.databricks.agent_proxy_access import _supervisor_agents
from tools.databricks.agent_runtime_access import _genie_spaces
from tools.databricks.authorization_denial import is_authorization_denied

_AMBIENT_AUTH_KEYS = (
    "DATABRICKS_ACCOUNT_CLIENT_ID",
    "DATABRICKS_ACCOUNT_CLIENT_SECRET",
    "DATABRICKS_CONFIG_PROFILE",
    "DATABRICKS_PASSWORD",
    "DATABRICKS_TOKEN",
    "DATABRICKS_USERNAME",
)
_MAX_INVENTORY = 1000


def _text(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _is_denied(exc: BaseException, *, allow_hidden_resource: bool = True) -> bool:
    return is_authorization_denied(
        exc,
        allow_hidden_resource=allow_hidden_resource,
    )


def _warehouse_error_is_denial(error: object) -> bool:
    return is_authorization_denied(error, allow_hidden_resource=True)


def _expect_denied(
    label: str,
    operation: Callable[[], object],
    *,
    allow_hidden_resource: bool = True,
) -> None:
    try:
        operation()
    except Exception as exc:  # noqa: BLE001 - classify the provider denial
        if _is_denied(exc, allow_hidden_resource=allow_hidden_resource):
            return
        raise RuntimeError(f"{label} was inconclusive: {type(exc).__name__}: {exc}") from exc
    raise RuntimeError(f"{label} unexpectedly succeeded")


def _bounded_unique(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    result = tuple(values)
    if (
        not result
        or len(result) > _MAX_INVENTORY
        or any(not value for value in result)
        or len(result) != len(set(result))
    ):
        raise RuntimeError(f"{label} inventory is empty, duplicated, or unbounded")
    return tuple(sorted(result))


def _validated_app_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or not parsed.hostname
        or not parsed.hostname.endswith(".databricksapps.com")
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("admin App inventory returned an unsafe URL")
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class AgentProxyBoundaryInventory:
    app_url: str
    app_names: tuple[str, ...]
    app_urls: tuple[str, ...]
    metastore_id: str
    secret_scope_names: tuple[str, ...]
    service_principal_ids: tuple[str, ...]
    lakebase_instances: tuple[str, ...]
    warehouse_ids: tuple[str, ...]
    supervisor_ids: tuple[str, ...]
    genie_space_ids: tuple[str, ...]
    serving_endpoint_names: tuple[str, ...]


def collect_admin_inventory(
    workspace: Any,
    *,
    app_name: str,
    app_url: str,
    lakebase_instance: str,
    warehouse_id: str,
    supervisor_id: str,
    genie_space_id: str,
) -> AgentProxyBoundaryInventory:
    """Capture an admin-complete immutable inventory before binding proxy auth."""

    app_names = _bounded_unique(
        (_text(item, "name") for item in workspace.apps.list()),
        label="App",
    )
    app_urls = tuple(
        _validated_app_url(_text(workspace.apps.get(name), "url"))
        for name in app_names
    )
    if len(app_urls) != len(set(app_urls)):
        raise RuntimeError("App URL inventory is duplicated")
    if app_name not in app_names:
        raise RuntimeError("configured App is absent from admin inventory")
    observed_url = app_urls[app_names.index(app_name)]
    if observed_url != _validated_app_url(app_url):
        raise RuntimeError("configured App URL does not match admin inventory")
    assignment = workspace.metastores.current()
    metastore_id = _text(assignment, "metastore_id")
    if not metastore_id:
        raise RuntimeError("workspace metastore assignment has no immutable id")
    service_principal_ids = _bounded_unique(
        (
            _text(item, "id")
            for item in workspace.service_principals.list(attributes="id,applicationId")
        ),
        label="service-principal",
    )
    secret_scope_names = _bounded_unique(
        (_text(item, "name") for item in workspace.secrets.list_scopes()),
        label="secret-scope",
    )
    lakebase_instances = _bounded_unique(
        (_text(item, "name") for item in workspace.database.list_database_instances()),
        label="Lakebase",
    )
    warehouse_ids = _bounded_unique(
        (_text(item, "id") for item in workspace.warehouses.list()),
        label="warehouse",
    )
    supervisor_ids = _bounded_unique(
        _supervisor_agents(workspace),
        label="Supervisor",
    )
    genie_space_ids = _bounded_unique(
        _genie_spaces(workspace),
        label="Genie",
    )
    serving_endpoint_names = _bounded_unique(
        (_text(item, "name") for item in workspace.serving_endpoints.list()),
        label="serving-endpoint",
    )
    expected_members = (
        (lakebase_instance, lakebase_instances, "Lakebase instance"),
        (warehouse_id, warehouse_ids, "warehouse"),
        (supervisor_id, supervisor_ids, "Supervisor"),
        (genie_space_id, genie_space_ids, "Genie space"),
    )
    for expected, inventory, label in expected_members:
        if expected not in inventory:
            raise RuntimeError(f"target {label} is absent from admin inventory")
    return AgentProxyBoundaryInventory(
        app_url=observed_url,
        app_names=app_names,
        app_urls=app_urls,
        metastore_id=metastore_id,
        secret_scope_names=secret_scope_names,
        service_principal_ids=service_principal_ids,
        lakebase_instances=lakebase_instances,
        warehouse_ids=warehouse_ids,
        supervisor_ids=supervisor_ids,
        genie_space_ids=genie_space_ids,
        serving_endpoint_names=serving_endpoint_names,
    )


def _verify_app_denial(
    workspace: Any,
    *,
    app_url: str,
    http_get: Callable[..., Any],
) -> None:
    response = http_get(
        f"{app_url}/api/v1/health",
        headers=dict(workspace.config.authenticate()),
        allow_redirects=False,
        timeout=30,
    )
    if response.status_code != 403:
        raise RuntimeError(
            "agent-proxy Databricks App denial unexpectedly returned "
            f"status={response.status_code}"
        )


def _verify_warehouse_denial(workspace: Any, *, warehouse_id: str) -> None:
    try:
        response = workspace.statement_execution.execute_statement(
            statement="SELECT 1",
            warehouse_id=warehouse_id,
            wait_timeout="10s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
        )
    except Exception as exc:  # noqa: BLE001 - classify the provider denial
        if _is_denied(exc):
            return
        raise RuntimeError(
            f"agent-proxy warehouse denial was inconclusive: {type(exc).__name__}: {exc}"
        ) from exc
    status = getattr(response, "status", None)
    state = (
        str(getattr(getattr(status, "state", None), "value", getattr(status, "state", "")) or "")
        .split(".")[-1]
        .upper()
    )
    error = getattr(status, "error", None)
    if state in {"FAILED", "CANCELED", "CLOSED"} and _warehouse_error_is_denial(error):
        return
    if state == "SUCCEEDED":
        raise RuntimeError("agent-proxy unexpectedly executed SQL")
    raise RuntimeError(f"agent-proxy warehouse denial was inconclusive: state={state or 'UNKNOWN'}")


def verify_boundary(
    *,
    workspace: Any,
    account: Any,
    inventory: AgentProxyBoundaryInventory,
    expected_application_id: str,
    app_name: str,
    warehouse_id: str,
    supervisor_id: str,
    genie_space_id: str,
    http_get: Callable[..., Any] = requests.get,
) -> None:
    """Run positive target and exhaustive negative probes under proxy OAuth."""

    me = workspace.current_user.me()
    authenticated = {
        _text(me, "application_id"),
        _text(me, "user_name"),
    }
    if expected_application_id not in authenticated:
        raise RuntimeError("authenticated agent-proxy identity does not match its application id")
    _expect_denied(
        "account administrator service-principal listing probe",
        lambda: list(account.service_principals.list(count=1)),
        allow_hidden_resource=False,
    )
    if app_name not in inventory.app_names:
        raise RuntimeError("target App is absent from immutable admin inventory")
    for candidate_app in inventory.app_names:
        _expect_denied(
            f"workspace App permission-administration probe {candidate_app}",
            partial(workspace.apps.get_permissions, candidate_app),
        )
    for candidate_url in inventory.app_urls:
        _verify_app_denial(workspace, app_url=candidate_url, http_get=http_get)
    _expect_denied(
        "metastore administrator GET probe",
        lambda: workspace.metastores.get(inventory.metastore_id),
    )
    for principal_id in inventory.service_principal_ids:
        _expect_denied(
            f"service-principal secret listing {principal_id}",
            lambda principal_id=principal_id: list(
                workspace.service_principal_secrets_proxy.list(
                    principal_id,
                    page_size=1,
                )
            ),
    )
    for instance_name in inventory.lakebase_instances:
        _expect_denied(
            f"Lakebase role inventory {instance_name}",
            partial(
                lambda database, name: list(
                    database.list_database_instance_roles(name, page_size=1)
                ),
                workspace.database,
                instance_name,
            ),
        )
    for scope_name in inventory.secret_scope_names:
        _expect_denied(
            f"secret-scope key inventory {scope_name}",
            lambda scope_name=scope_name: list(
                workspace.secrets.list_secrets(scope=scope_name)
            ),
        )
    for candidate in inventory.warehouse_ids:
        _expect_denied(
            f"warehouse metadata {candidate}",
            partial(workspace.warehouses.get, candidate),
        )
    _verify_warehouse_denial(workspace, warehouse_id=warehouse_id)

    target_supervisor = workspace.api_client.do(
        "GET",
        f"/api/2.1/supervisor-agents/{supervisor_id}",
    )
    if _text(target_supervisor, "supervisor_agent_id") != supervisor_id:
        raise RuntimeError("agent-proxy target Supervisor identity drifted")
    for candidate in inventory.supervisor_ids:
        if candidate != supervisor_id:
            _expect_denied(
                f"non-target Supervisor {candidate}",
                lambda candidate=candidate: workspace.api_client.do(
                    "GET",
                    f"/api/2.1/supervisor-agents/{candidate}",
                ),
            )

    target_genie = workspace.genie.get_space(genie_space_id)
    if _text(target_genie, "space_id") != genie_space_id:
        raise RuntimeError("agent-proxy target Genie identity drifted")
    for candidate in inventory.genie_space_ids:
        if candidate != genie_space_id:
            _expect_denied(
                f"non-target Genie space {candidate}",
                partial(workspace.genie.get_space, candidate),
            )
    for endpoint in inventory.serving_endpoint_names:
        _expect_denied(
            f"serving endpoint metadata {endpoint}",
            partial(workspace.serving_endpoints.get, endpoint),
        )


def _bind_proxy_auth(*, admin_workspace: Any, application_id: str) -> tuple[str, str]:
    configured_id = os.environ.get("DATABRICKS_AGENT_PROXY_CLIENT_ID", "").strip()
    secret = os.environ.get("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", "").strip()
    host = _text(getattr(admin_workspace, "config", None), "host")
    if configured_id != application_id.strip() or not secret or not host:
        raise RuntimeError("agent-proxy identity verifier lacks its exact OAuth credential or host")
    for key in _AMBIENT_AUTH_KEYS:
        os.environ.pop(key, None)
    os.environ.pop("DATABRICKS_AGENT_PROXY_CLIENT_SECRET", None)
    os.environ["DATABRICKS_HOST"] = host
    os.environ["DATABRICKS_AUTH_TYPE"] = "oauth-m2m"
    os.environ["DATABRICKS_CLIENT_ID"] = configured_id
    os.environ["DATABRICKS_CLIENT_SECRET"] = secret
    os.environ["MIP_DISABLE_DOTENV"] = "1"
    return configured_id, secret


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-application-id", required=True)
    parser.add_argument("--account-host", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--lakebase-instance", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--supervisor-id", required=True)
    parser.add_argument("--genie-space-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    admin_workspace = WorkspaceClient()
    inventory = collect_admin_inventory(
        admin_workspace,
        app_name=args.app_name,
        app_url=args.app_url,
        lakebase_instance=args.lakebase_instance,
        warehouse_id=args.warehouse_id,
        supervisor_id=args.supervisor_id,
        genie_space_id=args.genie_space_id,
    )
    client_id, client_secret = _bind_proxy_auth(
        admin_workspace=admin_workspace,
        application_id=args.expected_application_id,
    )
    proxy_workspace = WorkspaceClient()
    proxy_account = AccountClient(
        host=args.account_host,
        account_id=args.account_id,
        client_id=client_id,
        client_secret=client_secret,
        auth_type="oauth-m2m",
    )
    verify_boundary(
        workspace=proxy_workspace,
        account=proxy_account,
        inventory=inventory,
        expected_application_id=args.expected_application_id,
        app_name=args.app_name,
        warehouse_id=args.warehouse_id,
        supervisor_id=args.supervisor_id,
        genie_space_id=args.genie_space_id,
    )
    print("agent-proxy effective authorization boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
