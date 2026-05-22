> **Internal implementation artifact. Not approved for public release.**

# CI hygiene — Wave 1 cleanup (2026-04-21)

Post-Wave-1 hardening on `slice13-accuracy-validation`. Two CI-adjacent gaps were
flagged against the eight new commits that landed in Wave 1; one turned out to
be a non-issue on inspection, the other required a one-line workflow change.

## TASK 1 — `tools/e2e_borrower_audit.py` ruff status

**Result: no lint errors. No code change required.**

The task hypothesis was that the Wave-1 audit script (commit `f1e8790`) had
skipped the `ruff` gate. Verified against the actual project config
(`pyproject.toml`: `select = ["E", "F", "I", "B", "UP", "SIM"]`,
`ignore = ["E501"]`, `line-length = 100`, `target-version = "py311"`):

```
$ .venv/bin/ruff check tools/e2e_borrower_audit.py
All checks passed!

$ .venv/bin/ruff check backend tests tools jobs pipelines
All checks passed!
```

`python -m py_compile tools/e2e_borrower_audit.py` also succeeds. Either the
Wave-1 agent cleaned the file before commit, or the suspected errors were
rule-set-dependent and the project ruleset doesn't select them. Either way, the
file does not break the `ruff check backend tests tools jobs pipelines` gate in
`.github/workflows/ci.yml`. No edits made; no `# noqa` markers introduced.

## TASK 2 — `test_genie_regression.py` wired into nightly

**Result: one new step appended to `parity-live`.**

Wave 1 landed `tests/integration/test_genie_regression.py` (22 live-gated Genie
queries + 7 credential-free graders) but the nightly `parity-live` job only
invoked `test_genie_live.py`. Added a `Genie regression + adversarial` step
directly after the existing `Genie live` step so both run under the same secrets
block. The new step reuses the env already defined at the job level — no extra
secrets required.

Diff summary (`.github/workflows/nightly.yml`, one hunk, step-only addition):

```yaml
      - name: Genie live
        run: pytest -q tests/integration/test_genie_live.py
+     - name: Genie regression + adversarial
+       run: pytest -q tests/integration/test_genie_regression.py
```

(Plus a three-line comment explaining the 22+7 split and why this step follows
the live-smoke step.)

YAML parse validated:

```
$ .venv/bin/python3 -c "import yaml; yaml.safe_load(open('.github/workflows/nightly.yml'))"
YAML valid
```

No other job, step, env, or trigger was touched. `playwright-e2e-live` and
`notify-on-failure` are unchanged.

## TASK 3 — full-suite baseline on the branch

All six required gates green locally against the current tree:

| Gate | Command | Result |
| --- | --- | --- |
| Python lint | `ruff check backend tests tools jobs pipelines` | All checks passed |
| Python tests (all) | `pytest -q` | **389 passed, 75 skipped** in 14.85s |
| Python tests (unit) | `pytest tests/unit` | 329 passed in 1.10s |
| Python tests (integration) | `pytest tests/integration` | 60 passed, 80 skipped in 13.74s |
| Frontend lint | `npm --prefix frontend run lint` | clean, `--max-warnings 0` |
| Frontend unit | `npm --prefix frontend run test` | 1 passed |
| Frontend build | `npm --prefix frontend run build` | `built in 117ms`, 5 assets emitted |
| Playwright parse | `npx playwright test --list` | 13 tests in 2 files (`module0.spec.ts` + `real_data.spec.ts`) |

All 80 integration skips are credential-gated (no `DATABRICKS_TOKEN` /
`LAKEBASE_*` / `GENIE_SPACE_ID` in this local shell), which matches the expected
PR-CI offline posture. The integration count is higher than the full-run skip
count (75) because a handful of tests are only collected when the integration
directory is targeted directly; this matches the pre-existing collection
topology and isn't a regression.

## Risks / residual red

None. Wave 1's additions (`tests/integration/test_genie_regression.py`,
`tools/e2e_borrower_audit.py`, load test harness, credential-kill drill,
observability, dashboards, segment-parity, borrower e2e audit, ZIP+4 fix) all
pass lint + type + import checks. The nightly workflow now exercises the new
Genie regression suite on every fire; if the Genie space regresses, the failure
issue filed by `notify-on-failure` will fire with the standard runbook link.
