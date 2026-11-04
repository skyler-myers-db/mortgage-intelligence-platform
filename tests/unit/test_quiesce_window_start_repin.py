"""The window closes on the deployment ``apps start`` creates, proven and re-pinned (M0-PR8).

The fake Apps API takes spike S7's worst case: a start creates a NEW
deployment id from the App's default source. With the same source, bytes and
env names the window closes and the signed record names the new id (with its
predecessor); a changed source path, snapshot digest or env-name set refuses,
keeps the App stopped and leaves the journal open for the dead-man. The
rollback's ``ensure`` and ``restore`` start paths re-pin the same way (tier a)
and ``restore`` falls back to the tier b redeploy when the start is not
provably blue. Positive controls: a release with no window stops nothing and
deploys once; a WR3 window stops once and re-pins once.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from backend.services.ai_gateway_proof_attestation import derive_gateway_proof_verify_key
from tests.fixtures.quiesce_window_estate import (
    APP_NAME,
    BLUE_ARTIFACT,
    BLUE_ID,
    OTHER_SOURCE,
    RELEASE,
    SOURCE_FILES,
    Estate,
    StartBehaviour,
    deployment,
    make_estate,
    stored_record,
)
from tests.unit.test_app_deployment_rollback import (
    APP_RESOURCES,
    GIT_SHA,
    LEASE_ID,
    RESOURCE_CONTRACT,
    RESOURCE_DIGEST,
    SIGNING_KEY,
    _binding,
    _immutable_payload,
)
from tools.databricks import app_deployment_rollback as rollback
from tools.databricks import app_rollback_record_contract as record_contract
from tools.databricks import app_start_deployment as starts
from tools.databricks import quiesce_window as window
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_rollback_record_builder import build_app_rollback_record
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract as real_contract,
)
from tools.databricks.app_rollback_resource_contract import (
    restore_signed_app_resource_contract as real_restore,
)
from tools.databricks.app_rollback_resource_contract import validated_app_resource_contract
from tools.databricks.quiesce_window_plan import window_plan
from tools.databricks.stop_app_fail_closed import stop_app_fail_closed

SCOPE = "mip"
ENV_NAMES = [str(item["name"]) for item in _immutable_payload()["env_vars"]]
MIGRATION_APPLY = {
    "mode": "plan",
    "decision": "apply",
    "blue_restorable": True,
    "migrations": {"pending": ["0042"], "pending_contract": []},
    "jobs_reader_grants": "exact",
}
MIGRATION_NOOP = {**MIGRATION_APPLY, "decision": "noop", "migrations": {"pending": []}}
EQUAL_BINDINGS = {"name": APP_NAME, "description": "", "resources": APP_RESOURCES}
# WR1: the bundle adds the read-only scheduler binding to blue's signed set.
WR1_BINDINGS = {
    **EQUAL_BINDINGS,
    "resources": [
        *APP_RESOURCES,
        {
            "name": "growth_agent_scheduler_job",
            "job": {"id": "scheduler-job", "permission": "CAN_VIEW"},
        },
    ],
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", derive_gateway_proof_verify_key(SIGNING_KEY))
    monkeypatch.setenv("MIP_LAKEBASE_INSTANCE", "mip-app-state")
    monkeypatch.setenv("LAKEBASE_INSTANCE_NAME", "mip-app-state")
    monkeypatch.setattr(record_contract, "assert_owned_app_rollback_scope", lambda *_a, **_k: None)
    monkeypatch.setattr(rollback, "held_assertion", lambda *_a, **_k: lambda: None)
    monkeypatch.setattr(rollback, "_health", lambda *_a, **_k: (GIT_SHA, _binding(), LEASE_ID))
    monkeypatch.setattr(rollback, "_stored_resource_proof", lambda *_a, **_k: None)
    monkeypatch.setattr(rollback, "converge_campaign_treatment_access", lambda **_k: True)
    monkeypatch.setattr(rollback, "preserve_blue_and_revoke_managed_candidates", lambda *_a, **_k: "managed")
    monkeypatch.setattr(rollback, "restore_signed_app_resource_contract", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rollback,
        "app_resource_contract",
        lambda *_a, **_k: validated_app_resource_contract(APP_RESOURCES),
    )
    monkeypatch.setattr(starts, "time", SimpleNamespace(sleep=lambda _s: None, monotonic=time.monotonic))


def _signed_blue(estate: Estate) -> dict[str, Any]:
    payload = _immutable_payload()
    payload["source_code_path"] = BLUE_ARTIFACT
    record = build_app_rollback_record(
        app_name=APP_NAME,
        deployment=estate.apps.deployments[BLUE_ID],
        payload=payload,
        git_sha=GIT_SHA,
        gateway_binding=_binding(),
        gateway_resources={**RESOURCE_CONTRACT, "resource_digest": RESOURCE_DIGEST},
        app_resources=APP_RESOURCES,
        app_service_principal_client_id="app-client",
        app_service_principal_scim_id="app-scim-id",
        expected_lakebase_instance="mip-app-state",
    )
    record_contract._save_record(estate.workspace, scope=SCOPE, record=record)
    return record


def _estate(behaviour: StartBehaviour | None = None) -> Estate:
    estate = make_estate(ENV_NAMES, behaviour)
    estate.apps.resources = [dict(item) for item in APP_RESOURCES]
    _signed_blue(estate)
    return estate


def _stop(estate: Estate) -> Any:
    return lambda: stop_app_fail_closed(
        app_name=APP_NAME,
        workspace=estate.workspace,
        interval_s=0,
        expected_app_id="app-object-id",
        expected_client_id="app-client",
        expected_scim_id="app-scim-id",
    )


def _plan(
    estate: Estate,
    *,
    migration: dict[str, Any] = MIGRATION_APPLY,
    ack: str = "",
    bindings: dict[str, Any] = EQUAL_BINDINGS,
    jobs_reader_role_state: str | None = "exact",
) -> Any:
    facts = window.collect_facts(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        lakebase_instance="mip-app-state",
        gate_report={"verdict": "passed", "instance_update_sha256": ""},
        binding_payload=bindings,
        role_state="exact",
        verifier_role_state="exact",
        jobs_reader_role_state=jobs_reader_role_state,
        migration_plan=migration,
        rebase_unverified_app=False,
    )
    first = window_plan(facts, position="pre-apply", acknowledgement=ack)
    return first if ack or not first.reasons else window_plan(
        facts, position="pre-apply", acknowledgement=first.sha256
    )


def _repin(
    estate: Estate, deployment_id: str, tree: str | None = None, applied: Any = None
) -> dict[str, Any]:
    return rollback.repin_started_deployment(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        deployment_id=deployment_id,
        expected_rollback_deployment_id=BLUE_ID,
        base_url="https://mip.example",
        bearer_token="token",
        deployment_lease_id=LEASE_ID,
        deployment_source_git_sha=GIT_SHA,
        expected_tree_sha256=tree,
        applied_app_resources=applied,
    )


def _run_window(estate: Estate, plan: Any = None) -> tuple[str, dict[str, Any]]:
    plan = plan or _plan(estate)
    opened = window.open_window(
        estate.workspace,
        app_name=APP_NAME,
        plan=plan.full(),
        acknowledgement=plan.sha256,
        lease_id=LEASE_ID,
        source_git_sha=GIT_SHA,
        assert_lease_held=lambda: None,
        stop=_stop(estate),
    )
    window.start_window(
        estate.workspace, app_name=APP_NAME, assert_lease_held=lambda: None, stop=_stop(estate)
    )
    proven = window.await_window(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        lakebase_instance="mip-app-state",
        assert_lease_held=lambda: None,
        stop=_stop(estate),
    )
    return proven, opened


# --- the window -----------------------------------------------------------------------


def test_a_start_that_creates_a_new_deployment_closes_the_window_and_is_re_pinned() -> None:
    estate = _estate()
    proven, opened = _run_window(estate)
    record = _repin(estate, proven, opened["source_tree_sha256"])
    window.close_window(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        lakebase_instance="mip-app-state",
        repinned=proven,
        superseded_by_activation=False,
        assert_lease_held=lambda: None,
    )

    assert proven == "deployment-start-1" != BLUE_ID, "the start created a new deployment"
    assert record["deployment_id"] == proven and record["predecessor_deployment_id"] == BLUE_ID
    assert record["payload"]["source_code_path"] == f"/Workspace/Users/app-sp/src/{proven}"
    blue = stored_record(estate)
    assert blue["deployment_id"] == proven and blue["git_sha"] == GIT_SHA
    assert blue["gateway_resources"] == record["gateway_resources"], "same provenance"
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "closed"
    assert (estate.apps.stops, estate.apps.starts, estate.apps.deploys) == (1, 1, 0)
    assert estate.apps.active_id == proven and estate.apps.compute == "ACTIVE"


@pytest.mark.parametrize(
    ("behaviour", "code"),
    (
        (StartBehaviour(source=OTHER_SOURCE), "start_source_path"),
        (StartBehaviour(tamper_snapshot=True), "start_tree_digest"),
        (StartBehaviour(carry_env=False), "start_env_names"),
        (StartBehaviour(state="FAILED"), "start_not_succeeded"),
    ),
)
def test_a_start_that_is_not_provably_blue_refuses_and_keeps_the_app_stopped(
    behaviour: StartBehaviour, code: str
) -> None:
    estate = _estate(behaviour)
    if behaviour.source:
        estate.files.put_tree(OTHER_SOURCE, {**SOURCE_FILES, "extra.py": b"x\n"})

    with pytest.raises(RuntimeError, match=code):
        _run_window(estate)

    entry = journal.read(estate.workspace, app_name=APP_NAME)
    assert entry["state"] == "window_refused" and entry["refusal"] == code
    assert journal.state_of(entry) not in {"closed", "absent"}, "the dead-man owns the open window"
    assert estate.apps.compute == "STOPPED", "kept stopped after the failed proof"
    assert stored_record(estate)["deployment_id"] == BLUE_ID, "the record still names blue"
    with pytest.raises(starts.StartProofError, match=code):
        _repin(estate, f"deployment-start-{estate.apps.starts}")  # repin re-proves on its own


def test_a_changed_restore_folder_refuses_even_when_the_snapshot_matches() -> None:
    estate = _estate()
    plan = _plan(estate)
    window.open_window(
        estate.workspace,
        app_name=APP_NAME,
        plan=plan.full(),
        acknowledgement=plan.sha256,
        lease_id=LEASE_ID,
        source_git_sha=GIT_SHA,
        assert_lease_held=lambda: None,
        stop=_stop(estate),
    )
    window.start_window(
        estate.workspace, app_name=APP_NAME, assert_lease_held=lambda: None, stop=_stop(estate)
    )
    estate.files.put_tree(RELEASE, {"late.py": b"written in the window\n"})

    with pytest.raises(RuntimeError, match="start_folder_digest"):
        window.await_window(
            estate.workspace,
            app_name=APP_NAME,
            scope=SCOPE,
            lakebase_instance="mip-app-state",
            assert_lease_held=lambda: None,
            stop=_stop(estate),
        )
    assert estate.apps.compute == "STOPPED"


def _open(estate: Estate) -> dict[str, Any]:
    plan = _plan(estate)
    return window.open_window(
        estate.workspace,
        app_name=APP_NAME,
        plan=plan.full(),
        acknowledgement=plan.sha256,
        lease_id=LEASE_ID,
        source_git_sha=GIT_SHA,
        assert_lease_held=lambda: None,
        stop=_stop(estate),
    )


def _await(estate: Estate) -> str:
    return window.await_window(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        lakebase_instance="mip-app-state",
        assert_lease_held=lambda: None,
        stop=_stop(estate),
    )


def _start(estate: Estate) -> str:
    return window.start_window(
        estate.workspace, app_name=APP_NAME, assert_lease_held=lambda: None, stop=_stop(estate)
    )


def _restore_folder_gone(estate: Estate) -> None:
    for path in [path for path in estate.files.data if path.startswith(RELEASE.removeprefix("/Workspace"))]:
        del estate.files.data[path]


def _get_deployment_fails(estate: Estate) -> None:
    def unreadable(_name: str, _deployment_id: str) -> object:
        raise ConnectionError("the Apps API is unreachable")

    estate.apps.get_deployment = unreadable  # type: ignore[method-assign]


def _record_moved(estate: Estate) -> None:
    record = stored_record(estate)
    moved = build_app_rollback_record(
        app_name=APP_NAME,
        deployment=SimpleNamespace(
            **{**estate.apps.deployments[BLUE_ID].__dict__, "deployment_id": "deployment-other"}
        ),
        payload=record["payload"],
        git_sha=GIT_SHA,
        gateway_binding=_binding(),
        gateway_resources={**RESOURCE_CONTRACT, "resource_digest": RESOURCE_DIGEST},
        app_resources=APP_RESOURCES,
        app_service_principal_client_id="app-client",
        app_service_principal_scim_id="app-scim-id",
        expected_lakebase_instance="mip-app-state",
    )
    record_contract._save_record(estate.workspace, scope=SCOPE, record=moved)


@pytest.mark.parametrize(
    ("break_it", "code"),
    (
        (_restore_folder_gone, "start_folder_digest"),
        (_get_deployment_fails, "start_unreadable"),
        (_record_moved, "start_record_changed"),
    ),
    ids=("restore-folder-gone", "get-deployment-raises", "record-moved"),
)
def test_any_failure_in_the_proof_stops_the_started_app_and_journals_the_refusal(
    break_it: Any, code: str
) -> None:
    estate = _estate()
    _open(estate)
    _start(estate)
    break_it(estate)

    with pytest.raises(RuntimeError, match=code):
        _await(estate)
    entry = journal.read(estate.workspace, app_name=APP_NAME)
    assert entry["state"] == "window_refused" and entry["refusal"] == code
    assert estate.apps.compute == "STOPPED" and estate.apps.stops == 2, "stopped again"


def test_a_start_whose_pending_id_is_unreadable_is_stopped_and_never_started_twice() -> None:
    estate = _estate(StartBehaviour(readable_pending_id=False))
    _open(estate)

    with pytest.raises(RuntimeError, match="start_unreadable"):
        _start(estate)
    entry = journal.read(estate.workspace, app_name=APP_NAME)
    assert entry["state"] == "window_refused" and entry["refusal"] == "start_unreadable"
    assert (estate.apps.starts, estate.apps.stops, estate.apps.compute) == (1, 2, "STOPPED")
    with pytest.raises(RuntimeError, match="no restorable window"):
        _start(estate)
    assert estate.apps.starts == 1, "a refused window is the dead-man's, never started again"


def test_a_start_journaled_only_as_its_intent_resumes_from_the_pending_deployment() -> None:
    estate = _estate()
    opened = _open(estate)
    journal.write(
        estate.workspace,
        app_name=APP_NAME,
        expected=opened,
        changes={"state": "window_starting"},
        assert_lease_held=lambda: None,
    )
    estate.apps.start(APP_NAME)  # the start ran; the run died before journaling its id

    assert _start(estate) == "deployment-start-1"
    assert estate.apps.starts == 1, "resumed, never a second start"
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_started"
    assert _await(estate) == "deployment-start-1"


def test_an_intent_with_nothing_pending_refuses_without_starting() -> None:
    estate = _estate()
    opened = _open(estate)
    journal.write(
        estate.workspace,
        app_name=APP_NAME,
        expected=opened,
        changes={"state": "window_starting"},
        assert_lease_held=lambda: None,
    )

    with pytest.raises(RuntimeError, match="start_unreadable"):
        _start(estate)
    assert (estate.apps.starts, estate.apps.compute) == (0, "STOPPED")
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_refused"


def test_a_start_without_a_readable_pending_id_refuses_rather_than_guess() -> None:
    estate = _estate(StartBehaviour(readable_pending_id=False))
    with pytest.raises(starts.StartProofError, match="start_unreadable"):
        starts.start_app(estate.workspace, app_name=APP_NAME)


def test_the_wait_reads_only_the_start_deployment_never_the_active_one() -> None:
    estate = _estate()
    estate.apps.compute = "STOPPED"
    created = starts.start_app(estate.workspace, app_name=APP_NAME)
    reads = estate.apps.get_calls
    finished = starts.wait_for_terminal(estate.workspace, app_name=APP_NAME, deployment_id=created)

    assert starts.deployment_state(finished) == "SUCCEEDED"
    assert estate.apps.get_calls == reads, "no apps.get: 'active changed' is never the signal"


def test_open_refuses_a_plan_whose_ack_is_not_its_exact_digest() -> None:
    estate = _estate()
    plan = _plan(estate)
    with pytest.raises(RuntimeError, match="exact window digest"):
        window.open_window(
            estate.workspace,
            app_name=APP_NAME,
            plan=plan.full(),
            acknowledgement="0" * 64,
            lease_id=LEASE_ID,
            source_git_sha=GIT_SHA,
            assert_lease_held=lambda: None,
            stop=_stop(estate),
        )
    assert estate.apps.stops == 0 and journal.read(estate.workspace, app_name=APP_NAME) is None


def test_open_rechecks_that_blue_is_still_the_last_deployment() -> None:
    estate = _estate()
    plan = _plan(estate)
    estate.apps.default_source = OTHER_SOURCE  # moved after the plan (TOCTOU)
    with pytest.raises(RuntimeError, match="last deployment"):
        window.open_window(
            estate.workspace,
            app_name=APP_NAME,
            plan=plan.full(),
            acknowledgement=plan.sha256,
            lease_id=LEASE_ID,
            source_git_sha=GIT_SHA,
            assert_lease_held=lambda: None,
            stop=_stop(estate),
        )
    assert estate.apps.stops == 0


def _wr1_window(estate: Estate) -> str:
    """A WR1 window: open, apply the bundle's bindings with the App stopped, start, prove."""

    plan = _plan(estate, migration=MIGRATION_NOOP, bindings=WR1_BINDINGS)
    assert plan.reasons == ("WR1",)
    real_open = window.open_window

    def open_then_update(*args: Any, **kwargs: Any) -> dict[str, Any]:
        opened = real_open(*args, **kwargs)
        estate.apps.update(APP_NAME, SimpleNamespace(as_dict=lambda: WR1_BINDINGS))
        return opened

    window.open_window = open_then_update  # type: ignore[assignment]
    try:
        proven, _opened = _run_window(estate, plan)
    finally:
        window.open_window = real_open  # type: ignore[assignment]
    return proven


