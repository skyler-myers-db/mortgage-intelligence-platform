from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound

from tools.databricks.ensure_campaign_treatment_table import (
    _canonical_expression,
    ensure_campaign_treatment_table,
    execute_sql,
    main,
)

_BASE_PROPERTIES = [
    ["delta.appendOnly", "true"],
    ["delta.logRetentionDuration", "interval 2555 days"],
    ["delta.deletedFileRetentionDuration", "interval 2555 days"],
]
_CONSTRAINT_PROPERTIES = [
    ["delta.constraints.campaign_treatment_row_kind_chk", "(row_kind IN ('manifest', 'member'))"],
    [
        "delta.constraints.campaign_treatment_assignment_chk",
        "assignment IS NULL OR assignment IN ('treatment', 'holdout')",
    ],
]


def _response(
    rows: list[list[str]] | None = None,
    *,
    state: str = "SUCCEEDED",
    statement_id: str = "statement-1",
) -> object:
    return SimpleNamespace(
        status=SimpleNamespace(state=state, error="failure" if state != "SUCCEEDED" else None),
        result=SimpleNamespace(data_array=rows or []),
        statement_id=statement_id,
    )


class _StatementExecution:
    def __init__(
        self,
        property_snapshots: list[list[list[str]]],
        *,
        failed_alters: int = 0,
        polls: list[object] | None = None,
    ) -> None:
        self.property_snapshots = list(property_snapshots)
        self.failed_alters = failed_alters
        self.polls = list(polls or [])
        self.statements: list[str] = []
        self.canceled: list[str] = []

    def execute_statement(self, *, statement: str, **_: object) -> object:
        self.statements.append(statement)
        if statement.startswith("SHOW TBLPROPERTIES"):
            if not self.property_snapshots:
                raise AssertionError("unexpected property read")
            return _response(self.property_snapshots.pop(0))
        if statement.startswith("ALTER") and self.failed_alters:
            self.failed_alters -= 1
            return _response(state="FAILED")
        return _response()

    def get_statement(self, statement_id: str) -> object:
        assert statement_id == "statement-1"
        if not self.polls:
            raise AssertionError("unexpected statement poll")
        return self.polls.pop(0)

    def cancel_execution(self, statement_id: str) -> None:
        self.canceled.append(statement_id)


def _workspace(
    *snapshots: list[list[str]],
    failed_alters: int = 0,
    polls: list[object] | None = None,
) -> tuple[object, _StatementExecution]:
    execution = _StatementExecution(
        list(snapshots), failed_alters=failed_alters, polls=polls
    )
    return SimpleNamespace(
        statement_execution=execution,
        tables=SimpleNamespace(get=lambda _: SimpleNamespace(full_name="present")),
    ), execution


def test_adds_only_missing_constraints_then_verifies_exact_contract() -> None:
    workspace, execution = _workspace(
        _BASE_PROPERTIES,
        [*_BASE_PROPERTIES, *_CONSTRAINT_PROPERTIES],
    )

    added = ensure_campaign_treatment_table(
        warehouse_id="warehouse-1", catalog="mip", workspace=workspace  # type: ignore[arg-type]
    )

    assert added == [
        "campaign_treatment_row_kind_chk",
        "campaign_treatment_assignment_chk",
    ]
    alters = [statement for statement in execution.statements if statement.startswith("ALTER")]
    assert alters == [
        "ALTER TABLE `mip`.`audit`.`campaign_treatment_snapshot` ADD CONSTRAINT "
        "`campaign_treatment_row_kind_chk` CHECK (row_kind IN ('manifest', 'member'))",
        "ALTER TABLE `mip`.`audit`.`campaign_treatment_snapshot` ADD CONSTRAINT "
        "`campaign_treatment_assignment_chk` CHECK "
        "(assignment IS NULL OR assignment IN ('treatment', 'holdout'))",
    ]


def test_exact_existing_contract_is_a_verified_noop() -> None:
    properties = [*_BASE_PROPERTIES, *_CONSTRAINT_PROPERTIES]
    workspace, execution = _workspace(properties, properties)

    added = ensure_campaign_treatment_table(
        warehouse_id="warehouse-1", catalog="customer_uc", workspace=workspace  # type: ignore[arg-type]
    )

    assert added == []
    assert not any(statement.startswith("ALTER") for statement in execution.statements)
    assert all("`customer_uc`.`audit`" in statement for statement in execution.statements)


