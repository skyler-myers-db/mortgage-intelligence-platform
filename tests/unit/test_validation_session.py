"""The <client>-validate validation profile and nightly.yml's sessions (M1-PR8).

tools/clients/validation_profile.py names what the validate environment holds
and refuses everything else by field and rule; tools/clients/validation_session.py
opens the validator's GitHub OIDC session (never the deployer, never an
admin) and the browser job's per-run App bearers. Every Databricks and GitHub
call here is a fake, so nothing reaches a workspace.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.client_profiles import IDENTITY_IDS, demo_profile, split_contexts
from tools.clients import dispatch_acks as acks
from tools.clients import dispatch_protections as protections
from tools.clients import validation_profile as vp
from tools.clients import validation_session as vs
from tools.clients.errors import ProfileRefusal
from tools.clients.schema import FIELD_CLASS, IDENTITY_ALIASES, FieldClass

ENVIRONMENT = "fictional-demo-validate"
# Where each kind runs: the validator jobs in <client>-validate, the browser job in <client>.
ENVIRONMENTS = {"validator": ENVIRONMENT, "browser": "fictional-demo"}
HOST = "https://dbc-unit.cloud.databricks.com"
SHA = "c" * 40


@pytest.fixture(autouse=True)
def _unmasked(monkeypatch: pytest.MonkeyPatch) -> None:
    # emit_mask writes ::add-mask:: lines under GITHUB_ACTIONS=true; the
    # value-free assertions below read output, so pin it off (masked mode is
    # tested on its own).
    monkeypatch.setenv("GITHUB_ACTIONS", "false")


def _contexts(kind: str = "browser", **overrides: str | None) -> tuple[dict[str, str], dict[str, str]]:
    names = set(vp.KINDS[kind])
    variables, secrets = split_contexts(demo_profile(**overrides))
    return (
        {name: value for name, value in variables.items() if name in names},
        {name: value for name, value in secrets.items() if name in names},
    )


def _files(tmp_path: Path, kind: str = "validator", **overrides: str | None) -> argparse.Namespace:
    variables, secrets = _contexts(kind, **overrides)
    (tmp_path / "vars.json").write_text(json.dumps(variables), encoding="utf-8")
    (tmp_path / "secrets.json").write_text(json.dumps(secrets), encoding="utf-8")
    return argparse.Namespace(
        vars_json=str(tmp_path / "vars.json"),
        secrets_json=str(tmp_path / "secrets.json"),
        environment=ENVIRONMENT,
        github_env=str(tmp_path / "github_env"),
        databrickscfg=str(tmp_path / "home" / ".databrickscfg"),
        command_argv=[],
    )


def _browser_files(tmp_path: Path) -> argparse.Namespace:
    """The browser job's collect files (the validator names) and its environment, <client>."""

    args = _files(tmp_path)
    args.environment = ENVIRONMENTS["browser"]
    return args


# ------------------------------------------------------------ the profile


def test_each_step_binds_only_its_names_and_the_collect_step_no_secret() -> None:
    deploy_only = {name for name, kind in FIELD_CLASS.items() if kind is FieldClass.SECRET} - set(vp.OPERATOR_SECRETS.values())
    for kind, names in vp.COLLECTS.items():
        env = vp.collect_env(kind)
        bound = " ".join(env.values())
        for name in deploy_only | set(IDENTITY_ALIASES) | {"DATABRICKS_TOKEN", "MIP_CI_DEPLOYER_CLIENT_ID"}:
            assert f".{name} " not in f"{bound} ", (kind, name)
        assert {key.split("__", 1)[1] for key in env if "__" in key} == set(names)
    validator = " ".join(vp.collect_env("validator").values())
    assert all(f"secrets.{name}" not in validator for name in vp.OPERATOR_SECRETS.values())
    assert set(vp.COLLECTS["operators"]) == set(vp.OPERATOR_SECRETS.values())
    assert set(vp.KINDS["browser"]) == set(vp.COLLECTS["validator"]) | set(vp.COLLECTS["operators"])
    assert set(vp.OPERATOR_SECRETS) == {"normal", "operator2", "admin"}


