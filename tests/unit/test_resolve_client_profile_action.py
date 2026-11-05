"""The resolve-client-profile composite action (M1-PR7): mask first, then write, then public outputs.

``.github/actions/resolve-client-profile`` resolves the client profile from
the job's ``toJSON(vars)`` and ``toJSON(secrets)``. Its first step registers
every identifying leaf and derived spelling as a runner mask; only then does
``tools.clients.ci_profile`` write ``.env.local`` (public and identifying
controls, never a secret) and the github-oidc ``DEFAULT`` profile, and output
public values. These tests run the action's own ``run`` lines.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from dotenv import dotenv_values

from tests.fixtures.client_profiles import IDENTITY_IDS, core_tier_profile, split_contexts
from tools.clients import ci_profile
from tools.clients import profile as views
from tools.clients.environment import from_github, resolve
from tools.clients.schema import FIELD_CLASS, FieldClass

REPO = Path(__file__).resolve().parents[2]
ACTION = REPO / ".github" / "actions" / "resolve-client-profile" / "action.yml"
CLIENT = "client-one"


def _action() -> dict[str, Any]:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def _values(**overrides: str | None) -> dict[str, str]:
    # An upper-case application id: the deploy writes ids in lower case, so the
    # lower-case spelling must be a mask too.
    ids = {**IDENTITY_IDS, "deployer": "ABCDEF00-0000-4000-8000-000000000001"}
    ids = {role: value for role, value in ids.items() if role not in ("verifier", "runtime", "proxy")}
    return core_tier_profile(MIP_IDENTITY_IDS_JSON=json.dumps(ids), **overrides)


def _run_action(tmp_path: Path, values: dict[str, str], *, actions: bool = True) -> tuple[list[subprocess.CompletedProcess[str]], Path]:
    return _run_contexts(tmp_path, *split_contexts(values), actions=actions)


def _run_contexts(
    tmp_path: Path, variables: dict[str, str], secrets: dict[str, str], *, actions: bool = True
) -> tuple[list[subprocess.CompletedProcess[str]], Path]:
    """Run each step's ``run`` under its own env, as the runner would, in a scratch checkout."""

    home, checkout = tmp_path / "home", tmp_path / "checkout"
    home.mkdir()
    checkout.mkdir()
    output = tmp_path / "github_output"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").write_text(f'#!/usr/bin/env bash\nexec {sys.executable} "$@"\n', encoding="utf-8")
    (bin_dir / "python").chmod(0o755)
    inputs = {"vars-json": json.dumps(variables), "secrets-json": json.dumps(secrets), "environment": CLIENT}
    results = []
    for step in _action()["runs"]["steps"]:
        env = {name: value.replace("${{ inputs.vars-json }}", inputs["vars-json"]).replace(
            "${{ inputs.secrets-json }}", inputs["secrets-json"]).replace("${{ inputs.environment }}", inputs["environment"])
            for name, value in step["env"].items()}
        results.append(
            subprocess.run(
                ["bash", "-c", step["run"]],
                cwd=checkout,
                env={
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "HOME": str(home),
                    "PYTHONPATH": str(REPO),
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_ACTIONS": "true" if actions else "false",
                    **env,
                },
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
        )
        if results[-1].returncode:
            break
    return results, tmp_path


def _profile(values: dict[str, str]) -> Any:
    return resolve(from_github(*split_contexts(values), environment=CLIENT))


def test_the_action_masks_first_and_every_step_reads_the_contexts_only_from_its_inputs() -> None:
    action = _action()
    steps = action["runs"]["steps"]

    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) == {"environment", "vars-json", "secrets-json"}
    assert steps[0]["name"] == "Mask every identifying profile value"
    assert steps[0]["run"].split()[:4] == ["python", "-m", "tools.clients", "mask"]
    assert steps[1]["id"] == "write" and "tools.clients.ci_profile" in steps[1]["run"]
    for step in steps:
        assert step["env"] == {
            "MIP_VARS_JSON": "${{ inputs.vars-json }}",
            "MIP_SECRETS_JSON": "${{ inputs.secrets-json }}",
            "MIP_CLIENT_ENVIRONMENT": "${{ inputs.environment }}",
        }
        assert "${{" not in step["run"], "inputs reach a step through env, never interpolated into a script"
        assert "secrets." not in json.dumps(step) and "vars." not in json.dumps(step)
    for output in action["outputs"].values():
        assert output["value"].startswith("${{ steps.write.outputs.")


