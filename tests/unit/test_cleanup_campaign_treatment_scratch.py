from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks.cleanup_campaign_treatment_scratch import (
    cleanup_campaign_treatment_scratch,
)


class _StatementExecution:
    def __init__(
        self, *, remaining: int = 0, stale_rows: list[list[object]] | None = None
    ) -> None:
        self.remaining = remaining
        self.stale_rows = list(stale_rows or [])
        self.stale_reads = 0
        self.statements: list[str] = []

    def execute_statement(self, *, statement: str, **_: object) -> object:
        self.statements.append(statement)
        if statement.startswith("SELECT COUNT"):
            rows: list[list[object]] = [[str(self.remaining)]]
        elif statement.startswith("SELECT table_name"):
            rows = self.stale_rows if self.stale_reads == 0 else []
            self.stale_reads += 1
        else:
            rows = []
        return SimpleNamespace(
            status=SimpleNamespace(state="SUCCEEDED", error=None),
            result=SimpleNamespace(data_array=rows),
        )


def _workspace(
    *, remaining: int = 0, stale_rows: list[list[object]] | None = None
) -> tuple[object, _StatementExecution]:
    execution = _StatementExecution(remaining=remaining, stale_rows=stale_rows)
    return SimpleNamespace(statement_execution=execution), execution


def test_drops_only_deterministic_exact_table_and_verifies_absence() -> None:
    workspace, execution = _workspace()

    cleanup_campaign_treatment_scratch(
        warehouse_id="warehouse-1",
        catalog="mip",
        suffix="gha_123",
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert execution.statements[0] == (
        "DROP TABLE IF EXISTS `mip`.`audit`.`campaign_treatment_cap_smoke_gha_123`"
    )
    assert "table_name = 'campaign_treatment_cap_smoke_gha_123'" in execution.statements[1]


def test_removes_only_safely_named_aged_github_scratch_tables() -> None:
    workspace, execution = _workspace(
        stale_rows=[["campaign_treatment_cap_smoke_gha_99"]]
    )

    cleanup_campaign_treatment_scratch(
        warehouse_id="warehouse-1",
        catalog="mip",
        suffix="gha_123",
        stale_older_than_hours=2,
        workspace=workspace,  # type: ignore[arg-type]
    )

    drops = [sql for sql in execution.statements if sql.startswith("DROP TABLE")]
    assert drops == [
        "DROP TABLE IF EXISTS `mip`.`audit`.`campaign_treatment_cap_smoke_gha_123`",
        "DROP TABLE IF EXISTS `mip`.`audit`.`campaign_treatment_cap_smoke_gha_99`",
    ]
    assert execution.stale_reads == 2


def test_rejects_unsafe_stale_query_row_before_any_drop() -> None:
    workspace, execution = _workspace(
        stale_rows=[["campaign_treatment_cap_smoke_not_owned"]]
    )

    with pytest.raises(RuntimeError, match="unsafe row"):
        cleanup_campaign_treatment_scratch(
            warehouse_id="warehouse-1",
            catalog="mip",
            suffix="gha_123",
            stale_older_than_hours=2,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert not any(sql.startswith("DROP TABLE") for sql in execution.statements)


def test_nonzero_cleanup_postflight_is_fatal() -> None:
    workspace, _ = _workspace(remaining=1)

    with pytest.raises(RuntimeError, match="postflight was not zero"):
        cleanup_campaign_treatment_scratch(
            warehouse_id="warehouse-1",
            catalog="mip",
            suffix="gha_123",
            workspace=workspace,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("hours", [0, -1, 169])
def test_rejects_unsafe_stale_age(hours: int) -> None:
    workspace, execution = _workspace()

    with pytest.raises(ValueError, match="stale_older_than_hours"):
        cleanup_campaign_treatment_scratch(
            warehouse_id="warehouse-1",
            catalog="mip",
            suffix="gha_123",
            stale_older_than_hours=hours,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []


@pytest.mark.parametrize("suffix", ["", "random.uuid", "gha-123", "bad`name"])
def test_rejects_unsafe_suffix(suffix: str) -> None:
    workspace, execution = _workspace()

    with pytest.raises(ValueError, match="suffix"):
        cleanup_campaign_treatment_scratch(
            warehouse_id="warehouse-1",
            catalog="mip",
            suffix=suffix,
            workspace=workspace,  # type: ignore[arg-type]
        )

    assert execution.statements == []
