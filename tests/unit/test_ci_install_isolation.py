"""The CI staging install is isolated from the live install (D-platform-process-b step 7, W5e).

release_gate_bootstrap_findings.md (binding): deploy-dev.yml must never upload
into, share state with, or fall back to the names of the live ``mip-app``
install. tools/databricks/ci_install_isolation.py is the fail-closed rule
table, the marked preflight block in scripts/lib/deploy_step_preflight_gates.sh
runs it before the confirmation prompt and any mutation, databricks.yml gives
the dev target a root leaf and an install suffix whose defaults render the live
install unchanged, and deploy-dev.yml / nightly.yml address the dev-ci install.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import ci_install_isolation as isolation
from tools.databricks import converge_static_app_source as converge

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "databricks.yml"
PREFLIGHT = ROOT / "scripts" / "lib" / "deploy_step_preflight_gates.sh"
DEPLOY_DEV = ROOT / ".github" / "workflows" / "deploy-dev.yml"
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"
DEPLOY_DEV_REF = "o/r/.github/workflows/deploy-dev.yml@refs/heads/main"
CI_REF = "o/r/.github/workflows/ci.yml@refs/heads/main"
LIVE_GENIE = "01f18188d7a41311abe3d99932b5aa9a"
STAGING = {
    "MIP_APP_NAME": "mip-app-pr105-staging",
    "MIP_DEFAULT_CATALOG": "mip_pr105_staging",
    "MIP_LAKEBASE_INSTANCE": "mip-app-state-staging",
    "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state_staging",
    "MIP_RUNTIME_SECRET_SCOPE": "mip-runtime-staging",
    "MIP_APP_ROLLBACK_SECRET_SCOPE": "mip-app-rollback-pr105-staging",
    "MIP_GENIE_SPACE_NAME": "Mortgage Lead Intelligence (staging)",
    "MIP_AI_GATEWAY_TABLE_PREFIX": "mip_agent_gateway_staging",
    "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE": "mip-agent-runtime-gateway-staging",
    "GENIE_SPACE_ID": "0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f",
}


def _ci(**extra: str) -> dict[str, str]:
    return {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": DEPLOY_DEV_REF, "MIP_BUNDLE_ROOT_LEAF": "dev-ci", **STAGING, **extra}


def _run(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = isolation.main(env)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ------------------------------------------------------------------ rule table


def test_the_ci_deploy_into_the_default_root_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    env = _ci()
    env.pop("MIP_BUNDLE_ROOT_LEAF")

    code, out, err = _run(env, capsys)
    assert (code, out) == (2, "")
    assert "MIP_BUNDLE_ROOT_LEAF" in err


def test_the_isolated_ci_install_passes_and_prints_its_two_values(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(_ci(), capsys)

    assert code == 0 and err == ""
    assert out == "bundle_root_leaf=dev-ci\ninstall_suffix=_ci\n"


@pytest.mark.parametrize("name", sorted(isolation.LIVE_DEFAULTS))
def test_each_live_default_name_is_refused_by_name_without_its_value(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    for value in (isolation.LIVE_DEFAULTS[name], ""):
        code, out, err = _run(_ci(**{name: value}), capsys)
        assert (code, out) == (2, ""), name
        assert name in err
        if value:
            assert value not in err, "a refusal never echoes the value"


def test_the_pinned_live_genie_space_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code, _out, err = _run(_ci(GENIE_SPACE_ID=LIVE_GENIE), capsys)

    assert code == 2 and "GENIE_SPACE_ID" in err and LIVE_GENIE not in err
    assert isolation.pinned_live_genie_space() == LIVE_GENIE


def test_an_operator_run_on_the_live_root_passes_and_a_non_default_root_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run({}, capsys) == (0, "bundle_root_leaf=dev\ninstall_suffix=\n", "")
    assert _run({"MIP_BUNDLE_ROOT_LEAF": "dev"}, capsys)[0] == 0
    code, out, err = _run({"MIP_BUNDLE_ROOT_LEAF": "dev-ci", **STAGING}, capsys)
    assert (code, out) == (2, "") and "CI-only" in err


@pytest.mark.parametrize("leaf", ["prod", "dev-CI", "dev-", "dev-ci/../x", "dev ci", "dev-" + "a" * 21])
def test_a_malformed_leaf_is_refused(leaf: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(_ci(MIP_BUNDLE_ROOT_LEAF=leaf), capsys)

    assert (code, out) == (2, "") and "MIP_BUNDLE_ROOT_LEAF must match" in err
    assert leaf not in err.split("must match", 1)[0]


def test_ci_pytest_and_other_workflows_keep_the_live_rule(capsys: pytest.CaptureFixture[str]) -> None:
    """C8: CI's own pytest inherits GITHUB_ACTIONS=true and must not be refused."""

    assert _run({"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": CI_REF}, capsys)[0] == 0
    assert _run({"GITHUB_ACTIONS": "true"}, capsys)[0] == 0


