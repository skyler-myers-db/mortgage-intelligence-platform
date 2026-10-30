"""The emergency recipe in docs/deployment.md still runs, against the legacy dev root (M1-PR2).

Until the live install is adopted (M2-PR6), every App ship to it goes through
the recipe block under "The emergency recipe, command by command (legacy dev
root)": stage, release id, publish, converge, payload, ``apps deploy``. The
``dev`` target left databricks.yml in M1-PR2, so the block names the legacy
root with explicit ``--expected-target dev --expected-root-leaf dev`` flags.

This test reads that block, substitutes synthetic values for its
placeholders and runs every Python command through the tool's own ``main``:
stage with its stage step stubbed (it would build a real closure), the
release id for real, the publish and the converge for real against an
in-memory workspace, and the payload for real. Nothing reaches the network.
The ``databricks apps deploy`` line is checked for its shape only. The three
commands that call the workspace (publish, converge, deploy) must all name the
same live-workspace CLI profile; the others name none.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
from databricks.sdk.service.workspace import ObjectType

from tests.fixtures.fake_workspace import FakeWorkspaceClient
from tools.databricks import (
    app_deploy_payload,
    app_release_contract,
    app_release_source,
    app_runtime_closure,
)
from tools.databricks import converge_static_app_source as converge

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_DOC = ROOT / "docs" / "deployment.md"
HEADING = "### The emergency recipe, command by command (legacy dev root)"
OWNER = "owner@lender.example"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
LEGACY_ROOT = f"/Workspace/Users/{OWNER}/.bundle/mortgage-intelligence-platform/dev"
FILES = {
    "app.yaml": b"command: ['python', '-m', 'backend.runtime']\n",
    "requirements.txt": b"# deps-digest\n-c uv.lock\n",
    "backend/__init__.py": b"",
    "frontend/dist/index.html": b"<html></html>\n",
}


def recipe_commands() -> list[str]:
    """Each command of the first ``bash`` block after the heading, continuations joined."""

    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    section = text[text.index(HEADING) :]
    block = section.split("```bash\n", 1)[1].split("\n```", 1)[0]
    return [line for line in block.replace("\\\n", " ").splitlines() if line.strip()]


def _argv(command: str, values: dict[str, str]) -> tuple[dict[str, str], list[str], str | None]:
    """(env prefix, argv, stdout redirect) with every ``<placeholder>`` filled."""

    filled = re.sub(r"<([a-z0-9-]+)>", lambda match: values[match.group(1)], command)
    tokens = shlex.split(filled)
    env: dict[str, str] = {}
    while tokens and re.fullmatch(r"[A-Z_][A-Z0-9_]*=.*", tokens[0]):
        name, value = tokens.pop(0).split("=", 1)
        env[name] = value
    redirect = None
    if ">" in tokens:
        index = tokens.index(">")
        redirect = tokens[index + 1]
        tokens = tokens[:index]
    return env, tokens, redirect


def _module_args(tokens: list[str], module: str) -> list[str]:
    assert tokens[:3] == ["python", "-m", module], tokens
    return tokens[3:]


@pytest.fixture
def staged(tmp_path: Path) -> dict[str, str]:
    stage_dir = tmp_path / "stage"
    for relpath, data in FILES.items():
        (stage_dir / relpath).parent.mkdir(parents=True, exist_ok=True)
        (stage_dir / relpath).write_bytes(data)
    manifest_path = tmp_path / "manifest.json"
    manifest = app_release_contract.write_manifest(
        manifest_path, commit=COMMIT, entries=app_release_contract.manifest_entries(stage_dir)
    )
    return {
        "sha": COMMIT,
        "stage-dir": str(stage_dir),
        "manifest": str(manifest_path),
        "release-id": manifest.release_id,
        "owner": OWNER,
        "lakebase-instance": "mip-app-state",
        "payload": str(tmp_path / "payload.json"),
        "app-name": "mip-app",
        "profile": "live-workspace",
    }


def test_the_recipe_is_the_six_reviewed_commands_in_order() -> None:
    commands = [shlex.split(command.split(" > ", 1)[0]) for command in recipe_commands()]
    heads = [
        [token for token in command if not re.fullmatch(r"[A-Z_][A-Z0-9_]*=.*", token)][:4]
        for command in commands
    ]
    assert heads == [
        ["python", "-m", "tools.databricks.app_runtime_closure", "stage"],
        ["python", "-m", "tools.databricks.app_release_contract", "release-id"],
        ["python", "-m", "tools.databricks.app_release_source", "publish"],
        ["python", "-m", "tools.databricks.converge_static_app_source", "--source-code-path"],
        ["python", "-m", "tools.databricks.app_deploy_payload", "--source-code-path"],
        ["databricks", "apps", "deploy", "<app-name>"],
    ]


def test_every_bundle_identity_check_names_the_legacy_dev_root_explicitly() -> None:
    publish, converge_command = recipe_commands()[2:4]
    for command in (publish, converge_command):
        assert "--expected-principal <owner>" in command
        assert command.rstrip().endswith("--expected-target dev --expected-root-leaf dev"), command
    legacy = "/Workspace/Users/<owner>/.bundle/mortgage-intelligence-platform/dev/"
    assert f"--files-root {legacy}files " in publish
    for command in recipe_commands()[3:5]:
        assert f"--source-code-path {legacy}app-src/<release-id> " in command


def _profile(command: str, values: dict[str, str]) -> str | None:
    """The CLI profile ``command`` names: its env prefix, or the ``databricks`` CLI's ``-p``."""

    env, tokens, _ = _argv(command, values)
    named = env.get("DATABRICKS_CONFIG_PROFILE")
    if tokens[0] == "databricks" and "-p" in tokens:
        assert named is None, command
        named = tokens[tokens.index("-p") + 1]
    return named


