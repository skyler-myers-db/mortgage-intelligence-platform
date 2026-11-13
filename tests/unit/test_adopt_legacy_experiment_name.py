"""An install deployed under an earlier experiment declaration refuses ``renamed`` until the owner renames it.

The bundle has declared three names for ``resources.experiments.mip_lead_scoring``
(``HISTORY``, from ``git log -S lead-scoring -- databricks.yml``): a sub-folder
of ``/Shared`` until 24b502ce (2026-07-17), a workspace-root name until
4d9a0f80 (2026-10-27), and today's, one level under ``/Shared``
(tests/unit/test_bundle_experiment_path.py pins that one). An install first
deployed under the sub-folder declaration, by the development-mode target of
that time, holds its experiment as the CLI rendered it then: in the sub-folder,
under the ``[dev <user>] `` prefix. The adopt map binds the bundle's name, or
its development-mode form in the same folder, and nothing else
(tools/databricks/adopt_legacy_map.py, ``same_name``), so the adopt phase's
bind plan refuses that experiment ``renamed`` before the lease. The cutover's
first read-only snapshot predicted exactly that for the first client.

The route is the owner's rename of the live experiment to the declared name
before the ownership handovers (docs/clients/first-client-cutover.md, step
3b), never a wider name rule: a rule that accepted another folder or base
name would bind whatever object the legacy state names. These tests pin why
the step exists (the legacy shape refuses, the declared name binds, and after
the rename nothing is left to rename), that the runbook renames to exactly
what databricks.yml declares and rolls back to exactly the refused shape, and
the other runbook statements the snapshot corrected.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

from tests.fixtures import adopt_estate as es
from tests.unit.test_bundle_experiment_path import DEV_PREFIX, _cli_presets_name
from tests.unit.test_install_state_adopt import Lease, _main
from tools.databricks import adopt_legacy_map as legacy_map
from tools.databricks import install_state_adopt as adopt

REPO = Path(__file__).resolve().parents[2]
RUNBOOK = "docs/clients/first-client-cutover.md"
KEY = "experiments.mip_lead_scoring"
# Every name the bundle has declared for the experiment, oldest first, and whether the
# legacy shape (the first one's development-mode render) is that declaration's name.
HISTORY = (
    ("/Shared/mip/lead-scoring", True),  # until 24b502ce, 2026-07-17
    ("/mip-lead-scoring", False),  # until 4d9a0f80, 2026-10-27
    ("/Shared/mip-lead-scoring", False),  # today's
)
LEGACY = _cli_presets_name(HISTORY[0][0], DEV_PREFIX)
# The same damage on the synthetic estate: another folder and another base name.
FIXTURE_LEGACY = "/Shared/synthetic/[dev example] lead-scoring"
LOOKUP = ("experiments", "get_by_name")


def _declared() -> str:
    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    return str(bundle["resources"]["experiments"]["mip_lead_scoring"]["name"])


def _flat(relative: str) -> str:
    return " ".join((REPO / relative).read_text(encoding="utf-8").split())


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end, text.index(start))]


# --- why the step exists --------------------------------------------------------------------


def test_the_legacy_experiment_shape_is_its_first_declarations_name_and_no_later_ones() -> None:
    declared = _declared()
    assert declared == HISTORY[-1][0], "the history's last entry is what databricks.yml declares today"
    assert LEGACY == "/Shared/mip/[dev someone] lead-scoring"

    # The refusal comes from the declaration changes, not from drift on the live side.
    for name, accepted in HISTORY:
        assert legacy_map.same_name("experiments", LEGACY, name) is accepted, name

    for live in (
        LEGACY,
        HISTORY[0][0],  # the first declaration, unprefixed
        "/Shared/mip/mip-lead-scoring",  # today's base name in the legacy folder
        "/Shared/mip/[dev someone] mip-lead-scoring",  # and its development-mode form there
        "[dev someone] /Shared/mip-lead-scoring",  # a prefix on the path, not on its basename
        _cli_presets_name(HISTORY[1][0], DEV_PREFIX),  # the root declaration's render
    ):
        assert not legacy_map.same_name("experiments", live, declared), live
    # What the plan does bind: the declared name, and its development-mode form in the same folder.
    assert legacy_map.same_name("experiments", declared, declared)
    assert legacy_map.same_name("experiments", _cli_presets_name(declared, DEV_PREFIX), declared)


def _refusal_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if " REFUSED " in line]


def test_the_bind_plan_refuses_the_legacy_shape_before_any_edit_and_binds_after_the_owners_rename(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    experiment = estate.object_of(KEY)
    experiment.names = {"name": FIXTURE_LEGACY}
    assert not legacy_map.same_name("experiments", FIXTURE_LEGACY, es.configured(KEY)["name"])
    cli = es.FakeBundleCli(estate)

    assert _main(estate, cli, ["plan", "--phase", "adopt"]) == 2
    refused = _refusal_lines(capsys.readouterr().err)
    assert len(refused) == 1, "the experiment's name is the one refusal"
    assert f"REFUSED renamed {KEY} (name): " in refused[0]
    for words in (
        "or its development-mode form in the same folder",
        "the owner gives the live object the bundle's name before the adoption",
        "docs/clients/first-client-cutover.md, step 3b",
        "the plan binds no other name",
    ):
        assert words in refused[0], words
    with pytest.raises(adopt.AdoptRefused) as stopped:
        adopt.apply("adopt", "0" * 64, es.inputs(), cli, estate, lease=Lease(), plan_dir=tmp_path)
    assert stopped.value.code == "plan_refused" and stopped.value.inverse_proven
    assert cli.edits() == [] and estate.bound() == {}, "refused by the read-only plan: nothing was bound"
    capsys.readouterr()

    # The owner's rename: the same object, the same id, the declared name.
    experiment.names = {"name": es.configured(KEY)["name"]}
    renamed = es.FakeBundleCli(estate)
    assert _main(estate, renamed, ["plan", "--phase", "adopt"]) == 0
    assert _refusal_lines(capsys.readouterr().err) == []
    plan = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate)
    before = len(estate.calls)
    result = adopt.apply("adopt", plan.sha256, es.inputs(), renamed, estate, lease=Lease(), plan_dir=tmp_path)

    assert len(plan.edits) == 16 and plan.expected_creates == es.NEW_KEYS
    assert estate.bound() == estate.legacy_ids and estate.bound()[KEY] == experiment.live_id
    assert result.gate.public_summary()["verdict"] == "passed"
    after = [(service, method) for service, method, _ in estate.calls[before:]]
    assert LOOKUP not in after, "the name is already the bundle's: the release has no experiment rename left"

    # Control: a development-mode name in the same folder binds too, and is then renamed by the
    # release, so the post-bind gate looks its new name up first.
    default = es.Estate()
    default_cli = es.FakeBundleCli(default)
    default_plan = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(default), default)
    adopt.apply("adopt", default_plan.sha256, es.inputs(), default_cli, default, lease=Lease(), plan_dir=tmp_path)
    assert LOOKUP in default.reads()


def test_a_renamed_app_is_sent_to_the_profile_and_never_to_a_rename_of_the_live_object(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An App, a Lakebase instance and a sync catalog are named by their id: another name is another object."""

    estate = es.Estate()
    estate.object_of("apps.mip_app").names["name"] = "another-app"

    assert _main(estate, es.FakeBundleCli(estate), ["plan", "--phase", "adopt"]) == 2
    refused = _refusal_lines(capsys.readouterr().err)

    assert len(refused) == 1 and "REFUSED renamed apps.mip_app (name): " in refused[0]
    assert "Correct the profile's name for it (docs/clients/first-client-cutover.md, step 4)" in refused[0]
    assert "the live object keeps its name" in refused[0]
    for owner_rename in ("step 3b", "the owner gives the live object"):
        assert owner_rename not in refused[0], owner_rename
    for kind in sorted(legacy_map.EXACT_NAME_KINDS):
        assert legacy_map.AdoptRefusal("renamed", f"{kind}.x").remedy == legacy_map.RENAMED_EXACT_REMEDY
    for kind in ("experiments", "jobs", "pipelines", "sql_warehouses", "dashboards"):
        assert legacy_map.AdoptRefusal("renamed", f"{kind}.x").remedy == legacy_map.REMEDIES["renamed"]


