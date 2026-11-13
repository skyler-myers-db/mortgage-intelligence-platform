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

Only ``holds`` lets the cutover start its ownership handover, so these also pin
what must never read as ``holds``: a set that carries the lease name (the blue
a CI adoption makes from it is the one the release then refuses
``frontend_ack_unpredictable``), and a measurement that did not complete, each
in the log and in the step summary an owner reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import rollback_records as rr
from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile, env_file_text
from tests.fixtures.core_blue_world import CoreWorld
from tests.fixtures.live_workspace import LiveWorkspace
from tests.unit.test_activation_prediction import (
    INPUTS,
    LEASE,
    NAMES,
    _predict,
    _profile_world,
    _Untouchable,
    _workspace,
    _world,
    builder,
)
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


# --- what must never read as ``holds`` ------------------------------------------------------


@pytest.fixture
def adopted_world(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub-builder world of tests/unit/test_activation_prediction.py: its keys, its owned scope."""

    rr.signing_env(monkeypatch)
    monkeypatch.setattr(ap, "_scope_names", lambda _workspace: {rr.SCOPE})
    monkeypatch.delenv(LEASE, raising=False)


def test_a_set_that_holds_the_lease_name_stops_before_step_7_and_never_reads_as_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A deployment scripts/deploy.sh made carries the lease name; CI adopts it unverified."""

    live, world, controls = _plan_shell(tmp_path, monkeypatch)
    world.add_deployment("deployment-governed", source=rr.LEGACY_FOLDER, env_names=sorted({*D0_NAMES, LEASE}))
    summary = tmp_path / "summary.md"

    code = ap.main(["env-names", "--step-summary", str(summary)], workspace_factory=lambda: _workspace(live, world.apps))

    printed = capsys.readouterr()
    assert code == 2, printed.out
    for text in (printed.err, summary.read_text(encoding="utf-8")):
        assert f"env-name parity is LEASE-BOUND: this profile rebuilds the App's newest deployment's 33 env names, but the set holds {LEASE}" in text
        assert "frontend_ack_unpredictable" in text and "no dispatch input clears that; do not start step 7" in text
    everything = printed.out + printed.err + summary.read_text(encoding="utf-8")
    assert "parity holds" not in everything and "no env-name difference" not in everything
    assert "[mip-activation-prediction]" not in printed.out, "a stop verdict is never the passing stream's"
    shown = "\n".join(line for line in everything.splitlines() if not line.startswith("::add-mask::"))
    for secret in ("deployment-governed", rr.LEGACY_FOLDER, *(value for value in controls.values() if len(value) > 8)):
        assert secret not in shown, secret
    assert world.apps.mutations == [] and world.secrets.puts == []


@pytest.mark.parametrize(
    ("live_names", "built_names", "verdict", "plan_code"),
    [
        (NAMES, NAMES, "same", "pass"),
        (sorted({*NAMES, LEASE}), sorted({*NAMES, LEASE}), "lease_bound", "frontend_ack_unpredictable"),
        (sorted({*NAMES, "MIP_OTEL_ENDPOINT"}), NAMES, "differs", "no_rollback_proof"),
        (NAMES, sorted({*NAMES, "MIP_OTEL_ENDPOINT"}), "differs", "no_rollback_proof"),
    ],
)
@pytest.mark.usefixtures("adopted_world")
def test_the_gate_before_step_7_holds_exactly_when_plan_3_would_print_the_frontend_digest(
    tmp_path: Path, live_names: list[str], built_names: list[str], verdict: str, plan_code: str
) -> None:
    """The same set, first unadopted (plans #1 and #2), then adopted in CI, so unverified (plan #3 and the release)."""

    before = _world(tmp_path / "before", names=live_names)
    before.secrets.values.clear()
    measured, message = ap.env_name_parity(before, app_name=rr.APP_NAME, payload_inputs=INPUTS, build=builder(built_names))
    adopted = _world(tmp_path / "adopted", names=live_names)
    plan = _predict(adopted, "plan", build=builder(built_names))
    release = _predict(adopted, "release", build=builder(built_names), frontend_ack=plan.frontend_ack or "0" * 64)

    assert (measured, plan.code) == (verdict, plan_code), message
    assert (measured == "same") is (plan.code == "pass" and plan.frontend_ack is not None) is (release.code == "pass")
    assert ("parity holds" in message) is (measured == "same") and "step" not in message.lower()


@pytest.mark.usefixtures("adopted_world")
def test_a_verdict_that_is_not_measured_reaches_the_step_summary_on_every_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def summary_of(label: str, factory: Any) -> str:
        summary = tmp_path / f"{label}.md"
        assert ap.main(["env-names", "--step-summary", str(summary)], workspace_factory=factory) == 2, label
        err = capsys.readouterr().err
        text = summary.read_text(encoding="utf-8")
        line = next(line for line in err.splitlines() if "env-name parity not measured: " in line)
        assert line.split("] ", 1)[1] in text and "do not start step 7" in text, (label, text)
        assert "### Activation prediction" in text and "parity holds" not in text + err
        return text

    # A read that fails: the class only, in the summary too.
    live, world = _profile_world(tmp_path / "read", monkeypatch)

    def unreadable(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("deployment 01f0-secret-id could not be listed")

    monkeypatch.setattr(world.apps, "list_deployments", unreadable)
    failed = summary_of("read", lambda: _workspace(live, world.apps))
    assert "the prediction could not complete (RuntimeError)" in failed and "01f0-secret-id" not in failed

    # A workspace that is not the profile's: nothing more is read.
    from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile
    from tests.fixtures.live_workspace import LiveWorkspace

    _profile_world(tmp_path / "stranger", monkeypatch)
    stranger = _workspace(LiveWorkspace(core_tier_profile(), identity_ids=IDENTITY_IDS, workspace_id="999"), _Untouchable())
    assert "the workspace or the deployer is not the profile's" in summary_of("stranger", lambda: stranger)

    # A profile that refuses: no workspace client at all.
    def never() -> Any:
        raise AssertionError("no workspace client without a resolved core profile")

    _profile_world(tmp_path / "broken", monkeypatch, MIP_APP_NAME=None)
    assert "the client profile refuses" in summary_of("broken", never)


def test_a_step_summary_that_cannot_be_written_is_not_measured_and_never_a_log_only_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live, world, _controls = _plan_shell(tmp_path, monkeypatch)
    nowhere = tmp_path / "no-such-folder" / "summary.md"

    assert ap.main(["env-names", "--step-summary", str(nowhere)], workspace_factory=lambda: _workspace(live, world.apps)) == 2

    printed = capsys.readouterr()
    assert "parity holds" not in printed.out + printed.err
    assert "env-name parity not measured: the prediction could not complete (FileNotFoundError)" in printed.err
    # The same App, with a summary that can be written, holds: only the write differed.
    assert ap.main(["env-names", "--step-summary", str(tmp_path / "s.md")], workspace_factory=lambda: _workspace(live, world.apps)) == 0
    assert "parity holds" in capsys.readouterr().out
    # A stop verdict keeps its names in the log even then: they are what the owner fixes.
    world.add_deployment("deployment-with-otel", source=rr.LEGACY_FOLDER, env_names=sorted({*D0_NAMES, "MIP_OTEL_ENDPOINT"}))
    assert ap.main(["env-names", "--step-summary", str(nowhere)], workspace_factory=lambda: _workspace(live, world.apps)) == 2
    printed = capsys.readouterr()
    assert "env-name parity DIFFERS" in printed.err and "(adds none; lacks MIP_OTEL_ENDPOINT)" in printed.err
    assert "do not start step 7" in printed.err and "parity holds" not in printed.out + printed.err


@pytest.mark.usefixtures("adopted_world")
def test_predicts_no_record_note_states_the_verdict_without_the_cutovers_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A release after an interrupted first install, or a plan after unadopt: no record, an App, no runbook."""

    from tests.unit.test_activation_prediction import _argv, _main

    rebuild = ap.core_blue.rebuild_for_env_names
    lacking = builder([name for name in NAMES if name != "APP_ENV"])
    monkeypatch.setattr(ap.core_blue, "rebuild_for_env_names", _with_build(rebuild, lacking))
    world = _world(tmp_path)
    world.secrets.values.clear()
    for phase in ("plan", "release", "complete"):
        summary = tmp_path / f"{phase}.md"
        assert _main(world, *_argv(phase, "--step-summary", str(summary))) == 0
        out = capsys.readouterr().out
        assert "PASS: no signed blue to protect; env-name parity DIFFERS" in out and "lacks APP_ENV" in out
        written = summary.read_text(encoding="utf-8") if summary.exists() else ""
        assert (phase == "plan") is bool(written)
        assert "step 7" not in (out + written).lower() and "step 12" not in (out + written).lower()

    governed = sorted({*NAMES, LEASE})
    monkeypatch.setattr(ap.core_blue, "rebuild_for_env_names", _with_build(rebuild, builder(governed)))
    world.add_deployment("deployment-governed", source=rr.LEGACY_FOLDER, env_names=governed)
    assert _main(world, *_argv("release")) == 0
    out = capsys.readouterr().out
    assert "PASS: no signed blue to protect; env-name parity is LEASE-BOUND" in out and "step" not in out.lower()


def _with_build(original: Any, build: Any) -> Any:
    def call(names: Any, **kwargs: Any) -> Any:
        return original(names, **{**kwargs, "build": build})

    return call
