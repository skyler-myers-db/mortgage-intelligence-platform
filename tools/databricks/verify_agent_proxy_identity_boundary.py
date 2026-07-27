"""Prove the agent-proxy effective boundary with its own OAuth credential."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

import requests

from backend.services.capability_serving_probes import query_serving_endpoint_with_proof
from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout
from tools.databricks.agent_proxy_access import _supervisor_agents
from tools.databricks.agent_proxy_identity_inventory_groups import (
    collect_managed_proxy_workspace_groups,
    reviewed_agent_proxy_capability_group_bindings,
)
from tools.databricks.agent_runtime_access import _genie_spaces
from tools.databricks.audit_global_m2m_access import (
    assert_workspace_admin_inventory_identity,
)
from tools.databricks.authenticated_app_denial import (
    verify_authenticated_app_denial,
)
from tools.databricks.authorization_denial import is_authorization_denied
from tools.databricks.identity_boundary_probes import (
    ManagedWorkspaceGroupBinding,
    verify_managed_query_group_administration_denied,
)
from tools.databricks.m2m_workspace_auth import (
    bind_exact_workspace_m2m_auth,
    reviewed_databricks_account_origin,
)
from tools.databricks.serving_endpoint_acl import is_platform_foundation_endpoint
from tools.databricks.serving_query_authorization_convergence import (
    _groups as _projected_identity_groups,
)
from tools.databricks.serving_query_authorization_convergence import (
    is_exact_target_supervisor_response,
    query_serving_endpoint_after_authorization,
    wait_for_managed_query_group_projection,
    wait_for_reviewed_query_group_projections,
)
from tools.databricks.serving_query_group_access import (
    managed_query_group_external_id,
    managed_query_group_name,
)

_MAX_INVENTORY = 1000
_TARGET_QUERY_PROMPT = (
    "Confirm that the governed Mortgage Growth Agent is ready for a "
    "human-review-only workflow. Do not call tools or include borrower data."
)


def _text(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _is_denied(exc: BaseException, *, allow_hidden_resource: bool = True) -> bool:
    return is_authorization_denied(exc, allow_hidden_resource=allow_hidden_resource)


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


def _verify_foundation_metadata_or_denied(workspace: Any, endpoint_name: str) -> None:
    try:
        details = workspace.serving_endpoints.get(endpoint_name)
    except Exception as exc:  # noqa: BLE001 - classify provider authorization
        if _is_denied(exc):
            return
        raise RuntimeError(
            f"foundation endpoint metadata {endpoint_name} was inconclusive: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not is_platform_foundation_endpoint(details):
        raise RuntimeError(
            f"non-reviewed endpoint {endpoint_name!r} was visible but is not a "
            "Databricks system.ai foundation endpoint"
        )


def _list_service_principal_secrets(workspace: Any, principal_id: str) -> list[object]:
    return list(workspace.service_principal_secrets_proxy.list(principal_id, page_size=1))


def _list_scope_secrets(workspace: Any, scope_name: str) -> list[object]:
    return list(workspace.secrets.list_secrets(scope=scope_name))


def _bounded_unique(
    values: Iterable[str], *, label: str, allow_empty: bool = False
) -> tuple[str, ...]:
    result = tuple(values)
    if (
        (not result and not allow_empty)
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
    foundation_endpoint_names: tuple[str, ...]
    managed_query_group_ids: tuple[str, ...]
    reviewed_supervisor_bindings: tuple[tuple[str, str, str], ...]
    reviewed_query_group_bindings: tuple[tuple[str, str, str, str], ...]
    reviewed_capability_group_bindings: tuple[tuple[str, str, str, str, str], ...] = ()
    managed_query_group_bindings: tuple[ManagedWorkspaceGroupBinding, ...] = ()


@dataclass(frozen=True)
class AgentProxyCustomerResourceDenialInventory:
    supervisor_ids: tuple[str, ...]
    genie_space_ids: tuple[str, ...]
    serving_endpoints: tuple[tuple[str, str, str, bool], ...]
    managed_query_group_ids: tuple[str, ...]
    managed_query_group_bindings: tuple[ManagedWorkspaceGroupBinding, ...] = ()


def collect_admin_customer_resource_denial_inventory(workspace: Any) -> AgentProxyCustomerResourceDenialInventory:
    """Capture customer agent resources plus classified foundation metadata."""
    supervisor_ids = _bounded_unique(
        _supervisor_agents(workspace), label="Supervisor", allow_empty=True
    )
    genie_space_ids = _bounded_unique(_genie_spaces(workspace), label="Genie", allow_empty=True)
    endpoint_names = _bounded_unique(
        (_text(item, "name") for item in workspace.serving_endpoints.list()),
        label="serving-endpoint",
        allow_empty=True,
    )
    endpoints: list[tuple[str, str, str, bool]] = []
    for name in endpoint_names:
        details = workspace.serving_endpoints.get(name)
        foundation = is_platform_foundation_endpoint(details)
        endpoint_id = _text(details, "id")
        task = _text(details, "task")
        if not foundation and (_text(details, "name") != name or not endpoint_id or not task):
            raise RuntimeError(
                f"non-foundation serving endpoint {name!r} lacks identity or query protocol"
            )
        endpoints.append((name, endpoint_id, task, foundation))
    managed_groups = collect_managed_proxy_workspace_groups(workspace)
    return AgentProxyCustomerResourceDenialInventory(
        supervisor_ids=supervisor_ids,
        genie_space_ids=genie_space_ids,
        serving_endpoints=tuple(endpoints),
        managed_query_group_ids=tuple(group.id for group in managed_groups),
        managed_query_group_bindings=managed_groups,
    )


def collect_admin_inventory(
    workspace: Any,
    *,
    app_name: str,
    app_url: str,
    lakebase_instance: str,
    warehouse_id: str,
    supervisor_id: str,
    supervisor_endpoint: str,
    supervisor_endpoint_id: str,
    genie_space_id: str,
    expected_application_id: str,
    preserved_supervisor_bindings: tuple[tuple[str, str, str], ...] = (),
) -> AgentProxyBoundaryInventory:
    """Capture an admin-complete immutable inventory before binding proxy auth."""
    app_names = _bounded_unique(
        (_text(item, "name") for item in workspace.apps.list()), label="App"
    )
    app_urls = tuple(
        _validated_app_url(_text(workspace.apps.get(name), "url")) for name in app_names
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
        (_text(item, "name") for item in workspace.secrets.list_scopes()), label="secret-scope"
    )
    lakebase_instances = _bounded_unique(
        (_text(item, "name") for item in workspace.database.list_database_instances()),
        label="Lakebase",
    )
    warehouse_ids = _bounded_unique(
        (_text(item, "id") for item in workspace.warehouses.list()), label="warehouse"
    )
    supervisor_ids = _bounded_unique(_supervisor_agents(workspace), label="Supervisor")
    requested_bindings = (
        (
            supervisor_id.strip(),
            supervisor_endpoint.strip(),
            supervisor_endpoint_id.strip(),
        ),
        *(
            (candidate_id.strip(), endpoint.strip(), endpoint_id.strip())
            for candidate_id, endpoint, endpoint_id in preserved_supervisor_bindings
        ),
    )
    if (
        any(
            not candidate_id or not endpoint or not endpoint_id
            for candidate_id, endpoint, endpoint_id in requested_bindings
        )
        or len({candidate_id for candidate_id, _endpoint, _endpoint_id in requested_bindings})
        != len(requested_bindings)
        or len({endpoint for _candidate_id, endpoint, _endpoint_id in requested_bindings})
        != len(requested_bindings)
        or len({endpoint_id for _id, _endpoint, endpoint_id in requested_bindings})
        != len(requested_bindings)
    ):
        raise RuntimeError("reviewed Supervisor bindings are empty or duplicated")
    reviewed_bindings: list[tuple[str, str, str]] = []
    for candidate_id, endpoint_name, expected_endpoint_id in requested_bindings:
        target_supervisor = workspace.api_client.do(
            "GET",
            f"/api/2.1/supervisor-agents/{quote(candidate_id, safe='')}",
        )
        if (
            _text(target_supervisor, "supervisor_agent_id") != candidate_id
            or _text(target_supervisor, "endpoint_name") != endpoint_name
        ):
            raise RuntimeError("configured Supervisor ID and endpoint binding drifted")
        endpoint = workspace.serving_endpoints.get(endpoint_name)
        endpoint_id = _text(endpoint, "id")
        if _text(endpoint, "name") != endpoint_name or endpoint_id != expected_endpoint_id:
            raise RuntimeError("configured Supervisor endpoint identity drifted")
        reviewed_bindings.append((candidate_id, endpoint_name, endpoint_id))
    managed_groups = collect_managed_proxy_workspace_groups(workspace)
    reviewed_capability_groups = reviewed_agent_proxy_capability_group_bindings(
        managed_groups,
        reviewed_supervisor_bindings=tuple(reviewed_bindings),
        genie_space_id=genie_space_id,
        expected_application_id=expected_application_id,
    )
    reviewed_query_groups: list[tuple[str, str, str, str]] = []
    for _candidate_id, _endpoint_name, endpoint_id in reviewed_bindings:
        expected_name = managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=expected_application_id,
        )
        expected_external_id = managed_query_group_external_id(
            endpoint_id=endpoint_id,
            application_id=expected_application_id,
        )
        matches = tuple(
            group
            for group in managed_groups
            if group.name == expected_name and group.external_id == expected_external_id
        )
        if len(matches) != 1:
            raise RuntimeError(
                "reviewed managed serving-query group contract drifted"
            )
        matched = matches[0]
        reviewed_query_groups.append(
            (endpoint_id, matched.name, matched.id, matched.external_id)
        )
    genie_space_ids = _bounded_unique(_genie_spaces(workspace), label="Genie")
    serving_endpoint_names = _bounded_unique(
        (_text(item, "name") for item in workspace.serving_endpoints.list()),
        label="serving-endpoint",
    )
    foundation_endpoint_names = tuple(
        name
        for name in serving_endpoint_names
        if is_platform_foundation_endpoint(workspace.serving_endpoints.get(name))
    )
    expected_members: tuple[tuple[str, tuple[str, ...], str], ...] = (
        (lakebase_instance, lakebase_instances, "Lakebase instance"),
        (warehouse_id, warehouse_ids, "warehouse"),
        (genie_space_id, genie_space_ids, "Genie space"),
    )
    expected_members += tuple(
        (candidate_id, supervisor_ids, "Supervisor")
        for candidate_id, _endpoint, _endpoint_id in reviewed_bindings
    )
    expected_members += tuple(
        (endpoint, serving_endpoint_names, "Supervisor endpoint")
        for _candidate_id, endpoint, _endpoint_id in reviewed_bindings
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
        foundation_endpoint_names=foundation_endpoint_names,
        managed_query_group_ids=tuple(group.id for group in managed_groups),
        reviewed_supervisor_bindings=tuple(reviewed_bindings),
        reviewed_query_group_bindings=tuple(reviewed_query_groups),
        reviewed_capability_group_bindings=reviewed_capability_groups,
        managed_query_group_bindings=managed_groups,
    )


def _verify_app_denial(
    workspace: Any,
    *,
    expected_application_id: str,
    app_url: str,
    http_get: Callable[..., Any],
    admin_workspace: Any | None = None,
    app_name: str | None = None,
    allow_attested_app_401: bool = False,
) -> None:
    verify_authenticated_app_denial(
        workspace,
        expected_application_id=expected_application_id,
        app_url=app_url,
        label="agent-proxy Databricks App denial",
        http_get=http_get,
        admin_workspace=admin_workspace,
        app_name=app_name,
        allow_attested_app_401=allow_attested_app_401,
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


def _verify_target_supervisor_query(
    workspace: Any,
    *,
    supervisor_endpoint: str,
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    try:
        execution = query_serving_endpoint_after_authorization(
            workspace,
            supervisor_endpoint=supervisor_endpoint,
            prompt=_TARGET_QUERY_PROMPT,
            sleep=sleep,
            clock=clock,
        )
    except Exception as exc:  # noqa: BLE001 - positive provider proof must be exact
        raise RuntimeError(
            "agent-proxy target Supervisor query was inconclusive: " f"{type(exc).__name__}: {exc}"
        ) from exc
    if not is_exact_target_supervisor_response(
        execution,
        supervisor_endpoint=supervisor_endpoint,
    ):
        raise RuntimeError(
            "agent-proxy target Supervisor query did not return the exact "
            "terminal Agent Responses payload"
        )


def verify_target_query_boundary(
    *,
    workspace: Any,
    inventory: AgentProxyBoundaryInventory,
    expected_application_id: str,
    account_id: str,
    supervisor_id: str,
    supervisor_endpoint: str,
    supervisor_endpoint_id: str,
    genie_space_id: str,
    preserved_supervisor_bindings: tuple[tuple[str, str, str], ...] = (),
    admin_workspace: Any | None = None,
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Prove only the exact reviewed Supervisor query paths under proxy OAuth."""
    me = workspace.current_user.me()
    authenticated = {
        value for value in (_text(me, "application_id"), _text(me, "user_name")) if value
    }
    if authenticated != {expected_application_id}:
        raise RuntimeError("authenticated agent-proxy identity does not match its application id")
    verify_managed_query_group_administration_denied(
        workspace,
        group_bindings=inventory.managed_query_group_bindings,
        admin_workspace=admin_workspace,
    )
    requested_bindings = (
        (supervisor_id, supervisor_endpoint, supervisor_endpoint_id),
        *preserved_supervisor_bindings,
    )
    if requested_bindings != inventory.reviewed_supervisor_bindings:
        raise RuntimeError("reviewed Supervisor bindings drifted from admin inventory")
    wait_for_reviewed_query_group_projections(
        workspace,
        expected_application_id=expected_application_id,
        reviewed_bindings=requested_bindings,
        reviewed_group_bindings=inventory.reviewed_query_group_bindings,
        sleep=sleep,
        clock=clock,
    )
    for _kind, _resource_id, group_name, group_id, _external_id in (
        inventory.reviewed_capability_group_bindings
    ):
        wait_for_managed_query_group_projection(
            workspace,
            expected_application_id=expected_application_id,
            expected_group_name=group_name,
            expected_group_id=group_id,
            sleep=sleep,
            clock=clock,
        )
    for candidate in inventory.supervisor_ids:
        _expect_denied(
            f"Supervisor definition metadata {candidate}",
            partial(
                workspace.api_client.do,
                "GET",
                f"/api/2.1/supervisor-agents/{quote(candidate, safe='')}",
            ),
        )
    reviewed_endpoint_ids = {
        endpoint: endpoint_id
        for _candidate_id, endpoint, endpoint_id in inventory.reviewed_supervisor_bindings
    }
    for endpoint in inventory.serving_endpoint_names:
        if endpoint in reviewed_endpoint_ids:
            details = workspace.serving_endpoints.get(endpoint)
            if (
                _text(details, "name") != endpoint
                or _text(details, "id") != reviewed_endpoint_ids[endpoint]
            ):
                raise RuntimeError("reviewed serving endpoint metadata drifted")
            _expect_denied(
                f"target serving endpoint permission administration {endpoint}",
                partial(
                    workspace.serving_endpoints.get_permissions,
                    reviewed_endpoint_ids[endpoint],
                ),
            )
        elif endpoint in inventory.foundation_endpoint_names:
            _verify_foundation_metadata_or_denied(workspace, endpoint)
        else:
            _expect_denied(
                f"non-target serving endpoint metadata {endpoint}",
                partial(workspace.serving_endpoints.get, endpoint),
            )
    for _candidate_id, endpoint, _endpoint_id in inventory.reviewed_supervisor_bindings:
        _verify_target_supervisor_query(
            workspace,
            supervisor_endpoint=endpoint,
            sleep=sleep,
            clock=clock,
        )
    _expect_denied(
        f"target Genie permission administration {genie_space_id}",
        lambda: workspace.api_client.do(
            "GET",
            f"/api/2.0/permissions/genie/{quote(genie_space_id, safe='')}",
        ),
    )
    target_genie = workspace.genie.get_space(genie_space_id)
    if _text(target_genie, "space_id") != genie_space_id:
        raise RuntimeError("agent-proxy target Genie identity drifted")
    for candidate in inventory.genie_space_ids:
        if candidate != genie_space_id:
            _expect_denied(
                f"non-target Genie space {candidate}", partial(workspace.genie.get_space, candidate)
            )


