from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks.read_agent_tool_probe_expectation import read_expected_count


def test_reads_exact_governed_cohort_count() -> None:
    statements: list[str] = []
    workspace = SimpleNamespace(
        statement_execution=SimpleNamespace(
            execute_statement=lambda **kwargs: (
                statements.append(kwargs["statement"])
                or SimpleNamespace(
                    status=SimpleNamespace(state="SUCCEEDED"),
                    result=SimpleNamespace(data_array=[["42"]]),
                )
            )
        )
    )

    assert (
        read_expected_count(
            workspace,
            warehouse_id="warehouse-id",
            catalog="mip",
            state="ca",
        )
        == 42
    )
    assert "fn_build_cohort" in statements[0]
    assert "array('itm')" in statements[0]
    assert "array('CA')" in statements[0]


def test_rejects_unvalidated_catalog_or_state() -> None:
    with pytest.raises(ValueError):
        read_expected_count(
            SimpleNamespace(),
            warehouse_id="warehouse-id",
            catalog="mip; DROP TABLE x",
            state="CA",
        )
    with pytest.raises(ValueError):
        read_expected_count(
            SimpleNamespace(),
            warehouse_id="warehouse-id",
            catalog="mip",
            state="CAL",
        )
    with pytest.raises(ValueError):
        read_expected_count(
            SimpleNamespace(),
            warehouse_id="warehouse-id",
            catalog="mip",
            state="CA",
            segment_code="itm'); DROP TABLE x; --",
        )
