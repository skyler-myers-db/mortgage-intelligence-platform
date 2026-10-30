"""The live-state skip rule for the catalog bootstrap (attack O12, M0-PR8).

``mip_init_catalog_schemas`` and its function republish are skipped only when
the live ``information_schema`` (catalogs, schemata, columns, routines,
parameters) and ``SHOW TBLPROPERTIES`` hash-equal the rendered sources. The
fake warehouse below answers those reads from the real rendered contract, so
the equal case is the live shape of a converged install; each fixture then
moves one fact and must run, with the PlanReport's EXECUTE-gap prediction.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import render_sql
from tools.databricks import catalog_bootstrap_skip_rule as rule
from tools.databricks.check_exit_codes import CHECK_DRIFT, CHECK_EXACT
from tools.databricks.deploy_plan_report import ExecuteGapPrediction, PlanReport

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rendered_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A repo-shaped root with the bootstrap rendered for catalog mip (no build artifact)."""

    root = tmp_path_factory.mktemp("rendered")
    render_sql.render(
        catalog="mip",
        demo_first_party_enabled=False,
        source_root=REPO / "sql",
        dest_root=root / "sql" / "_rendered",
    )
    return root


@pytest.fixture()
def files(rendered_root: Path) -> list[Path]:
    return rule.bootstrap_files(REPO / "databricks.yml", rendered_root)


class FakeWarehouse:
    """information_schema answers for a live catalog equal to ``contract``."""

    def __init__(self, contract: rule.Contract) -> None:
        self.statements: list[str] = []
        self.catalogs = set(contract.catalogs)
        self.schemas = {name.split(".", 1)[1] for name in contract.schemas}
        self.columns = [
            [name.split(".")[1], name.split(".")[2], column, index, data_type, "YES" if nullable else "NO"]
            for name, columns in contract.tables.items()
            for index, (column, data_type, nullable) in enumerate(columns, start=1)
        ]
        self.routines = [
            [
                name.split(".")[1],
                name.split(".")[2],
                spec["returns"].upper(),
                "YES" if spec["deterministic"] else "NO",
                spec["comment"],
                spec["body"],
            ]
            for name, spec in contract.functions.items()
        ]
        self.parameters = [
            [name.split(".")[1], name.split(".")[2], param, data_type.upper()]
            for name, spec in contract.functions.items()
            for param, data_type in spec["parameters"]
        ]
        self.properties = {
            name: [[key, value] for key, value in values.items()]
            for name, values in contract.table_properties.items()
        }
        self.fail = False

    def execute_statement(self, *, statement: str, **_kwargs: Any) -> object:
        self.statements.append(statement)
        if self.fail:
            raise RuntimeError("warehouse unavailable")
        if "information_schema.catalogs" in statement:
            catalog = _kwargs["parameters"][0].value
            rows = [[catalog]] if catalog in self.catalogs else []
        elif "information_schema.schemata" in statement:
            rows = [[schema] for schema in sorted(self.schemas)]
        elif "information_schema.columns" in statement:
            rows = self.columns
        elif "information_schema.routines" in statement:
            rows = self.routines
        elif "information_schema.parameters" in statement:
            rows = self.parameters
        elif statement.startswith("SHOW TBLPROPERTIES"):
            name = statement.split()[-1].replace("`", "")
            rows = self.properties.get(name, [])
        else:
            raise AssertionError(f"unexpected statement {statement}")
        assert not any(word in statement.upper() for word in ("CREATE ", "ALTER ", "GRANT ", "DROP ")), "read-only"
        return SimpleNamespace(
            status=SimpleNamespace(state="SUCCEEDED"),
            result=SimpleNamespace(data_array=rows),
            manifest=SimpleNamespace(total_chunk_count=1),
            statement_id="statement-1",
        )


def _workspace(warehouse: FakeWarehouse) -> object:
    return SimpleNamespace(statement_execution=warehouse)


def _check(
    tmp_path: Path, warehouse: FakeWarehouse, root: Path
) -> tuple[int, dict[str, Any]]:
    out = tmp_path / "skip-rule.json"
    rc = rule.main(
        [
            "check",
            "--warehouse-id",
            "warehouse-PLACEHOLDER",
            "--bundle",
            str(REPO / "databricks.yml"),
            "--repo-root",
            str(root),
            "--out",
            str(out),
        ],
        workspace_factory=lambda: _workspace(warehouse),
    )
    return rc, json.loads(out.read_text(encoding="utf-8"))


def test_the_rendered_bootstrap_parses_with_no_unknown_statement(files: list[Path]) -> None:
    contract = rule.rendered_contract(files)
    assert contract.unknown == []
    assert "mip" in contract.catalogs and "mip.gold" in contract.schemas
    assert set(contract.functions) == {
        "mip.gold.fn_build_cohort",
        "mip.gold.fn_segment_counts",
        "mip.gold.fn_lead_queue_url",
    }
    assert contract.table_properties["mip.audit.campaign_treatment_snapshot"]["delta.appendOnly"] == "true"
    assert contract.functions["mip.gold.fn_segment_counts"]["deterministic"] is False


