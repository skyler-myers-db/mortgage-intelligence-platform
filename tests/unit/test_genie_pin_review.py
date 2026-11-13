"""The owner's review of a 10B-0 Genie pin plan prints counts and a digest only (tools/clients/genie_pin_review.py).

In CI the pin plan's candidate, managers and deployer are runner masks, so the
log shows ``***`` for each; the owner compares the counts this tool prints, as
the owner, with the masked line, and the holders with the profile's reviewed
Genie managers. The pin plan digest it recomputes with the profile's deployer
binds that review to the plan CI printed, and its count of grantees the
pre-stop owner-equivalent probe would refuse previews 10B-1. It never prints a
space id or a principal name, makes no write, and refuses (exit 2) any title
count but one, as the pin check does, and a shell without the profile's
deployer.

A space in a user's home folder gives that user CAN_MANAGE by inheritance, not
by a direct grant. The probe judges it like a direct one, and the review says
so: whether the caller inherits, how many reviewed managers hold their grant
another way or not at all, and how many refused grantees are inherited-only
(a grant no one can remove on the space itself).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound

from tests.fixtures.genie_pin_workspace import (
    APP,
    INSTANCE,
    SCOPE,
    TITLE,
    PinWorkspace,
    Space,
    patch_rollback_scope,
)
from tools.clients import genie_pin_review as review
from tools.databricks import genie_space_pin
from tools.databricks import prestop_owner_equivalent as probe

ROOT = Path(__file__).resolve().parents[2]
CREATOR = "creator@lender.example"
REVIEWED = "reviewed-principal@lender.example"
STRANGER = "unreviewed-principal@lender.example"
# The CI deployer the profile pins (MIP_IDENTITY_IDS_JSON.deployer): the pin plan's deploying identity.
CI_DEPLOYER = "00000000-0000-4000-8000-0000000000c1"


def _identity_ids(deployer: str = CI_DEPLOYER) -> str:
    return json.dumps({"deployer": deployer})


def _main(
    workspace: PinWorkspace,
    capsys: pytest.CaptureFixture[str],
    managers: str | None = None,
    *,
    owners: str | None = None,
    identity_ids: str | None = None,
    app_name: str = APP,
) -> tuple[int, str]:
    environ = {review.IDENTITY_IDS_ENV: _identity_ids() if identity_ids is None else identity_ids}
    if managers is not None:
        environ[review.MANAGERS_ENV] = managers
    if owners is not None:
        environ[review.OWNERS_ENV] = owners
    rc = review.main(
        ["--app-name", app_name, "--genie-space-name", TITLE], workspace_factory=lambda: workspace, environ=environ
    )
    out = capsys.readouterr()
    return rc, out.out + out.err


def test_one_title_match_prints_its_direct_manager_count_and_no_identifier(capsys: pytest.CaptureFixture[str]) -> None:
    workspace = PinWorkspace(
        spaces=[Space("governed-space-id", managers=(CREATOR, REVIEWED)), Space("other-space-id", title="Another title")],
        deployer=CREATOR,
    )

    rc, text = _main(workspace, capsys, managers=REVIEWED)

    assert rc == 0
    assert "Genie spaces carrying the governed title: 1" in text
    assert "direct CAN_MANAGE holders of that space: 2 (the caller is one: yes)" in text
    assert "reviewed Genie managers (MIP_GENIE_MANAGER_PRINCIPALS) among them: 1 of 1; holders outside that list: 1" in text
    assert "must list exactly 2 masked entries" in text
    for value in ("governed-space-id", "other-space-id", CREATOR, REVIEWED, "users", CI_DEPLOYER):
        assert value not in text, value
    assert workspace.mutations == [], "read-only"


def test_a_reviewed_manager_missing_and_an_unreviewed_holder_show_in_the_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = PinWorkspace(spaces=[Space("governed-space-id", managers=(CREATOR, STRANGER))], deployer=CREATOR)

    rc, text = _main(workspace, capsys, managers=f" {REVIEWED.upper()} , {CREATOR} ")

    assert rc == 0, "counts for the owner to judge, never a verdict on names"
    assert "among them: 1 of 2; holders outside that list: 1" in text
    for value in (CREATOR, REVIEWED, REVIEWED.upper(), STRANGER):
        assert value not in text, value


def test_the_inherited_holders_do_not_count_and_the_caller_may_be_none_of_them(capsys: pytest.CaptureFixture[str]) -> None:
    workspace = PinWorkspace(spaces=[Space("governed-space-id", managers=(REVIEWED,))], deployer=CREATOR)

    rc, text = _main(workspace, capsys)

    assert rc == 0 and "holders of that space: 1 (the caller is one: no)" in text
    assert "MIP_GENIE_MANAGER_PRINCIPALS is unset in this shell" in text and "among them" not in text


def test_the_reviewed_managers_come_from_the_process_environment(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(review.MANAGERS_ENV, REVIEWED)
    monkeypatch.setenv(review.IDENTITY_IDS_ENV, _identity_ids())
    workspace = PinWorkspace(spaces=[Space("governed-space-id", managers=(REVIEWED,))], deployer=CREATOR)

    rc = review.main(["--app-name", APP, "--genie-space-name", TITLE], workspace_factory=lambda: workspace)

    assert rc == 0 and "among them: 1 of 1; holders outside that list: 0" in capsys.readouterr().out


@pytest.mark.parametrize("count", (0, 2))
def test_any_count_but_one_refuses_as_the_pin_check_does(count: int, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = PinWorkspace(spaces=[Space(f"space-{index}", managers=(CREATOR,)) for index in range(count)])

    rc, text = _main(workspace, capsys, managers=CREATOR)

    assert rc == 2 and f"Genie spaces carrying the governed title: {count}" in text
    assert "REFUSED" in text and "holders" not in text
    assert not [f"space-{index}" for index in range(count) if f"space-{index}" in text]


def test_a_failed_read_prints_its_class_only(capsys: pytest.CaptureFixture[str]) -> None:
    workspace = PinWorkspace(spaces=[Space("governed-space-id")])

    def denied(method: str, path: str) -> None:
        raise PermissionError(f"denied on {path}")

    workspace.api_client.do = denied

    rc, text = _main(workspace, capsys)

    assert rc == 2 and "(PermissionError)" in text and "governed-space-id" not in text


# --- binding the review to the CI plan, and previewing 10B-1's probe --------------------------

OWNER = "owner@lender.example"  # the owner running the review: never a holder here
SPACE = "governed-space-id"


def _direct(level: str = "CAN_MANAGE", *, inherited: bool = False) -> list[dict[str, object]]:
    return [{"permission_level": level, "inherited": inherited}]


def _user(name: str, level: str = "CAN_MANAGE", *, inherited: bool = False) -> dict[str, object]:
    return {"user_name": name, "all_permissions": _direct(level, inherited=inherited)}


def _acl_workspace(*entries: dict[str, object], deployer: str = OWNER) -> PinWorkspace:
    """One governed-title space whose permissions are exactly ``entries``."""

    workspace = PinWorkspace(spaces=[Space(SPACE)], deployer=deployer)
    workspace.api_client.do = lambda method, path: {"access_control_list": list(entries)}
    return workspace


def _ci_digest(workspace: PinWorkspace, monkeypatch: pytest.MonkeyPatch) -> str:
    """The ``pin plan sha256`` the CI step-0 pin check prints, as the CI deployer, for this space."""

    patch_rollback_scope(monkeypatch)
    me = workspace.current_user.me
    workspace.current_user.me = lambda: SimpleNamespace(user_name=CI_DEPLOYER)
    try:
        with pytest.raises(genie_space_pin.GeniePinRefusal) as refused:
            genie_space_pin.resolve(workspace, app_name=APP, title=TITLE, scope=SCOPE, lakebase_instance=INSTANCE)
    finally:
        workspace.current_user.me = me
    assert refused.value.plan is not None
    return str(refused.value.plan["sha256"])


def _printed_digest(text: str) -> str:
    lines = [line for line in text.splitlines() if "pin plan sha256 for that space" in line]
    assert len(lines) == 1, text
    return lines[0].rsplit(": ", 1)[1]


def _unapproved(text: str) -> int:
    lines = [line for line in text.splitlines() if "does not approve" in line]
    assert len(lines) == 1, text
    return int(lines[0].rsplit(": ", 1)[1])


def test_the_recomputed_digest_is_the_ci_pin_plan_digest_only_for_the_holders_ci_saw(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stranger in the creator's place counts the same, but its plan's digest differs (G12)."""

    expected = _acl_workspace(_user(CREATOR), _user(REVIEWED))
    swapped = _acl_workspace(_user(STRANGER), _user(REVIEWED))
    ci = _ci_digest(expected, monkeypatch)
    capsys.readouterr()  # the CI run's own runner masks, not the review's output

    rc, text = _main(expected, capsys, managers=REVIEWED)
    rc_swapped, text_swapped = _main(swapped, capsys, managers=REVIEWED)
    _rc_app, text_app = _main(expected, capsys, managers=REVIEWED, app_name="another-app")

    assert rc == rc_swapped == 0
    assert _printed_digest(text) == ci, "the owner's digest is the one the CI log prints"
    assert _printed_digest(text_swapped) != ci and _printed_digest(text_app) != ci
    assert "must equal this one before it is passed as genie_pin" in text
    for value in (SPACE, CREATOR, STRANGER, REVIEWED, CI_DEPLOYER, OWNER, APP):
        assert value not in text + text_swapped, value