def test_the_action_writes_the_controls_and_the_oidc_profile_and_outputs_public_values_only(tmp_path: Path) -> None:
    values = _values()
    results, root = _run_action(tmp_path, values)
    assert [result.returncode for result in results] == [0, 0], [result.stderr for result in results]
    profile = _profile(values)
    env_local = root / "checkout" / ".env.local"
    cfg = root / "home" / ".databrickscfg"

    assert env_local.stat().st_mode & 0o777 == 0o600 and cfg.stat().st_mode & 0o777 == 0o600
    # The whole profile, as an operator's .env.local holds it: deploy.sh
    # un-exports every credential, so its children read the secrets here.
    assert dict(dotenv_values(env_local, interpolate=False)) == {
        **views.deploy_controls(profile),
        **ci_profile.secret_fields(profile),
    }
    assert set(ci_profile.secret_fields(profile)) == {
        name for name, value in values.items() if FIELD_CLASS[name] is FieldClass.SECRET
    }
    assert cfg.read_text(encoding="utf-8") == (
        "[DEFAULT]\nhost  = https://dbc-unit.cloud.databricks.com\nauth_type = github-oidc\n"
        "client_id = ABCDEF00-0000-4000-8000-000000000001\n"
    )
    outputs = dict(line.split("=", 1) for line in (root / "github_output").read_text(encoding="utf-8").splitlines())
    assert outputs == {
        "client": CLIENT,
        "install-class": "demo",
        "agentic-tier": "off",
        "review-mode": "single_maintainer_audited",
        "app-name": "mip-app",
        "profile-digest": views.digest(profile),
    }


def test_the_mask_step_registers_every_identifying_and_derived_value_before_anything_else(tmp_path: Path) -> None:
    values = _values()
    results, _root = _run_action(tmp_path, values)
    profile = _profile(values)
    masked = {line.removeprefix("::add-mask::") for line in results[0].stdout.splitlines() if line.startswith("::add-mask::")}

    assert masked == {*views.masks(profile), *views.derived_masks(profile)}
    assert "abcdef00-0000-4000-8000-000000000001" in masked, "the lower-case spelling the deploy writes"
    assert "registered" in results[0].stderr
    # Every other line of either step is value-free.
    printed = "\n".join(
        line
        for result in results
        for line in (result.stdout + result.stderr).splitlines()
        if not line.startswith("::add-mask::")
    )
    for value in (*masked, *(value for name, value in values.items() if FIELD_CLASS[name] is FieldClass.SECRET)):
        assert value not in printed


def test_outside_actions_the_mask_step_registers_nothing(tmp_path: Path) -> None:
    results, _root = _run_action(tmp_path, _values(), actions=False)
    assert [result.returncode for result in results] == [0, 0]
    assert "::add-mask::" not in "".join(result.stdout + result.stderr for result in results)


def test_an_identifying_variable_refuses_at_the_mask_step_before_any_file_is_written(tmp_path: Path) -> None:
    values = _values()
    variables, secrets = split_contexts(values)
    host = secrets.pop("DATABRICKS_HOST")
    variables["DATABRICKS_HOST"] = host
    results, root = _run_contexts(tmp_path, variables, secrets)
    assert results[0].returncode == 2 and len(results) == 1
    assert "DATABRICKS_HOST: is identifying" in results[0].stderr
    assert host not in results[0].stdout + results[0].stderr
    assert not (root / "checkout" / ".env.local").exists() and not (root / "home" / ".databrickscfg").exists()


def test_ci_profile_never_overwrites_either_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    variables, secrets = split_contexts(_values())
    monkeypatch.setenv("MIP_VARS_JSON", json.dumps(variables))
    monkeypatch.setenv("MIP_SECRETS_JSON", json.dumps(secrets))
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    env_local = tmp_path / ".env.local"
    env_local.write_text("an operator's own file\n", encoding="utf-8")
    argv = [
        "--vars-json", "env:MIP_VARS_JSON", "--secrets-json", "env:MIP_SECRETS_JSON", "--environment", CLIENT,
        "--env-local", str(env_local), "--databrickscfg", str(tmp_path / "cfg"),
    ]

    assert ci_profile.main(argv) == 2
    assert ".env.local: already exists; the deploy job writes it once" in capsys.readouterr().err
    assert env_local.read_text(encoding="utf-8") == "an operator's own file\n"
    assert not (tmp_path / "cfg").exists()
    os.remove(env_local)
    assert ci_profile.main(argv) == 0
