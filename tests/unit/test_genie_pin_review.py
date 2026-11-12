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
"""

from __future__ import annotations

import json
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