def test_a_wr1_re_pin_records_the_bindings_blue_restarted_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Blue restarts on the bindings the window applied and is proven healthy
    # on them, so the re-pinned record names them. Otherwise the next run's
    # step 0a would revert them on the serving App, outside any window.
    estate = _estate()
    proven = _wr1_window(estate)
    applied = validated_app_resource_contract(WR1_BINDINGS["resources"])
    record = _repin(estate, proven, applied=applied)
    assert record["app_resources"] == applied and estate.apps.updates == 1

    monkeypatch.setattr(rollback, "restore_signed_app_resource_contract", real_restore)
    monkeypatch.setattr(rollback, "app_resource_contract", real_contract)
    rollback.ensure_current(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        base_url="https://mip.example",
        bearer_token="token",
        deployment_lease_id=LEASE_ID,
        deployment_source_git_sha=GIT_SHA,
        treatment_warehouse_id="warehouse-id",
        treatment_catalog="mip",
    )
    assert estate.apps.updates == 1, "the next step 0a never reverts the serving App's bindings"
    assert estate.apps.resources == WR1_BINDINGS["resources"]


@pytest.mark.parametrize("applied", (None, APP_RESOURCES[:1]), ids=("none-given", "other-set"))
def test_a_re_pin_refuses_bindings_that_are_neither_signed_nor_applied(applied: Any) -> None:
    estate = _estate()
    proven = _wr1_window(estate)
    contract = validated_app_resource_contract(applied) if applied else None

    with pytest.raises(RuntimeError, match="neither the signed record's nor"):
        _repin(estate, proven, applied=contract)
    assert stored_record(estate)["deployment_id"] == BLUE_ID, "nothing was re-signed"