def test_the_staging_install_is_dev_target_only(capsys: pytest.CaptureFixture[str]) -> None:
    code, _out, err = _run(_ci(MIP_DEPLOY_TARGET="prod"), capsys)

    assert code == 2 and "dev-target only" in err


# ---------------------------------------------------- constants and the bundle


def _variable_default(name: str) -> str:
    variables = yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))["variables"]
    return str(variables[name]["default"])


def test_the_live_defaults_equal_the_bundle_and_workflow_fallbacks() -> None:
    assert isolation.LIVE_DEFAULTS["MIP_APP_NAME"] == _variable_default("app_name")
    assert isolation.LIVE_DEFAULTS["MIP_DEFAULT_CATALOG"] == _variable_default("uc_catalog")
    assert isolation.LIVE_DEFAULTS["MIP_LAKEBASE_INSTANCE"] == _variable_default("lakebase_instance_name")
    assert isolation.LIVE_DEFAULTS["MIP_LAKEBASE_SYNC_CATALOG"] == _variable_default("lakebase_catalog_name")
    assert isolation.LIVE_DEFAULTS["MIP_RUNTIME_SECRET_SCOPE"] == _variable_default("runtime_secret_scope")

    deploy_step = next(
        step for step in yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy dev Databricks App"
    )
    for name in ("MIP_APP_NAME", "MIP_DEFAULT_CATALOG", "MIP_LAKEBASE_INSTANCE", "MIP_LAKEBASE_SYNC_CATALOG",
                 "MIP_RUNTIME_SECRET_SCOPE", "MIP_APP_ROLLBACK_SECRET_SCOPE", "MIP_GENIE_SPACE_NAME"):
        fallback = re.fullmatch(rf"\$\{{\{{ vars\.{name} \|\| '([^']+)' \}}\}}", deploy_step["env"][name])
        assert fallback and fallback.group(1) == isolation.LIVE_DEFAULTS[name], name
    scripts = (ROOT / "scripts" / "lib" / "deploy_step_agentic_provisioning.sh").read_text(encoding="utf-8")
    for name in ("MIP_AI_GATEWAY_TABLE_PREFIX", "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE"):
        assert f"${{{name}:-{isolation.LIVE_DEFAULTS[name]}}}" in scripts, name


def test_the_default_leaf_renders_the_live_root_and_names_byte_for_byte() -> None:
    text = BUNDLE.read_text(encoding="utf-8")
    dev = re.search(r"(?ms)^  dev:\n(?P<body>.*?)(?=^  prod:\n)", text)
    assert dev and "      root_path: ~/.bundle/${bundle.name}/${var.bundle_root_leaf}\n" in dev.group("body")
    assert _variable_default("bundle_root_leaf") == "dev" and _variable_default("install_suffix") == ""

    user = "operator@summit.example"
    rendered = (
        "~/.bundle/${bundle.name}/${var.bundle_root_leaf}".replace("~", f"/Workspace/Users/{user}")
        .replace("${bundle.name}", "mortgage-intelligence-platform")
        .replace("${var.bundle_root_leaf}", "dev")
    ) + "/files"
    assert rendered == f"/Workspace/Users/{user}/.bundle/mortgage-intelligence-platform/dev/files"
    assert rendered == converge._expected_source_path(principal=user, target="dev"), "the live App source path"

    names = re.findall(r"^      name: (\S*\$\{var\.install_suffix\})$", text, re.MULTILINE)
    assert [name.replace("${var.install_suffix}", "") for name in names] == [
        "mip_serverless_sql",
        "mip_feature_pipeline",
        "/mip-lead-scoring",
    ], "an empty suffix renders the live names unchanged"


def test_the_workflows_address_the_dev_ci_install() -> None:
    deploy_step = next(
        step for step in yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy dev Databricks App"
    )
    assert deploy_step["env"]["MIP_BUNDLE_ROOT_LEAF"] == "dev-ci", "a literal, not a var"
    nightly_env = yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))["env"]
    assert nightly_env["BUNDLE_VAR_bundle_root_leaf"] == "dev-ci" != isolation.DEFAULT_LEAF
    assert nightly_env["BUNDLE_VAR_install_suffix"] == isolation.install_suffix("dev-ci") == "_ci"


