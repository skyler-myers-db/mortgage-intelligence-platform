"""The wave-2 CI integration duties stay wired (2026-09-21 UI/UX audit).

Three frontend gates in ``.github/workflows/ci.yml`` used to be able to pass
vacuously while their subject was not yet on the tree; it is now, so each is
pinned fail-closed:

- the perf-budget step runs unconditionally (its ``hashFiles`` guard is gone:
  perf-budget.fixture.spec.ts is committed);
- the source-map upload errors when the build emitted no maps
  (tools/postbuild_artifacts.mjs moves them to frontend/sourcemaps/);
- the e2e-fixture job re-runs tests/unit/test_frontend_build_artifacts.py
  right after its build with ``MIP_REQUIRE_FRONTEND_DIST=1``, so the
  served-layer and build-meta checks fail rather than skip.

Wave 4 (test-infra PR-1) adds: the e2e-visual container and ``MIP_VRT_IMAGE``
name the image of the exact ``@playwright/test`` pin, and the lock resolves
the Playwright trio to it; the oxlint jsx-a11y ratchet is its own
frontend-tests step before ``lint`` and the tail of the ``lint`` chain, with
an exact pin and a jsx-a11y-only config; the single-worker perf step also
collects interaction-budget, and PERF_SPEC matches exactly the two budget
specs.

The parse job (``playwright-offline``) runs the whole route-fulfilled
layout-stability spec: a ``--grep`` there ran one of its five tests and hid
two broken ones, and the product layout shift behind one of them, for waves.

The backend job's Node + ``MIP_REQUIRE_FIXTURE_CONTRACT`` wiring is pinned by
tests/unit/test_e2e_fixture_contract.py beside the test it serves.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PERF_SPEC = ROOT / "frontend" / "tests" / "e2e" / "fixture" / "perf-budget.fixture.spec.ts"
BUILD_ARTIFACTS_TEST = "tests/unit/test_frontend_build_artifacts.py"
LAYOUT_STABILITY_SPEC = "tests/e2e/layout-stability.spec.ts"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _step_index(steps: list[dict[str, Any]], needle: str) -> int:
    for index, step in enumerate(steps):
        if needle in str(step.get("run", "")) or needle in str(step.get("uses", "")):
            return index
    raise AssertionError(f"no step runs or uses {needle!r}")


PERF_STEP_RUN = "npm --prefix frontend run e2e:fixture:ci -- perf-budget interaction-budget --workers=1"


def test_the_perf_budget_step_is_unconditional() -> None:
    assert PERF_SPEC.is_file(), "the perf budget spec the step runs is committed"
    steps = _jobs()["e2e-fixture"]["steps"]
    perf = steps[_step_index(steps, "perf-budget interaction-budget --workers=1")]

    assert "if" not in perf, "the perf-budget step must not be guarded now that its spec exists"
    assert perf["env"]["MIP_PERF"] == "1"
    # Audit runtime-09: the same single-worker step collects the lead-queue
    # interaction budget. The file filters are required (MIP_PERF=1 alone
    # also collects every normal fixture spec), and perf-budget stays gating.
    assert perf["name"] == "Run the perf and interaction budgets (single worker)"
    assert perf["run"] == PERF_STEP_RUN
    assert "continue-on-error" not in perf, "perf-budget is calibrated on the reference runner and gates"


def test_the_perf_calibration_is_uploaded_after_the_perf_step() -> None:
    """Audit runtime-09 / quality-08: every perf run's calibration JSON is an artifact."""
    steps = _jobs()["e2e-fixture"]["steps"]
    perf = _step_index(steps, PERF_STEP_RUN)
    upload = next(i for i, step in enumerate(steps) if step.get("name") == "Upload the perf calibration")
    assert upload == perf + 1, "the upload follows the perf step"
    step = steps[upload]
    assert step["if"] == "always()"
    assert str(step["uses"]).startswith("actions/upload-artifact@")
    assert step["with"] == {
        "name": "perf-calibration-${{ github.run_id }}-${{ github.run_attempt }}",
        "path": "frontend/test-results/perf/calibration/*.json",
        "if-no-files-found": "warn",
        "retention-days": 30,
    }