MIGRATION_CONTRACT = {
    **MIGRATION_APPLY,
    "blue_restorable": False,
    "migrations": {"pending": ["0043"], "pending_contract": ["0043"]},
}


def _close_by_activation(estate: Estate) -> None:
    window.close_window(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        lakebase_instance="mip-app-state",
        repinned="",
        superseded_by_activation=True,
        assert_lease_held=lambda: None,
    )


def test_a_contract_window_closes_only_once_green_is_the_signed_record_it_serves() -> None:
    estate = _estate()
    plan = _plan(estate, migration=MIGRATION_CONTRACT)
    assert plan.blue_restorable is False
    window.open_window(
        estate.workspace,
        app_name=APP_NAME,
        plan=plan.full(),
        acknowledgement=plan.sha256,
        lease_id=LEASE_ID,
        source_git_sha=GIT_SHA,
        assert_lease_held=lambda: None,
        stop=_stop(estate),
    )
    green = dict(_immutable_payload(git_sha="b" * 40), source_code_path=RELEASE)
    estate.apps.compute = "ACTIVE"
    estate.apps.deploy_and_wait(APP_NAME, SimpleNamespace(as_dict=lambda: green), timeout=None)
    with pytest.raises(RuntimeError, match="the contract window stays open"):
        _close_by_activation(estate)  # activated, but blue is still the signed record
    assert journal.read(estate.workspace, app_name=APP_NAME)["state"] == "window_open"

    captured = build_app_rollback_record(
        app_name=APP_NAME,
        deployment=estate.apps.deployments["deployment-redeploy-1"],
        payload=green,
        git_sha="b" * 40,
        gateway_binding=_binding(),
        gateway_resources={**RESOURCE_CONTRACT, "resource_digest": RESOURCE_DIGEST},
        app_resources=APP_RESOURCES,
        app_service_principal_client_id="app-client",
        app_service_principal_scim_id="app-scim-id",
        expected_lakebase_instance="mip-app-state",
    )
    record_contract._save_record(estate.workspace, scope=SCOPE, record=captured)
    _close_by_activation(estate)
    closed = journal.read(estate.workspace, app_name=APP_NAME)
    assert closed["state"] == "closed" and closed["closed_by"] == "green_activation"


