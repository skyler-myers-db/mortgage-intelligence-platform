"""What nightly.yml writes, where each check runs, and the docs that describe it (M1-PR8).

The companion of tests/unit/test_nightly_workflow_contract.py:

* nothing nightly runs (a test, a script, an inline script) writes a lead
  outcome, disposition, snooze or assignment (tests/fixtures/nightly_write_scan.py
  and nightly_step_scan.py), and a ``prod`` App has a path to a green run;
* the Genie semantic scorecard runs where the warehouse env it needs is
  exported;
* the browser job mints only for the profile the validator jobs read, and
  its first mints run before any npm package code;
* no doc still describes the parked nightly or its real-infra drill job.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures import nightly_step_scan as step_scan
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


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))


def _jobs() -> dict[str, dict[str, Any]]:
    return _workflow()["jobs"]


def _steps(job: str) -> list[dict[str, Any]]:
    return _jobs()[job]["steps"]


def _index(job: str, needle: str) -> int:
    return next(i for i, step in enumerate(_steps(job)) if needle in step.get("name", "") or needle in step.get("run", ""))


def _step(job: str, name: str) -> dict[str, Any]:
    (step,) = [step for step in _steps(job) if step.get("name") == name]
    return step


def _suites(run: str) -> set[str]:
    return set(re.findall(r"tests/integration/[a-z0-9_]+\.py", run))


# ---------------------------------------------------------------- what the tests nightly runs write


def _scanned(workflow: dict[str, Any] | None = None) -> list[tuple[str | None, step_scan.StepScan]]:
    """(step name, what its ``run:`` runs, read in its shell and directory) for every step."""

    workflow = workflow or _workflow()
    return [
        (step.get("name"), step_scan.step_findings(REPO, f"{job}: {step.get('name', step.get('uses'))}", step.get("run", ""), **step_scan.step_context(workflow, data, step)))
        for job, data in workflow["jobs"].items()
        for step in data["steps"]
    ]


def _playwright_steps(workflow: dict[str, Any] | None = None) -> list[tuple[str | None, tuple[str, ...], str | None, str | None]]:
    """(step, specs, --grep, --grep-invert) for each ``playwright test`` a step runs."""

    return [(name, run.specs, run.grep, run.grep_invert) for name, step in _scanned(workflow) for run in step.playwright]


def _run_findings(root: Path, step: step_scan.StepScan) -> tuple[list[str], set[tuple[str, str]], set[tuple[str, str]]]:
    """(findings, suite writers, spec writers) for one step: its own findings, each suite and spec it runs."""

    findings = list(step.findings)
    suite_writers: set[tuple[str, str]] = set()
    spec_writers: set[tuple[str, str]] = set()
    for run in step.pytest:
        for suite in run.suites:
            found, writers = scan.python_findings(root, suite, {test for path, test in run.deselected if path == suite})
            findings += found
            suite_writers.update((suite, test) for test in writers)
    frontend = root / "frontend"
    for run in step.playwright:
        for spec in run.specs:
            found, writers = scan.spec_findings(frontend, spec, run.grep, run.grep_invert)
            findings += found
            titles = {test.title for test in scan.spec_calls((frontend / spec).read_text(encoding="utf-8")) if test.kind == "test"}
            spec_writers.update((spec, title) for title in titles if any(writer.endswith(f" {title}") for writer in writers))
    return findings, suite_writers, spec_writers


def _all_findings(workflow: dict[str, Any]) -> tuple[list[str], set[tuple[str, str]], set[tuple[str, str]], list[Path]]:
    """(findings, suite writers, spec writers, the files steps run) over everything nightly runs."""

    findings = step_scan.workflow_string_findings(workflow)
    suite_writers: set[tuple[str, str]] = set()
    spec_writers: set[tuple[str, str]] = set()
    files: list[Path] = []
    for _name, step in _scanned(workflow):
        found, suites, specs = _run_findings(REPO, step)
        findings += found
        suite_writers |= suites
        spec_writers |= specs
        files += step.files
    findings += scan.whole_module_findings(REPO, files)
    return findings, suite_writers, spec_writers, files


def test_no_test_nightly_runs_writes_a_lead_outcome_disposition_snooze_or_assignment() -> None:
    """Every pytest suite, Playwright spec, script, inline script and module a step runs, with what they load.

    None of these writes carries the run marker, and no cleanup undoes one (a
    funded-loan outcome reaches attribution, an application_submitted one the
    Sales ops KPIs), so the tests that make them are deselected on every install.
    """

    findings, suite_writers, spec_writers, _files = _all_findings(_workflow())

    assert suite_writers == SUITE_WRITERS, "positive control: the suite scan finds the outcome, disposition and assignment replay"
    assert spec_writers == SPEC_WRITERS, "positive control: the spec scan finds the four browser tests that write"
    assert findings == []


def test_the_scan_reads_the_scripts_nightly_runs_by_path_and_what_they_run() -> None:
    """``python tools/x.py``, ``bash tools/x.sh``, a script a script runs or sources, a tool a module runs as a child."""

    loaded = {str(path.relative_to(REPO)) for path in scan.loaded_files(REPO, _all_findings(_workflow())[3])}

    assert {
        "tools/wait_app_ready.py",
        "tools/genie_eval.py",
        "tools/kill_drill/run_drill.sh",
        "tools/kill_drill/lib_warehouse_on_demand.sh",
        "tools/kill_drill/real_infra.py",
        "tools/cleanup_live_campaign_fixtures.py",
        "tools/clients/validation_session.py",
        "tools/oauth_m2m_mint.py",
    } <= loaded, loaded


M2M_SMOKE = "Authenticated non-admin smoke (M2M)"
GENIE_SCORECARD = "Genie semantic eval scorecard (deployed app)"
ROUND_TRIP = "Lakebase post-activation read path"
ROUND_TRIP_RUN = "pytest -q tests/integration/test_lakebase_round_trip.py"
PROOF_CANARY = "Run mocked Genie proof layout canary"
PROOF_CANARY_RUN = "npx playwright test \\\n  tests/e2e/genie_proof_layout.spec.ts \\\n"
MOVEMENT_SPEC = "tests/e2e/movement_closed_loop_regressions.spec.ts"
SUITE_WRITER = "tests/integration/test_lakebase_idempotency_live.py::test_live_duplicate_outcome_and_disposition_replay_without_breaker_trip writes"
PLANTED_POST = 'urllib.request.urlopen(urllib.request.Request(app_url + "/api/leads/B-0000000000000/outcome", method="POST", data=b"{}"))\n'


def _edited(step: str, old: str, new: str, **keys: str) -> dict[str, Any]:
    workflow = _workflow()
    (target,) = [each for data in workflow["jobs"].values() for each in data["steps"] if each.get("name") == step]
    assert target["run"].count(old) == 1, (step, old)
    target["run"] = target["run"].replace(old, new)
    target.update(keys)
    return workflow


@pytest.mark.parametrize(
    ("step", "old", "new", "expected"),
    [
        (M2M_SMOKE, "if failed:\n", PLANTED_POST + "if failed:\n", M2M_SMOKE),
        (GENIE_SCORECARD, "tools/wait_app_ready.py", "tools/wait_app_ready_moved.py", "tools/wait_app_ready_moved.py, which this scan cannot read"),
        (GENIE_SCORECARD, "python tools/genie_eval.py", 'python "$RUNNER_TEMP/genie_eval.py"', "$RUNNER_TEMP/genie_eval.py, which this scan cannot read"),
        # What a step selects by directory, marker, testpaths or an npm script, not by file.
        (ROUND_TRIP, ROUND_TRIP_RUN, "pytest -q tests/integration/", "pytest selects tests/integration/, which is no suite file"),
        (ROUND_TRIP, ROUND_TRIP_RUN, "python -m pytest -q tests/integration -k lakebase", "pytest selects tests/integration, which is no suite file"),
        ("Run deep Genie fuzz", "pytest -m genie_fuzz_deep tests/integration/test_genie_fuzz.py -q", "pytest -m genie_fuzz_deep -q", "pytest names no suite file"),
        (PROOF_CANARY, PROOF_CANARY_RUN, "npx playwright test \\\n", "playwright test names no spec"),
        (PROOF_CANARY, "tests/e2e/genie_proof_layout.spec.ts", "tests/e2e/", "playwright test selects tests/e2e/, which is no spec filter"),
        (PROOF_CANARY, PROOF_CANARY_RUN, f"npm run e2e:ci -- \\\n  {MOVEMENT_SPEC} \\\n", f"{MOVEMENT_SPEC}:"),
        # A second command in the same step: its own --deselect and --grep-invert, not the first's.
        (MUTATION_STEP, "_breaker_trip\n", "_breaker_trip\npytest -q tests/integration/test_lakebase_idempotency_live.py\n", SUITE_WRITER),
        (REAL_UC_STEP, "--reporter=list --workers=1\n", f"--reporter=list --workers=1\nnpx playwright test {MOVEMENT_SPEC}\n", f"{MOVEMENT_SPEC}:"),
    ],
    ids=[
        "heredoc-writes",
        "path-form-tool-moved",
        "script-outside-the-repo",
        "pytest-by-directory",
        "python-m-pytest-by-directory-and-k",
        "pytest-by-marker-alone",
        "playwright-every-spec",
        "playwright-by-directory",
        "playwright-through-an-npm-script",
        "second-pytest-without-the-deselect",
        "second-playwright-without-the-grep-invert",
    ],
)
def test_the_nightly_scan_reports_a_write_or_an_unread_script_in_a_step(step: str, old: str, new: str, expected: str) -> None:
    findings = _all_findings(_edited(step, old, new))[0]

    assert findings and all(expected in finding for finding in findings), findings


@pytest.mark.parametrize(
    ("step", "old", "new"),
    [
        # A frontend step runs a repo tool the way frontend/package.json does (budget, gen:api).
        (PROOF_CANARY, "--reporter=list --workers=1", "--reporter=list --workers=1\nnode ../tools/check_frontend_budgets.mjs --report-only"),
        (PROOF_CANARY, "--reporter=list --workers=1", "--reporter=list --workers=1\npython3 ../tools/gen_api_types.py --check"),
        (ROUND_TRIP, ROUND_TRIP_RUN, ROUND_TRIP_RUN + " tests/integration/test_genie_live.py"),
        (ROUND_TRIP, ROUND_TRIP_RUN, "python --version\npython -m pip list --format=freeze | head -5\n" + ROUND_TRIP_RUN),
        (ROUND_TRIP, ROUND_TRIP_RUN, ROUND_TRIP_RUN + '\npython -m json.tool "$RUNNER_TEMP/mip-validate-vars.json" > /dev/null'),
        (PROOF_CANARY, PROOF_CANARY_RUN, "npm run e2e:ci -- \\\n  tests/e2e/genie_proof_layout.spec.ts \\\n"),
    ],
    ids=["node-a-repo-tool-from-frontend", "python-a-repo-tool-from-frontend", "a-read-only-suite", "diagnostics", "stdlib-json-tool", "playwright-through-an-npm-script"],
)
def test_the_nightly_scan_passes_an_edit_that_runs_nothing_that_writes(step: str, old: str, new: str) -> None:
    findings, suite_writers, spec_writers, _files = _all_findings(_edited(step, old, new))

    assert (findings, suite_writers, spec_writers) == ([], SUITE_WRITERS, SPEC_WRITERS)


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


# The decorator opens its own literal: "\n@pytest" reads as an email address to the identifier ratchet.
PLANTED_PLUGIN = "import pytest\n\n\n" '@pytest.fixture(autouse=True)\ndef _write():\n    post("/api/leads/B/outcome")\n'


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


SPEC_WRITE = "export async function write(request) { await request.post('/api/leads/B/snooze'); }\n"
SPEC_READS = "test('reads', async ({ request }) => { await write(request); });\n"


@pytest.mark.parametrize(
    ("spec", "files"),
    [
        ("async function write(request) {\n  await request.post(`${API}/api/leads/${id}/outcome`);\n}\ntest('reads', async ({ request }) => { await write(request); });\n", {}),
        ("test.beforeEach(async ({ request }) => { await request.post('/api/leads/B/disposition'); });\ntest('reads', async () => {});\n", {}),
        ("test('writes', async ({ request }) => { await request.post('/api/leads/B/assign'); });\n", {}),
        ("import { write } from './planted_helpers';\n" + SPEC_READS, {"tests/e2e/planted_helpers.ts": SPEC_WRITE}),
        ("for (const kind of ['a', 'b']) {\n  test(`writes ${kind}`, async ({ request }) => { await request.post('/api/leads/B/outcome'); });\n}\n", {}),
        # real_data.spec.ts imports '../../src/routes/analytics.lib': the file is analytics.lib.ts, beside analytics.tsx.
        ("import { write } from '../../src/routes/planted.lib';\n" + SPEC_READS, {"src/routes/planted.lib.ts": SPEC_WRITE, "src/routes/planted.tsx": "export const Route = 1;\n"}),
        ("import { write } from './planted_dir';\n" + SPEC_READS, {"tests/e2e/planted_dir/index.tsx": SPEC_WRITE}),
        ("import { write } from './planted_helpers.js';\n" + SPEC_READS, {"tests/e2e/planted_helpers.ts": SPEC_WRITE}),
        ("const { write } = require('./planted_helpers');\n" + SPEC_READS, {"tests/e2e/planted_helpers.ts": SPEC_WRITE}),
        ("import { write } from './planted_helpers_moved';\n" + SPEC_READS, {"tests/e2e/planted_helpers.ts": SPEC_WRITE}),
        ("import { write } from './planted_helpers';\n" + SPEC_READS, {"tests/e2e/planted_helpers.ts": "export { write } from './planted_missing';\n"}),
        ("const kind = 'helpers';\nconst { write } = await import(`./planted_${kind}`);\n" + SPEC_READS, {"tests/e2e/planted_helpers.ts": SPEC_WRITE}),
    ],
    ids=[
        "module-helper",
        "hook",
        "running-test",
        "imported-helper",
        "generated-title",
        "dotted-module-import",
        "directory-index-tsx",
        "js-specifier",
        "require-import",
        "unresolved-import",
        "unresolved-import-in-a-helper",
        "import-built-at-run-time",
    ],
)
def test_the_spec_scan_finds_a_write_no_deselected_test_holds(tmp_path: Path, spec: str, files: dict[str, str]) -> None:
    for relative, text in {"tests/e2e/planted.spec.ts": SPEC_HEAD + spec, **files}.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text, encoding="utf-8")

    findings, _writers = scan.spec_findings(tmp_path, "tests/e2e/planted.spec.ts", None, "a title no test has")

    assert findings


def test_the_spec_scan_reads_imports_from_code_and_not_from_comments_or_strings(tmp_path: Path) -> None:
    """A comment naming a retired helper, a doc comment's import example, an import in a string, ``Array.from``."""

    stale = (
        "// openEvidenceDrawer moved here from './evidenceHelpers', which is retired.\n"
        "/**\n *   import { expect, test } from './test';\n */\n"
        "const example = \"import { gone } from './nowhere'\";\n"
        "const parts = Array.from('./nowhere');\n"
    )
    files = {
        "tests/e2e/planted.spec.ts": SPEC_HEAD + "import { label } from './planted_helpers';\n" + stale + "test('reads', async () => { label(); });\n",
        "tests/e2e/planted_helpers.ts": stale + "export const label = () => 'reads';\n",
    }
    for relative, text in files.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text, encoding="utf-8")

    assert scan.spec_findings(tmp_path, "tests/e2e/planted.spec.ts", None, None) == ([], [])
    assert scan._relative_imports(tmp_path / "tests/e2e/planted.spec.ts", files["tests/e2e/planted.spec.ts"]) == ([tmp_path / "tests/e2e/planted_helpers.ts"], []), (
        "positive control: the real import still resolves"
    )


