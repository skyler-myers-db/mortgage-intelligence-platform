"""The GitHub OIDC deployer proof (M1-PR7): read-only, booleans only, the profile's workspace and metastore.

tools/clients/oidc_proof.py runs as deploy-dev.yml's "Prove the GitHub OIDC
deployer identity" step against a recording fake SDK and CLI here. Its
expectations come from the ``.env.local`` the resolve-client-profile action
wrote; the metastore-owner and warehouse checks are gone, the workspace id
and metastore id checks are new.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOYER = "00000000-0000-4000-8000-000000000001"
HOST = "https://dbc-oidc-proof.cloud.databricks.com"
WORKSPACE_ID = "4200000001"
METASTORE_ID = "00000000-0000-4000-8000-00000000a001"
FAKE_SDK = """
import json
import os
from types import SimpleNamespace


def _log(call):
    with open(os.environ["FAKE_DATABRICKS_CALLS"], "a", encoding="utf-8") as handle:
        handle.write(call + "\\n")


class WorkspaceClient:
    def __init__(self, *, profile=None, **kwargs):
        _log(f"sdk:WorkspaceClient(profile={profile}, extra={sorted(kwargs)})")
        with open(os.environ["FAKE_DATABRICKS_SPEC"], encoding="utf-8") as handle:
            self._spec = json.load(handle)
        if self._spec.get("construct_error"):
            raise PermissionError("token exchange refused")
        self.config = SimpleNamespace(**self._spec["config"])
        self.current_user = SimpleNamespace(me=self._me)
        self.metastores = SimpleNamespace(current=self._current)

    def _me(self):
        _log("sdk:current_user.me")
        me = self._spec["me"]
        return SimpleNamespace(user_name=me["user_name"], groups=[SimpleNamespace(display=g) for g in me["groups"]])

    def get_workspace_id(self):
        _log("sdk:get_workspace_id")
        return self._spec["workspace_id"]

    def _current(self):
        _log("sdk:metastores.current")
        return SimpleNamespace(metastore_id=self._spec["metastore_id"])
"""
FAKE_CLI = """#!/usr/bin/env bash
printf 'cli:%s profile_env=%s oidc_env=%s\\n' "$*" "${DATABRICKS_CONFIG_PROFILE:-}" \\
  "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:+set}" >> "$FAKE_DATABRICKS_CALLS"
if [ -n "${FAKE_CLI_EXIT:-}" ]; then
  echo "cli refused" >&2
  exit "$FAKE_CLI_EXIT"
