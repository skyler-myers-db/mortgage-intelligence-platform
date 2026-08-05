"""Authoritative audit recovery for an ambiguous managed-Supervisor create."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from databricks.sdk.service.sql import (
    ExecuteStatementRequestOnWaitTimeout,
    StatementParameterListItem,
)

_MAX_MATCHES = 4
_QUERY = """
SELECT
  event_time,
  event_id,
  request_id,
  user_identity.email,
  to_json(request_params),
  response.result
FROM system.access.audit
WHERE service_name = 'supervisorAgent'
  AND action_name = 'create'
  AND workspace_id = :workspace_id
  AND event_date >= DATE(CAST(:authorized_from AS TIMESTAMP))
  AND event_time >= CAST(:authorized_from AS TIMESTAMP)
  AND event_time <= CAST(:authorized_until AS TIMESTAMP)
  AND user_identity.email = :actor
  AND response.status_code = 200
  AND to_json(request_params) LIKE :marker_pattern
ORDER BY event_time ASC
LIMIT 4
""".strip()


@dataclass(frozen=True)
class SupervisorCreateAuditProof:
    event_time: str
    event_id: str
    request_id: str
    supervisor_id: str


def _state(response: object) -> str:
    status = getattr(response, "status", None)
    state = getattr(status, "state", "")
    return str(getattr(state, "value", state) or "").split(".")[-1].upper()


def _json_object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise RuntimeError(f"Supervisor create audit {label} is not JSON")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Supervisor create audit {label} is not JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"Supervisor create audit {label} is not an object")
    return decoded


def _parameters(
    *,
    workspace_id: str,
    actor: str,
    authorized_from: datetime,
    authorized_until: datetime,
    marker: str,
) -> list[StatementParameterListItem]:
    values = {
        "workspace_id": workspace_id,
        "actor": actor,
        "authorized_from": authorized_from.isoformat(),
        "authorized_until": authorized_until.isoformat(),
        "marker_pattern": f"%{marker}%",
    }
    return [
        StatementParameterListItem(name=name, type="STRING", value=value)
        for name, value in values.items()
    ]


def find_supervisor_create_proof(
    workspace: Any,
    *,
    warehouse_id: str,
    workspace_id: str,
    actor: str,
    authorized_from: datetime,
    authorized_until: datetime,
    marker: str,
    expected_request: dict[str, str],
) -> SupervisorCreateAuditProof:
    """Return one exact successful create event or fail closed for later retry."""

    if authorized_from.tzinfo is None or authorized_until.tzinfo is None:
        raise ValueError("Supervisor create audit window must be timezone-aware")
    if authorized_until <= authorized_from:
        raise ValueError("Supervisor create audit window is invalid")
    response = workspace.statement_execution.execute_statement(
        statement=_QUERY,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
        parameters=_parameters(
            workspace_id=workspace_id,
            actor=actor,
            authorized_from=authorized_from,
            authorized_until=authorized_until,
            marker=marker,
        ),
        row_limit=_MAX_MATCHES,
    )
    state = _state(response)
    if state != "SUCCEEDED":
        raise RuntimeError(f"Supervisor create audit query did not succeed ({state})")
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if not isinstance(rows, list) or len(rows) >= _MAX_MATCHES:
        raise RuntimeError("Supervisor create audit proof is saturated or invalid")
    proofs: list[SupervisorCreateAuditProof] = []
    for row in rows:
        if not isinstance(row, list | tuple) or len(row) != 6:
            raise RuntimeError("Supervisor create audit row shape is invalid")
        event_time, event_id, request_id, event_actor, request_json, result_json = row
        if str(event_actor or "").strip() != actor:
            raise RuntimeError("Supervisor create audit actor changed")
        request = _json_object(request_json, label="request")
        supervisor_request = _json_object(
            request.get("supervisor_agent"),
            label="nested request",
        )
        observed = {"instructions": str(supervisor_request.get("instructions") or "").strip()}
        if observed != expected_request:
            raise RuntimeError("Supervisor create audit request differs from signed intent")
        result = _json_object(result_json, label="result")
        supervisor_id = str(result.get("supervisor_agent_id") or result.get("id") or "").strip()
        resource_name = str(result.get("name") or "").strip()
        result_creator = str(result.get("creator") or "").strip()
        result_endpoint = str(result.get("endpoint_name") or "").strip()
        result_instructions = str(result.get("instructions") or "").strip()
        normalized_time = str(event_time or "").strip()
        normalized_event = str(event_id or "").strip()
        normalized_request = str(request_id or "").strip()
        if (
            not all(
                (
                    supervisor_id,
                    normalized_time,
                    normalized_event,
                    normalized_request,
                    result_endpoint,
                )
            )
            or resource_name != f"supervisor-agents/{supervisor_id}"
            or result_creator.casefold() != actor.casefold()
            or result_instructions != expected_request["instructions"]
        ):
            raise RuntimeError("Supervisor create audit result is incomplete")
        try:
            observed_at = datetime.fromisoformat(normalized_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("Supervisor create audit timestamp is invalid") from exc
        if (
            observed_at.tzinfo is None
            or observed_at < authorized_from
            or observed_at > authorized_until
        ):
            raise RuntimeError("Supervisor create audit timestamp is unauthorized")
        proofs.append(
            SupervisorCreateAuditProof(
                event_time=normalized_time,
                event_id=normalized_event,
                request_id=normalized_request,
                supervisor_id=supervisor_id,
            )
        )
    if not proofs:
        raise RuntimeError("Supervisor create audit proof is not available yet")
    if len(proofs) != 1:
        raise RuntimeError("Supervisor create audit proof is ambiguous")
    return proofs[0]