# --- the runbook's step 3b ------------------------------------------------------------------

_RENAME = "databricks experiments update-experiment "
_ID = "<legacy-experiment-id>"
_OWNER = "-p <owner-profile>"
_COUNTS = "jq '{holders: (.access_control_list | length), direct: "


def _blocks(section: str) -> list[list[str]]:
    return [block.splitlines() for block in re.findall(r"```bash\n(.*?)\n```", section, flags=re.DOTALL)]


def _new_name(command: str) -> str:
    argv = shlex.split(command.replace("<user>", "someone"))
    return argv[argv.index("--new-name") + 1]


def test_the_runbook_renames_to_the_declared_name_and_rolls_back_to_the_refused_shape() -> None:
    declared = _declared()
    raw = (REPO / RUNBOOK).read_text(encoding="utf-8")
    step = _section(raw, "## Step 3b. Rename the legacy MLflow experiment", "## Step 4.")
    blocks = _blocks(step)
    assert [len(block) for block in blocks] == [4, 1, 3, 1], "read first, rename, proof, rollback"
    read_first, rename, proof, rollback = blocks
    commands = [line for block in blocks for line in block]

    renames = [line for line in commands if line.startswith(_RENAME)]
    assert renames == [rename[0], rollback[0]], "exactly two renames: forward, and back"
    assert _new_name(rename[0]) == declared, "the forward rename gives the name databricks.yml declares"
    assert _new_name(rollback[0]) == LEGACY and "[dev <user>] " in rollback[0]
    assert not legacy_map.same_name("experiments", _new_name(rollback[0]), declared), "the rollback is the refused shape"
    for line in renames:
        assert line.startswith(f"{_RENAME}{_ID} --new-name ") and line.endswith(_OWNER) and " | " not in line, line
    for line in commands:
        assert line.startswith("databricks ") and f" {_OWNER}" in line, line
        assert _ID in line or f" {declared} " in line, line

    # The governed name is proven free by both reads, and never by a create call.
    assert f"databricks experiments get-by-name {declared} {_OWNER} -o json" in read_first
    assert f"databricks workspace get-status {declared} {_OWNER} -o json" in read_first
    flat = " ".join(step.split())
    assert "Never test a name with a create call" in flat and "create-experiment" not in "\n".join(commands)
    # The permission counts are read before and after, by the same projection (binding change 3).
    counts = [line for line in commands if line.startswith(f"databricks experiments get-permissions {_ID} ")]
    assert len(counts) == 2 and counts[0] == counts[1] and _COUNTS in counts[0]
    assert counts[0] in read_first and counts[1] in proof
    assert "inherited_from_object" in counts[0] and "user_name" not in counts[0] and "group_name" not in counts[0]
    for words in (
        "**under one condition**: every grant on that experiment was inherited",
        "the sub-folder had no ACL of its own",
        "the four permission counts equal the ones read first",
        "if the second prints a `direct` count other than 0",
        # Run from outside the checkout, as spike S8 measured it (binding change 2).
        "**Run every command of this step from a directory outside the checkout**",
        "so no bundle context can choose the host",
        # A failed proof stops the cutover (binding change 4).
        "**If the proof fails, stop: do not go on to step 7.**",
        "`same_id` false, or a `name` that is not exactly",
        "a create call at an existing name returns that experiment's id with exit 0",
        "a rename onto a name another experiment holds was not measured",
        "Roll back only to abandon the cutover",
        "After step 12 the bundle manages the experiment under the governed name: do not rename it back then.",
        "This step never deletes the legacy folder",
    ):
        assert words in flat, words
    same_id = next(line for line in proof if "same_id" in line)
    assert same_id.startswith(f"databricks experiments get-by-name {declared} ") and f'== "{_ID}"' in same_id

    # The dating: the sub-folder declaration ended on 2026-07-17, not with the 2026-10-27 change.
    names = [f"`{name}`" for name, _ in HISTORY]
    assert flat.index(names[0]) < flat.index(names[1]) < flat.index("and today's since 2026-10-27")
    assert f"{names[0]} until 2026-07-17" in flat and "An install first deployed before 2026-07-17" in flat
    assert f"`{LEGACY.replace('someone', '<user>')}`" in flat
    for stale in ("before 2026-10-27", "pre-2026-10-27"):
        assert stale not in _flat(RUNBOOK), stale
        assert stale not in _flat("docs/deployment.md"), stale


