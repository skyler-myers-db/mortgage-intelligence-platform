"""``python -m tools.clients.validation_session``: nightly.yml's sessions (M1-PR8).

Each command is one step of nightly.yml. None writes to a workspace, none
authenticates as the deployer, and none accepts an identity in the
workspace ``admins`` group:

* ``check-environment --client K``: the ``check-validate-environment`` job,
  in no environment, before any job names ``<client>-validate`` (the
  validator jobs) or ``<client>`` (the browser job). GitHub creates an
  environment the first time a job references a name it does not know, with
  no protection rules, and a federation policy trusts each name (the
  validator's and the deployer's). So both must already exist with their
  review mode's protections (tools/clients/dispatch_protections.read, the
  reader deploy-dev.yml's preflight uses). Missing or drifted refuses;
  unreadable (spike S8(a) owed) refuses unless the dispatch carries, for each
  unreadable environment, the exact ``protections_unverified`` ack over the
  client, that environment and the run's sha
  (tools/clients/dispatch_acks.protections_ack), which the refusal prints.
* ``session``: a validator job (``id-token: write``). Resolve the
  ``validator`` names (tools/clients/validation_profile.py) and register every
  mask; write the ``mip-validator`` github-oidc profile (host, auth type and
  the validator's application id: no credential); then, read-only, the SDK
  authenticates through it as the validator, which must **not** be in
  ``admins``, and the App ``MIP_APP_NAME`` names its URL, SQL warehouse and
  Genie space. Each id is a runner mask the moment it is read. The session
  goes to ``$GITHUB_ENV`` (``SESSION_EXPORTS``): ``DATABRICKS_HOST``, a
  short-lived validator token as ``DATABRICKS_TOKEN`` and ``MIP_BEARER_TOKEN``,
  ``DATABRICKS_WAREHOUSE_ID``, ``GENIE_SPACE_ID``, ``MIP_APP_URL``,
  ``MIP_DEPLOYED_APP_URL``, ``MIP_APP_NAME`` and ``MIP_DEFAULT_CATALOG``; with
  ``--github-output``, the profile's per-run digest
  (validation_profile.profile_digest) goes to ``$GITHUB_OUTPUT``. It refuses,
  before any call, a catalog other than ``LIVE_SUITES_CATALOG``: the live
  suites read ``mip.`` literally, and against another catalog they would
  fail, or read another install's ``mip``.
* ``token``: remint the validator token through the same profile before a
  long step (a federated workspace token lives about an hour).
* ``operators``: the browser job (no ``id-token``). Resolve the ``browser``
  names: the public and identifying ones from the collect step's files, the
  three operator secrets from this step's own env only (``step_secrets``;
  they never touch disk, and no child but the mint inherits one). Refuse an
  ambient ``DATABRICKS_TOKEN``, and refuse unless ``<client>`` holds the
  validation profile the validator jobs read in ``<client>-validate``
  (``MIP_VALIDATE_PROFILE_DIGEST``, parity-live's output). Then mint one App
  bearer per reserved operator identity (``normal``, ``operator2``,
  ``admin``) through tools/oauth_m2m_mint.py, each in a child that receives
  only the host and that identity's id and secret. Each bearer must be its
  role's own identity and that identity must **not** be in ``admins`` (the
  App's admin role is a product role, never workspace admin membership), or
  nothing is exported. The normal operator's bearer then reads the App's
  URL. With ``--github-env`` it appends the bearers and the URL to that file;
  after ``--`` it runs the command with them instead (an ``always()``
  cleanup, which must not depend on an earlier step).
* ``app-sha`` and ``install-class``: the deployed App's own answers
  (tools/clients/validation_app.py).

Output is booleans, codes, counts and public names, never a value. Exit 0, or
2 on a refusal or a failed check.
"""

from __future__ import annotations

import argparse
import configparser
import hmac
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.clients import dispatch_acks as acks
from tools.clients import dispatch_protections as protections
from tools.clients import workflow_contexts as contexts
from tools.clients.environment import read_context
from tools.clients.errors import ProfileRefusal
from tools.clients.github_protections import VALIDATE_SUFFIX, environment_names
from tools.clients.oidc_proof import OVERRIDES, REQUEST_ENV, Proof, _admins, _same_folded
from tools.clients.redact import emit_mask
from tools.clients.validation_app import app_sha, install_class
from tools.clients.validation_profile import (
    COLLECTS,
    ValidationProfile,
    client_of,
    masks,
    profile_digest,
    resolve,
)

