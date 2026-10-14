"""The fail-soft optional gold projection (audits wow-stage-2 / wow-stage-4).

A read of a registered optional column ahead of the gold refresh that builds
it (``UNRESOLVED_COLUMN``) must not 503 the Lead Queue or the dossier: the
resilient client latches the family, re-runs ONCE on NULL twins, and while
the latch holds rewrites before executing. Pins:

* one re-run, after which rows carry null points / null crossing fields;
* the latch skips the failing round trip, then expires after one soft TTL;
* an unrelated UNRESOLVED_COLUMN stays a 503 after ONE statement with no
  breaker failure, and a permission refusal is unchanged (no re-run);
* every fragment occurs verbatim in the backend SQL constants, each NULL
  twin keeps the original output names and only ever yields NULL, and no
  registered column appears in backend SQL outside its fragment;
* GET /api/v1/leads through the real lead repository answers 200 with null
  score_points instead of a 503 while the columns are missing.
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services import optional_gold_columns as ogc
from backend.services.databricks_sql import (
    DatabricksSqlColumnMissingError,
    DatabricksSqlError,
    DatabricksSqlObjectMissingError,
    DatabricksSqlPermissionError,
    ResilientSqlClient,
)
from backend.services.repositories.databricks_borrowers import DatabricksBorrowerRepository
from backend.services.repositories.databricks_leads import DatabricksLeadRepository
from backend.services.repositories.databricks_shared import (
    _BORROWER_DOSSIER_COLUMNS,
    _LEAD_POPULATION_SELECT_FROM_B360,
    _LEAD_POPULATION_SELECT_FROM_LP,
)
from backend.services.repositories.factory import get_lead_repository
from backend.services.resilience import CircuitBreaker, DependencyDownError, Resilient

REPO = Path(__file__).resolve().parents[2]


def _missing(column: str) -> str:
    return (
        "[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function parameter with "
        f"name `lp`.`{column}` cannot be resolved. Did you mean one of the following? "
        "[`lp`.`fit_points`, `lp`.`confidence`]. SQLSTATE: 42703"
    )


class _Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _clean_latch(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Clock]:
    clock = _Clock()
    monkeypatch.setattr(ogc, "_monotonic", clock)
    monkeypatch.setattr(settings, "mip_cache_ttl_s", 300)
    ogc._reset_optional_gold_columns_for_tests()
    yield clock
    ogc._reset_optional_gold_columns_for_tests()


class _Warehouse:
    """Bare-client double: a column is 'missing' until ``built`` is set."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.statements: list[str] = []
        self.rows = rows if rows is not None else [{"borrower_id": "B-OGCTESTROW001"}]
        self.missing: tuple[str, ...] = ogc.SCORE_POINTS_COLUMNS + ogc.SPREAD_HISTORY_COLUMNS
        self.error: BaseException | None = None

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        for column in self.missing:
            if re.search(rf"(?<!AS )\b(?:lp\.|b\.)?{column}\b(?=,)", statement):
                raise DatabricksSqlColumnMissingError(_missing(column))
        if "COUNT(*) AS n" in statement:
            return [{"n": len(self.rows)}]
        return [dict(row) for row in self.rows]

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


def _client(warehouse: _Warehouse, breaker: CircuitBreaker | None = None) -> ResilientSqlClient:
    resilient = Resilient[Any](
        breaker=breaker or CircuitBreaker("ogc-test", failure_threshold=1),
        dependency_name="warehouse",
        attempts=3,
        backoff_base=0.0,
        backoff_max=0.0,
        retry_on=(DatabricksSqlError, OSError),
        permission_denied_on=(DatabricksSqlPermissionError,),
        object_missing_on=(DatabricksSqlObjectMissingError, DatabricksSqlColumnMissingError),
    )
    return ResilientSqlClient(warehouse, resilient)  # type: ignore[arg-type]


_LIST_SQL = f"SELECT {_LEAD_POPULATION_SELECT_FROM_LP} FROM mip.gold.lead_population lp LIMIT 5"


def test_a_missing_score_points_column_reruns_once_on_null_twins(caplog: pytest.LogCaptureFixture) -> None:
    warehouse = _Warehouse()
    breaker = CircuitBreaker("ogc-test", failure_threshold=1)
    with caplog.at_level(logging.WARNING):
        rows = _client(warehouse, breaker).execute(_LIST_SQL)

    assert rows == warehouse.rows
    assert len(warehouse.statements) == 2, "the failing statement, then ONE re-run"
    assert "lp.economic_incentive_points" in warehouse.statements[0]
    rerun = warehouse.statements[1]
    assert "lp.economic_incentive_points" not in rerun
    assert "CAST(NULL AS DECIMAL(5,2)) AS economic_incentive_points, " in rerun
    assert rerun.endswith("lp.refreshed_at FROM mip.gold.lead_population lp LIMIT 5")
    assert breaker.state == CircuitBreaker.CLOSED, "a definitive answer, not an outage"
    latched = [r for r in caplog.records if getattr(r, "mip_event", None) == "optional_gold_columns_unavailable"]
    assert len(latched) == 1
    assert latched[0].mip_extras == {"family": "score_points", "error_class": "DatabricksSqlColumnMissingError"}  # type: ignore[attr-defined]
    assert "SELECT" not in caplog.text and "B-OGCTESTROW001" not in caplog.text


