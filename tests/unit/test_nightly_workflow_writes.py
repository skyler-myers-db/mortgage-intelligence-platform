"""What nightly.yml writes, where each check runs, and the docs that describe it (M1-PR8).

The companion of tests/unit/test_nightly_workflow_contract.py:

* the App API mutation suites write no lead outcome, disposition or
  assignment, and a ``prod`` App has a path to a green run;
* the Genie semantic scorecard runs where the warehouse env it needs is
  exported;
* the browser job mints only for the profile the validator jobs read, and
  its first mints run before any npm package code;
* no doc still describes the parked nightly or its real-infra drill job.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from tools.clients import validation_app
from tools.clients import validation_session as vs

REPO = Path(__file__).resolve().parents[2]
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"
GENIE_EVAL = REPO / "tools" / "genie_eval.py"
BROWSER_JOB = "playwright-e2e-live"
MUTATION_STEP = "App API workflow contracts"
# The suites' own refusal of any App that is not dev or sandbox.
APP_ENV_GUARD = re.compile(r"app_env[^\n]*\bin \{([^}]*)\}")
# What a lead outcome, call disposition or loan-officer assignment write calls.
UNMARKED_LEAD_WRITES = ("/outcome", "/disposition", "/assign")


def _jobs() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))["jobs"]


def _steps(job: str) -> list[dict[str, Any]]:
    return _jobs()[job]["steps"]


def _index(job: str, needle: str) -> int:
    return next(i for i, step in enumerate(_steps(job)) if needle in step.get("name", "") or needle in step.get("run", ""))


def _step(job: str, name: str) -> dict[str, Any]:
    (step,) = [step for step in _steps(job) if step.get("name") == name]
    return step


def _suites(run: str) -> set[str]:
    return set(re.findall(r"tests/integration/[a-z0-9_]+\.py", run))


def _deselected(run: str) -> set[tuple[str, str]]:
    return {(path, test) for path, test in re.findall(r"--deselect (tests/integration/[a-z0-9_]+\.py)::([a-z0-9_]+)", run)}


# ---------------------------------------------------------------- what the App API suites write


def _local_calls(tree: ast.Module) -> dict[str, set[str]]:
    """Each top-level function -> the top-level functions of the same file it calls."""

    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    return {
        node.name: {call.func.id for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in names}
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def _reach(calls: dict[str, set[str]], start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(calls.get(name, ()))
    return seen


def test_no_running_app_api_suite_test_writes_a_lead_outcome_disposition_or_assignment() -> None:
    """None of them carries the run marker, and no cleanup undoes them (a funded-loan outcome reaches attribution)."""

    run = _step(BROWSER_JOB, MUTATION_STEP)["run"]
    deselected = _deselected(run)
    writers: set[tuple[str, str]] = set()
    for suite in sorted(_suites(run)):
        source = (REPO / suite).read_text(encoding="utf-8")
        tree = ast.parse(source)
        bodies = {node.name: ast.get_source_segment(source, node) or "" for node in tree.body if isinstance(node, ast.FunctionDef)}
        calls = _local_calls(tree)
        for test in (name for name in bodies if name.startswith("test_")):
            reached = "\n".join(bodies[name] for name in _reach(calls, test))
            if any(path in reached for path in UNMARKED_LEAD_WRITES):
                writers.add((suite, test))
    assert ("tests/integration/test_lakebase_idempotency_live.py", "test_live_duplicate_outcome_and_disposition_replay_without_breaker_trip") in writers, (
        "positive control: the scan finds the outcome, disposition and assignment replay"
    )
    assert writers <= deselected, sorted(writers - deselected)


def test_a_prod_app_has_a_path_to_a_green_run() -> None:
    """Every suite nightly runs that refuses a non-dev/sandbox App runs only in the install-class-gated step."""

    install = _index(BROWSER_JOB, "Read the install class the App reports")
    mutation = _index(BROWSER_JOB, MUTATION_STEP)
    step = _steps(BROWSER_JOB)[install]
    assert install < mutation and step["id"] == "install-class"
    assert step["run"] == (
        'python -m tools.clients.validation_session install-class --token-env MIP_ADMIN_BEARER_TOKEN --github-output "$GITHUB_OUTPUT"'
    )
    assert _steps(BROWSER_JOB)[mutation]["if"] == f"steps.install-class.outputs.{validation_app.MUTATION_OUTPUT} == 'run'"
    guarded: dict[str, set[str]] = {}
    for job, data in _jobs().items():
        for each in data["steps"]:
            for suite in _suites(each.get("run", "")) if "pytest" in each.get("run", "") else ():
                found = APP_ENV_GUARD.findall((REPO / suite).read_text(encoding="utf-8"))
                if found:
                    guarded[suite] = {value.strip().strip("'\"") for match in found for value in match.split(",")}
                    assert (job, each.get("name")) == (BROWSER_JOB, MUTATION_STEP), (suite, job, each.get("name"))
    assert guarded, "positive control: the App API suites carry the app_env guard"
    assert all(values == set(validation_app.MUTATION_APP_ENVS) for values in guarded.values()), guarded
    # No browser spec refuses an App by its app_env.
    for spec in (REPO / "frontend" / "tests" / "e2e").glob("*.spec.ts"):
        assert "app_env" not in spec.read_text(encoding="utf-8"), spec.name


# ---------------------------------------------------------------- the Genie semantic scorecard


def _env_groups(function: str) -> list[set[str]]:
    """The env names ``function`` in tools/genie_eval.py reads, one group per alternative (``a or b``)."""

    tree = ast.parse(GENIE_EVAL.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)

    def name_of(call: ast.AST) -> str | None:
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "get"
            and ast.unparse(call.func.value) == "os.environ"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        ):
            return str(call.args[0].value)
        return None

    groups: list[set[str]] = []
    grouped: set[int] = set()
    for each in ast.walk(node):
        if isinstance(each, ast.BoolOp) and isinstance(each.op, ast.Or):
            names = {name for value in each.values if (name := name_of(value))}
            if names:
                groups.append(names)
                grouped.update(id(value) for value in each.values)
    groups += [{name} for each in ast.walk(node) if (name := name_of(each)) and id(each) not in grouped]
    return groups


def test_the_genie_scorecard_runs_where_its_warehouse_env_is_exported() -> None:
    """tools/genie_eval.py fails a canonical question when DATABRICKS_HOST, DATABRICKS_TOKEN or DATABRICKS_WAREHOUSE_ID is unset."""

    where = [job for job, data in _jobs().items() for step in data["steps"] if "tools/genie_eval.py" in step.get("run", "")]
    assert where == ["parity-live"], where
    groups = _env_groups("_warehouse_creds") + _env_groups("_configured_catalog")
    assert {"DATABRICKS_WAREHOUSE_ID"} in groups and {"MIP_DEFAULT_CATALOG"} in groups, groups
    for group in groups:
        assert group & set(vs.SESSION_EXPORTS), group
    session = _index("parity-live", "validation_session session")
    remint = _index("parity-live", "Remint the validator token before the Genie scorecard")
    scorecard = _index("parity-live", "Genie semantic eval scorecard (deployed app)")
    assert session < remint < scorecard
    run = _steps("parity-live")[scorecard]["run"]
    assert run.count('--token "$MIP_BEARER_TOKEN"') == 2 and "--soft" not in run
    assert "continue-on-error" not in _steps("parity-live")[scorecard]


# ---------------------------------------------------------------- the two environments and npm


def test_the_browser_job_mints_only_for_the_profile_the_validator_jobs_read() -> None:
    jobs = _jobs()
    parity = jobs["parity-live"]
    session = _steps("parity-live")[_index("parity-live", "validation_session session")]

    assert parity["outputs"] == {"profile_digest": "${{ steps.session.outputs.profile_digest }}"}
    assert session["id"] == "session" and session["run"].endswith('--github-output "$GITHUB_OUTPUT"')
    assert jobs[BROWSER_JOB]["needs"] == "parity-live"
    minting = [step for step in _steps(BROWSER_JOB) if "validation_session operators" in step.get("run", "")]
    assert minting and all(step["env"][vs.DIGEST_ENV] == "${{ needs.parity-live.outputs.profile_digest }}" for step in minting)


def test_the_first_mints_and_the_app_api_writes_run_before_any_npm_package_code() -> None:
    steps = _steps(BROWSER_JOB)
    first_npm = min(i for i, step in enumerate(steps) if re.search(r"\b(?:npm|npx)\b", step.get("run", "")))

    for needle in (
        "Mint per-run App bearers for the reserved operator identities",
        "Recover abandoned live campaign fixtures",
        "Authenticated non-admin smoke (M2M)",
        MUTATION_STEP,
        "Always archive run-marked live campaign fixtures",
    ):
        assert _index(BROWSER_JOB, needle) < first_npm, needle
    assert steps[first_npm]["run"] == "npm --prefix frontend ci --ignore-scripts --no-audit --no-fund"
    assert all("setup-node" not in step.get("uses", "") for step in steps[first_npm:])


def test_every_pip_install_in_nightly_takes_wheels_only() -> None:
    installs = [
        line.strip()
        for data in _jobs().values()
        for step in data["steps"]
        for line in step.get("run", "").splitlines()
        if re.match(r"\s*(?:python -m )?pip install ", line) and "--upgrade pip" not in line
    ]
    assert len(installs) == 6, installs
    assert all("--only-binary=:all:" in line for line in installs), installs


# ---------------------------------------------------------------- docs that described the parked nightly


def test_no_doc_describes_the_parked_nightly_or_its_real_infra_drill_job() -> None:
    docs = {
        path: (REPO / path).read_text(encoding="utf-8")
        for path in (".github/workflows/README.md", "docs/clients/github-environment.md", "docs/credential-kill-drill.md")
    }
    for path, text in docs.items():
        assert "nightly" in text, f"positive control: {path} describes nightly"
        for stale in (
            "run_real_drills",
            "kill-drill-real-infra",
            "`nightly.yml` is parked",
            "the parked `nightly.yml` jobs",
            "stays parked until M1-PR8",
            "still names it for its live checks",
            "real-infra drill jobs",
        ):
            assert stale not in text, (path, stale)