def test_allow_absent_uses_authoritative_table_api_without_sql() -> None:
    workspace, execution = _workspace()

    def absent(_: str) -> object:
        raise NotFound("missing")

    workspace.tables.get = absent  # type: ignore[attr-defined,method-assign]

    assert (
        ensure_campaign_treatment_table(
            warehouse_id="warehouse-1",
            catalog="mip",
            allow_absent=True,
            workspace=workspace,  # type: ignore[arg-type]
        )
        is None
    )
    assert execution.statements == []


def test_allow_absent_cli_never_claims_nonexistent_properties(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "tools.databricks.ensure_campaign_treatment_table.ensure_campaign_treatment_table",
        lambda **_: None,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "ensure_campaign_treatment_table.py",
            "--warehouse-id",
            "warehouse-1",
            "--allow-absent",
        ],
    )

    assert main() == 0
    output = capsys.readouterr().out
    assert "table is absent" in output
    assert "retention properties" not in output


def test_allow_absent_converges_an_existing_partial_install() -> None:
    workspace, execution = _workspace(
        _BASE_PROPERTIES,
        [*_BASE_PROPERTIES, *_CONSTRAINT_PROPERTIES],
    )

    added = ensure_campaign_treatment_table(
        warehouse_id="warehouse-1",
        catalog="mip",
        allow_absent=True,
        workspace=workspace,  # type: ignore[arg-type]
    )

    assert added == [
        "campaign_treatment_row_kind_chk",
        "campaign_treatment_assignment_chk",
    ]
    assert len([sql for sql in execution.statements if sql.startswith("ALTER")]) == 2


def test_conflicting_existing_constraint_fails_without_mutation() -> None:
    properties = [
        *_BASE_PROPERTIES,
        ["delta.constraints.campaign_treatment_row_kind_chk", "row_kind = 'member'"],
    ]
    workspace, execution = _workspace(properties)

    with pytest.raises(RuntimeError, match="conflicts with the governed definition"):
        ensure_campaign_treatment_table(
            warehouse_id="warehouse-1", catalog="mip", workspace=workspace  # type: ignore[arg-type]
        )

    assert not any(statement.startswith("ALTER") for statement in execution.statements)


@pytest.mark.parametrize(
    "drifted",
    [
        "row_kind IN ('MANIFEST', 'MEMBER')",
        "row_kind IN ('mani fest', 'member')",
        "row_kind IN ('mani`fest', 'member')",
        "row_kind IN ('mani''fest', 'member')",
    ],
)
def test_literal_sensitive_constraint_drift_is_rejected(drifted: str) -> None:
    properties = [
        *_BASE_PROPERTIES,
        ["delta.constraints.campaign_treatment_row_kind_chk", drifted],
    ]
    workspace, execution = _workspace(properties)

    with pytest.raises(RuntimeError, match="conflicts with the governed definition"):
        ensure_campaign_treatment_table(
            warehouse_id="warehouse-1", catalog="mip", workspace=workspace  # type: ignore[arg-type]
        )

    assert not any(statement.startswith("ALTER") for statement in execution.statements)


def test_canonicalizer_preserves_literals_but_normalizes_sql_syntax() -> None:
    expected = "row_kind IN ('manifest', 'member')"
    assert _canonical_expression("( `ROW_KIND`  in('manifest','member') )") == (
        _canonical_expression(expected)
    )
    assert _canonical_expression("row_kind IN ('MANIFEST','member')") != (
        _canonical_expression(expected)
    )


def test_unexpected_extra_constraint_is_rejected() -> None:
    properties = [
        *_BASE_PROPERTIES,
        *_CONSTRAINT_PROPERTIES,
        ["delta.constraints.block_all_treatment", "assignment IS NULL"],
    ]
    workspace, execution = _workspace(properties)

    with pytest.raises(RuntimeError, match="Unexpected Delta constraints"):
        ensure_campaign_treatment_table(
            warehouse_id="warehouse-1", catalog="mip", workspace=workspace  # type: ignore[arg-type]
        )

    assert not any(statement.startswith("ALTER") for statement in execution.statements)


