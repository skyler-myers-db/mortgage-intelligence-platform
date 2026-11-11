"""What nightly.yml writes, where each check runs, and the docs that describe it (M1-PR8).

The companion of tests/unit/test_nightly_workflow_contract.py:

* no test nightly runs writes a lead outcome, disposition, snooze or
  assignment (tests/fixtures/nightly_write_scan.py), and a ``prod`` App has a
  path to a green run;
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

import pytest
import yaml

from tests.fixtures import nightly_write_scan as scan
from tools import live_validation_gate as gate
from tools.clients import validation_app
from tools.clients import validation_session as vs

REPO = Path(__file__).resolve().parents[2]
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"
GENIE_EVAL = REPO / "tools" / "genie_eval.py"
BROWSER_JOB = "playwright-e2e-live"
MUTATION_STEP = "App API workflow contracts"
FRONTEND = REPO / "frontend"
DEPLOYMENT_DOC = REPO / "docs" / "deployment.md"
REAL_UC_STEP = "Run real-UC Playwright specs"
# The suites' own refusal of any App that is not dev or sandbox.
APP_ENV_GUARD = re.compile(r"app_env[^\n]*\bin \{([^}]*)\}")
# The tests that write a lead's sales state, deselected on every install (docs/deployment.md, gaps).
SUITE_WRITERS = {("tests/integration/test_lakebase_idempotency_live.py", "test_live_duplicate_outcome_and_disposition_replay_without_breaker_trip")}
SPEC_WRITERS = {
    ("tests/e2e/movement_closed_loop_regressions.spec.ts", "closed-loop outcome labels reject PII/raw names and summarize only governed aliases"),
    ("tests/e2e/movement_closed_loop_regressions.spec.ts", "duplicate disposition request ids replay without opening the Lakebase breaker"),
    ("tests/e2e/movement_closed_loop_regressions.spec.ts", "simultaneous outcome idempotency race produces one durable outcome without a breaker cascade"),
    ("tests/e2e/real_data.spec.ts", "sales outcomes: live manual import writes Lakebase ledger and Lead Queue explains status"),
}


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


# ---------------------------------------------------------------- what the tests nightly runs write


def _quoted(flag: str, run: str) -> str | None:
    match = re.search(rf"{flag}\s+(['\"])(.*?)\1", run)
    return match[2] if match else None


def _playwright_steps(jobs: dict[str, dict[str, Any]] | None = None) -> list[tuple[str, list[str], str | None, str | None]]:
    """(step, specs, --grep, --grep-invert) for each step that runs ``npx playwright test``."""

    return [
        (step["name"], re.findall(r"tests/e2e/[A-Za-z0-9_.-]+\.spec\.ts", step["run"]), _quoted("--grep", step["run"]), _quoted("--grep-invert", step["run"]))
        for data in (jobs or _jobs()).values()
        for step in data["steps"]
        if "npx playwright test" in step.get("run", "")
    ]


def _all_findings(jobs: dict[str, dict[str, Any]]) -> tuple[list[str], set[tuple[str, str]], set[tuple[str, str]]]:
    """(findings, suite writers, spec writers) over every pytest, Playwright and tools module nightly runs."""

    findings: list[str] = []
    suite_writers: set[tuple[str, str]] = set()
    spec_writers: set[tuple[str, str]] = set()
    modules: set[str] = set()
    for data in jobs.values():
        for step in data["steps"]:
            run = step.get("run", "")
            modules.update(re.findall(r"python -m (tools(?:\.[a-z0-9_]+)+)", run))
            if "pytest" in run:
                deselected = _deselected(run)
                for suite in sorted(_suites(run)):
                    found, writers = scan.python_findings(REPO, suite, {test for path, test in deselected if path == suite})
                    findings += found
                    suite_writers.update((suite, test) for test in writers)
    for _name, specs, grep, grep_invert in _playwright_steps(jobs):
        for spec in specs:
            found, writers = scan.spec_findings(FRONTEND, spec, grep, grep_invert)
            findings += found
            titles = {test.title for test in scan.spec_calls((FRONTEND / spec).read_text(encoding="utf-8")) if test.kind == "test"}
            spec_writers.update((spec, title) for title in titles if any(writer.endswith(f" {title}") for writer in writers))
    for module in sorted(modules):
        path = scan._module_path(REPO, module)
        assert path is not None, module
        findings += scan.whole_module_findings(REPO, [path])
    return findings, suite_writers, spec_writers


def test_no_test_nightly_runs_writes_a_lead_outcome_disposition_snooze_or_assignment() -> None:
    """Every pytest suite, Playwright spec and tools module a step runs, with their imports and conftests.

    None of these writes carries the run marker, and no cleanup undoes one (a
    funded-loan outcome reaches attribution, an application_submitted one the
    Sales ops KPIs), so the tests that make them are deselected on every install.
    """

    findings, suite_writers, spec_writers = _all_findings(_jobs())

    assert suite_writers == SUITE_WRITERS, "positive control: the suite scan finds the outcome, disposition and assignment replay"
    assert spec_writers == SPEC_WRITERS, "positive control: the spec scan finds the four browser tests that write"
    assert findings == []


def test_the_real_uc_step_deselects_each_browser_writer_and_nothing_else() -> None:
    """Literal alternatives only, so an anchor or a class can never make one match more or less than Playwright does."""

    (step,) = [each for each in _playwright_steps() if each[0] == REAL_UC_STEP]
    _name, specs, grep, grep_invert = step
    assert grep is None and grep_invert is not None
    alternatives = grep_invert.split("|")
    assert all(re.fullmatch(r"[A-Za-z0-9 :,'-]+", alternative) for alternative in alternatives), alternatives
    titles = [(spec, title) for spec in specs for title in scan.spec_grep_titles(FRONTEND, spec)]
    for alternative in alternatives:
        matched = [(spec, title) for spec, title in titles if scan.grep_matches(alternative, title)]
        assert len(matched) == 1, (alternative, matched)
        assert any(matched[0][1].endswith(f" {title}") and matched[0][0] == spec for spec, title in SPEC_WRITERS), matched
    assert len(alternatives) == len(SPEC_WRITERS)
    gaps = DEPLOYMENT_DOC.read_text(encoding="utf-8").split("**Gaps: no home yet.**", 1)[1].split("**Supply chain.**", 1)[0]
    for spec, title in SPEC_WRITERS:
        assert f"`{Path(spec).name}`" in gaps and f"\"{title}\"" in " ".join(gaps.split()), (spec, title)


def test_every_grep_of_a_playwright_step_selects_a_test() -> None:
    """Playwright greps " <project> <file> <describe...> <title>": an anchored pattern finds "No tests found" and fails the job."""

    assert not scan.grep_matches("^mocked response-shape canary:", scan.spec_grep_titles(FRONTEND, "tests/e2e/economics_scatter.spec.ts")[1]), (
        "positive control: the anchored canary grep nightly carried matches no test"
    )
    for name, specs, grep, grep_invert in _playwright_steps():
        titles = [title for spec in specs for title in scan.spec_grep_titles(FRONTEND, spec)]
        selected = [title for title in titles if (grep is None or scan.grep_matches(grep, title)) and not (grep_invert and scan.grep_matches(grep_invert, title))]
        assert selected, name


# ---------------------------------------------------------------- the scanners fail closed


PLANTED_PLUGIN = 'import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _write():\n    post("/api/leads/B/outcome")\n'


def _suite_tree(tmp_path: Path, suite: str, **modules: str) -> Path:
    for relative, text in {"tests/integration/test_planted_live.py": suite, **modules}.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("suite", "modules"),
    [
        ('FUNDED = "/api/leads/{}/outcome"\n\n\ndef test_reads():\n    post(FUNDED.format("B"))\n', {}),
        ("from tests.fixtures.planted import approve\n\n\ndef test_reads():\n    approve()\n", {"tests/fixtures/planted.py": 'def approve():\n    post("/api/leads/B/assign")\n'}),
        ("from .planted_helper import approve\n\n\ndef test_reads():\n    approve()\n", {"tests/integration/planted_helper.py": 'def approve():\n    post("/api/leads/B/outcome")\n'}),
        ('@pytest.fixture(autouse=True)\ndef _cleanup():\n    post("/api/leads/B/outcome")\n\n\ndef test_reads():\n    pass\n', {}),
        ('class TestPlanted:\n    def test_writes(self):\n        post("/api/leads/B/disposition")\n', {}),
        ('def _helper():\n    post("/api/leads/B/snooze")\n\n\ndef test_reads():\n    _helper()\n', {}),
        ('def _helper():\n    post("/api/sales/distribute")\n\n\ndef test_reads():\n    run(_helper)\n', {}),
        ('def _helper():\n    post("/api/loan-officers/assignments")\n\n\nALIAS = _helper\n', {}),
        ("def test_reads():\n    pass\n", {"tests/conftest.py": 'def pytest_sessionstart(session):\n    post("/api/leads/B/outcome")\n'}),
        # pytest runs these by name, and nothing in the module references them.
        ('def setup_module(module):\n    post("/api/leads/B/outcome")\n\n\ndef test_reads():\n    pass\n', {}),
        ('def teardown_module(module):\n    post("/api/leads/B/outcome")\n\n\ndef test_reads():\n    pass\n', {}),
        ('def setup_function(function):\n    post("/api/leads/B/snooze")\n\n\ndef test_reads():\n    pass\n', {}),
        ('def teardown_function(function):\n    post("/api/leads/B/assign")\n\n\ndef test_reads():\n    pass\n', {}),
        ('def pytest_generate_tests(metafunc):\n    post("/api/leads/B/disposition")\n\n\ndef test_reads():\n    pass\n', {}),
        ('def __getattr__(name):\n    post("/api/leads/B/outcome")\n\n\ndef test_reads():\n    pass\n', {}),
        ('def _helper():\n    post("/api/leads/B/outcome")\n\n\ndef _caller():\n    _helper()\n\n\ndef test_reads():\n    pass\n', {}),
        ('pytest_plugins = ["tests.fixtures.planted_plugin"]\n\n\ndef test_reads():\n    pass\n', {"tests/fixtures/planted_plugin.py": PLANTED_PLUGIN}),
        ('pytest_plugins = ("tests.fixtures.planted_plugin",)\n\n\ndef test_reads():\n    pass\n', {"tests/fixtures/planted_plugin.py": PLANTED_PLUGIN}),
        ('pytest_plugins = "tests.fixtures.other,tests.fixtures.planted_plugin"\n\n\ndef test_reads():\n    pass\n', {"tests/fixtures/planted_plugin.py": PLANTED_PLUGIN}),
        ("pytest_plugins = PLUGINS\n\n\ndef test_reads():\n    pass\n", {}),
        ("def test_reads():\n    pass\n", {"tests/conftest.py": 'pytest_plugins = ["tests.fixtures.planted_plugin"]\n', "tests/fixtures/planted_plugin.py": PLANTED_PLUGIN}),
    ],
    ids=[
        "module-constant",
        "imported-helper",
        "relative-import",
        "autouse-fixture",
        "class-method",
        "reached-helper",
        "helper-as-value",
        "aliased-helper",
        "conftest",
        "xunit-setup-module",
        "xunit-teardown-module",
        "xunit-setup-function",
        "xunit-teardown-function",
        "generate-tests-hook",
        "module-getattr",
        "helper-no-test-reaches",
        "pytest-plugins-list",
        "pytest-plugins-tuple",
        "pytest-plugins-comma-string",
        "pytest-plugins-unreadable",
        "conftest-pytest-plugins",
    ],
)
def test_the_suite_scan_finds_a_write_in_every_shape_a_call_graph_misses(tmp_path: Path, suite: str, modules: dict[str, str]) -> None:
    root = _suite_tree(tmp_path, suite, **modules)

    findings, _writers = scan.python_findings(root, "tests/integration/test_planted_live.py", set())

    assert findings


def test_a_write_pytest_runs_by_name_is_a_finding_even_when_a_deselected_test_calls_it(tmp_path: Path) -> None:
    """pytest runs ``setup_function`` around every test, the deselected one's call notwithstanding."""

    suite = 'def setup_function(function):\n    post("/api/leads/B/outcome")\n\n\ndef test_deselected():\n    setup_function(None)\n\n\ndef test_reads():\n    pass\n'
    root = _suite_tree(tmp_path, suite)

    findings, writers = scan.python_findings(root, "tests/integration/test_planted_live.py", {"test_deselected"})

    assert writers == ["test_deselected"] and len(findings) == 1 and "setup_function" in findings[0], findings


