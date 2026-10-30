"""The read-only treatment-table check never alters the table and matches the converger."""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound

from tests.unit.test_ensure_campaign_treatment_table import (
    _BASE_PROPERTIES,
    _CONSTRAINT_PROPERTIES,
    _response,
)
from tools.databricks import ensure_campaign_treatment_table as table_module
from tools.databricks.ensure_campaign_treatment_table import (
    _assert_contract,
    check_campaign_treatment_table,
    contract_drift,
)

_ROW_KIND = "delta.constraints.campaign_treatment_row_kind_chk"
_ASSIGNMENT = "delta.constraints.campaign_treatment_assignment_chk"


class _RecordingExecution:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    def execute_statement(self, *, statement: str, **_: object) -> object:
        self.statements.append(statement)
        assert statement.startswith("SHOW TBLPROPERTIES "), statement
        return _response(self.rows)


def _workspace(
    rows: list[list[str]], *, exists: bool = True
) -> tuple[object, _RecordingExecution, list[str]]:
    execution = _RecordingExecution(rows)
    lookups: list[str] = []

    def get(name: str) -> object:
        lookups.append(name)
        if not exists:
            raise NotFound("absent")
        return SimpleNamespace(full_name=name)

    return (
        SimpleNamespace(statement_execution=execution, tables=SimpleNamespace(get=get)),
        execution,
        lookups,
    )


def _properties(rows: list[list[str]]) -> dict[str, str]:
    return {key.lower(): value for key, value in rows}


def test_converged_table_reports_no_drift_with_one_property_read() -> None:
    workspace, execution, lookups = _workspace([*_BASE_PROPERTIES, *_CONSTRAINT_PROPERTIES])

    drift = check_campaign_treatment_table(
        warehouse_id="warehouse-1",
        catalog="mip",
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert drift == []
    assert lookups == ["mip.audit.campaign_treatment_snapshot"]
    assert execution.statements == [
        "SHOW TBLPROPERTIES `mip`.`audit`.`campaign_treatment_snapshot`"
    ]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (
            [*_BASE_PROPERTIES, _CONSTRAINT_PROPERTIES[0]],
            ["constraint campaign_treatment_assignment_chk: missing (convergence adds it)"],
        ),
        (
            [*_BASE_PROPERTIES, _CONSTRAINT_PROPERTIES[0], [_ASSIGNMENT, "assignment IS NULL"]],
            [
                "constraint campaign_treatment_assignment_chk: conflicting definition "
                "(convergence refuses)"
            ],
        ),
        (
            [*_BASE_PROPERTIES, *_CONSTRAINT_PROPERTIES, ["delta.constraints.extra", "x > 0"]],
            ["1 unreviewed Delta constraint(s) (convergence refuses)"],
        ),
        (
            [*_BASE_PROPERTIES[1:], *_CONSTRAINT_PROPERTIES],
            ["property delta.appendonly: missing (convergence refuses)"],
        ),
        (
            [["delta.appendOnly", "false"], *_BASE_PROPERTIES[1:], *_CONSTRAINT_PROPERTIES],
            ["property delta.appendonly: differs from the governed value " "(convergence refuses)"],
        ),
        (
            [*_BASE_PROPERTIES, [_ROW_KIND, "row_kind IN ('manifest"], _CONSTRAINT_PROPERTIES[1]],
            [
                "constraint campaign_treatment_row_kind_chk: conflicting definition "
                "(convergence refuses)"
            ],
        ),
    ],
)
def test_drift_is_reported_without_any_alter(rows: list[list[str]], expected: list[str]) -> None:
    workspace, execution, _ = _workspace(rows)

    drift = check_campaign_treatment_table(
        warehouse_id="warehouse-1",
        catalog="mip",
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert drift == expected
    assert all(sql.startswith("SHOW TBLPROPERTIES") for sql in execution.statements)
    assert all("mip" not in line for line in drift)


@pytest.mark.parametrize(
    ("allow_absent", "expected"),
    [(True, None), (False, ["treatment table: absent (convergence cannot add its constraints)"])],
)
def test_absent_table_is_reported_without_a_sql_statement(
    allow_absent: bool, expected: list[str] | None
) -> None:
    workspace, execution, _ = _workspace([], exists=False)

    drift = check_campaign_treatment_table(
        warehouse_id="warehouse-1",
        catalog="mip",
        allow_absent=allow_absent,
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert drift == expected
    assert execution.statements == []


_CONSTRAINT_VARIANTS = {
    _ROW_KIND: (
        None,
        "row_kind IN ('manifest', 'member')",
        "(ROW_KIND in ('manifest','member'))",
        "row_kind IN ('Manifest', 'member')",
        "row_kind IN ('manifest",
    ),
    _ASSIGNMENT: (
        None,
        "assignment IS NULL OR assignment IN ('treatment', 'holdout')",
        "assignment IS NULL OR assignment IN ('treatment')",
    ),
}
_PROPERTY_VARIANTS = {
    "delta.appendonly": (None, "true", "false"),
    "delta.logretentionduration": (None, "interval 2555 days", "interval 30 days"),
    "delta.deletedfileretentionduration": (None, "interval 2555 days", "interval 7 days"),
}


def test_drift_is_empty_exactly_when_the_converger_postflight_passes() -> None:
    keys = [*_CONSTRAINT_VARIANTS, *_PROPERTY_VARIANTS]
    variants = [*_CONSTRAINT_VARIANTS.values(), *_PROPERTY_VARIANTS.values()]
    disagreements: list[tuple[object, ...]] = []
    checked = 0
    for values in itertools.product(*variants):
        for extra in (False, True):
            properties = {
                key: value for key, value in zip(keys, values, strict=True) if value is not None
            }
            if extra:
                properties["delta.constraints.unreviewed"] = "x > 0"
            try:
                _assert_contract(dict(properties), require_all=True)
                passes = True
            except RuntimeError:
                passes = False
            if passes is not (contract_drift(dict(properties)) == []):
                disagreements.append((values, extra))
            checked += 1
    assert disagreements == []
    assert checked == 5 * 3 * 3 * 3 * 3 * 2


def test_cli_check_exits_one_on_drift_and_zero_when_exact(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    drifted, drifted_execution, _ = _workspace([*_BASE_PROPERTIES, _CONSTRAINT_PROPERTIES[0]])
    monkeypatch.setattr(table_module, "deployment_workspace_client", lambda: drifted)

    assert table_module.main(["--warehouse-id", "warehouse-1", "--check"]) == 1
    assert "[mip-treatment-table] drift: constraint campaign_treatment_assignment_chk" in (
        capsys.readouterr().out
    )
    assert all(sql.startswith("SHOW") for sql in drifted_execution.statements)

    exact, _, _ = _workspace([*_BASE_PROPERTIES, *_CONSTRAINT_PROPERTIES])
    monkeypatch.setattr(table_module, "deployment_workspace_client", lambda: exact)
    assert table_module.main(["--warehouse-id", "warehouse-1", "--check"]) == 0
    assert "check passed" in capsys.readouterr().out


def test_property_parser_lowercases_like_the_converger() -> None:
    assert contract_drift(_properties([*_BASE_PROPERTIES, *_CONSTRAINT_PROPERTIES])) == []