def test_the_digest_covers_the_profiles_deployer_not_the_caller(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even run as the CI identity, a profile naming another deployer gives another digest."""

    workspace = _acl_workspace(_user(CREATOR), _user(REVIEWED), deployer=CI_DEPLOYER)
    ci = _ci_digest(workspace, monkeypatch)
    capsys.readouterr()

    _rc, text = _main(workspace, capsys, managers=REVIEWED, identity_ids=_identity_ids("00000000-0000-4000-8000-0000000000d2"))

    assert _printed_digest(text) != ci


def test_an_upper_case_deployer_pin_still_prints_the_ci_digest(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile may pin the deployer's application id in upper case; the platform spells it lower.

    CI's pin plan covers ``current_user.me().user_name`` (lower case), and every deploy check
    compares the deployer case-insensitively (``deployer_principal`` is the pin lower-cased,
    tools/clients/profile.py). So a pin the deploy accepts must give the owner the digest CI
    printed: read with its case unchanged, the review's digest could never equal CI's, and
    10B-0 would dead-end on a correct profile.
    """

    workspace = _acl_workspace(_user(CREATOR), _user(REVIEWED))
    ci = _ci_digest(workspace, monkeypatch)
    capsys.readouterr()
    upper = CI_DEPLOYER.upper()
    assert upper != CI_DEPLOYER, "the premise: the pin differs from the platform's spelling by case only"

    rc, text = _main(workspace, capsys, managers=REVIEWED, identity_ids=_identity_ids(upper))
    rc_lower, text_lower = _main(workspace, capsys, managers=REVIEWED)
    _rc_other, text_other = _main(
        workspace, capsys, managers=REVIEWED, identity_ids=_identity_ids("00000000-0000-4000-8000-0000000000D2")
    )

    assert rc == rc_lower == 0
    assert _printed_digest(text) == ci == _printed_digest(text_lower), "one digest, whatever the pin's case"
    assert _printed_digest(text_other) != ci, "another deployer is still another plan, in any case"
    assert _unapproved(text) == _unapproved(text_lower), "the probe's preview compares case-folded already"
    for value in (CI_DEPLOYER, upper, SPACE, CREATOR, REVIEWED):
        assert value not in text, value


def _probe_refusals(workspace: PinWorkspace, *, owners: str, managers: str) -> int:
    """10B-1's owner-equivalent verdict on this space (the governed catalog absent)."""

    def absent(name: str) -> None:
        raise NotFound("no such catalog")

    refusals, _notes = probe.owner_equivalent_refusals(
        SimpleNamespace(catalogs=SimpleNamespace(get=absent)),
        catalog="mip",
        genie_space_id=SPACE,
        acls=[(SPACE, genie_space_pin.space_acl(workspace, SPACE))],
        approved=probe.approved_principals(approved_owners=owners, deployer=CI_DEPLOYER, genie_managers=managers),
    )
    assert all(refusal.code == "owner_equivalent" for refusal in refusals)
    return len(refusals)


@pytest.mark.parametrize(
    ("entries", "managers", "owners", "expected"),
    (
        pytest.param((_user(CREATOR), _user(REVIEWED)), REVIEWED, "", 1, id="an-unapproved-creator"),
        pytest.param((_user(CREATOR), _user(REVIEWED)), f"{REVIEWED}, {CREATOR.upper()}", "", 0, id="creator-approved"),
        pytest.param((_user(STRANGER), _user(REVIEWED)), f"{REVIEWED}, {CREATOR}", "", 1, id="a-stranger-in-its-place"),
        pytest.param(
            (
                _user(REVIEWED),
                {"service_principal_name": CI_DEPLOYER, "all_permissions": _direct()},
                {"group_name": "admins", "all_permissions": _direct()},
                {"group_name": "data-owners", "all_permissions": _direct(inherited=True)},
            ),
            REVIEWED,
            "data-owners",
            0,
            id="deployer-admins-and-an-approved-owner",
        ),
        pytest.param((_user(REVIEWED), _user(STRANGER, "CAN_EDIT")), REVIEWED, "", 1, id="an-unapproved-editor"),
        pytest.param(
            (_user(REVIEWED), {"group_name": "users", "all_permissions": _direct(inherited=True)}),
            REVIEWED,
            "",
            1,
            id="an-inherited-manager",
        ),
        pytest.param((_user(CREATOR), _user(REVIEWED)), "", "", 2, id="no-reviewed-managers"),
    ),
)
def test_the_unapproved_count_is_the_pre_stop_probes_own_verdict(
    entries: tuple[dict[str, object], ...],
    managers: str,
    owners: str,
    expected: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _acl_workspace(*entries)

    rc, text = _main(workspace, capsys, managers=managers, owners=owners)

    assert rc == 0
    assert _unapproved(text) == expected == _probe_refusals(workspace, owners=owners, managers=managers)
    assert ("10B-1 refuses each of them (owner_equivalent)" in text) == bool(expected)
    assert ("MIP_UC_APPROVED_OWNER_PRINCIPALS is unset in this shell" in text) == (not owners)
    for value in (SPACE, CREATOR, STRANGER, REVIEWED, CI_DEPLOYER, "data-owners"):
        assert value not in text, value


@pytest.mark.parametrize(
    "identity_ids",
    ("", json.dumps({"jobs": CI_DEPLOYER}), "not json " + CI_DEPLOYER, json.dumps({"deployer": "not-a-uuid-" + CREATOR})),
)
def test_a_shell_without_the_profiles_deployer_refuses_naming_no_value(
    identity_ids: str, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = _acl_workspace(_user(CREATOR), _user(REVIEWED))

    rc, text = _main(workspace, capsys, managers=REVIEWED, identity_ids=identity_ids)

    assert rc == 2 and "REFUSED" in text and "MIP_IDENTITY_IDS_JSON" in text
    assert "holders" not in text and "sha256" not in text
    for value in (CI_DEPLOYER, CREATOR, SPACE):
        assert value not in text, value


# --- inherited grants: the home-folder case (the snapshot's gap g2) -----------------------------

SERVICE = "00000000-0000-4000-8000-0000000000a7"  # another App's service principal: the one direct holder
LINES = {
    "inherits": "the caller holds CAN_EDIT or CAN_MANAGE on that space by inheritance only: ",
    "another_way": "another way (by inheritance, or a direct CAN_EDIT): ",
    "inherited_unapproved": "of them, by inheritance only: ",
    "rule": "go on only when the unapproved count above is 0 and no reviewed manager is with neither",
}


def _service(name: str, level: str = "CAN_MANAGE", *, inherited: bool = False) -> dict[str, object]:
    return {"service_principal_name": name, "all_permissions": _direct(level, inherited=inherited)}


def _direct_levels(entry: dict[str, object]) -> list[dict[str, object]]:
    levels = entry["all_permissions"]
    assert isinstance(levels, list)
    return levels


def _line(text: str, marker: str) -> str:
    lines = [line for line in text.splitlines() if marker in line]
    assert len(lines) == 1, (marker, text)
    return lines[0]


def _another_way(text: str) -> tuple[int, int]:
    """(reviewed managers holding another way, reviewed managers with neither)."""

    tail = _line(text, LINES["another_way"]).split(LINES["another_way"], 1)[1]
    other, absent = tail.split("; with neither: ")
    return int(other), int(absent)


def _probe_messages(workspace: PinWorkspace, *, owners: str, managers: str) -> list[str]:
    def absent(name: str) -> None:
        raise NotFound("no such catalog")

    refusals, _notes = probe.owner_equivalent_refusals(
        SimpleNamespace(catalogs=SimpleNamespace(get=absent), workspace=SimpleNamespace(get_status=absent)),
        catalog="mip",
        genie_space_id=SPACE,
        acls=[(SPACE, genie_space_pin.space_acl(workspace, SPACE))],
        approved=probe.approved_principals(approved_owners=owners, deployer=CI_DEPLOYER, genie_managers=managers),
    )
    return [refusal.message for refusal in refusals]


def test_a_space_in_the_owners_home_folder_passes_once_both_holders_are_reviewed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One direct holder and the owner's inherited CAN_MANAGE: ``1 of 2`` is the correct, passing review."""

    workspace = _acl_workspace(_service(SERVICE), _user(OWNER, inherited=True))
    managers = f"{SERVICE}, {OWNER}"

    rc, text = _main(workspace, capsys, managers=managers)

    assert rc == 0
    assert "direct CAN_MANAGE holders of that space: 1 (the caller is one: no)" in text
    assert _line(text, LINES["inherits"]).endswith(": yes")
    assert "among them: 1 of 2; holders outside that list: 0" in text
    assert _another_way(text) == (1, 0)
    assert _unapproved(text) == 0 == len(_probe_messages(workspace, owners="", managers=managers))
    assert LINES["inherited_unapproved"] not in text, "nothing is refused, so nothing is inherited-and-refused"
    assert text.rstrip().splitlines()[-1].endswith(
        f"{LINES['rule']}: direct and inherited holders are both judged, and only the direct ones are on the CI creator line"
    )
    assert "must list exactly 1 masked entries" in text, "the creator line lists the direct holder only"
    for value in (SPACE, SERVICE, OWNER, CI_DEPLOYER):
        assert value not in text, value


def test_an_unreviewed_home_folder_owner_is_refused_and_the_review_says_the_grant_is_inherited(
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _acl_workspace(_service(SERVICE), _user(OWNER, inherited=True))

    rc, text = _main(workspace, capsys, managers=SERVICE)

    messages = _probe_messages(workspace, owners="", managers=SERVICE)
    assert rc == 0 and _unapproved(text) == 1 == len(messages)
    assert "among them: 1 of 1; holders outside that list: 0" in text and _another_way(text) == (0, 0)
    assert _line(text, LINES["inherits"]).endswith(": yes")
    inherited = _line(text, LINES["inherited_unapproved"])
    assert inherited.split(LINES["inherited_unapproved"], 1)[1].startswith("1. ")
    assert "An inherited grant cannot be removed on the space itself" in inherited
    # The probe's own remedy for that grantee says the same.
    assert "the grant is inherited, so it cannot be removed on the space" in messages[0]
    for value in (SPACE, SERVICE, OWNER):
        assert value not in text, value


@pytest.mark.parametrize(
    ("entries", "managers", "direct", "other", "absent", "unapproved", "inherited"),
    (
        pytest.param((_service(SERVICE), _user(OWNER, inherited=True)), f"{SERVICE},{OWNER}", 1, 1, 0, 0, 0, id="home-folder"),
        pytest.param((_service(SERVICE),), f"{SERVICE},{REVIEWED}", 1, 0, 1, 0, 0, id="a-reviewed-manager-with-no-grant"),
        pytest.param((_service(SERVICE), _user(REVIEWED, "CAN_EDIT")), f"{SERVICE},{REVIEWED}", 1, 1, 0, 0, 0, id="a-direct-editor"),
        pytest.param(
            (_service(SERVICE), _user(STRANGER), _user(CREATOR, inherited=True)), SERVICE, 1, 0, 0, 2, 1,
            id="a-direct-stranger-and-an-inherited-one",
        ),
        pytest.param(
            (_service(SERVICE, inherited=True), _user(REVIEWED, "CAN_EDIT", inherited=True), _user(STRANGER, "CAN_RUN")),
            f"{SERVICE},{REVIEWED},{CREATOR},{STRANGER}", 0, 2, 2, 0, 0, id="all-inherited-and-a-runner",
        ),
        pytest.param((_user(STRANGER, inherited=True), _user(CREATOR, "CAN_EDIT", inherited=True)), REVIEWED, 0, 0, 1, 2, 2, id="only-inherited-strangers"),
        pytest.param((_service(SERVICE), _user(OWNER, "CAN_EDIT")), f"{SERVICE},{OWNER}", 1, 1, 0, 0, 0, id="the-caller-edits-directly"),
    ),
)
def test_the_three_reviewed_counts_add_up_and_the_inherited_refusals_are_counted(
    entries: tuple[dict[str, object], ...],
    managers: str,
    direct: int,
    other: int,
    absent: int,
    unapproved: int,
    inherited: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = _acl_workspace(*entries)
    reviewed = len(managers.split(","))

    rc, text = _main(workspace, capsys, managers=managers)

    assert rc == 0
    assert f"among them: {direct} of {reviewed}; " in text
    assert _another_way(text) == (other, absent)
    assert direct + other + absent == reviewed, "direct, another way and neither partition the reviewed list"
    assert _unapproved(text) == unapproved == len(_probe_messages(workspace, owners="", managers=managers))
    if unapproved:
        tail = _line(text, LINES["inherited_unapproved"]).split(LINES["inherited_unapproved"], 1)[1]
        assert int(tail.split(". ", 1)[0]) == inherited
    else:
        assert LINES["inherited_unapproved"] not in text
    assert LINES["rule"] in text.rstrip().splitlines()[-1]
    # The caller (the owner) inherits only when every level of the owner's own entry is inherited.
    own = [level for entry in entries if entry.get("user_name") == OWNER for level in _direct_levels(entry)]
    inherits = bool(own) and all(level["inherited"] for level in own)
    assert _line(text, LINES["inherits"]).endswith(": yes" if inherits else ": no")
    for value in (SPACE, SERVICE, CREATOR, REVIEWED, STRANGER, OWNER, CI_DEPLOYER):
        assert value not in text, value


def test_the_new_lines_keep_the_markers_the_other_lines_are_found_by(capsys: pytest.CaptureFixture[str]) -> None:
    """The unapproved count and the digest are each still exactly one line; no reviewed list, no reviewed counts."""

    workspace = _acl_workspace(_service(SERVICE), _user(OWNER, inherited=True))

    _rc, text = _main(workspace, capsys, managers=SERVICE)
    _rc, unset = _main(workspace, capsys)

    for marker in ("does not approve", "pin plan sha256 for that space", "among them"):
        assert len([line for line in text.splitlines() if marker in line]) == 1, marker
    assert "among them" not in unset and LINES["another_way"] not in unset
    assert _line(unset, LINES["inherits"]).endswith(": yes") and LINES["rule"] in unset


def test_the_runbooks_review_rule_quotes_the_reviews_own_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """10B-0's go-on rule is read against this output, so each line it quotes is one the tool prints."""

    workspace = _acl_workspace(_service(SERVICE), _user(OWNER, inherited=True))
    _rc, text = _main(workspace, capsys, managers=f"{SERVICE}, {OWNER}")
    runbook = " ".join((ROOT / "docs" / "clients" / "first-client-cutover.md").read_text(encoding="utf-8").split())
    rule = runbook[runbook.index("**10B-0, read-only: the Genie pin plan.**") : runbook.index("**10B-1, read-only: the adopt plans.**")]

    for quoted in ("the caller is one: no", "with neither: 0", "another way (by inheritance, or a direct CAN_EDIT): 1", "1 of 2"):
        assert f"`{quoted}`" in rule and quoted in text, quoted
    for words in (
        "**the unapproved count is 0.**",
        "direct or inherited",
        "That line lists the **direct** `CAN_MANAGE` holders only: an inherited holder is never on it",
        "An inherited grant cannot be removed on the space itself",
        "no reviewed manager is without a grant on the space",
    ):
        assert words in rule, words
    assert "every reviewed manager is among the holders" not in runbook, "unsatisfiable for a space in a home folder"
    step_2 = runbook[runbook.index("## Step 2. Owner decisions") : runbook.index("## Step 3.")]
    assert "by inheritance from the folder it lives in" in step_2 and "Settle this list before step 6" in step_2
    step_6c = runbook[runbook.index("## Step 6c.") : runbook.index("## Step 7.")]
    assert "python -m tools.clients.genie_pin_review --app-name <app-name>" in step_6c
    assert "the unapproved count is 0 and no reviewed manager is with neither" in step_6c
    step_7 = runbook[runbook.index("## Step 7. Unity Catalog ownership handover") : runbook.index("## Step 8.")]
    assert "- Step 6c's Genie review shows one title match, an unapproved count of 0 and no reviewed manager with neither." in step_7
    deployment = " ".join((ROOT / "docs" / "deployment.md").read_text(encoding="utf-8").split())
    assert "would refuse on that space, direct or inherited (an inherited grant" in deployment
