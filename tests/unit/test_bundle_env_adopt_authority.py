"""bundle_env edits deployment state for a non-App key only under an acknowledged adopt plan (M2-PR6).

``bundle_env deployment bind|unbind`` admits the governed App binding, as
before, and otherwise exactly one bind or unbind that an adopt plan names. The
plan file and its sha256 arrive as wrapper-only flags from the caller, so they
prove nothing alone (anyone can digest a document they wrote): the digest
must also be the operator's ``MIP_ADOPT_PLAN_ACK`` in bundle_env's own
environment, the plan must name the resolved profile's install, and the
profile App's signed lease (whose evidence the caller passes) must be held.
``--force-lock`` is never admitted under a plan.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures import adopt_estate as es
from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tools.clients.environment import KNOWN_NAMES, from_local, resolve
from tools.databricks import bundle_env, bundle_env_adopt
from tools.databricks import install_state_adopt as adopt
from tools.databricks.adopt_legacy_map import PLAN_SCHEMA
from tools.databricks.bundle_plan_shape import sha256_hex

APP = "synthetic-mip_app"
# The client profile whose install es.inputs() describes (its deployer owns es.DEPLOYER_ROOT).
PROFILE = SimpleNamespace(
    client="client-one",
    identity_ids=SimpleNamespace(deployer="00000000-0000-4000-8000-00000000d0e1"),
    legacy_bundle_root=es.LEGACY_ROOT,
    dashboard_parent_path=es.DASHBOARD_PARENT,
    app_name=APP,
)
LEASE = ("lease-1", "0" * 40)


class HeldLease:
    """``held_lease`` for the verifier: records what it asserts, and raises when not held."""

    def __init__(self, *, held: bool = True) -> None:
        self.held = held
        self.asserted: list[tuple[str, str, str]] = []

    def __call__(self, app_name: str, authority: Any) -> Any:
        def check() -> None:
            self.asserted.append((app_name, authority.lease_id, authority.source_git_sha))
            if not self.held:
                raise RuntimeError("App deployment lease was released")

        return check


def _authority(path: Path, document: dict[str, Any]) -> adopt.Authority:
    path.write_text(json.dumps(document), encoding="utf-8")
    return adopt.Authority(path, sha256_hex(document), *LEASE)


@pytest.fixture
def acked(tmp_path: Path) -> tuple[adopt.Authority, adopt.legacy_map.AdoptPlan]:
    estate = es.Estate()
    plan = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate)
    return _authority(tmp_path / "adopt-plan.json", dict(plan.document)), plan


def _check(
    args: list[str],
    authority: adopt.Authority,
    *,
    ack: str | None = None,
    lease: HeldLease | None = None,
    profile: Any = PROFILE,
) -> str | None:
    """bundle_env's verdict with ``ack`` exported (the authority's own digest unless given)."""

    environ = {bundle_env_adopt.ACK_ENV: authority.sha256 if ack is None else ack}
    verifier = bundle_env_adopt.edit_verifier(authority, profile, environ=environ, held_lease=lease or HeldLease())
    return bundle_env._validate_app_deployment_command(args, expected_app_name=APP, adopt_edit=verifier)


def _job(plan: object) -> Any:
    return next(edit for edit in plan.edits if edit.resource.startswith("jobs."))  # type: ignore[attr-defined]


def test_the_profile_stub_names_the_estates_install() -> None:
    assert adopt.inputs_from_profile(PROFILE) == es.inputs()


def test_exactly_the_planned_bind_and_its_inverse_are_admitted(acked: tuple[adopt.Authority, object]) -> None:
    authority, plan = acked
    job = _job(plan)
    lease = HeldLease()

    assert _check(["bind", job.key, job.live_id, "-t", "client", "--auto-approve"], authority, lease=lease) is None
    assert _check(["unbind", job.key, "-t", "client"], authority, lease=lease) is None
    assert _check(["bind", "mip_app", APP, "-t", "client", "--auto-approve"], authority, lease=lease) is None
    assert lease.asserted == [(APP, *LEASE)] * 3, "the profile App's lease, with the evidence passed"


@pytest.mark.parametrize(
    "args",
    (
        ["bind", "{key}", "999999", "-t", "client", "--auto-approve"],
        ["bind", "mip_scheduled_refresh", "1", "-t", "client", "--auto-approve"],
        ["bind", "{key}", "{id}", "-t", "ci", "--auto-approve"],
        ["bind", "{key}", "{id}", "extra", "-t", "client"],
        ["bind", "{key}", "{id}", "-t", "client", "--plan", "x"],
        ["bind", "mip_app", "another-app", "-t", "client", "--auto-approve"],
        ["unbind", "mip_scheduled_refresh", "-t", "client"],
        ["bind", "{key}", "{id}", "-t", "client", "--auto-approve", "--force-lock"],
        ["unbind", "{key}", "-t", "client", "--force-lock"],
    ),
    ids=(
        "another-id", "an-unplanned-key", "another-target", "an-extra-argument", "an-unknown-flag",
        "another-app", "an-unplanned-unbind", "a-forced-bind", "a-forced-unbind",
    ),
)
def test_anything_else_is_refused(acked: tuple[adopt.Authority, object], args: list[str]) -> None:
    authority, plan = acked
    job = _job(plan)
    filled = [arg.format(key=job.key, id=job.live_id) for arg in args]

    assert _check(filled, authority) is not None


def test_a_tampered_or_unreadable_plan_admits_nothing(acked: tuple[adopt.Authority, object], tmp_path: Path) -> None:
    authority, plan = acked
    job = _job(plan)
    args = ["bind", job.key, job.live_id, "-t", "client", "--auto-approve"]
    path = authority.plan_path

    assert _check(args, adopt.Authority(tmp_path / "absent.json", authority.sha256, *LEASE)) == (
        "bundle deployment bind refused: the adopt plan could not be read"
    )
    path.write_text(path.read_text(encoding="utf-8").replace('"bind"', '"bind" ', 1), encoding="utf-8")
    assert _check(args, authority) is None, "whitespace does not change the canonical digest"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["bind"][0]["id"] = "1"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert _check(args, authority) is not None
    assert bundle_env._validate_app_deployment_command(args, expected_app_name=APP) is not None, (
        "without a plan only the App binding is admitted"
    )


def test_a_self_digested_plan_proves_nothing_alone(tmp_path: Path) -> None:
    """A document a caller wrote and digested itself, as the review's forge did."""

    forged = {"schema": PLAN_SCHEMA, "phase": "adopt", "bind": [{"key": "mip_refresh_scores", "id": "999999999"}]}
    authority = _authority(tmp_path / "forged.json", forged)
    args = ["bind", "mip_refresh_scores", "999999999", "-t", "client", "--auto-approve"]
    unbind = ["unbind", "mip_refresh_scores", "-t", "client"]
    lease = HeldLease()

    for edit in (args, unbind):
        assert "MIP_ADOPT_PLAN_ACK" in str(_check(edit, authority, ack="", lease=lease)), edit
    assert "never forces the deployment lock" in str(_check([*unbind, "--force-lock"], authority, lease=lease))
    # The caller exports its own digest too: the plan names no install.
    assert "client is not this install's" in str(_check(args, authority, lease=lease))
    # It copies this install's names as well: only the held signed lease is left.
    named = {**forged, "client": "client-one", "deployer_root": es.DEPLOYER_ROOT, "legacy_root": es.LEGACY_ROOT}
    authority = _authority(tmp_path / "forged-named.json", named)
    assert "lease is not held (RuntimeError)" in str(_check(args, authority, lease=HeldLease(held=False)))
    assert lease.asserted == [], "every local check refuses before the lease is read"