def test_accepts_only_an_exact_constraint_after_concurrent_add_race() -> None:
    final_properties = [*_BASE_PROPERTIES, *_CONSTRAINT_PROPERTIES]
    workspace, execution = _workspace(
        _BASE_PROPERTIES,
        final_properties,
        final_properties,
        final_properties,
        failed_alters=2,
    )

    added = ensure_campaign_treatment_table(
        warehouse_id="warehouse-1", catalog="mip", workspace=workspace  # type: ignore[arg-type]
    )

    assert added == [
        "campaign_treatment_row_kind_chk",
        "campaign_treatment_assignment_chk",
    ]
    assert len([sql for sql in execution.statements if sql.startswith("ALTER")]) == 2


def test_failed_add_remains_fatal_when_constraint_is_still_missing() -> None:
    workspace, execution = _workspace(
        _BASE_PROPERTIES,
        _BASE_PROPERTIES,
        failed_alters=1,
    )

    with pytest.raises(RuntimeError, match="SQL statement did not succeed"):
        ensure_campaign_treatment_table(
            warehouse_id="warehouse-1", catalog="mip", workspace=workspace  # type: ignore[arg-type]
        )

    assert len([sql for sql in execution.statements if sql.startswith("ALTER")]) == 1


def test_execute_sql_polls_active_statement_to_success() -> None:
    workspace, execution = _workspace(
        polls=[_response(state="RUNNING"), _response(state="SUCCEEDED")]
    )
    execution.failed_alters = 1
    initial_execute = execution.execute_statement

    def pending_execute(**kwargs: object) -> object:
        initial_execute(**kwargs)
        return _response(state="PENDING")

    execution.execute_statement = pending_execute  # type: ignore[method-assign]

    response = execute_sql(
        workspace,  # type: ignore[arg-type]
        warehouse_id="warehouse-1",
        statement="ALTER TABLE governed ADD CONSTRAINT exact CHECK (value = 1)",
        timeout_s=10,
        poll_interval_s=0,
    )

    assert response.status.state == "SUCCEEDED"  # type: ignore[attr-defined]
    assert execution.canceled == []


def test_execute_sql_cancels_at_bounded_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, execution = _workspace(polls=[_response(state="RUNNING")])

    def pending_execute(**_: object) -> object:
        return _response(state="PENDING")

    execution.execute_statement = pending_execute  # type: ignore[method-assign]
    monotonic = iter([0.0, 1.0])
    monkeypatch.setattr("tools.databricks.ensure_campaign_treatment_table.time.monotonic", lambda: next(monotonic))

    with pytest.raises(RuntimeError, match="deadline and was canceled"):
        execute_sql(
            workspace,  # type: ignore[arg-type]
            warehouse_id="warehouse-1",
            statement="ALTER TABLE governed ADD CONSTRAINT exact CHECK (value = 1)",
            timeout_s=1,
            poll_interval_s=0,
        )

    assert execution.canceled == ["statement-1"]


def test_execute_sql_rejects_polled_terminal_failure() -> None:
    workspace, execution = _workspace(polls=[_response(state="FAILED")])

    def pending_execute(**_: object) -> object:
        return _response(state="PENDING")

    execution.execute_statement = pending_execute  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match=r"did not succeed \(FAILED\)"):
        execute_sql(
            workspace,  # type: ignore[arg-type]
            warehouse_id="warehouse-1",
            statement="ALTER TABLE governed ADD CONSTRAINT exact CHECK (value = 1)",
            timeout_s=10,
            poll_interval_s=0,
        )

    assert execution.canceled == []


def test_missing_retention_property_fails_before_constraint_mutation() -> None:
    workspace, execution = _workspace(_BASE_PROPERTIES[:-1])

    with pytest.raises(RuntimeError, match="deletedfileretentionduration"):
        ensure_campaign_treatment_table(
            warehouse_id="warehouse-1", catalog="mip", workspace=workspace  # type: ignore[arg-type]
        )

    assert not any(statement.startswith("ALTER") for statement in execution.statements)


@pytest.mark.parametrize("catalog", ["mip.prod", "mip-prod", "mip`prod", ""])
def test_rejects_unsafe_catalog_identifiers(catalog: str) -> None:
    workspace, execution = _workspace()

    with pytest.raises(ValueError, match="Invalid catalog identifier"):
        ensure_campaign_treatment_table(
            warehouse_id="warehouse-1", catalog=catalog, workspace=workspace  # type: ignore[arg-type]
        )

    assert execution.statements == []
