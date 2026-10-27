"""The CI staging install is isolated from the live install (D-platform-process-b step 7, W5e).

release_gate_bootstrap_findings.md (binding): deploy-dev.yml must never upload
into, share state with, or fall back to the names of the live ``mip-app``
install. tools/databricks/ci_install_isolation.py is the fail-closed rule
table, the marked preflight block in scripts/lib/deploy_step_preflight_gates.sh
runs it before the confirmation prompt and any mutation, databricks.yml gives
the dev target a root leaf and an install suffix whose defaults render the live
install unchanged, and deploy-dev.yml / nightly.yml address the dev-ci install.
Post-W5: that install lives only on the forked-anchor ``pr105-host`` target,
whose own workspace host keeps it off the live install's workspace and
identities; deploy-dev.yml may deploy nowhere else.
"""

from __future__ import annotations

import itertools
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.fixtures.deploy_script import deploy_contract_text, deploy_entrypoint_text
from tools.databricks import ci_install_isolation as isolation
from tools.databricks import converge_static_app_source as converge

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "databricks.yml"
PREFLIGHT = ROOT / "scripts" / "lib" / "deploy_step_preflight_gates.sh"
DEPLOY_DEV = ROOT / ".github" / "workflows" / "deploy-dev.yml"
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"
DEPLOY_DEV_REF = "o/r/.github/workflows/deploy-dev.yml@refs/heads/main"
CI_REF = "o/r/.github/workflows/ci.yml@refs/heads/main"
LIVE_GENIE = "01f13d4968af1b249dc388fd5b18b195"
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


CI_TARGET = "pr105-host"


def _ci(**extra: str) -> dict[str, str]:
    """deploy-dev.yml's deploy step: its ref, its literal leaf, the CI target, staging names."""

    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_WORKFLOW_REF": DEPLOY_DEV_REF,
        "MIP_BUNDLE_ROOT_LEAF": "dev-ci",
        "MIP_DEPLOY_TARGET": CI_TARGET,
        "TARGET": CI_TARGET,
        **STAGING,
        **extra,
    }


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


@pytest.mark.parametrize("name", isolation.UC_CATALOG_NAMES)
@pytest.mark.parametrize(
    "value",
    ["mip", "mip_app_state", "MIP", "`Mip_App_State`", " `mip` "],
    ids=["live-catalog", "live-sync-catalog", "case", "backticked-case", "padded-backticked"],
)
def test_neither_ci_catalog_may_name_a_live_catalog_in_the_shared_metastore(
    name: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """pr105-host shares the live metastore: the catalog names are the primary UC isolation.

    Covers the crossed names (a CI sync catalog `mip`, a CI default catalog
    `mip_app_state`) and every spelling Unity Catalog resolves to a live catalog,
    none of which equals that variable's own live default byte for byte.
    """

    code, out, err = _run(_ci(**{name: value}), capsys)

    assert (code, out) == (2, "")
    assert err.startswith(f"[ci-install-isolation] refused: {name} ") and value.strip() not in err.split(name, 1)[1]


def test_the_staging_catalog_names_pass_the_shared_metastore_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert {isolation.LIVE_DEFAULTS[name] for name in isolation.UC_CATALOG_NAMES} == {"mip", "mip_app_state"}
    assert _run(_ci(MIP_DEFAULT_CATALOG="mip_ci", MIP_LAKEBASE_SYNC_CATALOG="mip_app_state_ci"), capsys)[0] == 0


def test_the_pinned_live_genie_space_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    code, _out, err = _run(_ci(GENIE_SPACE_ID=LIVE_GENIE), capsys)

    assert code == 2 and "GENIE_SPACE_ID" in err and LIVE_GENIE not in err
    assert isolation.pinned_live_genie_space() == LIVE_GENIE


@pytest.mark.parametrize(
    ("catalogs", "refused"),
    [
        ({"MIP_DEFAULT_CATALOG": "mip_pr105_staging", "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state_staging"}, None),
        ({}, "MIP_DEFAULT_CATALOG"),
        ({"MIP_DEFAULT_CATALOG": "mip_pr105_staging"}, "MIP_LAKEBASE_SYNC_CATALOG"),
        ({"MIP_DEFAULT_CATALOG": "mip", "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state_staging"}, "MIP_DEFAULT_CATALOG"),
        ({"MIP_DEFAULT_CATALOG": "`MIP`", "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state_staging"}, "MIP_DEFAULT_CATALOG"),
        ({"MIP_DEFAULT_CATALOG": "mip_pr105_staging", "MIP_LAKEBASE_SYNC_CATALOG": "Mip_App_State"}, "MIP_LAKEBASE_SYNC_CATALOG"),
        ({"MIP_DEFAULT_CATALOG": "mip_app_state", "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state_staging"}, "MIP_DEFAULT_CATALOG"),
        ({"MIP_DEFAULT_CATALOG": " ", "MIP_LAKEBASE_SYNC_CATALOG": "mip_app_state_staging"}, "MIP_DEFAULT_CATALOG"),
    ],
    ids=["staging", "unset", "sync-unset", "live", "live-quoted-upper", "live-sync-mixed-case", "crossed", "blank"],
)
def test_the_uc_catalog_check_runs_alone_for_nightly(
    catalogs: dict[str, str], refused: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    """nightly.yml's first job: the UC row of the rule table, with no leaf, target or CI ref needed."""

    code = isolation.main(catalogs, ["uc-catalogs"])
    captured = capsys.readouterr()
    if refused is None:
        assert (code, captured.out) == (0, "") and "uc-catalogs: ok" in captured.err
    else:
        assert code == 2 and refused in captured.err and captured.out == ""
        for value in catalogs.values():
            assert not value.strip() or value not in captured.err, "names the variable, never the value"
    assert isolation.main(catalogs, ["no-such-check"]) == 2
    assert isolation.main(catalogs, ["uc-catalogs", "extra"]) == 2
    capsys.readouterr()
    doc = _flat(_doc_section(BOOTSTRAP_HEADING))
    assert "Its first job, `ci-install-isolation`, runs the rule table's UC-catalog row" in doc
    assert "and every other job needs it" in doc


_REPAIR_HOSTS = {"dev": "https://live.example", CI_TARGET: "https://staging.example"}


def _repair(**extra: str) -> dict[str, str]:
    """deploy-dev.yml's credential-repair step: its ref, the staging host and the staging App."""

    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_WORKFLOW_REF": DEPLOY_DEV_REF,
        "DATABRICKS_HOST": "https://staging.example",
        "MIP_APP_NAME": STAGING["MIP_APP_NAME"],
        **extra,
    }


@pytest.mark.parametrize(
    "host", ["https://staging.example", "https://STAGING.example/", "staging.example", "https://staging.example.:443"]
)
def test_the_credential_repair_runs_only_on_the_staging_workspace_host(host: str) -> None:
    isolation.check_credential_repair(_repair(DATABRICKS_HOST=host), hosts=_REPAIR_HOSTS)


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"DATABRICKS_HOST": "https://live.example"}, "DATABRICKS_HOST"),
        ({"DATABRICKS_HOST": "https://live.example.:443/"}, "DATABRICKS_HOST"),
        ({"DATABRICKS_HOST": ""}, "DATABRICKS_HOST"),
        ({"DATABRICKS_HOST": "https://[staging.example"}, "DATABRICKS_HOST"),
        ({"MIP_APP_NAME": "mip-app"}, "MIP_APP_NAME"),
        ({"MIP_APP_NAME": " "}, "MIP_APP_NAME"),
        ({"GITHUB_WORKFLOW_REF": CI_REF}, "deploy-dev.yml"),
        ({"GITHUB_ACTIONS": ""}, "deploy-dev.yml"),
    ],
    ids=["live-host", "live-host-spelling", "no-host", "unparseable-host", "live-app", "blank-app", "ci-ref", "not-actions"],
)
def test_the_credential_repair_refuses_the_live_workspace_and_app(override: dict[str, str], reason: str) -> None:
    """deploy-dev.yml's repair rotates an OAuth secret before, and outside, deploy.sh's preflight."""

    with pytest.raises(isolation.Refused, match=reason):
        isolation.check_credential_repair(_repair(**override), hosts=_REPAIR_HOSTS)
    with pytest.raises(isolation.Refused, match="must anchor its own workspace host"):
        isolation.check_credential_repair(_repair(), hosts={"dev": "https://live.example", CI_TARGET: "https://live.example."})