def test_the_operator_secrets_come_from_the_steps_env_and_no_child_inherits_one() -> None:
    secrets = vs.step_secrets(_operator_env(MIP_CLIENT_SECRET__DATABRICKS_TOKEN="dapiEXAMPLE"))

    assert set(secrets) == set(vp.COLLECTS["operators"]), "only the three operator names are read"
    with pytest.raises(ProfileRefusal, match="DATABRICKS_ADMIN_CLIENT_SECRET: is secret; store it as an environment secret"):
        vs.step_secrets({"MIP_CLIENT_VARS_MISFILED": "DATABRICKS_ADMIN_CLIENT_SECRET"})
    child = vs._child_environ({**_operator_env(), "MIP_CLIENT_VARS_MISFILED": "", "PATH": "/usr/bin"})
    assert child == {"PATH": "/usr/bin"}


@pytest.mark.parametrize("kind", sorted(vp.KINDS))
def test_the_demo_subset_resolves_for_each_kind(kind: str) -> None:
    profile = vp.resolve(*_contexts(kind), environment=ENVIRONMENTS[kind], kind=kind)

    assert (profile.client, profile.environment, profile.host) == ("fictional-demo", ENVIRONMENTS[kind], HOST)
    assert profile.validator == IDENTITY_IDS["validator"]
    assert set(profile.operator_secrets) == (set(vp.OPERATOR_SECRETS) if kind == "browser" else set())
    assert "secret-" not in repr(profile) and IDENTITY_IDS["validator"] not in repr(profile)


def _refusal(kind: str = "browser", environment: str | None = None, **edits: Any) -> str:
    variables, secrets = _contexts(kind)
    for name, value in edits.items():
        target, key = name.split("__", 1)
        context = variables if target == "var" else secrets
        if value is None:
            context.pop(key, None)
        else:
            context[key] = value
    with pytest.raises(ProfileRefusal) as caught:
        vp.resolve(variables, secrets, environment=environment or ENVIRONMENTS[kind], kind=kind)
    return "\n".join(caught.value.lines)


def test_an_environment_without_a_validation_profile_refuses_with_the_owner_action() -> None:
    text = _refusal("validator", var__MIP_CLIENT=None)

    assert "fictional-demo-validate: holds no validation profile" in text
    assert "tools.clients.github_environment --apply" in text


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        ({"var__MIP_CLIENT": "another-client"}, "MIP_CLIENT: must equal the GitHub Environment"),
        ({"var__DATABRICKS_HOST": HOST}, "DATABRICKS_HOST: is identifying; store it as an environment secret"),
        ({"secret__MIP_APP_NAME": "mip-app"}, "MIP_APP_NAME: is public; store it as an environment variable"),
        ({"secret__DATABRICKS_ADMIN_CLIENT_SECRET": None}, "DATABRICKS_ADMIN_CLIENT_SECRET: required in the fictional-demo environment as a secret"),
        ({"secret__DATABRICKS_HOST": "https://<workspace-host>"}, "DATABRICKS_HOST: is a template placeholder"),
        ({"secret__DATABRICKS_HOST": "https://dbc-unit.cloud.databricks.com/path"}, "DATABRICKS_HOST: must be a bare https:// origin"),
        ({"secret__MIP_IDENTITY_IDS_JSON": "{not json"}, "MIP_IDENTITY_IDS_JSON: must be the client's identity map"),
        ({"var__MIP_APP_NAME": "Not A Dns Name"}, "MIP_APP_NAME: must be a lowercase DNS-style name"),
    ],
)
def test_each_misfiled_missing_or_malformed_value_refuses_by_name_never_by_value(edits: dict[str, Any], expected: str) -> None:
    text = _refusal(**edits)

    assert expected in text
    for value in (HOST, "secret-admin-alpha", "secret-normal-alpha", IDENTITY_IDS["validator"], "Not A Dns Name"):
        assert value not in text


def test_the_identity_map_must_carry_the_validator_role() -> None:
    without = {role: value for role, value in IDENTITY_IDS.items() if role != "validator"}
    assert "MIP_IDENTITY_IDS_JSON: must be the client's identity map" in _refusal(secret__MIP_IDENTITY_IDS_JSON=json.dumps(without))


