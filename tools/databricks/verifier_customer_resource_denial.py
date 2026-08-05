"""Customer-resource denial proof for the AI Gateway verifier credential."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from backend.services.capability_serving_probes import (
    query_serving_endpoint_with_proof,
)
from tools.databricks.agent_proxy_identity_inventory_groups import (
    collect_managed_proxy_workspace_groups,
)
from tools.databricks.agent_runtime_access import _genie_spaces
from tools.databricks.authorization_denial import is_authorization_denied
from tools.databricks.identity_boundary_probes import (
    ManagedWorkspaceGroupBinding,
    verify_managed_query_group_administration_denied,
)
from tools.databricks.serving_endpoint_acl import is_platform_foundation_endpoint
from tools.databricks.serving_query_authorization_convergence import (
    _groups as _projected_identity_groups,
)

_MAX_GLOBAL_INVENTORY = 1000
_DENIAL_PROMPT = (
    "Return a short readiness acknowledgement without borrower data or tool calls."
)


def _text(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _expect_denied(label: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except Exception as exc:  # noqa: BLE001 - exact provider classification
        if is_authorization_denied(exc, allow_hidden_resource=True):
            return
        raise RuntimeError(
            f"{label} was inconclusive: {type(exc).__name__}: {exc}"
        ) from exc
    raise RuntimeError(f"{label} unexpectedly succeeded")


@dataclass(frozen=True)
class VerifierCustomerResourceDenialInventory:
    serving_endpoints: tuple[tuple[str, str, str, bool], ...]
    genie_space_ids: tuple[str, ...]
    managed_group_bindings: tuple[ManagedWorkspaceGroupBinding, ...] = ()


def _bounded_unique(values: list[str], *, label: str) -> tuple[str, ...]:
    if (
        len(values) > _MAX_GLOBAL_INVENTORY
        or any(not value for value in values)
        or len(values) != len(set(values))
    ):
        raise RuntimeError(f"{label} inventory is empty, duplicated, or unbounded")
    return tuple(sorted(values))


def collect_admin_customer_resource_denial_inventory(
    workspace: Any,
) -> VerifierCustomerResourceDenialInventory:
    """Capture customer serving, Genie, and managed-group resources."""

    names = _bounded_unique(
        [_text(item, "name") for item in workspace.serving_endpoints.list()],
        label="serving endpoint",
    )
    endpoints: list[tuple[str, str, str, bool]] = []
    for name in names:
        details = workspace.serving_endpoints.get(name)
        foundation = is_platform_foundation_endpoint(details)
        endpoint_id = _text(details, "id")
        task = _text(details, "task")
        if not foundation and (
            _text(details, "name") != name or not endpoint_id or not task
        ):
            raise RuntimeError(
                f"non-foundation serving endpoint {name!r} lacks identity or query protocol"
            )
        endpoints.append((name, endpoint_id, task, foundation))
    return VerifierCustomerResourceDenialInventory(
        serving_endpoints=tuple(endpoints),
        genie_space_ids=_bounded_unique(
            list(_genie_spaces(workspace)),
            label="Genie",
        ),
        managed_group_bindings=collect_managed_proxy_workspace_groups(workspace),
    )


def verify_customer_resource_denial_boundary(
    *,
    workspace: Any,
    inventory: VerifierCustomerResourceDenialInventory,
    expected_application_id: str,
    admin_workspace: Any | None = None,
) -> None:
    """Prove no customer serving, Genie, or group-administration capability."""

    me = workspace.current_user.me()
    authenticated = {
        value
        for value in (_text(me, "application_id"), _text(me, "user_name"))
        if value
    }
    if authenticated != {expected_application_id}:
        raise RuntimeError(
            "authenticated verifier identity does not match the configured application id"
        )
    managed_ids = {binding.id for binding in inventory.managed_group_bindings}
    managed_names = {binding.name for binding in inventory.managed_group_bindings}
    if any(
        group_id in managed_ids or group_name in managed_names
        for group_id, group_name in _projected_identity_groups(me)
    ):
        raise RuntimeError("verifier retains a managed customer-capability group")
    verify_managed_query_group_administration_denied(
        workspace,
        group_bindings=inventory.managed_group_bindings,
        admin_workspace=admin_workspace,
    )
    for name, endpoint_id, task, foundation in inventory.serving_endpoints:
        if foundation:
            try:
                details = workspace.serving_endpoints.get(name)
            except Exception as exc:  # noqa: BLE001 - classify provider denial
                if is_authorization_denied(exc, allow_hidden_resource=True):
                    continue
                raise RuntimeError(
                    f"foundation endpoint metadata {name} was inconclusive: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if not is_platform_foundation_endpoint(details):
                raise RuntimeError(
                    f"visible endpoint {name!r} is not a system.ai foundation endpoint"
                )
            continue
        _expect_denied(
            f"serving endpoint metadata {name}",
            partial(workspace.serving_endpoints.get, name),
        )
        _expect_denied(
            f"serving endpoint permission administration {name}",
            partial(workspace.serving_endpoints.get_permissions, endpoint_id),
        )
        _expect_denied(
            f"serving endpoint query capability {name}",
            partial(
                query_serving_endpoint_with_proof,
                workspace,
                name,
                task=task,
                prompt=_DENIAL_PROMPT,
                client_request_id=f"mip-verifier-denial-{uuid4().hex}",
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