# ----------------------------------------------------------- the shell block


def _block() -> str:
    text = PREFLIGHT.read_text(encoding="utf-8")
    return text[text.index("# >>> ci-install-isolation") : text.index("# <<< ci-install-isolation")]


def test_the_block_runs_before_the_prompt_the_exports_and_the_first_mutation() -> None:
    text = deploy_entrypoint_text()
    block = text.index("# >>> ci-install-isolation")

    assert block < text.index('read -r -p "About to DEPLOY')
    assert block < text.index('export BUNDLE_VAR_app_name="$MIP_APP_NAME"')
    assert block < text.index("run npm --prefix frontend ci --no-audit --no-fund")
    assert block < text.index("\nrun "), "before the first top-level run-wrapped mutation"
    code_lines = [line for line in _block().splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code_lines if "DRY_RUN" in line], "pure: it runs in DRY_RUN too"


def _bash_block(
    env: dict[str, str], python: str = sys.executable, dotenv: dict[str, str] | None = None, epilogue: str | None = None
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash
    # The real helper reads the shell, then .env.local; ``dotenv`` stands in for the file.
    arms = "".join(f"{name}) value={shlex.quote(value)} ;; " for name, value in (dotenv or {}).items())
    prelude = (
        "set -euo pipefail\nRED=''\nRST=''\nTARGET=\"${TARGET:-dev}\"\n"
        "deployment_control_value() { local name=\"$1\" value; value=\"${!name:-}\"; "
        f"if [[ -z \"$value\" ]]; then case \"$name\" in {arms}*) ;; esac; fi; printf '%s' \"$value\"; }}\n"
        f"PYTHON={python!r}\n"
    )
    epilogue = epilogue or '\nprintf "%s|%s|%s\\n" "$BUNDLE_VAR_bundle_root_leaf" "$BUNDLE_VAR_install_suffix" "$MIP_BUNDLE_ROOT_LEAF_RESOLVED"\n'
    return subprocess.run(
        [bash, "-c", prelude + _block() + epilogue], cwd=ROOT, env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True, text=True, check=False, timeout=60,
    )


def test_the_block_exports_the_staging_values_and_refuses_without_echoing() -> None:
    passed = _bash_block(_ci())
    assert passed.returncode == 0, passed.stderr
    assert passed.stdout.strip().splitlines()[-1] == "dev-ci|_ci|dev-ci"

    refused = _bash_block(_ci(MIP_LAKEBASE_INSTANCE="mip-app-state"))
    assert refused.returncode == 2
    assert "MIP_LAKEBASE_INSTANCE" in refused.stderr and "mip-app-state" not in refused.stderr


def test_the_ci_install_pins_the_gateway_names_it_checked_for_the_later_steps() -> None:
    """The later steps read these two from the shell only, falling back to the live defaults."""

    names = ("MIP_AI_GATEWAY_TABLE_PREFIX", "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE")
    env = _ci()
    checked = {name: env.pop(name) for name in names}
    epilogue = '\nprintf "%s|%s\\n" "$(printenv MIP_AI_GATEWAY_TABLE_PREFIX)" "$(printenv MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE)"\n'

    result = _bash_block(env, dotenv=checked, epilogue=epilogue)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "|".join(checked[name] for name in names), "exported as checked"

    operator = _bash_block({}, python="/usr/bin/false", dotenv=checked, epilogue=epilogue)
    assert operator.returncode == 0 and operator.stdout.strip() == "|", "an operator run exports nothing new"


def test_an_operator_run_never_starts_python_and_keeps_the_live_root() -> None:
    for env in ({}, {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": CI_REF}):
        result = _bash_block(env, python="/usr/bin/false")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "dev||dev"
    prod = _bash_block({"TARGET": "prod"}, python="/usr/bin/false")
    assert prod.stdout.strip() == "dev||prod", "a prod converge keeps --expected-root-leaf prod"


def test_any_leaf_or_the_deploy_dev_ref_always_runs_the_rule_table() -> None:
    """The Python-free path is only for 'no leaf and not deploy-dev.yml'."""

    local_leaf = _bash_block({"MIP_BUNDLE_ROOT_LEAF": "dev-ci", **STAGING})
    assert local_leaf.returncode == 2 and "CI-only" in local_leaf.stderr, "a leaf outside CI is refused"
    for env in ({"MIP_BUNDLE_ROOT_LEAF": "dev-ci"}, {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": DEPLOY_DEV_REF}):
        consulted = _bash_block(env, python="/usr/bin/false")
        assert consulted.returncode == 2, f"{env} must reach the tool, which fails closed here"
