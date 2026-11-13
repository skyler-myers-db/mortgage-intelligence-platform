"""What the docs say about the pre-lease activation prediction is what the tool does (B3).

The runbook's step-7 gate is read by an owner from a step summary, and
docs/deployment.md is where a refusal's remedy is looked up, so their claims
are pinned to the code: which verdicts the no-rollback ack defers (three, not
every one the prediction can refuse), which verdicts ``env-names`` prints and
which of them lets the cutover go on, and what the Blockers section says still
blocks the first-client cutover once B3 is on main.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.unit.test_activation_prediction import INPUTS, LEASE, NAMES, _predict, _world, builder
from tools.databricks import activation_prediction as ap
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import core_blue

REPO = Path(__file__).resolve().parents[2]
DEFERRED = {"unverified_frontend_unacked", "no_rollback_proof", "frontend_ack_unpredictable"}


def _flat(relative: str) -> str:
    return " ".join((REPO / relative).read_text(encoding="utf-8").split())


@pytest.fixture
def world_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    monkeypatch.setattr(ap, "_scope_names", lambda _workspace: {rr.SCOPE})
    monkeypatch.delenv(LEASE, raising=False)


@pytest.mark.usefixtures("world_keys")
def test_the_no_rollback_ack_defers_three_verdicts_and_the_docs_name_exactly_those(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (REPO / "tools" / "databricks" / "activation_prediction.py").read_text(encoding="utf-8")
    assert set(re.findall(r'_unverified\(\s*"([a-z_]+)"', source)) == DEFERRED

    # The two the ack never defers: the late gate raises them before it reads that ack.
    world = _world(tmp_path / "invalid")
    record = world.record()
    assert record is not None
    good, _why = core_blue.blue_restore_payload(record, build=builder(), **INPUTS)
    assert good is not None
    elsewhere = {**good, "source_code_path": rr.LEGACY_FOLDER}
    monkeypatch.setattr(core_blue, "blue_restore_payload", lambda *_a, **_kw: (elsewhere, "rebuilt"))
    agentic = _world(tmp_path / "agentic")
    contract.save_versioned_record(agentic, scope=rr.SCOPE, record=rr.v6_record())
    for phase in ("release", "complete"):
        assert _predict(world, phase, no_rollback_ack_given=True).code == "payload_invalid"
        assert _predict(agentic, phase, no_rollback_ack_given=True).code == "blue_tier_mismatch"

    deployment = _flat("docs/deployment.md")
    assert "defers each of those verdicts" not in deployment
    start = deployment.index("`MIP_ACK_NO_ROLLBACK_FOR` (its value is never passed) defers three of those verdicts")
    sentence = deployment[start : deployment.index("a plan never defers.", start)]
    deferred, kept = sentence.split("`payload_invalid` and the tier gate's codes still refuse before the lease")
    assert set(re.findall(r"`([a-z_]+)`", deferred)) == DEFERRED, deferred
    assert "because the proof raises them before it reads that ack" in kept
    assert "defers every one of those three verdicts" in " ".join((ap.__doc__ or "").split())


VERDICTS = {
    "same": "env-name parity holds: ",
    "differs": "env-name parity DIFFERS: ",
    "lease_bound": "env-name parity is LEASE-BOUND: ",
}


@pytest.mark.usefixtures("world_keys")
def test_the_runbooks_step_6_lists_every_verdict_env_names_prints_and_only_holds_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    governed = sorted({*NAMES, LEASE})
    cases: dict[str, tuple[list[str], Any]] = {
        "same": (NAMES, builder()),
        "differs": (sorted({*NAMES, "MIP_OTEL_ENDPOINT"}), builder()),
        "lease_bound": (governed, builder(governed)),
    }
    runbook = _flat("docs/clients/first-client-cutover.md")
    step_6 = runbook[runbook.index("## Step 6. Plan #1") : runbook.index("## Step 7.")]
    for verdict, (names, build) in cases.items():
        world = _world(tmp_path / verdict, names=names)
        world.secrets.values.clear()
        measured, message = ap.env_name_parity(world, app_name=rr.APP_NAME, payload_inputs=INPUTS, build=build)
        assert measured == verdict and message.startswith(VERDICTS[verdict]), message
        assert f"`{VERDICTS[verdict].rstrip(': ')}" in step_6, verdict
        assert (verdict in ap._CUTOVER_STOP) is (verdict != "same")
    assert "`env-name parity not measured`" in step_6
    assert "each of the others ends `do not start step 7`" in step_6
    for stop in (*ap._CUTOVER_STOP.values(), ap._NOT_MEASURED_STOP):
        assert stop.endswith("do not start step 7") and "parity holds" not in stop
    assert f"the set holds {LEASE}`: **stop.**" in step_6 and "`frontend_ack_unpredictable`" in step_6
    step_7 = runbook[runbook.index("## Step 7. Unity Catalog ownership handover") : runbook.index("## Step 8.")]
    assert "its `env-name parity holds` (no env-name difference; B3)" in step_7
    deployment = _flat("docs/deployment.md")
    for word in ("`holds`; `DIFFERS` (names only); `LEASE-BOUND`", "or `not measured`", "Every verdict but `holds` exits the tool 2"):
        assert word in deployment, word
    # The two readings that measure nothing are no verdict: nothing to rebuild, exit 0.
    assert "With no App, or an App that has no deployment yet, there is nothing to measure, and it says so (exit 0)." in deployment
    undeployed = _world(tmp_path / "undeployed")
    undeployed.secrets.values.clear()
    monkeypatch.setattr(undeployed.apps, "list_deployments", lambda *_a, **_kw: [])
    measured, message = ap.env_name_parity(undeployed, app_name=rr.APP_NAME, payload_inputs=INPUTS, build=builder())
    assert measured == "no_deployment" and "parity" not in message and measured not in ap._CUTOVER_STOP


def test_the_blockers_section_says_plainly_that_no_code_gap_is_left_and_names_the_follow_up() -> None:
    runbook = _flat("docs/clients/first-client-cutover.md")
    blockers = runbook[runbook.index("## Blockers on main") : runbook.index("## Decisions taken")]
    assert "**What still blocks the cutover: no known code gap.**" in blockers
    assert "| B3 | fixed |" in blockers and "`restore_unverified_frontend` (`MIP_ACK_RESTORE_UNVERIFIED_FRONTEND`, phase `release` only)" in blockers
    for fixed in ("B1 (#344 and #348)", "B2, B4(a), B4(b) and B6 (#345)", "B3 (#349)", "B4(c) (#347)", "B5 (#343)", "S0(b) (#346)"):
        assert fixed in blockers, fixed
    assert "**No known code gap is left on 10B's path:**" in blockers
    assert "**12 walked with #343 to #348 and B3 (#349) all on `main`**" in blockers
    assert "**No known code gap is left on step 12's path.**" in blockers
    # Premise change (wave 7c): the CI-signed freeze marker is built, so one follow-up is left.
    assert "**One follow-up stops nothing:** an owner break-glass re-pin over an open quiesce window" in blockers
    assert "The CI-signed freeze and thaw marker is on `main` (wave 7c): steps 6b and 13." in blockers
    assert "One smaller gap is noted at its step, and it does not stop the cutover" in blockers
    assert "so CI signs it (`phase=freeze`, step 6b) and its thaw (`phase=thaw`, the end of step 13)" in blockers
    for stale in ("a CI-run freeze is a follow-up", "a freeze and thaw marker that CI signs", "and a CI-signed freeze marker ("):
        assert stale not in runbook, stale
    assert "No step from 6 on has run live." in blockers
    # Nothing still calls B3 open, in the runbook or beside it.
    onboarding = _flat("docs/clients/onboarding.md")
    for stale in (
        "blocked (B3", "blocked by B3", "B3 has landed or is about to land", "B3, or B3 is about to land",
        "it stops step 12", "Do not dispatch step 12 until the follow-up lands", "The gaps below remain",
        "**wait** for the blockers",
    ):
        assert stale not in runbook, stale
    assert "**Blocked** by the unverified-frontend ack" not in onboarding
    assert "**No known code refusal is left on its path**" in onboarding and "(`LEASE-BOUND`) stops there" in onboarding
    glance = runbook[runbook.index("## Steps at a glance") : runbook.index("## Step 1. Freeze and snapshot")]
    assert "| 12 | Release (run 1) | CI | after 11; needs the S0(b′) attestation |" in glance
    assert "## Step 12. Release (run 1) **Who:** CI." in runbook
    release = runbook[runbook.index("## Step 12. Release (run 1)") : runbook.index("## Step 13.")]
    assert "metastore_app_grants=<sha256> restore_unverified_frontend=<sha256>" in release
