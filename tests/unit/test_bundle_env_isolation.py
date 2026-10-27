"""bundle_env applies the CI isolation rule table to its mutating subcommands (post-W5).

scripts/deploy.sh's preflight runs tools/databricks/ci_install_isolation.py
once, before any mutation. tools/databricks/bundle_env.py is the only other
path to a bundle mutation (``deploy`` with exact non-App selectors, and the
App ``deployment bind/unbind``), so it runs the same rule table on the exact
target, leaf, names and governed Genie id it would hand the CLI: before any
workspace call, and again after the governed Genie resolution. The forked-
anchor ``pr105-host`` target is therefore mutable only from deploy-dev.yml,
and deploy-dev.yml can mutate no other target. The read-only subcommands
(validate, plan, summary) keep their behaviour.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import bundle_env, ci_install_isolation

CI_TARGET = "pr105-host"
DEPLOY_DEV_REF = "o/r/.github/workflows/deploy-dev.yml@refs/heads/main"
STAGING_GENIE = "0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f"
STAGING = {
    "MIP_APP_NAME": "mip-app-pr105-staging",
    "MIP_DEFAULT_CATALOG": "mip_pr105_staging",
    "MIP_LAKEBASE_INSTANCE": "mip-app-state-staging",
    "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state_staging",
    "MIP_RUNTIME_SECRET_SCOPE": "mip-runtime-staging",
    "MIP_APP_ROLLBACK_SECRET_SCOPE": "mip-app-rollback-pr105-staging",
    "MIP_GENIE_SPACE_NAME": "Mortgage Lead Intelligence (staging)",
    "MIP_AI_GATEWAY_TABLE_PREFIX": "mip_agent_gateway_staging",
    "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE": "mip-agent-runtime-gateway-staging",
}
_AMBIENT = (
    "GITHUB_ACTIONS",
    "GITHUB_WORKFLOW_REF",
    "MIP_BUNDLE_ROOT_LEAF",
    "MIP_DEPLOY_TARGET",
    "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS",
    "MIP_AI_GATEWAY_AGENT_MODEL_FAMILY",
    "DATABRICKS_WAREHOUSE_ID",
    "GENIE_SPACE_ID",
    "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID",
    *STAGING,
)
DEPLOY = ["deploy", "--select", "jobs.mip_refresh_scores"]
# Every mutating subcommand bundle_env forwards, addressed to the CI staging App.
MUTATIONS = [
    DEPLOY,
    ["deployment", "bind", "mip_app", STAGING["MIP_APP_NAME"], "--auto-approve"],
    ["deployment", "unbind", "mip_app"],
]
MUTATION_IDS = ["deploy", "bind", "unbind"]


class _Run:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.resolved: list[str] = []
        self.renders: list[bool] = []

    def render(self, **kwargs: Any) -> tuple[int, int, int]:
        self.renders.append(bool(kwargs["demo_first_party_enabled"]))
        return (1, 0, 0)

    def cli(self, command: list[str], *, env: dict[str, str], check: bool) -> Any:
        assert check is False
        self.calls.append((command, env))
        return SimpleNamespace(returncode=0)


BUNDLE_ROOT = "bundle-root"  # under tmp_path


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Run:
    for name in _AMBIENT:
        monkeypatch.delenv(name, raising=False)
    # bundle_env.main() writes the bundle's variable-overrides.json (the
    # scheduled-refresh recipients) before every CLI call: keep it out of the
    # checkout, as tests/unit/test_workspace_auth.py does.
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / BUNDLE_ROOT)
    monkeypatch.delenv("DATABRICKS_BUNDLE_ROOT", raising=False)
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "DATABRICKS_WAREHOUSE_ID=warehouse-id\n"
        f"GENIE_SPACE_ID={STAGING_GENIE}\n"
        "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID=verifier-client-id\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    state = _Run()
    monkeypatch.setattr(bundle_env.render_sql, "render", state.render)
    monkeypatch.setattr(bundle_env.subprocess, "run", state.cli)

    def resolve(_env: dict[str, str], *, space_name: str) -> str:
        state.resolved.append(space_name)
        return STAGING_GENIE

    monkeypatch.setattr(bundle_env, "_resolve_governed_genie_space_id", resolve)
    return state


def _repo_overrides() -> dict[str, tuple[int, int] | None]:
    """(mtime_ns, size) of each target's variable-overrides.json in the real checkout, or None."""

    root = Path(bundle_env.REPO) / ".databricks" / "bundle"
    snapshot: dict[str, tuple[int, int] | None] = {}
    for target in ("dev", "prod", CI_TARGET):
        path = root / target / "variable-overrides.json"
        snapshot[target] = (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
    return snapshot


def test_the_isolation_tests_write_overrides_only_under_tmp(
    monkeypatch: pytest.MonkeyPatch, run: _Run, tmp_path: Path
) -> None:
    """The fixture keeps bundle_env's overrides write out of the checkout (post-W5 integration fix)."""

    before = _repo_overrides()
    _deploy_dev(monkeypatch)
    assert _main(monkeypatch, *DEPLOY, "-t", CI_TARGET) == 0
    assert _main(monkeypatch, "validate", "-t", "dev") == 0

    assert (tmp_path / BUNDLE_ROOT / ".databricks" / "bundle" / CI_TARGET / "variable-overrides.json").is_file()
    assert (tmp_path / BUNDLE_ROOT / ".databricks" / "bundle" / "dev" / "variable-overrides.json").is_file()
    assert _repo_overrides() == before, "the real checkout's overrides are untouched"


def _main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", *args])
    return bundle_env.main()


def _deploy_dev(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_WORKFLOW_REF": DEPLOY_DEV_REF,
        "MIP_BUNDLE_ROOT_LEAF": "dev-ci",
        **STAGING,
        **extra,
    }.items():
        monkeypatch.setenv(name, value)


def test_deploy_dev_mutates_the_forked_anchor_target_with_its_isolated_root(
    monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    _deploy_dev(monkeypatch)

    assert _main(monkeypatch, *DEPLOY, "-t", CI_TARGET) == 0, capsys.readouterr().err
    (command, env), = run.calls
    assert command == ["databricks", "bundle", *DEPLOY, "-t", CI_TARGET]
    assert (env["BUNDLE_VAR_bundle_root_leaf"], env["BUNDLE_VAR_install_suffix"]) == ("dev-ci", "_ci")
    assert env["BUNDLE_VAR_genie_space_id"] == STAGING_GENIE
    assert env["BUNDLE_VAR_app_name"] == STAGING["MIP_APP_NAME"]
    assert run.renders == [True, False], "pr105-host renders the dev-class Summit demo feeds, then restores"


@pytest.mark.parametrize("target", ["dev", "prod"])
@pytest.mark.parametrize(
    "subcommand",
    [DEPLOY, ["deployment", "bind", "mip_app", "mip-app-pr105-staging", "--auto-approve"], ["deployment", "unbind", "mip_app"]],
    ids=["deploy", "bind", "unbind"],
)
def test_deploy_dev_cannot_mutate_any_other_target(
    target: str, subcommand: list[str], monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    _deploy_dev(monkeypatch)

    assert _main(monkeypatch, *subcommand, "-t", target) == 2
    err = capsys.readouterr().err
    assert f"use -t {CI_TARGET}" in err
    assert run.calls == [] and run.resolved == [], "refused before any workspace call or CLI mutation"


@pytest.mark.parametrize(
    "subcommand",
    [DEPLOY, ["deployment", "bind", "mip_app", "mip-app", "--auto-approve"], ["deployment", "unbind", "mip_app"]],
    ids=["deploy", "bind", "unbind"],
)
def test_an_operator_cannot_mutate_the_forked_anchor_target(
    subcommand: list[str], monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(monkeypatch, *subcommand, "-t", CI_TARGET) == 2
    assert "deploy-dev.yml only" in capsys.readouterr().err
    assert run.calls == [] and run.resolved == []


def test_a_live_default_name_is_refused_by_name_without_its_value(
    monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    _deploy_dev(monkeypatch, MIP_LAKEBASE_INSTANCE="mip-app-state")

    assert _main(monkeypatch, *DEPLOY, "-t", CI_TARGET) == 2
    err = capsys.readouterr().err
    assert "MIP_LAKEBASE_INSTANCE" in err and "mip-app-state" not in err
    assert run.calls == []


@pytest.mark.parametrize("subcommand", MUTATIONS, ids=MUTATION_IDS)
def test_the_governed_genie_id_is_checked_after_resolution(
    subcommand: list[str], monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The staging title resolving to the live install's pinned space is refused before the CLI runs.

    Every mutating subcommand, not only ``deploy``: the App ``deployment
    bind/unbind`` reaches the same governed resolution, and the first check
    ran on the env/dotenv id, never on the id the command would bind.
    """

    _deploy_dev(monkeypatch)
    live = ci_install_isolation.pinned_live_genie_space()

    def resolve(_env: dict[str, str], *, space_name: str) -> str:
        run.resolved.append(space_name)
        return live

    monkeypatch.setattr(bundle_env, "_resolve_governed_genie_space_id", resolve)

    assert _main(monkeypatch, *subcommand, "-t", CI_TARGET) == 2
    err = capsys.readouterr().err
    assert "GENIE_SPACE_ID" in err and live not in err
    assert run.resolved == [STAGING["MIP_GENIE_SPACE_NAME"]], "refused after, not before, the governed resolution"
    assert run.calls == []


@pytest.mark.parametrize("subcommand", MUTATIONS, ids=MUTATION_IDS)
def test_every_mutating_subcommand_reaches_the_cli_once_the_resolved_id_is_isolated(
    subcommand: list[str], monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The non-vacuity control for the refusal above: same path, a staging id, one CLI call."""

    _deploy_dev(monkeypatch)

    assert _main(monkeypatch, *subcommand, "-t", CI_TARGET) == 0, capsys.readouterr().err
    assert run.resolved == [STAGING["MIP_GENIE_SPACE_NAME"]]
    assert [command for command, _env in run.calls] == [["databricks", "bundle", *subcommand, "-t", CI_TARGET]]


@pytest.mark.parametrize("subcommand", MUTATIONS, ids=MUTATION_IDS)
def test_an_ambient_ci_target_cannot_launder_a_dev_mutation_from_deploy_dev(
    subcommand: list[str], monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 2 (post-W5 review B1): the rule table checks the CLI target, never an ambient one.

    With an ambient MIP_DEPLOY_TARGET=pr105-host winning the merge, deploy-dev.yml
    would pass isolation for ``-t dev`` and mutate the live workspace under the
    dev-ci root.
    """

    _deploy_dev(monkeypatch, MIP_DEPLOY_TARGET=CI_TARGET)

    assert _main(monkeypatch, *subcommand, "-t", "dev") == 2
    assert f"use -t {CI_TARGET}" in capsys.readouterr().err
    assert run.calls == [] and run.resolved == [], "refused before any workspace call or CLI mutation"


@pytest.mark.parametrize(
    "subcommand",
    [DEPLOY, ["deployment", "bind", "mip_app", "mip-app", "--auto-approve"], ["deployment", "unbind", "mip_app"]],
    ids=MUTATION_IDS,
)
def test_an_ambient_dev_target_cannot_launder_an_operator_ci_target_mutation(
    subcommand: list[str], monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIP_DEPLOY_TARGET", "dev")

    assert _main(monkeypatch, *subcommand, "-t", CI_TARGET) == 2
    assert "deploy-dev.yml only" in capsys.readouterr().err
    assert run.calls == [] and run.resolved == []


def test_an_ambient_target_never_refuses_the_cli_target_it_disagrees_with(
    monkeypatch: pytest.MonkeyPatch, run: _Run, capsys: pytest.CaptureFixture[str]
) -> None:
    """The non-vacuity control: an ambient ``dev`` cannot refuse deploy-dev.yml's own pr105-host deploy."""

    _deploy_dev(monkeypatch, MIP_DEPLOY_TARGET="dev")

    assert _main(monkeypatch, *DEPLOY, "-t", CI_TARGET) == 0, capsys.readouterr().err
    assert [command for command, _env in run.calls] == [["databricks", "bundle", *DEPLOY, "-t", CI_TARGET]]


def test_the_live_operator_deploy_is_unchanged(monkeypatch: pytest.MonkeyPatch, run: _Run) -> None:
    assert _main(monkeypatch, *DEPLOY, "-t", "dev") == 0
    (_command, env), = run.calls
    assert (env["BUNDLE_VAR_bundle_root_leaf"], env["BUNDLE_VAR_install_suffix"]) == ("dev", "")


def test_read_only_subcommands_keep_their_behaviour(monkeypatch: pytest.MonkeyPatch, run: _Run) -> None:
    """The rule table gates mutation; an operator may still render the CI target read-only."""

    assert _main(monkeypatch, "validate", "-t", CI_TARGET) == 0
    assert [command for command, _env in run.calls] == [["databricks", "bundle", "validate", "-t", CI_TARGET]]


def test_the_forked_anchor_target_is_dev_class_for_demo_feeds() -> None:
    assert bundle_env._demo_first_party_flag_for_target({}, target=CI_TARGET) == "1"
    assert bundle_env._demo_feeds_allowed_for_target(target=CI_TARGET, enabled=True, env={})
    assert not bundle_env._demo_feeds_allowed_for_target(target="customer", enabled=True, env={})
    assert bundle_env._validate_non_app_deploy_selectors(["-t", CI_TARGET, "--select", "jobs.x"]) is None
    assert bundle_env._validate_non_app_deploy_selectors(["-t", "ci", "--select", "jobs.x"]) is not None