fi
cat "$FAKE_CLI_ME"
"""
READ_ONLY_CALLS = [
    "sdk:WorkspaceClient(profile=DEFAULT, extra=[])",
    "sdk:current_user.me",
    "sdk:get_workspace_id",
    "sdk:metastores.current",
    "cli:current-user me --profile DEFAULT -o json profile_env=DEFAULT oidc_env=set",
]


def _profile(host: str = HOST, *, auth_type: str = "github-oidc", client_id: str = DEPLOYER, extra: str = "") -> str:
    return f"[DEFAULT]\nhost  = {host}\nauth_type = {auth_type}\nclient_id = {client_id}\n{extra}"


def _run(
    tmp_path: Path,
    *,
    spec: dict[str, Any] | None = None,
    profile: str | None = None,
    cli_me: str | None = None,
    env: dict[str, str] | None = None,
    env_local: dict[str, str] | None = None,
    cli: bool = True,
    bundle: str = "targets:\n  client: {}\n",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    sdk_root = tmp_path / "sdk"
    (sdk_root / "databricks" / "sdk").mkdir(parents=True)
    (sdk_root / "databricks" / "__init__.py").write_text("", encoding="utf-8")
    (sdk_root / "databricks" / "sdk" / "__init__.py").write_text(FAKE_SDK, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if cli:
        (bin_dir / "databricks").write_text(FAKE_CLI, encoding="utf-8")
        (bin_dir / "databricks").chmod(0o755)
    (bin_dir / "python").write_text(f'#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} "$@"\n', encoding="utf-8")
    (bin_dir / "python").chmod(0o755)
    home, checkout = tmp_path / "home", tmp_path / "checkout"
    home.mkdir()
    checkout.mkdir()
    (home / ".databrickscfg").write_text(_profile() if profile is None else profile, encoding="utf-8")
    (checkout / "databricks.yml").write_text(bundle, encoding="utf-8")
    controls = {
        "MIP_CI_DEPLOYER_CLIENT_ID": DEPLOYER,
        "DATABRICKS_HOST": HOST,
        "MIP_EXPECT_WORKSPACE_ID": WORKSPACE_ID,
        "MIP_EXPECT_METASTORE_ID": METASTORE_ID,
        **(env_local or {}),
    }
    (checkout / ".env.local").write_text("".join(f"{k}='{v}'\n" for k, v in controls.items()), encoding="utf-8")
    full_spec = {
        "config": {"auth_type": "github-oidc", "client_id": DEPLOYER, "host": HOST},
        "me": {"user_name": DEPLOYER, "groups": ["users", "admins"]},
        "workspace_id": int(WORKSPACE_ID),
        "metastore_id": METASTORE_ID,
        **(spec or {}),
    }
    (tmp_path / "spec.json").write_text(json.dumps(full_spec), encoding="utf-8")
    (tmp_path / "cli-me.json").write_text(
        json.dumps({"userName": DEPLOYER, "groups": [{"display": "admins"}]}) if cli_me is None else cli_me, encoding="utf-8"
    )
    calls = tmp_path / "calls.log"
    result = subprocess.run(
        ["bash", "-c", "python -m tools.clients.oidc_proof --env-file .env.local"],
        cwd=checkout,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(home),
            "PYTHONPATH": f"{sdk_root}:{REPO}",
            "DATABRICKS_CONFIG_PROFILE": "DEFAULT",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.example/request",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "github-request-token",
            "FAKE_DATABRICKS_SPEC": str(tmp_path / "spec.json"),
            "FAKE_DATABRICKS_CALLS": str(calls),
            "FAKE_CLI_ME": str(tmp_path / "cli-me.json"),
            **(env or {}),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    return result, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def _checks(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if re.fullmatch(r"[a-z_A-Z0-9]+=(?:true|false).*", line))


def test_the_proof_passes_and_prints_only_booleans(tmp_path: Path) -> None:
    result, calls = _run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    checks = _checks(result.stdout)
    assert set(checks.values()) == {"true"}
    assert list(checks) == [
        "deployer_client_id_set",
        "github_oidc_request_env_present",
        "no_ambient_auth_override",
        "profile_holds_only_host_auth_type_client_id",
        "profile_auth_type_is_github_oidc",
        "profile_client_id_is_the_deployer",
        "profile_host_is_DATABRICKS_HOST",
        "bundle_target_commits_no_host",
        "sdk_authenticates_with_the_github_oidc_profile",
        "me_is_the_deployer",
        "me_in_admins",
        "workspace_id_is_MIP_EXPECT_WORKSPACE_ID",
        "metastore_id_is_MIP_EXPECT_METASTORE_ID",
        "cli_authenticates",
        "cli_me_is_the_deployer",
        "cli_me_in_admins",
    ]
    assert result.stdout.splitlines()[-1] == "GitHub OIDC deployer identity proven."
    assert len(result.stdout.splitlines()) == len(checks) + 1, "booleans and the verdict, nothing else"
    for value in (DEPLOYER, HOST, WORKSPACE_ID, METASTORE_ID, "github-request-token"):
        assert value not in result.stdout + result.stderr
    assert calls == READ_ONLY_CALLS
    assert "metastore_owner" not in result.stdout and "warehouse" not in result.stdout, "both retired (M1-PR7)"


STATIC = {
    "env-local-host-differs": ({"env_local": {"DATABRICKS_HOST": "https://other.example"}}, "profile_host_is_DATABRICKS_HOST"),
    "a-committed-bundle-host": ({"bundle": f"targets:\n  client:\n    workspace:\n      host: {HOST}\n"}, "bundle_target_commits_no_host"),
    "pat-profile": ({"profile": _profile(auth_type="pat")}, "profile_auth_type_is_github_oidc"),
    "stored-token": ({"profile": _profile(extra="token = a-stored-pat\n")}, "profile_holds_only_host_auth_type_client_id"),
    "profile-client-differs": ({"profile": _profile(client_id="another-service-principal")}, "profile_client_id_is_the_deployer"),
    "deployer-unset": ({"env_local": {"MIP_CI_DEPLOYER_CLIENT_ID": ""}}, "deployer_client_id_set"),
    "no-id-token-permission": ({"env": {"ACTIONS_ID_TOKEN_REQUEST_TOKEN": ""}}, "github_oidc_request_env_present"),
    **{
        f"ambient-{name.lower()}": ({"env": {name: "set-by-a-step"}}, "no_ambient_auth_override")
        for name in ("DATABRICKS_CLIENT_ID", "DATABRICKS_TOKEN", "DATABRICKS_AUTH_TYPE", "DATABRICKS_HOST", "DATABRICKS_CONFIG_FILE")
    },
}


@pytest.mark.parametrize("case", sorted(STATIC))
def test_a_wrong_profile_refuses_before_any_network_call(tmp_path: Path, case: str) -> None:
    arrangement, failed = STATIC[case]
    result, calls = _run(tmp_path, **arrangement)

    assert result.returncode == 2, result.stdout + result.stderr
    assert _checks(result.stdout)[failed] == "false"
    assert "::error::GitHub OIDC deployer identity proof failed" in result.stdout
    assert calls == [], "no SDK client, no CLI: nothing reaches a workspace"
    assert "a-stored-pat" not in result.stdout + result.stderr


NETWORK = {
    "another-identity": ({"spec": {"me": {"user_name": "someone-else", "groups": ["admins"]}}}, ["me_is_the_deployer"]),
    "not-workspace-admin": ({"spec": {"me": {"user_name": DEPLOYER, "groups": ["users"]}}}, ["me_in_admins"]),
    "another-workspace": ({"spec": {"workspace_id": 4299999999}}, ["workspace_id_is_MIP_EXPECT_WORKSPACE_ID"]),
    "another-metastore": (
        {"spec": {"metastore_id": "00000000-0000-4000-8000-00000000ffff"}},
        ["metastore_id_is_MIP_EXPECT_METASTORE_ID"],
    ),
    "sdk-exchange-refused": ({"spec": {"construct_error": True}}, ["sdk_authenticates_with_the_github_oidc_profile"]),
    "sdk-host-drift": (
        {"spec": {"config": {"auth_type": "github-oidc", "client_id": DEPLOYER, "host": "https://another.example.invalid"}}},
        ["sdk_authenticates_with_the_github_oidc_profile"],
    ),
    "cli-refused": ({"env": {"FAKE_CLI_EXIT": "1"}}, ["cli_authenticates", "cli_me_is_the_deployer", "cli_me_in_admins"]),
    "cli-not-installed": ({"cli": False}, ["cli_authenticates", "cli_me_is_the_deployer", "cli_me_in_admins"]),
    "cli-another-identity": ({"cli_me": json.dumps({"userName": "someone-else", "groups": [{"display": "admins"}]})}, ["cli_me_is_the_deployer"]),
}


@pytest.mark.parametrize("case", sorted(NETWORK))
def test_the_wrong_identity_workspace_or_metastore_refuses(tmp_path: Path, case: str) -> None:
    arrangement, failed = NETWORK[case]
    result, calls = _run(tmp_path, **arrangement)

    assert result.returncode == 2, result.stdout + result.stderr
    assert [name for name, value in _checks(result.stdout).items() if not value.startswith("true")] == failed
    assert set(calls) <= set(READ_ONLY_CALLS), "read-only calls only"
    for message in ("token exchange refused", "cli refused", "someone-else", "4299999999", "ffff", "another.example.invalid"):
        assert message not in result.stdout + result.stderr, "never an exception message or a value"


def test_an_upper_case_deployer_id_still_proves_against_the_lower_case_scim_name(tmp_path: Path) -> None:
    upper = DEPLOYER.upper()
    result, _calls = _run(
        tmp_path,
        env_local={"MIP_CI_DEPLOYER_CLIENT_ID": upper},
        profile=_profile(client_id=upper),
        spec={"config": {"auth_type": "github-oidc", "client_id": upper, "host": HOST}},
    )
    assert result.returncode == 0, result.stdout
