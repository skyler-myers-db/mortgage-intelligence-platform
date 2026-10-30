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
}
MIGRATION_NOOP = {**MIGRATION_APPLY, "decision": "noop", "migrations": {"pending": []}}
EQUAL_BINDINGS = {"name": APP_NAME, "description": "", "resources": []}


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


def _plan(estate: Estate, *, migration: dict[str, Any] = MIGRATION_APPLY, ack: str = "") -> Any:
    facts = window.collect_facts(
        estate.workspace,
        app_name=APP_NAME,
        scope=SCOPE,
        lakebase_instance="mip-app-state",
        gate_report={"verdict": "passed", "instance_update_sha256": ""},
        binding_payload=EQUAL_BINDINGS,
        role_state="exact",
        migration_plan=migration,
        rebase_unverified_app=False,
    )
    first = window_plan(facts, position="pre-apply", acknowledgement=ack)
    return first if ack or not first.reasons else window_plan(
        facts, position="pre-apply", acknowledgement=first.sha256
    )


def _repin(estate: Estate, deployment_id: str, tree: str | None = None) -> dict[str, Any]:
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
    )


def _run_window(estate: Estate) -> tuple[str, dict[str, Any]]:
    plan = _plan(estate)
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
    window.start_window(estate.workspace, app_name=APP_NAME, assert_lease_held=lambda: None)
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
    window.start_window(estate.workspace, app_name=APP_NAME, assert_lease_held=lambda: None)
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
