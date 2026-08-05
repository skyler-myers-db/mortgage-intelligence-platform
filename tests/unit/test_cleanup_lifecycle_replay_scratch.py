from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks.cleanup_lifecycle_replay_scratch import (
    cleanup_lifecycle_replay_scratch,
)


class _StatementExecution:
    def __init__(
        self,
        *,
        select_responses: list[list[list[object]]] | None = None,
    ) -> None:
        self.select_responses = list(select_responses or [])
        self.statements: list[str] = []

    def execute_statement(self, *, statement: str, **_: object) -> object:
        self.statements.append(statement)
        rows = (
            self.select_responses.pop(0)
            if statement.startswith("SELECT") and self.select_responses
            else []
        )
        return SimpleNamespace(
            status=SimpleNamespace(state="SUCCEEDED", error=None),
            result=SimpleNamespace(data_array=rows),
        )


def _workspace(
    *, select_responses: list[list[list[object]]] | None = None
) -> tuple[object, _StatementExecution]:
    execution = _StatementExecution(select_responses=select_responses)
    return SimpleNamespace(statement_execution=execution), execution


def test_drops_only_exact_audit_relations_and_proves_absence() -> None:
    workspace, execution = _workspace()

    cleanup_lifecycle_replay_scratch(
        warehouse_id="warehouse-1",
        catalog="mip",
        suffix="gha_123",
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert execution.statements[:2] == [
        "DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_target_gha_123`",
        "DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_borrower_gha_123`",
    ]
    assert "table_schema = 'audit'" in execution.statements[2]
    assert (
        "table_name IN ('lifecycle_replay_target_gha_123', "
        "'lifecycle_replay_borrower_gha_123')" in execution.statements[2]
    )


def test_nonempty_cleanup_postflight_is_fatal() -> None:
    workspace, _execution = _workspace(
        select_responses=[[['lifecycle_replay_target_gha_123']]]
    )

    with pytest.raises(RuntimeError, match="postflight was not empty"):
        cleanup_lifecycle_replay_scratch(
            warehouse_id="warehouse-1",
            catalog="mip",
            suffix="gha_123",
            workspace=workspace,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "suffix",
    ["", "gha_", "gha_123_extra", "manual_123", "gha-123", "gha_12.3"],
)
def test_rejects_any_non_github_run_suffix_before_sql(suffix: str) -> None:
    workspace, execution = _workspace()

    with pytest.raises(ValueError, match="expected gha_"):
        cleanup_lifecycle_replay_scratch(
            warehouse_id="warehouse-1",
            catalog="mip",
            suffix=suffix,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


def test_stale_cleanup_drops_only_reviewed_inventory_and_proves_absence() -> None:
    workspace, execution = _workspace(
        select_responses=[
            [
                ["lifecycle_replay_borrower_gha_100"],
                ["lifecycle_replay_target_gha_100"],
            ],
            [],
            [],
        ]
    )

    cleanup_lifecycle_replay_scratch(
        warehouse_id="warehouse-1",
        catalog="mip",
        suffix="gha_123",
        stale_older_than_hours=2,
        workspace=workspace,  # type: ignore[arg-type]
    )

    drops = [statement for statement in execution.statements if statement.startswith("DROP")]
    assert drops == [
        "DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_target_gha_123`",
        "DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_borrower_gha_123`",
        "DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_borrower_gha_100`",
        "DROP TABLE IF EXISTS `mip`.`audit`.`lifecycle_replay_target_gha_100`",
    ]
    assert execution.statements.count(execution.statements[0]) == 2


def test_stale_cleanup_rejects_unsafe_inventory_before_drop() -> None:
    workspace, execution = _workspace(
        select_responses=[[["lifecycle_replay_target_gha_1;DROP"]]]
    )

    with pytest.raises(RuntimeError, match="unsafe row"):
        cleanup_lifecycle_replay_scratch(
            warehouse_id="warehouse-1",
            catalog="mip",
            suffix="gha_123",
            stale_older_than_hours=2,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert not any(statement.startswith("DROP") for statement in execution.statements)


@pytest.mark.parametrize("hours", [0, 169])
def test_stale_cleanup_rejects_unbounded_age_before_sql(hours: int) -> None:
    workspace, execution = _workspace()

    with pytest.raises(ValueError, match="between 1 and 168"):
        cleanup_lifecycle_replay_scratch(
            warehouse_id="warehouse-1",
            catalog="mip",
            suffix="gha_123",
            stale_older_than_hours=hours,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


@pytest.mark.parametrize("catalog", ["", "mip.prod", "mip-prod", "mip`"])
def test_rejects_unsafe_catalog_before_sql(catalog: str) -> None:
    workspace, execution = _workspace()

    with pytest.raises(ValueError, match="catalog"):
        cleanup_lifecycle_replay_scratch(
            warehouse_id="warehouse-1",
            catalog=catalog,
            suffix="gha_123",
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []
