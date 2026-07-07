from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from tools.databricks import grant_ai_gateway_inference_table as grant_module


@dataclass
class _Status:
    state: str = "SUCCEEDED"
    error: str | None = None


@dataclass
class _Result:
    data_array: list[list[str]]


@dataclass
class _StatementResponse:
    result: _Result
    status: _Status = field(default_factory=_Status)


class _FakeStatementExecution:
    def __init__(self, table_rows: list[list[str]]) -> None:
        self.table_rows = table_rows
        self.statements: list[str] = []

    def execute_statement(self, *, statement: str, **kwargs: Any) -> _StatementResponse:
        _ = kwargs
        self.statements.append(statement)
        if "system.information_schema.tables" in statement:
            return _StatementResponse(result=_Result(self.table_rows))
        return _StatementResponse(result=_Result([]))


class _FakeServingEndpoints:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def query(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.queries.append((endpoint, kwargs))
        return {"choices": [{"message": {"content": "ok"}}]}


class _FakeWorkspace:
    def __init__(self, table_rows: list[list[str]]) -> None:
        self.statement_execution = _FakeStatementExecution(table_rows)
        self.serving_endpoints = _FakeServingEndpoints()


def test_grant_gateway_table_access_grants_only_concrete_app_owned_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _FakeWorkspace(
        [
            ["mip_agent_gateway_llama_payload"],
            ["mip_agent_gateway_llama_assessment"],
        ]
    )
    monkeypatch.setattr(grant_module, "WorkspaceClient", lambda: workspace)

    granted = grant_module.grant_gateway_table_access(
        warehouse_id="wh-1",
        relation_prefix="mip.audit.mip_agent_gateway_llama",
        principal="app-sp-1",
    )

    assert granted == [
        "mip.audit.mip_agent_gateway_llama_payload",
        "mip.audit.mip_agent_gateway_llama_assessment",
    ]
    statements = "\n".join(workspace.statement_execution.statements)
    assert "GRANT USE SCHEMA ON SCHEMA `mip`.`audit` TO `app-sp-1`" in statements
    assert "GRANT SELECT ON TABLE `mip`.`audit`.`mip_agent_gateway_llama_payload`" in statements
    assert "GRANT SELECT ON SCHEMA" not in statements
    assert "mip_other_table" not in statements


@pytest.mark.parametrize(
    "relation_prefix",
    [
        "mip.audit.mip",
        "mip.audit.gateway",
        "mip.audit.mip_agent",
        "mip.audit.mip_agent_gateway",
        "mip.audit.mip_agent_gateway_x;DROP",
    ],
)
def test_grant_gateway_table_access_rejects_broad_or_invalid_prefixes(
    relation_prefix: str,
) -> None:
    with pytest.raises(ValueError):
        grant_module.grant_gateway_table_access(
            warehouse_id="wh-1",
            relation_prefix=relation_prefix,
            principal="app-sp-1",
        )


def test_grant_gateway_table_access_fails_when_no_prefixed_tables_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _FakeWorkspace([])
    monkeypatch.setattr(grant_module, "WorkspaceClient", lambda: workspace)

    with pytest.raises(RuntimeError, match="No AI Gateway inference tables"):
        grant_module.grant_gateway_table_access(
            warehouse_id="wh-1",
            relation_prefix="mip.audit.mip_agent_gateway_llama",
            principal="app-sp-1",
            timeout_s=0,
        )

    assert workspace.serving_endpoints.queries == []
