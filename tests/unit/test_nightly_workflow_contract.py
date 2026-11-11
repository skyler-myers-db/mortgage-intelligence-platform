"""Contracts for nightly.yml, the per-client live validation (M1-PR8).

Live validation is a dispatch-only release gate, one client per run. Every job
that reaches a workspace runs in the client's ``<client>-validate`` GitHub
Environment, never in ``<client>`` (the deployer's federation subject). The
validator jobs authenticate as the client's ``<prefix>-validator`` through
GitHub OIDC; the browser job holds no ID token and calls the App API with
per-run bearers of the reserved operator identities. No identity a job uses
is in the workspace ``admins`` group, and the only writes are product rows
through the App API plus the validator's own Genie conversations.

Most contracts walk the parsed workflow rather than grep its text.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures.workflow_expressions import expressions_in, references, scalars
from tools import live_validation_gate as gate
from tools.clients import validation_profile as vp
from tools.clients import workflow_contexts as contexts

REPO = Path(__file__).resolve().parents[2]
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
DEPLOYMENT_DOC = REPO / "docs" / "deployment.md"
REAL_DATA_SPEC = REPO / "frontend" / "tests" / "e2e" / "real_data.spec.ts"
LIVE_CAMPAIGN_PROOF = REPO / "frontend" / "tests" / "support" / "liveCampaignProof.ts"
LIVE_CAMPAIGN_PROOF_TEST = REPO / "frontend" / "tests" / "support" / "liveCampaignProof.test.ts"
LIVE_HARDENING_SPEC = REPO / "frontend" / "tests" / "e2e" / "live_hardening_regressions.spec.ts"
CONSOLE_LAYOUT_SPEC = REPO / "frontend" / "tests" / "e2e" / "console-layout.spec.ts"
ECONOMICS_SCATTER_SPEC = REPO / "frontend" / "tests" / "e2e" / "economics_scatter.spec.ts"

VALIDATE_ENVIRONMENT = "${{ inputs.client }}-validate"
# The deployer's federation subject; only a job with no ID token may name it.
DEPLOY_ENVIRONMENT = "${{ inputs.client }}"
BROWSER_JOB = "playwright-e2e-live"
VALIDATOR_JOBS = ("parity-live", "kill-drill-simulated", "genie-fuzz-standard", "genie-fuzz-deep")
WORKSPACE_JOBS = (*VALIDATOR_JOBS, BROWSER_JOB)
GUARD_JOBS = ("check-dispatch", "check-validate-environment")
# A status check function that lets a job run after a failed need.
STATUS_FUNCTION = re.compile(r"\b(?:always|failure|cancelled)\s*\(")


def _text() -> str:
    return NIGHTLY.read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(_text())


def _jobs() -> dict[str, dict[str, Any]]:
    return _workflow()["jobs"]


def _step(job: str, name: str) -> dict[str, Any]:
    (step,) = [step for step in _jobs()[job]["steps"] if step.get("name") == name]
    return step


def _runs(job: str) -> list[tuple[str, str]]:
    return [(step.get("name", step.get("uses", "")), step.get("run", "")) for step in _jobs()[job]["steps"]]


def _index(job: str, needle: str) -> int:
    """The position of the first step of ``job`` whose name or run holds ``needle``."""

    return next(i for i, (name, run) in enumerate(_runs(job)) if needle in name or needle in run)


def _needs(job: dict[str, Any]) -> set[str]:
    needs = job.get("needs") or []
    return {needs} if isinstance(needs, str) else set(needs)


def _closure(jobs: dict[str, dict[str, Any]], name: str) -> set[str]:
    direct = _needs(jobs[name])
    return direct.union(*(_closure(jobs, parent) for parent in direct)) if direct else set()


def _strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)


def _context_names(node: object) -> set[tuple[str, str | None]]:
    """Every (context, NAME) an expression under ``node`` reads, by value or by presence.

    Every form counts (tests/fixtures/workflow_expressions.py): ``secrets.X``
    is (secrets, X); a bracket, a ``format()`` index, ``toJSON(secrets)`` or the
    bare context is (secrets, None), which no allowed name set holds. An
    ``if:`` value is an expression even without ``${{ }}``.
    """

    return {
        reference
        for path, value in scalars(node)
        for expression in expressions_in(value, condition=bool(path) and path[-1] == "if")
        for reference in references(expression)
    }


def _pytest_files() -> dict[str, set[str]]:
    """job -> every tests/... path a pytest step of that job runs."""

    found: dict[str, set[str]] = {}
    for name in _jobs():
        for _step_name, run in _runs(name):
            if "pytest" in run:
                found.setdefault(name, set()).update(re.findall(r"tests/integration/[a-z0-9_]+\.py", run))
    return found


# ---------------------------------------------------------------- dispatch, client and sha


def test_live_validation_is_dispatch_only_and_names_its_client_and_sha() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))

    assert list(triggers) == ["workflow_dispatch"]
    assert "\n  schedule:" not in _text() and "cron:" not in _text()
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"client", "protections_unverified_ack", "run_deep_genie_fuzz"}
    assert inputs["client"]["required"] is True and inputs["client"]["type"] == "string"
    # The release gate matches evidence on this run-name (tools/live_validation_gate.RUN_NAME).
    assert workflow["run-name"] == "nightly client=${{ inputs.client }} sha=${{ github.sha }}"
    rendered = workflow["run-name"].replace("${{ inputs.client }}", "client-one").replace("${{ github.sha }}", "a" * 40)
    assert gate.RUN_NAME.fullmatch(rendered)


def test_nightly_shares_the_clients_deployment_lane() -> None:
    """deploy-dev.yml's deploy job holds the same group, so nightly never validates mid-deploy."""

    deploy = yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))["jobs"]["deploy"]
    concurrency = _workflow()["concurrency"]

    assert concurrency["group"] == deploy["concurrency"]["group"] == "mip-client-${{ inputs.client }}"
    assert concurrency["cancel-in-progress"] is False