def test_the_spec_scan_reads_the_file_a_dotted_import_names_and_not_its_sibling(tmp_path: Path) -> None:
    """``planted.lib`` is planted.lib.ts: a write in planted.tsx, which no spec imports, is no finding."""

    spec = SPEC_HEAD + "import { label } from '../../src/routes/planted.lib';\ntest('reads', async () => { label(); });\n"
    files = {
        "tests/e2e/planted.spec.ts": spec,
        "src/routes/planted.lib.ts": "export const label = () => 'reads';\n",
        "src/routes/planted.tsx": "export const write = (request) => request.post('/api/leads/B/outcome');\n",
    }
    for relative, text in files.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text, encoding="utf-8")

    assert scan.spec_findings(tmp_path, "tests/e2e/planted.spec.ts", None, None) == ([], [])


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


TOOL_WRITE = 'import urllib.request\n\n\ndef main():\n    urllib.request.urlopen("http://app/api/leads/B/outcome", data=b"{}")\n'
SHELL_WRITE = 'curl -X POST "$APP_URL/api/leads/B/disposition"\n'


def _step_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text, encoding="utf-8")
    return tmp_path


def _step_scan(root: Path, run: str, **context: str) -> list[str]:
    step = step_scan.step_findings(root, "planted: step", run, **context)
    return _run_findings(root, step)[0] + step_scan.workflow_string_findings({"run": run}) + scan.whole_module_findings(root, step.files)


