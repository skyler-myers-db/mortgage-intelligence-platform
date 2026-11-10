"""An adopted dashboard's folder is compared in one spelling, and a real move still refuses (spike S4).

Spike S4 read both live client-#1 dashboards (and its throwaway one) with
``lakeview.get``: ``parent_path`` came back ``/Users/<owner>/...``, while the
profile's ``MIP_DASHBOARD_PARENT_PATH`` must be ``/Workspace/...`` and the plan's
``new_state`` carries that form. The direct engine reports the remote folder
under ``/Workspace`` and plans no ``parent_path`` change between the two
spellings of one folder. The adopt map compared the raw strings, so it refused
``dashboard_parent_moved`` on both live dashboards; the fixture answered the
configured form, so no test saw it.

``adopt_legacy_map.canonical_folder`` aliases exactly ``/Users`` and
``/Shared`` to their ``/Workspace`` spelling (and drops a trailing slash):
nothing else, so a folder that differs in any other way still refuses. The
fixture's measured answers are pinned here, so it cannot drift back.
"""

from __future__ import annotations

import pytest

from tests.fixtures import adopt_estate as es
from tools.databricks import install_state_adopt as adopt
from tools.databricks.adopt_legacy_map import canonical_folder

OWNER = "legacy-owner@example.invalid"
FOLDER = f"/Users/{OWNER}/.bundle/mortgage-intelligence-platform/dev/resources"
DASHBOARDS = ("dashboards.mip_executive_dashboard", "dashboards.mip_segment_dashboard")


def _codes(estate: es.Estate) -> set[tuple[str, str]]:
    plan = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate)
    return {(refusal.code, refusal.resource) for refusal in plan.refusals}


# --- the measured answers the fixture models --------------------------------------


def test_the_fake_answers_the_folder_the_way_lakeview_get_did() -> None:
    estate = es.Estate()
    for resource in DASHBOARDS:
        live_id = estate.legacy_ids[resource]
        dashboard = estate.lakeview.get(dashboard_id=live_id)
        assert dashboard.parent_path == FOLDER, "lakeview.get answers /Users/..., never the /Workspace form"
        assert dashboard.path.startswith(f"{FOLDER}/") and dashboard.path.endswith(".lvdash.json")
    assert f"/Workspace{FOLDER}" == es.DASHBOARD_PARENT, "the profile and the plan carry the /Workspace form"


def test_the_fake_plan_reports_the_remote_folder_under_workspace_and_no_parent_change() -> None:
    estate = es.adopted_estate()
    plan = es.FakeBundleCli(estate).plan()
    entries = {entry.symbolic: entry for entry in plan.entries}
    for resource in DASHBOARDS:
        entry = entries[resource]
        assert entry.action == "update", "the development-mode display name drops; the folder stays"
        assert "parent_path" not in entry.changes, "one folder in two spellings is no change"
        assert entry.changes["display_name"].action == "update"
    assert es.plan_folder(FOLDER) == es.DASHBOARD_PARENT and es.live_folder(es.DASHBOARD_PARENT) == FOLDER


def test_the_fake_plan_recreates_a_moved_dashboard_and_reports_its_remote_under_workspace() -> None:
    estate = es.adopted_estate()
    estate.object_of(DASHBOARDS[0]).names["parent_path"] = f"/Users/{OWNER}/elsewhere"
    entry = {e.symbolic: e for e in es.FakeBundleCli(estate).plan().entries}[DASHBOARDS[0]]
    assert entry.action == "recreate", "parent_path is spec:immutable: a move is a recreate"
    assert entry.changes["parent_path"].remote == f"/Workspace/Users/{OWNER}/elsewhere"
    assert entry.changes["parent_path"].new == es.DASHBOARD_PARENT


# --- the comparison --------------------------------------------------------------


@pytest.mark.parametrize(
    ("live", "configured"),
    (
        (FOLDER, f"/Workspace{FOLDER}"),
        (f"/Workspace{FOLDER}", f"/Workspace{FOLDER}"),
        (f"{FOLDER}/", f"/Workspace{FOLDER}"),
        ("/Shared/dashboards", "/Workspace/Shared/dashboards"),
        ("/Shared", "/Workspace/Shared"),
    ),
    ids=("users-alias", "same-spelling", "trailing-slash", "shared-alias", "shared-root"),
)
def test_one_folder_in_either_spelling_is_one_folder(live: str, configured: str) -> None:
    assert canonical_folder(live) == canonical_folder(configured)


@pytest.mark.parametrize(
    ("live", "configured"),
    (
        (f"/Users/{OWNER}/elsewhere", f"/Workspace{FOLDER}"),
        (FOLDER.replace(OWNER, OWNER.upper()), f"/Workspace{FOLDER}"),
        (f"/Workspace/Workspace{FOLDER}", f"/Workspace{FOLDER}"),
        (f"/Repos{FOLDER}", f"/Workspace{FOLDER}"),
        (f"/Workspace/Repos{FOLDER}", f"/Workspace{FOLDER}"),
        ("/Shared/dashboards", f"/Workspace{FOLDER}"),
        (f"/Users{FOLDER}", f"/Workspace{FOLDER}"),
        (f"/UsersX/{OWNER}", f"/Workspace/UsersX/{OWNER}"),
    ),
    ids=("other-folder", "case-variant", "doubled-prefix", "repos-is-not-aliased", "repos-workspace",
         "shared-vs-users", "nested-users", "prefix-lookalike"),
)
def test_any_other_difference_is_another_folder(live: str, configured: str) -> None:
    assert canonical_folder(live) != canonical_folder(configured)


# --- the adoption ---------------------------------------------------------------


@pytest.mark.parametrize("spelling", ("live", "workspace"))
def test_adopt_accepts_the_legacy_folder_in_either_spelling(spelling: str) -> None:
    estate = es.Estate()
    if spelling == "workspace":
        for resource in DASHBOARDS:
            estate.object_of(resource).names["parent_path"] = es.DASHBOARD_PARENT
    assert _codes(estate) == set()


@pytest.mark.parametrize(
    "moved",
    (
        f"/Users/{OWNER}/elsewhere",
        "/Shared/elsewhere",
        FOLDER.replace(OWNER, OWNER.upper()),
        f"/Workspace/Users/{OWNER}/elsewhere",
    ),
    ids=("same-home-other-folder", "shared", "case-variant", "workspace-spelled-elsewhere"),
)
def test_a_genuinely_moved_dashboard_still_refuses(moved: str) -> None:
    estate = es.Estate()
    estate.object_of(DASHBOARDS[1]).names["parent_path"] = moved
    assert _codes(estate) == {("dashboard_parent_moved", DASHBOARDS[1])}