# ---------------------------------------------------------------- identity and environment


def test_only_validator_jobs_hold_an_id_token_and_each_runs_in_the_validate_environment() -> None:
    """No job can exchange a GitHub ID token for the deployer.

    The deployer's one federation policy trusts exactly ``environment:<client>``.
    The validator jobs, the only ones with ``id-token: write``, run in
    ``<client>-validate``; the browser job runs in ``<client>``, where the
    credential sink writes the operator M2M secrets
    (tools/databricks/m2m_oauth_github.py refuses a ``-validate``
    environment), and requests no ID token, so it reaches no identity by OIDC.
    """

    workflow = _workflow()
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}, "no workflow-level ID token"
    assert {name for name, job in jobs.items() if "environment" in job} == set(WORKSPACE_JOBS)
    for name in VALIDATOR_JOBS:
        assert jobs[name]["environment"] == VALIDATE_ENVIRONMENT, name
    assert jobs[BROWSER_JOB]["environment"] == DEPLOY_ENVIRONMENT
    with_id_token = {name for name, job in jobs.items() if (job.get("permissions") or {}).get("id-token") == "write"}
    assert with_id_token == set(VALIDATOR_JOBS)
    for name, job in jobs.items():
        if job.get("environment") == DEPLOY_ENVIRONMENT:
            assert job["permissions"] == {"contents": "read"}, f"{name} could become the deployer"
    sink = (REPO / "tools" / "databricks" / "m2m_oauth_github.py").read_text(encoding="utf-8")
    assert "never holds an M2M secret" in sink
    for name, job in jobs.items():
        if "environment" not in job:
            assert not [ref for ref in _context_names(job) if ref[0] == "secrets"] or name == "check-dispatch", name


