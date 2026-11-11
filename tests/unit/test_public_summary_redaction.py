"""A synthetic deploy-dev.yml dispatch, offline, as the runner prints it: no identifier in the public log (M1-PR7, G2).

The repository and its run logs are public. Before each step runs, the runner
prints the step's ``with:`` inputs and its ``env:`` (the job's and the step's)
in the step's log header, and only secrets are masked there: a variable prints
in clear. So this drives a ``phase: plan`` dispatch from deploy-dev.yml itself,
in the offline sandbox (tests/fixtures/deploy_sandbox.py; the replay is
tests/fixtures/dispatch_replay.py, which the second-client portability proof
shares): every step's header
is rendered from the workflow with the job's contexts
(tests/fixtures/workflow_expressions.py), as the runner renders it, and every
step the sandbox can run runs under that env, with each ``$GITHUB_ENV`` write
carried to the steps after it:

1. check-dispatch, whose contexts hold the repository's own variables and
   secrets only;
2. the deploy job's prebuilt-release verify, from a sealed release;
3. the collect step, which reads the client environment name by name;
4. the resolve-client-profile action's two steps;
5. the dispatch preflight, with GitHub's API unreachable (S8's worst case);
6. ``scripts/deploy.sh -t client --phase plan --prebuilt-release`` under the
   step's own env, with ``--dry-run`` added (the sandbox is offline);
7. the plan-report summary of a PlanReport whose full half names foreign
   catalogs and owners.

The contexts hold what GitHub merges into them: the client environment's
profile, plus a repository-level variable and secret that are no profile
name (an identifier-shaped tenant id and a retired token). With the runner's
masking applied in order (every secret's whole value and each of its lines
from the job's start, then each ``::add-mask::``), no resolved identifier,
derived spelling, secret, repository value or foreign name is left in any
header or output line, and the step summary holds none even before masking.

Foreign names: no tool prints a live foreign name through
tools/clients/redact.foreign() yet (plan §4.16), so ``MIP_REDACT_FOREIGN_NAMES=1``
is set but inert. This proves only that the foreign names the profile and the
PlanReport hold (the accepted exposure's leaves, the report's full half) are
masked or kept out of the public half; the handles are tested as a class.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from tests.fixtures.client_profiles import EXPOSURE, paychex_shaped_profile
from tests.fixtures.deploy_sandbox import ANSI
from tests.fixtures.dispatch_replay import WORKFLOW, replay, run
from tools.clients import profile as views
from tools.clients.environment import from_github, resolve
from tools.clients.redact import ForeignNames
from tools.clients.schema import FIELD_CLASS, FieldClass
from tools.databricks.deploy_plan_report import LookupRecord, PlanReport, Refusal

CLIENT = "client-one"
# Other teams' objects, owners and emails: in the profile's accepted exposure
# (identifying leaves) and in the PlanReport's full half only.
FOREIGN = ("zyrplax_team_catalog", "Zyrplax Shared Space", "zyrplax-readers", "owner.zyrplax@other-team.example")
# What the repository's own scope adds to every environment's contexts: no profile name.
REPOSITORY_VARS = {"MIP_TENANT_ID": "tenant-zyrplax-EXAMPLE-7f3a91"}
REPOSITORY_SECRETS = {"DATABRICKS_TOKEN": "dapi-EXAMPLE-PLACEHOLDER-retired"}
# A profile name another test left in this worker's os.environ (the Lakebase
# tools write LAKEBASE_DATABASE straight into it): a shell value beats the
# .env.local the action writes, so the sandbox must strip it (CI, by test order;
# tests/fixtures/deploy_sandbox.inherited).
AMBIENT = {"LAKEBASE_DATABASE": "ambient_database", "LAKEBASE_INSTANCE_NAME": "ambient-instance", "APP_ENV": "dev"}


def _values() -> dict[str, str]:
    exposure = json.loads(json.dumps(EXPOSURE))
    exposure[0]["securable"]["name"] = FOREIGN[1]
    exposure[1]["securable"] = {"type": "CATALOG", "id": FOREIGN[0], "name": FOREIGN[0]}
    exposure[1]["grantee"] = FOREIGN[2]
    return paychex_shaped_profile(MIP_ACCEPTED_EXPOSURE_JSON=json.dumps(exposure))


@pytest.fixture(scope="module")
def dispatch(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp_path = tmp_path_factory.mktemp("dispatch")
    replayed = replay(
        tmp_path,
        values=_values(),
        client=CLIENT,
        repository_vars=REPOSITORY_VARS,
        repository_secrets=REPOSITORY_SECRETS,
        ambient=AMBIENT,
    )
    report = PlanReport(
        position="pre-lease",
        app_resource="apps.mip_app",
        app_bound=False,
        plan_sha256="b" * 64,
        refusals=(Refusal("not_adopted", "apps.mip_app", "adopt it first", detail={"owner": FOREIGN[3], "catalog": FOREIGN[0]}),),
        lookups=(LookupRecord("catalogs.mip", "catalogs", (FOREIGN[0],), "metastore", "catalogs.get", "exists"),),
    )
    public = tmp_path / "public.json"
    public.write_text(json.dumps(report.public_summary()), encoding="utf-8")
    replayed.jobs["deploy"].record(
        "Publish the public summary",
        [],
        run(["python", "-m", "tools.databricks.plan_report_publish", "summary", "--public", str(public), "--out", str(replayed.summary)],
            cwd=replayed.checkout, env=replayed.runner),
    )
    profile = resolve(from_github(replayed.variables, replayed.secrets, environment=CLIENT))
    secrets = replayed.secrets
    return {
        "jobs": replayed.jobs,
        "summary": replayed.summary.read_text(encoding="utf-8"),
        "log": replayed.log,
        "runner_temp": replayed.runner_temp,
        "secrets": [value for name, value in secrets.items() if value and FIELD_CLASS[name] is not FieldClass.PUBLIC],
        "identifiers": sorted({*views.masks(profile), *views.derived_masks(profile)}, key=len, reverse=True),
    }


def _forbidden(dispatch: dict[str, Any]) -> list[str]:
    return [
        *dispatch["identifiers"], *dispatch["secrets"], *REPOSITORY_VARS.values(), *REPOSITORY_SECRETS.values(),
        *FOREIGN, *(name.lower() for name in FOREIGN),
    ]


def test_the_synthetic_dispatch_shape_passes_every_step(dispatch: dict[str, Any]) -> None:
    for job in dispatch["jobs"].values():
        for name, result in job.results.items():
            assert result.returncode == 0, (job.name, name, result.stdout[-3000:], result.stderr[-3000:])
    deploy = dispatch["jobs"]["deploy"].results
    assert "Collect the client's profile from its environment" in deploy, "the collect step ran"
    assert "Refuse a profile name set at repository or organization level" in dispatch["jobs"]["check-dispatch"].results
    assert dispatch["log"].read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    assert "environment protections: environment_protections_unreadable" in deploy["Preflight the dispatch"].stdout
    run = ANSI.sub("", deploy["Run the client phase"].stdout)
    assert "plan phase complete: stopped before the lease; no governed resource was changed" in run
    assert "staged source files are this checkout's commit" in deploy["Verify the prebuilt release against its manifest"].stdout
    assert not (dispatch["runner_temp"] / "mip-client-vars.json").exists(), "the last step removes the profile files"


def test_the_public_log_headers_and_output_hold_no_identifier_secret_or_repository_value(dispatch: dict[str, Any]) -> None:
    """The runner prints each step's with: and env: before it runs; only secrets are masked there."""

    for job in dispatch["jobs"].values():
        for name, line in job.shown():
            for value in _forbidden(dispatch):
                assert value not in line, (job.name, name, "a value escaped its mask")


