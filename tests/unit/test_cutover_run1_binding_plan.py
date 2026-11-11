"""Cutover run 1 on client #1, as spike S2 read the live App: no WR1 window (S2 re-run).

Spike S2 (read-only ``apps get`` of the live App) found run 1's binding diff
on origin/main non-empty, for three reasons: the live description is empty
against the bundle's, the live App has 11 bindings against the bundle's 14
(the three optional Salesforce secret bindings missing live), and
``growth_agent_scheduler_job`` sits in another position. Each was a WR1
window, and every binding send re-grants CREATE ON DATABASE to the App role.
The same read shows a fourth difference the S2 summary did not print: the
three secret bindings ``cotality_id_mask_secret``,
``genie_action_current_secret`` and ``genie_action_previous_secret`` carry no
description of their own, while the bundle declares one for each. Every
other field of every live binding (kind, target, permission, secret key and
the other eight descriptions) equals the bundle's.

With this change that measured shape plans no window: the bindings compare
as a set (order is equal) on their access contract (a binding's own
description is display text: ``BINDING_DISPLAY_FIELDS``, reported apart),
the Salesforce bindings follow the profile's ``MIP_SALESFORCE_BINDINGS``
(off: client #1's eleven), and the App description is an online fix-up (a
create-update masked to description, no restart and no role change).
Switching the connector on is the one WR1 left, and its window names the
re-grant its migration job revokes. Every value is a placeholder; only the
measured shape (binding names, their order, which descriptions are missing)
is the spike's. Whether run 1 needs another window (WR2-WR4) depends on facts
the spike did not read (the live App role's ACL among them).
"""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreWorld, adopted_core_world
from tests.fixtures.quiesce_window_estate import FakeApps, FakeFiles
from tests.unit.test_app_optional_bindings import APP_NAME, bundle_summary
from tools.databricks import app_resource_bindings as bindings
from tools.databricks import app_rollback_record_contract as contract
from tools.databricks import quiesce_window_facts as facts
from tools.databricks.app_description_fixup import apply_bundle_description
from tools.databricks.quiesce_window_plan import WindowFacts, window_plan

NOOP = {
    "mode": "plan",
    "decision": "noop",
    "blue_restorable": True,
    "migrations": {"pending": []},
    "jobs_reader_grants": "exact",
    "app_role_regrant": "exact",
}


# The live bindings S2's read shows with no description of their own.
UNDESCRIBED = ("cotality_id_mask_secret", "genie_action_current_secret", "genie_action_previous_secret")


def _measured_live(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The live bindings S2 read: the scheduler last, three secrets undescribed, the rest the bundle's."""

    resources = copy.deepcopy(payload["resources"])
    scheduler = next(item for item in resources if item["name"] == "growth_agent_scheduler_job")
    live = [item for item in resources if item is not scheduler] + [scheduler]
    for item in live:
        if item["name"] in UNDESCRIBED:
            item.pop("description")
    return live


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    rr.signing_env(monkeypatch)
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)
    monkeypatch.setattr(facts.journal, "read", lambda *_a, **_kw: None)
    monkeypatch.setattr(facts, "app_or_none", lambda workspace, *, app_name: workspace.apps.get(app_name))


@pytest.fixture
def world(tmp_path: Path) -> CoreWorld:
    return adopted_core_world(tmp_path)


def _collect(world: CoreWorld, payload: dict[str, Any]) -> WindowFacts:
    return facts.collect_facts(
        world,
        app_name=rr.APP_NAME,
        scope=rr.SCOPE,
        lakebase_instance=rr.LAKEBASE,
        gate_report={"verdict": "passed", "instance_update_sha256": ""},
        binding_payload={**payload, "name": rr.APP_NAME},
        role_state="exact",
        verifier_role_state="absent",
        jobs_reader_role_state="exact",
        migration_plan=NOOP,
        rebase_unverified_app=False,
    )


