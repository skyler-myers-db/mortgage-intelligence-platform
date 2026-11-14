"""The dispatch carries adopt-live's acks, the supersede selector, and the adopt phases' reads (B2, B4, B6).

``operation_acks`` (tools/clients/dispatch_acks.py) is the only way an exact
digest reaches a CI run's shell. Cutover step 10B and its rollback need
adopt-live's three purpose acks, the supersede selector, and the Genie pin and
accepted-exposure acks in the adopt phases; plan #3 needs the quiesce-window
ack so a plan that predicted a window can complete. Each key is admitted only
in the phases whose run reads it, every value is 64 lowercase hex (the
selector is a digest, never a switch), and each tool still recomputes its own
exact digest: a wrong one flows through the carrier and its tool refuses it.

The phase sets are pinned literally here, never read from the module.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from tests.fixtures.genie_pin_workspace import (
    APP,
    INSTANCE,
    SCOPE,
    TITLE,
    PinWorkspace,
    Space,
    patch_rollback_scope,
)
from tests.unit.test_accepted_exposure import NOW, _clean, _env, genie_entry
from tools.clients import dispatch_acks as acks
from tools.clients.errors import ProfileRefusal
from tools.databricks import genie_space_pin
from tools.databricks.estate.accepted_exposure import ACK_ENV as EXPOSURE_ACK_ENV
from tools.databricks.estate.accepted_exposure import ExposureRefusal, load_policy
from tools.databricks.operation_ack import AckRefused, ack_digest, require_ack

ROOT = Path(__file__).resolve().parents[2]
# freeze and thaw (wave 7c) run no scripts/deploy.sh: deploy-dev.yml's own step writes the freeze marker.
PHASES = ("plan", "release", "data", "adopt", "unadopt", "freeze", "thaw")
# key -> (the env its tool reads, the phases whose run reads it)
READ_BY = {
    "adopt_plan": ("MIP_ADOPT_PLAN_ACK", {"adopt", "unadopt"}),
    "adopt_live": ("MIP_ACK_ADOPT_LIVE", {"adopt"}),
    "supersede": ("MIP_ACK_SUPERSEDE", {"adopt"}),
    "supersedes_deployment": ("MIP_ADOPT_LIVE_SUPERSEDE", {"adopt"}),
    "unadopt": ("MIP_ACK_UNADOPT", {"unadopt"}),
    "quiesce_window": ("MIP_ACK_QUIESCE_WINDOW", {"plan", "release"}),
    "database_instance_update": ("MIP_ACK_DATABASE_INSTANCE_UPDATE", {"plan", "release"}),
    "genie_pin": ("MIP_GENIE_PIN_ACK", {"plan", "release", "adopt", "unadopt"}),
    "accepted_exposure_plan": ("MIP_ACK_ACCEPTED_EXPOSURE_PLAN", {"plan", "release", "adopt", "unadopt"}),
    # B3: the core release's unverified-frontend ack (its prediction, step 0a, the proof, tier b).
    "restore_unverified_frontend": ("MIP_ACK_RESTORE_UNVERIFIED_FRONTEND", {"release"}),
    # Wave 7c: an owner's break-glass start over an open window; a plan prints it, a release carries it.
    "owner_start_repin": ("MIP_ACK_OWNER_START_REPIN", {"release"}),
    "data_ledger_supersede": ("MIP_ACK_DATA_LEDGER_SUPERSEDE", {"data"}),
    "data_ledger_rerun": ("MIP_ACK_DATA_LEDGER_RERUN", {"data"}),
    # The metastore owner's attestation (spike S0(b′)): never data, whose run never reads it.
    "metastore_app_grants": ("MIP_ACK_METASTORE_APP_GRANTS", {"plan", "adopt", "unadopt", "release"}),
    # Wave 7c: the thaw of one signed freeze marker. Only the thaw phase reads it; a freeze takes none.
    "freeze_thaw": ("MIP_ACK_FREEZE_THAW", {"thaw"}),
}
NEW_KEYS = ("adopt_live", "supersede", "supersedes_deployment", "unadopt", "quiesce_window", "genie_pin", "accepted_exposure_plan")


def _hex(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _refusal(text: str, phase: str) -> list[str]:
    with pytest.raises(ProfileRefusal) as caught:
        acks.operation_exports(text, phase)
    return list(caught.value.lines)


# --- (a) the closed map and each key's phases --------------------------------------------


def test_the_map_is_exactly_the_reviewed_keys_envs_and_phases() -> None:
    assert {name: (ack.env, set(ack.phases)) for name, ack in acks.OPERATION_ACKS.items()} == READ_BY
    assert {name for name, ack in acks.OPERATION_ACKS.items() if ack.kind == "selector"} == {"supersedes_deployment"}
    assert {ack.kind for ack in acks.OPERATION_ACKS.values()} == {"ack", "selector"}
    with pytest.raises(ValueError):
        acks.OperationAck("MIP_ACK_X", frozenset({"adopt"}), kind="switch")
    with pytest.raises(ValueError):
        acks.OperationAck("MIP_ACK_X", frozenset({"deploy"}))


@pytest.mark.parametrize("name", sorted(READ_BY))
@pytest.mark.parametrize("phase", PHASES)
def test_each_key_is_exported_in_its_phases_and_refused_outside_them(name: str, phase: str) -> None:
    env, phases = READ_BY[name]
    text = f"{name}={_hex(name)}"
    if phase in phases:
        assert acks.operation_exports(text, phase) == {env: _hex(name)}
    else:
        assert _refusal(text, phase) == [f"operation_acks: {name} is never read by --phase {phase}"]


def test_genie_pin_and_accepted_exposure_stay_out_of_data_and_the_window_ack_out_of_the_adopt_phases() -> None:
    """Data never pins and skips the probes; the adopt phases plan no window."""

    assert _refusal(f"genie_pin={_hex('g')}", "data") == ["operation_acks: genie_pin is never read by --phase data"]
    assert _refusal(f"accepted_exposure_plan={_hex('a')}", "data")[0].endswith("never read by --phase data")
    for phase in ("data", "adopt", "unadopt"):
        assert _refusal(f"quiesce_window={_hex('w')}", phase) == [
            f"operation_acks: quiesce_window is never read by --phase {phase}"
        ]


# --- (b) shape -----------------------------------------------------------------------------

BAD_VALUES = (
    "A" * 64,
    "a" * 63,
    "a" * 65,
    "",
    "yes",
    "true",
    "1",
    "on",
    "<sha256>",
    "0x" + "a" * 64,
)


@pytest.mark.parametrize("name", NEW_KEYS)
@pytest.mark.parametrize("value", BAD_VALUES, ids=lambda value: value[:8] or "empty")
def test_each_new_key_refuses_a_malformed_value(name: str, value: str) -> None:
    phase = sorted(READ_BY[name][1])[0]
    assert _refusal(f"{name}={value}", phase) == ["operation_acks: each entry is purpose=<64 lowercase hex>"]


@pytest.mark.parametrize("name", NEW_KEYS)
def test_each_new_key_refuses_a_spaced_pair_an_env_name_an_uppercase_key_and_a_repeat(name: str) -> None:
    env, phases = READ_BY[name]
    phase = sorted(phases)[0]
    digest = _hex(name)
    shape = "operation_acks: each entry is purpose=<64 lowercase hex>"
    for text in (f"{name} = {digest}", f"{env}={digest}", f"{name.upper()}={digest}"):
        assert shape in _refusal(text, phase), text
    assert _refusal(f"{name}={digest} {name}={_hex('other')}", phase) == [f"operation_acks: {name} is given twice"]


# --- (c) the supersede selector ---------------------------------------------------------------


def test_the_supersede_selector_is_a_digest_never_a_switch() -> None:
    selector, ack, bind = _hex("deployment"), _hex("supersede"), _hex("bind")
    # Alone: the read-only supersede plan, which prints the supersede ack.
    assert acks.operation_exports(f"supersedes_deployment={selector}", "adopt") == {"MIP_ADOPT_LIVE_SUPERSEDE": selector}
    assert acks.operation_exports(
        f"supersedes_deployment={selector} adopt_plan={bind} supersede={ack}", "adopt"
    ) == {"MIP_ADOPT_LIVE_SUPERSEDE": selector, "MIP_ADOPT_PLAN_ACK": bind, "MIP_ACK_SUPERSEDE": ack}
    for switch in ("yes", "true", "1", "on", "TRUE"):
        assert _refusal(f"supersedes_deployment={switch}", "adopt") == [
            "operation_acks: each entry is purpose=<64 lowercase hex>"
        ]
    assert _refusal(f"supersedes_deployment={selector}", "unadopt") == [
        "operation_acks: supersedes_deployment is never read by --phase unadopt"
    ]


# --- (e) the runbook's dispatches -------------------------------------------------------------

DISPATCH = (
    "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase={phase} "
    "-f owner_attestation=<profile-ack> -f operation_acks=\"{acks}\" -f acknowledge_single_maintainer_break_glass=true"
)
PLAN_DISPATCH = (
    "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=plan "
    "-f operation_acks=\"{acks}\" -f acknowledge_single_maintainer_break_glass=true"
)
# The metastore owner's attestation (spike S0(b′)). The cutover's CI deployer is not a metastore
# admin, so the runbook shows every plan, adopt and unadopt dispatch carrying it, last.
ATTESTED = " metastore_app_grants=<sha256>"
# (template, phase, the acks text before the attestation) as first-client-cutover.md shows them.
RUNBOOK = (
    (DISPATCH, "adopt", "accepted_exposure_plan=<sha256>"),  # 10B-0
    (DISPATCH, "adopt", "genie_pin=<sha256> accepted_exposure_plan=<sha256>"),  # 10B-1
    (DISPATCH, "adopt", "genie_pin=<sha256> accepted_exposure_plan=<sha256> adopt_plan=<sha256> adopt_live=<sha256>"),
    (DISPATCH, "unadopt", "accepted_exposure_plan=<sha256>"),
    (DISPATCH, "unadopt", "accepted_exposure_plan=<sha256> adopt_plan=<sha256> unadopt=<sha256>"),
    (DISPATCH, "unadopt", "genie_pin=<sha256> accepted_exposure_plan=<sha256> adopt_plan=<sha256> unadopt=<sha256>"),
    (DISPATCH, "adopt", "accepted_exposure_plan=<sha256> supersedes_deployment=<sha256>"),
    (
        DISPATCH,
        "adopt",
        "accepted_exposure_plan=<sha256> supersedes_deployment=<sha256> adopt_plan=<sha256> supersede=<sha256>",
    ),
    (PLAN_DISPATCH, "plan", "quiesce_window=<sha256> accepted_exposure_plan=<sha256>"),  # plan #3, acked
)


def _filled(acks_text: str) -> tuple[str, dict[str, str]]:
    """Each purpose gets its own digest, so an export cannot land under the wrong name."""

    names = re.findall(r"([a-z_]+)=<sha256>", acks_text)
    filled = " ".join(f"{name}={_hex(name)}" for name in names)
    return filled, {READ_BY[name][0]: _hex(name) for name in names}


@pytest.mark.parametrize(("template", "phase", "acks_text"), RUNBOOK, ids=lambda value: str(value)[:40])
def test_the_runbook_dispatch_lines_parse_to_their_exports(template: str, phase: str, acks_text: str) -> None:
    # A metastore-admin deployer's dispatch (no attestation), then the line the runbook shows.
    for text in (acks_text, acks_text + ATTESTED):
        filled, expected = _filled(text)
        assert acks.operation_exports(filled, phase) == expected
        assert list(acks.operation_exports(filled, phase).values()) == [_hex(name) for name in re.findall(r"([a-z_]+)=<", text)]
    assert expected["MIP_ACK_METASTORE_APP_GRANTS"] == _hex("metastore_app_grants")
    runbook = (ROOT / "docs" / "clients" / "first-client-cutover.md").read_text(encoding="utf-8")
    command = template.format(phase=phase, acks=acks_text + ATTESTED)
    assert command in runbook, f"the runbook no longer shows: {command}"
    unattested = template.format(phase=phase, acks=acks_text)
    assert unattested not in runbook, f"this dispatch refuses metastore_authority without the attestation: {unattested}"


# --- (f) a digest of another plan flows through, and its own tool refuses it ------------------

ADOPT_PLAN = {"mode": "adopt", "deployment_sha256": "d" * 64, "record_sha256": "", "tree_sha256": "e" * 64}
SUPERSEDE_PLAN = {**ADOPT_PLAN, "mode": "supersede", "supersedes_deployment_sha256": "f" * 64, "record_sha256": "1" * 64}
UNADOPT_PLAN = {"mode": "unadopt", "record_sha256": "2" * 64}


@pytest.mark.parametrize(
    ("purpose", "plan", "changed"),
    (
        ("adopt_live", ADOPT_PLAN, {**ADOPT_PLAN, "deployment_sha256": "3" * 64}),
        ("supersede", SUPERSEDE_PLAN, {**SUPERSEDE_PLAN, "record_sha256": "4" * 64}),
        ("unadopt", UNADOPT_PLAN, {**UNADOPT_PLAN, "record_sha256": "5" * 64}),
    ),
)
def test_adopt_lives_acks_are_carried_byte_for_byte_and_its_tool_refuses_another_plans_digest(
    purpose: str, plan: dict[str, str], changed: dict[str, str]
) -> None:
    phase = sorted(READ_BY[purpose][1])[0]
    env = READ_BY[purpose][0]
    exported = acks.operation_exports(f"{purpose}={ack_digest(purpose, changed)}", phase)[env]
    assert exported == ack_digest(purpose, changed), "the carrier never rewrites a value"
    with pytest.raises(AckRefused) as refused:
        require_ack(purpose, plan, exported)
    assert refused.value.code == "ack_mismatch"
    assert require_ack(purpose, plan, acks.operation_exports(f"{purpose}={ack_digest(purpose, plan)}", phase)[env])


def test_the_owner_start_ack_rides_a_release_only_and_its_tool_refuses_another_plans_digest() -> None:
    """Wave 7c: a plan prints the digest and carries none; the release's tool recomputes it exactly."""

    plan = {"version": 1, "window_sha256": "a" * 64, "start_deployment_id": "deployment-placeholder"}
    other = {**plan, "start_deployment_id": "another-deployment-placeholder"}
    env = "MIP_ACK_OWNER_START_REPIN"
    assert acks.OPERATION_ACKS["owner_start_repin"] == acks.OperationAck(env, frozenset({"release"}))
    pair = f"owner_start_repin={ack_digest('owner_start_repin', plan)}"
    assert acks.operation_exports(pair, "release") == {env: ack_digest("owner_start_repin", plan)}
    for phase in ("plan", "data", "adopt", "unadopt"):
        assert _refusal(pair, phase) == [f"operation_acks: owner_start_repin is never read by --phase {phase}"]
    carried = acks.operation_exports(f"owner_start_repin={ack_digest('owner_start_repin', other)}", "release")[env]
    with pytest.raises(AckRefused) as refused:
        require_ack("owner_start_repin", plan, carried)
    assert refused.value.code == "ack_mismatch", "the carrier never rewrites a value, and never checks one"
    for given in ("adopt_live", "supersede", "unadopt", "restore_unverified_frontend"):
        with pytest.raises(AckRefused):
            require_ack("owner_start_repin", plan, ack_digest(given, plan))