REPO = Path(__file__).resolve().parents[2]
PROFILE = "mip-validator"
MINT = REPO / "tools" / "oauth_m2m_mint.py"
# The App bearer names each reserved operator identity's token goes to.
BEARERS: dict[str, tuple[str, ...]] = {
    "normal": ("MIP_BEARER_TOKEN", "MIP_NON_ADMIN_BEARER_TOKEN"),
    "operator2": ("MIP_OPERATOR2_BEARER_TOKEN",),
    "admin": ("MIP_ADMIN_BEARER_TOKEN",),
}
WorkspaceFactory = Callable[..., Any]
# What ``session`` appends to $GITHUB_ENV, and the catalog the live suites read.
SESSION_EXPORTS = (
    "DATABRICKS_HOST",
    "DATABRICKS_TOKEN",
    "MIP_BEARER_TOKEN",
    "DATABRICKS_WAREHOUSE_ID",
    "GENIE_SPACE_ID",
    "MIP_APP_URL",
    "MIP_DEPLOYED_APP_URL",
    "MIP_APP_NAME",
    "MIP_DEFAULT_CATALOG",
)
LIVE_SUITES_CATALOG = "mip"
DIGEST_ENV = "MIP_VALIDATE_PROFILE_DIGEST"


class CheckFailed(Exception):
    """A read-only check failed; the message is a code, never a value."""


def _workspace(**kwargs: Any) -> Any:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(**kwargs)


def _append_env(path: Path, values: Mapping[str, str]) -> None:
    if any("\n" in value or "\r" in value for value in values.values()):
        raise CheckFailed("a session value holds a line break")
    with path.open("a", encoding="utf-8") as handle:
        handle.writelines(f"{name}={value}\n" for name, value in values.items())


def step_secrets(environ: Mapping[str, str]) -> dict[str, str]:
    """The operator secrets this step's own env binds (``collect_env("operators")``), never a file.

    A name found in ``vars`` (by presence) refuses, as the collect step does.
    """

    misfiled = sorted(set(environ.get(contexts.MISFILED_ENV, "").split()))
    if misfiled:
        raise ProfileRefusal([f"{name}: is secret; store it as an environment secret, never a variable" for name in misfiled])
    return {
        name: value
        for name in COLLECTS["operators"]
        if (value := environ.get(f"{contexts.SECRET_PREFIX}{name}", "")).strip()
    }


