"""A window opens only within the job budget (M0-PR9, threat T5).

The admission compares ``MIP_JOB_DEADLINE_EPOCH - now`` with the predicted
window plus the predicted restore plus 15 minutes. Those three inputs are in
the window's canonical body, so the exact digest ack covers them, while a plan
with no reason keeps M0-PR8's body and digest exactly. Fail closed: a window
with a missing, unparseable, past or implausibly far deadline refuses, before
the lease in the plan and again in the open, with no stop and no submit.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.quiesce_window_estate import APP_NAME, window_hooks
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    GIT_SHA,
    LEASE_ID,
    _env,
    _estate,
    _plan,
    _stop,
)
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_budget as budget
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.quiesce_window_plan import (
    ADMISSION_MARGIN_S,
    CANONICAL_SCHEMA,
    STEP_BUDGET_S,
    WindowFacts,
    body_sha256,
    budget_body,
    predicted_restore_seconds,
    predicted_seconds,
    window_plan,
)

NOW = 1_800_000_000
TREE = "a" * 64
BLUE = WindowFacts(
    applicable=True,
    blue_deployment_id="deployment-blue-PLACEHOLDER",
    blue_current=True,
    restore_source="/Workspace/Users/deployer-PLACEHOLDER/app-src/release",
    default_source_code_path="/Workspace/Users/deployer-PLACEHOLDER/app-src/release",
    env_names_sha256="b" * 64,
    source_tree_sha256=TREE,
    artifact_tree_sha256=TREE,
)
WR3 = replace(BLUE, migration_decision="apply", migration_pending=("0042",))
# M0-PR8's canonical body, key for key: a no-window plan's digest is unchanged.
M0_PR8_KEYS = {
    "schema",
    "reasons",
    "blue_deployment_id",
    "restore_source",
    "source_tree_sha256",
    "env_names_sha256",
    "blue_restorable",
    "binding_state",
    "binding_lines",
    "role_state",
    "verifier_role_state",
    "jobs_reader_role_state",
    "jobs_reader_grants",
    "migration_pending",
    "migration_pending_contract",
    "instance_update_sha256",
}


def _required(blue_restorable: bool = True) -> int:
    window_s = predicted_seconds(("WR3",), blue_restorable=blue_restorable)
    return window_s + predicted_restore_seconds(blue_restorable=blue_restorable) + ADMISSION_MARGIN_S


# --- the budget inputs and the digest ---------------------------------------------------


def test_the_predicted_restore_is_the_exit_traps_start_and_re_pin_and_nothing_for_a_contract() -> None:
    assert predicted_restore_seconds(blue_restorable=True) == STEP_BUDGET_S["start"] + STEP_BUDGET_S["repin"]
    assert predicted_restore_seconds(blue_restorable=False) == 0
    assert ADMISSION_MARGIN_S == 15 * 60


def test_a_window_body_names_its_budget_and_the_digest_covers_it() -> None:
    plan = window_plan(WR3, position="pre-lease", acknowledgement="")
    body = plan.canonical
    assert body["budget"] == budget_body(("WR3",), blue_restorable=True)
    assert body["budget"]["required_seconds"] == _required()
    without = {key: value for key, value in body.items() if key != "budget"}
    assert body_sha256(without) != plan.sha256, "the ack covers the budget inputs"
    contract = window_plan(
        replace(WR3, migration_blue_restorable=False), position="pre-lease", acknowledgement=""
    )
    assert contract.canonical["budget"]["required_seconds"] == _required(blue_restorable=False)


def test_a_no_window_plan_keeps_m0_pr8s_body_and_digest() -> None:
    plan = window_plan(BLUE, position="pre-apply", acknowledgement="")
    assert plan.code == "none" and set(plan.canonical) == M0_PR8_KEYS
    assert plan.canonical["schema"] == CANONICAL_SCHEMA and "budget" not in plan.canonical
    assert plan.sha256 == body_sha256(plan.canonical)


# --- the admission ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deadline", "state"),
    (
        ("", "deadline_missing"),
        ("   ", "deadline_missing"),
        ("tomorrow", "deadline_unparseable"),
        ("1800000000.5", "deadline_unparseable"),
        ("-1800003600", "deadline_unparseable"),
        ("1e9", "deadline_unparseable"),
        (str(NOW), "deadline_past"),
        (str(NOW - 60), "deadline_past"),
        (str(NOW + budget.MAX_HORIZON_S + 1), "deadline_too_far"),
        (str(NOW + _required() - 1), "insufficient"),
        (str(NOW + _required()), "admitted"),
        (str(NOW + budget.MAX_HORIZON_S), "admitted"),
    ),
)
def test_the_admission_fails_closed_on_every_unusable_deadline(deadline: str, state: str) -> None:
    decided = budget.admission(budget_body(("WR3",), blue_restorable=True), deadline, now=NOW)
    assert decided.state == state
    assert decided.admitted is (state == "admitted")


def test_a_run_that_needs_no_window_needs_no_deadline() -> None:
    decided = budget.admission(None, "", now=NOW)
    assert decided.state == "not_required" and decided.admitted
    plan = window_plan(BLUE, position="pre-lease", acknowledgement="")
    assert window.with_admission(plan, decided.public(), admitted=True).code == "none"


@pytest.mark.parametrize("deadline", ("", str(NOW + 600)))
def test_a_window_the_budget_does_not_admit_refuses_in_the_plan(deadline: str) -> None:
    unacked = window_plan(WR3, position="pre-lease", acknowledgement="")
    acked = window_plan(WR3, position="pre-lease", acknowledgement=unacked.sha256)
    decided = budget.admission(acked.canonical["budget"], deadline, now=NOW)
    refused = window.with_admission(acked, decided.public(), admitted=decided.admitted)
    assert refused.code == "refused" and refused.refusals == ("window_budget_refused",)
    assert refused.public_summary()["admission"]["state"] == decided.state
    assert refused.sha256 == acked.sha256, "the admission is never in the digest"
    admitted = budget.admission(acked.canonical["budget"], str(NOW + 3 * 3600), now=NOW)
    assert window.with_admission(acked, admitted.public(), admitted=True).code == "window"


def test_the_open_recomputes_the_budget_from_the_reasons() -> None:
    plan = window_plan(WR3, position="pre-apply", acknowledgement="")
    lowered = {**plan.canonical, "budget": {**plan.canonical["budget"], "required_seconds": 901}}
    with pytest.raises(RuntimeError, match="not the reviewed budget"):
        budget.require_admitted(lowered, str(NOW + 3 * 3600), now=NOW)
    with pytest.raises(RuntimeError, match="carries no budget"):
        budget.require_admitted(
            {key: value for key, value in plan.canonical.items() if key != "budget"},
            str(NOW + 3 * 3600),
            now=NOW,
        )
    with pytest.raises(budget.WindowAdmissionError, match="deadline_missing"):
        budget.require_admitted(plan.canonical, "", now=NOW)
    assert budget.require_admitted(plan.canonical, str(NOW + 3 * 3600), now=NOW).state == "admitted"


@pytest.mark.parametrize("budget_s", (-60.0, 600.0, float(_required() - 30)))
def test_an_insufficient_budget_refuses_the_open_with_no_stop_and_no_journal(budget_s: float) -> None:
    estate = _estate()
    plan = _plan(estate)
    with pytest.raises((budget.WindowAdmissionError,), match="window_budget_refused"):
        window.open_window(
            estate.workspace,
            app_name=APP_NAME,
            plan=plan.full(),
            acknowledgement=plan.sha256,
            lease_id=LEASE_ID,
            source_git_sha=GIT_SHA,
            assert_lease_held=lambda: None,
            stop=_stop(estate),
            **window_hooks(estate, budget_s=budget_s),
        )
    assert estate.apps.stops == 0 and journal.read(estate.workspace, app_name=APP_NAME) is None


# --- the CLI ----------------------------------------------------------------------------


def _plan_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, facts: WindowFacts, deadline: str, ack: str = ""
) -> tuple[int, dict[str, Any]]:
    monkeypatch.setattr(window, "collect_facts", lambda *_a, **_k: facts)
    monkeypatch.setenv(window.ACK_ENV, ack)
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"verdict": "passed"}), encoding="utf-8")
    out = tmp_path / "plan.json"
    argv = ["plan", "--app-name", APP_NAME, "--position", "pre-lease", "--plan-gate-report", str(gate)]
    rc = window.main([*argv, "--deadline-epoch", deadline, "--out", str(out)], workspace_factory=lambda: None)
    return rc, json.loads(out.read_text(encoding="utf-8"))


def test_the_plan_cli_refuses_an_unbudgeted_window_before_the_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ack = window_plan(WR3, position="pre-lease", acknowledgement="").sha256
    rc, plan = _plan_cli(tmp_path, monkeypatch, WR3, "", ack)
    assert rc == 2 and plan["refusals"] == ["window_budget_refused"]
    assert "admission deadline_missing" in capsys.readouterr().out
    rc, plan = _plan_cli(tmp_path, monkeypatch, WR3, str(int(time.time()) + 3 * 3600), ack)
    assert rc == 0 and plan["code"] == "window" and plan["admission"]["state"] == "admitted"
    rc, plan = _plan_cli(tmp_path, monkeypatch, BLUE, "")
    assert rc == 0 and plan["code"] == "none" and plan["admission"]["state"] == "not_required"


def test_the_dry_run_line_describes_the_admission_without_a_call(capsys: pytest.CaptureFixture[str]) -> None:
    assert budget.main(["describe", "--deadline-epoch", ""]) == 0
    assert f"{budget.DEADLINE_ENV} unset: a window would refuse" in capsys.readouterr().out
    assert budget.main(["describe", "--deadline-epoch", str(int(time.time()) + 3600)]) == 0
    line = capsys.readouterr().out
    assert f"{budget.DEADLINE_ENV} leaves" in line and f"and {ADMISSION_MARGIN_S} s" in line