def test_no_job_authenticates_as_the_deployer_or_binds_a_deploy_only_name() -> None:
    """Walks every job: only its kind's validation-profile names are bound, each one by one.

    The deployer's id, any workspace token, the account principal, the agentic
    identities and every signing key are deploy-only; check-dispatch reads the
    repository scope by presence only, never a value. A validator job binds
    the validator names only; the browser job adds the three operator secrets.
    """

    text = _text()
    assert "toJSON(secrets)" not in text and "toJSON(vars)" not in text
    for name, job in _jobs().items():
        if name == "check-dispatch":
            continue
        allowed = set(vp.KINDS["browser" if name == BROWSER_JOB else "validator"])
        names = {context_name for _context, context_name in _context_names(job)}
        assert names <= allowed, (name, sorted(names - allowed))
    for forbidden in (
        "DATABRICKS_TOKEN }}",
        "MIP_CI_DEPLOYER_CLIENT_ID }}",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET }}",
        "DATABRICKS_VERIFIER_CLIENT_SECRET }}",
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE }}",
        "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET }}",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY }}",
    ):
        assert forbidden not in text, forbidden
    # No Databricks CLI, no CLI profile with a token, no auth override in a step env.
    assert "databricks/setup-cli" not in text
    assert not re.search(r"(?m)^\s+databricks\s", text)
    assert not re.search(r"(?m)^\s*echo\s+[\"']?token\s*=", text) and "token = " not in text
    for name in _jobs():
        for step in _jobs()[name]["steps"]:
            for variable in ("DATABRICKS_TOKEN", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_AUTH_TYPE"):
                assert variable not in (step.get("env") or {}), (name, step.get("name"), variable)


def _operator_secret_violations(workflow: dict[str, Any]) -> tuple[list[str], int]:
    """(each operator secret or whole/indexed context bound outside a minting step's env, the minting envs seen)."""

    operator_names = set(vp.OPERATOR_SECRETS.values())
    violations = [f"workflow level: {reference}" for reference in _context_names({k: v for k, v in workflow.items() if k != "jobs"})]
    minting_envs = 0
    for name, job in workflow["jobs"].items():
        job_level = {key: value for key, value in job.items() if key != "steps"}
        violations += [f"{name} job level: {ref}" for ref in _context_names(job_level) if ref[1] is None or ref[1] in operator_names]
        for step in job["steps"]:
            minting = name == BROWSER_JOB and "validation_session operators" in step.get("run", "")
            for key, value in step.items():
                where = (name, step.get("name", step.get("uses")), key)
                references_here = _context_names({key: value})
                violations += [f"{where}: {ref}" for ref in references_here if ref[1] is None]
                found = {context_name for _c, context_name in references_here} & operator_names
                if minting and key == "env":
                    if found != operator_names:
                        violations.append(f"{where}: binds {sorted(found)}")
                    minting_envs += 1
                elif name != "check-dispatch" and found:
                    violations.append(f"{where}: {sorted(found)}")
    return violations, minting_envs


def test_the_operator_secrets_reach_only_the_env_of_the_minting_steps() -> None:
    """Every string of the workflow: its top-level keys, each job's keys, each step's env, with, run and if.

    An operator secret bound at workflow or job level would reach ``npm ci``,
    ``npx playwright install`` and every Playwright step; one inline in a
    ``run:`` would print in the step's script. Only the env of a
    ``validation_session operators`` step of the browser job may name one, by
    name: a bracket, ``format()`` or whole-context form is refused anywhere.
    """

    workflow = _workflow()
    violations, minting_envs = _operator_secret_violations(workflow)

    assert violations == []
    assert minting_envs == 4, "positive control: the walk reaches each minting step's env"
    assert set(workflow["env"]) == {"PYTHON_VERSION", "NODE_VERSION"}


@pytest.mark.parametrize(
    ("where", "expression"),
    [
        ("workflow", "${{ secrets.DATABRICKS_ADMIN_CLIENT_SECRET }}"),
        ("workflow", "${{ secrets['DATABRICKS_ADMIN_CLIENT_SECRET'] }}"),
        ("job", "${{ secrets.DATABRICKS_ADMIN_CLIENT_SECRET }}"),
        ("job", "${{ secrets['DATABRICKS_ADMIN_CLIENT_SECRET'] }}"),
        ("job", "${{ secrets[format('{0}_CLIENT_SECRET', 'DATABRICKS_ADMIN')] }}"),
        ("job", "${{ toJSON(secrets) }}"),
        ("npm step", "${{ secrets['DATABRICKS_CLIENT_SECRET'] }}"),
        ("npm step if", "secrets.DATABRICKS_CLIENT_SECRET != ''"),
        ("minting step", "${{ secrets['DATABRICKS_OPERATOR2_CLIENT_SECRET'] }}"),
    ],
)
def test_an_operator_secret_bound_anywhere_else_or_in_another_form_is_caught(where: str, expression: str) -> None:
    """The dot form at job level only was caught before: the workflow env and the bracket form passed."""

    workflow = _workflow()
    npm = next(step for step in workflow["jobs"][BROWSER_JOB]["steps"] if step.get("run", "").startswith("npm "))
    minting = next(step for step in workflow["jobs"][BROWSER_JOB]["steps"] if "validation_session operators" in step.get("run", ""))
    target = {
        "workflow": workflow["env"],
        "job": workflow["jobs"][BROWSER_JOB]["env"],
        "npm step": npm.setdefault("env", {}),
        "minting step": minting["env"],
    }
    if where == "npm step if":
        npm["if"] = expression
    else:
        target[where]["MIP_PLAYWRIGHT_ADMIN_SECRET"] = expression

    assert _operator_secret_violations(workflow)[0], (where, expression)


def test_each_collect_step_is_the_generated_validation_profile_block() -> None:
    """Generated by tools/clients/validation_profile.collect_env; the operator secrets never reach a file."""

    validator = vp.collect_env("validator")
    operators = vp.collect_env("operators")
    for name in WORKSPACE_JOBS:
        (collect,) = [step for step in _jobs()[name]["steps"] if "workflow_contexts collect" in step.get("run", "")]
        assert {key: str(value).split() for key, value in collect["env"].items()} == {
            key: value.split() for key, value in validator.items()
        }, name
    minting = [step for step in _jobs()[BROWSER_JOB]["steps"] if "validation_session operators" in step.get("run", "")]
    assert len(minting) == 4
    for step in minting:
        bound = {key: str(value).split() for key, value in step["env"].items() if key.startswith("MIP_CLIENT_")}
        assert bound == {key: value.split() for key, value in operators.items()}, step["name"]
        # The browser job mints only for the profile the validator jobs read.
        assert step["env"]["MIP_VALIDATE_PROFILE_DIGEST"] == "${{ needs.parity-live.outputs.profile_digest }}", step["name"]
    for name in set(_jobs()) - {"check-dispatch"}:
        for step in _jobs()[name]["steps"]:
            if "validation_session operators" not in step.get("run", ""):
                assert not any(f"secrets.{secret}" in str(step.get("env")) for secret in vp.OPERATOR_SECRETS.values())


def test_every_validator_job_proves_the_validator_session_before_any_test() -> None:
    """tools/clients/validation_session.session refuses a validator in admins, and the deployer."""

    for name in VALIDATOR_JOBS:
        session = _index(name, "validation_session session")
        first_test = min(i for i, (_n, run) in enumerate(_runs(name)) if "pytest" in run or "run_drill.sh" in run)
        assert _index(name, "workflow_contexts collect") < session < first_test, name
        assert '--environment "$MIP_VALIDATE_ENVIRONMENT"' in _runs(name)[session][1]
        assert _step(name, _runs(name)[session][0])["env"] == {"MIP_VALIDATE_ENVIRONMENT": VALIDATE_ENVIRONMENT}
    source = (REPO / "tools" / "clients" / "validation_session.py").read_text(encoding="utf-8")
    assert '"me_not_in_admins"' in source and '"me_is_the_validator"' in source
    assert 'f"{role}_not_in_admins"' in source
    assert '"catalog_is_the_one_the_live_suites_read"' in source


def test_every_workspace_job_refuses_debug_logging_first_and_removes_its_session_files() -> None:
    for name in WORKSPACE_JOBS:
        steps = _jobs()[name]["steps"]
        assert steps[0]["name"] == "Refuse step debug logging" and steps[0]["if"] == "${{ runner.debug == '1' }}"
        last = steps[-1]
        assert last["name"] == "Remove the session files" and last["if"] == "always()"
        assert '"$RUNNER_TEMP/mip-validate-secrets.json"' in last["run"]
        if name in VALIDATOR_JOBS:
            assert '"$HOME/.databrickscfg"' in last["run"]


# ---------------------------------------------------------------- the dispatch refusals


def test_check_dispatch_holds_deploy_devs_refusals() -> None:
    deploy = yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))["jobs"]["check-dispatch"]["steps"]
    nightly = _jobs()["check-dispatch"]

    assert [step["name"] for step in nightly["steps"]] == [
        "Refuse step debug logging",
        "Refuse a malformed client key",
        "Refuse a profile name set at repository or organization level",
    ]
    assert "environment" not in nightly and "permissions" not in nightly and "if" not in nightly
    assert not any("uses" in step for step in nightly["steps"]), "no checkout"
    debug, key, scope = nightly["steps"]
    assert debug["run"] == deploy[0]["run"].replace("deploy-dev.yml", "nightly.yml") and debug["if"] == deploy[0]["if"]
    for line in deploy[1]["run"].splitlines()[:8]:
        assert line in key["run"], line
    assert {name: value.split() for name, value in scope["env"].items()} == {
        name: value.split() for name, value in contexts.repository_scope_env().items()
    }
    assert scope["run"] == deploy[2]["run"]


