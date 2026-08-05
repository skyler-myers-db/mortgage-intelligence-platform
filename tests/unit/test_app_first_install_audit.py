from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tools.databricks.app_first_install_audit import find_app_create_proof

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)
EXPECTED_REQUEST = {
    "name": "mip-app",
    "description": "Mortgage Intelligence [mip-first-install:bootstrap-id]",
    "resources": [
        {
            "name": "sql_warehouse",
            "sql_warehouse": {"id": "warehouse-id", "permission": "CAN_USE"},
        }
    ],
}


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
    request: str | None = None,
    result: str = '{"name":"mip-app","id":"app-object-id"}',
    event_time: str = "2026-07-18T12:01:00+00:00",
    event_id: str = "audit-event-id",
) -> list[object]:
    import json

    return [
        event_time,
        event_id,
        "audit-request-id",
        "deployer@example.com",
        request or json.dumps(EXPECTED_REQUEST),
        result,
    ]


def _find(workspace: object):
    return find_app_create_proof(
        workspace,
        warehouse_id="warehouse-id",
        workspace_id="123456789",
        actor="deployer@example.com",
        authorized_from=NOW,
        authorized_until=NOW + timedelta(hours=4),
        marker="[mip-first-install:bootstrap-id]",
        expected_request=EXPECTED_REQUEST,
    )


def test_exact_create_event_returns_immutable_audit_proof() -> None:
    workspace = _workspace([_row()])

    proof = _find(workspace)

    assert proof.app_id == "app-object-id"
    assert proof.event_id == "audit-event-id"
    assert proof.request_id == "audit-request-id"
    parameters = {
        item.name: item.value
        for item in workspace.statement_execution.kwargs["parameters"]
    }
    assert parameters["workspace_id"] == "123456789"
    assert parameters["marker_pattern"] == "%[mip-first-install:bootstrap-id]%"
    assert workspace.statement_execution.kwargs["warehouse_id"] == "warehouse-id"


def test_missing_create_event_is_retriable_without_proof() -> None:
    with pytest.raises(RuntimeError, match="not available yet"):
        _find(_workspace([]))


def test_multiple_successful_create_events_are_ambiguous() -> None:
    with pytest.raises(RuntimeError, match="ambiguous"):
        _find(_workspace([_row(), _row(event_id="second-event-id")]))


def test_marker_match_with_different_request_is_rejected() -> None:
    request = (
        '{"name":"mip-app","description":'
        '"replacement [mip-first-install:bootstrap-id]","resources":[]}'
    )
    with pytest.raises(RuntimeError, match="request differs from signed intent"):
        _find(_workspace([_row(request=request)]))


def test_audit_result_and_window_are_revalidated_after_sql() -> None:
    with pytest.raises(RuntimeError, match="names another App"):
        _find(
            _workspace(
                [_row(result='{"name":"other-app","id":"app-object-id"}')]
            )
        )
    with pytest.raises(RuntimeError, match="timestamp is unauthorized"):
        _find(_workspace([_row(event_time="2026-07-18T17:00:00+00:00")]))


def test_failed_or_saturated_audit_query_never_returns_proof() -> None:
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
