from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tools.databricks.supervisor_creation_audit import (
    find_supervisor_create_proof,
)

NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)
ACTOR = "d8c11669-c1f1-4be9-8ce7-be42c61ed626"
MARKER = "[mip-supervisor-create:01234567-89ab-cdef-8123-456789abcdef]"
INSTRUCTIONS = f"Governed Supervisor instructions. {MARKER}"
SUPERVISOR_ID = "397b1e34-a182-497a-bbf9-e512254a9c9b"


class _Statements:
    def __init__(self, rows: list[list[object]], *, state: str = "SUCCEEDED") -> None:
        self.rows = rows
        self.state = state
        self.kwargs: dict[str, object] = {}

    def execute_statement(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return SimpleNamespace(
            status=SimpleNamespace(state=self.state),
            result=SimpleNamespace(data_array=self.rows),
        )


def _workspace(rows: list[list[object]], *, state: str = "SUCCEEDED") -> object:
    return SimpleNamespace(statement_execution=_Statements(rows, state=state))


def _row(
    *,
    request: object | None = None,
    result: object | None = None,
    event_time: str = "2026-07-25T12:01:00+00:00",
    event_id: str = "audit-event-id",
) -> list[object]:
    return [
        event_time,
        event_id,
        "audit-request-id",
        ACTOR,
        json.dumps(
            request
            if request is not None
            else {"supervisor_agent": json.dumps({"instructions": INSTRUCTIONS})}
        ),
        json.dumps(
            result
            if result is not None
            else {
                "name": f"supervisor-agents/{SUPERVISOR_ID}",
                "supervisor_agent_id": SUPERVISOR_ID,
                "creator": ACTOR,
                "endpoint_name": "mas-397b1e34-endpoint",
                "instructions": INSTRUCTIONS,
            }
        ),
    ]


def _find(workspace: object):
    return find_supervisor_create_proof(
        workspace,
        warehouse_id="warehouse-id",
        workspace_id="123456789",
        actor=ACTOR,
        authorized_from=NOW,
        authorized_until=NOW + timedelta(minutes=15),
        marker=MARKER,
        expected_request={"instructions": INSTRUCTIONS},
    )


def test_exact_live_schema_returns_immutable_supervisor_id() -> None:
    workspace = _workspace([_row()])

    proof = _find(workspace)

    assert proof.supervisor_id == SUPERVISOR_ID
    assert proof.event_id == "audit-event-id"
    assert proof.request_id == "audit-request-id"
    parameters = {
        item.name: item.value for item in workspace.statement_execution.kwargs["parameters"]
    }
    assert parameters["workspace_id"] == "123456789"
    assert parameters["actor"] == ACTOR
    assert parameters["marker_pattern"] == f"%{MARKER}%"


def test_zero_and_multiple_create_events_never_claim() -> None:
    with pytest.raises(RuntimeError, match="not available yet"):
        _find(_workspace([]))
    with pytest.raises(RuntimeError, match="ambiguous"):
        _find(_workspace([_row(), _row(event_id="second-event-id")]))


def test_malformed_or_different_marked_request_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="nested request is not JSON"):
        _find(_workspace([_row(request={"supervisor_agent": {"instructions": INSTRUCTIONS}})]))
    with pytest.raises(RuntimeError, match="request differs from signed intent"):
        _find(
            _workspace(
                [
                    _row(
                        request={
                            "supervisor_agent": json.dumps({"instructions": f"different {MARKER}"})
                        }
                    )
                ]
            )
        )


def test_result_identity_and_window_are_revalidated_after_sql() -> None:
    with pytest.raises(RuntimeError, match="result is incomplete"):
        _find(
            _workspace(
                [
                    _row(
                        result={
                            "name": f"supervisor-agents/{SUPERVISOR_ID}",
                            "supervisor_agent_id": SUPERVISOR_ID,
                            "creator": "another-runtime",
                            "endpoint_name": "mas-397b1e34-endpoint",
                            "instructions": INSTRUCTIONS,
                        }
                    )
                ]
            )
        )
    with pytest.raises(RuntimeError, match="timestamp is unauthorized"):
        _find(_workspace([_row(event_time="2026-07-25T13:00:00+00:00")]))


def test_failed_and_saturated_queries_fail_closed() -> None:
    with pytest.raises(RuntimeError, match=r"did not succeed \(FAILED\)"):
        _find(_workspace([], state="FAILED"))
    with pytest.raises(RuntimeError, match="saturated"):
        _find(
            _workspace(
                [
                    _row(event_id="one"),
                    _row(event_id="two"),
                    _row(event_id="three"),
                    _row(event_id="four"),
                ]
            )
        )
