"""Credential-free contracts for the live at-cap treatment proof."""

from __future__ import annotations

import json
from itertools import product
from typing import Any

import pytest

from tests.integration import test_campaign_treatment_at_cap_live as at_cap_live

_PRODUCTION_PARTS = ("mip", "audit", "campaign_treatment_snapshot")
_PRODUCTION_TABLE_FORMS = tuple(
    ".".join(
        f"`{part}`" if quoted else part
        for part, quoted in zip(_PRODUCTION_PARTS, quote_parts, strict=True)
    )
    for quote_parts in product((False, True), repeat=len(_PRODUCTION_PARTS))
)


class _RecordingClient:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.statements: list[str] = []
        self.fail_create = fail_create

    def execute(
        self,
        statement: str,
        _parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        self.statements.append(statement)
        if self.fail_create and statement.startswith("CREATE TABLE"):
            raise RuntimeError("forced create failure")
        return []

    def execute_one(
        self,
        statement: str,
        _parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> dict[str, Any] | None:
        self.statements.append(statement)
        return None


def test_sql_wrapper_rewrites_only_exact_production_treatment_fqn() -> None:
    delegate = _RecordingClient()
    wrapper = at_cap_live._ScratchTreatmentSqlClient(
        delegate,
        production_table="mip.audit.campaign_treatment_snapshot",
        scratch_table="mip.audit.campaign_treatment_cap_smoke_contract",
    )
    statement = (
        "SELECT * FROM mip.audit.campaign_treatment_snapshot "
        "WHERE note = 'campaign_treatment_snapshot' "
        "AND near_name = 'mip.audit.campaign_treatment_snapshot_archive'"
    )

    wrapper.execute(statement)

    assert delegate.statements == [
        "SELECT * FROM mip.audit.campaign_treatment_cap_smoke_contract "
        "WHERE note = 'campaign_treatment_snapshot' "
        "AND near_name = 'mip.audit.campaign_treatment_snapshot_archive'"
    ]
    assert wrapper.rewrite_count == 1
    assert wrapper.rewrite_shapes == ["FROM"]


def test_sql_wrapper_fails_closed_on_unrecognized_production_fqn_context() -> None:
    delegate = _RecordingClient()
    wrapper = at_cap_live._ScratchTreatmentSqlClient(
        delegate,
        production_table="mip.audit.campaign_treatment_snapshot",
        scratch_table="mip.audit.campaign_treatment_cap_smoke_contract",
    )

    with pytest.raises(AssertionError, match="unrecognized production"):
        wrapper.execute("DELETE FROM TABLE mip.audit.campaign_treatment_snapshot")

    assert delegate.statements == []


@pytest.mark.parametrize("production_form", _PRODUCTION_TABLE_FORMS)
@pytest.mark.parametrize(
    ("statement_template", "expected_prefix", "shape"),
    [
        (
            "MERGE INTO {table} AS target USING source",
            "MERGE INTO mip.audit.campaign_treatment_cap_smoke_contract",
            "MERGE INTO",
        ),
        (
            "SELECT * FROM {table}",
            "SELECT * FROM mip.audit.campaign_treatment_cap_smoke_contract",
            "FROM",
        ),
        (
            "DESCRIBE HISTORY {table} LIMIT 1",
            "DESCRIBE HISTORY mip.audit.campaign_treatment_cap_smoke_contract",
            "DESCRIBE HISTORY",
        ),
    ],
)
def test_sql_wrapper_rewrites_reviewed_backtick_quoted_relation_contexts(
    production_form: str,
    statement_template: str,
    expected_prefix: str,
    shape: str,
) -> None:
    delegate = _RecordingClient()
    wrapper = at_cap_live._ScratchTreatmentSqlClient(
        delegate,
        production_table="mip.audit.campaign_treatment_snapshot",
        scratch_table="mip.audit.campaign_treatment_cap_smoke_contract",
    )

    wrapper.execute(statement_template.format(table=production_form))

    assert delegate.statements[0].startswith(expected_prefix)
    assert wrapper.rewrite_count == 1
    assert wrapper.rewrite_shapes == [shape]


@pytest.mark.parametrize("production_form", _PRODUCTION_TABLE_FORMS)
def test_sql_wrapper_rejects_unknown_backtick_quoted_production_write(
    production_form: str,
) -> None:
    delegate = _RecordingClient()
    wrapper = at_cap_live._ScratchTreatmentSqlClient(
        delegate,
        production_table="mip.audit.campaign_treatment_snapshot",
        scratch_table="mip.audit.campaign_treatment_cap_smoke_contract",
    )

    with pytest.raises(AssertionError, match="unrecognized production"):
        wrapper.execute(f"DELETE FROM {production_form}")

    assert delegate.statements == []


def test_scratch_context_always_attempts_drop_after_create_failure() -> None:
    client = _RecordingClient(fail_create=True)

    with (
        pytest.raises(RuntimeError, match="forced create failure"),
        at_cap_live._scratch_treatment_table(
            client,
            production_table="mip.audit.campaign_treatment_snapshot",
            scratch_table="mip.audit.campaign_treatment_cap_smoke_contract",
        ),
    ):
        raise AssertionError("unreachable")

    assert client.statements == [
        "CREATE TABLE mip.audit.campaign_treatment_cap_smoke_contract "
        "LIKE mip.audit.campaign_treatment_snapshot",
        "DROP TABLE IF EXISTS mip.audit.campaign_treatment_cap_smoke_contract",
    ]


def test_live_config_requires_credentials_and_explicit_mutation_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DATABRICKS_HOST",
        "DATABRICKS_SERVER_HOSTNAME",
        "DATABRICKS_TOKEN",
        "DATABRICKS_WAREHOUSE_ID",
        "MIP_LIVE_MUTATION_OK",
    ):
        monkeypatch.delenv(name, raising=False)
    assert at_cap_live._live_warehouse_config() is None

    monkeypatch.setenv("DATABRICKS_HOST", "workspace.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-token")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "warehouse-id")
    assert at_cap_live._live_warehouse_config() is None

    monkeypatch.setenv("MIP_LIVE_MUTATION_OK", "1")
    assert at_cap_live._live_warehouse_config() == (
        "https://workspace.example",
        "test-token",
        "warehouse-id",
    )


