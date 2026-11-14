"""What the docs say about the pre-lease activation prediction is what the tool does (B3).

The runbook's step-7 gate is read by an owner from a step summary, and
docs/deployment.md is where a refusal's remedy is looked up, so their claims
are pinned to the code: which verdicts the no-rollback ack defers (three, not
every one the prediction can refuse), which verdicts ``env-names`` prints and
which of them lets the cutover go on, and what the Blockers section says still
blocks the first-client cutover once B3 is on main. The runbook's step 4 names
every profile field whose env name the comparison can add or lack, and step 6
says what ``lacks`` and ``adds`` each ask of the profile, pinned to the real
payload builder in the shell a plan has at its live checks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile, env_file_text
from tests.fixtures.live_workspace import LiveWorkspace
from tests.unit.test_activation_prediction import (
    INPUTS,
    LEASE,
    NAMES,
    _predict,
    _workspace,
    _world,
    builder,
)
from tests.unit.test_settings_d0_env_parity import D0_NAMES, D0_OPERATOR_INPUTS, _d0_operator_config
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve
from tools.databricks import activation_prediction as ap
from tools.databricks import app_deploy_payload, core_blue
from tools.databricks import app_rollback_record_contract as contract

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


def test_the_blockers_section_says_plainly_that_no_code_gap_is_left_and_that_no_follow_up_is_left() -> None:
    runbook = _flat("docs/clients/first-client-cutover.md")
    blockers = runbook[runbook.index("## Blockers on main") : runbook.index("## Decisions taken")]
    assert "**What still blocks the cutover: no known code gap.**" in blockers
    assert "| B3 | fixed |" in blockers and "`restore_unverified_frontend` (`MIP_ACK_RESTORE_UNVERIFIED_FRONTEND`, phase `release` only)" in blockers
    for fixed in ("B1 (#344 and #348)", "B2, B4(a), B4(b) and B6 (#345)", "B3 (#349)", "B4(c) (#347)", "B5 (#343)", "S0(b) (#346)"):
        assert fixed in blockers, fixed
    assert "**No known code gap is left on 10B's path:**" in blockers
    assert "**12 walked with #343 to #348 and B3 (#349) all on `main`**" in blockers
    assert "**No known code gap is left on step 12's path.**" in blockers
    # Premise change (wave 7c, both parts on main): the owner break-glass re-pin (#356) and the
    # CI-signed freeze and thaw (#352) are both built, so the section names no follow-up left.
    assert "**No follow-up is left in this list:** both that it named are closed on `main` (wave 7c)" in blockers
    assert "a release re-pins an owner's break-glass start over an open quiesce window, with the exact `owner_start_repin` digest a plan prints" in blockers
    assert "The CI-signed freeze and thaw marker is on `main` (wave 7c): steps 6b and 13." in blockers
    assert "No smaller gap is left at its step: wave 7c closed both that this section named." in blockers
    assert "so CI signs it (`phase=freeze`, step 6b) and its thaw (`phase=thaw`, the end of step 13)" in blockers
    assert "the next release re-pins that start under its lease, only with the owner's exact `owner_start_repin` ack" in blockers
    assert "an open quiesce window that the owner restarts by hand stays refused" not in blockers
    for stale in (
        "a CI-run freeze is a follow-up",
        "a freeze and thaw marker that CI signs",
        "and a CI-signed freeze marker (",
        "follow-up stops nothing",
        "follow-ups stop nothing",
        "smaller gap is noted",
        "smaller gaps are noted",
        "It is a follow-up: a reviewed owner break-glass re-pin",
    ):
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


# --- step 4's optional fields and step 6's remedy (the snapshot's gap g1) -----------------------

APPROVERS = "MIP_APPROVER_IDENTITIES"
# The names the emergency recipe's D0 set holds only because its shell set them.
D0_PROFILE_SET = ("MIP_ADMIN_EMAILS", "MIP_APPROVER_EMAILS", APPROVERS)


def test_step_4_names_every_field_the_env_name_comparison_can_lack_or_add_and_step_6_says_what_to_do() -> None:
    runbook = _flat("docs/clients/first-client-cutover.md")
    step_4 = runbook[runbook.index("## Step 4. Bootstrap") : runbook.index("## Step 5.")]
    optional = step_4[step_4.index("**Optional fields, and blue's env names.**") : step_4.index("**Commands,** in this order:")]
    for name in (*views.OPTIONAL_PAYLOAD_ENV_NAMES, APPROVERS, "MIP_ADMIN_IDENTITIES"):
        assert f"`{name}`" in optional, f"step 4 does not name {name}"
    assert set(D0_PROFILE_SET) <= set(D0_NAMES), "the emergency recipe's set holds the three step 4 points at"
    assert not (set(views.OPTIONAL_PAYLOAD_ENV_NAMES) - set(D0_PROFILE_SET)) & set(D0_NAMES)
    assert APPROVERS not in views.OPTIONAL_PAYLOAD_ENV_NAMES and APPROVERS not in ap.GOVERNED_DERIVED_NAMES
    assert "MIP_ADMIN_IDENTITIES" in ap.GOVERNED_DERIVED_NAMES, "the one identity list the deploy derives"
    for words in (
        "exported only when the profile sets them",
        "is reported `lacks <name>`",
        "is left out of the rebuild, so it is harmless",
        f"`{APPROVERS}`: no deploy step derives it (unlike `MIP_ADMIN_IDENTITIES`)",
        "Unset while the live set holds it: `lacks`. Set while the live set lacks it: `adds` (it is never left out)",
        "`MIP_LENDER_IDENTITY_JSON`, `MIP_LENDER_IDENTITY_SHA256`",
        "presenter mode on (`MIP_PRESENTER_MODE`)",
        "a `LAKEBASE_DATABASE` other than the default",
        "a non-canonical Cotality source (`MIP_COTALITY_SOURCE_*`)",
        "Salesforce bindings on",
        "Name parity is not value parity",
    ):
        assert words in optional, words
    step_6 = runbook[runbook.index("## Step 6. Plan #1") : runbook.index("## Step 7.")]
    differs = step_6[step_6.index("`env-name parity DIFFERS") : step_6.index("`env-name parity is LEASE-BOUND")]
    assert "`lacks <name>`: the live set holds a name this profile does not produce: set the profile field of that name" in differs
    assert "`adds <name>`: the profile produces a name the live install never had: unset that field or undo that choice" in differs
    assert "Names only: a value is never compared." in differs and "never start step 7 around it" in differs


@pytest.mark.parametrize(
    ("profile", "live_names", "expected"),
    (
        pytest.param({}, set(D0_NAMES), "env-name parity holds", id="every-name-the-live-set-holds-is-set"),
        pytest.param({"MIP_ADMIN_EMAILS": None}, set(D0_NAMES), "(adds none; lacks MIP_ADMIN_EMAILS)", id="an-unset-role-email-lacks"),
        pytest.param({APPROVERS: None}, set(D0_NAMES), f"(adds none; lacks {APPROVERS})", id="unset-approver-identities-lacks"),
        pytest.param({}, set(D0_NAMES) - {APPROVERS}, f"(adds {APPROVERS}; lacks none)", id="set-approver-identities-adds"),
        pytest.param({}, set(D0_NAMES) - {"MIP_ADMIN_EMAILS"}, "env-name parity holds", id="a-set-optional-name-is-left-out"),
    ),
)
def test_the_real_builder_lacks_and_adds_exactly_as_step_4_says(
    profile: dict[str, str | None],
    live_names: set[str],
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The deploy path's rebuild: ``.env.local`` is the profile and the shell holds its deploy controls."""

    _d0_operator_config(monkeypatch, tmp_path)  # no builder input from this machine
    for name in D0_OPERATOR_INPUTS:  # nor the emergency recipe's own shell
        monkeypatch.delenv(name, raising=False)
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    values = core_tier_profile(**profile)
    env_local = tmp_path / "profile.env.local"
    env_local.write_text(env_file_text(values), encoding="utf-8")
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", env_local)
    controls = views.deploy_controls(resolve(RawProfile(values=values)))
    for name, value in controls.items():  # resolve_client_profile exports these
        monkeypatch.setenv(name, value)
    # The profile sets every optional name: only the ones the live set holds may ride the rebuild.
    assert {name for name in views.OPTIONAL_PAYLOAD_ENV_NAMES if name in controls} - live_names
    assert APPROVERS not in controls or APPROVERS not in profile
    world = _world(tmp_path / "world", names=sorted(live_names))
    world.secrets.values.clear()  # before an adoption: no record
    live = LiveWorkspace(values, identity_ids=IDENTITY_IDS)

    code = ap.main(["env-names"], workspace_factory=lambda: _workspace(live, world.apps))

    printed = capsys.readouterr()
    assert expected in printed.out + printed.err, printed.err
    assert (code == 0) is (expected == "env-name parity holds")
    assert ("env-name parity DIFFERS" in printed.err) is (code == 2)
