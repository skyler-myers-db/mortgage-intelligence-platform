"""databricks.yml has one host-less client target with per-job run_as (M1-PR2).

* Targets are exactly ``client`` and the validate-only ``ci``; the root host
  anchor, the ``dev`` target (with its Genie pin) and ``prod`` are gone.
* No workspace host is committed: none at the root, none on ``client``; ``ci``
  keeps a placeholder that names no workspace.
* ``client`` is production mode with the deployer's root plus the client key,
  the ``mip_client`` presets tag and the dashboard folder override, and no
  top-level ``run_as`` anywhere.
* Every job and the pipeline name their own ``run_as``: the deployer service
  principal, until M2-PR5.
* The new variables carry the defaults the plan names, and the profile sets
  exactly the client target's own ones.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures.client_profiles import customer_profile, demo_profile, paychex_shaped_profile
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_TEXT = (ROOT / "databricks.yml").read_text(encoding="utf-8")
BUNDLE: dict[str, Any] = yaml.safe_load(BUNDLE_TEXT)
DEPLOYER_RUN_AS = {"service_principal_name": "${var.deployer_principal}"}
CLIENT_VARIABLES = ("client_key", "deployer_principal", "jobs_principal", "dashboard_parent_path", "lakebase_readable_secondaries")
# A workspace host of any reviewed cloud, or the legacy per-workspace `dbc-` label.
WORKSPACE_HOST = re.compile(
    r"(?i)\b(?:dbc-[0-9a-f]{4,}-[0-9a-f]{4}|adb-[0-9]{6,}\.[0-9]+)\b"
    r"|[a-z0-9-]+\.(?:cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)\b"
)


def test_the_targets_are_exactly_client_and_ci() -> None:
    assert set(BUNDLE["targets"]) == {"client", "ci"}
    assert "&default_host" not in BUNDLE_TEXT and "*default_host" not in BUNDLE_TEXT
    assert "genie_space_id: 0" not in BUNDLE_TEXT, "no target pins a Genie id"


def test_no_workspace_host_is_committed() -> None:
    assert "workspace" not in BUNDLE, "no root workspace block: the host came from there"
    assert "host" not in BUNDLE["targets"]["client"].get("workspace", {})
    ci_host = BUNDLE["targets"]["ci"]["workspace"]["host"]
    assert ci_host.endswith(".invalid"), "the ci placeholder resolves to no workspace (RFC 2606)"
    # Positive control first: the pattern does catch each cloud's host shape.
    for host in ("dbc-1a2b3c4d-5e6f.cloud.databricks.com", "adb-1234567890123456.7.azuredatabricks.net",
                 "1234567890123456.7.gcp.databricks.com"):
        assert WORKSPACE_HOST.search(f"host: https://{host}"), host
    assert WORKSPACE_HOST.search(BUNDLE_TEXT) is None


def test_the_client_target_is_host_less_production_mode_with_the_deployer_root() -> None:
    client = BUNDLE["targets"]["client"]
    assert client["mode"] == "production"
    assert client["workspace"] == {
        "root_path": "/Workspace/Users/${var.deployer_principal}/.bundle/${bundle.name}/${var.client_key}"
    }
    assert client["presets"] == {"tags": {"mip_client": "${var.client_key}"}}
    assert client["resources"] == {
        "dashboards": {
            "mip_executive_dashboard": {"parent_path": "${var.dashboard_parent_path}"},
            "mip_segment_dashboard": {"parent_path": "${var.dashboard_parent_path}"},
        }
    }
    instance = BUNDLE["resources"]["database_instances"]["mip_app_state"]
    assert instance["enable_readable_secondaries"] == "${var.lakebase_readable_secondaries}"


def test_there_is_no_top_level_run_as() -> None:
    assert "run_as" not in BUNDLE
    for name, target in BUNDLE["targets"].items():
        assert "run_as" not in target, name


@pytest.mark.parametrize("kind", ("jobs", "pipelines"))
def test_every_job_and_the_pipeline_name_their_own_run_as(kind: str) -> None:
    resources = BUNDLE["resources"][kind]
    assert resources, kind
    for key, resource in resources.items():
        assert resource.get("run_as") == DEPLOYER_RUN_AS, f"{kind}.{key}"
    # Nothing else in the bundle names a run_as: apps and dashboards run as the deployer.
    assert BUNDLE_TEXT.count("run_as:") == len(BUNDLE["resources"]["jobs"]) + len(BUNDLE["resources"]["pipelines"])


def test_the_ci_target_validates_with_placeholders_only() -> None:
    ci = BUNDLE["targets"]["ci"]
    assert "mode" not in ci
    assert ci["workspace"]["root_path"] == "/Workspace/ci-placeholder/.bundle/${bundle.name}/${bundle.target}"
    assert ci["variables"] == {"client_key": "ci", "deployer_principal": "00000000-0000-0000-0000-000000000000"}


def test_the_client_variables_carry_the_planned_defaults() -> None:
    variables = BUNDLE["variables"]
    for name in CLIENT_VARIABLES:
        assert variables[name]["description"].strip(), name
    assert "default" not in variables["client_key"] and "default" not in variables["deployer_principal"]
    assert variables["jobs_principal"]["default"] == "${var.deployer_principal}"
    assert variables["dashboard_parent_path"]["default"] == "${workspace.resource_path}"
    assert variables["lakebase_readable_secondaries"]["default"] is False


@pytest.mark.parametrize("build", (demo_profile, customer_profile, paychex_shaped_profile))
def test_the_profile_sets_only_the_client_targets_own_variables(build: Any) -> None:
    profile = resolve(RawProfile(values=build()))
    target_vars = views.client_target_vars(profile)
    assert set(target_vars) <= set(CLIENT_VARIABLES) - {"jobs_principal"}, "jobs_principal stays the default"
    assert target_vars["client_key"] == profile.client
    assert target_vars["deployer_principal"] == profile.identity_ids.deployer.lower()
    assert set(views.bundle_vars(profile)) <= set(BUNDLE["variables"])
    view = views.client_target(profile)
    assert view == {
        "MIP_CLIENT": profile.client,
        "MIP_INSTALL_CLASS": profile.install_class,
        "DATABRICKS_HOST": profile.databricks_host,
        **{f"BUNDLE_VAR_{name}": value for name, value in target_vars.items()},
    }


def test_an_unset_dashboard_folder_or_secondaries_flag_keeps_the_bundle_default() -> None:
    profile = resolve(RawProfile(values=paychex_shaped_profile(MIP_LAKEBASE_READABLE_SECONDARIES=None)))
    assert set(views.client_target_vars(profile)) == {"client_key", "deployer_principal"}
    pinned = resolve(RawProfile(values=demo_profile()))
    assert views.client_target_vars(pinned)["dashboard_parent_path"] == "/Workspace/Shared/dashboards-alpha"
    assert views.client_target_vars(pinned)["lakebase_readable_secondaries"] == "false"