def test_the_sequence_waits_for_the_rename_and_says_what_a_rollback_leaves() -> None:
    runbook = _flat(RUNBOOK)
    blockers = _section(runbook, "## Blockers on main", "## Decisions taken")
    assert "**The owner renames one legacy resource (step 3b).**" in blockers
    assert "Step 1's snapshot (2026-11-12) predicted" in blockers and "with no bundle plan run" in blockers
    assert "That is the install's own state, not a code gap" in blockers
    assert "rollback-scope check is clean, step 3b is done" in blockers
    walk = _section(runbook, "**10B walked with", "Two smaller gaps are noted")
    assert "a legacy object whose live name is not the bundle's (`renamed`: the MLflow experiment, until step 3b)" in walk
    state = _section(runbook, "## The state this runbook starts from", "## Blockers on main")
    assert "**Step 1's read-only snapshot is taken** (an agent, 2026-11-12)." in state
    glance = _section(runbook, "## Steps at a glance", "## Step 1. Freeze and snapshot")
    assert "| 3b | Rename the legacy MLflow experiment | owner | ready; do and prove before step 7 |" in glance

    step_7 = _section(runbook, "## Step 7. Unity Catalog ownership handover", "## Step 8.")
    preconditions = _section(step_7, "**Preconditions:**", "**The rollback-scope check (read-only).**")
    assert "- Step 3b is done and proven: the legacy MLflow experiment carries the bundle's name." in preconditions
    assert "The reverts move owners only: the experiment keeps the name step 3b gave it" in step_7

    adopt_step = _section(runbook, "### 10B. Adopt (CI)", "## Step 11.")
    refusals = _section(adopt_step, "**Refusals:**", "**A Genie holder that changes mid-run.**")
    assert f"`renamed` on `{KEY}` means step 3b was skipped or rolled back" in refusals
    assert "nothing changed, the owner renames as step 3b says, and 10B-1 is dispatched again with steps 7 and 8 left as they are" in refusals
    assert "the profile does not carry the live install's name, step 4, and the live object is never renamed" in refusals
    rollback = _section(adopt_step, "**Rollback:** dispatch `phase=unadopt`", "While no signed record names the space")
    assert "Nothing is deleted. Nothing is renamed either: the experiment keeps its governed name (step 3b)" in rollback
    assert "because it binds the id" in rollback


