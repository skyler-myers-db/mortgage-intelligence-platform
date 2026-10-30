"""A paychex-shaped client install, run offline end to end (M1-PR2).

The client profile is client #1's shape with synthetic identifiers
(tests/fixtures/client_profiles.paychex_shaped_profile).

* ``scripts/deploy.sh -t client --dry-run`` in a sandbox clone
  (tests/fixtures/deploy_sandbox.py), with a stub ``databricks`` that fails on
  anything but ``--version``: the profile resolves first, every bundle and job
  command addresses ``client``, the read-only live checks are planned before
  any bundle command, and every App source check names the deployer and the
  client key. A refused profile, or a profile whose host is not the auth
  host, stops the run before any planned command. The demo install's weekly
  refresh stops it before any bundle command: its failure mail would go to
  the deployer service principal's application id, so the preflight refuses
  ``MIP_SCHEDULED_REFRESH=on`` until M1-PR3 mails the profile's recipients,
  and the plan above runs with it ``off``.
* tools/databricks/bundle_env.py with that profile renders byte for byte the
  SQL the retired dev target rendered for the same install (catalog ``mip``,
  Summit demo feeds on), and exports today's resource names: the variable
  defaults the live install runs on.
* Under GitHub Actions on the deploy-dev workflow's ref the dry run is refused
  before any Databricks call: that workflow is parked until a client
  environment is selected (M1-PR7).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.config import source_names
from backend.schemas.lender_identity_record import (
    lender_identity_digest,
    reviewed_lender_identity_from_env,
)
from tests.fixtures.client_profiles import (
    IDENTITY_IDS,
    LENDER_IDENTITY,
    env_file_text,
    paychex_shaped_profile,
)
from tests.fixtures.deploy_sandbox import ANSI, dry_run, planned_commands, sandbox
from tools import render_sql
from tools.databricks import bundle_env

# The renderer itself, before any test patches bundle_env's reference to it.
RENDER = render_sql.render
ROOT = Path(__file__).resolve().parents[2]
CLIENT = "client-one"
DEPLOYER = IDENTITY_IDS["deployer"]
IDENTITY = f"--expected-principal {DEPLOYER} --expected-target client --expected-root-leaf {CLIENT}"
# Ambient names an in-process bundle_env call must not inherit.
_AMBIENT_PREFIXES = ("MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_", "LAKEBASE_", "APP_ENV")
NAME_VARIABLES = (
    "uc_catalog",
    "app_name",
    "lakebase_instance_name",
    "lakebase_catalog_name",
    "lakebase_database_name",
    "runtime_secret_scope",
)


def test_the_client_dry_run_addresses_only_the_client_target(tmp_path: Path) -> None:
    checkout, env, log = sandbox(tmp_path)
    _with_env_local(checkout, MIP_SCHEDULED_REFRESH="off", MIP_SCHEDULED_REFRESH_RECIPIENTS=None)

    result = dry_run(checkout, env)

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    out = ANSI.sub("", result.stdout)
    commands = planned_commands(result.stdout)
    assert f"client:     {CLIENT} (demo install; profile resolved)" in out
    assert out.index("profile resolved") < out.index("$ "), "the profile resolves before any planned command"
    # The read-only live checks are the first planned command, before any bundle call.
    assert commands[0] == ".venv/bin/python -m tools.clients.live_checks"
    targeted = [command for command in commands if " -t " in f" {command} " or "bundle_env" in command]
    assert len(targeted) >= 8, "validate, plan, deploy, bind and the job runs"
    for command in targeted:
        assert re.search(r"(?:^| )-t client(?: |$)", command), command
    assert not any(re.search(r"(?:^| )-t (?!client\b)", command) for command in commands)
    assert "target:     client (client client-one, demo install)" in out
    assert "scheduled refresh: PAUSED" in out
    verify = [command for command in commands if "verify_scheduled_refresh_state" in command]
    assert verify == [".venv/bin/python -m tools.databricks.verify_scheduled_refresh_state --expect PAUSED --live-install"]
    assert "deployment bind mip_app mip-app -t client" in out
    # The per-release App source: preflight checks the closure for real (git
    # only), step 1 prints the stage, step 5 the publish, and every
    # activation's converge names the one release folder of the dry run under
    # the deployer and the client key.
    assert "app runtime closure: current" in out
    sha = re.search(r"exact source: ([0-9a-f]{40}) ", out).group(1)
    release = f"{sha[:12]}-dry-run"
    publish = [command for command in commands if "app_release_source publish" in command]
    assert len(publish) == 1 and f"--release-id {release} " in publish[0]
    assert publish[0].endswith(IDENTITY)
    sources = [command for command in commands if "converge_static_app_source" in command]
    folder = f"/Workspace/dry-run/mortgage-intelligence-platform/app-src/{release}"
    # A first-install dry run: the first-install and Agent Evaluation activations.
    assert len(sources) == 2 and all(command.endswith(IDENTITY) for command in sources)
    assert all(f"--source-code-path {folder} --release-id {release} " in command for command in sources)
    prune = [command for command in commands if "app_release_prune" in command]
    assert len(prune) == 1 and prune[0].endswith(IDENTITY)


def _with_env_local(checkout: Path, **overrides: str | None) -> None:
    values: dict[str, str | None] = {
        **paychex_shaped_profile(),
        "DATABRICKS_WAREHOUSE_ID": "warehouse-1",
        "GENIE_SPACE_ID": "space-1",
        **overrides,
    }
    kept = {name: value for name, value in values.items() if value is not None}
    (checkout / ".env.local").write_text(env_file_text(kept), encoding="utf-8")


def test_a_refused_profile_stops_the_dry_run_before_any_planned_command(tmp_path: Path) -> None:
    checkout, env, log = sandbox(tmp_path)
    _with_env_local(checkout, MIP_EXPECT_WORKSPACE_ID=None)

    result = dry_run(checkout, env)

    assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
    assert "MIP_EXPECT_WORKSPACE_ID" in result.stderr and "the client profile was refused" in result.stderr
    assert planned_commands(result.stdout) == []
    assert not log.exists(), "refused before any Databricks call, --version included"


def test_a_demo_refresh_mailing_the_deployer_sp_stops_the_dry_run_before_any_bundle_command(
    tmp_path: Path,
) -> None:
    """The paychex-shaped profile has the weekly refresh on: refused before any apply could write it.

    Only the read-only live checks, which pin the deploying user to that
    service principal, are planned ahead of the refusal.
    """

    checkout, env, log = sandbox(tmp_path)

    result = dry_run(checkout, env)

    assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
    assert "MIP_SCHEDULED_REFRESH=on mails mip_scheduled_refresh's failures to the deploying user" in result.stderr
    assert "scheduled refresh:" not in ANSI.sub("", result.stdout)
    assert planned_commands(result.stdout) == [".venv/bin/python -m tools.clients.live_checks"]
    assert log.read_text(encoding="utf-8").splitlines() in ([], ["--version"]), "no Databricks CLI call"


def test_a_host_that_is_not_the_profiles_stops_the_dry_run_before_the_live_checks(tmp_path: Path) -> None:
    checkout, env, log = sandbox(tmp_path)
    # The shell's host wins for the profile; the dry-run auth bind plans .env.local's.
    env["DATABRICKS_HOST"] = "https://dbc-elsewhere.cloud.databricks.com"

    result = dry_run(checkout, env)

    assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
    assert "the deployer's workspace host is not the client profile's DATABRICKS_HOST" in result.stderr
    assert planned_commands(result.stdout) == []


def test_the_parked_deploy_dev_workflow_is_refused_before_any_databricks_call(tmp_path: Path) -> None:
    checkout, env, log = sandbox(tmp_path)
    ci = {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/deploy-dev.yml@refs/heads/main"}

    result = dry_run(checkout, {**env, **ci})

    assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
    assert "deploy-dev.yml is parked until a client environment is selected (M1-PR7)" in result.stderr
    assert not log.exists() or log.read_text(encoding="utf-8") == "", "refused before any Databricks call"
    assert "step 1" not in result.stdout


def test_a_pinned_lender_records_identifying_values_never_reach_the_transcript(tmp_path: Path) -> None:
    # Review of #298: run echoes every argument, and GitHub masks only a
    # whole secret, never the legal name or NMLS id taken from it. Under
    # -t client the record and its pin are the customer profile's: a demo
    # profile refuses them.
    record = {**LENDER_IDENTITY, "legal_name": "Zyrplax Harbor Lending", "nmls_id": "7788991", "tenant_id": "zyrplax"}
    text = json.dumps(record)
    checkout, env, _log = sandbox(tmp_path)
    _with_env_local(
        checkout,
        MIP_INSTALL_CLASS="customer",
        MIP_APP_ENV="prod",
        MIP_SCHEDULED_REFRESH="off",
        MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=None,
        MIP_PRESENTER_MODE=None,
        MIP_SEED_OPERATOR_EMAIL=None,
        MIP_LENDER_IDENTITY_JSON=text,
        MIP_LENDER_IDENTITY_SHA256=lender_identity_digest(text),
    )

    result = dry_run(checkout, env)

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    transcript = ANSI.sub("", result.stdout + result.stderr)
    assert f"client:     {CLIENT} (customer install; profile resolved)" in transcript
    assert f"[deploy] lender identity: digest-pinned record {lender_identity_digest(text)[:12]}" in transcript
    for value in (record["legal_name"], "Zyrplax", record["nmls_id"]):
        assert value not in transcript, value
    assert "tools.branding.lender_mark stage --identity-env" in transcript, "the stage step ran"


# ------------------------------------------ rendered SQL and today's names


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(f"{path.relative_to(root).as_posix()}\t{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def _client_validate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """bundle_env ``validate -t client`` with the profile; what the CLI call would have seen."""

    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile()), encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    for name in [name for name in os.environ if name.startswith(_AMBIENT_PREFIXES)]:
        monkeypatch.delenv(name)
    rendered = tmp_path / "rendered"
    renders: list[dict[str, Any]] = []

    def render_into_tmp(**kwargs: Any) -> tuple[int, int, int]:
        renders.append(kwargs)
        return RENDER(**kwargs, dest_root=rendered)

    seen: dict[str, Any] = {}

    def fake_cli(command: list[str], *, env: dict[str, str], check: bool) -> Any:
        seen.update(command=command, env=dict(env), digest=_tree_digest(rendered))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.render_sql, "render", render_into_tmp)
    monkeypatch.setattr(bundle_env.subprocess, "run", fake_cli)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "client"])
    assert bundle_env.main() == 0
    return {**seen, "renders": renders}


def test_the_client_render_is_the_retired_dev_targets_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dev target rendered catalog ``mip``, the canonical Cotality source and the demo feeds on (its
    default); so must the client."""

    seen = _client_validate(monkeypatch, tmp_path)
    reference = tmp_path / "dev-render"
    RENDER(catalog="mip", demo_first_party_enabled=True, dest_root=reference)

    # A demo profile sets no lender: the Summit demo identity, unpinned, so
    # the render writes the seed's own tenant rows (#298).
    demo_lender = reviewed_lender_identity_from_env({})
    assert not demo_lender.pinned and demo_lender.lender_name == "Summit Mortgage"
    canonical = {"catalog": "mip", "cotality": source_names.CANONICAL, "lender": demo_lender}
    assert seen["renders"][0] == {**canonical, "demo_first_party_enabled": True}
    assert seen["digest"] == _tree_digest(reference)
    assert seen["command"] == ["databricks", "bundle", "validate", "-t", "client"]
    # After the CLI exits the tree is restored with the feeds off, as before.
    assert seen["renders"][1:] == [{**canonical, "demo_first_party_enabled": False}]