READ_SUITE = {"tests/integration/test_reads.py": "def test_reads():\n    pass\n"}
JS_WRITE = "export const write = (request) => request.post('/api/leads/B/outcome');\n"
PLANTED = {"scripts/planted.py": TOOL_WRITE}
PLANTED_TOOL = {"tools/planted.py": TOOL_WRITE}
WRITER_SUITE = {"tests/integration/test_planted_live.py": 'def test_writes():\n    post("/api/leads/B/outcome")\n'}


def _package(**scripts: str) -> dict[str, str]:
    return {"frontend/package.json": json.dumps({"scripts": scripts})}


def _case(run: str, files: dict[str, str], expected: str, id: str, **context: str) -> Any:  # noqa: A002 - pytest's own name
    return pytest.param(run, files, expected, context, id=id)


@pytest.mark.parametrize(
    ("run", "files", "expected", "context"),
    [
        _case("python tools/planted.py \\\n  --base \"$MIP_APP_URL\"\n", PLANTED_TOOL, "tools/planted.py:5: /outcome", "path-form-python"),
        _case("bash tools/planted.sh --target genie\n", {"tools/planted.sh": SHELL_WRITE}, "tools/planted.sh:1: /disposition", "path-form-bash"),
        _case("bash tools/drill.sh\n", {"tools/drill.sh": '"$pybin" "$REPO_ROOT/tools/planted.py" stop\n', **PLANTED_TOOL}, "tools/planted.py:5", "bash-runs-a-tool-by-path"),
        _case("bash tools/drill.sh\n", {"tools/drill.sh": '. "$REPO_ROOT/tools/lib.sh"\n', "tools/lib.sh": SHELL_WRITE}, "tools/lib.sh:1", "bash-sources-a-lib"),
        _case("bash tools/drill.sh\n", {"tools/drill.sh": '"$pybin" -m tools.planted\n', **PLANTED_TOOL}, "tools/planted.py:5", "bash-runs-a-tools-module"),
        _case("bash tools/drill.sh\n", {"tools/drill.sh": "\"$pybin\" - <<'PY'\nfrom tools.planted import main\nmain()\nPY\n", **PLANTED_TOOL}, "tools/planted.py:5", "bash-heredoc-imports-a-tool"),
        _case("python -m tools.runner\n", {"tools/runner.py": 'MINT = REPO / "tools" / "planted.py"\n', **PLANTED_TOOL}, "tools/planted.py:5", "module-runs-a-tool-as-a-child"),
        _case("python - <<'PY'\nimport urllib.request\napp_url = 'http://app'\n" + PLANTED_POST + "PY\n", {}, "/outcome in the workflow text", "heredoc-writes"),
        _case("python - <<'PY'\nfrom tools.planted import main\nmain()\nPY\n", PLANTED_TOOL, "tools/planted.py:5", "heredoc-imports-a-tool"),
        _case("python -c 'import urllib.request; urllib.request.urlopen(\"http://app/api/leads/B/snooze\")'\n", {}, "/snooze in the workflow text", "inline-c-writes"),
        _case("pytest -p tests.fixtures.planted_plugin -q tests/integration/test_reads.py\n", {"tests/fixtures/planted_plugin.py": PLANTED_PLUGIN, **READ_SUITE}, "tests/fixtures/planted_plugin.py:", "pytest-p-plugin"),
        _case("python scripts/planted.py\n", PLANTED, "scripts/planted.py:5", "repo-script-outside-tools"),
        _case("timeout 60 python \"$GITHUB_WORKSPACE/scripts/planted.py\"\n", PLANTED, "scripts/planted.py:5", "workspace-prefixed-script"),
        _case("python tools/missing.py\n", {}, "python runs tools/missing.py, which this scan cannot read", "tool-path-that-is-no-file"),
        _case("python -m tools.missing\n", {}, "runs tools.missing, which is no module", "tools-module-that-is-no-module"),
        _case('bash "$RUNNER_TEMP/planted.sh"\n', {}, "bash runs $RUNNER_TEMP/planted.sh, which this scan cannot read", "script-outside-the-repo"),
        _case("python -m planted_package\n", {}, "runs a module this scan does not read", "module-this-scan-does-not-read"),
        _case("python - <<'PY'\nthis is not ( python\nPY\n", {}, "inline Python this scan cannot parse", "python-heredoc-that-does-not-parse"),
        # Other ways a step runs repo code: an interpreter's version, a wrapper, the file itself, inline code's imports.
        _case("python3.11 scripts/planted.py\n", PLANTED, "scripts/planted.py:5", "versioned-interpreter"),
        _case("coverage run --branch scripts/planted.py\n", PLANTED, "scripts/planted.py:5", "coverage-run-a-script"),
        _case("coverage run --source=backend -m pytest tests/integration/test_planted_live.py\n", WRITER_SUITE, "test_planted_live.py::test_writes writes", "coverage-run-pytest"),
        _case("uv run --with requests python scripts/planted.py\n", PLANTED, "scripts/planted.py:5", "uv-run"),
        _case("env -u HOME MIP_X=1 python scripts/planted.py\n", PLANTED, "scripts/planted.py:5", "env-wrapper"),
        _case("./scripts/planted.py --base \"$MIP_APP_URL\"\n", PLANTED, "scripts/planted.py:5", "the-file-run-by-path"),
        _case("python -c 'from tools.planted import main; main()'\n", PLANTED_TOOL, "tools/planted.py:5", "inline-c-imports-a-tool"),
        _case("python <<< 'from tools.planted import main'\n", PLANTED_TOOL, "tools/planted.py:5", "here-string-imports-a-tool"),
        _case("python < scripts/planted.py\n", PLANTED, "scripts/planted.py:5", "python-reads-a-script-on-stdin"),
        _case("cat <<'SH' | bash\npython scripts/planted.py\nSH\n", PLANTED, "scripts/planted.py:5", "here-document-piped-to-bash"),
        _case("bash -c 'python scripts/planted.py'\n", PLANTED, "scripts/planted.py:5", "bash-c-runs-a-script"),
        _case("bash <<'SH'\npython scripts/planted.py\nSH\n", PLANTED, "scripts/planted.py:5", "bash-here-document-runs-a-script"),
        _case('eval "python scripts/planted.py"\n', PLANTED, "scripts/planted.py:5", "eval"),
        _case("source scripts/planted.sh\n", {"scripts/planted.sh": SHELL_WRITE}, "scripts/planted.sh:1", "source"),
        _case("python -m tools.runner operators -- python scripts/planted.py\n", {"tools/runner.py": "", **PLANTED}, "scripts/planted.py:5", "a-program-handed-on-after-dashes"),
        _case("echo \"$(python scripts/planted.py)\"\n", PLANTED, "scripts/planted.py:5", "command-substitution"),
        _case('"$PY" scripts/planted.py\n', PLANTED, "runs $PY, which this scan cannot resolve", "program-named-by-a-variable"),
        _case("curl -s https://example.invalid/x.py | python -\n", {}, "python runs code from a pipe this scan cannot read", "code-from-a-pipe"),
        _case("make deploy-dev\n", {}, "make runs recipes this scan does not read", "make"),
        _case("echo 'unterminated\n", {}, "shell text this scan cannot split", "unsplittable-shell"),
        _case("node -e 'fetch(1)'\n", {}, "node -e runs code this scan cannot read", "node-inline"),
        _case("node scripts/planted.mjs\n", {"scripts/planted.mjs": "import { write } from './lib.mjs';\nwrite();\n", "scripts/lib.mjs": JS_WRITE}, "scripts/lib.mjs:1: /outcome", "node-script-imports-a-writer"),
        _case("node scripts/planted.mjs\n", {"scripts/planted.mjs": "import { write } from './lib_moved.mjs';\n"}, "imports ./lib_moved.mjs, which resolves to no file", "node-script-import-that-is-no-file"),
        _case("node --import ./scripts/planted.mjs scripts/reads.mjs\n", {"scripts/planted.mjs": JS_WRITE, "scripts/reads.mjs": ""}, "scripts/planted.mjs:1", "node-preload"),
        # The step's shell, and its directory.
        _case("from tools.planted import main\nmain()\n", PLANTED_TOOL, "tools/planted.py:5", "shell-python-imports-a-tool", shell="python"),
        _case("this is not ( python\n", {}, "inline Python this scan cannot parse", "shell-python-that-does-not-parse", shell="python {0}"),
        _case("Write-Output reads\n", {}, "runs under shell pwsh, which this scan cannot read", "another-shell", shell="pwsh"),
        _case("node ../tools/planted.mjs\n", {"tools/planted.mjs": JS_WRITE}, "tools/planted.mjs:1", "a-frontend-step-runs-a-repo-tool", working_directory="frontend"),
        _case("python3 ../../outside.py\n", {}, "python3 runs ../../outside.py, which this scan cannot read", "a-path-outside-the-repo", working_directory="frontend"),
        # The package.json scripts an npm command runs, and npm's own pre/post and lifecycle scripts.
        _case("npm --prefix frontend run probe\n", {**_package(probe="node ../scripts/planted.mjs"), "scripts/planted.mjs": JS_WRITE}, "scripts/planted.mjs:1", "npm-run"),
        _case("npm run probe\n", {**_package(preprobe="python ../scripts/planted.py", probe="echo ok"), **PLANTED}, "scripts/planted.py:5", "npm-pre-script", working_directory="frontend"),
        _case("npm --prefix frontend test --ignore-scripts\n", {**_package(test="python ../scripts/planted.py"), **PLANTED}, "scripts/planted.py:5", "npm-test-under-ignore-scripts"),
        _case("npm --prefix frontend ci\n", {**_package(postinstall="python ../scripts/planted.py"), **PLANTED}, "scripts/planted.py:5", "npm-install-lifecycle"),
        _case("npm run -w frontend probe\n", _package(probe="echo ok"), "npm -w runs scripts this scan does not follow", "npm-workspace"),
        _case("npm run probe -- ../tools/reads.py\nnpm run probe -- ../scripts/planted.py\n", {**_package(probe="python"), "tools/reads.py": "", **PLANTED}, "scripts/planted.py:5", "npm-one-script-two-argument-lists", working_directory="frontend"),
    ],
)
def test_the_step_scan_finds_a_write_or_an_unread_script_in_every_shape_a_step_runs(tmp_path: Path, run: str, files: dict[str, str], expected: str, context: dict[str, str]) -> None:
    root = _step_tree(tmp_path, files)

    findings = _step_scan(root, run, **context)

    assert any(expected in finding for finding in findings), findings


