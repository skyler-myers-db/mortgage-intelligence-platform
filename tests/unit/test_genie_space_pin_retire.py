"""Operator modes of the Genie pin: retire a gone space's pin, and update without a pin (M1-PR3).

A pinned space that is gone refuses every deploy and never falls back to a
title match (attack G12). Disaster recovery retires that pin through
``provision_genie_space --retire-pin``: read-only with ``--plan-pin``, and
otherwise only with ``--ack-plan`` equal to the retire plan's sha256,
recomputed right before the one write (a signed tombstone). After it, the
space resolves as on a first install, a rollback record naming the gone id is
superseded, and the gone id is never pinned again.

``--no-pin`` is the legacy live install's stand-alone spec update until it is
adopted: an acked title match is updated with no pin written, and it never
creates. scripts/deploy.sh passes neither mode.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tests.fixtures.genie_pin_workspace import (
    APP,
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
from tools.databricks import genie_space_pin_retire as retire

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "mip_provision_genie_space_retire", REPO / "tools" / "databricks" / "provision_genie_space.py"
)
assert _spec is not None and _spec.loader is not None
pgs = importlib.util.module_from_spec(_spec)
sys.modules["mip_provision_genie_space_retire"] = pgs
_spec.loader.exec_module(pgs)  # type: ignore[union-attr]

SQUATTER = "squatter@other-team.example"
GONE = "deleted-space"
PIN_PUT = f"secrets.put_secret:{pin.pin_key(APP)}"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in signing_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("MIP_DEFAULT_CATALOG", "mip")


def _target() -> dict[str, str]:
    return {"app_name": APP, "title": TITLE, "scope": SCOPE, "lakebase_instance": INSTANCE}


def _resolve(workspace: PinWorkspace, ack: str = "") -> pin.Resolution:
    return pin.resolve(workspace, ack_plan=ack, **_target())


def _pinned(workspace: PinWorkspace, space_id: str, *, retired: list[str] | None = None) -> None:
    signed = pin.sign_pin(
        {
            "version": 1,
            "app_name": APP,
            "title": TITLE,
            "space_id": space_id,
            "provenance": "retired" if not space_id else "created",
            "plan_sha256": "",
            "pinned_at": "2026-10-30T00:00:00+00:00",
            "retired_space_ids": retired or [],
        }
    )
    workspace.secrets_by_key[pin.pin_key(APP)] = json.dumps(signed, sort_keys=True, separators=(",", ":"))


def _provision(workspace: PinWorkspace, monkeypatch: pytest.MonkeyPatch, *extra: str) -> int:
    monkeypatch.setattr(pgs, "_build_client", lambda args: workspace)
    monkeypatch.setattr(pgs, "_write_space_id", lambda space_id: None)
    return pgs.main(
        [
            "--space-name", TITLE, "--catalog", "mip", "--app-name", APP, "--pin-scope", SCOPE,
            "--lakebase-instance", INSTANCE, "--warehouse-id", "warehouse-alpha", "--no-smoke-test", *extra,
        ]
    )  # fmt: skip


def _retire_digest(workspace: PinWorkspace) -> str:
    return str(retire.retire_plan(workspace, **_target())["sha256"])


# ----------------------------------------------------------------- disaster recovery: a deleted space


def test_a_deleted_pinned_space_is_recovered_by_an_acked_retire_never_by_its_title(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("squat-space", managers=(SQUATTER,))])
    _pinned(workspace, GONE)
    assert _provision(workspace, monkeypatch) == pgs.EXIT_PIN_REFUSED
    assert "--retire-pin --plan-pin" in capsys.readouterr().err

    # 1. The read-only retire plan.
    assert _provision(workspace, monkeypatch, "--retire-pin", "--plan-pin") == 0
    out = capsys.readouterr().out
    digest = _retire_digest(workspace)
    assert f"retire the pin of: {GONE} (named by the pin; the space is gone)" in out
    assert f"retire plan sha256: {digest}" in out and "read-only retire plan: nothing was changed" in out
    # 2. No ack, or another plan's, changes nothing.
    for ack in ((), ("--ack-plan", "0" * 64)):
        assert _provision(workspace, monkeypatch, "--retire-pin", *ack) == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == []
    # 3. The exact ack writes the signed tombstone, and nothing else.
    assert _provision(workspace, monkeypatch, "--retire-pin", "--ack-plan", digest) == 0
    assert workspace.mutations == [PIN_PUT]
    tombstone = pin.read_pin(workspace, scope=SCOPE, app_name=APP)
    assert tombstone is not None
    assert (tombstone["provenance"], tombstone["space_id"], tombstone["retired_space_ids"]) == ("retired", "", [GONE])
    assert tombstone["plan_sha256"] == digest
    # 4. The squatter's title match is still never adopted silently.
    with pytest.raises(pin.GeniePinRefusal, match="never adopted by its title"):
        _resolve(workspace)


def test_after_the_retire_the_next_run_creates_and_pins_carrying_the_retired_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace()
    _pinned(workspace, GONE)
    retire.retire(workspace, ack_plan=_retire_digest(workspace), **_target())

    assert _resolve(workspace).action == "create"
    assert _provision(workspace, monkeypatch) == 0

    stored = pin.read_pin(workspace, scope=SCOPE, app_name=APP)
    assert stored is not None
    assert (stored["space_id"], stored["provenance"], stored["retired_space_ids"]) == ("created-space-1", "created", [GONE])


def test_a_rollback_record_naming_the_retired_space_is_superseded(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch, record_space=GONE)
    workspace = PinWorkspace()
    store_record(workspace, GONE)
    plan = retire.retire_plan(workspace, **_target())
    assert (plan["space_id"], plan["source"]) == (GONE, "record")

    retire.retire(workspace, ack_plan=str(plan["sha256"]), **_target())
    assert _provision(workspace, monkeypatch) == 0

    # The pin (the new space) and the record (the retired one) no longer disagree.
    again = _resolve(workspace)
    assert (again.action, again.space_id, again.source) == ("use", "created-space-1", "pin")


def test_a_retired_id_with_the_governed_title_is_never_adopted_again(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space(GONE)])  # restored after its pin was retired
    _pinned(workspace, "", retired=[GONE])

    assert _resolve(workspace).action == "create"


@pytest.mark.parametrize(
    ("spaces", "pinned", "refusal"),
    [
        ([Space(GONE)], GONE, "still exists"),
        ([], "", "nothing pins a governed Genie space"),
    ],
    ids=["space-still-exists", "nothing-pinned"],
)
def test_a_retire_refuses_unless_a_pinned_space_is_gone(
    spaces: list[Space], pinned: str, refusal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=spaces)
    if pinned:
        _pinned(workspace, pinned)

    with pytest.raises(pin.GeniePinRefusal, match=refusal):
        retire.retire_plan(workspace, **_target())
    assert _provision(workspace, monkeypatch, "--retire-pin", "--ack-plan", "0" * 64) == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == []


def test_a_retire_ack_for_a_plan_that_changed_since_review_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan is recomputed right before the write: another deploying identity changes its digest."""

    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace()
    _pinned(workspace, GONE)
    reviewed = _retire_digest(workspace)
    workspace.deployer = "someone-else@example.invalid"

    with pytest.raises(pin.GeniePinRefusal, match="not acked"):
        retire.retire(workspace, ack_plan=reviewed, **_target())
    assert workspace.mutations == []