def test_the_suite_scan_allows_a_write_only_a_deselected_test_reaches_and_ignores_reads(tmp_path: Path) -> None:
    suite = (
        'def _inner():\n    post("/api/leads/B/outcome")\n\n\n'
        "def _helper():\n    _inner()\n\n\n"
        "def test_deselected():\n    _helper()\n\n\n"
        'def test_reads():\n    get("/api/sales/outcomes/summary")\n    get("/api/leads/B/assignment")\n    get("/api/geo/assignment-overlay")\n'
    )
    root = _suite_tree(tmp_path, suite)

    assert scan.python_findings(root, "tests/integration/test_planted_live.py", {"test_deselected"}) == ([], ["test_deselected"])
    assert scan.python_findings(root, "tests/integration/test_planted_live.py", set())[0], "the same write, not deselected"


SPEC_HEAD = "import { test } from '@playwright/test';\nconst API = process.env.MIP_API_URL;\n"


@pytest.mark.parametrize(
    ("spec", "helpers"),
    [
        ("async function write(request) {\n  await request.post(`${API}/api/leads/${id}/outcome`);\n}\ntest('reads', async ({ request }) => { await write(request); });\n", None),
        ("test.beforeEach(async ({ request }) => { await request.post('/api/leads/B/disposition'); });\ntest('reads', async () => {});\n", None),
        ("test('writes', async ({ request }) => { await request.post('/api/leads/B/assign'); });\n", None),
        ("import { write } from './planted_helpers';\ntest('reads', async ({ request }) => { await write(request); });\n", "export async function write(request) { await request.post('/api/leads/B/snooze'); }\n"),
        ("for (const kind of ['a', 'b']) {\n  test(`writes ${kind}`, async ({ request }) => { await request.post('/api/leads/B/outcome'); });\n}\n", None),
    ],
    ids=["module-helper", "hook", "running-test", "imported-helper", "generated-title"],
)
def test_the_spec_scan_finds_a_write_no_deselected_test_holds(tmp_path: Path, spec: str, helpers: str | None) -> None:
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "e2e" / "planted.spec.ts").write_text(SPEC_HEAD + spec, encoding="utf-8")
    if helpers is not None:
        (tmp_path / "tests" / "e2e" / "planted_helpers.ts").write_text(helpers, encoding="utf-8")

    findings, _writers = scan.spec_findings(tmp_path, "tests/e2e/planted.spec.ts", None, "a title no test has")

    assert findings


