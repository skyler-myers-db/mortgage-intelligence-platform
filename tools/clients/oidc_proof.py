"""Prove the GitHub OIDC deployer identity, read-only, before scripts/deploy.sh (M1-PR7).

deploy-dev.yml's "Prove the GitHub OIDC deployer identity" step runs this
after the resolve-client-profile action wrote ``.env.local`` and the
``DEFAULT`` profile. The expectations come from ``.env.local`` alone (the
client profile the action resolved from the job's GitHub Environment): the
deployer's application id (``MIP_CI_DEPLOYER_CLIENT_ID``, the profile's
``MIP_IDENTITY_IDS_JSON.deployer``), ``DATABRICKS_HOST``,
``MIP_EXPECT_WORKSPACE_ID`` and ``MIP_EXPECT_METASTORE_ID``.

Local checks first, and a failure stops before any network call:

* the deployer id is set, and the runner offers the ID-token request pair;
* no ambient ``DATABRICKS_HOST``, ``_TOKEN``, ``_AUTH_TYPE``, ``_CLIENT_ID``,
  ``_CLIENT_SECRET`` or ``_CONFIG_FILE`` overrides or redirects the profile;
* the profile holds exactly ``host``, ``auth_type = github-oidc`` and
  ``client_id``; its client id is the deployer and its host the profile's;
* the bundle's ``client`` target commits no host (M1-PR2).

Then, through the Python SDK and the Go CLI: the profile authenticates as the
deployer, which is in ``admins``; the workspace is ``MIP_EXPECT_WORKSPACE_ID``
and its current metastore ``MIP_EXPECT_METASTORE_ID``. The metastore-owner
check is gone with the cancelled metastore transfer, and the warehouse check
with M1-PR3 (the bundle state binds the warehouse). Every id read is a runner
mask the moment it is read. It prints one ``name=true|false`` line per check
(a failed call adds its exception class or the CLI's exit code, never a
message or a value) and exits 2 on any false check.
"""

from __future__ import annotations

import configparser
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from tools.clients.redact import emit_mask

TARGET = "client"
PROFILE = "DEFAULT"
# Ambient names that would override or redirect the profile.
OVERRIDES = (
    "DATABRICKS_HOST",
    "DATABRICKS_TOKEN",
    "DATABRICKS_AUTH_TYPE",
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_CLIENT_SECRET",
    "DATABRICKS_CONFIG_FILE",
)
EXPECTED = ("MIP_CI_DEPLOYER_CLIENT_ID", "DATABRICKS_HOST", "MIP_EXPECT_WORKSPACE_ID", "MIP_EXPECT_METASTORE_ID")
REQUEST_ENV = ("ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")


class Proof:
    """The ordered checks of one run, with a value-free note per failed call."""

    def __init__(self) -> None:
        self.checks: dict[str, bool] = {}
        self.notes: dict[str, str] = {}

    def probe(self, name: str, read: Callable[[], Any]) -> None:
        try:
            self.checks[name] = bool(read())
        except Exception as exc:  # the class only, never its message
            self.checks[name] = False
            self.notes[name] = type(exc).__name__

    @property
    def holds(self) -> bool:
        return all(self.checks.values())

    def lines(self) -> list[str]:
        return [
            f"{name}={'true' if ok else 'false'}{f' ({self.notes[name]})' if name in self.notes else ''}"
            for name, ok in self.checks.items()
        ]


def _same(left: object, right: object) -> bool:
    return bool(left) and str(left).strip() == str(right).strip()


def _same_folded(left: object, right: object) -> bool:
    return bool(left) and str(left).strip().casefold() == str(right).strip().casefold()


def _origin(value: object) -> str:
    return str(value or "").strip().rstrip("/")


def _admins(groups: Any) -> bool:
    return "admins" in {str(group or "").strip().casefold() for group in groups}


def _expectations(env_file: Path) -> dict[str, str]:
    found = dotenv_values(env_file, interpolate=False) if env_file.is_file() else {}
    return {name: str(found.get(name) or "").strip() for name in EXPECTED}