# --- the rollback's start paths -----------------------------------------------------


def _restore(estate: Estate) -> None:
    rollback.restore_last_good(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        base_url="https://mip.example",
        bearer_token="token",
        deployment_lease_id=LEASE_ID,
        deployment_source_git_sha=GIT_SHA,
        treatment_warehouse_id="warehouse-id",
        treatment_catalog="mip",
    )


def test_restore_of_a_stopped_blue_starts_and_re_pins_without_a_redeploy() -> None:
    estate = _estate()
    estate.apps.compute = "STOPPED"
    _restore(estate)

    record = stored_record(estate)
    assert record["deployment_id"] == "deployment-start-1"
    assert record["predecessor_deployment_id"] == BLUE_ID
    assert (estate.apps.starts, estate.apps.deploys) == (1, 0), "tier a: start only"


def test_restore_falls_back_to_the_tier_b_redeploy_when_the_start_is_not_blue() -> None:
    estate = _estate(StartBehaviour(carry_env=False))
    estate.apps.compute = "STOPPED"
    _restore(estate)

    assert (estate.apps.starts, estate.apps.deploys) == (1, 1)
    assert stored_record(estate)["deployment_id"] == "deployment-redeploy-1"
    assert "predecessor_deployment_id" not in stored_record(estate)