@pytest.mark.parametrize(
    "tamper",
    [
        lambda record: record.update(retired_space_ids=["b-space", "a-space"]),
        lambda record: record.update(retired_space_ids=["governed-space"]),
        lambda record: record.update(retired_space_ids="deleted-space"),
        lambda record: record.update(provenance="retired"),
    ],
    ids=["unsorted", "retires-the-pinned-id", "not-a-list", "tombstone-naming-a-space"],
)
def test_a_pin_record_with_a_malformed_retired_list_refuses(tamper: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("governed-space")])
    _pinned(workspace, "governed-space")
    record = json.loads(workspace.secrets_by_key[pin.pin_key(APP)])
    tamper(record)
    resigned = pin.sign_pin({key: value for key, value in record.items() if not key.startswith("attestation_")})
    workspace.secrets_by_key[pin.pin_key(APP)] = json.dumps(resigned)

    with pytest.raises(pin.GeniePinRefusal, match="does not name this App's governed space"):
        _resolve(workspace)


# ----------------------------------------------------------------- the legacy live install: --no-pin


def test_no_pin_updates_the_acked_space_and_writes_no_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace(spaces=[Space("legacy-space", managers=("owner@lender.example",))])
    with pytest.raises(pin.GeniePinRefusal) as refused:
        _resolve(workspace)
    assert refused.value.plan is not None
    digest = refused.value.plan["sha256"]
    monkeypatch.delenv("MIP_AI_GATEWAY_PROOF_SIGNING_KEY")  # no pin write, so no signing key

    assert _provision(workspace, monkeypatch, "--no-pin") == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == [], "the ack is still required"
    assert _provision(workspace, monkeypatch, "--no-pin", "--ack-plan", digest) == 0
    assert workspace.mutations == ["genie.update_space:legacy-space"]
    assert pin.pin_key(APP) not in workspace.secrets_by_key


def test_no_pin_never_creates_and_is_exclusive_with_retire(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_rollback_scope(monkeypatch)
    workspace = PinWorkspace()

    assert _provision(workspace, monkeypatch, "--no-pin") == pgs.EXIT_PIN_REFUSED
    assert _provision(workspace, monkeypatch, "--no-pin", "--retire-pin") == pgs.EXIT_PIN_REFUSED
    assert workspace.mutations == []


def test_the_deploy_passes_neither_operator_mode() -> None:
    text = deploy_entrypoint_text()

    assert "provision_genie_space" in text, "positive control"
    for flag in ("--no-pin", "--retire-pin"):
        assert flag not in text, flag