def test_the_validate_environment_is_checked_before_any_job_names_it() -> None:
    """GitHub creates an unknown environment, unprotected, the first time a job names it."""

    jobs = _jobs()
    check = jobs["check-validate-environment"]

    assert _needs(check) == {"check-dispatch"} and "environment" not in check
    assert check["permissions"] == {"contents": "read", "actions": "read"}
    install, refuse = check["steps"][2:]
    assert install["run"].split() == "python -m pip install --require-hashes --only-binary=:all: -r requirements-deploy.hashes.txt".split()
    assert refuse["run"] == 'python -m tools.clients.validation_session check-environment --client "$MIP_DISPATCH_CLIENT"'
    assert refuse["env"]["MIP_DISPATCH_PROTECTIONS_ACK"] == "${{ inputs.protections_unverified_ack }}"
    for name in WORKSPACE_JOBS:
        assert {"check-dispatch", "check-validate-environment"} <= _closure(jobs, name), name
        assert not STATUS_FUNCTION.search(str(jobs[name].get("if", ""))), f"{name} runs past a refused check"
    notify = jobs["notify-on-failure"]
    assert not set(GUARD_JOBS) & _needs(notify), "a refused dispatch files no issue"
    assert all(f"needs.{need}.result == 'failure'" in notify["if"] for need in _needs(notify))


# ---------------------------------------------------------------- the browser job


def test_the_playwright_job_has_no_id_token_and_npm_runs_no_install_script() -> None:
    jobs = _jobs()
    browser = jobs[BROWSER_JOB]

    assert browser["permissions"] == {"contents": "read"}
    npm = [run for _name, run in _runs(BROWSER_JOB) if re.search(r"\bnpm\b", run)]
    assert npm and all("npm --prefix frontend ci --ignore-scripts" in run for run in npm)
    assert "npm install" not in _text()
    for name, job in jobs.items():
        if name == BROWSER_JOB:
            continue
        for step in job["steps"]:
            assert "setup-node" not in step.get("uses", ""), name
            assert not re.search(r"\b(?:npm|npx)\b", step.get("run", "")), (name, step.get("name"))