def test_the_budget_step_writes_the_per_chunk_json() -> None:
    """Audit quality-08: the gating budget run also writes the per-chunk report."""
    steps = _jobs()["frontend-tests"]["steps"]
    budget = steps[_step_index(steps, "npm --prefix frontend run budget")]
    assert budget["run"] == "npm --prefix frontend run budget -- --json ../frontend-budget.json"
    assert "continue-on-error" not in budget and "if" not in budget


def test_the_bundle_delta_job_is_informational_and_token_free() -> None:
    """Audit quality-08: head vs merge-base in the step summary; the gates stay in frontend-tests."""
    job = _jobs()["bundle-delta"]
    assert job["if"] == "github.event_name == 'pull_request'"
    assert job["steps"][0]["with"]["fetch-depth"] == 0
    text = yaml.safe_dump(job)
    assert "secrets." not in text and "GITHUB_TOKEN" not in text, "no token: fork PRs get the same summary"
    runs = [str(step.get("run", "")) for step in job["steps"]]
    assert any("git merge-base HEAD" in run and "git worktree add" in run for run in runs)
    base = next(run for run in runs if "--dist ../bundle-base/frontend/dist" in run)
    assert "--report-only" in base and "--build-meta ../bundle-base/frontend/build-meta" in base and "--json ../bundle-base.json" in base
    assert any(run.startswith("node tools/check_frontend_budgets.mjs --report-only --base ../bundle-base.json") for run in runs)
    assert all("continue-on-error" not in step for step in job["steps"])


def _playwright_perf_spec() -> re.Pattern[str]:
    config = (FRONTEND / "playwright.config.ts").read_text(encoding="utf-8")
    literal = re.search(r"^(?:export )?const PERF_SPEC = /(.+)/;$", config, re.MULTILINE)
    assert literal, "playwright.config.ts declares PERF_SPEC as a regex literal"
    return re.compile(literal.group(1))


def test_perf_spec_collects_the_perf_and_interaction_budgets_only() -> None:
    perf_spec = _playwright_perf_spec()
    for separator in ("/", "\\"):
        fixture = separator.join(("frontend", "tests", "e2e", "fixture"))
        assert perf_spec.search(f"{fixture}{separator}perf-budget.fixture.spec.ts")
        assert perf_spec.search(f"{fixture}{separator}interaction-budget.fixture.spec.ts")
        # perf-motion pins motion quick wins, not timings: the normal suite runs it.
        assert not perf_spec.search(f"{fixture}{separator}perf-motion.fixture.spec.ts")
        assert not perf_spec.search(f"{fixture}{separator}visual.fixture.spec.ts")
        assert not perf_spec.search(f"{fixture}{separator}my-perf-budget.fixture.spec.ts")
        # Anchored at the end: a snapshot directory beside the spec is not the spec.
        assert not perf_spec.search(f"{fixture}{separator}perf-budget.fixture.spec.ts-snapshots{separator}home.png")


def test_the_source_map_upload_fails_when_the_build_emitted_none() -> None:
    steps = _jobs()["frontend-tests"]["steps"]
    upload = next(step for step in steps if step.get("name", "").startswith("Upload source maps"))

    assert upload["with"]["path"] == "frontend/sourcemaps/**"
    assert upload["with"]["if-no-files-found"] == "error"