def test_the_client_validate_exports_todays_names_and_the_client_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    child = _client_validate(monkeypatch, tmp_path)["env"]
    defaults = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))["variables"]

    # Today's names: exactly the bundle defaults the live install runs on.
    for name in NAME_VARIABLES:
        assert child[f"BUNDLE_VAR_{name}"] == defaults[name]["default"], name
    assert child["MIP_APP_NAME"] == "mip-app" and child["MIP_LAKEBASE_INSTANCE"] == "mip-app-state"
    assert child["BUNDLE_VAR_lender_name"] == "Summit Mortgage"
    # The one intended difference from the retired dev render: the verifier is
    # the profile's MIP_IDENTITY_IDS_JSON.verifier, never the environment's
    # MIP_AI_GATEWAY_VERIFIER_CLIENT_ID (which the dev target read, and without
    # which it rendered the placeholder).
    assert child["BUNDLE_VAR_ai_gateway_verifier_client_id"] == IDENTITY_IDS["verifier"]
    assert "MIP_AI_GATEWAY_VERIFIER_CLIENT_ID" not in paychex_shaped_profile()
    # The client target's own variables, from the profile alone.
    assert child["BUNDLE_VAR_client_key"] == CLIENT
    assert child["BUNDLE_VAR_deployer_principal"] == DEPLOYER
    assert child["BUNDLE_VAR_lakebase_readable_secondaries"] == "false"
    assert "BUNDLE_VAR_dashboard_parent_path" not in child and "BUNDLE_VAR_jobs_principal" not in child