def test_the_browser_job_mints_reserved_operator_bearers_before_any_app_call() -> None:
    mint = _index(BROWSER_JOB, "Mint per-run App bearers for the reserved operator identities")
    for needle in (
        "Recover abandoned live campaign fixtures",
        "Authenticated non-admin smoke (M2M)",
        "App API workflow contracts",
        "Run real-UC Playwright specs",
    ):
        assert mint < _index(BROWSER_JOB, needle), needle
    assert _step(BROWSER_JOB, "Mint per-run App bearers for the reserved operator identities")["run"].endswith('--github-env "$GITHUB_ENV"')
    assert set(vp.OPERATOR_SECRETS) == {"normal", "operator2", "admin"}


def test_live_playwright_job_budgets_cold_app_and_serial_browser_matrix() -> None:
    assert int(_jobs()[BROWSER_JOB]["timeout-minutes"]) >= 90


def test_live_browser_smoke_never_logs_untrusted_response_bodies() -> None:
    block = _step(BROWSER_JOB, "Authenticated non-admin smoke (M2M)")["run"]

    assert "body=" not in block and "session!r" not in block
    assert '"operator_b": os.environ["MIP_OPERATOR2_BEARER_TOKEN"]' in block


# ---------------------------------------------------------------- what nightly may write


APP_API_SUITES = (
    "tests/integration/test_campaign_audit_workflow_live.py",
    "tests/integration/test_lakebase_idempotency_live.py",
    "tests/integration/test_lakebase_concurrency_live.py",
    "tests/integration/test_two_operator_recovery_live.py",
)


def test_no_job_mutates_lakebase_delta_or_grants_directly() -> None:
    """The only writes are product rows through the App API; the App SP performs every transaction."""

    # Every key and value of the parsed workflow: what runs, never a comment
    # (check-dispatch reads the repository scope by presence only).
    text = "\n".join(_strings({name: job for name, job in _jobs().items() if name != "check-dispatch"}))
    for forbidden in (
        "secrets.LAKEBASE",
        "verify_ai_gateway_exact_proof",
        "grant_ai_gateway_inference_table",
        "cleanup_lifecycle_replay_scratch",
        "cleanup_campaign_treatment_scratch",
        "MIP_LIVE_SCRATCH_SUFFIX",
        "test_lifecycle_delta_replay_live",
        "test_campaign_treatment_at_cap_live",
        "test_lineage_manifest_live",
        "run_agent_eval",
        "tools.databricks.",
        "psql",
        "--target warehouse-real",
        "--target lakebase-real",
    ):
        assert forbidden not in text, forbidden
    mutation = _step(BROWSER_JOB, "App API workflow contracts")
    assert mutation["env"] == {"MIP_LIVE_MUTATION_OK": "1"}
    assert mutation["if"] == "steps.install-class.outputs.app_api_mutation == 'run'"
    assert [step for job in _jobs().values() for step in job["steps"] if "MIP_LIVE_MUTATION_OK" in str(step)] == [mutation]
    assert set(re.findall(r"tests/integration/[a-z0-9_]+\.py", mutation["run"])) == set(APP_API_SUITES)
    for suite in APP_API_SUITES:
        source = (REPO / suite).read_text(encoding="utf-8")
        assert "MIP_APP_URL" in source, suite
        for direct in ("psycopg", "LAKEBASE_HOST", "LAKEBASE_PASSWORD", "generate_database_credential"):
            assert direct not in source, (suite, direct)
    # Its one warehouse reader inspects the audit snapshot, beyond the validator's reads.
    assert "--deselect tests/integration/test_lakebase_idempotency_live.py::test_live_saved_holdout_and_household_dedup_exclude_non_treatment_borrowers" in mutation["run"]


def test_the_lakebase_read_path_is_ddl_free_and_precedes_the_app_api_writes() -> None:
    jobs = _jobs()

    read = _step("parity-live", "Lakebase post-activation read path")
    assert read["run"] == "pytest -q tests/integration/test_lakebase_round_trip.py"
    assert "parity-live" in _closure(jobs, BROWSER_JOB)
    files = _pytest_files()
    assert "tests/integration/test_lakebase_round_trip.py" in files["parity-live"]
    assert not set(APP_API_SUITES) & (set().union(*files.values()) - files[BROWSER_JOB])


def test_simulated_drills_never_receive_static_lakebase_secrets() -> None:
    job = _jobs()["kill-drill-simulated"]
    export = _step("kill-drill-simulated", "Export simulated drill env")["run"]

    assert not [ref for ref in _context_names(job) if ref[1] is None or ref[1].startswith("LAKEBASE")]
    for line in ("LAKEBASE_HOST=invalid.host.example.com", "LAKEBASE_USER=mip-simulated-drill", "LAKEBASE_PASSWORD=drill-invalid-password"):
        assert line in export
    targets = re.findall(r"--target ([a-z-]+)", " ".join(run for _n, run in _runs("kill-drill-simulated")))
    assert targets == ["genie", "token", "warehouse-sim", "lakebase-sim"]


