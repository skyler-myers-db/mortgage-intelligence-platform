"""Identifying and secret profile fields never come from GitHub variables.

A GitHub Environment variable prints in clear in step env and in the run log
of a public repository (plan threat T2), so the resolver refuses an
identifying or secret field, or any alias of one, found in ``vars``. A public
field found in ``secrets`` is refused too: the runner would mask it, and a
masked catalog or scope name blanks unrelated log text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.fixtures.client_profiles import demo_profile, split_contexts
from tools.clients.__main__ import main
from tools.clients.environment import KNOWN_NAMES, from_github, read_context, resolve
from tools.clients.errors import ProfileRefusal
from tools.clients.schema import ALIASES, FieldClass, name_class

ROOT = Path(__file__).resolve().parents[2]
# The demo fixture's MIP_CLIENT, which is also its environment's name.
ENVIRONMENT = "fictional-demo"
_NON_PUBLIC = sorted(name for name in KNOWN_NAMES if name_class(name) is not FieldClass.PUBLIC)
_PUBLIC = sorted(name for name in KNOWN_NAMES if name_class(name) is FieldClass.PUBLIC)
# Identifying names deploy-dev.yml still reads from ``vars`` at origin/main
# 6c363903. The per-client deploy workflow (M1-PR7) reads them from the
# profile's secrets and empties this set; it may only shrink.
_LEGACY_IDENTIFYING_VARS = frozenset(
    {
        "MIP_ADMIN_EMAILS",
        "MIP_ADMIN_GROUP_NAME",
        "MIP_APPROVER_GROUP_NAME",
        "MIP_AUDITOR_EMAILS",
        "MIP_CI_DEPLOYER_CLIENT_ID",
        "MIP_UC_APPROVED_OWNER_PRINCIPALS",
    }
)


def test_every_known_name_has_a_class() -> None:
    assert all(name_class(name) is not None for name in KNOWN_NAMES)
    assert "MIP_CI_DEPLOYER_CLIENT_ID" in _NON_PUBLIC
    assert {alias for aliases in ALIASES.values() for alias in aliases} <= set(KNOWN_NAMES)


@pytest.mark.parametrize("name", _NON_PUBLIC)
def test_an_identifying_or_secret_name_in_vars_is_refused(name: str) -> None:
    variables, secrets = split_contexts(demo_profile())
    variables[name] = "value-that-must-not-print"
    with pytest.raises(ProfileRefusal) as caught:
        from_github(variables, secrets, environment=ENVIRONMENT)
    assert caught.value.lines == (
        f"{name}: is {name_class(name)}; store it as an environment secret, never a variable",
    )
    assert "value-that-must-not-print" not in str(caught.value)


@pytest.mark.parametrize("name", _PUBLIC)
def test_a_public_name_in_secrets_is_refused(name: str) -> None:
    variables, secrets = split_contexts(demo_profile())
    secrets[name] = "public-value"
    with pytest.raises(ProfileRefusal) as caught:
        from_github(variables, secrets, environment=ENVIRONMENT)
    assert caught.value.lines[0].startswith(f"{name}: is public; store it as an environment variable")


def test_a_profile_split_by_class_resolves_and_unknown_keys_are_ignored() -> None:
    variables, secrets = split_contexts(demo_profile())
    variables["REPOSITORY_WIDE_VARIABLE"] = "anything"
    secrets["github_token"] = "unused-token-value"
    assert resolve(from_github(variables, secrets, environment=ENVIRONMENT)).client == ENVIRONMENT


@pytest.mark.parametrize("environment", [ENVIRONMENT, "Fictional-Demo"])
def test_the_contexts_resolve_in_their_own_environment(environment: str) -> None:
    # GitHub matches environment names without regard to case.
    variables, secrets = split_contexts(demo_profile())
    assert resolve(from_github(variables, secrets, environment=environment)).client == ENVIRONMENT


@pytest.mark.parametrize("environment", ["fictional-two", "", "  "])
def test_the_contexts_are_refused_in_another_environment(environment: str) -> None:
    variables, secrets = split_contexts(demo_profile())
    with pytest.raises(ProfileRefusal) as caught:
        from_github(variables, secrets, environment=environment)
    expected = (
        "MIP_CLIENT: must equal the GitHub Environment the job runs in"
        if environment.strip()
        else "--environment: name the GitHub Environment the job runs in"
    )
    assert caught.value.lines == (expected,)


def test_a_client_key_only_the_repository_holds_fails_the_binding() -> None:
    # A second client's environment that omits MIP_CLIENT inherits the
    # repository-level one, because GitHub merges the scopes into one context.
    repository_vars, repository_secrets = split_contexts(demo_profile())
    variables, secrets = split_contexts(demo_profile(MIP_CLIENT=None, MIP_APP_NAME="mip-app-two"))
    with pytest.raises(ProfileRefusal) as caught:
        from_github(
            {**repository_vars, **variables},
            {**repository_secrets, **secrets},
            environment="fictional-two",
        )
    assert caught.value.lines == ("MIP_CLIENT: must equal the GitHub Environment the job runs in",)


def test_the_cli_needs_the_environment_with_the_contexts_and_only_then(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    variables, secrets = split_contexts(demo_profile())
    (tmp_path / "vars.json").write_text(json.dumps(variables), encoding="utf-8")
    (tmp_path / "secrets.json").write_text(json.dumps(secrets), encoding="utf-8")
    contexts = ["--vars-json", str(tmp_path / "vars.json"), "--secrets-json", str(tmp_path / "secrets.json")]
    assert main(["check", *contexts]) == 2
    assert "--environment: required with the CI contexts" in capsys.readouterr().err
    assert main(["check", *contexts, "--environment", ENVIRONMENT]) == 0
    assert capsys.readouterr().out.startswith(f"client profile ok: client={ENVIRONMENT} ")
    assert main(["check", "--env-file", str(tmp_path / "absent.env"), "--environment", ENVIRONMENT]) == 2
    assert "--environment: applies to the CI contexts only" in capsys.readouterr().err


def test_contexts_come_from_a_step_env_or_a_file_never_argv(tmp_path: Path) -> None:
    variables, secrets = split_contexts(demo_profile())
    assert read_context("env:MIP_VARS_JSON", {"MIP_VARS_JSON": json.dumps(variables)}) == variables
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps(secrets), encoding="utf-8")
    assert read_context(str(path)) == secrets
    with pytest.raises(ProfileRefusal, match="never JSON on the command line"):
        read_context(json.dumps(secrets))
    with pytest.raises(ProfileRefusal, match="must hold a JSON object"):
        read_context("env:MIP_ABSENT", {})


def test_the_cli_refuses_an_identifying_variable_without_printing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    variables, secrets = split_contexts(demo_profile())
    host = secrets.pop("DATABRICKS_HOST")
    variables["DATABRICKS_HOST"] = host
    (tmp_path / "vars.json").write_text(json.dumps(variables), encoding="utf-8")
    (tmp_path / "secrets.json").write_text(json.dumps(secrets), encoding="utf-8")
    code = main(
        [
            "check",
            *("--vars-json", str(tmp_path / "vars.json"), "--secrets-json", str(tmp_path / "secrets.json")),
            *("--environment", ENVIRONMENT),
        ]
    )
    out = capsys.readouterr()
    assert code == 2
    assert "DATABRICKS_HOST: is identifying" in out.err
    assert host not in out.out + out.err


def test_workflows_read_no_new_identifying_name_from_vars() -> None:
    referenced: set[str] = set()
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        referenced.update(re.findall(r"\bvars\.([A-Z][A-Z0-9_]*)", workflow.read_text(encoding="utf-8")))
    identifying = {name for name in referenced if name in KNOWN_NAMES and name not in _PUBLIC}
    assert identifying <= _LEGACY_IDENTIFYING_VARS, sorted(identifying - _LEGACY_IDENTIFYING_VARS)
    secret = {name for name in identifying if name_class(name) is FieldClass.SECRET}
    assert not secret
