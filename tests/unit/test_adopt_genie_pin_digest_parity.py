"""B4(c): the pin plan digest bundle_env recomputes is the one the step-0 pin check printed.

10B-0's read-only dispatch refuses at the step-0 pin check
(``provision_genie_space --plan-pin``) and prints the pin plan's sha256; 10B-1
carries it as ``genie_pin``. The adopt phases' bundle calls then recompute
that digest in bundle_env (``--adopt-acked-genie-pin``). The plan names the
deploying identity (``current_user.me().user_name``), so both tools must
resolve the same one in the same run, or 10B-1 refuses ``the ack names another
plan`` at install_state_adopt's plan and the cutover stays blocked.

The auth here is the deploy job's own: the ``DEFAULT`` github-oidc profile
tools.clients.ci_profile writes, bound by scripts/deploy.sh as
``MIP_DEPLOYER_DATABRICKS_PROFILE`` with ``DATABRICKS_HOST``/``TOKEN`` unset,
and the profile's ``.env.local`` (which carries the App's own
``DATABRICKS_CLIENT_ID``). Each workspace client is the real SDK ``Config``,
built offline (no host metadata read, a no-network credentials strategy), so
the SDK's own precedence (arguments, then the environment, then the profile
file) decides who each client is. The Genie reads are the in-memory
PinWorkspace. The second half drives install_state_adopt's real
``BundleEnvCli``, and adopt-live's ``reviewed_bindings``, through an
in-process bundle_env that sees what a real child would: the ``env=`` its
caller built and nothing else, with its stderr on the caller's pipe. So an
ack a caller strips from the child's environment refuses here as it would in
10B-1, and the tests read what a CI job log shows when bundle_env refuses the
Genie binding (the bundle call's name and exit code) and where the cause goes
(the private report only).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from databricks.sdk.config import Config
from databricks.sdk.credentials_provider import CredentialsStrategy

from tests.fixtures import adopt_estate as es
from tests.fixtures.client_profiles import IDENTITY_IDS, paychex_shaped_profile
from tests.fixtures.genie_pin_workspace import PinWorkspace, Space, patch_rollback_scope
from tools.clients import ci_profile
from tools.clients.environment import KNOWN_NAMES, RawProfile, resolve
from tools.databricks import (
    adopt_live_facts,
    bundle_env,
    bundle_env_adopt,
    provision_genie_space,
    workspace_auth,
)
from tools.databricks import install_state_adopt as adopt

FLAG = bundle_env_adopt.ADOPT_GENIE_FLAG
ACK_ENV = bundle_env_adopt.GENIE_PIN_ACK_ENV
PROFILE = resolve(RawProfile(values=paychex_shaped_profile()))
CANDIDATE = "acked-candidate-space-0001"
DEPLOYER = IDENTITY_IDS["deployer"]
APP_FACING = IDENTITY_IDS["normal"]
SIGNING_KEYS = ("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY")
STRANGER = "stranger@other.example"


class _Offline(CredentialsStrategy):
    def auth_type(self) -> str:
        return "github-oidc"

    def __call__(self, cfg: Any) -> Any:
        return lambda: {}


class SdkConfigClient:
    """A WorkspaceClient double: the real SDK Config says who it is; the Genie reads are ``estate``'s."""

    estate: ClassVar[PinWorkspace]
    built: ClassVar[list[dict[str, str]]]

    def __init__(self, **kwargs: Any) -> None:
        strategy = kwargs.pop("credentials_strategy", None)
        self.config = Config(**kwargs, credentials_strategy=_Offline())
        type(self).built.append(
            {"strategy": type(strategy).__name__, "client_id": str(self.config.client_id), "host": str(self.config.host)}
        )
        self.genie, self.secrets, self.api_client = self.estate.genie, self.estate.secrets, self.estate.api_client
        self.current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name=self.config.client_id))


def genie_world() -> PinWorkspace:
    return PinWorkspace(spaces=[Space(CANDIDATE, title=PROFILE.genie_space_name, managers=("creator@lender.example",))])