def _green_is_last(estate: Estate) -> None:
    """A post-activation rollback: green is the active, newest deployment, now stopped."""

    estate.apps.deployments["deployment-green"] = deployment(
        "deployment-green",
        source=RELEASE,
        artifact="/Workspace/Users/app-sp/src/deployment-green",
        env_names=ENV_NAMES,
        created="2026-10-01T12:00:00Z",
    )
    estate.apps.active_id = "deployment-green"
    estate.apps.compute = "STOPPED"


@pytest.mark.parametrize("green_last", (True, False), ids=("tier-b-only", "tier-a-falls-back"))
def test_restore_redeploys_blue_when_the_start_id_is_unreadable(green_last: bool) -> None:
    # Tier b needs only compute ACTIVE before its redeploy: a start whose own
    # deployment id cannot be read never stops the rollback short of blue.
    estate = _estate(StartBehaviour(readable_pending_id=False))
    if green_last:
        _green_is_last(estate)
    else:
        estate.apps.compute = "STOPPED"
    _restore(estate)

    assert (estate.apps.starts, estate.apps.deploys) == (1, 1)
    assert stored_record(estate)["deployment_id"] == "deployment-redeploy-1"
    assert estate.apps.active_id == "deployment-redeploy-1" and estate.apps.compute == "ACTIVE"