def test_live_campaign_fixtures_are_run_bound_and_recovered_after_cancellation() -> None:
    marker = _step(BROWSER_JOB, "Derive governed live campaign run marker")["run"]
    assert 'str.maketrans("0123456789", "abcdefghij")' in marker and "MIP_LIVE_CAMPAIGN_RUN_MARKER=" in marker

    recovery = _index(BROWSER_JOB, "Recover abandoned live campaign fixtures")
    assert _index(BROWSER_JOB, "Mint per-run App bearers") < recovery < _index(BROWSER_JOB, "Authenticated non-admin smoke")
    assert "--all-run-markers" in _runs(BROWSER_JOB)[recovery][1]
    mutation = _step(BROWSER_JOB, "App API workflow contracts")["run"]
    assert mutation.index("--all-run-markers") < mutation.index("pytest -q")

    archive = _step(BROWSER_JOB, "Always archive run-marked live campaign fixtures")
    assert archive["if"] == "always()"
    assert "validation_session operators" in archive["run"] and "--github-env" not in archive["run"]
    assert archive["run"].split(" -- ", 1)[1].split() == (
        'python -m tools.cleanup_live_campaign_fixtures --run-marker "$MIP_LIVE_CAMPAIGN_RUN_MARKER"'.split()
    )
    reconcile = _step(BROWSER_JOB, "Always reconcile live browser campaign fixtures")
    assert reconcile["if"] == "always()" and reconcile["run"].endswith("--all-run-markers")
    assert _index(BROWSER_JOB, "Run real-UC Playwright specs") < _index(BROWSER_JOB, "Always reconcile live browser")


# ---------------------------------------------------------------- the release gate and the header


def test_the_release_gate_jobs_and_git_sha_checks_exist() -> None:
    """tools/live_validation_gate.py needs these three jobs green; the two app-sha checks certify the commit."""

    names = {job.get("name") for job in _jobs().values()}
    assert set(gate.REQUIRED_JOBS) <= names
    assert NIGHTLY.name == gate.WORKFLOW_FILE
    checks = [
        (job, run)
        for job in _jobs()
        for _name, run in _runs(job)
        if run.startswith("python -m tools.clients.validation_session app-sha")
    ]
    assert checks == [
        ("parity-live", 'python -m tools.clients.validation_session app-sha --token-env MIP_BEARER_TOKEN --git-sha "$GITHUB_SHA"'),
        (BROWSER_JOB, 'python -m tools.clients.validation_session app-sha --token-env MIP_NON_ADMIN_BEARER_TOKEN --git-sha "$GITHUB_SHA"'),
    ]
    assert _index("parity-live", "app-sha") < _index("parity-live", "test_sql_python_parity")
    assert _index(BROWSER_JOB, "app-sha") < _index(BROWSER_JOB, "App API workflow contracts")


def _header() -> str:
    return " ".join(line.lstrip("# ") for line in _text().split("\non:", 1)[0].splitlines())


def test_the_header_states_the_corrected_authority() -> None:
    """Only what is checked or written: workspace-admin membership, App API rows, the validator's Genie turns."""

    header = _header()

    for claim in (
        "Every identity nightly uses is proven, before any test, not to be in the workspace `admins` group",
        "metastore admin, account admin and MODIFY or MANAGE grants are kept out by the owner's grants",
        "nightly writes product rows only through the App API, and the validator, read-only, opens Genie conversations directly",
        "outreach drafts and approvals of real leads, with their audit rows, which stay",
        "Lead outcomes, call dispositions, snoozes and loan-officer assignments are never written",
        "the suite test and the browser tests that write them are deselected",
        "a prod App skips them, and the gate's verdict says so",
        "tools/live_validation_gate.py --client <key>",
    ):
        assert claim in header, claim
    assert "holds no Databricks admin power" not in header
    assert "Parked" not in header and "admin PAT" not in header


# Each protection that left nightly with its home on main. A home is the
# step file that runs it under the deployer in scripts/deploy.sh, at deploy
# time only.
HOMES = {
    "tools.verify_deployed_app_contract": "deploy_step_agentic_cutover.sh",
    "tools.databricks.export_gateway_runtime_contract": "deploy_step_agentic_cutover.sh",
    "tools.databricks.verify_verifier_identity_boundary": "deploy_verifier_gateway_lifecycle.sh",
    "tools.databricks.run_agent_eval": "deploy_step_agent_eval_and_smoke.sh",
    "reconcile_reviewed_function_execute_grants": "deploy_governed_phases_lifecycle.sh",
}
# Each one with no home yet: never silently dropped, listed in docs/deployment.md.
# The delayed grant retry and the rolling proof refresh are gaps although the
# deploy runs each tool once: what left nightly is the re-run after it.
GAPS = (
    "the delayed AI Gateway inference-table grant retry",
    "tools.databricks.grant_ai_gateway_inference_table",
    "the rolling exact-proof refresh",
    "verify-pending",
    "tests/integration/test_lifecycle_delta_replay_live.py",
    "tests/integration/test_campaign_treatment_at_cap_live.py",
    "tests/integration/test_lineage_manifest_live.py",
    "test_live_saved_holdout_and_household_dedup_exclude_non_treatment_borrowers",
    "test_live_duplicate_outcome_and_disposition_replay_without_breaker_trip",
    "on a `prod` App, the whole App API mutation suite",
    "--target warehouse-real",
    "--target lakebase-real",
)


