"""The owner-start re-pin's one digest at every site, its resume, ``status --recovery`` and its codes (wave 7c).

Split from tests/unit/test_quiesce_window_owner_start.py (the file-size rule),
whose estate and helpers these tests share: a dead run's window, then the
owner's raw start. The plan, ``status --recovery`` and the re-pin under the
lease compute one ``owner_start_repin`` digest; the plan command reads the ack
from its environment and only before the lease; a re-pin signed before a
failed journal close resumes with the same ack; and every refusal code is a
closed, reviewed set whose public output is codes and the digest only.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ID,
    DEPLOYER_CALLER,
    OWNER_CALLER,
    RELEASE,
    stored_record,
)
from tests.unit.test_app_deployment_deadman_repin import _dead_runner
from tests.unit.test_quiesce_window_owner_start import (
    NEXT_LEASE,
    _classify,
    _down,
    _facts,
    _frozen,
    _open_then_die,
    _refuses,
    _repin,
)
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    MIGRATION_NOOP,
    SCOPE,
    _env,
    _estate,
)
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_journal as journal
from tools.databricks import quiesce_window_recovery as recovery
from tools.databricks.app_deployment_owner_start_repin import REFUSAL_CODES, OwnerStartRepinRefused
from tools.databricks.quiesce_window_plan import OWNER_START_REFUSALS, window_plan

# --- one digest at every site, and the resume -------------------------------------------------


def test_the_plan_the_status_and_the_re_pin_under_the_lease_compute_one_digest(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = _estate()
    started = _open_then_die(estate)
    facts = _facts(estate)
    plan = window_plan(facts, position="pre-lease", acknowledgement="")
    assert (facts.owner_start_state, facts.owner_start_creator) == ("candidate", "other")
    assert plan.public_summary()["owner_start_sha256"] == facts.owner_start_sha256

    out = tmp_path / "status.env"
    argv = ["status", "--recovery", "--app-name", APP_NAME, "--out-env", str(out)]
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    printed = capsys.readouterr().out
    assert "MIP_QUIESCE_WINDOW_RECOVERY=owner_start\n" in out.read_text(encoding="utf-8")
    assert f"recovery owner_start (owner start candidate, creator other, sha256 {facts.owner_start_sha256})" in printed
    assert started not in printed and RELEASE not in printed, "codes and the digest only"

    under_lease = _refuses(estate, "owner_start_unacked", ack="")
    assert facts.owner_start_sha256 in str(under_lease)
    assert _classify(estate).sha256 == facts.owner_start_sha256


def test_the_plan_command_reads_the_ack_from_its_environment_and_only_before_the_lease(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI the deploy shell runs: the plan phase prints the digest, the release carries it."""

    estate = _estate()
    started = _open_then_die(estate)
    digest = _classify(estate).sha256
    files = {
        "--plan-gate-report": {"verdict": "passed", "instance_update_sha256": ""},
        "--binding-payload": {"name": APP_NAME, "description": "", "resources": estate.apps.resources},
        "--migration-plan": MIGRATION_NOOP,
    }
    argv = ["plan", "--app-name", APP_NAME, "--scope", SCOPE, "--lakebase-instance", "mip-app-state"]
    for flag in ("--role-check", "--verifier-role-check", "--jobs-reader-role-check"):
        argv += [flag, "exact"]
    for index, (flag, payload) in enumerate(files.items()):
        path = tmp_path / f"input-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        argv += [flag, str(path)]

    def plan(position: str) -> tuple[int, str, dict[str, Any]]:
        out = tmp_path / f"{position}.json"
        code = window.main([*argv, "--position", position, "--out", str(out)], workspace_factory=lambda: estate.workspace)
        printed = capsys.readouterr()
        assert started not in printed.out + printed.err and RELEASE not in printed.out + printed.err
        return code, printed.out + printed.err, json.loads(out.read_text(encoding="utf-8"))

    before = _frozen(estate)
    monkeypatch.delenv(window.ACK_ENV, raising=False)
    monkeypatch.delenv(recovery.ACK_ENV, raising=False)
    code, printed, written = plan("pre-lease")
    assert code == 2 and written["refusals"] == ["window_journal_open"]
    assert f"re-pin it on a release with operation_acks owner_start_repin={digest}" in printed
    assert "REFUSED window_journal_open (remedy in the private plan)" in printed
    assert "owner_start_repin digest this plan prints (repin --from-owner-start;" in written["remedies"]["window_journal_open"]

    monkeypatch.setenv(recovery.ACK_ENV, "0" * 64)
    assert plan("pre-lease")[0] == 2, "another plan's digest"
    monkeypatch.setenv(window.ACK_ENV, digest)  # the window's own ack is another purpose
    monkeypatch.delenv(recovery.ACK_ENV)
    assert plan("pre-lease")[0] == 2
    monkeypatch.delenv(window.ACK_ENV)

    monkeypatch.setenv(recovery.ACK_ENV, f" {digest}\n")
    code, printed, written = plan("pre-lease")
    assert code == 0 and written["refusals"] == [] and written["code"] == "none"
    assert "this run re-pins it under the lease (repin --from-owner-start" in printed
    # Under the lease the recovery ran first: a journal still open at pre-apply is refused, acked or not.
    code, _printed, written = plan("pre-apply")
    assert code == 2 and written["refusals"] == ["window_journal_open"]
    assert _frozen(estate) == before, "a plan reads only"


