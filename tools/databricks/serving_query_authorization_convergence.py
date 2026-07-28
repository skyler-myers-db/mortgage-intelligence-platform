"""Bounded credential-side convergence for managed serving-query access."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, TypeVar
from uuid import uuid4

from backend.services.capability_serving_probes import (
    ServingEndpointExecution,
    query_serving_endpoint_with_proof,
)
from databricks.sdk.errors import PermissionDenied
from tools.databricks.ai_gateway_tool_trace import (
    warm_endpoint_with_cold_start_patience,
)

_T = TypeVar("_T")
AUTHORIZATION_CONVERGENCE_SECONDS = 180.0
AUTHORIZATION_POLL_SECONDS = 3.0
MAX_EFFECTIVE_GROUPS = 1_000
_BOUNDARY_REQUEST_ID = re.compile(r"mip-agent-proxy-boundary-[0-9a-f]{32}\Z")


def is_exact_target_supervisor_response(
    execution: ServingEndpointExecution,
    *,
    supervisor_endpoint: str,
) -> bool:
    """Prove the reviewed synchronous route, not independent model provenance."""

    canonical_task = str(execution.task or "").lower().replace("-", "_").replace("/", "_")
    if (
        execution.endpoint != supervisor_endpoint
        or execution.transport != "responses_api"
        or canonical_task != "agent_v1_responses"
        or _BOUNDARY_REQUEST_ID.fullmatch(str(execution.client_request_id or "")) is None
    ):
        return False
    response = execution.response
    if not isinstance(response, dict):
        converter = next(
            (
                candidate
                for name in ("as_dict", "to_dict")
                if callable(candidate := getattr(response, name, None))
            ),
            None,
        )
        try:
            response = converter() if converter else None
        except Exception:  # noqa: BLE001 - exact validation below fails closed
            return False
    if not isinstance(response, dict):
        return False
    required = {"id", "object", "model", "status", "error", "incomplete_details", "output"}
    if not required.issubset(response) or not str(response.get("id") or "").strip():
        return False
    # Managed Databricks Supervisor endpoints currently return an explicit
    # JSON null for the Responses `model` echo.  The synchronous request is
    # still bound to the exact reviewed endpoint by the immutable execution
    # envelope and the request body constructed by
    # `query_serving_endpoint_with_proof`.  Accept only that observed explicit
    # null or the exact endpoint echo; missing, blank, non-string, and
    # contradictory values remain fail-closed.
    model = response["model"]
    if (
        (model is not None and model != supervisor_endpoint)
        or str(response.get("status") or "").strip().casefold() != "completed"
        or str(response["object"] or "").strip() != "response"
        or response.get("error") is not None
        or response.get("incomplete_details") is not None
    ):
        return False
    output = response.get("output")
    if not isinstance(output, list) or not output:
        return False
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
            continue
        status = str(item.get("status") or "").strip().casefold()
        content = item.get("content")
        if status not in {"", "completed"} or not isinstance(content, list) or not content:
            continue
        if any(
            isinstance(part, dict)
            and part.get("type") == "output_text"
            and bool(str(part.get("text") or "").strip())
            for part in content
        ):
            return True
    return False


def _text(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _groups(me: object) -> tuple[tuple[str, str], ...]:
    raw = me.get("groups") if isinstance(me, dict) else getattr(me, "groups", None)
    groups = tuple(raw or ())
    if len(groups) > MAX_EFFECTIVE_GROUPS:
        raise RuntimeError("agent-proxy effective group projection is unbounded")
    projected = tuple(
        (_text(group, "value") or _text(group, "id"), _text(group, "display"))
        for group in groups
    )
    if any(not group_id or not name for group_id, name in projected):
        raise RuntimeError("agent-proxy effective group projection is incomplete")
    group_ids = tuple(group_id.casefold() for group_id, _name in projected)
    group_names = tuple(name.casefold() for _group_id, name in projected)
    if (
        len(group_ids) != len(set(group_ids))
        or len(group_names) != len(set(group_names))
    ):
        raise RuntimeError("agent-proxy effective group projection is duplicated")
    return projected


def _assert_proxy_identity(me: object, expected_application_id: str) -> None:
    authenticated = {
        value
        for value in (_text(me, "application_id"), _text(me, "user_name"))
        if value
    }
    if authenticated != {expected_application_id}:
        raise RuntimeError(
            "authenticated agent-proxy identity does not match its application id"
        )


def wait_for_managed_query_group_projection(
    workspace: Any,
    *,
    expected_application_id: str,
    expected_group_name: str,
    expected_group_id: str,
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    deadline_seconds: float = AUTHORIZATION_CONVERGENCE_SECONDS,
) -> None:
    """Require the proxy's own SCIM projection to expose the exact managed group."""

    if not expected_group_name or not expected_group_id or deadline_seconds <= 0:
        raise ValueError("managed serving-query convergence contract is incomplete")
    deadline = clock() + deadline_seconds
    while True:
        me = workspace.current_user.me()
        _assert_proxy_identity(me, expected_application_id)
        projected = _groups(me)
        named = tuple(item for item in projected if item[1] == expected_group_name)
        if named:
            if len(named) != 1 or named[0][0] != expected_group_id:
                raise RuntimeError(
                    "agent-proxy managed serving-query group projection drifted"
                )
            return
        if any(group_id == expected_group_id for group_id, _name in projected):
            raise RuntimeError(
                "agent-proxy managed serving-query group projection name drifted"
            )
        if clock() >= deadline:
            raise RuntimeError(
                "agent-proxy managed serving-query group projection did not converge"
            )
        sleep(AUTHORIZATION_POLL_SECONDS)