def _doc_part(start: str, end: str) -> str:
    doc = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    section = doc[doc.index("## Live validation per client") :]
    section = section[: section.find("\n## ", 1)]
    return section[section.index(start) : section.index(end)]


def test_each_protection_that_left_nightly_has_a_home_or_a_listed_gap() -> None:
    text = _text()
    homes = _doc_part("**What left nightly, and its home.**", "**Gaps: no home yet.**")
    gaps = _doc_part("**Gaps: no home yet.**", "**Supply chain.**")
    for command, home in HOMES.items():
        assert command not in text, command
        assert command in (REPO / "scripts" / "lib" / home).read_text(encoding="utf-8"), (command, home)
        assert command in homes and home in homes, command
    assert "at deploy time only" in homes
    for gap in GAPS:
        assert gap in gaps, gap
    # A gap is never also claimed as homed.
    for gap in ("grant_ai_gateway_inference_table", "verify_ai_gateway_exact_proof"):
        assert gap not in homes and gap not in text, gap


# ---------------------------------------------------------------- the live browser suites


def test_live_playwright_executes_live_hardening_and_excludes_mocked_console() -> None:
    run = _step(BROWSER_JOB, "Run real-UC Playwright specs")["run"]

    assert _jobs()[BROWSER_JOB]["env"]["E2E_LIVE"] == "1"
    assert "tests/e2e/live_hardening_regressions.spec.ts" in run and "tests/e2e/layout-stability.spec.ts" in run
    assert "tests/e2e/console-layout.spec.ts" not in run and "--list" not in run and "E2E_LAYOUT_MOCK" not in run
    assert "page.route('**/api/**'" in CONSOLE_LAYOUT_SPEC.read_text(encoding="utf-8")


def test_mocked_canaries_are_explicitly_separate_from_real_uc_proof() -> None:
    real = _step(BROWSER_JOB, "Run real-UC Playwright specs")
    scatter = _step(BROWSER_JOB, "Run mocked economics scatter response-shape canary")
    layout = _step(BROWSER_JOB, "Run mocked Genie proof layout canary")
    spec = ECONOMICS_SCATTER_SPEC.read_text(encoding="utf-8")

    assert "env" not in real and scatter["env"] == {"ECONOMICS_SCATTER_MOCKED": "1"}
    assert layout["env"] == {"GENIE_PROOF_LAYOUT_MOCKED": "1"}
    # Unanchored: Playwright greps "<project> <file> <describe> <title>", so the
    # `^mocked` this pinned matched no test and failed the job with "No tests
    # found" (tests/unit/test_nightly_workflow_writes.py selects through it).
    assert "--grep 'mocked response-shape canary:'" in scatter["run"]
    assert "test.skip(" in spec and "!MOCKED_POINTS_CANARY" in spec


def test_live_failure_artifacts_stay_off_unless_an_operator_enables_them() -> None:
    uploads = [step for job in _jobs().values() for step in job["steps"] if "upload-artifact" in step.get("uses", "")]

    assert uploads and all("env.E2E_LIVE_FAILURE_ARTIFACTS == '1'" in step["if"] for step in uploads)


def test_real_genie_browser_proof_executes_bound_native_follow_up() -> None:
    spec = REAL_DATA_SPEC.read_text(encoding="utf-8")

    assert "genie/message/submit" in spec
    assert "submit.request().postDataJSON()" in spec
    assert "followUpRequest.conversation_id" in spec
    assert "toBe(firstConversationId)" in spec
    assert "standalone native Genie follow-up" in spec


def test_live_genie_campaign_action_archives_the_exact_created_campaign() -> None:
    spec = REAL_DATA_SPEC.read_text(encoding="utf-8")
    proof = LIVE_CAMPAIGN_PROOF.read_text(encoding="utf-8")
    proof_test = LIVE_CAMPAIGN_PROOF_TEST.read_text(encoding="utf-8")
    action_pos = spec.index("ask-genie: dynamic chart, proof drawer, and governed action confirmation")
    action_block = spec[action_pos : spec.index("\n  test(", action_pos + 1)]

    for needle in (
        "export async function archiveLiveCampaign",
        "export async function reconcileGenieCampaignAction",
        "Authorization: `Bearer ${options.adminBearer}`",
        "expected_status: currentStatus",
        "final GET ${confirmed.status()}",
        "assertExpectedNameAfterCleanup",
    ):
        assert needle in proof, needle
    for needle in (
        "const actionRequest = page.waitForRequest",
        "LIVE_CAMPAIGN_RUN_MARKER",
        "submittedRequest.postDataJSON()",
        "typeof actionPayload.campaign_id === 'string'",
        "finally",
        "reconcileGenieCampaignAction(page.request, {",
        "archiveLiveCampaign(page.request, {",
        "expectedName: `Genie strategy draft ${LIVE_CAMPAIGN_RUN_MARKER}`",
    ):
        assert needle in action_block, needle
    assert "AUTH_HEADERS['X-MIP-Live-Campaign-Run-Marker']" in spec
    for needle in ("response lost", "response(503", "truncated response", "campaign marker was not persisted"):
        assert needle in proof_test, needle


