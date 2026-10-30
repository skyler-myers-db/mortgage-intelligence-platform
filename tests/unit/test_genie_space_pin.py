"""The governed Genie space is pinned by id, never adopted by its title (attack G12, M1-PR3).

Fixtures (tests/fixtures/genie_pin_workspace.py, all in memory; Genie is never
called):

* a squatter's space with the governed title, created by another principal,
  is refused, and the refusal prints the read-only pin plan and its digest;
* a pinned id is accepted, even beside a squatter's title match;
* a first create pins the new id;
* a wrong ack refuses with zero mutations, and the right one pins the space;
* the pin record and the signed rollback record must agree, a pinned space
  that is gone refuses instead of falling back to a title, and a pin record
  that is tampered with, or signed by another key, refuses.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.genie_pin_workspace import (
    APP,
    DEPLOYER,
    INSTANCE,
    SCOPE,
    TITLE,
    PinWorkspace,
    Space,
    patch_rollback_scope,
    signing_env,
    store_record,
)
from tools.databricks import genie_space_pin as pin

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "mip_provision_genie_space_pin", REPO / "tools" / "databricks" / "provision_genie_space.py"
)
assert _spec is not None and _spec.loader is not None
pgs = importlib.util.module_from_spec(_spec)
sys.modules["mip_provision_genie_space_pin"] = pgs
_spec.loader.exec_module(pgs)  # type: ignore[union-attr]

SQUATTER = "squatter@other-team.example"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in signing_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("MIP_DEFAULT_CATALOG", "mip")


def _resolve(workspace: PinWorkspace, ack: str = "") -> pin.Resolution:
    return pin.resolve(workspace, app_name=APP, title=TITLE, scope=SCOPE, lakebase_instance=INSTANCE, ack_plan=ack)


def _pinned(workspace: PinWorkspace, space_id: str, provenance: str = "created") -> None:
    signed = pin.sign_pin(
        {
            "version": 1,
            "app_name": APP,
            "title": TITLE,
            "space_id": space_id,
            "provenance": provenance,
            "plan_sha256": "",
            "pinned_at": "2026-10-30T00:00:00+00:00",
        }
    )
    workspace.secrets_by_key[pin.pin_key(APP)] = json.dumps(signed, sort_keys=True, separators=(",", ":"))


def _provision(workspace: PinWorkspace, monkeypatch: pytest.MonkeyPatch, *extra: str) -> int:
    monkeypatch.setattr(pgs, "_build_client", lambda args: workspace)
    monkeypatch.setattr(pgs, "_write_space_id", lambda space_id: None)
    return pgs.main(
        [
            "--space-name",
            TITLE,
            "--catalog",
            "mip",
            "--app-name",
            APP,
            "--pin-scope",
            SCOPE,
            "--lakebase-instance",
            INSTANCE,
            "--warehouse-id",
            "warehouse-alpha",
            "--no-smoke-test",
            *extra,
        ]
    )


# ----------------------------------------------------------------- the squatter


def test_a_squatters_title_match_by_another_creator_refuses_with_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("squat-space", managers=(SQUATTER,))])

    with pytest.raises(pin.GeniePinRefusal, match="never adopted by its title") as refused:
        _resolve(workspace)

    plan = refused.value.plan
    assert plan is not None
    assert plan["space_id"] == "squat-space" and plan["managers"] == [SQUATTER] and plan["deployer"] == DEPLOYER
    assert plan["sha256"] == pin.plan_sha256({key: value for key, value in plan.items() if key != "sha256"})
    assert "squat-space" not in str(refused.value), "the refusal itself names no id"
    assert workspace.mutations == []


def test_the_provisioner_refuses_the_squatter_with_zero_mutations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("squat-space", managers=(SQUATTER,))])

    code = _provision(workspace, monkeypatch)

    out = capsys.readouterr()
    assert code == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == []
    assert "read-only pin plan (nothing was changed):" in out.out
    assert "candidate space: squat-space" in out.out
    assert f"creator (direct CAN_MANAGE; the Genie API reports no creator): {SQUATTER}" in out.out
    assert "pin plan sha256: " in out.out


@pytest.mark.parametrize("ack", ["0" * 64, "not-a-digest"])
def test_a_wrong_ack_refuses_with_zero_mutations(ack: str, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("squat-space", managers=(SQUATTER,))])

    assert _provision(workspace, monkeypatch, "--ack-plan", ack) == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == []
    assert pin.pin_key(APP) not in workspace.secrets_by_key


def test_the_exact_ack_pins_the_reviewed_space_then_updates_it(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("legacy-space", managers=("owner@lender.example",))])
    with pytest.raises(pin.GeniePinRefusal) as refused:
        _resolve(workspace)
    assert refused.value.plan is not None
    digest = refused.value.plan["sha256"]

    assert _provision(workspace, monkeypatch, "--ack-plan", digest) == 0

    assert workspace.mutations == [f"secrets.put_secret:{pin.pin_key(APP)}", "genie.update_space:legacy-space"]
    stored = pin.read_pin(workspace, scope=SCOPE, app_name=APP)
    assert stored is not None
    assert (stored["space_id"], stored["provenance"], stored["plan_sha256"]) == ("legacy-space", "acked", digest)
    # Pinned now: the next run needs no ack.
    assert _resolve(workspace).action == "use"


def test_an_ack_for_a_plan_that_changed_since_review_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan is recomputed right before the write: a new manager on the space changes its digest."""

    patch_rollback_scope(monkeypatch)
    space = Space("legacy-space", managers=("owner@lender.example",))
    workspace = PinWorkspace(spaces=[space])
    with pytest.raises(pin.GeniePinRefusal) as refused:
        _resolve(workspace)
    assert refused.value.plan is not None
    reviewed = refused.value.plan["sha256"]
    space.managers = ("owner@lender.example", SQUATTER)

    assert _provision(workspace, monkeypatch, "--ack-plan", reviewed) == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == []


def test_more_than_one_title_match_refuses() -> None:
    workspace = PinWorkspace(spaces=[Space("a"), Space("b")], scope_exists=False)

    with pytest.raises(pin.GeniePinRefusal, match="2 Genie spaces carry the governed title"):
        _resolve(workspace)


# ----------------------------------------------------------------- pinned ids


def test_a_pinned_id_is_accepted_beside_a_squatters_title_match(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("governed-space"), Space("squat-space", managers=(SQUATTER,))])
    _pinned(workspace, "governed-space")

    resolution = _resolve(workspace)

    assert (resolution.action, resolution.space_id, resolution.source, resolution.write_pin) == (
        "use",
        "governed-space",
        "pin",
        None,
    )
    assert _provision(workspace, monkeypatch) == 0
    assert workspace.mutations == ["genie.update_space:governed-space"]


def test_a_signed_rollback_record_pins_the_space_and_seeds_the_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch, record_space="governed-space")
    workspace = PinWorkspace(spaces=[Space("governed-space"), Space("squat-space", managers=(SQUATTER,))])
    store_record(workspace, "governed-space")

    resolution = _resolve(workspace)
    assert (resolution.action, resolution.space_id, resolution.source, resolution.write_pin) == (
        "use",
        "governed-space",
        "record",
        "record",
    )
    assert _provision(workspace, monkeypatch) == 0
    assert workspace.mutations == [f"secrets.put_secret:{pin.pin_key(APP)}", "genie.update_space:governed-space"]
    assert _resolve(workspace).source == "pin"


def test_a_pin_and_a_record_that_disagree_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch, record_space="other-space")
    workspace = PinWorkspace(spaces=[Space("governed-space"), Space("other-space")])
    _pinned(workspace, "governed-space")
    store_record(workspace, "other-space")

    with pytest.raises(pin.GeniePinRefusal, match="name different spaces"):
        _resolve(workspace)


def test_a_pinned_space_that_is_gone_refuses_and_never_falls_back_to_the_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("squat-space", managers=(SQUATTER,))])
    _pinned(workspace, "deleted-space")

    with pytest.raises(pin.GeniePinRefusal, match="no longer exists"):
        _resolve(workspace)
    assert _provision(workspace, monkeypatch) == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == []


@pytest.mark.parametrize(
    "tamper",
    [
        lambda record: record.update(space_id="squat-space"),
        lambda record: record.update(provenance="anything"),
        lambda record: record.update(extra="field"),
        lambda record: record.pop("pinned_at"),
    ],
    ids=["space-id", "provenance", "extra-field", "missing-field"],
)
def test_a_tampered_pin_record_refuses(tamper: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("governed-space"), Space("squat-space")])
    _pinned(workspace, "governed-space")
    record = json.loads(workspace.secrets_by_key[pin.pin_key(APP)])
    tamper(record)
    workspace.secrets_by_key[pin.pin_key(APP)] = json.dumps(record)

    with pytest.raises(pin.GeniePinRefusal):
        _resolve(workspace)