@pytest.mark.parametrize("environment", ["fictional-demo", "dev-validate", "acme-validate-validate", "Fictional Demo-validate", "x-validate"])
def test_only_a_clients_validate_environment_is_accepted(environment: str) -> None:
    with pytest.raises(ProfileRefusal, match="must be <client>-validate"):
        vp.client_of(environment)


@pytest.mark.parametrize("environment", ["fictional-demo-validate", "dev", "github-pages", "Fictional Demo", "x"])
def test_the_browser_kind_accepts_only_a_clients_deploy_environment(environment: str) -> None:
    assert vp.client_of("fictional-demo", "browser") == "fictional-demo"
    with pytest.raises(ProfileRefusal, match="must be <client> for the browser kind"):
        vp.client_of(environment, "browser")


def test_masks_cover_the_host_its_origin_and_every_identity_in_each_spelling() -> None:
    profile = vp.resolve(*_contexts("validator"), environment=ENVIRONMENT, kind="validator")
    found = set(vp.masks(profile))

    assert {HOST, HOST.removeprefix("https://"), *IDENTITY_IDS.values()} <= found


# ------------------------------------------------------------ the validator session


class FakeClient:
    def __init__(self, *, user: str, groups: tuple[str, ...] = ("users",), resources: list[Any] | None = None, **config: Any) -> None:
        self.kwargs = config
        self.config = SimpleNamespace(
            auth_type="github-oidc",
            client_id=IDENTITY_IDS["validator"],
            authenticate=lambda: {"Authorization": "Bearer validator-token-EXAMPLE"},
        )
        self.current_user = SimpleNamespace(me=lambda: SimpleNamespace(user_name=user, groups=[SimpleNamespace(display=g) for g in groups]))
        app = SimpleNamespace(
            url="https://mip-app-0000.example.databricksapps.com",
            resources=resources
            if resources is not None
            else [
                SimpleNamespace(sql_warehouse=SimpleNamespace(id="wh-EXAMPLE"), genie_space=None),
                SimpleNamespace(sql_warehouse=None, genie_space=SimpleNamespace(space_id="space-EXAMPLE")),
            ],
        )
        self.apps = SimpleNamespace(get=lambda name: app)


def _operator_env(**extra: str) -> dict[str, str]:
    """A minting step's own env: the three operator secrets, bound one by one."""

    secrets = _contexts("browser")[1]
    return {f"MIP_CLIENT_SECRET__{name}": secrets[name] for name in vp.COLLECTS["operators"]} | extra


def _bearer_owner(token: str) -> str:
    """The application id a fake bearer ("bearer-<last two>-EXAMPLE") was minted for."""

    return next(value for value in IDENTITY_IDS.values() if value.endswith(token.split("-")[1]))


def _factory(calls: list[dict[str, Any]], *, admin_roles: tuple[str, ...] = (), wrong_roles: tuple[str, ...] = (), **client: Any):
    def factory(**kwargs: Any) -> FakeClient:
        calls.append(kwargs)
        if "token" not in kwargs:
            return FakeClient(user=client.get("user", IDENTITY_IDS["validator"]), **{k: v for k, v in client.items() if k != "user"})
        user = _bearer_owner(kwargs["token"])
        role = next(name for name, value in IDENTITY_IDS.items() if value == user)
        groups = ("users", "admins") if role in admin_roles else ("users",)
        return FakeClient(user=IDENTITY_IDS["deployer"] if role in wrong_roles else user, groups=groups)

    return factory


OIDC_ENV = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.example", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-EXAMPLE"}


def _env_lines(args: argparse.Namespace) -> dict[str, str]:
    path = Path(args.github_env)
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()) if path.exists() else {}