def test_the_step_scan_passes_a_step_that_only_reads(tmp_path: Path) -> None:
    """Installs, a read-only tool by path or module, an inline script, a suite, a shell heredoc, plain shell, stale comments."""

    run = (
        "set -euo pipefail\n"
        "# tools/retired_probe.py moved to tools/reads.py\n"
        "# python -m tools.retired_module moved to tools.reads_module\n"
        "python --version\n"
        "python -m pip install --only-binary=:all: -r requirements.txt\n"
        "python tools/reads.py --base \"$MIP_APP_URL\" && bash tools/reads.sh\n"
        "python -m tools.reads_module\n"
        "python -m json.tool \"$RUNNER_TEMP/vars.json\" > /dev/null\n"
        "python - <<'PY'\nfrom tools.reads_module import main\nprint(main('/api/sales/outcomes/summary'))\nPY\n"
        "cat <<EOF >> \"$GITHUB_STEP_SUMMARY\"\nbash and python, in prose (not code)\nEOF\n"
        "pytest -q tests/integration/test_reads.py\n"
        "npm --prefix frontend ci --ignore-scripts --no-audit --no-fund\n"
        "if [ -n \"$X\" ]; then echo ok; fi\n"
    )
    root = _step_tree(
        tmp_path,
        {
            "tools/reads.py": 'GET = "/api/leads/B/assignment"\n',
            "tools/reads.sh": 'curl "$APP_URL/api/geo/assignment-overlay"\n',
            "tools/reads_module.py": "def main(path):\n    return path\n",
            **READ_SUITE,
            **_package(postinstall="python ../scripts/planted.py"),
            **PLANTED,
        },
    )

    assert _step_scan(root, run) == []