# --- what else the snapshot corrected (g3, g4, g5) --------------------------------------------


def test_the_runbook_says_what_the_snapshot_found_at_steps_1_and_3_and_the_deployment_guide_keeps_16_keys_true() -> None:
    runbook = _flat(RUNBOOK)
    step_1 = _section(runbook, "## Step 1. Freeze and snapshot", "## Step 2.")
    listed = _section(step_1, "Take the read-only snapshot", "**Not in the read-only pass: the Postgres side.**")
    assert "the Lakebase instance, its sync catalog and its synced tables, through their control-plane reads" in listed
    for postgres in ("datdba", "relowner", "pg_auth_members", "the Lakebase owners and roles"):
        assert postgres not in listed, postgres
    side = step_1[step_1.index("**Not in the read-only pass: the Postgres side.**") :]
    for words in (
        "(`database list-database-instance-roles`) has to ask Postgres too",
        "That role list is the likeliest cause, not a proven one",
        "So leave the role list out of this pass.",
        "(`lakebase_handover --preconditions`, then `--plan`) reads the Postgres side with the legacy owner's own login",
    ):
        assert words in side, words

    step_3 = _section(runbook, "## Step 3. Supervisor rename", "## Step 3b.")
    rollback = step_3[step_3.index("**Rollback:**") :]
    assert "An endpoint that no longer exists has no ACL" in rollback
    assert "nothing is recreated to carry a grant" in rollback
    assert "the re-grant half has no object left to grant on here" in rollback
    assert "the rename back still applies if it exists" in rollback
    assert "the rollback of step 3 is empty" not in runbook, "the snapshot read the endpoint names, not the Supervisor"
    assert "rename back and re-grant from the step-1 snapshot" not in runbook

    deployment = _flat("docs/deployment.md")
    adopting = _section(deployment, "### Adopting an existing install (M2-PR6, bind only)", "### Signing the live deployment")
    assert (
        "binds 16 keys and plans exactly 2 creates (the two jobs the legacy install never had), once its "
        "legacy MLflow experiment carries the bundle's name (the cutover runbook's step 3b"
    ) in adopting
    assert "the owner renames the live object to the bundle's name before the adoption" in adopting
    assert "so there the remedy is the profile's name, never another name for the live object" in adopting
    for name, _ in HISTORY:
        assert f"`{name}`" in adopting, name
    # The owed measurement is the post-bind action, not the tags (the CLI's own lifecycle rules).
    owed = adopting[adopting.index("**Owed live confirmations.**") : adopting.index("**Dashboards (attack O9).**")]
    for words in (
        "ignore `tags` as a local and as a remote change",
        "apply a `name` change in place",
        "recreate only on `artifact_location`",
        "shows `skip`, not `recreate`, for an experiment whose declaration has no `artifact_location`",
        "What the bind writes as the state's old value is not established.",
    ):
        assert words in owed, words
    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    experiment = bundle["resources"]["experiments"]["mip_lead_scoring"]
    assert set(experiment) == {"name"}, "the declaration sets no artifact_location and no trace_location"