def verify_customer_resource_denial_boundary(
    *,
    workspace: Any,
    inventory: AgentProxyCustomerResourceDenialInventory,
    expected_application_id: str,
    account_id: str,
    admin_workspace: Any | None = None,
) -> None:
    """Prove no customer capability."""
    me = workspace.current_user.me()
    authenticated = {
        value for value in (_text(me, "application_id"), _text(me, "user_name")) if value
    }
    if authenticated != {expected_application_id}:
        raise RuntimeError("authenticated agent-proxy identity does not match its application id")
    managed_ids = {
        binding.id for binding in inventory.managed_query_group_bindings
    }
    managed_names = {
        binding.name for binding in inventory.managed_query_group_bindings
    }
    if any(
        group_id in managed_ids or group_name in managed_names
        for group_id, group_name in _projected_identity_groups(me)
    ):
        raise RuntimeError(
            "agent-proxy retains a managed customer-capability group"
        )
    verify_managed_query_group_administration_denied(
        workspace,
        group_bindings=inventory.managed_query_group_bindings,
        admin_workspace=admin_workspace,
    )
    for supervisor_id in inventory.supervisor_ids:
        _expect_denied(
            f"Supervisor definition metadata {supervisor_id}",
            partial(
                workspace.api_client.do,
                "GET",
                f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}",
            ),
        )
    for endpoint_name, endpoint_id, task, foundation in inventory.serving_endpoints:
        if foundation:
            _verify_foundation_metadata_or_denied(workspace, endpoint_name)
            continue
        _expect_denied(
            f"serving endpoint metadata {endpoint_name}",
            partial(workspace.serving_endpoints.get, endpoint_name),
        )
        _expect_denied(
            f"serving endpoint permission administration {endpoint_name}",
            partial(workspace.serving_endpoints.get_permissions, endpoint_id),
        )
        _expect_denied(
            f"serving endpoint query capability {endpoint_name}",
            partial(
                query_serving_endpoint_with_proof,
                workspace,
                endpoint_name,
                task=task or None,
                prompt=_TARGET_QUERY_PROMPT,
                client_request_id=f"mip-agent-proxy-denial-{uuid4().hex}",
                max_tokens=16,
            ),
        )
    for space_id in inventory.genie_space_ids:
        _expect_denied(
            f"Genie space metadata {space_id}",
            partial(workspace.genie.get_space, space_id),
        )
        _expect_denied(
            f"Genie permission administration {space_id}",
            partial(
                workspace.api_client.do,
                "GET",
                f"/api/2.0/permissions/genie/{quote(space_id, safe='')}",
            ),
        )


