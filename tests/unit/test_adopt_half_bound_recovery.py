"""The half-bound state an unproven undo leaves is recovered by the advised ``--phase unadopt`` (M2-PR6).

An adopt or unadopt whose undo cannot be proven exits 3 and tells the operator
to run ``--phase unadopt``. adopt's undo unbinds in reverse (the App last) and
goes on past a failed unbind, so it can leave legacy keys bound without the
App: a state the release-mode live checks refuse (``adoption``). These tests
follow the advice through each layer it crosses: the exit-3 guidance, the
live-check arguments the deploy's preflight passes for ``--phase unadopt``
(evaluated from the command of record), the live checks with those arguments
against that state, then the unadopt plan and apply.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures import adopt_estate as es
from tests.fixtures.client_profiles import DEMO_PROFILE, IDENTITY_IDS
from tests.fixtures.deploy_block_harness import block
from tests.fixtures.live_workspace import LiveAccount, LiveWorkspace, bundle_state
from tools.clients import live_checks
from tools.databricks import install_state_adopt as adopt

APP = DEMO_PROFILE["MIP_APP_NAME"]


class Lease:
    lease_id = "lease-1"
    source_git_sha = "0" * 40

    def __call__(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _outside_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def _preflight_live_check_args(phase: str) -> list[str]:
    """The arguments the deploy's preflight hands the live checks in ``phase``."""

    text = block("# >>> client live checks >>>", "# <<< client live checks <<<")
    (line,) = [line for line in text.splitlines() if line.startswith("_LIVE_CHECK_ARGS=")]
    script = f'DEPLOY_PHASE={phase}\n{line}\nprintf "%s\\n" "${{_LIVE_CHECK_ARGS[@]}}"\n'
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=True).stdout.split()


def _live_checks(argv: list[str], estate: es.Estate, tmp_path: Path) -> int:
    """The live checks against this deployer's state (its App bound, if at all, to the profile's App)."""

    bound = {key: APP if key == "apps.mip_app" else value for key, value in estate.bound().items()}
    workspace = LiveWorkspace(
        DEMO_PROFILE,
        identity_ids=IDENTITY_IDS,
        app=SimpleNamespace(name=APP),
        state=bundle_state(**bound),
        catalog_properties={"mip.install_key": DEMO_PROFILE["MIP_CLIENT"]},
    )
    env_file = tmp_path / "client.env"
    env_file.write_text("".join(f"{k}='{v}'\n" for k, v in DEMO_PROFILE.items()), encoding="utf-8")
    return live_checks.main(
        argv,
        environ={},
        env_file=env_file,
        client=lambda: workspace,
        account=lambda: LiveAccount(IDENTITY_IDS["account_principal"]),
    )


def _apply_args(phase: str, ack: str) -> list[str]:
    return ["apply", "--phase", phase, "--ack-plan", ack, "--app-name", "a", "--lease-id", "l", "--source-git-sha", "0" * 40]


def _main(estate: es.Estate, cli: es.FakeBundleCli, argv: list[str]) -> int:
    return adopt.main(
        argv,
        inputs=es.inputs,
        workspace_factory=lambda: estate,
        cli_factory=lambda: cli,
        lease_factory=lambda _workspace, _args: Lease(),
    )


def _unadopt_recovers(estate: es.Estate, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The advised recovery: the deploy's unadopt live checks pass, then unadopt unbinds the rest."""

    argv = _preflight_live_check_args("unadopt")
    assert argv == ["--phase", "unadopt"]
    assert _live_checks(argv, estate, tmp_path) == 0, capsys.readouterr().err
    sha = adopt.compute_plan("unadopt", es.inputs(), es.FakeBundleCli(estate), estate).sha256

    assert _main(estate, es.FakeBundleCli(estate), _apply_args("unadopt", sha)) == 0
    assert estate.bound() == {}, "nothing adopt bound is left bound"


def test_an_unproven_adopt_undo_that_unbinds_the_app_is_recovered_by_unadopt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    plan = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate)
    pipeline = next(edit for edit in plan.edits if edit.resource.startswith("pipelines."))
    # The post-bind gate refuses; the undo fails the pipeline's unbind and goes
    # on to the App's, which it removes last.
    cli = es.FakeBundleCli(estate, recreate_dashboards=True, fail_unbind={pipeline.key: RuntimeError("x")})

    assert _main(estate, cli, _apply_args("adopt", plan.sha256)) == 3
    assert "then run --phase unadopt\n" in capsys.readouterr().err
    assert [call[1] for call in cli.edits() if call[0] == "unbind"][-1] == "mip_app"
    assert estate.bound() == {pipeline.resource: pipeline.live_id}, "the App unbound, the pipeline still bound"
    assert _live_checks([], estate, tmp_path) == 2, "a release's live checks refuse the half-bound state"
    assert "  - adoption: " in capsys.readouterr().err

    _unadopt_recovers(estate, tmp_path, capsys)


def test_a_partial_unadopt_keeps_the_app_bound_and_unadopt_again_finishes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    estate = es.Estate()
    sha = adopt.compute_plan("adopt", es.inputs(), es.FakeBundleCli(estate), estate).sha256
    assert _main(estate, es.FakeBundleCli(estate), _apply_args("adopt", sha)) == 0
    plan = adopt.compute_plan("unadopt", es.inputs(), es.FakeBundleCli(estate), estate)
    assert plan.edits[-1].resource == "apps.mip_app", "the App's binding is the last one removed"
    cli = es.FakeBundleCli(estate, fail_unbind={plan.edits[1].key: RuntimeError("x")})

    assert _main(estate, cli, _apply_args("unadopt", plan.sha256)) == 3
    assert "then run --phase unadopt again\n" in capsys.readouterr().err
    left = estate.bound()
    assert len(left) == 15 and "apps.mip_app" in left

    _unadopt_recovers(estate, tmp_path, capsys)