def test_ensure_of_a_stopped_blue_re_pins_the_start_instead_of_failing_its_check() -> None:
    estate = _estate()
    estate.apps.compute = "STOPPED"
    rollback.ensure_current(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        base_url="https://mip.example",
        bearer_token="token",
        deployment_lease_id=LEASE_ID,
        deployment_source_git_sha=GIT_SHA,
        treatment_warehouse_id="warehouse-id",
        treatment_catalog="mip",
    )
    assert stored_record(estate)["deployment_id"] == "deployment-start-1"
    assert (estate.apps.starts, estate.apps.deploys) == (1, 0)


def test_ensure_refuses_tier_a_when_a_start_would_not_snapshot_blue() -> None:
    estate = _estate()
    estate.apps.compute = "STOPPED"
    estate.apps.default_source = OTHER_SOURCE
    with pytest.raises(RuntimeError, match="tier a refused"):
        rollback.ensure_current(
            estate.workspace,
            app_name=APP_NAME,
            scope=SCOPE,
            base_url="https://mip.example",
            bearer_token="token",
            deployment_lease_id=LEASE_ID,
            deployment_source_git_sha=GIT_SHA,
            treatment_warehouse_id="warehouse-id",
            treatment_catalog="mip",
        )
    assert estate.apps.starts == 0, "never started a snapshot of other bytes"