def test_the_spec_scan_attributes_a_write_to_the_one_test_that_holds_it(tmp_path: Path) -> None:
    """Strings, regex literals and template expressions full of brackets never move a test's bounds."""

    spec = SPEC_HEAD + (
        "test.describe('outer', () => {\n"
        "  test('first reads', async () => {\n"
        "    const re = /\\)\\}\\(/;\n"
        "    const text = `${ { close: '})' }.close }`;\n"
        "    const slash = 4 / 2;\n"
        "  });\n"
        "  test('second writes', async ({ request }) => {\n"
        "    await request.post(`${API}/api/leads/${'B'}/outcome`);\n"
        "  });\n"
        "});\n"
    )
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "e2e" / "planted.spec.ts").write_text(spec, encoding="utf-8")

    assert scan.spec_findings(tmp_path, "tests/e2e/planted.spec.ts", None, "second writes") == ([], [" project planted.spec.ts outer second writes"])
    findings, _writers = scan.spec_findings(tmp_path, "tests/e2e/planted.spec.ts", None, "first reads")
    assert len(findings) == 1 and "outer second writes" in findings[0], findings
    assert scan.spec_findings(tmp_path, "tests/e2e/planted.spec.ts", "first reads", None)[0] == [], "a --grep that leaves the writer out"


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
    # The gate reads this step's conclusion, so a prod App's skip is in the verdict.
    assert (_jobs()[BROWSER_JOB]["name"], _steps(BROWSER_JOB)[mutation]["name"]) == (gate.PLAYWRIGHT_JOB, gate.APP_API_MUTATION_STEP)
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
