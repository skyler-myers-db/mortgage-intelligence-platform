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
    def __init__(
        self,
        table_rows: list[list[str]],
        *,
        target_select_count: int = 2,
        catalog_forbidden_count: int = 0,
        schema_forbidden_count: int = 0,
        obsolete_privilege_count: int = 0,
        owner_forbidden_count: int = 0,
    ) -> None:
        self.table_rows = table_rows
        self.target_select_count = target_select_count
        self.catalog_forbidden_count = catalog_forbidden_count
        self.schema_forbidden_count = schema_forbidden_count
        self.obsolete_privilege_count = obsolete_privilege_count
        self.owner_forbidden_count = owner_forbidden_count
        self.statements: list[str] = []

    def execute_statement(self, *, statement: str, **kwargs: Any) -> _StatementResponse:
        _ = kwargs
        self.statements.append(statement)
        if "mip_gateway_postflight_owner_forbidden" in statement:
            return _StatementResponse(result=_Result([[str(self.owner_forbidden_count)]]))
        if "system.information_schema.tables" in statement:
            return _StatementResponse(result=_Result(self.table_rows))
        if "mip_gateway_postflight_target_select" in statement:
            return _StatementResponse(result=_Result([[str(self.target_select_count)]]))
        if "mip_gateway_postflight_schema_forbidden" in statement:
            return _StatementResponse(result=_Result([[str(self.schema_forbidden_count)]]))
        if "mip_gateway_postflight_obsolete" in statement:
            return _StatementResponse(result=_Result([[str(self.obsolete_privilege_count)]]))
        if "mip_gateway_postflight_target_forbidden" in statement:
            return _StatementResponse(result=_Result([["0"]]))
        if "mip_gateway_postflight_catalog_forbidden" in statement:
            return _StatementResponse(result=_Result([[str(self.catalog_forbidden_count)]]))
        return _StatementResponse(result=_Result([]))


class _FakeServingEndpoints:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def get(self, endpoint: str) -> object:
        return type("Endpoint", (), {"task": "agent/v1/responses", "name": endpoint})()


class _FakeWorkspace:
    def __init__(self, table_rows: list[list[str]], **counts: int) -> None:
        self.statement_execution = _FakeStatementExecution(table_rows, **counts)
        self.serving_endpoints = _FakeServingEndpoints()
        self.service_principals = type(
            "ServicePrincipals",
            (),
            {"list": lambda _self, **_kwargs: iter([type("SP", (), {"id": "sp-scim-id"})()])},
        )()
        self.groups = type(
            "Groups",
            (),
            {"list": lambda _self, **_kwargs: iter([])},
        )()


def test_grant_gateway_table_access_grants_only_concrete_app_owned_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _FakeWorkspace(
        [
            ["mipXagentXgatewayXllama_payload"],
            ["mip_agent_gateway_llama_payload"],
            ["mip_agent_gateway_growth_agent_payload"],
            ["mip_agent_gateway_growth_agent_assessment"],
        ]
    )
    monkeypatch.setattr(grant_module, "WorkspaceClient", lambda: workspace)

    granted = grant_module.grant_gateway_table_access(
        warehouse_id="wh-1",
        relation_prefix="mip.audit.mip_agent_gateway_growth_agent",
        principal="app-sp-1",
    )

    assert granted == [
        "mip.audit.mip_agent_gateway_growth_agent_payload",
        "mip.audit.mip_agent_gateway_growth_agent_assessment",
    ]
    statements = "\n".join(workspace.statement_execution.statements)
    assert "GRANT USE CATALOG ON CATALOG `mip` TO `app-sp-1`" in statements
    assert "GRANT USE SCHEMA ON SCHEMA `mip`.`audit` TO `app-sp-1`" in statements
    assert "REVOKE ALL PRIVILEGES ON SCHEMA `mip`.`audit` FROM `app-sp-1`" in statements
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE `mip`.`audit`.`mip_agent_gateway_llama_payload`"
        in statements
    )
    assert (
        "GRANT SELECT ON TABLE `mip`.`audit`.`mip_agent_gateway_growth_agent_payload`" in statements
    )
    assert "GRANT SELECT ON SCHEMA" not in statements
    assert "mipXagentXgatewayXllama_payload" not in statements
    discovery = next(
        statement
        for statement in workspace.statement_execution.statements
        if "system.information_schema.tables" in statement
    )
    assert "mip\\_agent\\_gateway\\_growth\\_agent%" in discovery
    assert "ESCAPE '\\\\'" in discovery


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