@pytest.mark.parametrize(
    "missing",
    ("the-operator-ack", "an-ack-for-another-plan", "this-install", "the-legacy-root", "the-held-lease"),
)
def test_each_factor_is_required_for_the_acknowledged_plan(
    acked: tuple[adopt.Authority, object], missing: str
) -> None:
    authority, plan = acked
    job = _job(plan)
    args = ["bind", job.key, job.live_id, "-t", "client", "--auto-approve"]
    kwargs: dict[str, Any] = {
        "the-operator-ack": {"ack": ""},
        "an-ack-for-another-plan": {"ack": "0" * 64},
        "this-install": {"profile": SimpleNamespace(**{**vars(PROFILE), "client": "client-two"})},
        "the-legacy-root": {"profile": SimpleNamespace(**{**vars(PROFILE), "legacy_bundle_root": es.OTHER_ROOT})},
        "the-held-lease": {"lease": HeldLease(held=False)},
    }[missing]

    assert _check(args, authority) is None
    assert _check(args, authority, **kwargs) is not None


@pytest.mark.parametrize(
    ("args", "kept", "authority", "error"),
    (
        (["bind", "k", "1", "-t", "client"], ["bind", "k", "1", "-t", "client"], None, None),
        (
            ["bind", "k", "1", "--adopt-plan", "p", "--adopt-plan-sha256", "s", "--adopt-lease-id", "l", "--adopt-source-git-sha", "g"],
            ["bind", "k", "1"],
            adopt.Authority(Path("p"), "s", "l", "g"),
            None,
        ),
        (
            ["bind", "k", "--adopt-plan=p", "--adopt-plan-sha256=s", "--adopt-lease-id=l", "--adopt-source-git-sha=g"],
            ["bind", "k"],
            adopt.Authority(Path("p"), "s", "l", "g"),
            None,
        ),
        (
            ["bind", "k", "--adopt-plan", "p", "--adopt-plan-sha256", "s"],
            None,
            None,
            "the adopt authority flags go together: " + ", ".join(bundle_env_adopt.FLAGS),
        ),
        (
            ["bind", "k", "--adopt-plan", "p", "--adopt-plan", "q", "--adopt-plan-sha256", "s"],
            None,
            None,
            "--adopt-plan must be given exactly once, with a value",
        ),
        (["bind", "k", "--adopt-lease-id"], None, None, "--adopt-lease-id requires a value"),
    ),
)
def test_the_wrapper_flags_go_together_once_each_and_never_reach_the_cli(
    args: list[str], kept: list[str] | None, authority: adopt.Authority | None, error: str | None
) -> None:
    result = bundle_env_adopt.split_authority(args)

    assert result[2] == error
    if error is None:
        assert result[0] == kept and result[1] == authority