def test_live_scratch_suffix_must_be_explicit_and_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIP_LIVE_SCRATCH_SUFFIX", raising=False)
    with pytest.raises(ValueError, match="scratch suffix"):
        at_cap_live._scratch_suffix()

    monkeypatch.setenv("MIP_LIVE_SCRATCH_SUFFIX", "gha_123")
    assert at_cap_live._scratch_suffix() == "gha_123"


def test_at_cap_selection_uses_only_canonical_governed_eligibility() -> None:
    class _AtCapClient(_RecordingClient):
        def execute(
            self,
            statement: str,
            _parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
        ) -> list[dict[str, Any]]:
            self.statements.append(statement)
            return [
                {"borrower_id": f"B-{index:013X}"}
                for index in range(at_cap_live.AT_CAP_MEMBER_COUNT)
            ]

    client = _AtCapClient()

    borrower_ids = at_cap_live._select_at_cap_masked_borrower_ids(client)

    assert len(borrower_ids) == 10_000
    statement = client.statements[0]
    assert "FROM mip.gold.borrower_360 AS b" in statement
    assert "b.marketing_eligible = TRUE" in statement
    assert "b.consent_status = 'opt_in'" in statement
    assert "b.suppression_reason IS NULL" in statement
    assert "COALESCE(b.dnc, FALSE) = FALSE" in statement
    assert "COALESCE(b.has_unresolved_owner, FALSE) = FALSE" in statement
    assert "LIMIT 10000" in statement


@pytest.mark.parametrize("unsafe", ["audit.prod", "audit;drop", "audit-name", ""])
def test_scratch_identifiers_fail_closed(unsafe: str) -> None:
    with pytest.raises(ValueError, match="unsafe schema identifier"):
        at_cap_live._safe_identifier(unsafe, field="schema")


def test_release_ceiling_accounts_for_all_production_round_trips() -> None:
    baseline = json.loads(at_cap_live._LOAD_TEST_BASELINE.read_text(encoding="utf-8"))
    canonical_budget_ms = baseline["endpoints"]["POST /api/v1/portfolio/create"]["p95_budget_ms"]

    assert at_cap_live.AT_CAP_MEMBER_COUNT == 10_000
    assert canonical_budget_ms == 5_000
    assert canonical_budget_ms / 1000 == at_cap_live.AT_CAP_MATERIALIZATION_CEILING_SECONDS