def test_the_latch_skips_the_failing_round_trip_then_expires(_clean_latch: _Clock) -> None:
    warehouse = _Warehouse()
    client = _client(warehouse)
    client.execute(_LIST_SQL)
    warehouse.statements.clear()

    client.execute(_LIST_SQL)
    assert len(warehouse.statements) == 1, "rewritten before executing while latched"
    assert "CAST(NULL AS DECIMAL(5,2)) AS fit_points" in warehouse.statements[0]

    _clean_latch.t += 300.0
    warehouse.missing = ()  # the gold refresh built the columns meanwhile
    warehouse.statements.clear()
    client.execute(_LIST_SQL)
    assert warehouse.statements == [_LIST_SQL], "the latch expired: the real projection again"


def test_the_dossier_family_reruns_on_its_own_twins() -> None:
    warehouse = _Warehouse()
    warehouse.missing = ("first_itm_week",)
    sql = f"SELECT {_BORROWER_DOSSIER_COLUMNS} FROM mip.gold.borrower_dossier WHERE borrower_id = :id"
    _client(warehouse).execute_one(sql, {"id": "B-OGCTESTROW001"})

    assert len(warehouse.statements) == 2
    assert ogc.latched_families() == ("spread_history",)
    assert (
        "CAST(NULL AS DATE) AS first_pos_date, CAST(NULL AS STRING) AS first_pos_rate_type, "
        "CAST(NULL AS DATE) AS first_itm_week, refreshed_at" in warehouse.statements[1]
    )


def test_an_unrelated_missing_column_is_a_503_after_one_statement() -> None:
    warehouse = _Warehouse()
    warehouse.missing = ()
    warehouse.error = DatabricksSqlColumnMissingError(_missing("not_a_registered_column"))
    breaker = CircuitBreaker("ogc-test", failure_threshold=1)

    with pytest.raises(DependencyDownError) as raised:
        _client(warehouse, breaker).execute(_LIST_SQL)

    assert len(warehouse.statements) == 1
    assert raised.value.kind == DependencyDownError.KIND_RETRIES_EXHAUSTED
    assert isinstance(raised.value.last_error, DatabricksSqlColumnMissingError)
    assert breaker.state == CircuitBreaker.CLOSED
    assert ogc.latched_families() == ()


def test_a_registered_column_outside_its_fragment_is_not_rewritten() -> None:
    warehouse = _Warehouse()
    warehouse.missing = ()
    warehouse.error = DatabricksSqlColumnMissingError(_missing("fit_points"))
    statement = "SELECT lp.borrower_id FROM mip.gold.lead_population lp"

    with pytest.raises(DependencyDownError):
        _client(warehouse).execute(statement)
    assert warehouse.statements == [statement]


def test_a_permission_refusal_is_unchanged() -> None:
    warehouse = _Warehouse()
    warehouse.error = DatabricksSqlPermissionError(
        "[INSUFFICIENT_PERMISSIONS] no SELECT on lp.economic_incentive_points. SQLSTATE: 42501"
    )
    with pytest.raises(DependencyDownError) as raised:
        _client(warehouse).execute(_LIST_SQL)
    assert raised.value.kind == DependencyDownError.KIND_PERMISSION_DENIED
    assert len(warehouse.statements) == 1, "never re-run"
    assert ogc.latched_families() == ()


def test_the_legacy_analyzer_wording_names_the_column_too() -> None:
    legacy = DatabricksSqlColumnMissingError(
        "cannot resolve 'lp.intent_trigger_points' given input columns: [lp.clip]; SQLSTATE: 42703"
    )
    assert ogc.family_for_missing_column(legacy, _LIST_SQL) == "score_points"
    assert ogc.family_for_missing_column(legacy, "SELECT 1") is None
    assert ogc.family_for_missing_column(DatabricksSqlError(_missing("fit_points")), _LIST_SQL) is None


def test_every_fragment_occurs_verbatim_in_the_backend_sql_constants() -> None:
    sql_constants = (
        _LEAD_POPULATION_SELECT_FROM_LP,
        _LEAD_POPULATION_SELECT_FROM_B360,
        _BORROWER_DOSSIER_COLUMNS,
        DatabricksLeadRepository._LIST_BASE_SQL_TEMPLATE,
        DatabricksLeadRepository._LIST_FILTERED_SQL_TEMPLATE,
        DatabricksLeadRepository._LIST_BY_GEO_SQL_TEMPLATE,
        DatabricksBorrowerRepository._GET_SQL,
    )
    for family in ogc.FAMILIES.values():
        for fragment, _twin in family.fragments:
            assert any(fragment in constant for constant in sql_constants), fragment
    assert ogc.LP_SCORE_POINTS_FRAGMENT + "lp.refreshed_at" in _LEAD_POPULATION_SELECT_FROM_LP
    assert ogc.B360_SCORE_POINTS_FRAGMENT + "b.refreshed_at" in _LEAD_POPULATION_SELECT_FROM_B360
    assert _LEAD_POPULATION_SELECT_FROM_LP.endswith("lp.refreshed_at")
    assert _LEAD_POPULATION_SELECT_FROM_B360.endswith("b.refreshed_at")