@pytest.fixture
def ci_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The deploy job's auth and profile files, and the SDK double behind both client builders."""

    for name in (*KNOWN_NAMES, ACK_ENV, "DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_AUTH_TYPE",
                 "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "MIP_DEPLOYER_DATABRICKS_HOST",
                 "MIP_DEPLOYER_DATABRICKS_TOKEN", "DATABRICKS_BUNDLE_ROOT", "GENIE_SPACE_ID"):
        monkeypatch.delenv(name, raising=False)
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(ci_profile.databrickscfg_text(PROFILE), encoding="utf-8")
    env_local = tmp_path / ".env.local"
    env_local.write_text(ci_profile.env_local_text(PROFILE), encoding="utf-8")
    assert f"DATABRICKS_CLIENT_ID='{APP_FACING}'" in env_local.read_text(encoding="utf-8")
    for name, value in {
        "DATABRICKS_CONFIG_FILE": str(cfg),
        "DATABRICKS_CONFIG_PROFILE": ci_profile.DEPLOYER_PROFILE,
        "MIP_DEPLOYER_DATABRICKS_PROFILE": ci_profile.DEPLOYER_PROFILE,
        "MIP_DATABRICKS_WORKSPACE_HOST": PROFILE.databricks_host,
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.example.invalid/idtoken",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "job-scoped-request-token",
        # The provisioner's run() writes it; setenv records the prior state.
        "MIP_DEFAULT_CATALOG": PROFILE.default_catalog,
    }.items():
        monkeypatch.setenv(name, value)

    def offline(_host: str) -> Any:
        raise RuntimeError("offline: no host metadata read")

    monkeypatch.setattr("databricks.sdk.config.get_host_metadata", offline)
    SdkConfigClient.estate = genie_world()
    SdkConfigClient.built = []
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", SdkConfigClient)
    monkeypatch.setattr(workspace_auth, "WorkspaceClient", SdkConfigClient)
    patch_rollback_scope(monkeypatch)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "REPO", tmp_path)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    monkeypatch.setattr(bundle_env.scheduled_refresh_posture, "apply", lambda env, **_kw: "refresh: test")
    return env_local


def step_0_pin_plan_sha256(capsys: pytest.CaptureFixture[str]) -> str:
    """Run the deploy's step-0 pin check exactly as deploy_step_preflight_gates.sh does; its printed digest."""

    rc = provision_genie_space.main([
        "--plan-pin",
        "--space-name", PROFILE.genie_space_name,
        "--catalog", PROFILE.default_catalog,
        "--app-name", PROFILE.app_name,
        "--pin-scope", PROFILE.app_rollback_secret_scope,
        "--lakebase-instance", PROFILE.lakebase_instance,
        "--no-smoke-test",
    ])
    out = capsys.readouterr().out
    assert rc == provision_genie_space.EXIT_PIN_REFUSED, "10B-0 refuses at the pin check, before the lease"
    found = re.findall(r"pin plan sha256: ([0-9a-f]{64})$", out, re.MULTILINE)
    assert len(found) == 1, out
    return found[0]


