"""Every fake Apps API updates an App the way spikes S2 and S2b measured, and the restore keeps the description.

A fake that answers the way the code hoped is the defect class wave 6 fixes:
``restore_signed_app_resource_contract`` sent ``{name, resources}`` through
``apps update`` and its fakes kept the description, while the real PATCH
cleared it (S2). Each fake below is driven through the measured shapes in
``tests/fixtures/measured_app_updates.py``; the restore is then proven on
them to keep the description (the App's and each kept binding's), to send
resources only when they differ, and to say that the send re-granted CREATE
ON DATABASE.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service.apps import App

from tests.fixtures import rollback_records as rr
from tests.fixtures.core_blue_world import CoreApps
from tests.fixtures.measured_app_updates import MissingSecretKey, UpdateLedger, apply_patch
from tests.fixtures.measured_apps import InvalidUpdateMask
from tests.fixtures.quiesce_window_estate import FakeApps, FakeFiles
from tools.databricks.app_rollback_resource_contract import (
    BINDING_DESCRIPTION_NOTICE,
    ROLE_REGRANT_NOTICE,
    restore_signed_app_resource_contract,
    validated_app_resource_contract,
)

DESCRIPTION = "Mortgage Intelligence Platform (synthetic)"
SIGNED = validated_app_resource_contract(copy.deepcopy(rr.APP_RESOURCES))
# What a killed run's candidate left: blue's bindings plus one it added.
CANDIDATE = [
    *SIGNED,
    {"name": "candidate_secret", "secret": {"scope": "candidate", "key": "headers", "permission": "READ"}},
]


def _window_estate_apps() -> tuple[Any, Any]:
    apps = FakeApps(files=FakeFiles(), env_names=["APP_ENV"], resources=copy.deepcopy(CANDIDATE))
    apps.description = DESCRIPTION
    return apps, apps


def _core_world_apps() -> tuple[Any, Any]:
    # No start in flight (measured_apps): CoreApps' reads tick the world first (S7).
    world = SimpleNamespace(
        resources=copy.deepcopy(CANDIDATE),
        description=DESCRIPTION,
        update_ledger=UpdateLedger(),
        in_flight=None,
        tick=lambda *, is_get: None,
    )

    def get(_name: str) -> SimpleNamespace:
        return SimpleNamespace(
            name=rr.APP_NAME,
            resources=copy.deepcopy(world.resources),
            description=world.description,
            active_deployment=SimpleNamespace(deployment_id="deployment-blue"),
            pending_deployment=None,
            compute_status=SimpleNamespace(state="ACTIVE"),
        )

    return CoreApps(world, SimpleNamespace(get=get)), world  # type: ignore[arg-type]


def _rollback_test_apps() -> tuple[Any, Any]:
    from tests.unit.test_app_deployment_rollback import _Apps, _deployment

    apps = _Apps([_deployment()])
    apps.resources = copy.deepcopy(CANDIDATE)
    apps.description = DESCRIPTION
    return apps, apps


FAKES: dict[str, Callable[[], tuple[Any, Any]]] = {
    "quiesce_window_estate": _window_estate_apps,
    "core_blue_world": _core_world_apps,
    "rollback_test": _rollback_test_apps,
}


@pytest.fixture(params=sorted(FAKES))
def fake(request: pytest.FixtureRequest) -> tuple[Any, Any]:
    return FAKES[request.param]()


def _ledger(state: Any) -> UpdateLedger:
    return state.update_ledger  # type: ignore[no-any-return]


def test_a_patch_without_a_description_clears_it_and_re_grants(fake: tuple[Any, Any]) -> None:
    apps, state = fake
    apps.update(rr.APP_NAME, App.from_dict({"name": rr.APP_NAME, "resources": SIGNED}))

    assert state.description == "", "S2: PATCH replaces the field it omits"
    assert state.resources == SIGNED
    assert _ledger(state).regrants == 1


def test_a_description_masked_create_update_changes_only_the_description(fake: tuple[Any, Any]) -> None:
    apps, state = fake
    before = copy.deepcopy(state.resources)
    waiter = apps.create_update(
        app_name=rr.APP_NAME, update_mask="description", app=App(name=rr.APP_NAME, description="new")
    )

    assert waiter.response.status.state == "IN_PROGRESS", "a long-running operation (S2b 4)"
    assert waiter.result().status.state == "SUCCEEDED"
    assert (state.description, state.resources) == ("new", before)
    assert _ledger(state).regrants == 0 and _ledger(state).masks == ["description"]


def test_a_resources_masked_create_update_keeps_the_description_and_re_grants(fake: tuple[Any, Any]) -> None:
    apps, state = fake
    apps.create_update(
        app_name=rr.APP_NAME,
        update_mask="resources",
        app=App.from_dict({"name": rr.APP_NAME, "resources": SIGNED}),
    ).result()

    assert (state.description, state.resources) == (DESCRIPTION, SIGNED)
    assert _ledger(state).regrants == 1, "S2: an identical or changed resources send re-grants"


def test_the_default_source_mask_is_refused(fake: tuple[Any, Any]) -> None:
    apps, state = fake
    with pytest.raises(InvalidUpdateMask):
        apps.create_update(
            app_name=rr.APP_NAME, update_mask="default_source_code_path", app=App(name=rr.APP_NAME)
        )
    assert _ledger(state).masks == [] and state.description == DESCRIPTION


def test_a_missing_secret_key_refuses_the_whole_update_atomically() -> None:
    state = SimpleNamespace(resources=copy.deepcopy(SIGNED), description=DESCRIPTION)
    ledger = UpdateLedger()
    with pytest.raises(MissingSecretKey):
        apply_patch(
            state,
            {"name": rr.APP_NAME, "resources": CANDIDATE},
            ledger,
            secret_keys=frozenset(),
        )
    assert (state.resources, state.description, ledger.regrants) == (SIGNED, DESCRIPTION, 0)


def test_the_restore_keeps_the_description_sends_only_a_difference_and_names_the_re_grant(
    fake: tuple[Any, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    apps, state = fake
    workspace = SimpleNamespace(apps=apps)

    assert restore_signed_app_resource_contract(workspace, app_name=rr.APP_NAME, resources=SIGNED) is True
    assert state.description == DESCRIPTION, "the restore never clears the description (S2)"
    assert validated_app_resource_contract(state.resources) == SIGNED
    assert (_ledger(state).patches, _ledger(state).masks, _ledger(state).regrants) == (0, ["resources"], 1)
    assert capsys.readouterr().err.strip() == ROLE_REGRANT_NOTICE

    assert restore_signed_app_resource_contract(workspace, app_name=rr.APP_NAME, resources=SIGNED) is False
    assert _ledger(state).regrants == 1, "an equal contract sends nothing, so re-grants nothing"
    assert capsys.readouterr().err == ""


def test_the_restore_refuses_when_the_description_changed_under_it() -> None:
    apps, state = _window_estate_apps()

    def clearing_create_update(app_name: str, update_mask: str, *, app: Any = None) -> Any:
        state.description = ""  # the PATCH-shaped outcome the restore must refuse
        state.resources = copy.deepcopy(SIGNED)
        return SimpleNamespace(result=lambda timeout=None: None)

    apps.create_update = clearing_create_update  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="description"):
        restore_signed_app_resource_contract(SimpleNamespace(apps=apps), app_name=rr.APP_NAME, resources=SIGNED)


# --- a binding's own description (unmeasured: the fakes take the worst case) ------------

LIVE_DESCRIPTIONS = {"genie_space": "Governed Genie space.", "sql_warehouse": "Serverless SQL warehouse."}


def _described(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**item, "description": LIVE_DESCRIPTIONS[item["name"]]} if item["name"] in LIVE_DESCRIPTIONS else item
        for item in copy.deepcopy(resources)
    ]


def _descriptions(resources: list[Any]) -> dict[str, str]:
    return {item["name"]: item["description"] for item in resources if item.get("description")}


def test_a_resources_send_that_omits_a_binding_description_drops_it(fake: tuple[Any, Any]) -> None:
    """No spike sent a binding without its description, so every fake must take the worst case.

    A fake that kept it by name would let a restore that sends the signed
    contract (which holds no description) look harmless.
    """

    apps, state = fake
    state.resources = _described(CANDIDATE)
    apps.create_update(
        app_name=rr.APP_NAME,
        update_mask="resources",
        app=App.from_dict({"name": rr.APP_NAME, "resources": SIGNED}),
    ).result()

    assert _descriptions(state.resources) == {}


def test_the_restore_re_sends_each_kept_binding_description(
    fake: tuple[Any, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    apps, state = fake
    state.resources = _described(CANDIDATE)

    assert restore_signed_app_resource_contract(
        SimpleNamespace(apps=apps), app_name=rr.APP_NAME, resources=SIGNED
    ) is True
    assert _descriptions(state.resources) == LIVE_DESCRIPTIONS, "kept bindings keep their own text"
    assert validated_app_resource_contract(state.resources) == SIGNED
    assert state.description == DESCRIPTION
    assert capsys.readouterr().err.strip() == ROLE_REGRANT_NOTICE


def test_a_binding_description_the_api_drops_anyway_is_a_names_only_notice_not_a_refusal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    apps, state = _window_estate_apps()
    state.resources = _described(CANDIDATE)

    def stripping_create_update(app_name: str, update_mask: str, *, app: Any = None) -> Any:
        state.resources = validated_app_resource_contract(copy.deepcopy(SIGNED))
        return SimpleNamespace(result=lambda timeout=None: None)

    apps.create_update = stripping_create_update  # type: ignore[method-assign]
    assert restore_signed_app_resource_contract(
        SimpleNamespace(apps=apps), app_name=rr.APP_NAME, resources=SIGNED
    ) is True
    err = capsys.readouterr().err
    assert f"{BINDING_DESCRIPTION_NOTICE}: genie_space, sql_warehouse" in err
    assert not any(text in err for text in LIVE_DESCRIPTIONS.values()), "names only"