# --- positive controls --------------------------------------------------------------


def _release(estate: Estate, migration: dict[str, Any]) -> int:
    """A release as the step files order it: plan, the window if any, then one activation."""

    record_versions = 0
    plan = _plan(estate, migration=migration)
    if plan.reasons:
        proven, opened = _run_window(estate)
        _repin(estate, proven, opened["source_tree_sha256"])
        record_versions += 1
    green = dict(_immutable_payload(git_sha="b" * 40), source_code_path=RELEASE)
    estate.apps.deploy_and_wait(APP_NAME, SimpleNamespace(as_dict=lambda: green), timeout=None)
    return record_versions


def test_a_no_window_plan_gives_zero_stops_and_one_deploy() -> None:
    estate = _estate()
    assert _plan(estate, migration=MIGRATION_NOOP).code == "none"
    repins = _release(estate, MIGRATION_NOOP)
    assert (estate.apps.stops, estate.apps.starts, estate.apps.deploys, repins) == (0, 0, 1, 0)


def test_a_wr3_plan_gives_exactly_one_stop_and_one_re_pin() -> None:
    estate = _estate()
    plan = _plan(estate)
    assert plan.reasons == ("WR3",) and plan.code == "window"
    repins = _release(estate, MIGRATION_APPLY)
    assert (estate.apps.stops, estate.apps.starts, estate.apps.deploys, repins) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("role", "grants"),
    (("drift", "drift"), ("exact", "drift"), ("drift", "exact")),
    ids=("first-roll-forward-absent", "grants-drifted", "role-drifted"),
)
def test_a_jobs_reader_re_grant_is_a_restorable_wr2_window_with_one_stop_and_one_re_pin(
    role: str, grants: str
) -> None:
    # M2-PR5 x M0-PR8: only the migration job grants the jobs reader, and a
    # noop plan skips that job, so a role or grant drift must open a window.
    # Blue never reads as the jobs reader: the window closes with blue re-pinned.
    estate = _estate()
    migration = {**MIGRATION_NOOP, "jobs_reader_grants": grants}
    plan = _plan(estate, migration=migration, jobs_reader_role_state=role)
    assert plan.reasons == ("WR2",) and plan.code == "window" and plan.blue_restorable is True
    assert (plan.canonical["jobs_reader_role_state"], plan.canonical["jobs_reader_grants"]) == (
        role,
        grants,
    )
    proven, opened = _run_window(estate, plan)
    _repin(estate, proven, opened["source_tree_sha256"])
    assert (estate.apps.stops, estate.apps.starts) == (1, 1)


@pytest.mark.parametrize(
    ("migration", "role"),
    (
        ({**MIGRATION_NOOP, "jobs_reader_grants": "off"}, "exact"),
        ({key: value for key, value in MIGRATION_NOOP.items() if key != "jobs_reader_grants"}, "exact"),
        (MIGRATION_NOOP, None),
    ),
    ids=("grants-off", "grants-unchecked", "role-unchecked"),
)
def test_signed_blue_refuses_a_plan_that_did_not_check_the_jobs_reader(
    migration: dict[str, Any], role: str | None
) -> None:
    with pytest.raises(ValueError, match="jobs"):
        _plan(_estate(), migration=migration, jobs_reader_role_state=role)