def test_an_adopt_live_digest_given_as_supersede_or_the_reverse_is_refused() -> None:
    for given, wanted, plan in (("adopt_live", "supersede", SUPERSEDE_PLAN), ("supersede", "adopt_live", ADOPT_PLAN)):
        exported = acks.operation_exports(f"{wanted}={ack_digest(given, plan)}", "adopt")[READ_BY[wanted][0]]
        with pytest.raises(AckRefused) as refused:
            require_ack(wanted, plan, exported)
        assert refused.value.code == "ack_mismatch", (given, wanted)


def test_the_accepted_exposure_policy_refuses_another_policys_digest_in_an_adopt_phase() -> None:
    mine, other = [genie_entry()], [genie_entry(reason="Another reviewed reason")]
    other_digest = load_policy(_env(other), now=NOW, ack="", static_control=_clean, require_ack=False).digest
    exported = acks.operation_exports(f"accepted_exposure_plan={other_digest}", "adopt")
    with pytest.raises(ExposureRefusal, match="not acknowledged"):
        load_policy({**_env(mine), **exported}, now=NOW, static_control=_clean)
    assert EXPOSURE_ACK_ENV in exported
    right = load_policy(_env(mine), now=NOW, ack="", static_control=_clean, require_ack=False).digest
    assert load_policy({**_env(mine), **acks.operation_exports(f"accepted_exposure_plan={right}", "unadopt")}, now=NOW, static_control=_clean)


