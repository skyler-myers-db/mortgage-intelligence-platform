from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.errors import PermissionDenied

from backend.services.capability_serving_probes import ServingEndpointExecution
from tools.databricks.serving_query_authorization_convergence import (
    is_exact_target_supervisor_response,
    retry_authorization_propagation,
    wait_for_managed_query_group_projection,
)


def _clock(*values: float):
    ticks = iter(values)
    return lambda: next(ticks)


def test_managed_group_projection_converges_after_delay() -> None:
    responses = iter(
        (
            SimpleNamespace(
                application_id="proxy-app",
                user_name="proxy-app",
                groups=[],
            ),
            SimpleNamespace(
                application_id="proxy-app",
                user_name="proxy-app",
                groups=[SimpleNamespace(value="group-id", display="managed-group")],
            ),
        )
    )
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: next(responses))
    )

    wait_for_managed_query_group_projection(
        workspace,
        expected_application_id="proxy-app",
        expected_group_name="managed-group",
        expected_group_id="group-id",
        sleep=lambda _seconds: None,
        clock=_clock(0.0, 0.0),
        deadline_seconds=1.0,
    )


def test_managed_group_projection_deadline_fails_closed() -> None:
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                application_id="proxy-app",
                user_name="proxy-app",
                groups=[],
            )
        )
    )

    with pytest.raises(RuntimeError, match="did not converge"):
        wait_for_managed_query_group_projection(
            workspace,
            expected_application_id="proxy-app",
            expected_group_name="managed-group",
            expected_group_id="group-id",
            sleep=lambda _seconds: None,
            clock=_clock(0.0, 2.0),
            deadline_seconds=1.0,
        )


def test_authorization_retry_converges_only_for_permission_denied() -> None:
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionDenied("permission denied while grant propagates")
        return "ready"

    assert retry_authorization_propagation(
        operation,
        sleep=lambda _seconds: None,
        clock=_clock(0.0, 0.0),
        deadline_seconds=1.0,
    ) == "ready"
    assert calls == 2


def test_authorization_retry_surfaces_unrelated_error_immediately() -> None:
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("provider transport failed")

    with pytest.raises(TimeoutError, match="transport failed"):
        retry_authorization_propagation(
            operation,
            sleep=lambda _seconds: None,
            clock=_clock(0.0),
            deadline_seconds=1.0,
        )
    assert calls == 1


def test_managed_group_projection_rejects_expected_name_with_swapped_id() -> None:
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                application_id="proxy-app",
                user_name="proxy-app",
                groups=[
                    SimpleNamespace(
                        value="another-managed-group-id",
                        display="managed-group",
                    )
                ],
            )
        )
    )

    with pytest.raises(RuntimeError, match="projection drifted"):
        wait_for_managed_query_group_projection(
            workspace,
            expected_application_id="proxy-app",
            expected_group_name="managed-group",
            expected_group_id="reviewed-group-id",
            sleep=lambda _seconds: None,
            clock=_clock(0.0),
            deadline_seconds=1.0,
        )


@pytest.mark.parametrize(
    "groups",
    (
        [
            SimpleNamespace(value="same-id", display="managed-group"),
            SimpleNamespace(value="same-id", display="other-group"),
        ],
        [
            SimpleNamespace(value="reviewed-id", display="managed-group"),
            SimpleNamespace(value="other-id", display="managed-group"),
        ],
    ),
)
def test_managed_group_projection_rejects_duplicate_ids_or_names(
    groups: list[SimpleNamespace],
) -> None:
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                application_id="proxy-app",
                user_name="proxy-app",
                groups=groups,
            )
        )
    )

    with pytest.raises(RuntimeError, match="projection is duplicated"):
        wait_for_managed_query_group_projection(
            workspace,
            expected_application_id="proxy-app",
            expected_group_name="managed-group",
            expected_group_id="reviewed-id",
            sleep=lambda _seconds: None,
            clock=_clock(0.0),
            deadline_seconds=1.0,
        )


def test_authorization_retry_rejects_foreign_permission_denied_class() -> None:
    class PermissionDenied(Exception):
        pass

    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise PermissionDenied("permission denied outside Databricks")

    with pytest.raises(PermissionDenied, match="outside Databricks"):
        retry_authorization_propagation(
            operation,
            sleep=lambda _seconds: None,
            clock=_clock(0.0),
            deadline_seconds=1.0,
        )
    assert calls == 1


@pytest.mark.parametrize("model", ("", "different-supervisor"))
def test_exact_target_response_rejects_missing_or_wrong_model(model: str) -> None:
    execution = ServingEndpointExecution(
        endpoint="reviewed-supervisor",
        task="agent_v1_responses",
        transport="responses_api",
        client_request_id="request-id",
        response={
            "id": "response-id",
            "object": "response",
            "model": model,
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
        },
    )

    assert not is_exact_target_supervisor_response(
        execution,
        supervisor_endpoint="reviewed-supervisor",
    )
