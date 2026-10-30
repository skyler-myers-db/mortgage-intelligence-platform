"""Human presence, the fresh session, and the bootstrap's closed call surface.

* The mutating actions refuse under CI or without a terminal, before any
  input is read or any session opens; ``--plan`` and ``--verify`` do not.
* ``fresh_session`` logs in inside a new private directory: its own config
  file, an empty token cache, no inherited auth environment, quiet SDK
  loggers. A cached token there refuses; the operator's ``~/.databrickscfg``
  is never read, whatever it holds.
* An AST allowlist pins every SDK call the ``tools/clients`` modules can
  make (the bootstrap and the credential sink M2-PR2 extends). The only
  delete in it is a service-principal secret's; nothing transfers an owner,
  revokes a grant, removes a member or touches an identity's roles. A call
  the allowlist could not see refuses outright: the raw ``api_client``,
  reflection (``getattr`` and kin) on a session client or naming an SDK
  API, ``attrgetter``/``methodcaller``, and the ``ApiClient`` class. The
  fake account's spy enforces the same at run time on every test path.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tests.fixtures.bootstrap_harness import TTY, FakeSink, plan_and_apply, run_tool, write_env
from tests.fixtures.fake_databricks_account import WORKSPACE_HOST, FakeEstate, assert_safe
from tools.clients import bootstrap_session as session_module
from tools.clients.bootstrap_inputs import load_inputs
from tools.clients.bootstrap_session import (
    ACCOUNT_PROFILE,
    WORKSPACE_PROFILE,
    Clients,
    SessionPaths,
    fresh_session,
)
from tools.clients.credential_sink import account_token_probe
from tools.clients.errors import BootstrapRefusal

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_MODULES = sorted((ROOT / "tools" / "clients").glob("*.py"))
ALLOWED_CALLS = frozenset(
    {
        ("access_control", "get_rule_set"),
        ("access_control", "update_rule_set"),
        ("apps", "get"),
        ("catalogs", "get"),
        # Read-only: tools/clients/live_checks.py proves the deploy runs as the
        # profile's deployer (M1-PR2).
        ("current_user", "me"),
        ("grants", "get"),
        ("grants", "update"),
        ("groups", "create"),
        ("groups", "get"),
        ("groups", "list"),
        ("groups", "patch"),
        ("metastores", "current"),
        ("schemas", "get"),
        ("service_principal_federation_policy", "create"),
        ("service_principal_federation_policy", "list"),
        ("service_principal_federation_policy", "update"),
        ("service_principal_secrets", "create"),
        ("service_principal_secrets", "delete"),
        ("service_principal_secrets", "list"),
        ("service_principals", "create"),
        ("service_principals", "get"),
        ("service_principals", "list"),
        ("token_management", "list"),
        ("users", "list"),
        ("workspace_assignment", "list"),
        ("workspace_assignment", "update"),
        # The client method; the SDK's Workspace (notebook) API shares the name
        # and has no such method, so any other ``workspace`` call still fails.
        ("workspace", "get_workspace_id"),
    }
)
FORBIDDEN_KEYWORDS = frozenset({"owner", "new_owner", "remove", "roles", "entitlements"})
DESTRUCTIVE_METHODS = frozenset({"delete", "unassign", "transfer_ownership", "remove", "reset"})
# Names that reach an API without a visible ``<client>.<api>.<method>(...)``.
DYNAMIC_NAMES = frozenset({"api_client", "ApiClient", "attrgetter", "methodcaller", "__getattribute__", "__dict__"})
REFLECTION = frozenset({"getattr", "setattr", "delattr", "vars"})
# A reflective lookup of one of these reaches a session client, wherever it starts.
CLIENT_ENTRY_NAMES = frozenset({"account", "workspace", "api_client", "do"})


@pytest.fixture
def estate() -> Iterator[FakeEstate]:
    found = FakeEstate()
    yield found
    assert_safe(found)


@pytest.fixture
def env(tmp_path: Path) -> Path:
    return write_env(tmp_path / "acme.env")


def _never_opened(inputs: Any) -> Any:
    raise AssertionError("a refused run must not open a session")


# --- human presence ------------------------------------------------------------------


@pytest.mark.parametrize("action", ["--apply", "--rotate-adopted", "--post-first-install"])
@pytest.mark.parametrize(
    ("environ", "stdin", "expected"),
    [
        ({"GITHUB_ACTIONS": "true"}, TTY(), "GITHUB_ACTIONS: is set"),
        ({"CI": "1"}, TTY(), "CI: is set"),
        ({}, io.StringIO(), "stdin: is not a terminal"),
    ],
)
def test_mutating_actions_refuse_without_a_human(
    estate: FakeEstate, env: Path, action: str, environ: dict[str, str], stdin: Any, expected: str
) -> None:
    result = run_tool(
        estate, action, "--ack-plan", "a" * 64, env_file=env, environ=environ, stdin=stdin, session=_never_opened
    )
    assert result.code == 2 and expected in result.err
    assert estate.calls == []


def test_plan_and_verify_run_without_a_terminal_and_under_ci(estate: FakeEstate, env: Path) -> None:
    for action in ("--plan", "--verify"):
        result = run_tool(estate, action, env_file=env, environ={"CI": "true"}, stdin=io.StringIO())
        assert result.code in {0, 1}, result.err
    assert estate.mutations() == []


# --- the fresh session --------------------------------------------------------------------


def _inputs(env: Path) -> Any:
    inputs, _aliases = load_inputs({}, env)
    return inputs


@contextmanager
def _canary_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "operator-home"
    home.mkdir()
    (home / ".databrickscfg").write_text(
        "[DEFAULT]\nhost = https://canary-home.cloud.databricks.com\ntoken = canary-home-token\n"
        "[account]\nhost = https://canary-account.cloud.databricks.com\naccount_id = canary\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DATABRICKS_TOKEN", "canary-ambient-token")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "account")
    monkeypatch.setenv("ARM_CLIENT_SECRET", "canary-azure-secret")
    monkeypatch.setenv("UNRELATED_SETTING", "kept")
    yield home


def test_the_session_logs_in_inside_a_private_throwaway_directory(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def factory(paths: SessionPaths, inputs: Any) -> Clients:
        seen["paths"] = paths
        seen["environ"] = dict(os.environ)
        seen["mode"] = stat.S_IMODE(paths.root.stat().st_mode)
        seen["config_mode"] = stat.S_IMODE(paths.config_file.stat().st_mode)
        seen["config"] = paths.config_file.read_text(encoding="utf-8")
        seen["level"] = logging.getLogger("databricks.sdk").getEffectiveLevel()
        return Clients(account=object(), workspace=object())

    with _canary_home(tmp_path, monkeypatch) as home:
        logging.getLogger("databricks.sdk").setLevel(logging.DEBUG)
        before = dict(os.environ)
        with fresh_session(_inputs(env), client_factory=factory):
            pass
        assert dict(os.environ) == before
        assert logging.getLogger("databricks.sdk").level == logging.DEBUG
        logging.getLogger("databricks.sdk").setLevel(logging.NOTSET)
    paths: SessionPaths = seen["paths"]
    assert not paths.root.exists()
    assert seen["mode"] == 0o700 and seen["config_mode"] == 0o600
    environ = seen["environ"]
    assert environ["HOME"] == str(paths.home) and environ["HOME"] != str(home)
    assert environ["DATABRICKS_CONFIG_FILE"] == str(paths.config_file)
    assert environ["UNRELATED_SETTING"] == "kept"
    for name in ("DATABRICKS_TOKEN", "DATABRICKS_CONFIG_PROFILE", "ARM_CLIENT_SECRET"):
        assert name not in environ
    assert seen["level"] >= logging.WARNING
    assert f"[{ACCOUNT_PROFILE}]" in seen["config"] and f"[{WORKSPACE_PROFILE}]" in seen["config"]
    assert seen["config"].count("auth_type = external-browser") == 2
    assert "canary" not in seen["config"] and "token" not in seen["config"]


def test_the_sdk_config_reads_the_session_file_and_never_the_operators(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import databricks.sdk.config as sdk_config
    from databricks.sdk.config import Config
    from databricks.sdk.credentials_provider import CredentialsStrategy

    class Offline(CredentialsStrategy):
        def auth_type(self) -> str:
            return "offline"

        def __call__(self, cfg: Any) -> Any:
            return lambda: {}

    def no_metadata(host: str) -> Any:
        raise ValueError("offline")

    monkeypatch.setattr(sdk_config, "get_host_metadata", no_metadata)
    loaded: dict[str, Config] = {}

    def factory(paths: SessionPaths, inputs: Any) -> Clients:
        for profile in (ACCOUNT_PROFILE, WORKSPACE_PROFILE):
            loaded[profile] = Config(profile=profile, config_file=str(paths.config_file), credentials_strategy=Offline())
        return Clients(account=object(), workspace=object())

    with _canary_home(tmp_path, monkeypatch), fresh_session(_inputs(env), client_factory=factory):
        pass
    assert loaded[ACCOUNT_PROFILE].host == "https://accounts.cloud.databricks.com"
    assert loaded[WORKSPACE_PROFILE].host == WORKSPACE_HOST
    for config in loaded.values():
        assert config.token is None and "canary" not in str(config.account_id)


def test_a_cached_token_in_the_session_directory_refuses(env: Path, tmp_path: Path) -> None:
    root = tmp_path / "stale-session"
    cache = SessionPaths(root).token_cache
    cache.mkdir(parents=True)
    (cache / "cached.json").write_text(json.dumps({"access_token": "stale"}), encoding="utf-8")
    with pytest.raises(BootstrapRefusal) as refused, fresh_session(
        _inputs(env), make_root=lambda: root, client_factory=lambda paths, inputs: Clients(object(), object())
    ):
        pass
    assert refused.value.lines == ("session: a cached U2M token is already present; every run needs a fresh login",)
    assert (cache / "cached.json").exists()


def test_the_auth_scrub_covers_every_sdk_environment_name() -> None:
    from databricks.sdk.config import Config

    names = session_module.sdk_auth_env_names()
    assert {"DATABRICKS_TOKEN", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_CONFIG_PROFILE", "ARM_CLIENT_SECRET"} <= names
    assert {a.env for a in Config.attributes() if a.env} <= names


# --- the closed call surface ---------------------------------------------------------------


def _sdk_api_names() -> frozenset[str]:
    from databricks.sdk import AccountClient, WorkspaceClient

    names = {n for client in (AccountClient, WorkspaceClient) for n in dir(client) if not n.startswith("_")}
    return frozenset(names - {"config", "api_client", "get_workspace_id", "get_workspace_client"})


def _is_client(node: ast.expr) -> bool:
    """``account``/``workspace`` or ``<x>.account``/``<x>.workspace``: a session client."""

    if isinstance(node, ast.Name):
        return node.id in {"account", "workspace"}
    return isinstance(node, ast.Attribute) and node.attr in {"account", "workspace"}


def _calls_in(source: str) -> list[tuple[ast.Call, tuple[str, str]]]:
    """Every ``<...>.<sdk api>.<method>(...)`` call in ``source``."""

    apis = _sdk_api_names()
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and owner.attr in apis:
                found.append((node, (owner.attr, node.func.attr)))
    return found


def _client_rooted(node: ast.expr) -> bool:
    """A session client, or reached from one through attributes and subscripts."""

    while isinstance(node, ast.Attribute | ast.Subscript):
        if isinstance(node, ast.Attribute) and node.attr in {"account", "workspace"}:
            return True
        node = node.value
    return isinstance(node, ast.Name) and node.id in {"account", "workspace", "clients"}


def _dynamic_in(source: str) -> list[str]:
    """Calls the allowlist cannot see: the raw API client, reflection, dynamic dispatch.

    Reflection is refused on a session client (any attribute) and anywhere
    for a client entry name; on a record, like ``getattr(item, "permissions")``,
    it reaches no API and stays allowed.
    """

    found = []
    for node in ast.walk(ast.parse(source)):
        name = (
            node.attr
            if isinstance(node, ast.Attribute)
            else node.id
            if isinstance(node, ast.Name)
            else node.name.rsplit(".", 1)[-1]
            if isinstance(node, ast.alias)
            else None
        )
        if name in DYNAMIC_NAMES:
            found.append(f"line {getattr(node, 'lineno', 0)}: {name}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in REFLECTION and node.args:
            named = node.args[1] if len(node.args) > 1 else None
            if _client_rooted(node.args[0]) or (isinstance(named, ast.Constant) and named.value in CLIENT_ENTRY_NAMES):
                found.append(f"line {node.lineno}: {node.func.id}()")
    return found


def _aliases_in(source: str) -> list[int]:
    """Lines binding ``<client>.<api>`` to a name, which would hide its calls."""

    apis = _sdk_api_names()
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr in apis
        and _is_client(node.value.value)
    ]


def _forbidden_in(source: str) -> list[str]:
    """Owner changes, revokes, role or entitlement writes, SCIM remove/replace."""

    found = []
    for node, pair in _calls_in(source):
        names = {keyword.arg for keyword in node.keywords} & FORBIDDEN_KEYWORDS
        found += [f"{pair} sets {name}" for name in sorted(n for n in names if n)]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.keyword) and node.arg == "remove":
            found.append(f"line {node.value.lineno}: remove=")
        if isinstance(node, ast.Attribute) and node.attr in {"REMOVE", "REPLACE"}:
            found.append(f"line {node.lineno}: {node.attr}")
    return found


def _sources() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in BOOTSTRAP_MODULES}


def test_the_checks_catch_a_planted_alias_transfer_and_revoke() -> None:
    planted = (
        "api = self.account.service_principal_secrets\n"
        "self.workspace.metastores.update('m', owner='x')\n"
        "self.workspace.grants.update('catalog', 'c', changes=[PermissionsChange(remove=[p])])\n"
        "self.account.service_principals.patch('1', operations=[Patch(op=PatchOp.REPLACE, path='roles')])\n"
        "self.workspace.api_client.do('PATCH', '/api/2.1/unity-catalog/metastores/m', body={'owner': 'x'})\n"
        "self.account.api_client.do('DELETE', '/api/2.0/accounts/a/servicePrincipals/1')\n"
        "getattr(self.workspace, 'metastores').update('m', owner='x')\n"
        "getattr(getattr(self, 'account'), 'service_principals').delete('1')\n"
        "from operator import methodcaller\n"
        "setattr(clients.account, 'groups', None)\n"
    )
    assert _aliases_in(planted) == [1]
    assert {pair for _node, pair in _calls_in(planted)} == {
        ("metastores", "update"),
        ("grants", "update"),
        ("service_principals", "patch"),
    }
    assert set(_forbidden_in(planted)) >= {"('metastores', 'update') sets owner", "line 3: remove=", "line 4: REPLACE"}
    assert set(_dynamic_in(planted)) == {
        "line 5: api_client",
        "line 6: api_client",
        "line 7: getattr()",
        "line 8: getattr()",
        "line 9: methodcaller",
        "line 10: setattr()",
    }
    # Reflection on a record stays allowed, even for a field named like an API.
    assert _dynamic_in(
        "getattr(record, attribute, None)\ngetattr(item, 'permissions', None)\n"
        "getattr(self.workspace.metastores.current(), 'id')\n"
    ) == []


def test_the_bootstrap_calls_only_the_reviewed_sdk_surface() -> None:
    sources = _sources()
    assert {"credential_sink.py", "bootstrap_execute.py", "bootstrap_observe.py", "bootstrap_session.py"} <= set(sources)
    for name, source in sources.items():
        assert _aliases_in(source) == [], name
        assert _dynamic_in(source) == [], name
    used = {pair for source in sources.values() for _node, pair in _calls_in(source)}
    assert used == ALLOWED_CALLS
    destructive = {pair for pair in ALLOWED_CALLS if pair[1] in DESTRUCTIVE_METHODS}
    assert destructive == {("service_principal_secrets", "delete")}
    assert [pair for pair in ALLOWED_CALLS if pair[0] == "metastores"] == [("metastores", "current")]
    assert not [pair for pair in ALLOWED_CALLS if pair[0] in {"catalogs", "schemas", "tables"} and pair[1] != "get"]
    assert not [pair for pair in ALLOWED_CALLS if pair[0] == "service_principals" and pair[1] in {"patch", "update"}]


def test_no_bootstrap_call_changes_an_owner_revokes_or_sets_roles() -> None:
    for name, source in _sources().items():
        assert _forbidden_in(source) == [], name
    text = "\n".join(_sources().values())
    assert "roles/servicePrincipal.user" in text and "roles/servicePrincipal.manager" in text
    assert "roles/account" not in text


def test_a_full_lifecycle_calls_nothing_outside_the_surface(estate: FakeEstate, env: Path, tmp_path: Path) -> None:
    sink = FakeSink()
    estate.add_sp("other-team-automation", protected=True)
    _planned, applied = plan_and_apply(estate, env, sink)
    assert applied.code == 0
    pinned = write_env(tmp_path / "pinned.env", pins=sink.pins)
    for action in ("rotate-adopted", "post-first-install"):
        if action == "post-first-install":
            app = estate.add_sp("app-acme-app")
            estate.apps["acme-app"] = app.application_id
        planned = run_tool(estate, "--plan", "--for", action, env_file=pinned, sink=sink)
        result = run_tool(estate, f"--{action}", "--ack-plan", planned.digest, env_file=pinned, sink=sink)
        assert result.code == 0, result.err
    assert run_tool(estate, "--verify", env_file=pinned, sink=sink).code == 0
    called = {(c.api, c.method) for c in estate.calls} - {("workspace", "get_workspace_id")}
    assert called <= ALLOWED_CALLS
    assert {c.api for c in estate.calls if c.method == "delete"} <= {"service_principal_secrets"}


# --- the credential probe ---------------------------------------------------------------------


class _Response(io.BytesIO):
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_the_probe_answers_yes_or_no_and_never_raises_the_secret() -> None:
    requests: list[Any] = []

    def ok(request: Any, timeout: float) -> _Response:
        requests.append(request)
        return _Response(b'{"access_token": "token"}')

    def boom(request: Any, timeout: float) -> _Response:
        raise RuntimeError(f"refused: {request.headers}")

    probe = account_token_probe("https://accounts.cloud.databricks.com", "acct", opener=ok)
    assert probe("app", SecretStr("canary-probe")) is True
    (request,) = requests
    assert request.full_url == "https://accounts.cloud.databricks.com/oidc/accounts/acct/v1/token"
    assert request.data == b"grant_type=client_credentials&scope=all-apis"
    assert account_token_probe("https://accounts.cloud.databricks.com", "acct", opener=boom)("app", SecretStr("x")) is False