def test_every_workspace_call_names_the_same_live_profile(staged: dict[str, str]) -> None:
    """Publish, converge and deploy name ``<profile>``; a Python tool would otherwise take DEFAULT."""

    commands = recipe_commands()
    profiles = [_profile(command, staged) for command in commands]

    assert profiles == [None, None, "live-workspace", "live-workspace", None, "live-workspace"]
    for command in (commands[2], commands[3]):
        env, _, _ = _argv(command, staged)
        assert set(env) == {"DATABRICKS_CONFIG_PROFILE"}, command


def test_stage_parses(monkeypatch: pytest.MonkeyPatch, staged: dict[str, str]) -> None:
    parsed: list[Any] = []
    monkeypatch.setattr(app_runtime_closure, "_stage", lambda args: parsed.append(args) or 0)
    _, tokens, _ = _argv(recipe_commands()[0], staged)

    assert app_runtime_closure.main(_module_args(tokens, "tools.databricks.app_runtime_closure")) == 0
    (args,) = parsed
    assert (args.command, args.ref, str(args.out), str(args.manifest_out)) == (
        "stage", COMMIT, staged["stage-dir"], staged["manifest"]
    )
    assert args.dist == Path("frontend/dist") and args.require_brotli_check is True


def test_release_id_runs(staged: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    _, tokens, _ = _argv(recipe_commands()[1], staged)

    assert app_release_contract.main(_module_args(tokens, "tools.databricks.app_release_contract")) == 0
    assert capsys.readouterr().out.strip() == staged["release-id"]


def test_publish_and_converge_run_against_the_legacy_root(
    monkeypatch: pytest.MonkeyPatch, staged: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    workspace = FakeWorkspaceClient()
    release = f"{LEGACY_ROOT}/app-src/{staged['release-id']}"
    publish_env, publish, _ = _argv(recipe_commands()[2], staged)
    converge_env, converge_command, _ = _argv(recipe_commands()[3], staged)
    profiles: list[str | None] = []

    def client() -> FakeWorkspaceClient:
        # The profile each command's env prefix sets, as its client sees it.
        profiles.append(os.environ.get("DATABRICKS_CONFIG_PROFILE"))
        return workspace

    with monkeypatch.context() as scoped:
        for name, value in publish_env.items():
            scoped.setenv(name, value)
        published = app_release_source.main(_module_args(publish, "tools.databricks.app_release_source"), client=client)
    with monkeypatch.context() as scoped:
        for name, value in converge_env.items():
            scoped.setenv(name, value)
        converged = converge.main(
            _module_args(converge_command, "tools.databricks.converge_static_app_source"), client=client
        )

    out = capsys.readouterr()
    assert (published, converged) == (0, 0), out.err
    api_release = release.removeprefix("/Workspace")
    uploaded = {
        path.removeprefix(f"{api_release}/"): node.data
        for path, node in workspace.workspace.nodes.items()
        if path.startswith(f"{api_release}/") and node.kind is ObjectType.FILE
    }
    assert uploaded == FILES, "the release folder sits in the legacy root, byte for byte"
    assert profiles and set(profiles) == {staged["profile"]}, "every client is built under the live profile"
    assert f"[app-release-source] {release}: {len(FILES)} uploaded" in out.out
    assert f"release {staged['release-id']} matches its manifest" in out.out


def test_the_legacy_flags_are_what_names_the_root(staged: dict[str, str]) -> None:
    """Without them the same publish refuses: the default leaf would not be the legacy root."""

    _, publish, _ = _argv(recipe_commands()[2], staged)
    args = _module_args(publish, "tools.databricks.app_release_source")
    retargeted = [("client" if value == "dev" else value) for value in args]

    assert app_release_source.main(retargeted, client=FakeWorkspaceClient) == 2


def test_payload_runs_and_names_the_legacy_release_folder(
    monkeypatch: pytest.MonkeyPatch, staged: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(app_deploy_payload, "_dotenv_overlay", dict)
    env, tokens, redirect = _argv(recipe_commands()[4], staged)
    for name in [name for name in os.environ if name.startswith(("MIP_", "APP_ENV"))]:
        monkeypatch.delenv(name)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert app_deploy_payload.main(_module_args(tokens, "tools.databricks.app_deploy_payload")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert redirect == staged["payload"] and env == {"MIP_GIT_SHA": COMMIT}
    assert payload["source_code_path"] == f"{LEGACY_ROOT}/app-src/{staged['release-id']}"
    env_vars = {item["name"]: item.get("value", item.get("value_from")) for item in payload["env_vars"]}
    assert env_vars["APP_ENV"] == "sandbox" and env_vars["MIP_DEFAULT_CATALOG"] == "mip"
    assert env_vars["MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED"] == "0"


def test_apps_deploy_names_the_payload_and_an_explicit_profile(staged: dict[str, str]) -> None:
    _, tokens, _ = _argv(recipe_commands()[5], staged)

    assert tokens == ["databricks", "apps", "deploy", "mip-app", "--json", f"@{staged['payload']}", "-p", "live-workspace"]