def test_each_null_twin_keeps_the_output_names_and_only_yields_null() -> None:
    for family in ogc.FAMILIES.values():
        for fragment, twin in family.fragments:
            names = [part.split(".")[-1] for part in fragment.rstrip(", ").split(", ")]
            assert names == list(family.columns)
            items = twin.rstrip(", ").split(", ")
            aliases = []
            for item in items:
                match = re.fullmatch(r"CAST\(NULL AS (DECIMAL\(5,2\)|DATE|STRING)\) AS (\w+)", item)
                assert match is not None, f"a twin may only yield NULL: {item}"
                aliases.append(match.group(2))
            assert aliases == names


_SQL_KEYWORD = re.compile(r"\b(SELECT|WHERE|ORDER BY|GROUP BY|FROM)\b")


def _backend_string_literals() -> Iterator[tuple[Path, str]]:
    for path in sorted((REPO / "backend").rglob("*.py")):
        if path.name == "optional_gold_columns.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield path, node.value


def test_no_registered_column_appears_in_backend_sql_outside_its_fragment() -> None:
    columns = [column for family in ogc.FAMILIES.values() for column in family.columns]
    offenders: list[str] = []
    for path, literal in _backend_string_literals():
        sql_like = bool(_SQL_KEYWORD.search(literal))
        for column in columns:
            qualified = re.search(rf"\b\w+\.{column}\b", literal)
            bare = re.search(rf"\b{column}\b", literal)
            if qualified or (sql_like and bare):
                offenders.append(f"{path.relative_to(REPO)}: {column}")
    assert offenders == [], (
        "a registered optional column is used in SQL outside its projection fragment "
        "(a WHERE / ORDER BY use cannot be rewritten to a NULL twin):\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# The route: GET /api/v1/leads through the real lead repository.
# ---------------------------------------------------------------------------

_LEAD_ROW: dict[str, Any] = {
    "clip": "clip_demo_ogc",
    "borrower_id": "B-OGCTESTROW001",
    "display_name": "Owner ab12cd34",
    "city": "Austin",
    "state": "TX",
    "zip": "78701",
    "segment_codes": ["itm"],
    "equity_estimate": 120000,
    "rate_spread_bps": 140,
    "opportunity_score": 72,
    "confidence": 64,
    "recommended_offer_code": "refi",
    "recommended_offer": "Refinance",
    "why_now": "The current mortgage appears above today's market reference rate.",
    "evidence_ids": [],
    "approval_status": "pending",
    "outreach_status": "none",
}


@pytest.fixture
def leads_route() -> Iterator[_Warehouse]:
    warehouse = _Warehouse(rows=[dict(_LEAD_ROW)])
    repository = DatabricksLeadRepository(_client(warehouse), cache_ttl_s=0)  # type: ignore[arg-type]
    app.dependency_overrides[get_lead_repository] = lambda: repository
    try:
        yield warehouse
    finally:
        app.dependency_overrides.pop(get_lead_repository, None)


def test_the_lead_queue_answers_200_with_null_points_while_the_columns_are_missing(
    leads_route: _Warehouse,
) -> None:
    response = TestClient(app).get("/api/v1/leads")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["borrower_id"] for row in body] == ["B-OGCTESTROW001"]
    assert body[0]["score_points"] is None, "NULL twins never become a fabricated 0"
    lists = [s for s in leads_route.statements if "economic_incentive_points" in s]
    assert len(lists) == 2, "one failing list statement, one re-run"
    assert "CAST(NULL AS DECIMAL(5,2)) AS economic_incentive_points" in lists[1]


def test_the_lead_queue_carries_score_points_once_gold_has_them(leads_route: _Warehouse) -> None:
    leads_route.missing = ()
    leads_route.rows = [
        {
            **_LEAD_ROW,
            "economic_incentive_points": "29.75",
            "intent_trigger_points": 18.0,
            "fit_points": "13.50",
            "relationship_points": "6.50",
            "evidence_points": "4.00",
        }
    ]
    response = TestClient(app).get("/api/v1/leads")

    assert response.status_code == 200, response.text
    assert response.json()[0]["score_points"] == {
        "economic_incentive": 29.75,
        "intent_trigger": 18.0,
        "fit": 13.5,
        "relationship": 6.5,
        "evidence": 4.0,
    }
