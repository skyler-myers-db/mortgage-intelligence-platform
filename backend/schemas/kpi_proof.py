"""Reproduce SQL for a headline KPI (audit 2026-09-21 ``flow-06`` phase 2).

``GET /api/kpi-proof?kpi=...`` emits the exact governed, parameter-bound,
gold-only statement that produced one KPI card's number, so a reviewer can
re-run it in Databricks SQL. The text is built from the same constants the
KPI's own read executes; nothing is composed client-side.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

KpiProofKey = Literal[
    "home.addressable_population",
    "home.in_the_money",
    "home.high_opportunity",
    "home.primary_offer_paths",
    "funnel.population",
    "funnel.high_opportunity",
]


class KpiProofParam(BaseModel):
    """One named bind the statement uses, as the KPI's read binds it."""

    name: str
    value: str


class KpiProofResponse(BaseModel):
    """The statement behind one KPI card and how to read its number."""

    kpi: KpiProofKey
    #: The result column whose value is the number on the card.
    measure_column: str
    #: Plain filter chips: the measure's own predicate, then each bound filter.
    predicates: list[str]
    sql: str
    sql_hash: str
    params: list[KpiProofParam]
    #: The governed relations the statement reads.
    relations: list[str]
    note: str
    databricks_sql_url: str | None = None
