"""v5/v6 step 0a's tier a never leaves an unproven start serving (spike S7).

``app_deployment_rollback ensure`` reads a STOPPED App by its newest
deployment (S7), so tier a is reachable on the measured API. A start
snapshots the CURRENT bytes of blue's folder, so a changed folder makes a
start that is not blue. Such a start, or any other that fails its proof (a
FAILED install, a snapshot that is not blue's), is tier b's:
``restore_last_good`` replaces it with the signed snapshot, as its own tier a
already did. A v5/v6 record always carries that snapshot, and tier b starts
a stopped App itself before its redeploy, so no pre-start re-hash would change
the outcome (the v7 core path has one: its record may carry no restore
payload). Against the review's probe on the branch before this fix, a changed
folder was started, refused ``start_tree_digest`` and left the App ACTIVE on it.
"""

from __future__ import annotations

import pytest

from tests.fixtures.quiesce_window_estate import RELEASE, Estate, StartBehaviour, stored_record
from tests.unit.test_quiesce_window_start_repin import (  # noqa: F401 - the autouse env
    GIT_SHA,
    LEASE_ID,
    SCOPE,
    _env,
    _estate,
)
from tools.databricks import app_deployment_rollback as rollback

APP_NAME = "mip-app"


def _ensure(estate: Estate) -> str:
    return rollback.ensure_current(
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


def _drift(estate: Estate) -> None:
    estate.files.put_tree(RELEASE, {"backend/runtime.py": b"print('not blue')\n"})


def test_a_stopped_blue_whose_folder_changed_is_restored_by_tier_b() -> None:
    estate = _estate()
    estate.apps.compute = "STOPPED"
    _drift(estate)

    _ensure(estate)

    record = stored_record(estate)
    assert record["deployment_id"] == estate.apps.active_id == "deployment-redeploy-1"
    assert estate.apps.compute == "ACTIVE", "blue's signed snapshot serves in the end"
    # Tier b deploys only to a running App: its start runs the changed folder's
    # bytes until the signed snapshot's redeploy replaces them.
    assert (estate.apps.starts, estate.apps.deploys) == (1, 1)


@pytest.mark.parametrize(
    "behaviour",
    (StartBehaviour(state="FAILED"), StartBehaviour(tamper_snapshot=True)),
    ids=("failed-install", "snapshot-not-blue"),
)
def test_an_unproven_start_is_replaced_by_tier_b(behaviour: StartBehaviour) -> None:
    estate = _estate(behaviour)
    estate.apps.compute = "STOPPED"

    _ensure(estate)

    assert (estate.apps.starts, estate.apps.deploys) == (1, 1), "one start, replaced by one redeploy"
    assert stored_record(estate)["deployment_id"] == estate.apps.active_id == "deployment-redeploy-1"