def bundle_env_plan(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> tuple[int, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def cli(command: list[str], *, env: dict[str, str], check: bool) -> Any:
        captured.update(command=command, env=env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bundle_env.subprocess, "run", cli)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", *argv])
    return bundle_env.main(), captured


@pytest.mark.parametrize("exported_app_client_id", (False, True), ids=("ci-shape", "app-facing-id-exported"))
def test_the_pin_plan_digest_bundle_env_recomputes_is_the_one_step_0_printed(
    ci_auth: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exported_app_client_id: bool,
) -> None:
    """Same run, same auth: one deploying identity, so 10B-1's genie_pin clears the bind plan.

    In ``ci-shape`` (deploy.sh un-exports the App's id) even a bare
    ``WorkspaceClient(profile=...)`` would resolve the deployer; the strategy
    assertion pins that bundle_env now builds the provisioner's client.
    ``app-facing-id-exported`` adds the App's own DATABRICKS_CLIENT_ID (the
    .env.local value) to the process environment: a bare
    ``WorkspaceClient(profile=...)`` lets the SDK take it over the profile's
    client id (another digest, so bundle_env refused), while both tools now
    build the bound profile with ``workspace_auth.deployer_profile_client``,
    which passes the profile's own.
    """

    if exported_app_client_id:
        monkeypatch.setenv("DATABRICKS_CLIENT_ID", APP_FACING)
    printed = step_0_pin_plan_sha256(capsys)
    monkeypatch.setenv(ACK_ENV, printed)

    rc, captured = bundle_env_plan(monkeypatch, ["plan", "-t", "client", "-o", "json", FLAG])

    err = capsys.readouterr().err
    assert rc == 0, err
    assert captured["env"]["BUNDLE_VAR_genie_space_id"] == CANDIDATE
    assert SdkConfigClient.built, "both tools built their clients through the SDK double"
    assert {client["client_id"] for client in SdkConfigClient.built} == {DEPLOYER}
    assert {client["host"] for client in SdkConfigClient.built} == {PROFILE.databricks_host}
    assert {client["strategy"] for client in SdkConfigClient.built} == {"CachedGitHubOidcCredentials"}
    assert SdkConfigClient.estate.mutations == []


def test_a_holder_change_after_step_0_refuses_the_bind_plan(
    ci_auth: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The digest binds the holders too: a CAN_MANAGE grant after 10B-0 refuses 10B-1 (review again)."""

    monkeypatch.setenv(ACK_ENV, step_0_pin_plan_sha256(capsys))
    SdkConfigClient.estate.spaces[0].managers += (STRANGER,)

    rc, captured = bundle_env_plan(monkeypatch, ["plan", "-t", "client", "-o", "json", FLAG])

    assert rc == 2 and captured == {}
    assert "the ack names another plan" in capsys.readouterr().err


# --- install_state_adopt's real BundleEnvCli, through an in-process bundle_env -------------------


def _plan_text(estate: es.Estate) -> str:
    """The bundle plan JSON the direct engine would print over ``estate`` (the fake CLI's own)."""

    texts: list[str] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(es, "parse_plan", lambda text: texts.append(text) or adopt.parse_plan(text))
        es.FakeBundleCli(estate).plan()
    return texts[0]


def _in_process_bundle_env(
    monkeypatch: pytest.MonkeyPatch,
    estate: es.Estate,
    bound: list[str],
    *,
    before_call: Any = None,
) -> list[dict[str, str]]:
    """Stand in for the bundle_env child process; returns each child's environment, in call order.

    A real child sees the ``env=`` mapping its caller built and nothing else,
    and its stderr is the caller's pipe, never the job log. So every call
    runs bundle_env.main() with ``os.environ`` replaced by that mapping and
    its stderr captured and handed back. A plan or summary answers with the
    fake engine's plan over ``estate``. A bind or unbind bundle_env lets
    through then edits the deployer's state there, checked against the adopt
    plan as bundle_env's own verifier does (tests/fixtures/adopt_estate.py;
    the verifier itself is stood in for, its tests are bundle_env_adopt's).
    The synthetic estate names its App after its bundle key; bundle_env admits
    only the profile's App, so the App's bind reaches it under that name.
    ``before_call(argv)`` runs first, for a world that changes mid-run.
    """

    children: list[dict[str, str]] = []
    engine = es.FakeBundleCli(estate)
    estate_app = estate.legacy_ids.get("apps.mip_app")

    def run(argv: list[str], *, env: dict[str, str], **_kwargs: Any) -> Any:
        assert argv[1:3] == ["-m", "tools.databricks.bundle_env"]
        args = argv[3:]
        if before_call is not None:
            before_call(args)
        children.append(dict(env))
        stderr = io.StringIO()
        with pytest.MonkeyPatch.context() as child, contextlib.redirect_stderr(stderr):
            for name in set(os.environ) - set(env):
                child.delenv(name)
            for name, value in env.items():
                child.setenv(name, value)
            child.setattr(bundle_env.bundle_env_adopt, "edit_verifier", lambda *_a, **_k: lambda *_e: None)
            rc, captured = bundle_env_plan(child, [PROFILE.app_name if arg == estate_app else arg for arg in args])
        bound.append(captured.get("env", {}).get("BUNDLE_VAR_genie_space_id", "refused"))
        if rc == 0 and args[0] == "deployment":
            kept, authority, error = bundle_env_adopt.split_authority([a for a in args if a != FLAG])
            assert error is None and authority is not None, error
            if kept[1] == "bind":
                engine.bind(kept[2], kept[3], authority)
            else:
                engine.unbind(kept[2], authority)
        return SimpleNamespace(
            returncode=rc, stdout=_plan_text(estate) if rc == 0 else "", stderr=stderr.getvalue()
        )

    monkeypatch.setattr(adopt.subprocess, "run", run)
    return children


def _public(captured: Any) -> list[str]:
    """What a job log shows: stdout and stderr, without the runner-mask registrations."""

    lines = (captured.out + captured.err).splitlines()
    return [line for line in lines if line and not line.startswith("::add-mask::")]


@pytest.mark.parametrize("phase", ("adopt", "unadopt"))
def test_the_adopt_phases_plan_binds_the_acked_candidate_through_the_real_cli(
    ci_auth: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path, phase: str
) -> None:
    estate = es.Estate()
    if phase == "unadopt":
        fake = es.FakeBundleCli(estate)
        ack = adopt.compute_plan("adopt", es.inputs(), fake, estate).sha256
        (tmp_path / "private").mkdir()
        lease = adopt.SignedLease(lambda: None, "lease-1", "0" * 40)
        adopt.apply("adopt", ack, es.inputs(), fake, estate, lease=lease, plan_dir=tmp_path / "private")
        assert estate.bound() == estate.legacy_ids
    monkeypatch.setenv(ACK_ENV, step_0_pin_plan_sha256(capsys))
    bound: list[str] = []
    _in_process_bundle_env(monkeypatch, estate, bound)

    plan = adopt.compute_plan(phase, es.inputs(), adopt.BundleEnvCli(python="python"), estate)

    assert not plan.refused, plan.refusals
    assert len(plan.edits) == 16
    assert bound == [CANDIDATE], "the pre-bind plan bound the acked candidate"


def test_the_ack_reaches_bundle_env_in_the_child_environment_each_caller_builds(
    ci_auth: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both callers hand MIP_GENIE_PIN_ACK to the bundle_env child and keep the signing keys from it.

    The plan is install_state_adopt's (``BundleEnvCli._run``, which the binds
    and unbinds share); the summary is adopt-live's ``reviewed_bindings``.
    """

    printed = step_0_pin_plan_sha256(capsys)
    monkeypatch.setenv(ACK_ENV, printed)
    for name in SIGNING_KEYS:
        monkeypatch.setenv(name, "PLACEHOLDER-not-a-key")
    bound: list[str] = []
    children = _in_process_bundle_env(monkeypatch, es.Estate(), bound)
    monkeypatch.setattr(
        "tools.databricks.app_rollback_resource_contract.reviewed_app_resource_contract", lambda summary: []
    )

    adopt.BundleEnvCli(python="python").plan()
    adopt_live_facts.reviewed_bindings()

    assert bound == [CANDIDATE, CANDIDATE], "the plan and the summary both bound the acked candidate"
    assert [child[ACK_ENV] for child in children] == [printed, printed]
    assert not any(name in child for child in children for name in SIGNING_KEYS)


def test_a_caller_that_strips_the_ack_from_the_child_refuses_as_10b_1_would(
    ci_auth: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control for the harness: the ack in the caller's own environment is not enough."""

    monkeypatch.setenv(ACK_ENV, step_0_pin_plan_sha256(capsys))
    monkeypatch.setattr(adopt, "_PRIVATE_ENV", (*adopt._PRIVATE_ENV, ACK_ENV))
    bound: list[str] = []
    children = _in_process_bundle_env(monkeypatch, es.Estate(), bound)
    cli = adopt.BundleEnvCli(python="python")

    with pytest.raises(adopt.BundleCliError, match=r"bundle plan failed \(exit 2\)"):
        cli.plan()

    assert bound == ["refused"] and ACK_ENV not in children[0]
    assert "no ack names it" in cli.errors[0]


def test_the_real_cli_refuses_the_adopt_plan_without_the_exact_ack(
    ci_auth: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    monkeypatch.setenv(ACK_ENV, "0" * 64)
    bound: list[str] = []
    _in_process_bundle_env(monkeypatch, estate, bound)
    cli = adopt.BundleEnvCli(python="python")

    with pytest.raises(adopt.BundleCliError, match=r"bundle plan failed \(exit 2\)"):
        adopt.compute_plan("adopt", es.inputs(), cli, estate)

    assert bound == ["refused"]
    # The child's stderr is the caller's pipe: the cause goes to the private report's cli_errors only.
    assert len(cli.errors) == 1 and "the ack names another plan" in cli.errors[0]
    assert "the ack names another plan" not in "\n".join(_public(capsys.readouterr()))


# --- what the job log shows, and what stays in the private report --------------------------------


def _adopt_main(estate: es.Estate, argv: list[str]) -> int:
    return adopt.main(
        argv,
        inputs=es.inputs,
        workspace_factory=lambda: estate,
        cli_factory=lambda: adopt.BundleEnvCli(python="python"),
        lease_factory=lambda _workspace, _args: adopt.SignedLease(lambda: None, "lease-1", "0" * 40),
    )


def _genie_cause(report: Path) -> list[str]:
    assert oct(report.stat().st_mode & 0o777) == "0o600", "the report is private"
    return list(json.loads(report.read_text(encoding="utf-8"))["cli_errors"])


def test_a_bind_plan_bundle_env_refuses_shows_only_the_bundle_calls_exit_in_the_job_log(
    ci_auth: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """10B-1 after a holder change: ``bundle plan failed (exit 2)``; the Genie cause is in the private report."""

    estate = es.Estate()
    monkeypatch.setenv(ACK_ENV, step_0_pin_plan_sha256(capsys))
    SdkConfigClient.estate.spaces[0].managers += (STRANGER,)
    _in_process_bundle_env(monkeypatch, estate, [])
    report = tmp_path / "plan-report.json"

    rc = _adopt_main(estate, ["plan", "--phase", "adopt", "--report-out", str(report)])

    assert rc == 2
    assert _public(capsys.readouterr()) == ["[mip-adopt] REFUSED: bundle plan failed (exit 2)"]
    (cause,) = _genie_cause(report)
    assert "governed Genie binding verification failed" in cause and "the ack names another plan" in cause
    assert estate.bound() == {}


@pytest.mark.parametrize(
    ("binds_before_the_change", "exit_code", "bound_after", "last_line"),
    (
        (4, 3, 4, "the inverse is unproven: the state may still bind keys this run bound"),
        (0, 2, 0, "nothing is left changed: every bind this run made was undone"),
    ),
    ids=("among-the-binds", "at-the-first-bind"),
)
def test_a_holder_change_under_the_lease_ends_bind_failed_and_half_bound_unless_nothing_was_bound(
    ci_auth: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    binds_before_the_change: int,
    exit_code: int,
    bound_after: int,
    last_line: str,
) -> None:
    """10B-2: the bind and the undo's unbinds each re-resolve the match, so the undo refuses too (exit 3)."""

    estate = es.Estate()
    monkeypatch.setenv(ACK_ENV, step_0_pin_plan_sha256(capsys))
    ack = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate).sha256

    def a_holder_appears(args: list[str]) -> None:
        space = SdkConfigClient.estate.spaces[0]
        at_the_bind = args[:2] == ["deployment", "bind"] and len(estate.bound()) == binds_before_the_change
        if at_the_bind and STRANGER not in space.managers:
            space.managers += (STRANGER,)

    _in_process_bundle_env(monkeypatch, estate, [], before_call=a_holder_appears)
    report = tmp_path / "apply-report.json"

    rc = _adopt_main(
        estate,
        ["apply", "--phase", "adopt", "--ack-plan", ack, "--app-name", "a", "--lease-id", "lease-1",
         "--source-git-sha", "0" * 40, "--report-out", str(report)],
    )

    public = _public(capsys.readouterr())
    assert rc == exit_code and len(estate.bound()) == bound_after
    assert public[0] == "[mip-adopt] REFUSED bind_failed: BundleCliError during the binds"
    assert public[-1].startswith(f"[mip-adopt] {last_line}") and len(public) == 2
    assert "ack names another plan" not in "\n".join(public), "the job log never names the Genie cause"
    causes = _genie_cause(report)
    assert len(causes) == 1 + bound_after, "the refused bind, then every unbind of the undo"
    assert all("the ack names another plan" in cause for cause in causes)


def test_a_holder_change_at_adopt_lives_summary_keeps_the_binds_and_stales_the_adopt_plan_ack(
    ci_auth: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """After the binds a fresh genie_pin is not enough for 10B-2: the adopt plan is another plan (10B-0, 10B-1, 10B-2)."""

    estate = es.Estate()
    monkeypatch.setenv(ACK_ENV, step_0_pin_plan_sha256(capsys))
    ack = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate).sha256
    _in_process_bundle_env(monkeypatch, estate, [])
    applied = _adopt_main(
        estate,
        ["apply", "--phase", "adopt", "--ack-plan", ack, "--app-name", "a", "--lease-id", "lease-1",
         "--source-git-sha", "0" * 40],
    )
    assert applied == 0 and len(estate.bound()) == 16, capsys.readouterr().err
    capsys.readouterr()

    SdkConfigClient.estate.spaces[0].managers += (STRANGER,)
    with pytest.raises(adopt_live_facts.AdoptLiveRefused, match=r"\Athe bundle summary failed \(exit 2\)\Z") as refused:
        adopt_live_facts.reviewed_bindings()

    assert refused.value.code == "bindings_differ"
    assert _public(capsys.readouterr()) == [], "adopt-live keeps no line of bundle_env's: no report holds the cause"
    assert len(estate.bound()) == 16, "the binds stay as acknowledged"
    # 10B-0 again gives a fresh genie_pin; the earlier 10B-1's adopt_plan digest is still another plan's.
    monkeypatch.setenv(ACK_ENV, step_0_pin_plan_sha256(capsys))
    assert _adopt_main(estate, ["plan", "--phase", "adopt", "--ack-plan", ack]) == 2
    mismatch = f"[mip-adopt] REFUSED ack_mismatch: {bundle_env_adopt.ACK_ENV} is not this plan's sha256"
    assert mismatch in _public(capsys.readouterr())
    again = adopt.compute_plan("adopt", es.inputs(), adopt.BundleEnvCli(python="python"), estate)
    assert again.sha256 != ack and again.edits == () and len(again.already) == 16