def test_a_re_pin_signed_before_a_failed_journal_close_resumes_with_the_same_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    estate = _estate()
    started = _open_then_die(estate)
    digest = _classify(estate).sha256
    real_write = journal.write

    def lease_lost(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the lease was lost before the journal close")

    monkeypatch.setattr(journal, "write", lease_lost)
    with pytest.raises(RuntimeError, match="lease was lost"):
        _repin(estate, digest)
    signed = estate.secrets.values.copy()
    assert stored_record(estate)["deployment_id"] == started, "the record was re-pinned"
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open"

    monkeypatch.setattr(journal, "write", real_write)
    assert _classify(estate).sha256 == digest, "the record is not in the plan: the same ack still names it"
    record = _repin(estate, digest)
    assert record["deployment_id"] == started and record["predecessor_deployment_id"] == BLUE_ID
    assert estate.secrets.values == signed, "never signed a second time"
    assert journal.read(estate.workspace, app_name=APP_NAME)["closed_by"] == "owner_start_repin"


def test_a_recovering_run_that_also_needs_a_window_keeps_its_pre_lease_window_ack() -> None:
    # The predecessor rule, as for the dead-man's re-pin: the pre-lease window digest names
    # the old blue; the owner-start re-pin moves blue to the owner's start (its predecessor
    # the old blue), so the pre-apply recheck matches that ack through the re-pin alone.
    from tests.unit.test_quiesce_window_start_repin import MIGRATION_APPLY

    estate = _estate()
    started = _open_then_die(estate)
    owner_ack = _classify(estate).sha256

    def plan(position: str, ack: str, owner: str = owner_ack) -> Any:
        facts = window.collect_facts(
            estate.workspace,
            app_name=APP_NAME,
            scope=SCOPE,
            lakebase_instance="mip-app-state",
            gate_report={"verdict": "passed", "instance_update_sha256": ""},
            binding_payload={"name": APP_NAME, "description": "", "resources": estate.apps.resources},
            role_state="exact",
            verifier_role_state="exact",
            jobs_reader_role_state="exact",
            migration_plan=MIGRATION_APPLY,
            rebase_unverified_app=False,
        )
        return window_plan(facts, position=position, acknowledgement=ack, owner_start_acknowledgement=owner)

    unacked = plan("pre-lease", "", owner="")
    assert set(unacked.refusals) == {"window_journal_open", "window_unacked"}
    digest = unacked.sha256
    lease = plan("pre-lease", digest)
    assert lease.code == "window" and lease.public_summary()["owner_start_recovery"] is True
    assert lease.canonical["blue_deployment_id"] == BLUE_ID
    _repin(estate, owner_ack)
    recheck = plan("pre-apply", digest)
    assert recheck.code == "window" and recheck.ack == "matched", recheck.refusals
    assert recheck.canonical["blue_deployment_id"] == started and recheck.sha256 != digest
    assert recheck.owner_start_ack == "not_required", "the journal is closed: nothing is left to acknowledge"


def test_status_without_the_flag_is_unchanged_and_the_flag_needs_the_verified_env_file(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = _estate()
    _open_then_die(estate)
    out = tmp_path / "status.env"
    reads = estate.apps.get_calls
    assert window.main(["status", "--app-name", APP_NAME, "--out-env", str(out)], workspace_factory=lambda: estate.workspace) == 0
    plain = capsys.readouterr().out
    assert "MIP_QUIESCE_WINDOW_RECOVERY" not in out.read_text(encoding="utf-8") and "recovery" not in plain
    assert estate.apps.get_calls == reads, "the EXIT trap's status reads the journal and the marker only"
    for argv in (["status", "--recovery", "--app-name", APP_NAME], ["status", "--recovery", "--unverified", "--app-name", APP_NAME]):
        with pytest.raises(SystemExit, match="status --recovery is the verified read, with --out-env"):
            window.main(argv, workspace_factory=lambda: estate.workspace)


@pytest.mark.parametrize("closed", (False, True), ids=("no-journal", "closed-journal"))
def test_status_recovery_reads_nothing_of_the_app_when_no_window_is_open(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], closed: bool
) -> None:
    estate = _estate()
    if closed:
        _open_then_die(estate)
        _repin(estate, _classify(estate).sha256)
    reads = (estate.apps.get_calls, estate.apps.starts, estate.apps.stops)
    out = tmp_path / "status.env"
    argv = ["status", "--recovery", "--app-name", APP_NAME, "--out-env", str(out)]
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    assert "MIP_QUIESCE_WINDOW_RECOVERY=none\n" in out.read_text(encoding="utf-8")
    assert "recovery none (owner start absent, creator unknown)" in capsys.readouterr().out
    assert (estate.apps.get_calls, estate.apps.starts, estate.apps.stops) == reads


def test_status_recovery_refines_a_dead_man_restart_the_app_no_longer_serves(tmp_path: Any) -> None:
    estate = _estate()
    _dead_runner(estate)
    out = tmp_path / "status.env"
    argv = ["status", "--recovery", "--app-name", APP_NAME, "--out-env", str(out)]
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    served = out.read_text(encoding="utf-8")
    assert "MIP_QUIESCE_WINDOW_RECOVERY=deadman\n" in served and "MIP_QUIESCE_WINDOW_DEADMAN_STATE=deadman_restarted\n" in served
    _down(estate)
    assert window.main(argv, workspace_factory=lambda: estate.workspace) == 0
    superseded = out.read_text(encoding="utf-8")
    assert "MIP_QUIESCE_WINDOW_RECOVERY=none\n" in superseded
    assert "MIP_QUIESCE_WINDOW_DEADMAN_STATE=deadman_superseded\n" in superseded, "never the dead-man arm again"
    # Without the flag the EXIT trap still reads the marker's own state.
    assert window.main(["status", "--app-name", APP_NAME, "--out-env", str(out)], workspace_factory=lambda: estate.workspace) == 0
    assert "MIP_QUIESCE_WINDOW_DEADMAN_STATE=deadman_restarted\n" in out.read_text(encoding="utf-8")


# --- the codes are a closed, reviewed set ----------------------------------------------------


def test_every_refusal_code_is_reviewed_and_public_output_is_codes_only() -> None:
    assert recovery.REFUSALS == OWNER_START_REFUSALS == (
        "owner_start_contract_window",
        "owner_start_marker_unreadable",
        "owner_start_no_baseline",
        "owner_start_not_running",
        "owner_start_none",
        "owner_start_ambiguous",
        "owner_start_not_current",
        "owner_start_not_a_start",
    )
    # What the marker may read is exactly what the dead-man job may write, plus the three
    # reads of a missing, foreign or broken marker: a state added to the job is reviewed here.
    from pathlib import Path

    from tests.fixtures.quiesce_window_estate import load_deadman

    job = load_deadman((Path(__file__).resolve().parents[2] / "jobs" / "window_deadman.py").read_bytes(), "deadman_states")
    assert job["MARKER_STATES"] | {"absent", "mismatch", "unreadable"} == recovery.MARKER_READS
    assert recovery.KNOWN_START_MARKERS - {recovery.SUPERSEDED} < job["MARKER_STATES"]
    under_the_lease = {"owner_start_usage", "owner_start_no_window", "owner_start_deadman", "owner_start_unacked", "owner_start_unproven"}
    assert {*OWNER_START_REFUSALS, *under_the_lease} == REFUSAL_CODES
    with pytest.raises(ValueError):
        OwnerStartRepinRefused("owner_start_whatever", "an unreviewed code")
    with pytest.raises(ValueError):
        recovery.Recovery(kind="owner_start")  # a candidate always carries its code
    with pytest.raises(ValueError):
        recovery.Recovery(deadman_state="deadman_restarted by owner-caller")
    estate = _estate()
    started = _open_then_die(estate)
    public = json.dumps(_classify(estate).public(), sort_keys=True)
    for private in (started, BLUE_ID, RELEASE, OWNER_CALLER, DEPLOYER_CALLER, NEXT_LEASE):
        assert private not in public, private


def test_the_two_new_modules_call_no_apps_start_stop_deploy_or_update() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "tools" / "databricks"
    for name in ("quiesce_window_recovery.py", "app_deployment_owner_start_repin.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert not called & {"start", "stop", "deploy", "deploy_and_wait", "update", "create_update", "delete", "upload"}, name
        assert {"get", "list_deployments", "get_deployment"} & called or name != "quiesce_window_recovery.py"