# --- bundle_env main: the wiring from the flags to the CLI ------------------------------------


LEGACY = "/Workspace/Users/legacy-owner@example.invalid/.bundle/mortgage-intelligence-platform/dev"


def _main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, ack: str | None, held: bool
) -> tuple[int, dict[str, Any], list[tuple[str, str]]]:
    """``bundle_env deployment bind`` under a plan for the paychex-shaped profile's install.

    ``ack`` is the ``MIP_ADOPT_PLAN_ACK`` exported (``""``: the plan's own
    digest; ``None``: none); ``held`` whether the signed lease is held.
    """

    for name in KNOWN_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(bundle_env_adopt.ACK_ENV, raising=False)
    values = paychex_shaped_profile(MIP_LEGACY_BUNDLE_ROOT=LEGACY, MIP_DASHBOARD_PARENT_PATH=f"{LEGACY}/resources")
    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(values), encoding="utf-8")
    inputs = adopt.inputs_from_profile(resolve(from_local({}, env_local)))
    document = {
        "schema": PLAN_SCHEMA,
        "phase": "adopt",
        "client": inputs.client_key,
        "deployer_root": inputs.deployer_root,
        "legacy_root": inputs.legacy_root,
        "bind": [{"key": "mip_ref_seed", "id": "1001"}],
    }
    authority = _authority(tmp_path / "plan.json", document)
    if ack is not None:
        monkeypatch.setenv(bundle_env_adopt.ACK_ENV, ack or authority.sha256)
    asserted: list[tuple[str, str]] = []

    def held_lease(app_name: str, given: Any) -> Any:
        def check() -> None:
            asserted.append((given.lease_id, given.source_git_sha))
            if not held:
                raise RuntimeError("App deployment lease was released")

        return check

    monkeypatch.setattr(bundle_env_adopt, "held_deployment_lease", held_lease)
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "REPO", tmp_path)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    monkeypatch.delenv("DATABRICKS_BUNDLE_ROOT", raising=False)
    monkeypatch.setattr(bundle_env, "_pinned_genie_space_id", lambda _env, _profile: "pinned-space")
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    monkeypatch.setattr(bundle_env.scheduled_refresh_posture, "apply", lambda env, **_kw: "refresh: test")
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], *, env: dict[str, str], check: bool) -> Any:
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    argv = [
        "deployment", "bind", "mip_ref_seed", "1001", "-t", "client", "--auto-approve",
        "--adopt-plan", str(authority.plan_path), "--adopt-plan-sha256", authority.sha256,
        "--adopt-lease-id", LEASE[0], "--adopt-source-git-sha", LEASE[1],
    ]
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", *argv])
    return bundle_env.main(), captured, asserted


def test_main_admits_the_plan_edit_with_the_ack_and_the_held_lease_and_strips_the_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc, captured, asserted = _main(monkeypatch, tmp_path, ack="", held=True)

    assert rc == 0
    assert captured["command"] == [
        "databricks", "bundle", "deployment", "bind", "mip_ref_seed", "1001", "-t", "client", "--auto-approve",
    ]
    assert asserted == [LEASE]


@pytest.mark.parametrize(
    ("ack", "held"),
    ((None, True), ("0" * 64, True), ("", False)),
    ids=("no-ack", "another-ack", "lease-not-held"),
)
def test_main_refuses_the_plan_edit_before_the_cli_without_each_factor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    ack: str | None,
    held: bool,
) -> None:
    rc, captured, _asserted = _main(monkeypatch, tmp_path, ack=ack, held=held)

    assert rc == 2 and captured == {}, "the CLI is never reached"
    assert "bundle deployment bind refused: " in capsys.readouterr().err
