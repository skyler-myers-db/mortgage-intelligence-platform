"""KPI reproduce SQL is the statement the KPI's own read executes (audit flow-06 phase 2).

The proof text is never a copy: a recording client runs the real repository
read behind each KPI card (Home's portfolio preview for Home's criteria, the
approval funnel's population read) and the statement and binds it executed
must EQUAL the proof's. Every proof passes the append-only reproduce policy,
names gold/semantics relations only, and each filter chip's column is the one
its measure column counts.
"""

from __future__ import annotations

import re
from typing import Any, get_args

import pytest

from backend.schemas.kpi_proof import KpiProofKey
from backend.schemas.portfolio import PortfolioPreviewRequest
from backend.services import kpi_proof_sql
from backend.services.kpi_proof_sql import KpiProofRefusedError, KpiStatement, build_kpi_proof
from backend.services.proof_policy import hash_sql, kpi_proof_relations, validate_kpi_proof_sql
from backend.services.repositories.databricks_analytics import DatabricksAnalyticsRepository
from backend.services.repositories.databricks_portfolio import DatabricksPortfolioRepository

HOME_KEYS = [key for key in get_args(KpiProofKey) if key.startswith("home.")]
FUNNEL_KEYS = [key for key in get_args(KpiProofKey) if key.startswith("funnel.")]


class _RecordingSql:
    """Records every executed statement and its binds; answers with no rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.calls.append((statement, dict(parameters or {})))
        return []

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:
        self.calls.append((statement, dict(parameters or {})))
        return None


def _proof_params(kpi: KpiProofKey) -> dict[str, str]:
    return {param.name: param.value for param in build_kpi_proof(kpi).params}


@pytest.mark.parametrize("kpi", HOME_KEYS)
def test_a_home_proof_is_the_statement_the_home_preview_executes(kpi: KpiProofKey) -> None:
    sql = _RecordingSql()
    DatabricksPortfolioRepository(sql, cache_ttl_s=0).preview(  # type: ignore[arg-type]
        PortfolioPreviewRequest.model_validate({"criteria": {"marketing_eligibility": "Any"}})
    )
    preview_calls = [(stmt, params) for stmt, params in sql.calls if "preview_population" in stmt]
    assert len(preview_calls) == 1

    proof = build_kpi_proof(kpi)

    executed, binds = preview_calls[0]
    assert proof.sql == executed.strip()
    assert _proof_params(kpi) == {name: str(value) for name, value in binds.items()}


@pytest.mark.parametrize("kpi", FUNNEL_KEYS)
def test_a_funnel_proof_is_the_statement_the_funnel_read_executes(kpi: KpiProofKey) -> None:
    sql = _RecordingSql()
    DatabricksAnalyticsRepository(sql, cache_ttl_s=0).funnel_population()  # type: ignore[arg-type]
    assert len(sql.calls) == 1

    proof = build_kpi_proof(kpi)

    executed, binds = sql.calls[0]
    assert proof.sql == executed.strip()
    assert binds == {}
    assert proof.params == []


def _measure_expression(sql: str, measure_column: str) -> str:
    """The select-list expression aliased ``measure_column`` (top-level comma to alias)."""
    match = re.search(rf"\bAS\s+{re.escape(measure_column)}\b", sql)
    assert match, f"{measure_column} is a result column"
    depth = 0
    index = match.start() - 1
    while index >= 0:
        char = sql[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
        elif char == "," and depth == 0:
            break
        if depth < 0:
            break
        if depth == 0 and sql[: index + 1].upper().endswith("SELECT"):
            break
        index -= 1
    return sql[index + 1 : match.start()]


@pytest.mark.parametrize("kpi", get_args(KpiProofKey))
def test_every_proof_passes_the_policy_and_its_chips_match_its_measure(kpi: KpiProofKey) -> None:
    proof = build_kpi_proof(kpi)

    assert validate_kpi_proof_sql(proof.sql) == proof.sql
    assert proof.sql_hash == hash_sql(proof.sql)
    assert proof.relations
    assert set(proof.relations) <= kpi_proof_relations()
    for relation in proof.relations:
        assert relation in proof.sql
    for forbidden in (".silver.", ".first_party.", "cotality_mortgage_data", "raw_"):
        assert forbidden not in proof.sql.lower()
    expression = _measure_expression(proof.sql, proof.measure_column)
    measure_chips = [chip for chip in proof.predicates if chip.endswith(" = TRUE")]
    for chip in measure_chips:
        column = chip.removesuffix(" = TRUE")
        assert f"CASE WHEN {column} THEN 1 ELSE 0 END" in expression, (kpi, expression)
    if not measure_chips:
        assert "COUNT(*)" in expression
    assert proof.note == f"Column {proof.measure_column} of this statement is the number on the card."


def test_home_proofs_bind_nothing_for_the_whole_refreshed_book() -> None:
    for kpi in HOME_KEYS:
        proof = build_kpi_proof(kpi)
        assert proof.params == []
        assert "WHERE" not in proof.sql.split("FROM preview_population", 1)[1]


def test_the_measure_chips_are_pinned() -> None:
    assert {kpi: build_kpi_proof(kpi).predicates for kpi in get_args(KpiProofKey)} == {
        "home.addressable_population": [],
        "home.in_the_money": ["in_the_money = TRUE"],
        "home.high_opportunity": ["is_high_opportunity = TRUE"],
        "home.primary_offer_paths": ["offer_recommended = TRUE"],
        "funnel.population": [],
        "funnel.high_opportunity": ["is_high_opportunity = TRUE"],
    }


def test_the_sql_editor_link_follows_the_workspace_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kpi_proof_sql.settings, "databricks_host", "")
    assert build_kpi_proof("funnel.population").databricks_sql_url is None
    monkeypatch.setattr(kpi_proof_sql.settings, "databricks_host", "https://dbc-unit.cloud.databricks.com/")
    assert build_kpi_proof("funnel.population").databricks_sql_url == "https://dbc-unit.cloud.databricks.com/sql/editor"


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT COUNT(*) AS population FROM mip.silver.lien_current",
        "SELECT COUNT(*) AS population FROM mip.semantics.portfolio_headline_metric_view; DROP TABLE x",
        "SELECT owner_full_name AS population FROM mip.gold.borrower_360",
        "SELECT * FROM mip.gold.borrower_360",
    ],
)
def test_a_statement_outside_the_policy_is_refused_never_relaxed(
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
) -> None:
    monkeypatch.setitem(
        kpi_proof_sql.KPI_STATEMENTS,
        "funnel.population",
        lambda: KpiStatement(statement, {}, "population", None, ""),
    )
    with pytest.raises(KpiProofRefusedError):
        build_kpi_proof("funnel.population")