def test_grant_bootstrap_uses_endpoint_task_aware_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _FakeWorkspace([])
    captured: dict[str, Any] = {}
    monkeypatch.setattr(grant_module, "WorkspaceClient", lambda: workspace)

    def _query(_workspace: object, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(endpoint=endpoint, **kwargs)
        return {"status": "completed", "output": [{"content": "ok"}]}

    monkeypatch.setattr(grant_module, "query_serving_endpoint", _query)

    with pytest.raises(RuntimeError, match="No AI Gateway inference tables"):
        grant_module.grant_gateway_table_access(
            warehouse_id="wh-1",
            relation_prefix="mip.audit.mip_agent_gateway_llama",
            principal="app-sp-1",
            endpoint="responses-endpoint",
            timeout_s=0,
        )

    assert captured["endpoint"] == "responses-endpoint"
    assert captured["task"] == "agent/v1/responses"
    assert str(captured["client_request_id"]).startswith("mip-grant-bootstrap-")


def test_grant_gateway_table_access_fails_when_obsolete_privilege_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _FakeWorkspace(
        [
            ["mip_agent_gateway_llama_payload"],
            ["mip_agent_gateway_growth_agent_payload"],
            ["mip_agent_gateway_growth_agent_assessment"],
        ],
        obsolete_privilege_count=1,
    )
    monkeypatch.setattr(grant_module, "WorkspaceClient", lambda: workspace)

    with pytest.raises(RuntimeError, match="grant postflight failed"):
        grant_module.grant_gateway_table_access(
            warehouse_id="wh-1",
            relation_prefix="mip.audit.mip_agent_gateway_growth_agent",
            principal="app-sp-1",
        )


def test_grant_gateway_table_access_rejects_effective_group_schema_privilege(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _FakeWorkspace(
        [
            ["mip_agent_gateway_growth_agent_payload"],
            ["mip_agent_gateway_growth_agent_assessment"],
        ],
        schema_forbidden_count=1,
    )
    monkeypatch.setattr(grant_module, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        grant_module,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {"group-1": "broad-readers"},
    )

    with pytest.raises(RuntimeError, match="grant postflight failed"):
        grant_module.grant_gateway_table_access(
            warehouse_id="wh-1",
            relation_prefix="mip.audit.mip_agent_gateway_growth_agent",
            principal="app-sp-1",
        )

    schema_check = next(
        statement
        for statement in workspace.statement_execution.statements
        if "mip_gateway_postflight_schema_forbidden" in statement
    )
    assert "'broad-readers'" in schema_check
    assert "UPPER(privilege_type) <> 'USE SCHEMA'" in schema_check


def test_grant_gateway_table_access_rejects_effective_group_catalog_create_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _FakeWorkspace(
        [
            ["mip_agent_gateway_growth_agent_payload"],
            ["mip_agent_gateway_growth_agent_assessment"],
        ],
        catalog_forbidden_count=1,
    )
    monkeypatch.setattr(grant_module, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        grant_module,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {"group-1": "catalog-creators"},
    )

    with pytest.raises(RuntimeError, match="grant postflight failed"):
        grant_module.grant_gateway_table_access(
            warehouse_id="wh-1",
            relation_prefix="mip.audit.mip_agent_gateway_growth_agent",
            principal="app-sp-1",
        )

    catalog_check = next(
        statement
        for statement in workspace.statement_execution.statements
        if "mip_gateway_postflight_catalog_forbidden" in statement
    )
    assert "NOT IN ('USE CATALOG', 'BROWSE')" in catalog_check
    assert "'catalog-creators'" in catalog_check


def test_grant_gateway_table_access_rejects_effective_group_object_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _FakeWorkspace(
        [
            ["mip_agent_gateway_legacy_payload"],
            ["mip_agent_gateway_growth_agent_payload"],
            ["mip_agent_gateway_growth_agent_assessment"],
        ],
        owner_forbidden_count=1,
    )
    monkeypatch.setattr(grant_module, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        grant_module,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {"group-1": "gateway-owners"},
    )

    with pytest.raises(RuntimeError, match="grant postflight failed"):
        grant_module.grant_gateway_table_access(
            warehouse_id="wh-1",
            relation_prefix="mip.audit.mip_agent_gateway_growth_agent",
            principal="app-sp-1",
        )

    owner_check = next(
        statement
        for statement in workspace.statement_execution.statements
        if "mip_gateway_postflight_owner_forbidden" in statement
    )
    assert "catalog_owner" in owner_check
    assert "schema_owner" in owner_check
    assert "table_owner" in owner_check
    assert "'gateway-owners'" in owner_check
    assert "'mip_agent_gateway_legacy_payload'" in owner_check
