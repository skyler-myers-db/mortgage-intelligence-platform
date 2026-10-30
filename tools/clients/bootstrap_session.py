"""Human presence and a fresh U2M login for the account bootstrap (plan §4.10).

**Human presence.** ``--apply``, ``--rotate-adopted`` and
``--post-first-install`` refuse when ``GITHUB_ACTIONS`` or ``CI`` is set, or
when stdin is not a terminal. The owner hardware-key ``mint`` attestation the
plan names is deferred; its stand-in is the exact plan-digest ack
(tools/clients/bootstrap_account.py).

**A fresh login, never a cached one.** Every run makes a new private
temporary directory and logs in there and only there:

* ``DATABRICKS_CONFIG_FILE`` names a config file written in it, with one
  ``account`` and one ``workspace`` profile, both ``external-browser`` (an
  interactive U2M login);
* ``HOME`` points into it, so the SDK's U2M token cache
  (``~/.config/databricks-sdk-py/oauth``) starts empty and dies with the run;
* every environment name the SDK reads for auth (``DATABRICKS_*``, the Azure
  and Google credentials, the Actions ID-token request) is removed for the
  session and restored after.

The SDK config is built from that file alone, so the operator's own
``~/.databrickscfg`` is never read, whatever profiles it holds. A session
directory that already holds a token cache, or anything else, is refused.

The ``databricks`` loggers are held at WARNING for the session: the SDK's
DEBUG round-trip log does not redact a ``secret`` field, and a minted
credential comes back in one.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from tools.clients.bootstrap_inputs import BootstrapInputs
from tools.clients.errors import BootstrapRefusal

CI_MARKERS = ("GITHUB_ACTIONS", "CI")
ACCOUNT_PROFILE = "account"
WORKSPACE_PROFILE = "workspace"
TOKEN_CACHE_PARTS = (".config", "databricks-sdk-py", "oauth")
CLI_TOKEN_CACHE_PARTS = (".databricks", "token-cache.json")
# Auth inputs the SDK reads outside the DATABRICKS_ prefix.
_EXTRA_AUTH_ENV = frozenset({"ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"})
_QUIET_LEVEL = logging.WARNING


def presence_refusals(environ: Mapping[str, str], stdin: TextIO) -> list[str]:
    lines = [
        f"{name}: is set; this bootstrap is human-run only and never runs in CI"
        for name in CI_MARKERS
        if environ.get(name, "").strip()
    ]
    try:
        interactive = stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        lines.append("stdin: is not a terminal; run this bootstrap interactively")
    return lines


def assert_human_present(environ: Mapping[str, str], stdin: TextIO) -> None:
    lines = presence_refusals(environ, stdin)
    if lines:
        raise BootstrapRefusal(lines)


@dataclass(frozen=True)
class Clients:
    """The account and workspace clients one run reads and mutates through."""

    account: Any
    workspace: Any


@dataclass(frozen=True)
class SessionPaths:
    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "databrickscfg"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def token_cache(self) -> Path:
        return self.home.joinpath(*TOKEN_CACHE_PARTS)

    @property
    def cli_token_cache(self) -> Path:
        return self.home.joinpath(*CLI_TOKEN_CACHE_PARTS)


SessionFactory = Callable[[BootstrapInputs], AbstractContextManager[Clients]]
ClientFactory = Callable[[SessionPaths, BootstrapInputs], Clients]


def config_text(inputs: BootstrapInputs) -> str:
    """The session's config file: two interactive U2M profiles and nothing else."""

    return (
        f"[{ACCOUNT_PROFILE}]\n"
        f"host = {inputs.resolved_account_host()}\n"
        f"account_id = {inputs.account_id}\n"
        "auth_type = external-browser\n"
        "\n"
        f"[{WORKSPACE_PROFILE}]\n"
        f"host = {inputs.workspace_host}\n"
        "auth_type = external-browser\n"
    )


def sdk_auth_env_names() -> frozenset[str]:
    """Every environment name the SDK config reads, plus the prefix-free extras."""

    from databricks.sdk.config import Config

    names = {attribute.env for attribute in Config.attributes() if attribute.env}
    return frozenset(names | _EXTRA_AUTH_ENV)


def _scrubbed(environ: Mapping[str, str], paths: SessionPaths) -> dict[str, str]:
    drop = sdk_auth_env_names()
    kept = {
        name: value
        for name, value in environ.items()
        if name not in drop and not name.startswith("DATABRICKS_")
    }
    kept["HOME"] = str(paths.home)
    kept["DATABRICKS_CONFIG_FILE"] = str(paths.config_file)
    return kept


@contextmanager
def _session_environment(paths: SessionPaths) -> Iterator[None]:
    saved = dict(os.environ)
    loggers = [logging.getLogger("databricks")] + [
        logging.getLogger(name) for name in list(logging.root.manager.loggerDict) if name.startswith("databricks.")
    ]
    levels = [(logger, logger.level) for logger in loggers]
    try:
        os.environ.clear()
        os.environ.update(_scrubbed(saved, paths))
        for logger, level in levels:
            if level == logging.NOTSET or level < _QUIET_LEVEL:
                logger.setLevel(_QUIET_LEVEL)
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
        for logger, level in levels:
            logger.setLevel(level)


def sdk_clients(paths: SessionPaths, inputs: BootstrapInputs) -> Clients:
    """SDK clients from the session config file only (each opens a browser login)."""

    from databricks.sdk import AccountClient, WorkspaceClient
    from databricks.sdk.config import Config

    account = AccountClient(config=Config(profile=ACCOUNT_PROFILE, config_file=str(paths.config_file)))
    workspace = WorkspaceClient(config=Config(profile=WORKSPACE_PROFILE, config_file=str(paths.config_file)))
    return Clients(account=account, workspace=workspace)


def _new_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="mip-bootstrap-"))


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def freshness_refusals(paths: SessionPaths) -> list[str]:
    if paths.token_cache.exists() and any(paths.token_cache.iterdir()):
        return ["session: a cached U2M token is already present; every run needs a fresh login"]
    if paths.cli_token_cache.exists():
        return ["session: a cached CLI token is already present; every run needs a fresh login"]
    if any(paths.root.iterdir()):
        return ["session: the temporary session directory is not empty; refusing to reuse it"]
    return []


@contextmanager
def fresh_session(
    inputs: BootstrapInputs,
    *,
    make_root: Callable[[], Path] = _new_root,
    client_factory: ClientFactory = sdk_clients,
) -> Iterator[Clients]:
    """Yield clients logged in fresh, inside a private, empty, throwaway directory."""

    paths = SessionPaths(make_root())
    os.chmod(paths.root, 0o700)
    lines = freshness_refusals(paths)
    if lines:
        raise BootstrapRefusal(lines)
    try:
        paths.home.mkdir(mode=0o700)
        _write_private(paths.config_file, config_text(inputs))
        with _session_environment(paths):
            yield client_factory(paths, inputs)
    finally:
        shutil.rmtree(paths.root, ignore_errors=True)