def _child_environ(environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` without any bound profile name or the digest, so no child inherits an operator secret."""

    bound = (contexts.VAR_PREFIX, contexts.SECRET_PREFIX, contexts.MISFILED_ENV, DIGEST_ENV)
    return {name: value for name, value in environ.items() if not name.startswith(bound)}


def _load(args: argparse.Namespace, kind: str, extra_secrets: Mapping[str, str] | None = None) -> ValidationProfile:
    secrets = {**read_context(args.secrets_json), **(extra_secrets or {})}
    profile = resolve(read_context(args.vars_json), secrets, environment=args.environment, kind=kind)
    for value in masks(profile):
        emit_mask(value)
    return profile


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CheckFailed(f"{path.name} already exists; a validator job writes it once") from None
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def databrickscfg_text(profile: ValidationProfile) -> str:
    """The validator's github-oidc profile: host, auth type and client id, nothing else."""

    return f"[{PROFILE}]\nhost = {profile.host}\nauth_type = github-oidc\nclient_id = {profile.validator}\n"


def _bearer(headers: object) -> str:
    auth = headers.get("Authorization", "") if isinstance(headers, dict) else ""
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise CheckFailed("no bearer was minted")
    return emit_mask(token)


def validator_client(cfg: Path, validator: str, proof: Proof, factory: WorkspaceFactory) -> Any:
    """Authenticate through the profile as the validator, never an admin; return the client."""

    state: dict[str, Any] = {}

    def authenticate() -> bool:
        state["client"] = client = factory(profile=PROFILE, config_file=str(cfg))
        state["me"] = client.current_user.me()
        return bool(client.config.auth_type == "github-oidc" and _same_folded(client.config.client_id, validator))

    proof.probe("sdk_authenticates_with_the_github_oidc_validator_profile", authenticate)
    if "me" not in state:
        return None
    me = state["me"]
    proof.probe("me_is_the_validator", lambda: _same_folded(emit_mask(str(me.user_name or "")), validator))
    proof.probe("me_not_in_admins", lambda: not _admins(group.display for group in me.groups or ()))
    return state["client"] if proof.holds else None


def app_resources(client: Any, app_name: str) -> dict[str, str]:
    """The App's URL, SQL warehouse and Genie space, each a mask once read."""

    app = client.apps.get(app_name)
    url = str(getattr(app, "url", "") or "").strip().rstrip("/")
    warehouses = [r.sql_warehouse.id for r in app.resources or () if getattr(r, "sql_warehouse", None)]
    spaces = [r.genie_space.space_id for r in app.resources or () if getattr(r, "genie_space", None)]
    if not url.startswith("https://") or len(warehouses) != 1 or len(spaces) != 1:
        raise CheckFailed("the App must name one https URL, one SQL warehouse and one Genie space")
    return {
        "MIP_APP_URL": emit_mask(url),
        "MIP_DEPLOYED_APP_URL": url,
        "DATABRICKS_WAREHOUSE_ID": emit_mask(str(warehouses[0])),
        "GENIE_SPACE_ID": emit_mask(str(spaces[0])),
    }


def _local_checks(proof: Proof, environ: Mapping[str, str]) -> None:
    proof.checks["github_oidc_request_env_present"] = all(environ.get(name) for name in REQUEST_ENV)
    proof.checks["no_ambient_auth_override"] = not any(environ.get(name) for name in OVERRIDES)


def _finish(proof: Proof, failure: str) -> int:
    for line in proof.lines():
        print(line)
    if not proof.holds:
        print(f"::error::{failure}")
        return 2
    return 0


def session(args: argparse.Namespace, environ: Mapping[str, str], factory: WorkspaceFactory) -> int:
    profile = _load(args, "validator")
    proof = Proof()
    _local_checks(proof, environ)
    proof.checks["catalog_is_the_one_the_live_suites_read"] = profile.catalog == LIVE_SUITES_CATALOG
    cfg = Path(args.databrickscfg)
    if proof.holds:
        _write_new(cfg, databrickscfg_text(profile))
        client = validator_client(cfg, profile.validator, proof, factory)
        if client is not None:
            found: dict[str, str] = {}

            def read_resources() -> bool:
                found.update(app_resources(client, profile.app_name))
                return True

            def mint() -> bool:
                found["DATABRICKS_TOKEN"] = found["MIP_BEARER_TOKEN"] = _bearer(client.config.authenticate())
                return True

            proof.probe("app_names_its_url_warehouse_and_genie_space", read_resources)
            proof.probe("validator_token_minted", mint)
            if proof.holds:
                _append_env(
                    Path(args.github_env),
                    {
                        "DATABRICKS_HOST": profile.host,
                        **found,
                        "MIP_APP_NAME": profile.app_name,
                        "MIP_DEFAULT_CATALOG": profile.catalog,
                    },
                )
                if getattr(args, "github_output", None):
                    digest = profile_digest(profile, environ.get("GITHUB_RUN_ID", ""))
                    _append_env(Path(args.github_output), {"profile_digest": digest})
    code = _finish(proof, "the validator session failed; see the false checks above (never the deployer, never an admin).")
    if code == 0:
        print(f"validator session ready: client={profile.client} environment={profile.environment} app={profile.app_name}")
    return code


def remint(args: argparse.Namespace, environ: Mapping[str, str], factory: WorkspaceFactory) -> int:
    """A fresh validator token through the profile ``session`` wrote, the same three checks first."""

    cfg = Path(args.databrickscfg)
    validator = _profile_client_id(cfg)
    proof = Proof()
    proof.checks["validator_profile_present"] = bool(validator)
    proof.checks["github_oidc_request_env_present"] = all(environ.get(name) for name in REQUEST_ENV)
    client = validator_client(cfg, validator, proof, factory) if proof.holds else None
    if client is not None:
        token: dict[str, str] = {}

        def mint() -> bool:
            token["DATABRICKS_TOKEN"] = token["MIP_BEARER_TOKEN"] = _bearer(client.config.authenticate())
            return True

        proof.probe("validator_token_minted", mint)
        if proof.holds:
            _append_env(Path(args.github_env), token)
    return _finish(proof, "the validator token could not be reminted; see the false checks above.")


def _profile_client_id(cfg: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(cfg, encoding="utf-8")
    return parser.get(PROFILE, "client_id", fallback="").strip() if parser.has_section(PROFILE) else ""


def mint_bearer(host: str, client_id: str, secret: str, runner: Callable[..., Any] = subprocess.run) -> str:
    """One App bearer from tools/oauth_m2m_mint.py, in a child holding only this identity."""

    with tempfile.TemporaryDirectory(prefix="mip-validate-") as scratch:
        out = Path(scratch) / "bearer"
        child_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GITHUB_ACTIONS": os.environ.get("GITHUB_ACTIONS", ""),
            "DATABRICKS_HOST": host,
            "DATABRICKS_CLIENT_ID": client_id,
            "DATABRICKS_CLIENT_SECRET": secret,
        }
        result = runner([sys.executable, str(MINT), "--output-file", str(out)], env=child_env, cwd=REPO, check=False)
        if result.returncode != 0 or not out.is_file():
            raise CheckFailed(f"the M2M mint exited {result.returncode}")
        return emit_mask(out.read_text(encoding="utf-8").strip())


def operator_is_not_an_admin(client: Any, role: str, application_id: str, proof: Proof) -> None:
    """The bearer is ``role``'s own identity, and that identity is no workspace admin.

    The App's ``admin`` role is a product role (a workspace USER); none of the
    three reserved operator identities may hold Databricks admin power.
    """

    state: dict[str, Any] = {}

    def me() -> bool:
        state["me"] = found = client.current_user.me()
        return _same_folded(emit_mask(str(found.user_name or "")), application_id)

    proof.probe(f"{role}_bearer_is_its_identity", me)
    if "me" in state:
        groups = state["me"].groups or ()
        proof.probe(f"{role}_not_in_admins", lambda: not _admins(group.display for group in groups))


def operators(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    factory: WorkspaceFactory,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    profile = _load(args, "browser", step_secrets(environ))
    if environ.get("DATABRICKS_TOKEN"):
        print("::error::the browser job refuses an ambient DATABRICKS_TOKEN: it holds no workspace token, only App bearers")
        return 2
    expected = environ.get(DIGEST_ENV, "").strip()
    if not expected or not hmac.compare_digest(expected, profile_digest(profile, environ.get("GITHUB_RUN_ID", ""))):
        print(
            f"::error::{profile.client} and {profile.client}-validate do not hold the same validation profile "
            "(client, host, App, catalog, identity map); nothing was minted"
        )
        return 2
    bearers: dict[str, str] = {}
    proof = Proof()
    for role, names in BEARERS.items():
        application_id, secret = profile.operator(role)
        token = mint_bearer(profile.host, application_id, secret, runner=runner)
        bearers.update(dict.fromkeys(names, token))
        operator_is_not_an_admin(factory(host=profile.host, token=token, auth_type="pat"), role, application_id, proof)
    if not proof.holds:
        return _finish(proof, "a reserved operator bearer is not its role's identity or is a workspace admin; nothing was exported.")
    url = str(factory(host=profile.host, token=bearers["MIP_BEARER_TOKEN"], auth_type="pat").apps.get(profile.app_name).url or "")
    url = emit_mask(url.strip().rstrip("/"))
    if not url.startswith("https://"):
        print("::error::the App has no https URL the normal operator can read")
        return 2
    session_env = {**bearers, "MIP_APP_URL": url, "MIP_API_URL": url}
    print(f"minted {len(BEARERS)} per-run App bearers (normal, operator2, admin) for client={profile.client}")
    if args.command_argv:
        child = {**_child_environ(environ), **session_env}
        return int(runner(list(args.command_argv), env=child, check=False).returncode)
    if not args.github_env:
        print("::error::operators needs --github-env or a command after --")
        return 2
    _append_env(Path(args.github_env), session_env)
    return 0


def _protections(session_: Any, environment: str, environ: Mapping[str, str]) -> Any:
    if session_ is None:
        return protections.Result(protections.UNREADABLE, cause="no GitHub session")
    return protections.read(session_, environment, protections.SINGLE_MAINTAINER, environ.get("GITHUB_ACTOR_ID", ""))


def check_environment(args: argparse.Namespace, environ: Mapping[str, str], opener: Any = None) -> int:
    """Both environments nightly names exist with their protections, or each unreadable one is acked."""

    try:
        client = client_of(f"{args.client}{VALIDATE_SUFFIX}")
    except ProfileRefusal as refusal:
        print(f"::error::{refusal.lines[0]}")
        return 2
    try:
        session_ = protections.from_environ(environ, **({"opener": opener} if opener else {}))
    except protections.Unreadable:
        session_ = None
    given = set(re.split(r"[\s,]+", environ.get("MIP_DISPATCH_PROTECTIONS_ACK", "").strip())) - {""}
    code = 0
    for environment in environment_names(client):
        result = _protections(session_, environment, environ)
        print(f"{environment}: {result.code}" + (f" ({result.cause})" if result.cause else ""))
        if result.code == protections.VERIFIED:
            continue
        if result.code == protections.DRIFT:
            for line in result.lines:
                print(f"  - {line}")
            print(f"::error::{environment} must exist with its review mode's protections before any job names it; {_owner_hint()}")
            code = 2
            continue
        expected = acks.protections_ack(client, environment, environ.get("GITHUB_SHA", ""))
        if any(acks.same_ack(ack, expected) for ack in given):
            print(f"{environment}: protections unverified, acknowledged for this run's sha")
            continue
        print(
            f"::error::{environment}: GITHUB_TOKEN cannot read its protections; once you have checked them, "
            f"re-dispatch with protections_unverified_ack holding {expected} (one ack per unreadable environment)"
        )
        code = 2
    return code


def _owner_hint() -> str:
    return "the owner runs python -m tools.clients.github_environment --apply (docs/deployment.md, 'Live validation per client')"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.clients.validation_session", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-environment")
    check.add_argument("--client", required=True)
    for name in ("session", "operators"):
        command = sub.add_parser(name)
        command.add_argument("--vars-json", required=True)
        command.add_argument("--secrets-json", required=True)
        command.add_argument("--environment", required=True)
        command.add_argument("--github-env")
        if name == "session":
            command.add_argument("--databrickscfg", required=True)
            command.add_argument("--github-output")
        else:
            command.add_argument("command_argv", nargs=argparse.REMAINDER)
    token = sub.add_parser("token")
    token.add_argument("--databrickscfg", required=True)
    token.add_argument("--github-env", required=True)
    for name in ("app-sha", "install-class"):
        app = sub.add_parser(name)
        app.add_argument("--token-env", required=True)
        app.add_argument(*(("--git-sha",) if name == "app-sha" else ("--github-output",)), required=True)
        app.add_argument("--attempts", type=int, default=10)
        app.add_argument("--interval", type=float, default=15.0)
    return parser


def main(argv: Sequence[str] | None = None, *, factory: WorkspaceFactory = _workspace) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "command_argv", None) and args.command_argv[0] == "--":
        args.command_argv = args.command_argv[1:]
    environ = dict(os.environ)
    try:
        if args.command == "check-environment":
            return check_environment(args, environ)
        if args.command == "session":
            if not args.github_env:
                print("::error::session needs --github-env")
                return 2
            return session(args, environ, factory)
        if args.command == "token":
            return remint(args, environ, factory)
        if args.command == "operators":
            return operators(args, environ, factory)
        if args.command == "install-class":
            return install_class(args, environ)
        return app_sha(args, environ)
    except ProfileRefusal as refusal:
        print("validation profile refused:", file=sys.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=sys.stderr)
        return 2
    except CheckFailed as failed:
        print(f"::error::{failed}")
        return 2
    except Exception as exc:  # the class only: a message could carry a value
        print(f"::error::validation session failed: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