def verify_boundary(
    *,
    workspace: Any,
    account: Any,
    inventory: AgentProxyBoundaryInventory,
    expected_application_id: str,
    account_id: str,
    app_name: str,
    warehouse_id: str,
    supervisor_id: str,
    supervisor_endpoint: str,
    supervisor_endpoint_id: str,
    genie_space_id: str,
    preserved_supervisor_bindings: tuple[tuple[str, str, str], ...] = (),
    admin_workspace: Any | None = None,
    allow_attested_app_401: bool = False,
    http_get: Callable[..., Any] = requests.get,
) -> None:
    """Run positive target and exhaustive negative probes under proxy OAuth."""
    me = workspace.current_user.me()
    authenticated = {
        value for value in (_text(me, "application_id"), _text(me, "user_name")) if value
    }
    if authenticated != {expected_application_id}:
        raise RuntimeError("authenticated agent-proxy identity does not match its application id")
    if allow_attested_app_401 and admin_workspace is None:
        raise RuntimeError("admin App attestation authority is absent")
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
    for candidate_app, candidate_url in zip(
        inventory.app_names,
        inventory.app_urls,
        strict=True,
    ):
        is_target = candidate_app == app_name
        _verify_app_denial(
            workspace,
            expected_application_id=expected_application_id,
            app_url=candidate_url,
            http_get=http_get,
            admin_workspace=admin_workspace if is_target else None,
            app_name=candidate_app if is_target else None,
            allow_attested_app_401=allow_attested_app_401 and is_target,
        )
    _expect_denied(
        "metastore administrator GET probe",
        lambda: workspace.metastores.get(inventory.metastore_id),
    )
    for principal_id in inventory.service_principal_ids:
        _expect_denied(
            f"service-principal secret listing {principal_id}",
            partial(_list_service_principal_secrets, workspace, principal_id),
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
            partial(_list_scope_secrets, workspace, scope_name),
        )
    for candidate in inventory.warehouse_ids:
        _expect_denied(
            f"warehouse metadata {candidate}",
            partial(workspace.warehouses.get, candidate),
        )
    _verify_warehouse_denial(workspace, warehouse_id=warehouse_id)

    verify_target_query_boundary(
        workspace=workspace,
        inventory=inventory,
        expected_application_id=expected_application_id,
        account_id=account_id,
        supervisor_id=supervisor_id,
        supervisor_endpoint=supervisor_endpoint,
        supervisor_endpoint_id=supervisor_endpoint_id,
        genie_space_id=genie_space_id,
        preserved_supervisor_bindings=preserved_supervisor_bindings,
        admin_workspace=admin_workspace,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-application-id", required=True)
    parser.add_argument("--expected-inventory-principal")
    parser.add_argument("--account-host")
    parser.add_argument("--account-id")
    parser.add_argument("--app-name")
    parser.add_argument("--app-url")
    parser.add_argument("--lakebase-instance")
    parser.add_argument("--warehouse-id")
    parser.add_argument("--supervisor-id")
    parser.add_argument("--supervisor-endpoint")
    parser.add_argument("--supervisor-endpoint-id")
    parser.add_argument("--preserve-supervisor-id")
    parser.add_argument("--preserve-supervisor-endpoint")
    parser.add_argument("--preserve-supervisor-endpoint-id")
    parser.add_argument("--genie-space-id")
    parser.add_argument("--target-query-only", action="store_true")
    parser.add_argument("--customer-resource-denial", action="store_true")
    parser.add_argument(
        "--allow-attested-app-401",
        action="store_true",
        help="Accept target-App 401 only with a stable independent admin attestation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    denial_mode = args.customer_resource_denial
    if denial_mode and args.target_query_only:
        raise SystemExit("--customer-resource-denial conflicts with --target-query-only")
    if denial_mode and not args.expected_inventory_principal:
        raise SystemExit("--customer-resource-denial requires --expected-inventory-principal")
    if denial_mode and not args.account_id:
        raise SystemExit("--customer-resource-denial requires --account-id")
    required = (
        "account_host",
        "account_id",
        "app_name",
        "app_url",
        "lakebase_instance",
        "warehouse_id",
        "supervisor_id",
        "supervisor_endpoint",
        "supervisor_endpoint_id",
        "genie_space_id",
    )
    missing = [name for name in required if not getattr(args, name)]
    if missing and not denial_mode:
        raise SystemExit(
            "positive boundary mode requires: "
            + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        )
    preserve_values = (
        str(args.preserve_supervisor_id or "").strip(),
        str(args.preserve_supervisor_endpoint or "").strip(),
        str(args.preserve_supervisor_endpoint_id or "").strip(),
    )
    if len({bool(value) for value in preserve_values}) != 1:
        raise SystemExit(
            "--preserve-supervisor-id, --preserve-supervisor-endpoint, and "
            "--preserve-supervisor-endpoint-id are required together"
        )
    preserved_bindings = (preserve_values,) if preserve_values[0] else ()
    account_host = (
        reviewed_databricks_account_origin(
            args.account_host,
            label="agent-proxy account host",
        )
        if not denial_mode
        else ""
    )
    admin_workspace = WorkspaceClient()
    if denial_mode:
        assert_workspace_admin_inventory_identity(
            admin_workspace,
            expected_principal=args.expected_inventory_principal,
        )
    client_id, client_secret = bind_exact_workspace_m2m_auth(
        admin_workspace=admin_workspace,
        expected_application_id=args.expected_application_id,
        client_id_env="DATABRICKS_AGENT_PROXY_CLIENT_ID",
        client_secret_env="DATABRICKS_AGENT_PROXY_CLIENT_SECRET",
        label="agent-proxy",
    )
    proxy_workspace = WorkspaceClient()
    if denial_mode:
        verify_customer_resource_denial_boundary(
            workspace=proxy_workspace,
            inventory=collect_admin_customer_resource_denial_inventory(admin_workspace),
            expected_application_id=args.expected_application_id,
            account_id=args.account_id,
            admin_workspace=admin_workspace,
        )
    else:
        inventory = collect_admin_inventory(
            admin_workspace,
            app_name=args.app_name,
            app_url=args.app_url,
            lakebase_instance=args.lakebase_instance,
            warehouse_id=args.warehouse_id,
            supervisor_id=args.supervisor_id,
            supervisor_endpoint=args.supervisor_endpoint,
            supervisor_endpoint_id=args.supervisor_endpoint_id,
            genie_space_id=args.genie_space_id,
            expected_application_id=args.expected_application_id,
            preserved_supervisor_bindings=preserved_bindings,
        )
        if args.target_query_only:
            verify_target_query_boundary(
                workspace=proxy_workspace,
                inventory=inventory,
                expected_application_id=args.expected_application_id,
                account_id=args.account_id,
                supervisor_id=args.supervisor_id,
                supervisor_endpoint=args.supervisor_endpoint,
                supervisor_endpoint_id=args.supervisor_endpoint_id,
                genie_space_id=args.genie_space_id,
                preserved_supervisor_bindings=preserved_bindings,
                admin_workspace=admin_workspace,
            )
        else:
            proxy_account = AccountClient(
                host=account_host,
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
                account_id=args.account_id,
                app_name=args.app_name,
                warehouse_id=args.warehouse_id,
                supervisor_id=args.supervisor_id,
                supervisor_endpoint=args.supervisor_endpoint,
                supervisor_endpoint_id=args.supervisor_endpoint_id,
                genie_space_id=args.genie_space_id,
                preserved_supervisor_bindings=preserved_bindings,
                admin_workspace=admin_workspace,
                allow_attested_app_401=args.allow_attested_app_401,
            )
    if denial_mode:
        print("agent-proxy authorization boundary: PASS (customer-created serving/agent resources denied; foundation invocation not asserted)")
    else:
        print("agent-proxy effective authorization boundary: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