def local_checks(proof: Proof, expected: Mapping[str, str], environ: Mapping[str, str], home: Path, bundle: Path) -> str:
    """The checks that need no network; return the profile's host."""

    document = yaml.safe_load(bundle.read_text(encoding="utf-8")) or {}
    workspace = (((document.get("targets") or {}).get(TARGET) or {}).get("workspace")) or {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(home / ".databrickscfg", encoding="utf-8")
    # DEFAULT is configparser's own defaults section: always present, never has_section().
    profile = {key: value.strip() for key, value in parser[PROFILE].items()}
    deployer = expected["MIP_CI_DEPLOYER_CLIENT_ID"]
    proof.checks["deployer_client_id_set"] = bool(deployer)
    proof.checks["github_oidc_request_env_present"] = all(environ.get(name) for name in REQUEST_ENV)
    proof.checks["no_ambient_auth_override"] = not any(environ.get(name) for name in OVERRIDES)
    proof.checks["profile_holds_only_host_auth_type_client_id"] = set(profile) == {"host", "auth_type", "client_id"}
    proof.checks["profile_auth_type_is_github_oidc"] = profile.get("auth_type") == "github-oidc"
    proof.checks["profile_client_id_is_the_deployer"] = _same(profile.get("client_id"), deployer)
    proof.checks["profile_host_is_DATABRICKS_HOST"] = _same(_origin(profile.get("host")), _origin(expected["DATABRICKS_HOST"]))
    proof.checks["bundle_target_commits_no_host"] = not _origin(workspace.get("host"))
    return _origin(profile.get("host"))


def network_checks(proof: Proof, expected: Mapping[str, str], profile_host: str, runner: Callable[..., Any]) -> None:
    from databricks.sdk import WorkspaceClient

    deployer = expected["MIP_CI_DEPLOYER_CLIENT_ID"]
    state: dict[str, Any] = {}

    def authenticate() -> bool:
        state["client"] = client = WorkspaceClient(profile=PROFILE)
        state["me"] = client.current_user.me()
        config = client.config
        return (
            config.auth_type == "github-oidc"
            and _same_folded(config.client_id, deployer)
            and _same(_origin(config.host), profile_host)
        )

    proof.probe("sdk_authenticates_with_the_github_oidc_profile", authenticate)
    if "me" in state:
        client, me = state["client"], state["me"]
        proof.probe("me_is_the_deployer", lambda: _same_folded(emit_mask(str(me.user_name or "")), deployer))
        proof.probe("me_in_admins", lambda: _admins(group.display for group in me.groups or ()))
        proof.probe(
            "workspace_id_is_MIP_EXPECT_WORKSPACE_ID",
            lambda: _same(emit_mask(str(client.get_workspace_id() or "")), expected["MIP_EXPECT_WORKSPACE_ID"]),
        )
        proof.probe(
            "metastore_id_is_MIP_EXPECT_METASTORE_ID",
            lambda: _same_folded(
                emit_mask(str(client.metastores.current().metastore_id or "")), expected["MIP_EXPECT_METASTORE_ID"]
            ),
        )
    # The Go CLI resolves the same profile through its own auth path.
    cli_me: dict[str, Any] = {}
    try:
        cli = runner(
            ["databricks", "current-user", "me", "--profile", PROFILE, "-o", "json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        cli_exit, cli_stdout = cli.returncode, cli.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        cli_exit, cli_stdout = None, ""
        proof.notes["cli_authenticates"] = type(exc).__name__
    proof.checks["cli_authenticates"] = cli_exit == 0
    if cli_exit not in (0, None):
        proof.notes["cli_authenticates"] = f"exit {cli_exit}"
    try:
        parsed = json.loads(cli_stdout) if cli_exit == 0 else {}
    except ValueError:
        parsed = {}
    cli_me.update(parsed if isinstance(parsed, dict) else {})
    proof.probe("cli_me_is_the_deployer", lambda: _same_folded(emit_mask(str(cli_me.get("userName") or "")), deployer))
    proof.probe("cli_me_in_admins", lambda: _admins(group.get("display") for group in cli_me.get("groups") or ()))


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    args = list(argv or ())
    env_file = Path(args[args.index("--env-file") + 1]) if "--env-file" in args else Path(".env.local")
    environ = os.environ if environ is None else environ
    proof = Proof()
    expected = _expectations(env_file)
    profile_host = local_checks(proof, expected, environ, Path(environ.get("HOME", str(Path.home()))), Path("databricks.yml"))
    if proof.holds:
        network_checks(proof, expected, profile_host, runner)
    for line in proof.lines():
        print(line)
    if not proof.holds:
        print("::error::GitHub OIDC deployer identity proof failed; see the false checks above.")
        return 2
    print("GitHub OIDC deployer identity proven.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
