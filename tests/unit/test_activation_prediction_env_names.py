"""Blue's env-name parity at a plan's live checks, with the real payload builder (B3).

The cutover's plans before an adoption stop at the client live checks and
measure there, names only, whether the CI profile rebuilds the live App's
newest deployment's env-name set (``activation_prediction env-names``): the
gate before the ownership handover. tests/unit/test_activation_prediction.py
drives that command with a stub builder. These drive it with
``app_deploy_payload.build_payload`` in the shell such a plan really has at
that point: ``.env.local`` is the client profile, ``resolve_client_profile``
has exported its deploy controls, and nothing later in scripts/deploy.sh has
run, so the names the preflight and the automation step derive afterwards are
still unset. The live set is D0's 32 names, which the emergency recipe built.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile, env_file_text
from tests.fixtures.core_blue_world import CoreWorld
from tests.fixtures.live_workspace import LiveWorkspace
from tests.unit.test_activation_prediction import INPUTS, _workspace, _world
from tests.unit.test_settings_d0_env_parity import D0_NAMES, D0_OPERATOR_INPUTS, _d0_operator_config
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve
from tools.databricks import activation_prediction as ap
from tools.databricks import app_deploy_payload
from tools.databricks import app_rollback_record_contract as contract


@pytest.fixture(autouse=True)
def _owned_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contract, "assert_owned_app_rollback_scope", lambda *_a, **_kw: None)


def _plan_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, CoreWorld, dict[str, str]]:
    """The shell of a plan at its live checks, and a live App whose newest deployment is D0's."""

    _d0_operator_config(monkeypatch, tmp_path)  # no builder input from this machine
    for name in D0_OPERATOR_INPUTS:  # nor the emergency recipe's own shell: this is the governed one
        monkeypatch.delenv(name, raising=False)
    rr.signing_env(monkeypatch)  # the fixture world's own keys (the verify key rides no core payload)
    values = core_tier_profile()
    env_local = tmp_path / "profile.env.local"
    env_local.write_text(env_file_text(values), encoding="utf-8")
    monkeypatch.setattr(app_deploy_payload, "ENV_LOCAL", env_local)
    controls = views.deploy_controls(resolve(RawProfile(values=values)))
    for name, value in controls.items():  # resolve_client_profile exports these
        monkeypatch.setenv(name, value)
    world = _world(tmp_path / "world", names=sorted(D0_NAMES))
    world.secrets.values.clear()  # before an adoption: no record
    return LiveWorkspace(values, identity_ids=IDENTITY_IDS), world, controls


def test_the_ci_profile_rebuilds_d0s_32_names_before_the_shell_derives_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live, world, controls = _plan_shell(tmp_path, monkeypatch)
    not_yet = [name for name in ap.GOVERNED_DERIVED_NAMES if name not in controls]
    assert not_yet, "the profile's controls do not carry every name the shell derives later"
    summary = tmp_path / "summary.md"

    code = ap.main(["env-names", "--step-summary", str(summary)], workspace_factory=lambda: _workspace(live, world.apps))

    out = capsys.readouterr().out
    assert code == 0, out
    assert "env-name parity holds" in out and "32 env names exactly (no env-name difference)" in out
    assert "no env-name difference" in summary.read_text(encoding="utf-8")
    # Without the stand-ins the same plan would report a difference the release never has:
    # the preflight has not defaulted that name yet, and it always does before the activation.
    verdict, message = ap.env_name_parity(world, app_name=rr.APP_NAME, payload_inputs=INPUTS)
    assert verdict == "differs" and f"adds none; lacks {', '.join(sorted(not_yet))})" in message
    assert world.apps.mutations == [] and world.secrets.puts == []


def test_a_set_the_ci_profile_cannot_rebuild_differs_by_name_in_both_directions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live, world, controls = _plan_shell(tmp_path, monkeypatch)
    run = lambda: ap.main(["env-names"], workspace_factory=lambda: _workspace(live, world.apps))  # noqa: E731

    world.add_deployment("deployment-with-otel", source=rr.LEGACY_FOLDER, env_names=sorted({*D0_NAMES, "MIP_OTEL_ENDPOINT"}))
    assert run() == 2
    lacks = capsys.readouterr().err
    assert "env-name parity DIFFERS" in lacks and "(adds none; lacks MIP_OTEL_ENDPOINT)" in lacks

    world.add_deployment("deployment-without-rum", source=rr.LEGACY_FOLDER, env_names=sorted(set(D0_NAMES) - {"MIP_RUM_ENABLED"}))
    assert run() == 2
    adds = capsys.readouterr().err
    assert "(adds MIP_RUM_ENABLED; lacks none)" in adds and "do not start step 7" in adds
    for text in (lacks, adds):  # names only: no id, no folder, no profile value
        # Under GitHub Actions the identity proof registers each id it reads as a runner mask.
        shown = "\n".join(line for line in text.splitlines() if not line.startswith("::add-mask::"))
        for secret in ("deployment-with", rr.LEGACY_FOLDER, *(value for value in controls.values() if len(value) > 8)):
            assert secret not in shown, secret


def test_a_measurement_that_cannot_complete_is_not_measured_and_prints_its_class_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live, world, _controls = _plan_shell(tmp_path, monkeypatch)

    def unreadable(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("deployment 01f0-secret-id could not be listed")

    monkeypatch.setattr(world.apps, "list_deployments", unreadable)
    assert ap.main(["env-names"], workspace_factory=lambda: _workspace(live, world.apps)) == 2
    err = capsys.readouterr().err
    assert "env-name parity not measured: the prediction could not complete (RuntimeError)" in err
    assert "01f0-secret-id" not in err and "REFUSED" not in err