def test_the_step_scan_passes_a_frontend_step_that_only_reads(tmp_path: Path) -> None:
    """From frontend: repo tools the way package.json runs them, a spec, and the npm scripts that run both."""

    run = (
        "node ../tools/reads.mjs --report-only\n"
        "python3 ../tools/reads.py --check\n"
        "npx playwright install --with-deps chromium\n"
        "npx playwright test tests/e2e/reads.spec.ts --reporter=list --workers=1\n"
        "npm run e2e:ci -- tests/e2e/reads.spec.ts\n"
        "npm run build\n"
    )
    root = _step_tree(
        tmp_path,
        {
            "tools/reads.mjs": "import { label } from './reads_lib.mjs';\n// import { gone } from './retired.mjs';\nlabel();\n",
            "tools/reads_lib.mjs": "export const label = () => '/api/sales/outcomes/summary';\n",
            "tools/reads.py": 'GET = "/api/leads/B/assignment"\n',
            "frontend/tests/e2e/reads.spec.ts": SPEC_HEAD + "test('reads', async () => {});\n",
            **_package(**{"e2e:ci": "playwright test --reporter=list --workers=1", "build": "tsc -b && vite build && node ../tools/reads.mjs"}),
        },
    )
    step = step_scan.step_findings(root, "planted: step", run, working_directory="frontend")

    assert _step_scan(root, run, working_directory="frontend") == []
    assert [each.specs for each in step.playwright] == [("tests/e2e/reads.spec.ts",)] * 2, "positive control: both runs select the spec"
    assert {path.relative_to(root).as_posix() for path in scan.loaded_files(root, step.files)} == {"tools/reads.mjs", "tools/reads_lib.mjs", "tools/reads.py"}


def test_the_workflow_scan_reads_every_string_a_step_holds() -> None:
    """A github-script body or an env value is code or data a step runs with, not only ``run:``."""

    step = {"uses": "actions/github-script@v9", "with": {"script": "await github.request('POST /api/leads/B/outcome');"}}
    assert step_scan.workflow_string_findings({"jobs": {"planted": {"steps": [step]}}}) == [".jobs.planted.steps[0].with.script: /outcome in the workflow text"]
    assert step_scan.workflow_string_findings({"env": {"PROBE": "/api/sales/distribute"}}) == [".env.PROBE: /sales/distribute in the workflow text"]
    assert step_scan.workflow_string_findings({"env": {"READ": "/api/sales/outcomes/summary"}}) == []


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