def wait_for_reviewed_query_group_projections(
    workspace: Any,
    *,
    expected_application_id: str,
    reviewed_bindings: tuple[tuple[str, str, str], ...],
    reviewed_group_bindings: tuple[tuple[str, str, str, str], ...],
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    groups_by_endpoint = {
        endpoint_id: (group_name, group_id)
        for endpoint_id, group_name, group_id, _external_id in reviewed_group_bindings
    }
    if (
        len(groups_by_endpoint) != len(reviewed_group_bindings)
        or set(groups_by_endpoint)
        != {endpoint_id for _supervisor_id, _endpoint, endpoint_id in reviewed_bindings}
    ):
        raise RuntimeError("reviewed serving-query group bindings are incomplete")
    for _supervisor_id, _endpoint, endpoint_id in reviewed_bindings:
        expected_name, expected_id = groups_by_endpoint[endpoint_id]
        wait_for_managed_query_group_projection(
            workspace,
            expected_application_id=expected_application_id,
            expected_group_name=expected_name,
            expected_group_id=expected_id,
            sleep=sleep,
            clock=clock,
        )


def _permission_denied(error: BaseException) -> bool:
    return isinstance(error, PermissionDenied)


def retry_authorization_propagation(
    operation: Callable[[], _T],
    *,
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    deadline_seconds: float = AUTHORIZATION_CONVERGENCE_SECONDS,
) -> _T:
    """Retry only provider authorization propagation; surface every other error."""

    if deadline_seconds <= 0:
        raise ValueError("authorization convergence deadline must be positive")
    deadline = clock() + deadline_seconds
    while True:
        try:
            return operation()
        except BaseException as error:
            if not _permission_denied(error) or clock() >= deadline:
                raise
            sleep(AUTHORIZATION_POLL_SECONDS)


def query_serving_endpoint_after_authorization(
    workspace: Any,
    *,
    supervisor_endpoint: str,
    prompt: str,
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ServingEndpointExecution:
    """Run the exact positive query after cold-start and authorization convergence."""

    def _query() -> ServingEndpointExecution:
        warm_endpoint_with_cold_start_patience(
            workspace,
            supervisor_endpoint,
            task="agent_v1_responses",
            prompt=prompt,
            sleep=sleep,
        )
        return query_serving_endpoint_with_proof(
            workspace,
            supervisor_endpoint,
            task="agent_v1_responses",
            prompt=prompt,
            client_request_id=f"mip-agent-proxy-boundary-{uuid4().hex}",
            max_tokens=64,
        )

    return retry_authorization_propagation(
        _query,
        sleep=sleep,
        clock=clock,
    )