def test_a_pin_signed_by_another_key_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("governed-space")])
    _pinned(workspace, "governed-space")
    for name, value in signing_env(start=64).items():
        monkeypatch.setenv(name, value)

    with pytest.raises(pin.GeniePinRefusal, match="not signed by this deploy's verification key"):
        _resolve(workspace)


def test_a_pin_cannot_be_verified_without_the_verify_key(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("governed-space")])
    _pinned(workspace, "governed-space")
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_VERIFY_KEY")

    with pytest.raises(pin.GeniePinRefusal, match="is not exported"):
        _resolve(workspace)


# ----------------------------------------------------------------- the first create


def test_a_first_create_pins_the_new_id(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace()

    assert _resolve(workspace).action == "create"
    assert _provision(workspace, monkeypatch) == 0

    assert workspace.mutations == ["genie.create_space", f"secrets.put_secret:{pin.pin_key(APP)}"]
    stored = pin.read_pin(workspace, scope=SCOPE, app_name=APP)
    assert stored is not None and (stored["space_id"], stored["provenance"]) == ("created-space-1", "created")
    # An interrupted run's next pass finds its own space by the pin, not by the title.
    again = _resolve(workspace)
    assert (again.action, again.space_id, again.source) == ("use", "created-space-1", "pin")


def test_a_first_create_without_the_signing_key_refuses_before_creating(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace()
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY")

    assert _provision(workspace, monkeypatch) == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == []


def test_a_first_install_without_the_scope_yet_creates_and_cannot_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """No scope means no pin and no record; the write then needs the scope and fails visibly."""

    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(scope_exists=False)

    assert _resolve(workspace).action == "create"


def test_plan_pin_is_read_only(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    patch_rollback_scope(monkeypatch)
    for spaces in ([], [Space("governed-space")]):
        workspace = PinWorkspace(spaces=list(spaces))
        if spaces:
            _pinned(workspace, "governed-space")
        assert _provision(workspace, monkeypatch, "--plan-pin") == 0
        assert workspace.mutations == []
    assert "read-only pin plan: nothing was changed" in capsys.readouterr().out


def test_no_title_only_adoption_path_remains() -> None:
    source = (REPO / "tools" / "databricks" / "provision_genie_space.py").read_text(encoding="utf-8")
    bundle_env = (REPO / "tools" / "databricks" / "bundle_env.py").read_text(encoding="utf-8")

    assert "_find_space" not in source and "list_spaces" not in source
    assert "list_spaces" not in bundle_env and "_resolve_governed_genie_space_id" not in bundle_env
    assert "genie_space_pin.resolve(" in source


# ----------------------------------------------------------------- the App's env


def test_the_app_env_name_set_is_unchanged_and_its_genie_id_is_the_pinned_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The emergency recipe gates on D0's exact 32 env names; retiring the input changes none.

    The App's GENIE_SPACE_ID stays a resource reference (``value_from:
    genie_space``), and databricks.yml binds that resource to
    ``${var.genie_space_id}``, which bundle_env sets from the pin alone
    (tests/unit/test_workspace_auth.py). An ambient GENIE_SPACE_ID never
    reaches the payload.
    """

    import yaml

    from tests.unit.test_settings_d0_env_parity import D0_NAME_COUNT, _d0_operator_config
    from tools.databricks.app_deploy_payload import build_payload

    def names_and_genie() -> tuple[list[str], list[dict[str, str]]]:
        payload = build_payload(source_code_path="/Workspace/app-src/release", install_class="demo")
        env_vars: list[dict[str, str]] = payload["env_vars"]  # type: ignore[assignment]
        return [item["name"] for item in env_vars], [item for item in env_vars if item["name"] == "GENIE_SPACE_ID"]

    _d0_operator_config(monkeypatch, tmp_path)
    names, genie = names_and_genie()
    assert len(names) == len(set(names)) == D0_NAME_COUNT
    assert genie == [{"name": "GENIE_SPACE_ID", "value_from": "genie_space"}]

    monkeypatch.setenv("GENIE_SPACE_ID", "ambient-retired-input")
    assert names_and_genie() == (names, genie)

    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    resources = bundle["resources"]["apps"]["mip_app"]["resources"]
    bound = [item for item in resources if item["name"] == "genie_space"]
    assert bound == [
        {
            "name": "genie_space",
            "description": "Mortgage Lead Intelligence Genie Space.",
            "genie_space": {
                "name": "mortgage_lead_intelligence",
                "space_id": "${var.genie_space_id}",
                "permission": "CAN_RUN",
            },
        }
    ]
