"""An adopted install's profile: its legacy root, and its dashboards pinned inside it (M2-PR6).

Spike S4 has not shown whether a dashboard ``parent_path`` change updates in
place, so an adopted install's dashboards stay in their legacy folder:
``MIP_LEGACY_BUNDLE_ROOT`` requires ``MIP_DASHBOARD_PARENT_PATH`` inside it.
"""

from __future__ import annotations

import pytest

from tests.fixtures.client_profiles import IDENTITY_IDS, demo_profile
from tools.clients.environment import RawProfile, resolve
from tools.clients.errors import ProfileRefusal

LEGACY = "/Workspace/Users/legacy-owner@example.invalid/.bundle/mortgage-intelligence-platform/dev"


def _refusals(**overrides: str | None) -> list[str]:
    try:
        resolve(RawProfile(values=demo_profile(**overrides)))
    except ProfileRefusal as refusal:
        return list(refusal.lines)
    return []


def test_an_adopted_install_with_its_dashboards_in_the_legacy_root_resolves() -> None:
    assert _refusals(MIP_LEGACY_BUNDLE_ROOT=LEGACY, MIP_DASHBOARD_PARENT_PATH=f"{LEGACY}/resources") == []
    assert _refusals(MIP_LEGACY_BUNDLE_ROOT=None) == [], "a fresh install needs no legacy root"


@pytest.mark.parametrize(
    ("parent", "rule"),
    (
        (None, "is required when MIP_LEGACY_BUNDLE_ROOT is set"),
        ("/Workspace/Shared/dashboards-alpha", "must lie inside MIP_LEGACY_BUNDLE_ROOT"),
        (f"{LEGACY}-other/resources", "must lie inside MIP_LEGACY_BUNDLE_ROOT"),
    ),
    ids=("unset", "elsewhere", "a-sibling-prefix"),
)
def test_an_adopted_installs_dashboards_stay_in_the_legacy_root(parent: str | None, rule: str) -> None:
    lines = _refusals(MIP_LEGACY_BUNDLE_ROOT=LEGACY, MIP_DASHBOARD_PARENT_PATH=parent)

    assert len(lines) == 1 and lines[0].startswith("MIP_DASHBOARD_PARENT_PATH: ") and rule in lines[0], lines
    assert "spike S4" in lines[0]
    assert LEGACY not in lines[0], "a refusal names fields, never values"


def test_the_legacy_root_is_never_the_client_targets_own_root() -> None:
    own = f"/Workspace/Users/{IDENTITY_IDS['deployer']}/.bundle/mortgage-intelligence-platform/{demo_profile()['MIP_CLIENT']}"

    lines = _refusals(MIP_LEGACY_BUNDLE_ROOT=own, MIP_DASHBOARD_PARENT_PATH=f"{own}/resources")

    assert lines == [
        "MIP_LEGACY_BUNDLE_ROOT: must not be the client target's own root; it names the root an "
        "adopted install's state is bound from"
    ]


@pytest.mark.parametrize(
    "root",
    (
        "/Workspace/Shared/.bundle/mortgage-intelligence-platform/dev",
        "/Workspace/Users/owner@example.invalid/.bundle/another-bundle/dev",
        "/Workspace/Users/owner@example.invalid/.bundle/mortgage-intelligence-platform",
        f"{LEGACY}/files",
    ),
)
def test_the_legacy_root_must_be_a_bundle_root(root: str) -> None:
    lines = _refusals(MIP_LEGACY_BUNDLE_ROOT=root, MIP_DASHBOARD_PARENT_PATH=f"{root}/resources")

    assert any(line.startswith("MIP_LEGACY_BUNDLE_ROOT") and "bundle root" in line for line in lines), lines
