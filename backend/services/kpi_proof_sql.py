"""Server-emitted reproduce SQL for the headline KPIs (audit 2026-09-21 ``flow-06`` phase 2).

A closed table maps each :data:`~backend.schemas.kpi_proof.KpiProofKey` to the
statement that produced its number, built from the SAME governed constants the
KPI's own read executes (never a copy of their text):

* ``home.*`` -- ``DatabricksPortfolioRepository._PREVIEW_SQL_TEMPLATE`` with
  the WHERE clause and binds of ``_build_preview_predicates`` for Home's
  criteria (lib/homeQueries ``HOME_PORTFOLIO_PREVIEW_CRITERIA``:
  ``marketing_eligibility='Any'``), exactly what ``preview()`` runs for Home;
* ``funnel.*`` -- ``DatabricksAnalyticsRepository._FUNNEL_POPULATION_SQL``, the
  approval funnel's population statement.

Every statement passes ``proof_policy.validate_kpi_proof_sql`` (read-only,
gold/semantics relations only); a key whose statement fails is refused, never
relaxed. Building a proof executes no SQL and writes no audit row.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from backend.config.settings import settings
from backend.schemas.kpi_proof import KpiProofKey, KpiProofParam, KpiProofResponse
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.proof_policy import hash_sql, kpi_proof_relations, validate_kpi_proof_sql
from backend.services.repositories.databricks_analytics import DatabricksAnalyticsRepository
from backend.services.repositories.databricks_portfolio import DatabricksPortfolioRepository

#: Home's KPI row criteria (frontend/src/lib/homeQueries.ts HOME_PORTFOLIO_PREVIEW_CRITERIA).
HOME_PREVIEW_CRITERIA: dict[str, Any] = {"marketing_eligibility": "Any"}


class KpiProofRefusedError(ValueError):
    """A KPI's statement failed the reproduce SQL policy; nothing is emitted."""


@dataclass(frozen=True)
class KpiStatement:
    """One KPI's statement as its read executes it."""

    sql: str
    params: dict[str, Any]
    measure_column: str
    #: The measure's own predicate (None for a plain COUNT(*)).
    measure_predicate: str | None
    #: The bound WHERE clause, without the keyword ("" when none applies).
    where: str


def home_statement(measure_column: str, measure_predicate: str | None) -> KpiStatement:
    where, params = DatabricksPortfolioRepository._build_preview_predicates(
        PortfolioCriteria(**HOME_PREVIEW_CRITERIA)
    )
    return KpiStatement(
        sql=DatabricksPortfolioRepository._PREVIEW_SQL_TEMPLATE.format(where=where),
        params=dict(params),
        measure_column=measure_column,
        measure_predicate=measure_predicate,
        where=where.removeprefix("WHERE ").strip(),
    )


def funnel_statement(measure_column: str, measure_predicate: str | None) -> KpiStatement:
    return KpiStatement(
        sql=DatabricksAnalyticsRepository._FUNNEL_POPULATION_SQL,
        params={},
        measure_column=measure_column,
        measure_predicate=measure_predicate,
        where="",
    )


KPI_STATEMENTS: dict[str, Callable[[], KpiStatement]] = {
    "home.addressable_population": lambda: home_statement("marketable_population", None),
    "home.in_the_money": lambda: home_statement("high_intent_leads", "in_the_money = TRUE"),
    "home.high_opportunity": lambda: home_statement("top_tier_opportunities", "is_high_opportunity = TRUE"),
    "home.primary_offer_paths": lambda: home_statement("offers_recommended", "offer_recommended = TRUE"),
    "funnel.population": lambda: funnel_statement("population", None),
    "funnel.high_opportunity": lambda: funnel_statement("high_opportunity", "is_high_opportunity = TRUE"),
}


def _param_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return ", ".join(str(item) for item in value)
    return str(value)


def _databricks_sql_url() -> str | None:
    host = settings.databricks_host
    return f"{host.rstrip('/')}/sql/editor" if host else None


def build_kpi_proof(kpi: KpiProofKey) -> KpiProofResponse:
    """The reproduce statement for one KPI card; raises KpiProofRefusedError on a policy failure."""

    statement = KPI_STATEMENTS[kpi]()
    try:
        sql = validate_kpi_proof_sql(statement.sql)
    except ValueError as exc:
        raise KpiProofRefusedError(str(exc)) from exc
    predicates = [statement.measure_predicate] if statement.measure_predicate else []
    if statement.where:
        predicates.append(statement.where)
    return KpiProofResponse(
        kpi=kpi,
        measure_column=statement.measure_column,
        predicates=predicates,
        sql=sql,
        sql_hash=hash_sql(sql),
        params=[KpiProofParam(name=name, value=_param_value(value)) for name, value in sorted(statement.params.items())],
        relations=sorted(relation for relation in kpi_proof_relations() if relation in sql),
        note=f"Column {statement.measure_column} of this statement is the number on the card.",
        databricks_sql_url=_databricks_sql_url(),
    )