def test_the_session_is_the_validator_never_an_admin_and_exports_the_app_resources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args, calls = _files(tmp_path, "validator"), []

    assert vs.session(args, OIDC_ENV, _factory(calls)) == 0

    cfg = Path(args.databrickscfg).read_text(encoding="utf-8")
    assert cfg == f"[mip-validator]\nhost = {HOST}\nauth_type = github-oidc\nclient_id = {IDENTITY_IDS['validator']}\n"
    assert IDENTITY_IDS["deployer"] not in cfg and oct(Path(args.databrickscfg).stat().st_mode)[-3:] == "600"
    assert calls == [{"profile": "mip-validator", "config_file": args.databrickscfg}]
    env = _env_lines(args)
    assert env["DATABRICKS_TOKEN"] == env["MIP_BEARER_TOKEN"] == "validator-token-EXAMPLE"
    assert (env["DATABRICKS_WAREHOUSE_ID"], env["GENIE_SPACE_ID"], env["DATABRICKS_HOST"]) == ("wh-EXAMPLE", "space-EXAMPLE", HOST)
    assert env["MIP_APP_URL"] == env["MIP_DEPLOYED_APP_URL"] and env["MIP_APP_NAME"] == "mip-app"
    out = capsys.readouterr().out
    assert "me_not_in_admins=true" in out
    for value in (HOST, "validator-token-EXAMPLE", "wh-EXAMPLE", IDENTITY_IDS["validator"]):
        assert value not in out


@pytest.mark.parametrize(
    ("client", "false_check"),
    [
        ({"groups": ("users", "admins")}, "me_not_in_admins=false"),
        ({"user": IDENTITY_IDS["deployer"]}, "me_is_the_validator=false"),
        ({"resources": []}, "app_names_its_url_warehouse_and_genie_space=false"),
    ],
    ids=["admin", "deployer", "no-app-resources"],
)
def test_the_session_refuses_an_admin_the_deployer_or_an_unbound_app_and_exports_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], client: dict[str, Any], false_check: str
) -> None:
    args = _files(tmp_path, "validator")

    assert vs.session(args, OIDC_ENV, _factory([], **client)) == 2
    assert false_check in capsys.readouterr().out
    assert _env_lines(args) == {}


@pytest.mark.parametrize(
    "environ",
    [{}, {**OIDC_ENV, "DATABRICKS_TOKEN": "dapiEXAMPLE"}, {**OIDC_ENV, "DATABRICKS_CLIENT_SECRET": "x"}],
    ids=["no-id-token", "ambient-pat", "ambient-m2m"],
)
def test_the_session_refuses_without_an_id_token_or_with_ambient_credentials_before_any_call(tmp_path: Path, environ: dict[str, str]) -> None:
    calls: list[dict[str, Any]] = []

    assert vs.session(_files(tmp_path, "validator"), environ, _factory(calls)) == 2
    assert calls == [], "no Databricks client was ever built"


def test_a_missing_validate_profile_refuses_before_any_databricks_call(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, Any]] = []
    args = _files(tmp_path, "validator", MIP_CLIENT=None)

    assert vs.main(["session", "--vars-json", args.vars_json, "--secrets-json", args.secrets_json, "--environment", ENVIRONMENT,
                    "--databrickscfg", args.databrickscfg, "--github-env", args.github_env], factory=_factory(calls)) == 2
    assert calls == [] and not Path(args.databrickscfg).exists()
    assert "holds no validation profile" in capsys.readouterr().err


def test_the_remint_reproves_the_validator_before_minting(tmp_path: Path) -> None:
    args = _files(tmp_path, "validator")
    assert vs.session(args, OIDC_ENV, _factory([])) == 0
    Path(args.github_env).unlink()

    assert vs.remint(args, OIDC_ENV, _factory([])) == 0
    assert set(_env_lines(args)) == {"DATABRICKS_TOKEN", "MIP_BEARER_TOKEN"}
    Path(args.github_env).unlink()
    assert vs.remint(args, OIDC_ENV, _factory([], groups=("admins",))) == 2
    assert _env_lines(args) == {}


def test_masked_mode_registers_each_identifier_before_it_prints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    assert vs.session(_files(tmp_path, "validator"), OIDC_ENV, _factory([])) == 0
    err = capsys.readouterr().err
    for value in (HOST, IDENTITY_IDS["validator"], "validator-token-EXAMPLE", "wh-EXAMPLE", "space-EXAMPLE"):
        assert f"::add-mask::{value}\n" in err


# ------------------------------------------------------------ the browser job's bearers