def test_no_step_header_holds_a_whole_context(dispatch: dict[str, Any]) -> None:
    """A whole context would print every name it holds, the repository's own included; no header does."""

    for job in dispatch["jobs"].values():
        headers = [line for _step, line, is_header in job.lines if is_header]
        assert headers, job.name
        for line in headers:
            for name in (*REPOSITORY_VARS, *REPOSITORY_SECRETS):
                assert name not in line, (job.name, line[:120])


def test_every_step_but_the_dry_run_commands_prints_no_value_even_unmasked(dispatch: dict[str, Any]) -> None:
    """The steps print codes, digests, names and paths only; headers are the runner's (masked above)."""

    for job in dispatch["jobs"].values():
        for name, result in job.results.items():
            if name == "Run the client phase":
                continue
            for line in (result.stdout + result.stderr).splitlines():
                if line.startswith("::add-mask::"):
                    continue
                for value in _forbidden(dispatch):
                    assert value not in line, (job.name, name)


def test_the_step_summary_holds_no_value_even_before_masking(dispatch: dict[str, Any]) -> None:
    summary = dispatch["summary"]
    assert "### deploy-dev dispatch preflight (--phase plan)" in summary
    assert "### Bundle plan gate (pre-lease): refused" in summary and "not_adopted" in summary
    for value in _forbidden(dispatch):
        assert value not in summary


def test_foreign_name_handles_as_a_class_the_jobs_switch_is_set_but_no_probe_prints_through_it_yet() -> None:
    switch = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]["env"]["MIP_REDACT_FOREIGN_NAMES"]
    names = ForeignNames(enabled=switch == "1")
    assert [names.handle(name) for name in FOREIGN[:2]] == ["foreign#1", "foreign#2"]
    assert names.mapping() == {"foreign#1": FOREIGN[0], "foreign#2": FOREIGN[1]}, "the mapping, for the full report only"