def test_the_vrt_container_is_the_image_of_the_playwright_pin() -> None:
    """Audit stack-10 / a11y-05 item 5: the renderer, its image and the lock move together.

    The e2e-visual job runs inside the Playwright image, and the VRT spec
    refuses any host whose MIP_VRT_IMAGE is not the image of the installed
    @playwright/test. Both literals must name the package.json pin, and the
    lock must resolve the whole Playwright trio to that same version.
    """
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    pin = package["devDependencies"]["@playwright/test"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", pin), f"@playwright/test must be an exact pin, got {pin!r}"
    visual = _jobs()["e2e-visual"]
    expected = f"mcr.microsoft.com/playwright:v{pin}-noble"

    assert visual["container"]["image"] == expected
    assert visual["env"]["MIP_VRT_IMAGE"] == expected

    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    for name in ("@playwright/test", "playwright", "playwright-core"):
        assert lock[f"node_modules/{name}"]["version"] == pin, f"{name} in the lock is not the {pin} pin"


def test_the_oxlint_ratchet_runs_as_its_own_step_before_lint_and_inside_it() -> None:
    """Audit a11y-05 item 2: the jsx-a11y ratchet is a named CI step and part of `npm run lint`."""
    steps = _jobs()["frontend-tests"]["steps"]
    ratchet = _step_index(steps, "npm --prefix frontend run lint:a11y")
    lint = next(i for i, step in enumerate(steps) if step.get("run") == "npm --prefix frontend run lint")

    assert steps[ratchet]["name"] == "oxlint jsx-a11y ratchet (audit a11y-05)"
    assert "if" not in steps[ratchet] and "continue-on-error" not in steps[ratchet]
    assert ratchet < lint

    scripts = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert scripts["lint:a11y"] == "node ../tools/oxlint_ratchet.mjs --check oxlint-baseline.json"
    # Test-infra PR-2 (audit stack-07 item 2) appends the legacy-spec
    # typecheck after the ratchet; the ratchet stays inside the chain.
    assert scripts["lint"].endswith(" && npm run lint:a11y && npm run typecheck:e2e")
    assert (FRONTEND / "oxlint-baseline.json").is_file(), "the ratchet's baseline is committed"


def test_the_legacy_specs_are_typechecked_inside_the_lint_chain() -> None:
    """Audit stack-07 item 2 / quality-10 step 1: tests/e2e/*.ts typecheck with the fixture's options."""
    scripts = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert scripts["typecheck:e2e"] == "tsc -p tests/e2e/tsconfig.json"

    legacy = json.loads((FRONTEND / "tests" / "e2e" / "tsconfig.json").read_text(encoding="utf-8"))
    fixture = json.loads((FRONTEND / "tests" / "e2e" / "fixture" / "tsconfig.json").read_text(encoding="utf-8"))
    assert legacy["compilerOptions"] == fixture["compilerOptions"], "only the include differs"
    assert "./*.ts" in legacy["include"], "the non-recursive legacy glob (fixture/ keeps its own project)"
    assert "../../playwright.config.ts" in legacy["include"]


def test_oxlint_is_an_exact_pin_and_its_config_names_only_jsx_a11y_rules() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", package["devDependencies"]["oxlint"]), "oxlint must be pinned exactly (no ^ or ~)"

    config = json.loads((FRONTEND / ".oxlintrc.json").read_text(encoding="utf-8"))
    # `overrides`, `extends`, `settings` or an extra ignore pattern would
    # disable a rule for a file outside the directive scan: a config-level
    # disable is banned like a directive.
    assert set(config) == {"$schema", "plugins", "categories", "rules", "ignorePatterns"}
    assert config["ignorePatterns"] == ["**/*.test.ts", "**/*.test.tsx", "src/test/**", "src/mocks/**"]
    assert config["plugins"] == ["jsx-a11y"]
    assert set(config["categories"]) == {
        "correctness", "suspicious", "pedantic", "perf", "style", "restriction", "nursery",
    }
    assert set(config["categories"].values()) == {"off"}, "no category may enable a non-jsx-a11y rule"
    assert config["rules"], "every rule is named explicitly"
    assert all(rule.startswith("jsx-a11y/") for rule in config["rules"])
    assert set(config["rules"].values()) <= {"error", "off"}

    # A rule turned off carries its one-line reason in docs/testing.md.
    testing_doc = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
    for rule, level in config["rules"].items():
        if level == "off":
            assert f"`{rule}`" in testing_doc, f"{rule} is off without a reason in docs/testing.md"


def test_the_fixture_job_checks_the_built_dist_with_the_require_flag() -> None:
    steps = _jobs()["e2e-fixture"]["steps"]
    build = _step_index(steps, "npm --prefix frontend run build")
    python = _step_index(steps, "actions/setup-python@")
    install = _step_index(steps, "pip install -r requirements.txt")
    check = _step_index(steps, BUILD_ARTIFACTS_TEST)

    assert build < python < install < check
    assert steps[check]["env"]["MIP_REQUIRE_FRONTEND_DIST"] == "1"


def test_the_parse_job_runs_the_whole_layout_stability_spec() -> None:
    assert (FRONTEND / LAYOUT_STABILITY_SPEC).is_file()
    steps = _jobs()["playwright-offline"]["steps"]
    step = steps[_step_index(steps, LAYOUT_STABILITY_SPEC)]
    lines = [line.strip() for line in step["run"].splitlines()]

    assert step["working-directory"] == "frontend"
    assert f"E2E_LAYOUT_MOCK=1 npx playwright test {LAYOUT_STABILITY_SPEC} --workers=1" in lines
    assert "--grep" not in step["run"], "every layout-stability test runs, not a --grep subset"
    assert "if" not in step and "continue-on-error" not in step


# --- Cross-engine projects (manual check 2026-09-30, a11y-10 item 4, css-06 item 3)


def _playwright_regex(name: str) -> re.Pattern[str]:
    config = (FRONTEND / "playwright.config.ts").read_text(encoding="utf-8")
    literal = re.search(rf"^export const {name} = /(.+)/;$", config, re.MULTILINE)
    assert literal, f"playwright.config.ts exports {name} as a regex literal"
    return re.compile(literal.group(1))


def test_the_cross_engine_job_installs_webkit_and_firefox_and_gates() -> None:
    job = _jobs()["e2e-cross-engine"]
    assert job["name"] == "e2e (fixture, WebKit + Firefox forced colors)"
    assert job["runs-on"] == "ubuntu-latest"
    assert "continue-on-error" not in job
    steps = job["steps"]
    install = steps[_step_index(steps, "npx playwright install --with-deps webkit firefox")]
    assert install["working-directory"] == "frontend"
    run = steps[_step_index(steps, "npm --prefix frontend run e2e:fixture:ci")]
    assert run["env"]["MIP_CROSS_ENGINE"] == "1"
    assert run["env"]["E2E_FIXTURE_WORKERS"] == "2"
    assert all("continue-on-error" not in step for step in steps)
    assert "secrets." not in yaml.safe_dump(job), "the cross-engine job is credential-free"


def test_only_the_cross_engine_job_sets_mip_cross_engine() -> None:
    for name, job in _jobs().items():
        if name == "e2e-cross-engine":
            continue
        assert "MIP_CROSS_ENGINE" not in yaml.safe_dump(job), f"{name} must not set MIP_CROSS_ENGINE"


def test_the_engine_projects_collect_exactly_their_specs() -> None:
    webkit, firefox, engine_only = (
        _playwright_regex(name) for name in ("WEBKIT_SPEC", "FIREFOX_FORCED_SPEC", "ENGINE_ONLY_SPEC")
    )
    fixture = "frontend/tests/e2e/fixture/"
    cases = {
        "queue-clearance.cross-engine.fixture.spec.ts": (True, False, False),
        "genie-pagehide.cross-engine.fixture.spec.ts": (True, True, False),
        "pinned-focus.webkit.fixture.spec.ts": (True, False, True),
        "forced-colors.firefox.fixture.spec.ts": (False, True, True),
        "lead-queue.fixture.spec.ts": (False, False, False),
        "visual.fixture.spec.ts": (False, False, False),
    }
    for spec, (in_webkit, in_firefox, ignored_in_chromium) in cases.items():
        path = fixture + spec
        assert bool(webkit.search(path)) is in_webkit, spec
        assert bool(firefox.search(path)) is in_firefox, spec
        assert bool(engine_only.search(path)) is ignored_in_chromium, spec
    # The shipped cross-engine specs are committed.
    for spec in ("queue-clearance.cross-engine", "forced-colors.firefox", "genie-pagehide.cross-engine"):
        assert (FRONTEND / "tests" / "e2e" / "fixture" / f"{spec}.fixture.spec.ts").is_file(), spec