def test_the_credential_repair_gate_is_the_workflow_steps_first_command() -> None:
    workflow = yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))
    repair = next(step for step in workflow["jobs"]["deploy"]["steps"] if step.get("name") == "Repair normal operator OAuth credential")
    commands = [line for line in repair["run"].splitlines() if line.strip() and not line.lstrip().startswith("#")]

    assert commands[:2] == ["set -euo pipefail", "python -m tools.databricks.ci_install_isolation repair-credential"]
    assert repair["env"]["MIP_APP_NAME"] == "${{ vars.MIP_APP_NAME }}", "no live mip-app fallback"
    assert "repair-credential" in isolation.CHECKS
    doc = _flat(_doc_section(BOOTSTRAP_HEADING))
    assert "first runs `python -m tools.databricks.ci_install_isolation repair-credential`" in doc


@pytest.mark.parametrize("flag", ["1", "true", " 1 "])
def test_foreign_catalog_remediation_is_refused_on_the_forked_anchor_target(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 2 (post-W5 review): remediation rewrites bindings metastore-wide, and pr105-host shares the metastore.

    Dispatched from deploy-dev.yml it would flip catalogs to ISOLATED and then
    re-bind every other metastore workspace, after signing its own manifest in
    the same run. Between those two calls (or after a killed run, which
    recovery never reopens) every other workspace on the shared metastore
    loses those catalogs, the live install's share catalogs included. The
    rule table refuses it before any mutation.
    """

    code, out, err = _run(_ci(MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS=flag), capsys)

    assert (code, out) == (2, "") and isolation.REMEDIATION_FLAG in err
    assert _run(_ci(MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS="0"), capsys)[0] == 0, "the workflow's off value passes"
    assert _run({"MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS": "1"}, capsys)[0] == 0, "an operator dev run is unchanged"
    owner = _doc_section(BOOTSTRAP_HEADING).split("**Owner steps", 1)[1].split("**Integrator steps", 1)[0]
    step8 = _flat(_numbered_item(owner, 8))
    assert "The rule table refuses it on `pr105-host` (exit 2, before any mutation)." in step8
    # Round 4 (post-W5 review B1): the signed manifest re-binds every other
    # metastore workspace, so the hazard is the window between the isolate and
    # re-bind calls, never a policy that leaves a workspace unbound.
    assert (
        "and between its isolate and re-bind calls (or after a killed run) every other workspace loses those "
        "catalogs, the live install's share catalogs included."
    ) in step8
    assert "unbinding every workspace the policy omits" not in _flat(DEPLOYMENT_DOC.read_text(encoding="utf-8"))
    workflow = yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))
    dispatch = workflow[True]["workflow_dispatch"]["inputs"]["remediate_foreign_catalog_bindings"]
    assert dispatch["default"] is False and dispatch["description"].startswith(f"Refused on {CI_TARGET}")


def test_the_shell_block_passes_the_remediation_flag_to_the_rule_table() -> None:
    """The preflight block's Python inherits the workflow's step env, so the refusal fires in deploy.sh too."""

    refused = _bash_block(_ci(MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS="1"))
    assert refused.returncode == 2 and isolation.REMEDIATION_FLAG in refused.stderr
    assert _bash_block(_ci(MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS="0")).returncode == 0


@pytest.mark.parametrize(
    "family", ["mip.audit.mortgage_growth_supervisor_proxy", "`MIP`.audit.proxy_family", "other_catalog.audit.proxy_family"]
)
def test_a_gateway_model_family_outside_the_staging_catalog_is_refused(family: str, capsys: pytest.CaptureFixture[str]) -> None:
    """An explicit live family would register and archive Gateway models in the live install's catalog."""

    code, out, err = _run(_ci(MIP_AI_GATEWAY_AGENT_MODEL_FAMILY=family), capsys)

    assert (code, out) == (2, "")
    assert "MIP_AI_GATEWAY_AGENT_MODEL_FAMILY" in err and family not in err


@pytest.mark.parametrize("family", ["", "mip_pr105_staging.audit.proxy_family", "`MIP_PR105_STAGING`.audit.proxy_family"])
def test_an_unset_or_staging_catalog_model_family_passes(family: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(_ci(MIP_AI_GATEWAY_AGENT_MODEL_FAMILY=family), capsys)[0] == 0
    operator = {"MIP_AI_GATEWAY_AGENT_MODEL_FAMILY": "mip.audit.mortgage_growth_supervisor_proxy"}
    assert _run(operator, capsys)[0] == 0, "an operator run is unchanged"


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


@pytest.mark.parametrize("target", ["dev", "prod", "", "ci"])
def test_deploy_dev_deploys_only_to_the_forked_anchor_target(target: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Post-W5: never the anchor workspace, whatever the leaf and names."""

    for env in (_ci(MIP_DEPLOY_TARGET=target), {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": DEPLOY_DEV_REF, "MIP_DEPLOY_TARGET": target}):
        code, out, err = _run(env, capsys)
        assert (code, out) == (2, ""), env
        assert f"use -t {CI_TARGET}" in err
    assert isolation.CI_TARGET == CI_TARGET


@pytest.mark.parametrize(
    "env",
    [
        {"MIP_DEPLOY_TARGET": CI_TARGET},
        {"MIP_DEPLOY_TARGET": CI_TARGET, "MIP_BUNDLE_ROOT_LEAF": "dev-ci", **STAGING},
        {"MIP_DEPLOY_TARGET": CI_TARGET, "GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": CI_REF},
        {"MIP_DEPLOY_TARGET": CI_TARGET, "GITHUB_ACTIONS": "true", "MIP_BUNDLE_ROOT_LEAF": "dev-ci", **STAGING},
    ],
    ids=["operator", "operator-with-leaf", "other-workflow", "actions-without-the-deploy-dev-ref"],
)
def test_the_forked_anchor_target_is_deploy_dev_only(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(env, capsys)

    assert (code, out) == (2, "")
    assert "deploy-dev.yml only" in err


def test_a_non_default_leaf_on_the_dev_target_is_refused_in_any_workflow(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run({"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": CI_REF, "MIP_BUNDLE_ROOT_LEAF": "dev-ci", **STAGING}, capsys)

    assert (code, out) == (2, "")
    assert f"{CI_TARGET}-target only" in err


@pytest.mark.parametrize(
    "hosts",
    [
        {"dev": "https://live.example", CI_TARGET: "https://live.example"},
        {"dev": "https://live.example", CI_TARGET: ""},
        {"dev": "https://live.example"},
    ],
    ids=["same-host-as-dev", "empty-host", "no-ci-target"],
)
def test_the_forked_anchor_target_must_name_its_own_workspace_host(hosts: dict[str, str]) -> None:
    with pytest.raises(isolation.Refused, match="must anchor its own workspace host"):
        isolation.resolve(_ci(), hosts=hosts)
    assert isolation.resolve(_ci(), hosts={"dev": "https://live.example", CI_TARGET: "https://staging.example"}) == ("dev-ci", "_ci")


_HOST_BUNDLE = """\
workspace:
  host: &default_host https://live.example
targets:
  dev:
    workspace:
      host: *default_host
  {ci}:
    workspace:
      host: {host}
"""


@pytest.mark.parametrize(
    "variant",
    ["HTTPS://LIVE.EXAMPLE", "https://live.example/", "HTTPS://LIVE.EXAMPLE/", "'  https://live.example//  '"],
    ids=["case", "trailing-slash", "case-and-slash", "padded-double-slash"],
)
def test_a_spelling_variant_of_the_dev_host_is_still_the_dev_host(variant: str, tmp_path: Path) -> None:
    """target_hosts() normalizes what databricks.yml spells, so a variant cannot pass as another workspace."""

    bundle = tmp_path / "databricks.yml"
    bundle.write_text(_HOST_BUNDLE.format(ci=CI_TARGET, host=variant), encoding="utf-8")

    hosts = isolation.target_hosts(bundle)
    assert hosts == {"dev": "https://live.example", CI_TARGET: "https://live.example"}
    with pytest.raises(isolation.Refused, match="must anchor its own workspace host"):
        isolation.resolve(_ci(), hosts=hosts)

    bundle.write_text(_HOST_BUNDLE.format(ci=CI_TARGET, host="https://staging.example/"), encoding="utf-8")
    assert isolation.resolve(_ci(), hosts=isolation.target_hosts(bundle)) == ("dev-ci", "_ci"), "a real second host passes"


@pytest.mark.parametrize(
    "variant",
    [
        "https://live.example:443",
        "http://live.example",
        "https://live.example/?o=2543889327043640",
        "https://live.example/some/path",
        "live.example",
        "https://user@LIVE.example:8443/",
        "https://live.example.",
        "https://live.example./",
        "https://LIVE.example.:443",
        "live.example.",
        "https://live.example..",
    ],
    ids=[
        "explicit-port",
        "http-scheme",
        "query-string",
        "path",
        "no-scheme",
        "userinfo-and-port",
        "trailing-dot",
        "trailing-dot-slash",
        "trailing-dot-uppercase-port",
        "trailing-dot-no-scheme",
        "double-trailing-dot",
    ],
)
def test_a_url_spelling_of_the_dev_host_is_still_the_dev_host(variant: str, tmp_path: Path) -> None:
    """Round 2 (post-W5 review): the rule table compares hostnames, not URL strings.

    A scheme, port, path, query, userinfo or trailing dot never makes a second workspace. An
    alias DNS name for the live workspace is out of a static check's reach;
    owner step 8's `metastores current` workspace id is that proof.
    """

    bundle = tmp_path / "databricks.yml"
    bundle.write_text(_HOST_BUNDLE.format(ci=CI_TARGET, host=variant), encoding="utf-8")

    with pytest.raises(isolation.Refused, match="must anchor its own workspace host"):
        isolation.resolve(_ci(), hosts=isolation.target_hosts(bundle))


@pytest.mark.parametrize(
    "hosts",
    [
        {"dev": "https://[live.example", CI_TARGET: "https://staging.example"},
        {"dev": "https://live.example", CI_TARGET: "https://[staging.example"},
        {"dev": "", CI_TARGET: "https://staging.example"},
        {CI_TARGET: "https://staging.example"},
        {"dev": "https://live.example", CI_TARGET: "https://."},
    ],
    ids=["unparseable-dev", "unparseable-ci", "empty-dev", "no-dev-target", "dot-only-ci"],
)
def test_a_host_that_names_no_workspace_is_refused(hosts: dict[str, str]) -> None:
    """Fail closed: an unparseable or missing host on either side is never 'a different workspace'."""

    with pytest.raises(isolation.Refused, match="must anchor its own workspace host"):
        isolation.resolve(_ci(), hosts=hosts)


def test_the_bundle_gives_the_forked_anchor_target_its_own_host_and_keeps_the_rest_anchored() -> None:
    text = BUNDLE.read_text(encoding="utf-8")
    hosts = isolation.target_hosts()
    data = yaml.safe_load(text)

    assert hosts[CI_TARGET] and hosts[CI_TARGET] != hosts["dev"], "never the live install's workspace"
    assert {hosts[name] for name in ("dev", "prod", "ci")} == {data["workspace"]["host"].rstrip("/").casefold()}
    targets = text.split("\ntargets:\n", 1)[1].split("\nresources:\n", 1)[0]
    assert targets.count("      host: *default_host\n") == 3, "dev, prod and ci keep the single customer-fork anchor"
    ci_literal = data["targets"][CI_TARGET]["workspace"]["host"]
    assert text.count(ci_literal) == 1 and f"host: &ci_staging_host {ci_literal}\n" in text, "one literal, on its own anchor"
    ci_target = data["targets"][CI_TARGET]
    assert ci_target["mode"] == "development" and "profile" not in ci_target["workspace"]
    assert ci_target["workspace"]["root_path"] == "~/.bundle/${bundle.name}/${var.bundle_root_leaf}"
    assert "genie_space_id" not in (ci_target.get("variables") or {}), "resolved by governed name, never pinned"
    assert isolation.pinned_live_genie_space() == LIVE_GENIE, "the dev pin is still found before the new target"


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
        "/Shared/mip-lead-scoring",
    ], "an empty suffix renders the live names unchanged"


def test_the_workflows_address_the_dev_ci_install() -> None:
    deploy_step = next(
        step for step in yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy dev Databricks App"
    )
    assert deploy_step["env"]["MIP_BUNDLE_ROOT_LEAF"] == "dev-ci", "a literal, not a var"
    assert f"args=(-t {CI_TARGET} --no-confirm)" in deploy_step["run"]
    assert './scripts/deploy.sh "${args[@]}"' in deploy_step["run"]


def test_the_workflows_never_address_the_anchor_workspace_targets() -> None:
    """deploy-dev.yml deploys, and nightly.yml validates and runs jobs, on pr105-host only."""

    deploy_dev = DEPLOY_DEV.read_text(encoding="utf-8")
    nightly = NIGHTLY.read_text(encoding="utf-8")
    anchor = re.compile(r"(?:-t|--target)[ =](?:dev|prod|ci)\b")
    assert not anchor.search(deploy_dev) and not anchor.search(nightly)
    bundle_commands = re.findall(r"databricks bundle (?:validate|run [a-z_]+) -t (\S+)", nightly)
    assert len(bundle_commands) == 4 and set(bundle_commands) == {CI_TARGET}
    assert deploy_dev.count(f"-t {CI_TARGET}") == 1
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


def _target_class_lines() -> str:
    """deploy.sh's own TARGET_CLASS assignment, run verbatim in the block's prelude."""

    text = deploy_entrypoint_text()
    start = text.index('TARGET_CLASS="$TARGET"\n')
    return text[start : text.index("\nfi\n", start) + 4]


def _bash_block(
    env: dict[str, str], python: str = sys.executable, dotenv: dict[str, str] | None = None, epilogue: str | None = None
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash
    # The real helper reads the shell, then .env.local; ``dotenv`` stands in for the file.
    arms = "".join(f"{name}) value={shlex.quote(value)} ;; " for name, value in (dotenv or {}).items())
    prelude = (
        "set -euo pipefail\nRED=''\nRST=''\nTARGET=\"${TARGET:-dev}\"\n" + _target_class_lines() +
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


def test_the_block_checks_the_gateway_model_family_from_the_shell_and_from_env_local() -> None:
    live_family = "mip.audit.mortgage_growth_supervisor_proxy"
    for result in (
        _bash_block(_ci(MIP_AI_GATEWAY_AGENT_MODEL_FAMILY=live_family)),
        _bash_block(_ci(), dotenv={"MIP_AI_GATEWAY_AGENT_MODEL_FAMILY": live_family}),
    ):
        assert result.returncode == 2
        assert "MIP_AI_GATEWAY_AGENT_MODEL_FAMILY" in result.stderr and live_family not in result.stderr
    staging = _bash_block(_ci(MIP_AI_GATEWAY_AGENT_MODEL_FAMILY="mip_pr105_staging.audit.proxy_family"))
    assert staging.returncode == 0, staging.stderr


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


def test_any_leaf_the_deploy_dev_ref_or_the_forked_anchor_target_always_runs_the_rule_table() -> None:
    """The Python-free path is only for 'no leaf, not pr105-host and not deploy-dev.yml'."""

    local_leaf = _bash_block({"MIP_BUNDLE_ROOT_LEAF": "dev-ci", **STAGING})
    assert local_leaf.returncode == 2 and "CI-only" in local_leaf.stderr, "a leaf outside CI is refused"
    local_ci_target = _bash_block({"TARGET": CI_TARGET})
    assert local_ci_target.returncode == 2 and "deploy-dev.yml only" in local_ci_target.stderr, "an operator never reaches pr105-host"
    for env in (
        {"MIP_BUNDLE_ROOT_LEAF": "dev-ci"},
        {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": DEPLOY_DEV_REF},
        {"TARGET": CI_TARGET},
    ):
        consulted = _bash_block(env, python="/usr/bin/false")
        assert consulted.returncode == 2, f"{env} must reach the tool, which fails closed here"


# The weekly scheduled refresh keys on the literal dev target ON PURPOSE: only
# the live install (target dev, empty install_suffix) may unpause
# mip_scheduled_refresh or ask the postflight for its deployer recipient, the
# same rule as tools/databricks/scheduled_refresh_posture.is_live_install. A
# TARGET_CLASS there would treat pr105-host as the live install. These two
# blocks, and exactly these lines in them, are the only literal-target
# decisions allowed after the allowlist; tests/unit/test_refresh_schedule_contract.py
# proves pr105-host stays PAUSED with no recipients.
LIVE_INSTALL_ONLY_BLOCKS = (
    ("# >>> scheduled-refresh", "# <<< scheduled-refresh"),
    ("# Step 9c: weekly scheduled refresh postflight", "unset _SCHEDULED_REFRESH_ARGS\n"),
)
LIVE_INSTALL_ONLY_LINES = (
    'if [[ "$TARGET" == "dev" ]]; then',
    'if [[ "$TARGET" != "dev" && "$_SCHEDULED_REFRESH" == "on" ]]; then',
    'if [[ "$TARGET" == "dev" && "$_SCHEDULED_REFRESH" == "on" && -z "$BUNDLE_VAR_install_suffix" ]]; then',
    'if [[ "$TARGET" == "dev" && -z "${BUNDLE_VAR_install_suffix:-}" ]]; then',
)


def _literal_target_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if '"$TARGET" == "dev"' in line or '"$TARGET" != "dev"' in line
    ]


def test_every_dev_class_decision_reads_the_target_class() -> None:
    """The sandbox runtime, the demo-feed default/refusal and the root leaf treat pr105-host as dev.

    Read across every sourced file (deploy_contract_text expands the reviewed
    libraries too), so a later `"$TARGET" == "dev"` in any of them is caught,
    outside the two live-install-only scheduled-refresh blocks above.
    """

    full = deploy_contract_text()
    text = full[full.index('TARGET_CLASS="$TARGET"\n') :]  # after the target allowlist
    exempt: list[str] = []
    for start, end in LIVE_INSTALL_ONLY_BLOCKS:
        assert text.count(start) == 1, start
        begin = text.index(start)
        finish = text.index(end, begin) + len(end)
        exempt.extend(_literal_target_lines(text[begin:finish]))
        text = text[:begin] + text[finish:]
    assert sorted(exempt) == sorted(LIVE_INSTALL_ONLY_LINES), "the exemption covers exactly the live-install lines"
    assert _literal_target_lines(text) == []
    assert text.count('"$TARGET_CLASS" == "dev"') == 3 and text.count('"$TARGET_CLASS" != "dev"') == 1
    assert '  if [[ "$TARGET_CLASS" == "dev" ]]; then\n    APP_RUNTIME_ENV="sandbox"\n' in text
    assert '  if [[ "$TARGET_CLASS" == "dev" ]]; then\n    DEMO_FEEDS_FROM_ENV=1\n' in text
    assert 'if [[ "$TARGET_CLASS" == "dev" ]]; then\n  MIP_BUNDLE_ROOT_LEAF_RESOLVED="$BUNDLE_VAR_bundle_root_leaf"\n' in text


def test_the_deploy_dev_block_refuses_the_dev_target() -> None:
    refused = _bash_block(_ci(TARGET="dev"))

    assert refused.returncode == 2 and f"use -t {CI_TARGET}" in refused.stderr


# ------------------------------------------ the bootstrap runbook (docs/deployment.md)

DEPLOYMENT_DOC = ROOT / "docs" / "deployment.md"
BOOTSTRAP_HEADING = "## CI staging install (deploy-dev.yml) and the release-gate bootstrap"
IDENTITIES_HEADING = "### Per-run M2M identities"


def _doc_section(heading: str) -> str:
    """The section's lines up to the next heading of its level or higher, outside code fences."""

    lines = DEPLOYMENT_DOC.read_text(encoding="utf-8").splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    body: list[str] = []
    fenced = False
    for line in lines[lines.index(heading) + 1 :]:
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(rf"#{{1,{level}}} ", line):
            break
        body.append(line)
    return "\n".join(body)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _numbered_item(block: str, number: int) -> str:
    return block.split(f"\n{number}. ", 1)[1].split(f"\n{number + 1}. ", 1)[0]


def test_the_runbook_states_the_reserved_identity_contract_and_the_forked_anchor_resolution() -> None:
    """C7(b), round 2: CI-only identities cannot coexist with the reserved names in one workspace.

    Post-W5: the bootstrap is no longer deferred. The forked-anchor target is
    built, the owner steps name its workspace host exactly as databricks.yml
    does, and the dispatch step says the workflow can only deploy there.
    """

    section = _doc_section(BOOTSTRAP_HEADING)
    owner = section.split("**Owner steps", 1)[1].split("**Integrator steps", 1)[0]
    integrator = section.split("**Integrator steps", 1)[1]
    step1 = _flat(_numbered_item(owner, 1))

    for anchor in (
        "seven names in \"Per-run M2M identities\"",
        "`tools/databricks/m2m_identity_contract.py` `IDENTITY_DEFAULTS`",
        "`provision_m2m_oauth._find_existing_sp`",
        "`agent_runtime_access.AGENT_RUNTIME_DISPLAY_NAME`",
        "CI-only identities cannot coexist with them there",
        "**Never create a second service principal with a reserved display name.**",
        "(`Multiple service principals use reserved display name`)",
        "Resolution (built): the forked-anchor (`pr105-host`) target",
        "chosen over a change that makes the reserved names install-scoped",
        "The CI install's identities are the seven reserved principals of the `pr105-host` workspace",
    ):
        assert anchor in step1, anchor
    status = _flat(section.split("**Why the CI install", 1)[0])
    assert "**Status: the forked-anchor `pr105-host` target is built" in status
    assert "blocked in the anchor workspace" in status, "the W5e history stays stated"
    dispatch = _flat(_numbered_item(owner, 10))
    assert f"can only deploy `-t {CI_TARGET}`, so it never runs against the anchor workspace" in dispatch
    assert "acknowledge_single_maintainer_break_glass=true" in dispatch
    assert "\n\nSteps 4-6 run only on the forked-anchor target" in integrator.split("\n4. ", 1)[0], "its own paragraph"
    assert f"`{CI_TARGET}`" in section and "exact remaining steps" in _flat(owner)


def test_the_owner_steps_name_the_bundle_host_and_pin_exactly() -> None:
    """The doc cannot drift from databricks.yml: the staging host and the live Genie pin."""

    section = _doc_section(BOOTSTRAP_HEADING)
    owner = section.split("**Owner steps", 1)[1].split("**Integrator steps", 1)[0]
    host = yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))["targets"][CI_TARGET]["workspace"]["host"]

    assert f"must equal `{host}`" in _flat(_numbered_item(owner, 3))
    assert f"`DATABRICKS_HOST` (the `{CI_TARGET}` host)" in _flat(_numbered_item(owner, 6))
    assert f"pin `{isolation.pinned_live_genie_space()}`" in _flat(_numbered_item(owner, 5))
    assert "remediate_foreign_catalog_bindings=true" in _flat(_numbered_item(owner, 8))
    assert [number for number in range(1, 12) if f"\n{number}. " in owner] == list(range(1, 11)), "ten owner steps"

    flat = _flat(section)
    assert "Give the CI install its own identities" not in flat
    assert "CI-only service principal" not in flat
    assert "(CI-only identities, confirmed)" not in flat
    assert not re.search(r"(?i)\b(?:give|create|assign|use)\b[^.]{0,80}\bCI-only\b", flat), "no instruction to make CI-only identities"
    for row in ("`reconcile_historical_agent_endpoints cleanup`", "`agent_runtime_access.audit_global_genie_access`",
                "`audit_agent_runtime_foreign_uc_access`"):
        assert row in section, f"workspace-global inventory row {row}"


GRANTS_DOC = ROOT / "docs" / "security" / "GRANTS.md"
RUNBOOK = ROOT / "docs" / "runbook.md"


def test_the_runbook_never_presents_the_forked_anchor_as_a_separate_metastore_or_account() -> None:
    """Round 1 (post-W5 review): pr105-host shares the live metastore and account.

    Read-only paychex reads (2026-10-27) put `mip_pr105_staging`, another team's
    catalog and the OPEN live catalogs on the live metastore. So catalog-name
    isolation is primary, step 8's UC changes are metastore-wide, and identity
    separation is an owner invariant, not a structural guarantee. The old
    overclaims stay out of the bootstrap section and of the runbook's recovery
    section (round 2: the live App has no governed recovery path today).
    """

    section = _doc_section(BOOTSTRAP_HEADING)
    flat = _flat(section) + " " + _flat(_runbook_recovery())
    owner = section.split("**Owner steps", 1)[1].split("**Integrator steps", 1)[0]
    step1, step2, step8 = (_flat(_numbered_item(owner, number)) for number in (1, 2, 8))

    assert "Shared: the live workspace's Unity Catalog metastore and the Databricks account" in flat
    assert "primary Unity Catalog isolation, never defence in depth, and must never be relaxed" in flat
    for name in isolation.UC_CATALOG_NAMES:
        assert f"`{name}`" in flat.split("primary Unity Catalog isolation", 1)[1].split("**Isolation rules", 1)[0]
    assert "**Owner invariant: each workspace keeps its own reserved principals.**" in step1
    assert "no code checks it" in step1 and "`2543889327043640`" in step1
    assert "name neither live catalog (`mip`, `mip_app_state`)" in step2
    assert "must return the live workspace's `metastore_id`" in step8
    assert "metastore-wide" in step8 and "the live install's share catalogs included" in step8
    assert "the Unity Catalog changes in step 8 are metastore-wide" in _flat(owner.split("\n1. ", 1)[0])
    assert "waits on the owner steps below, then integrator steps 1-6" in flat
    assert "render in integrator step 2 has never run" in flat

    for overclaim in (
        "Identities follow the host",
        "never the live install's",
        "read its own catalogs",
        "stay as defence in depth",
        "never against the live workspace",
        "run as the live install's own seven",
        "mutable only from",
        "recovers through an operator",
        "bootstrap mints",
        "integrator steps 4-6",
    ):
        assert overclaim not in flat, overclaim


def _runbook_recovery() -> str:
    """docs/runbook.md section 1.2's stopped-App recovery, up to section 1.3."""

    runbook = RUNBOOK.read_text(encoding="utf-8")
    start = runbook.index("If the Lakebase instance or App is stopped")
    return runbook[start : runbook.index("### 1.3", start)]


def test_the_step8_blockers_are_counted_by_privilege_class_never_named() -> None:
    """Round 2 (post-W5 review B3): no third-party person or other team's catalog in the product docs.

    The step names the two OPEN blockers by count and privilege class and hands
    the owner a read-only inventory that finds them, and their owners, at run
    time. Every backticked lower-case token in the step is a platform, product
    or API name from the allowlist, so a catalog or a user handle cannot creep
    back in.
    """

    owner = _doc_section(BOOTSTRAP_HEADING).split("**Owner steps", 1)[1].split("**Integrator steps", 1)[0]
    raw = _numbered_item(owner, 8)
    step8 = _flat(raw)

    assert "the two known blockers, two `OPEN` catalogs owned by other teams" in step8
    assert "grant `account users` `ALL_PRIVILEGES` and `BROWSE` respectively" in step8
    for probe in (
        "databricks catalogs list -o json",
        'select(.isolation_mode == "OPEN")',
        'databricks grants get catalog "$catalog" -o json',
        'select(.principal == "account users")',
    ):
        assert probe in raw, probe
    assert "on a `pr105-host` credential" in step8
    assert not re.search(r"\bowner [a-z]+\.[a-z]+", step8), "no user handle"
    allowed = {"samples", "system", "users", "install_scope", "metastore_id", "workspace_id"}
    tokens = set(re.findall(r"`([a-z_]+)`", raw))
    assert tokens <= allowed, sorted(tokens - allowed)


# An operator deploy of the live install, however it is spelled in prose.
_OPERATOR_LIVE_DEPLOY = r"`?(?:\./)?scripts/deploy\.sh`? -t dev`?"
# A recovery verb that reaches the operator deploy, or an un-negated dispatch for the live App, in one sentence.
_LIVE_RECOVERY_OVERCLAIMS = (
    re.compile(r"(?i)\b(?:recover|restor|restart|redeploy|repair)\w*\b[^.]{0,80}?(?:" + _OPERATOR_LIVE_DEPLOY + ")"),
    re.compile(r"(?i)(?<!not )\bdispatch\b (?:the )?(?:workflow|`?deploy-dev(?:\.yml)?`?)[^.]{0,60}?\bfor the live\b"),
    re.compile(r"(?i)\brecovers? through an operator\b"),
)


def assert_no_live_recovery_overclaim(text: str) -> None:
    """No sentence presents an operator deploy or a dispatch as the live App's recovery."""

    for pattern in _LIVE_RECOVERY_OVERCLAIMS:
        match = pattern.search(text)
        assert match is None, match.group(0) if match else ""


@pytest.mark.parametrize(
    "overclaim",
    [
        "so it can never deploy or recover the live dev App, which an operator recovers with `./scripts/deploy.sh -t dev`.",
        "Restart it with `./scripts/deploy.sh -t dev`.",
        "Redeploy the live App: run ./scripts/deploy.sh -t dev now.",
        "Do not start that deploy during an incident, and dispatch the workflow below for the live App.",
        "Dispatch deploy-dev.yml for the live App.",
        "The live App recovers through an operator.",
    ],
)
def test_the_live_recovery_overclaim_ban_catches_paraphrases(overclaim: str) -> None:
    with pytest.raises(AssertionError):
        assert_no_live_recovery_overclaim(overclaim)


def test_the_live_app_has_no_governed_recovery_path_and_escalates_to_the_owner() -> None:
    """Round 2 (post-W5 review B2, B1): the docs never present a NO-GO path as the live App's recovery.

    deploy-dev.yml recovers only the CI staging install. The live App's command
    of record, an operator ``./scripts/deploy.sh -t dev``, is NO-GO today (no
    reserved principals in the live workspace, deploy.sh never creates one, the
    governed creation tool's only sink is this repository's secrets), so the
    runbook escalates to the owner instead of starting it.
    """

    section = _doc_section(BOOTSTRAP_HEADING)
    flat = _flat(section)
    owner = section.split("**Owner steps", 1)[1].split("**Integrator steps", 1)[0]
    step1 = _flat(_numbered_item(owner, 1))
    recovery = _flat(_runbook_recovery())

    live = flat.split("**The live install is outside this bootstrap, and its governed deploy is NO-GO.**", 1)[1]
    live = live.split("**Why the CI install", 1)[0]
    assert "`scripts/deploy.sh` never creates one" in live and "`--expected-application-id`" in live
    assert "Deploying or recovering the live App is therefore an owner-only decision." in live
    assert "Never start `./scripts/deploy.sh -t dev` against the live workspace during an incident" in live

    assert "`scripts/deploy.sh` never creates a reserved principal" in step1
    assert "The only path that mints a usable credential, for a new principal or a rotation, is `--set-gh-secrets`" in step1
    assert "The only path that mints a usable credential cannot serve the live install" in live
    for loose in ("never creates a principal", "only governed creation path"):
        assert loose not in flat, loose
    for sink in (
        "`provision_m2m_oauth --pre-app-bootstrap --set-gh-secrets`",
        "repository-level Actions secrets (`gh secret set --repo`)",
        "`MIP_M2M_GITHUB_REPOSITORY`",
        "in this repository they belong to the CI install",
        "It stays out of scope until that design is reviewed.",
        "Compare the two listings, never a stored `*_CLIENT_ID`",
    ):
        assert sink in step1, sink

    whole = _flat(DEPLOYMENT_DOC.read_text(encoding="utf-8"))
    assert "`./scripts/deploy.sh -t dev` or dispatch `deploy-dev.yml`" not in whole, "not equivalent first installs"
    assert "dispatch `deploy-dev.yml` for the CI staging install, which deploys only the `pr105-host` target" in whole

    assert recovery.index("**The live dev App**") < recovery.index("**The CI staging install**")
    assert recovery.index("**The CI staging install**") < recovery.index("gh workflow run deploy-dev.yml")
    for anchor in (
        "No governed path recovers it today.",
        "is NO-GO on the live workspace",
        "\"The live install is outside this bootstrap, and its governed deploy is NO-GO\"",
        "Do not start that deploy during an incident",
        "Escalate to the owner",
        "is an owner-only decision",
        "so it can never deploy or recover the live dev App",
        "Do not start that deploy during an incident, and do not dispatch the workflow below for the live App.",
    ):
        assert anchor in recovery, anchor
    assert_no_live_recovery_overclaim(flat + " " + recovery)
    assert "A read-only listing on 2026-10-27 found none of the seven reserved principals in the live workspace" in recovery
    assert "The workspace holds none of the seven" not in recovery, "a dated finding, never an undated present fact"
    raw_recovery = _runbook_recovery()
    dispatch = raw_recovery[raw_recovery.index("gh workflow run deploy-dev.yml") : raw_recovery.index("# Fail closed unless")]
    assert "-f acknowledge_single_maintainer_break_glass=true" in dispatch, "or the run ends at refuse-unacknowledged"
    assert "-f remediate_foreign_catalog_bindings=false" in dispatch
    grants = _flat(GRANTS_DOC.read_text(encoding="utf-8"))
    assert "acts on that workspace's catalogs" not in grants
    assert "shares the live install's Unity Catalog metastore" in grants
    assert "every other workspace (the live one and every other team's) loses it" in grants


def test_grants_gives_foreign_catalog_remediation_no_ci_command_of_record() -> None:
    """Round 3 (post-W5 review G2/B1): GRANTS.md never names a refused dispatch as the command of record.

    The rule table refuses the deploy-dev input on pr105-host (exit 2, before
    any mutation), the operator path targets the NO-GO live workspace, so the
    remediation is an owner-only, separately reviewed decision. The refusal
    itself is pinned by test_foreign_catalog_remediation_is_refused_on_the_forked_anchor_target.
    """

    raw = GRANTS_DOC.read_text(encoding="utf-8")
    grants = _flat(raw)

    assert "The command of record is the manual `deploy-dev` workflow" not in grants
    heading = "**Foreign-catalog remediation has no CI command of record (post-W5, 2026-10-27).**"
    assert heading in grants
    # No sentence after the negated heading may name a command of record for
    # it, however paraphrased (post-W5 review, mutation G5).
    paragraph = grants.split(heading, 1)[1].split("When an owner-approved run does reach it", 1)[0]
    assert "command of record" not in paragraph.lower()
    assert (
        "a dispatch with `remediate_foreign_catalog_bindings=true` exits 2 in the `scripts/deploy.sh` "
        "preflight, before any mutation"
    ) in grants
    assert "The operator path, `MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS=1 ./scripts/deploy.sh -t dev`, targets the live workspace, whose governed deploy is NO-GO" in grants
    assert "Any metastore-wide remediation is therefore an owner-only, separately reviewed decision" in grants
    # Round 4 (post-W5 review B1): the owner decision rests on the blast radius
    # the code has. foreign_catalog_binding_manifest refuses a policy that
    # leaves any other metastore workspace unbound, so the hazard is the window
    # between _apply_catalog's isolate and re-bind calls, which a killed run
    # keeps open until a resumed recovery.
    assert (
        "this remediation flips each `OPEN` catalog the policy names to `ISOLATED` and then binds every other "
        "metastore workspace `READ_WRITE`. The signed manifest refuses a policy that omits one of them, that "
        "changes an already-`ISOLATED` catalog's bindings, or that binds the deploying workspace. At completion "
        "only the deploying workspace (`pr105-host` from deploy-dev.yml) loses the catalog. Between each "
        "catalog's flip and its re-binding, every other workspace (the live one and every other team's) loses "
        "it. A run killed there stays in that narrower state until a resumed recovery, which never reopens. A "
        "workspace assigned to the metastore later never sees the catalog."
    ) in grants
    assert "every workspace the policy does not bind loses that catalog" not in grants
    assert "must keep the live workspace bound" not in grants, "the manifest already refuses an unbound workspace"
    assert "Its signed manifest preserves every non-MIP metastore workspace as `READ_WRITE`" in grants, "consistency"
    # The dead dispatch never comes back as a runnable block, and the input
    # appears only beside its refusal.
    assert "-f remediate_foreign_catalog_bindings=true" not in raw
    for match in re.finditer(r"remediate_foreign_catalog_bindings=true", grants):
        assert "exits 2" in grants[match.end() : match.end() + 20], "only next to the refusal"


def test_the_audit_record_points_its_superseded_bootstrap_order_at_the_deployment_doc() -> None:
    """The W5e audit's 'waits for a forked-anchor target' order is history; a dated addendum says so."""

    audit = (ROOT / "docs" / "audits" / "ui-ux-technology-audit-2026-09-21.md").read_text(encoding="utf-8")
    waits = audit.index("it waits for a forked-anchor (pr105-host) target or install-scoped reserved names")
    paragraph = audit[waits : audit.index("\n\n", waits)]

    assert "Addendum (2026-10-27): the forked-anchor `pr105-host` target this paragraph waits for is now built" in paragraph
    assert '"CI staging install (deploy-dev.yml) and the release-gate bootstrap"' in paragraph
    assert f"## {BOOTSTRAP_HEADING.removeprefix('## ')}" in DEPLOYMENT_DOC.read_text(encoding="utf-8")


def test_every_reserved_identity_name_is_in_the_per_run_identities_table() -> None:
    """The contract the runbook cites cannot drift from the code that enforces it."""

    from tools.databricks.agent_runtime_access import AGENT_RUNTIME_DISPLAY_NAME
    from tools.databricks.m2m_identity_contract import IDENTITY_DEFAULTS

    lines = _doc_section(IDENTITIES_HEADING).splitlines()
    first = next(index for index, line in enumerate(lines) if line.startswith("| "))
    table = list(itertools.takewhile(lambda line: line.startswith("|"), lines[first:]))
    named = [match for line in table[2:] for match in re.findall(r"^\| [^|]+ \| `([^`]+)` \|", line)]

    assert sorted(named) == sorted(defaults.sp_name for defaults in IDENTITY_DEFAULTS.values())
    assert len(named) == len(IDENTITY_DEFAULTS) == 7, "the runbook says seven"
    assert IDENTITY_DEFAULTS["agent_runtime"].sp_name == AGENT_RUNTIME_DISPLAY_NAME