def test_native_genie_browser_gate_requires_live_governed_turn_and_visible_trust() -> None:
    text = REAL_DATA_SPEC.read_text(encoding="utf-8")
    native_pos = text.index("ask-genie: native Conversation API turn renders governed proof and feedback")
    native_block = text[native_pos : text.index("\n  test(", native_pos + 1)]
    helper_pos = text.index("async function expectLiveGenieUi")
    helper_block = text[helper_pos : text.index("\n}\n\ntype MapDrillTarget", helper_pos) + 2]
    turn_helper_block = text[text.index("function expectLiveGenieTurn") : helper_pos]

    for needle in ("NATIVE_GENIE_CONVERSATION_QUESTION", "expect(payload.source", ").toBe('genie')", "expectLiveGenieTurn(payload"):
        assert needle in native_block, needle
    for needle in (
        "conversation_id",
        "genie_status",
        "toBe('COMPLETED')",
        "completed native turn must expose public API reasoning summaries",
        "completed native turn must expose follow-up suggestions",
        "proof must preserve the same public reasoning summaries",
    ):
        assert needle in turn_helper_block, needle
    for needle in (
        "genie-feedback-up",
        "genie-feedback-down",
        "Answer source: Databricks Genie Conversation API",
        "Public Preview reasoning returned by the API must render",
        "API follow-up suggestions must render as actions",
    ):
        assert needle in helper_block, needle
    assert "test.skip(" not in native_block


def test_deterministic_genie_has_no_native_reasoning_or_follow_ups() -> None:
    text = REAL_DATA_SPEC.read_text(encoding="utf-8")
    canonical_pos = text.index("genie FAB returns a non-empty answer and source chip opens lineage")
    canonical_block = text[canonical_pos : text.index("\n  test(", canonical_pos + 1)]

    assert ").toBe('trusted_sql')" in canonical_block
    # Genie-authored fields only ever appear when a live turn produced them.
    assert "canonicalPayload.genie_status && canonicalPayload.message_id" in canonical_block
    for needle in (
        "a deterministic answer with no live turn must not expose reasoning summaries",
        "a deterministic answer with no live turn must not fabricate proof reasoning",
        "a deterministic answer with no live turn must not claim Genie follow-ups",
        "a deterministic answer with no live turn must not render a reasoning disclosure",
    ):
        assert needle in canonical_block, needle
    assert "test.skip(" not in canonical_block


def test_live_browser_specs_pin_campaign_role_segment_and_catalog_proofs() -> None:
    real_data = REAL_DATA_SPEC.read_text(encoding="utf-8")
    hardening = LIVE_HARDENING_SPEC.read_text(encoding="utf-8")

    for needle in (
        "/api/v1/admin/capabilities?live=1",
        "a claimable Supervisor capability must produce the AI campaign recommendation",
        "recommendation.performance_status",
        "campaign response must carry governed evidence",
        "clicking a lineage asset must open its exact Catalog Explorer destination",
    ):
        assert needle in real_data, needle
    role_pos = hardening.index("admin and non-admin identities enforce navigation and activity boundaries")
    role_block = hardening[role_pos : hardening.index("\ntest(", role_pos + 1)]
    for needle in (
        "/api/session",
        "/api/audit/my-events?limit=8",
        "/api/audit/events?limit=1",
        "non-admin global activity lookup must fail closed",
        "admin global activity lookup",
        "toHaveURL(/\\/admin-config$/)",
    ):
        assert needle in role_block, needle
    assert "test.skip(" not in role_block
    segment_pos = hardening.index("segment any/all API counts are de-duplicated and intersection-safe")
    segment_block = hardening[segment_pos : hardening.index("\ntest(", segment_pos + 1)]
    for needle in ("itm + equity - allMode", "new Set(borrowerIds).size", "live proof requires a nonempty ITM/equity intersection"):
        assert needle in segment_block, needle


def test_the_failure_issue_names_the_client_and_never_a_refused_dispatch() -> None:
    notify = _jobs()["notify-on-failure"]

    assert _needs(notify) == {"parity-live", BROWSER_JOB, "kill-drill-simulated", "genie-fuzz-standard"}
    assert notify["permissions"] == {"contents": "read", "issues": "write"}
    (step,) = notify["steps"]
    assert step["env"] == {"MIP_DISPATCH_CLIENT": "${{ inputs.client }}"}
    assert "live validation (${client})" in step["with"]["script"]
    assert "environment" not in notify