def _mint_runner(children: list[dict[str, str]], commands: list[list[str]]):
    def runner(argv: list[str], *, env: dict[str, str], check: bool, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[1:2] == [str(vs.MINT)]:
            children.append(env)
            Path(argv[argv.index("--output-file") + 1]).write_text(f"bearer-{env['DATABRICKS_CLIENT_ID'][-2:]}-EXAMPLE\n", encoding="utf-8")
        else:
            # The command after -- inherits the bearers, never an operator secret.
            assert not [name for name in env if name.startswith("MIP_CLIENT_")], sorted(env)
            assert "secret-normal-alpha" not in env.values()
            commands.append([*argv, env.get("MIP_ADMIN_BEARER_TOKEN", ""), env.get("MIP_APP_URL", "")])
        return subprocess.CompletedProcess(argv, 0)

    return runner


def test_each_operator_bearer_is_minted_in_a_child_holding_only_its_own_identity(tmp_path: Path) -> None:
    args, children, calls = _browser_files(tmp_path), [], []

    assert vs.operators(args, _operator_env(), _factory(calls), runner=_mint_runner(children, [])) == 0

    expected = [(IDENTITY_IDS["normal"], "secret-normal-alpha"), (IDENTITY_IDS["operator2"], "secret-operator2-alpha"), (IDENTITY_IDS["admin"], "secret-admin-alpha")]
    assert [(child["DATABRICKS_CLIENT_ID"], child["DATABRICKS_CLIENT_SECRET"]) for child in children] == expected
    for child in children:
        assert set(child) == {"PATH", "GITHUB_ACTIONS", "DATABRICKS_HOST", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"}
        assert child["DATABRICKS_HOST"] == HOST
    env = _env_lines(args)
    assert env["MIP_BEARER_TOKEN"] == env["MIP_NON_ADMIN_BEARER_TOKEN"] == "bearer-04-EXAMPLE"
    assert (env["MIP_OPERATOR2_BEARER_TOKEN"], env["MIP_ADMIN_BEARER_TOKEN"]) == ("bearer-05-EXAMPLE", "bearer-06-EXAMPLE")
    assert env["MIP_APP_URL"] == env["MIP_API_URL"]
    # Each bearer proves its own identity; the App's URL is read with the
    # normal operator's bearer, never a workspace token.
    assert calls == [
        {"host": HOST, "token": token, "auth_type": "pat"}
        for token in ("bearer-04-EXAMPLE", "bearer-05-EXAMPLE", "bearer-06-EXAMPLE", "bearer-04-EXAMPLE")
    ]


@pytest.mark.parametrize(
    ("factory_kwargs", "false_check"),
    [
        ({"admin_roles": ("admin",)}, "admin_not_in_admins=false"),
        ({"admin_roles": ("normal",)}, "normal_not_in_admins=false"),
        ({"wrong_roles": ("operator2",)}, "operator2_bearer_is_its_identity=false"),
    ],
    ids=["app-admin-is-a-workspace-admin", "normal-is-a-workspace-admin", "bearer-of-another-identity"],
)
def test_an_operator_bearer_that_is_a_workspace_admin_or_another_identity_exports_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], factory_kwargs: dict[str, Any], false_check: str
) -> None:
    args, commands = _browser_files(tmp_path), []
    args.command_argv = ["python", "-m", "tools.cleanup_live_campaign_fixtures", "--all-run-markers"]

    assert vs.operators(args, _operator_env(), _factory([], **factory_kwargs), runner=_mint_runner([], commands)) == 2
    out = capsys.readouterr().out
    assert false_check in out and "nothing was exported" in out
    assert commands == [] and _env_lines(args) == {}
    for value in (IDENTITY_IDS["admin"], IDENTITY_IDS["normal"], "bearer-06-EXAMPLE"):
        assert value not in out


def test_the_browser_job_refuses_an_ambient_workspace_token(tmp_path: Path) -> None:
    children: list[dict[str, str]] = []

    environ = _operator_env(DATABRICKS_TOKEN="dapiEXAMPLE")

    assert vs.operators(_browser_files(tmp_path), environ, _factory([]), runner=_mint_runner(children, [])) == 2
    assert children == []