def test_the_genie_pin_refuses_the_digest_of_a_plan_with_other_managers(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    workspace = PinWorkspace(spaces=[Space("governed-space", managers=("creator@lender.example",))])
    with pytest.raises(genie_space_pin.GeniePinRefusal) as first:
        genie_space_pin.resolve(workspace, app_name=APP, title=TITLE, scope=SCOPE, lakebase_instance=INSTANCE)
    plan = {key: value for key, value in (first.value.plan or {}).items() if key != "sha256"}
    other = genie_space_pin.plan_sha256({**plan, "managers": ["creator@lender.example", "someone@else.example"]})
    exported = acks.operation_exports(f"genie_pin={other}", "adopt")["MIP_GENIE_PIN_ACK"]
    with pytest.raises(genie_space_pin.GeniePinRefusal, match="the ack names another plan"):
        genie_space_pin.resolve(
            workspace, app_name=APP, title=TITLE, scope=SCOPE, lakebase_instance=INSTANCE, ack_plan=exported
        )
    acked = acks.operation_exports(f"genie_pin={genie_space_pin.plan_sha256(plan)}", "unadopt")["MIP_GENIE_PIN_ACK"]
    resolution = genie_space_pin.resolve(
        workspace, app_name=APP, title=TITLE, scope=SCOPE, lakebase_instance=INSTANCE, ack_plan=acked
    )
    assert resolution.action == "adopt" and workspace.mutations == [], "resolve is read-only"


# --- (g) and (h) --------------------------------------------------------------------------------


def test_refusals_never_print_a_value() -> None:
    values = [_hex(name) for name in READ_BY]
    for name in READ_BY:
        for phase in PHASES:
            text = f"{name}={_hex(name)} {name}={_hex(name + 'twice')} zyrplax={_hex('z')}"
            lines = _refusal(text, phase)
            assert not [value for value in (*values, _hex(name + "twice"), _hex("z")) if any(value in line for line in lines)]


@pytest.mark.parametrize("phase", PHASES)
def test_an_unreviewed_purpose_still_refuses_in_every_phase(phase: str) -> None:
    lines = _refusal(f"zyrplax={_hex('zyrplax')}", phase)
    assert len(lines) == 1 and lines[0].startswith("operation_acks: zyrplax is not a reviewed purpose (")


# --- the docs name the closed map ---------------------------------------------------------------


def _purposes_in(text: str) -> set[str]:
    return set(re.findall(r"`([a-z_]+)`", text))


def test_the_docs_name_every_reviewed_operation_pair() -> None:
    deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    start = deployment.index("- `operation_acks` carries `purpose=<sha256>` pairs from a closed map")
    bullet = deployment[start : deployment.index("\n- ", start + 1)]
    assert _purposes_in(bullet) >= set(acks.OPERATION_ACKS)
    assert _purposes_in(bullet) & set(READ_BY) == set(acks.OPERATION_ACKS)
    review = (ROOT / "docs" / "clients" / "review-modes.md").read_text(encoding="utf-8")
    start = review.index("operation acks a tool printed (")
    listed = review[start : review.index(")", start + len("operation acks a tool printed ("))]
    assert _purposes_in(listed) == set(acks.OPERATION_ACKS)
