"""The online description fix-up refuses every way it could change more than the description (S2, S2b).

``tools/databricks/app_description_fixup.py`` runs at every binding step with
no WR1. Spike S2b measured that a create-update masked to ``description``
changes only the description; the tool proves it after the update and
refuses otherwise. Each refusal below has a fake that does the thing the
tool must catch, built on the measured update (``measured_app_updates``) and
then perturbed. Every value is a placeholder.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.measured_app_updates import UpdateLedger, apply_create_update
from tools.databricks import app_description_fixup as fixup
from tools.databricks.app_description_fixup import DescriptionFixupRefused, apply_bundle_description

APP = "mip-app-PLACEHOLDER"
IDENTITY = ("app-object-id", "app-client", "app-scim-id")
BUNDLE = "Mortgage Intelligence Platform (synthetic)"
BINDINGS = [
    {"name": "sql_warehouse", "sql_warehouse": {"id": "warehouse-id", "permission": "CAN_USE"}},
    {"name": "cotality_id_mask_secret", "secret": {"scope": "s", "key": "k", "permission": "READ"}},
]


class _Apps:
    """One App; ``create_update`` is the measured one unless a test perturbs it after."""

    def __init__(self, *, description: str = "", perturb: Callable[[_Apps], None] | None = None) -> None:
        self.description = description
        self.resources = copy.deepcopy(BINDINGS)
        self.identity = IDENTITY
        self.active, self.pending, self.compute = "deployment-blue", "", "ACTIVE"
        self.ledger = UpdateLedger()
        self.perturb = perturb
        self.updates = 0

    def get(self, _name: str) -> SimpleNamespace:
        body = {
            "name": APP,
            "id": self.identity[0],
            "service_principal_client_id": self.identity[1],
            "service_principal_id": self.identity[2],
            "description": self.description,
            "resources": copy.deepcopy(self.resources),
            "compute_status": {"state": self.compute},
            "active_deployment": {"deployment_id": self.active} if self.active else None,
            "pending_deployment": {"deployment_id": self.pending} if self.pending else None,
        }
        return SimpleNamespace(as_dict=lambda: body)

    def create_update(self, app_name: str, update_mask: str, *, app: Any = None) -> Any:
        self.updates += 1
        waiter = apply_create_update(self, app_name, update_mask, app, self.ledger)
        if self.perturb is not None:
            self.perturb(self)
        return waiter


def _apply(apps: _Apps, description: str = BUNDLE) -> str:
    return apply_bundle_description(
        SimpleNamespace(apps=apps), app_name=APP, description=description, identity=IDENTITY
    )


def test_a_differing_description_is_applied_alone() -> None:
    apps = _Apps(description="")

    assert _apply(apps) == "applied"
    assert (apps.description, apps.resources, apps.ledger.masks, apps.ledger.regrants) == (
        BUNDLE,
        BINDINGS,
        ["description"],
        0,
    )


def test_an_equal_description_sends_nothing() -> None:
    apps = _Apps(description=BUNDLE)

    assert _apply(apps) == "equal" and apps.updates == 0


def test_no_bundle_description_and_none_live_is_equal_and_sends_nothing() -> None:
    """The shell runs the fix-up at every no-WR1 binding step, whatever the bundle declares."""

    apps = _Apps(description="")

    assert _apply(apps, description="") == "equal" and apps.updates == 0


def test_no_bundle_description_never_clears_a_live_one() -> None:
    apps = _Apps(description="Edited in the UI")

    with pytest.raises(DescriptionFixupRefused, match="never cleared"):
        _apply(apps, description="")
    assert apps.updates == 0 and apps.description == "Edited in the UI"


def test_a_foreign_identity_refuses_before_any_update() -> None:
    apps = _Apps(description="")
    apps.identity = ("another-app", "app-client", "app-scim-id")

    with pytest.raises(DescriptionFixupRefused, match="immutable identity"):
        _apply(apps)
    assert apps.updates == 0 and apps.description == ""


def _bindings_changed(apps: _Apps) -> None:
    apps.resources = list(reversed(apps.resources))[:1]


def _binding_text_changed(apps: _Apps) -> None:
    apps.resources[0]["description"] = "rewritten"


def _compute_changed(apps: _Apps) -> None:
    apps.compute = "STOPPED"


def _active_changed(apps: _Apps) -> None:
    apps.active = "deployment-other"


def _pending_appeared(apps: _Apps) -> None:
    apps.pending = "deployment-pending"


@pytest.mark.parametrize(
    "perturb",
    (_bindings_changed, _binding_text_changed, _compute_changed, _active_changed, _pending_appeared),
    ids=("bindings", "binding-text", "compute", "active-deployment", "pending-deployment"),
)
def test_an_update_that_changed_anything_but_the_description_refuses(perturb: Callable[[_Apps], None]) -> None:
    apps = _Apps(description="", perturb=perturb)

    with pytest.raises(DescriptionFixupRefused, match="changed the bindings, a deployment or the compute"):
        _apply(apps)


def test_an_update_that_left_another_description_refuses() -> None:
    def other(apps: _Apps) -> None:
        apps.description = "not the bundle's"

    with pytest.raises(DescriptionFixupRefused, match="not the bundle's after the update"):
        _apply(_Apps(description="", perturb=other))


def test_an_identity_that_changed_under_the_update_refuses() -> None:
    def swapped(apps: _Apps) -> None:
        apps.identity = ("app-object-id", "another-client", "app-scim-id")

    with pytest.raises(DescriptionFixupRefused, match="immutable identity"):
        _apply(_Apps(description="", perturb=swapped))


def test_the_cli_exits_2_on_a_refusal_and_never_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    apps = _Apps(description="")
    apps.identity = ("another-app", "app-client", "app-scim-id")
    import databricks.sdk

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda: SimpleNamespace(apps=apps))
    payload = tmp_path / "payload.json"
    payload.write_text(f'{{"name": "{APP}", "description": "{BUNDLE}"}}', encoding="utf-8")

    status = fixup.main(
        [
            "--app-name",
            APP,
            "--payload",
            str(payload),
            "--expected-app-id",
            IDENTITY[0],
            "--expected-client-id",
            IDENTITY[1],
            "--expected-scim-id",
            IDENTITY[2],
        ]
    )

    assert status == 2 and apps.updates == 0
    err = capsys.readouterr().err
    assert "refused: the App's immutable identity" in err and BUNDLE not in err