def test_after_a_double_dash_the_command_runs_with_the_bearers_and_nothing_is_appended(tmp_path: Path) -> None:
    args, commands = _browser_files(tmp_path), []
    args.command_argv = ["python", "-m", "tools.cleanup_live_campaign_fixtures", "--all-run-markers"]

    assert vs.operators(args, _operator_env(PATH="/usr/bin"), _factory([]), runner=_mint_runner([], commands)) == 0
    assert commands == [[*args.command_argv, "bearer-06-EXAMPLE", "https://mip-app-0000.example.databricksapps.com"]]
    assert _env_lines(args) == {}


# ------------------------------------------------------------ the commit check and the environment check


@pytest.mark.parametrize(
    ("answers", "code"),
    [([(200, {"git_sha": SHA})], 0), ([(503, None), (200, {"git_sha": SHA})], 0), ([(200, {"git_sha": "d" * 40})], 2), ([(401, None)] * 3, 2)],
    ids=["match", "warm-up", "other-commit", "never-200"],
)
def test_app_sha_holds_the_deployed_commit_to_the_runs(capsys: pytest.CaptureFixture[str], answers: list[Any], code: int) -> None:
    replies = iter(answers)
    args = argparse.Namespace(token_env="MIP_BEARER_TOKEN", git_sha=SHA, attempts=3, interval=0)
    environ = {"MIP_APP_URL": "https://mip-app-0000.example.databricksapps.com", "MIP_BEARER_TOKEN": "bearer-EXAMPLE"}

    assert vs.app_sha(args, environ, health=lambda url, token: next(replies), sleep=lambda s: None) == code
    out = capsys.readouterr().out
    assert "databricksapps" not in out and "bearer-EXAMPLE" not in out


def _check(monkeypatch: pytest.MonkeyPatch, result: protections.Result, ack: str = "") -> tuple[int, str]:
    monkeypatch.setattr(protections, "from_environ", lambda environ, **kwargs: object())
    monkeypatch.setattr(protections, "read", lambda session, environment, mode, actor: result)
    environ = {"GITHUB_SHA": SHA, "GITHUB_ACTOR_ID": "42", "MIP_DISPATCH_PROTECTIONS_ACK": ack}
    return vs.check_environment(argparse.Namespace(client="fictional-demo"), environ), ""


def test_the_validate_environment_must_exist_with_its_protections(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert _check(monkeypatch, protections.Result(protections.VERIFIED))[0] == 0
    missing = protections.Result(protections.DRIFT, ("github: the fictional-demo-validate environment is not in the listing",))
    assert _check(monkeypatch, missing)[0] == 2
    out = capsys.readouterr().out
    assert "is not in the listing" in out and "github_environment --apply" in out


def test_unreadable_protections_need_the_exact_ack_per_environment_for_this_client_and_sha(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    unreadable = protections.Result(protections.UNREADABLE, cause="HTTP 403")
    validate = acks.protections_ack("fictional-demo", ENVIRONMENT, SHA)
    deploy = acks.protections_ack("fictional-demo", "fictional-demo", SHA)

    assert _check(monkeypatch, unreadable)[0] == 2
    out = capsys.readouterr().out
    assert f"protections_unverified_ack holding {validate}" in out and f"protections_unverified_ack holding {deploy}" in out
    assert _check(monkeypatch, unreadable, ack=f"{validate}, {deploy}")[0] == 0
    # One environment's ack never stands for the other's, nor another sha's.
    for partial in (validate, deploy, f"{validate} {acks.protections_ack('fictional-demo', 'fictional-demo', 'd' * 40)}"):
        assert _check(monkeypatch, unreadable, ack=partial)[0] == 2


def test_both_environments_nightly_names_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    read: list[str] = []

    def fake_read(session: object, environment: str, mode: str, actor: str) -> protections.Result:
        read.append(environment)
        return protections.Result(protections.VERIFIED) if environment == ENVIRONMENT else protections.Result(protections.DRIFT, ("x",))

    monkeypatch.setattr(protections, "from_environ", lambda environ, **kwargs: object())
    monkeypatch.setattr(protections, "read", fake_read)

    assert vs.check_environment(argparse.Namespace(client="fictional-demo"), {"GITHUB_SHA": SHA, "GITHUB_ACTOR_ID": "42"}) == 2
    assert read == ["fictional-demo", ENVIRONMENT]
