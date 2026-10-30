"""Owner convergence touches only governed schemas, and only the jobs identity's objects (M2-PR5).

jobs/converge_governed_owner.py ends every App-triggerable job that creates or
replaces governed objects. Against a fake Spark that serves an inventory with
objects inside and outside the governed schemas, owned by the jobs identity,
the deployer and others:

* the only ALTERs are ``ALTER TABLE|VIEW|FUNCTION <pinned catalog>.<governed
  schema>.<name> OWNER TO <pinned group>``, for objects the run's identity owns;
* the deployer's objects (the reviewed Growth Agent functions among them),
  other owners' objects, pipeline datasets and the catalog and schemas
  themselves are never altered;
* a row outside the governed schemas or an unreviewed table type refuses
  before any ALTER; an overridden ``--catalog`` refuses before any SQL;
* :data:`GOVERNED_SCHEMAS` is tools/render_sql.py's governed set, and the task
  is the last one, on ALL_DONE, in exactly the jobs that create or replace
  objects.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from jobs import converge_governed_owner as owner
from tools import render_sql
from tools.databricks import install_pins

ROOT = Path(__file__).resolve().parents[2]
JOBS = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))["resources"]["jobs"]
JOBS_SP = "00000000-0000-4000-8000-000000000002"
DEPLOYER = "00000000-0000-4000-8000-000000000001"
GROUP = "mip-data-owners"
ALTER = re.compile(
    r"ALTER (TABLE|VIEW|FUNCTION) `mip`\.`(?P<schema>[a-z_]+)`\.`(?P<name>[A-Za-z0-9_]+)` OWNER TO `mip-data-owners`\Z"
)


def _table(schema: str, name: str, kind: str, table_owner: str, catalog: str = "mip") -> dict[str, str]:
    return {
        "table_catalog": catalog,
        "table_schema": schema,
        "table_name": name,
        "table_type": kind,
        "table_owner": table_owner,
    }


def _routine(schema: str, name: str, routine_owner: str) -> dict[str, str]:
    return {
        "routine_catalog": "mip",
        "routine_schema": schema,
        "routine_name": name,
        "routine_owner": routine_owner,
    }


TABLES = [
    _table("gold", "borrower_360", "MANAGED", JOBS_SP),
    _table("gold", "lead_scores", "MANAGED", GROUP),
    _table("semantics", "lead_population_view", "VIEW", JOBS_SP),
    _table("semantics", "certified_lead_generation", "METRIC_VIEW", JOBS_SP),
    _table("silver", "property_owners_primary", "VIEW", JOBS_SP.upper()),
    _table("silver", "lien_current", "STREAMING_TABLE", JOBS_SP),
    _table("silver", "owner_rollup", "MATERIALIZED_VIEW", JOBS_SP),
    _table("audit", "campaign_treatment", "MANAGED", DEPLOYER),
    _table("ref", "lender_dictionary", "MANAGED", "someone@example.invalid"),
]
ROUTINES = [
    _routine("gold", "fn_rate_spread", JOBS_SP),
    _routine("gold", "fn_build_cohort", DEPLOYER),
    _routine("gold", "fn_segment_counts", DEPLOYER),
    _routine("gold", "fn_lead_queue_url", DEPLOYER),
]


class FakeSpark:
    def __init__(self, tables: list[dict[str, str]], routines: list[dict[str, str]]) -> None:
        self.tables, self.routines = tables, routines
        self.statements: list[str] = []

    def execute(self, statement: str) -> list[dict[str, Any]]:
        self.statements.append(statement)
        if statement == "SELECT current_user() AS identity":
            return [{"identity": JOBS_SP}]
        if "information_schema.tables" in statement:
            return self.tables
        if "information_schema.routines" in statement:
            return self.routines
        assert statement.startswith("ALTER "), statement
        return []

    @property
    def alters(self) -> list[str]:
        return [statement for statement in self.statements if statement.startswith("ALTER ")]


def test_only_the_jobs_identitys_objects_in_governed_schemas_are_handed_over() -> None:
    spark = FakeSpark(TABLES, ROUTINES)

    assert owner.converge(spark.execute, catalog="mip", owner_group=GROUP) == 0

    assert spark.alters == sorted(
        [
            "ALTER FUNCTION `mip`.`gold`.`fn_rate_spread` OWNER TO `mip-data-owners`",
            "ALTER TABLE `mip`.`gold`.`borrower_360` OWNER TO `mip-data-owners`",
            "ALTER VIEW `mip`.`semantics`.`certified_lead_generation` OWNER TO `mip-data-owners`",
            "ALTER VIEW `mip`.`semantics`.`lead_population_view` OWNER TO `mip-data-owners`",
            "ALTER VIEW `mip`.`silver`.`property_owners_primary` OWNER TO `mip-data-owners`",
        ]
    )
    for statement in spark.alters:
        match = ALTER.fullmatch(statement)
        assert match and match.group("schema") in owner.GOVERNED_SCHEMAS, statement
    altered = " ".join(spark.alters)
    for untouched in ("fn_build_cohort", "fn_segment_counts", "fn_lead_queue_url", "campaign_treatment",
                      "lender_dictionary", "lead_scores", "lien_current", "owner_rollup"):
        assert untouched not in altered, untouched
    # Never the catalog or a schema itself: every ALTER names a three-part object.
    assert not any(re.match(r"ALTER (CATALOG|SCHEMA)", s) for s in spark.statements)


def test_the_inventory_reads_only_the_governed_schemas_of_the_pinned_catalog() -> None:
    tables, routines = owner.inventory_statements("mip")
    governed = ", ".join(f"'{schema}'" for schema in owner.GOVERNED_SCHEMAS)
    assert tables.endswith(f"FROM `mip`.information_schema.tables WHERE table_schema IN ({governed})")
    assert routines.endswith(f"FROM `mip`.information_schema.routines WHERE routine_schema IN ({governed})")


@pytest.mark.parametrize(
    "stray",
    [
        _table("information_schema", "tables", "VIEW", JOBS_SP),
        _table("someone_elses_schema", "t", "MANAGED", JOBS_SP),
        _table("gold", "t", "MANAGED", JOBS_SP, catalog="another_catalog"),
        _table("gold", "t", "FOREIGN", JOBS_SP),
    ],
)
def test_a_row_outside_the_governed_schemas_or_an_unreviewed_type_refuses_before_any_alter(
    stray: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    spark = FakeSpark([*TABLES, stray], ROUTINES)
    assert owner.converge(spark.execute, catalog="mip", owner_group=GROUP) == 2
    assert spark.alters == []
    assert "nothing was altered" in capsys.readouterr().err


def test_a_failed_alter_fails_the_task_after_trying_the_rest() -> None:
    spark = FakeSpark(TABLES, ROUTINES)
    seen: list[str] = []

    def execute(statement: str) -> list[dict[str, Any]]:
        seen.append(statement)
        if statement.startswith("ALTER TABLE"):
            raise RuntimeError("PERMISSION_DENIED")
        return spark.execute(statement)

    assert owner.converge(execute, catalog="mip", owner_group=GROUP) == 3
    assert len([s for s in seen if s.startswith("ALTER ")]) == 5


def test_identifiers_are_quoted_so_a_name_cannot_close_the_statement() -> None:
    spark = FakeSpark([_table("gold", "x` OWNER TO `attacker", "MANAGED", JOBS_SP)], [])
    owner.converge(spark.execute, catalog="mip", owner_group=GROUP)
    assert spark.alters == ["ALTER TABLE `mip`.`gold`.`x`` OWNER TO ``attacker` OWNER TO `mip-data-owners`"]


def test_an_overridden_catalog_refuses_before_any_sql(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = tmp_path / "files" / "jobs"
    folder.mkdir(parents=True)
    shutil.copy(ROOT / "jobs" / "_install_pins.py", folder / "_install_pins.py")
    install_pins.render(
        tmp_path / "files",
        install_pins.document(
            catalog="mip", lakebase_instance="mip-app-state", lakebase_database="mip_app_state",
            data_owner_group=GROUP,
        ),
    )
    monkeypatch.setattr(owner, "__file__", str(folder / "converge_governed_owner.py"))
    sessions: list[str] = []
    monkeypatch.setattr(owner, "_get_spark", lambda: sessions.append("spark"))

    with pytest.raises(SystemExit) as exited:
        owner.run(["--catalog=another_catalog"])
    assert exited.value.code == 5 and sessions == []

    reached: list[tuple[Any, str]] = []
    monkeypatch.setattr(owner, "main", lambda argv, *, owner_group: reached.append((argv, owner_group)) or 0)
    assert owner.run(["--catalog=mip"]) == 0
    assert reached == [(["--catalog=mip"], GROUP)], "the group comes from the pins, never a parameter"


def test_the_governed_schemas_are_render_sqls() -> None:
    assert set(owner.GOVERNED_SCHEMAS) == set(render_sql._UC_SCHEMAS)


def test_the_task_ends_exactly_the_jobs_that_create_or_replace_objects() -> None:
    with_task = {
        key for key, job in JOBS.items() if any(t["task_key"] == "converge_governed_owner" for t in job["tasks"])
    }
    assert with_task == {"mip_refresh_silver", "mip_refresh_scores", "mip_fred_rates_ingest"}
    for key in with_task:
        job = JOBS[key]
        assert job["run_as"] == {"service_principal_name": "${var.jobs_principal}"}, key
        task = next(t for t in job["tasks"] if t["task_key"] == "converge_governed_owner")
        others = [t["task_key"] for t in job["tasks"] if t is not task]
        depended = {d["task_key"] for t in job["tasks"] if t is not task for d in t.get("depends_on", [])}
        leaves = {k for k in others if k not in depended}
        assert {d["task_key"] for d in task["depends_on"]} == leaves, f"{key}: the last task"
        assert task["run_if"] == "ALL_DONE", key
        assert task["spark_python_task"] == {
            "python_file": "jobs/converge_governed_owner.py",
            "parameters": ["--catalog=${var.uc_catalog}"],
        }
        assert task["environment_key"] in {env["environment_key"] for env in job["environments"]}
