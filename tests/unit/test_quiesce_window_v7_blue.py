"""The quiesce window plan over a v7 core blue: no reason passes; a reason plans a window (M2-PR7b, S7).

Spike S7 is recorded, so a v7 core blue that needs a window is planned like a
v5/v6 one (its start-and-re-pin: tests/unit/test_quiesce_window_v7_start.py),
never refused as ``window_over_v7_blue``. A v7 agentic record stays refused.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld, adopted_core_world
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import quiesce_window
from tools.databricks import quiesce_window_facts as facts
from tools.databricks.quiesce_window_plan import window_plan

NOOP = {
    "mode": "plan",
    "decision": "noop",
    "blue_restorable": True,
    "migrations": {"pending": []},
    "jobs_reader_grants": "exact",
}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    monkeypatch.setattr(facts.journal, "read", lambda *_a, **_kw: None)
    monkeypatch.setattr(facts, "app_or_none", lambda workspace, *, app_name: workspace.apps.get(app_name))


@pytest.fixture
def world(tmp_path: Path) -> CoreWorld:
    return adopted_core_world(tmp_path)


def _facts(world: CoreWorld, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "app_name": rr.APP_NAME,
        "scope": rr.SCOPE,
        "lakebase_instance": rr.LAKEBASE,
        "gate_report": {"verdict": "passed", "instance_update_sha256": ""},
        "binding_payload": {"name": rr.APP_NAME, "resources": copy.deepcopy(rr.APP_RESOURCES)},
        "role_state": "exact",
        "verifier_role_state": "absent",
        "jobs_reader_role_state": "exact",
        "migration_plan": NOOP,
        "rebase_unverified_app": False,
        **overrides,
    }
    return facts.collect_facts(world, **values)


def test_a_core_blue_with_no_reason_plans_no_window_from_the_records_own_env_names(world: CoreWorld) -> None:
    record = world.record()
    assert record is not None

    found = _facts(world)

    assert found.applicable and found.reasons() == ()
    assert found.blue_deployment_id == "deployment-blue"
    assert found.env_names_sha256 == record["env_names_sha256"]
    assert window_plan(found, position="pre-lease", acknowledgement="").code == "none"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"role_state": "drift"}, "WR2"),
        ({"migration_plan": {**NOOP, "decision": "apply", "migrations": {"pending": ["0042"]}}}, "WR3"),
        ({"gate_report": {"verdict": "passed", "instance_update_sha256": "f" * 64}}, "WR4"),
    ],
)
def test_a_core_blue_that_needs_a_window_plans_one_against_the_records_tree(
    world: CoreWorld, override: dict[str, Any], reason: str
) -> None:
    record = world.record()
    assert record is not None
    found = _facts(world, **override)
    assert found.reasons() == (reason,) and found.blue_current
    assert found.artifact_tree_sha256 == record["source_tree_sha256"] == found.source_tree_sha256
    first = window_plan(found, position="pre-apply", acknowledgement="")
    plan = window_plan(found, position="pre-apply", acknowledgement=first.sha256)
    assert plan.code == "window" and plan.refusals == () and plan.tier == "a"
    assert world.apps.mutations == [], "planning is read-only"


def test_the_window_plan_command_asks_for_the_exact_ack_never_refuses_a_v7_blue(
    world: CoreWorld, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report, migration = tmp_path / "gate.json", tmp_path / "migration.json"
    report.write_text('{"verdict": "passed", "instance_update_sha256": "' + "f" * 64 + '"}', encoding="utf-8")
    migration.write_text(json.dumps(NOOP), encoding="utf-8")
    argv = [
        "plan", "--app-name", rr.APP_NAME, "--scope", rr.SCOPE, "--lakebase-instance", rr.LAKEBASE,
        "--plan-gate-report", str(report), "--rebase-unverified-app", "0", "--deadline-epoch", "",
        "--out", str(tmp_path / "plan.json"), "--position", "pre-lease", "--role-check", "exact",
        "--verifier-role-check", "absent", "--jobs-reader-role-check", "exact",
        "--migration-plan", str(migration),
    ]  # fmt: skip

    assert quiesce_window.main(argv, workspace_factory=lambda: world) == 2
    err = capsys.readouterr().err
    assert "REFUSED window_unacked" in err and "window_over_v7_blue" not in err


def test_an_agentic_v7_record_is_still_refused_and_assert_serving_reads_a_core_blue(world: CoreWorld) -> None:
    quiesce_window.assert_serving(world, app_name=rr.APP_NAME, scope=rr.SCOPE, lakebase_instance=rr.LAKEBASE)
    contract.save_versioned_record(world, scope=rr.SCOPE, record=rr.v7_agentic_record())
    with pytest.raises(RuntimeError, match=contract.V7_REFUSAL):
        _facts(world)