def test_an_ambient_client_variable_never_reaches_the_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in [name for name in os.environ if name.startswith(_AMBIENT_PREFIXES)]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("BUNDLE_VAR_client_key", "another-client")
    monkeypatch.setenv("BUNDLE_VAR_jobs_principal", IDENTITY_IDS["admin"])
    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile()), encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    seen: dict[str, str] = {}

    def fake_cli(_command: list[str], *, env: dict[str, str], check: bool) -> Any:
        seen.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_cli)
    for target, expected in (("client", CLIENT), ("ci", None)):
        seen.clear()
        monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", target])
        assert bundle_env.main() == 0, capsys.readouterr().err
        assert seen.get("BUNDLE_VAR_client_key") == expected, target
        assert "BUNDLE_VAR_jobs_principal" not in seen, target


@pytest.mark.parametrize("alias", ["MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", "DATABRICKS_VERIFIER_CLIENT_ID"])
def test_a_verifier_alias_that_is_not_the_profiles_is_refused_before_the_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], alias: str
) -> None:
    """An ambient verifier id can neither replace the profile's nor be silently dropped."""

    for name in [name for name in os.environ if name.startswith(_AMBIENT_PREFIXES)]:
        monkeypatch.delenv(name)
    monkeypatch.setenv(alias, IDENTITY_IDS["admin"])
    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile()), encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path / "bundle-root")
    calls: list[list[str]] = []
    monkeypatch.setattr(bundle_env.subprocess, "run", lambda command, **_kwargs: calls.append(command))
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "client"])

    assert bundle_env.main() == 2
    err = capsys.readouterr().err
    assert f"{alias}: must equal MIP_IDENTITY_IDS_JSON.verifier" in err
    assert IDENTITY_IDS["admin"] not in err and calls == []