def test_an_equal_live_catalog_skips_the_bootstrap(tmp_path: Path, files: list[Path]) -> None:
    warehouse = FakeWarehouse(rule.rendered_contract(files))
    rc, report = _check(tmp_path, warehouse, files[0].parents[3])

    assert rc == CHECK_EXACT and report["decision"] == "skip"
    assert report["rendered_sha256"] == report["live_sha256"]
    assert report["functions_republished"] == []
    assert report["execute_gap"]["predicted_seconds"] == 0


@pytest.mark.parametrize(
    "drift",
    (
        "function_body",
        "function_comment",
        "function_determinism",
        "missing_table",
        "column_type",
        "column_nullability",
        "table_property",
        "missing_schema",
    ),
)
def test_an_unequal_live_catalog_runs_and_predicts_the_execute_gap(
    tmp_path: Path, drift: str, files: list[Path]
) -> None:
    warehouse = FakeWarehouse(rule.rendered_contract(files))
    if drift == "function_body":
        warehouse.routines[0][5] = warehouse.routines[0][5].replace("COUNT", "SUM", 1)
    elif drift == "function_comment":
        warehouse.routines[1][4] = "edited outside the release"
    elif drift == "function_determinism":
        warehouse.routines[0][3] = "NO" if warehouse.routines[0][3] == "YES" else "YES"
    elif drift == "missing_table":
        first = warehouse.columns[0][:2]
        warehouse.columns = [row for row in warehouse.columns if row[:2] != first]
    elif drift == "column_type":
        warehouse.columns[0][4] = "bigint" if warehouse.columns[0][4] != "bigint" else "int"
    elif drift == "column_nullability":
        warehouse.columns[0][5] = "NO" if warehouse.columns[0][5] == "YES" else "YES"
    elif drift == "table_property":
        warehouse.properties = {name: [] for name in warehouse.properties}
    else:
        warehouse.schemas.discard("ref")
    rc, report = _check(tmp_path, warehouse, files[0].parents[3])

    assert rc == CHECK_DRIFT and report["decision"] == "run"
    assert report["rendered_sha256"] != report["live_sha256"]
    assert report["functions_republished"] == [
        "gold.fn_build_cohort",
        "gold.fn_lead_queue_url",
        "gold.fn_segment_counts",
    ], "the job republishes every function, so the gap covers all of them"
    assert report["execute_gap"]["predicted_seconds"] == sum(rule.EXECUTE_GAP_BUDGET_S.values())
    assert any(report["differing"].values())


def test_a_failed_live_read_never_skips(tmp_path: Path, files: list[Path]) -> None:
    warehouse = FakeWarehouse(rule.rendered_contract(files))
    warehouse.fail = True
    rc, report = _check(tmp_path, warehouse, files[0].parents[3])
    assert rc == CHECK_DRIFT and report["decision"] == "run" and report["live_error"]


def test_an_unknown_statement_kind_never_skips(tmp_path: Path) -> None:
    sql = tmp_path / "extra.sql"
    sql.write_text("CREATE SCHEMA IF NOT EXISTS mip.gold;\nGRANT SELECT ON SCHEMA mip.gold TO x;\n")
    contract = rule.rendered_contract([sql])
    live = rule.Contract(catalogs=set(), schemas={"mip.gold"})
    assert contract.unknown == ["grant"]
    assert rule.decide(contract, live)["decision"] == "run"


def test_statements_split_outside_quotes_and_comments() -> None:
    text = (
        "-- a comment; not a split\n"
        "CREATE SCHEMA IF NOT EXISTS mip.a COMMENT 'one; two';\n"
        "/* block; comment */ CREATE SCHEMA IF NOT EXISTS mip.b COMMENT 'it''s; fine'"
    )
    statements = rule.split_statements(text)
    assert len(statements) == 2
    assert statements[0].endswith("'one; two'") and "it''s; fine" in statements[1]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    (
        ("INTEGER", "int"),
        ("DECIMAL", "decimal(10,0)"),
        ("decimal(5, 2)", "decimal(5,2)"),
        ("ARRAY<STRING>", "array<string>"),
        ("STRUCT<`timestamp`: STRING>", "struct<timestamp:string>"),
        ("LONG", "bigint"),
    ),
)
def test_types_normalize_alike_on_both_sides(raw: str, normalized: str) -> None:
    assert rule.normalize_type(raw) == normalized


def test_the_plan_report_carries_the_prediction_publicly_without_the_catalog(
    files: list[Path],
) -> None:
    contract = rule.rendered_contract(files)
    report = rule.decide(contract, rule.Contract())
    gap = ExecuteGapPrediction.from_skip_rule(report)
    plan = PlanReport(position="pre-lease", app_resource="apps.mip_app", app_bound=True, execute_gap=gap)
    public = plan.public_summary()["execute_gap"]

    assert public["code"] == "gap" and public["predicted_seconds"] > 0
    assert public["functions"] == ["gold.fn_build_cohort", "gold.fn_lead_queue_url", "gold.fn_segment_counts"]
    assert "mip." not in json.dumps(public)
    assert ExecuteGapPrediction.from_skip_rule(None).public()["code"] == "unknown"
