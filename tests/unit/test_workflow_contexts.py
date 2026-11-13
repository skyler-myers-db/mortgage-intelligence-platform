"""deploy-dev.yml names each profile field it reads, and no step holds a whole context (M1-PR7 review, G2).

GitHub prints a step's ``with:`` and ``env:`` values in its log header before
the step runs, and masks secrets only: a variable prints in clear in a public
log. tools/clients/workflow_contexts.py generates the only two places the
workflow reads ``vars`` or ``secrets``; these tests hold the workflow equal to
them, parse every other expression in it (dot, bracket, whole-context and
function forms alike), and run both steps' scripts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures.workflow_expressions import expressions_in, references, render, scalars
from tools.clients import workflow_contexts as contexts
from tools.clients.schema import ALIASES, FIELD_CLASS, IDENTITY_ALIASES, FieldClass

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "deploy-dev.yml"
ACTION = REPO / ".github" / "actions" / "resolve-client-profile" / "action.yml"
SCOPE_STEP = ("check-dispatch", "Refuse a profile name set at repository or organization level")
COLLECT_STEP = ("deploy", "Collect the client's profile from its environment")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(workflow: dict[str, Any], job: str, name: str) -> dict[str, Any]:
    (step,) = [step for step in workflow["jobs"][job]["steps"] if step.get("name") == name]
    return step


def test_the_bound_names_are_the_schemas_fields_and_their_aliases_never_an_identity_alias() -> None:
    aliases = {alias for names in ALIASES.values() for alias in names}
    assert set(contexts.BOUND_NAMES) == {*FIELD_CLASS, *aliases}
    assert set(contexts.PUBLIC_NAMES) | set(contexts.PRIVATE_NAMES) == set(contexts.BOUND_NAMES)
    assert not set(contexts.PUBLIC_NAMES) & set(contexts.PRIVATE_NAMES)
    assert set(contexts.PRIVATE_NAMES) == {name for name, kind in FIELD_CLASS.items() if kind is not FieldClass.PUBLIC}
    assert not set(contexts.BOUND_NAMES) & set(IDENTITY_ALIASES), "an identity alias never supplies a value"
    assert "DATABRICKS_TOKEN" not in contexts.BOUND_NAMES
    # check-dispatch also refuses an identity alias at repository scope.
    assert set(contexts.SCOPE_NAMES) == {*contexts.BOUND_NAMES, *IDENTITY_ALIASES}
    assert "DATABRICKS_TOKEN" not in contexts.SCOPE_NAMES


def test_deploy_dev_binds_exactly_the_generated_names() -> None:
    workflow = _workflow()
    scope, collect = _step(workflow, *SCOPE_STEP), _step(workflow, *COLLECT_STEP)

    assert scope["env"] == contexts.repository_scope_env()
    assert collect["env"] == contexts.collect_env()
    # A private name is read from vars by presence only: its value is never bound.
    for name in contexts.PRIVATE_NAMES:
        assert f"{contexts.VAR_PREFIX}{name}" not in collect["env"]
    assert collect["run"].split()[:4] == ["python", "-m", "tools.clients.workflow_contexts", "collect"]


def test_the_print_command_emits_both_blocks_as_the_workflow_holds_them(capsys: pytest.CaptureFixture[str]) -> None:
    assert contexts.main(["print"]) == 0
    text = capsys.readouterr().out
    scope_text, collect_text = text.split("# deploy, ", 1)
    assert yaml.safe_load(scope_text) == contexts.repository_scope_env()
    assert yaml.safe_load(collect_text.split("\n", 1)[1]) == contexts.collect_env()
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("#"):
            assert f"{line}\n" in workflow, line


def _violations(workflow: dict[str, Any]) -> list[str]:
    """Each use of vars or secrets outside the two generated steps, or of another form inside them."""

    allowed = {
        SCOPE_STEP: {(context, name) for context in ("vars", "secrets") for name in contexts.SCOPE_NAMES},
        COLLECT_STEP: {(context, name) for context in ("vars", "secrets") for name in contexts.BOUND_NAMES},
    }
    found: list[str] = []
    for path, value in scalars(workflow):
        where: tuple[str, str] | None = None
        if len(path) >= 4 and path[0] == "jobs" and path[2] == "steps":
            step = workflow["jobs"][path[1]]["steps"][int(path[3])]
            where = (path[1], step.get("name", ""))
        for expression in expressions_in(value, condition=path[-1] == "if"):
            for reference in references(expression):
                if where not in allowed or reference not in allowed[where]:
                    found.append(f"{'/'.join(path)}: {reference}")
    return found


def test_no_other_expression_in_the_workflow_or_the_action_names_vars_or_secrets() -> None:
    assert _violations(_workflow()) == []
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert [
        reference
        for _path, value in scalars(action)
        for expression in expressions_in(value)
        for reference in references(expression)
    ] == []


@pytest.mark.parametrize(
    "expression",
    (
        "${{ fromJSON(secrets['MIP_IDENTITY_IDS_JSON']).deployer }}",
        "${{ secrets[format('{0}', 'DATABRICKS_HOST')] }}",
        "${{ toJSON(secrets) }}",
        "${{ toJSON(vars) }}",
        "${{ Secrets.DATABRICKS_HOST }}",
        "${{ vars.MIP_CLIENT }}",
    ),
)
def test_any_form_of_a_context_reference_in_another_step_is_caught(expression: str) -> None:
    """The dot form only was matched before: a bracket or a whole context passed every test."""

    workflow = _workflow()
    steps = workflow["jobs"]["deploy"]["steps"]
    steps.insert(1, {"name": "Print the deployer", "env": {"DEPLOYER": expression}, "run": 'echo "deployer is $DEPLOYER"'})
    assert _violations(workflow) == [f"jobs/deploy/steps/1/env/DEPLOYER: {next(iter(references(expression[3:-2])))}"]
    collect = _step(workflow, *COLLECT_STEP)
    collect["env"]["EXTRA"] = expression
    inside = [item for item in _violations(workflow) if "/env/EXTRA:" in item]
    if expression in ("${{ vars.MIP_CLIENT }}", "${{ Secrets.DATABRICKS_HOST }}"):
        assert inside == [], "a bound name's dot form is the collect step's own (its env is pinned exactly above)"
    else:
        assert inside, "inside the collect step too, only a bound name's dot form"


def test_both_jobs_refuse_step_debug_logging_before_any_step_that_names_a_context() -> None:
    """Debug logging traces each expression with its operands, a variable's value included.

    check-dispatch refuses first; the deploy job right after its deadline step
    (M0-PR9 keeps that one first), which names no context.
    """

    workflow = _workflow()
    for job, guarded, position in ((*SCOPE_STEP, 0), (*COLLECT_STEP, 1)):
        steps = workflow["jobs"][job]["steps"]
        names = [step.get("name") for step in steps]
        assert names.index("Refuse step debug logging") == position < names.index(guarded)
        refusal = steps[position]
        assert refusal["if"] == "${{ runner.debug == '1' }}"
        assert "exit 1" in refusal["run"]
        for step in steps[: names.index(guarded)]:
            assert [
                reference
                for path, value in scalars(step)
                for expression in expressions_in(value, condition=path[-1:] == ("if",))
                for reference in references(expression)
            ] == [], step.get("name")


def _run_step(step: dict[str, Any], context: dict[str, Any], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {name: render(value, context) for name, value in step["env"].items()}
    return subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=REPO,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": str(REPO), "RUNNER_TEMP": str(tmp_path), **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_check_dispatch_refuses_a_repository_level_profile_name_and_prints_names_only(tmp_path: Path) -> None:
    step = _step(_workflow(), *SCOPE_STEP)
    values = {"zyrplax-admins-EXAMPLE", "https://dbc-zyrplax-EXAMPLE.cloud.databricks.com", "mip"}
    context = {
        "vars": {"MIP_ADMIN_GROUP_NAME": "zyrplax-admins-EXAMPLE", "MIP_DEFAULT_CATALOG": "mip", "UNRELATED": "x"},
        "secrets": {"DATABRICKS_HOST": "https://dbc-zyrplax-EXAMPLE.cloud.databricks.com", "DATABRICKS_TOKEN": "dapiEXAMPLE"},
    }
    header = "\n".join(f"{name}: {render(value, context)}" for name, value in step["env"].items())
    result = _run_step(step, context, tmp_path)

    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "variables: MIP_ADMIN_GROUP_NAME MIP_DEFAULT_CATALOG" in out and "secrets: DATABRICKS_HOST" in out
    for value in values - {"mip"}:
        assert value not in header and value not in out, "the header and the step print names only"
    assert "UNRELATED" not in out and "DATABRICKS_TOKEN" not in out, "only profile names are checked"
    assert _run_step(step, {"vars": {"UNRELATED": "x"}, "secrets": {"DATABRICKS_TOKEN": "dapiEXAMPLE"}}, tmp_path).returncode == 0


def test_check_dispatch_refuses_a_repository_level_identity_alias_too(tmp_path: Path) -> None:
    """An alias supplies no value (the collect step binds none), but another install's would ride along."""

    step = _step(_workflow(), *SCOPE_STEP)
    context = {
        "vars": {"MIP_CI_DEPLOYER_CLIENT_ID": "00000000-0000-4000-8000-00000000c0de"},
        "secrets": {"DATABRICKS_CLIENT_ID": "zyrplax-EXAMPLE-normal", "DATABRICKS_ACCOUNT_CLIENT_ID": "zyrplax-EXAMPLE-scim"},
    }
    result = _run_step(step, context, tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    out = result.stdout + result.stderr
    assert "variables: MIP_CI_DEPLOYER_CLIENT_ID" in out
    assert "secrets: DATABRICKS_ACCOUNT_CLIENT_ID DATABRICKS_CLIENT_ID" in out
    for value in (*context["vars"].values(), *context["secrets"].values()):
        assert value not in out, "names only"
    collect = _step(_workflow(), *COLLECT_STEP)
    for name in IDENTITY_ALIASES:
        assert name not in " ".join(collect["env"].values()), "the collect step still binds no alias"


@pytest.mark.parametrize(("client", "code"), (("client-one", 0), ("dev", 1), ("copilot", 1), ("github-pages", 1)))
def test_check_dispatch_refuses_an_environment_that_is_not_a_clients(client: str, code: int) -> None:
    step = _step(_workflow(), "check-dispatch", "Refuse a malformed client key or phase")
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        env={"PATH": os.environ["PATH"], "MIP_DISPATCH_CLIENT": client, "MIP_DISPATCH_PHASE": "plan"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == code, result.stdout + result.stderr
    for name in contexts.NON_CLIENT_ENVIRONMENTS:
        assert f"{name})" in step["run"] or f"{name}|" in step["run"] or f"|{name}" in step["run"]


def _collect_env(**values: str) -> dict[str, str]:
    context = {"vars": {}, "secrets": {}}
    for name, value in values.items():
        context["vars" if name.startswith("var_") else "secrets"][name.split("_", 1)[1]] = value
    return {name: render(value, context) for name, value in contexts.collect_env().items()}


def test_collect_writes_both_objects_create_only_and_mode_0600(tmp_path: Path) -> None:
    environ = _collect_env(var_MIP_CLIENT="client-one", var_MIP_APP_NAME="mip-app", secret_DATABRICKS_HOST="https://dbc-EXAMPLE")
    vars_out, secrets_out = tmp_path / "vars.json", tmp_path / "secrets.json"

    assert contexts.collect(environ, vars_out=vars_out, secrets_out=secrets_out) == (2, 1)
    assert json.loads(vars_out.read_text()) == {"MIP_APP_NAME": "mip-app", "MIP_CLIENT": "client-one"}
    assert json.loads(secrets_out.read_text()) == {"DATABRICKS_HOST": "https://dbc-EXAMPLE"}
    assert vars_out.stat().st_mode & 0o777 == 0o600 and secrets_out.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        contexts.collect(environ, vars_out=vars_out, secrets_out=tmp_path / "other.json")


def test_collect_refuses_a_misfiled_identifying_variable_by_name_never_by_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = _collect_env(var_MIP_CLIENT="client-one", var_MIP_ADMIN_EMAILS="ops@zyrplax-EXAMPLE.example")
    assert "ops@zyrplax-EXAMPLE.example" not in json.dumps(environ), "a misfiled value is never bound"
    assert environ[contexts.MISFILED_ENV].split() == ["MIP_ADMIN_EMAILS"]
    for name, value in environ.items():
        monkeypatch.setenv(name, value)

    assert contexts.main(["collect", "--vars-out", str(tmp_path / "v.json"), "--secrets-out", str(tmp_path / "s.json")]) == 2
    err = capsys.readouterr().err
    assert "MIP_ADMIN_EMAILS: is identifying; store it as an environment secret, never a variable" in err
    assert not (tmp_path / "v.json").exists(), "nothing is written on a refusal"
    monkeypatch.setenv(contexts.MISFILED_ENV, "MIP_CLIENT")
    assert contexts.main(["collect", "--vars-out", str(tmp_path / "v.json"), "--secrets-out", str(tmp_path / "s.json")]) == 2
    assert "names that are not identifying or secret profile names" in capsys.readouterr().err


def _owner_steps() -> str:
    text = (REPO / "docs" / "deployment.md").read_text(encoding="utf-8")
    return text.split("**Owner steps (never agent work).**", 1)[1].split("**Integrator steps.**", 1)[0]


def test_the_owner_steps_set_every_profile_name_in_the_client_environment_only() -> None:
    """check-dispatch refuses a repository-level profile name, so no owner step may tell the owner to set one."""

    steps = _owner_steps()
    unscoped = re.findall(r"gh\s+(?:secret|variable)\s+set\b(?!\s+--env\b)\s*\S*", steps)
    assert unscoped == [], "every gh secret/variable set names the client's environment"
    for name in re.findall(r"repository\s+(?:variable|secret)s?\s+`([A-Z0-9_]+)`", steps):
        assert name not in contexts.SCOPE_NAMES, f"{name}: a profile name, refused at repository level"
    assert not re.search(r"environment\s+shadows|dev\s+environment's\s+deployment\s+branch", steps), "no dev environment"
    carried = "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS" in WORKFLOW.read_text(encoding="utf-8")
    assert carried == bool(re.search(r"`deploy-dev\.yml`\s+passes\s+the\s+historical", steps)), (
        "the runbook says deploy-dev.yml passes the historical keys exactly when it does"
    )


def test_a_first_installs_bootstrap_warehouse_is_carried_as_a_secret_only() -> None:
    """B1: MIP_BOOTSTRAP_WAREHOUSE_ID is identifying, so deploy-dev.yml binds it from secrets alone."""

    name = "MIP_BOOTSTRAP_WAREHOUSE_ID"
    collect = contexts.collect_env()
    assert name in contexts.PRIVATE_NAMES and name not in contexts.PUBLIC_NAMES
    assert collect[f"{contexts.SECRET_PREFIX}{name}"] == f"${{{{ secrets.{name} }}}}"
    assert f"{contexts.VAR_PREFIX}{name}" not in collect, "never bound from vars by value"
    assert f"${{{{ vars.{name} && '{name}' || '' }}}}" in collect[contexts.MISFILED_ENV], "a misfiled var refuses"
    for scope in contexts.repository_scope_env().values():
        assert f".{name} && '{name}'" in scope, "refused at repository or organization scope"
    collected = _step(_workflow(), *COLLECT_STEP)["env"]
    assert collected[f"{contexts.SECRET_PREFIX}{name}"] == f"${{{{ secrets.{name} }}}}"
