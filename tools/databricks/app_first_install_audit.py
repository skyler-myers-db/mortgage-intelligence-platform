#!/usr/bin/env python3
"""Read authoritative Databricks Apps creation proof from system audit logs."""

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
  request_params['app'],
  response.result
FROM system.access.audit
WHERE service_name = 'apps'
  AND action_name = 'createApp'
  AND workspace_id = :workspace_id
  AND event_date >= DATE(CAST(:authorized_from AS TIMESTAMP))
  AND event_time >= CAST(:authorized_from AS TIMESTAMP)
  AND event_time <= CAST(:authorized_until AS TIMESTAMP)
  AND user_identity.email = :actor
  AND response.status_code = 200
  AND request_params['app'] LIKE :marker_pattern
ORDER BY event_time ASC
LIMIT 4
""".strip()


@dataclass(frozen=True)
class AppCreateAuditProof:
    event_time: str
    event_id: str
    request_id: str
    app_id: str


def _state(response: object) -> str:
    status = getattr(response, "status", None)
    state = getattr(status, "state", "")
    return str(getattr(state, "value", state) or "").split(".")[-1].upper()


def _json_object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise RuntimeError(f"first-install create audit {label} is not JSON")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"first-install create audit {label} is not JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"first-install create audit {label} is not an object")
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


def find_app_create_proof(
    workspace: Any,
    *,
    warehouse_id: str,
    workspace_id: str,
    actor: str,
    authorized_from: datetime,
    authorized_until: datetime,
    marker: str,
    expected_request: dict[str, Any],
) -> AppCreateAuditProof:
    """Return one exact successful create event or fail closed for later retry."""

    if authorized_from.tzinfo is None or authorized_until.tzinfo is None:
        raise ValueError("first-install create audit window must be timezone-aware")
    if authorized_until <= authorized_from:
        raise ValueError("first-install create audit window is invalid")
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
        raise RuntimeError(f"first-install create audit query did not succeed ({state})")
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if not isinstance(rows, list) or len(rows) >= _MAX_MATCHES:
        raise RuntimeError("first-install create audit proof is saturated or invalid")

    proofs: list[AppCreateAuditProof] = []
    for row in rows:
        if not isinstance(row, list | tuple) or len(row) != 6:
            raise RuntimeError("first-install create audit row shape is invalid")
        event_time, event_id, request_id, event_actor, request_app, result = row
        if str(event_actor or "").strip() != actor:
            raise RuntimeError("first-install create audit actor changed")
        if _json_object(request_app, label="request") != expected_request:
            raise RuntimeError("first-install create audit request differs from signed intent")
        result_app = _json_object(result, label="result")
        if str(result_app.get("name") or "").strip() != str(
            expected_request.get("name") or ""
        ).strip():
            raise RuntimeError("first-install create audit result names another App")
        app_id = str(result_app.get("id") or "").strip()
        normalized_event_id = str(event_id or "").strip()
        normalized_request_id = str(request_id or "").strip()
        normalized_event_time = str(event_time or "").strip()
        if not all((app_id, normalized_event_id, normalized_request_id, normalized_event_time)):
            raise RuntimeError("first-install create audit result is incomplete")
        try:
            observed_at = datetime.fromisoformat(normalized_event_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("first-install create audit timestamp is invalid") from exc
        if (
            observed_at.tzinfo is None
            or observed_at < authorized_from
            or observed_at > authorized_until
        ):
            raise RuntimeError("first-install create audit timestamp is unauthorized")
        proofs.append(
            AppCreateAuditProof(
                event_time=normalized_event_time,
                event_id=normalized_event_id,
                request_id=normalized_request_id,
                app_id=app_id,
            )
        )
    if not proofs:
        raise RuntimeError("first-install create audit proof is not available yet")
    if len(proofs) != 1:
        raise RuntimeError("first-install create audit proof is ambiguous")
    return proofs[0]