def test_run_1_on_the_measured_live_shape_plans_no_window_and_one_online_fix_up(world: CoreWorld) -> None:
    payload = bindings.build_resource_binding_payload(
        bundle_summary(), app_name=APP_NAME, salesforce_bindings=False
    )
    assert all(item["description"] for item in payload["resources"]), "the bundle describes every binding"
    world.resources = _measured_live(payload)
    world.description = ""  # S2: the live description reads empty
    assert [item["name"] for item in world.resources] != [item["name"] for item in payload["resources"]]
    assert bindings.binding_description_drift(expected=payload, live={"resources": world.resources}) == sorted(
        UNDESCRIBED
    )

    found = _collect(world, payload)

    assert (found.binding_state, found.binding_lines, found.description_state) == ("equal", (), "differs")
    assert found.reasons() == ()
    plan = window_plan(found, position="pre-lease", acknowledgement="")
    assert (plan.code, plan.ack, plan.reasons) == ("none", "not_required", ())
    assert plan.public_summary()["online_fixups"] == ["description"]
    assert plan.public_summary()["app_role_regrant"] == []
    assert world.apps.mutations == [], "the plan is read-only"


def test_switching_the_connector_on_is_the_one_wr1_left_and_it_names_its_re_grant() -> None:
    on = bindings.build_resource_binding_payload(bundle_summary(), app_name=APP_NAME, salesforce_bindings=True)
    off = bindings.build_resource_binding_payload(bundle_summary(), app_name=APP_NAME, salesforce_bindings=False)
    live = {"name": APP_NAME, "description": "", "resources": _measured_live(off)}

    lines = bindings.binding_diff(expected=on, live=live)

    assert sorted(lines) == [
        "binding salesforce_client_secret: missing (update adds it)",
        "binding salesforce_password: missing (update adds it)",
        "binding salesforce_security_token: missing (update adds it)",
    ]
    tree = "a" * 64
    blue = WindowFacts(
        applicable=True,
        blue_deployment_id="deployment-blue-PLACEHOLDER",
        blue_current=True,
        restore_source="/Workspace/Users/deployer-PLACEHOLDER/app-src/release",
        env_names_sha256="b" * 64,
        source_tree_sha256=tree,
        artifact_tree_sha256=tree,
    )
    plan = window_plan(
        replace(blue, binding_state="diff", binding_lines=tuple(lines), description_state="differs"),
        position="pre-lease",
        acknowledgement="",
    )
    assert plan.reasons == ("WR1",) and plan.canonical["app_role_regrant"] == ["binding_send"]


def test_the_online_fix_up_changes_the_description_alone_on_the_measured_shape() -> None:
    off = bindings.build_resource_binding_payload(bundle_summary(), app_name=APP_NAME, salesforce_bindings=False)
    apps = FakeApps(files=FakeFiles(), env_names=["APP_ENV"], resources=_measured_live(off))
    before = apps._app()

    outcome = apply_bundle_description(
        SimpleNamespace(apps=_Named(apps)),
        app_name=APP_NAME,
        description=off["description"],
        identity=("app-object-id", "app-client", "app-scim-id"),
    )

    assert outcome == "applied" and apps.description == off["description"]
    assert apps.resources == before.resources, "a description mask leaves the bindings (S2b 4)"
    assert (apps.update_ledger.masks, apps.update_ledger.regrants, apps.update_ledger.patches) == (
        ["description"],
        0,
        0,
    )
    assert (apps.active_id, apps.pending_id, apps.compute) == ("deployment-blue", "", "ACTIVE")


class _Named:
    """``FakeApps`` under this test's placeholder App name (the fake answers for ``mip-app``)."""

    def __init__(self, apps: FakeApps) -> None:
        self._apps = apps

    def get(self, _name: str) -> Any:
        app = self._apps._app()
        body = app.as_dict()
        return SimpleNamespace(
            as_dict=lambda: {
                **body,
                "name": APP_NAME,
                "id": "app-object-id",
                "service_principal_client_id": "app-client",
                "service_principal_id": "app-scim-id",
                "compute_status": {"state": app.compute_status.state},
                "active_deployment": {"deployment_id": app.active_deployment.deployment_id},
            }
        )

    def create_update(self, app_name: str, update_mask: str, *, app: Any = None) -> Any:
        return self._apps.create_update(app_name, update_mask, app=app)
