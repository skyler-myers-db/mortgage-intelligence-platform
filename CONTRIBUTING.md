# Contributing

Keep changes small, Module 0-scoped, and evidence-backed. This repository ships
a governed Databricks App, so a green build is not enough by itself: PRs should
explain what changed, which contracts were touched, and what validation proves
the change.

## Commit And PR Policy

Use conventional commit prefixes:

- `feat:` for new customer-visible capability.
- `fix:` for bug fixes and contract corrections.
- `audit:` for audit remediation or validation-only tranches.
- `docs:` for documentation-only changes.
- `test:` for test-only additions.
- `chore:` for dependency, build, and maintenance work.

Every PR description should include:

- Summary of the user-facing or operator-facing change.
- Risk and rollback notes.
- Commands run, with live deployment evidence when applicable.
- Screenshots for UI changes and live API probes for backend/runtime changes.
- Whether `CHANGELOG.md`, OpenAPI baseline, load baseline, or docs changed.

Update `CHANGELOG.md` for customer-visible behavior, API contract changes,
operator workflow changes, dependency/license posture changes, and deployment
or recovery changes. Version bumps in `pyproject.toml` and
`frontend/package.json` should happen only for release branches or explicit
release-prep PRs.

## Local Validation

Use the repo virtualenv and package scripts:

```bash
.venv/bin/python -m ruff check backend tests tools jobs pipelines
.venv/bin/python -m pytest -q tests/unit
.venv/bin/python -m pytest -q tests/integration --maxfail=1
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
npm --prefix frontend run budget
```

Integration tests skip when live Databricks credentials are absent. Do not
replace those skips with mocks; production code has no runtime mock fallback.

For deployed-app changes, also run a live smoke against the Databricks App and
record the active deployment ID:

```bash
scripts/smoke_live.sh --boot-timeout 60
```

## CI Gates

The PR workflow is credential-free and runs on forks. The nightly workflow owns
live Databricks validation.

Key PR gates:

- `ruff check backend tests tools jobs pipelines`: Python lint and import
  ordering.
- `pytest -q -n auto --cov=backend --cov-fail-under=83`: unit/integration
  suite under skip-gated live tests plus backend coverage.
- `tests/unit/test_architecture_boundaries.py`: router layering, schema/service
  boundaries, structured logging, no production `InMemory*` stores, no
  runtime mock toggles, monolith ceiling, and route-test manifest.
- `tests/unit/test_supply_chain_licenses.py`: commercial license blockers,
  retired noncommercial packages, real Python lockfile, and license notice
  coverage.
- `tests/unit/test_openapi_contract.py`: canonical `/api/v1/*` paths,
  deprecated `/api/*` aliases, `info.version`, response-model discipline, and
  no breaking OpenAPI baseline drift.
- `tests/unit/test_load_test_contract.py`: read/write load harness shape,
  committed baseline coverage, Lakebase pool/concurrency alignment, and cache
  behavior.
- `tests/unit/test_documentation_contract.py`: public docs are substantive,
  current operator docs use canonical `/api/v1/*`, load-baseline docs have a
  single canonical source, and backend modules carry docstrings.
- Frontend lint/test/build/budget: TypeScript, React/Vitest coverage, Vite
  production build, and bundle-size budgets.
- Security job: gitleaks, bandit, pip-audit, and npm audit.

Nightly gates include SQL/Python parity, Lakebase round-trip, Genie live and
adversarial regression, dashboard widget resolution, gold data-truth checks,
source readiness, geography reconciliation, and live Playwright against the
deployed app.

## Changing The API

The canonical API surface is `/api/v1/*`. The unversioned `/api/*` routes are
deprecated compatibility aliases and must keep the same request/response
contract until they are intentionally retired.

When adding or changing an endpoint:

1. Add explicit request and response Pydantic models. Do not return generic
   `dict` from canonical routes.
2. Add or update router tests.
3. Update `ROUTE_TEST_MANIFEST` in
   `tests/unit/test_architecture_boundaries.py`.
4. If the wire contract changes — including additive changes — regenerate
   `tests/fixtures/openapi_baseline.json` with
   `python tools/regen_openapi_baseline.py` (deterministic snapshot of
   `backend.main.app.openapi()`) and commit it in the same PR, explaining why
   the change is additive or intentionally breaking.
   `tests/unit/test_openapi_contract.py` fails loud when the baseline drifts
   from the live `/api` surface in either direction.
5. Update frontend API types and callers, or generated types if that path is
   adopted later.
6. Update `CHANGELOG.md` and the relevant operator docs.

## Changing Data Contracts

When adding a gold table, metric view, UC function, Lakebase table, or scoring
field:

1. Update SQL under `sql/` or DDL under `lakebase/`.
2. Preserve catalog portability through `MIP_DEFAULT_CATALOG`, `qualify()`, and
   `tools/render_sql.py`.
3. Update storage-contract tests such as `test_gold_ddl_contract.py`,
   `test_metric_view_ddl_contract.py`, `test_silver_ddl_contract.py`,
   `test_lifecycle_sync_contract.py`, or `test_audit_store_contract.py`.
4. If lender identity is involved, update `mip.ref.lender_dictionary` handling
   and the multi-catalog runbook.
5. Keep migrations append-only and idempotent unless a rollback plan is
   documented in `docs/disaster-recovery.md`.

## Changing Genie Or Governed Actions

When changing Genie instructions, trusted assets, SQL policy, numeric
verification, or action confirmation:

1. Keep trusted assets in sync across Genie YAML, backend allowlists, and tests.
2. Preserve PII/protected-class refusal behavior and SQL SELECT-only policy.
3. Run `tests/integration/test_genie_regression.py` and the live Genie smoke
   when credentials are available.
4. If action-token claims change, update HMAC rotation docs and tests.
5. Confirm audit rows for refused prompts, trusted answers, and confirmed
   governed actions.

## Changing Frontend Or Design

Before touching routes, visible components, CSS, copy, or interaction states,
read `design_files/index.html` and `design_files/Module 0 Prototype.html`.
Those files are the design contract. Keep BEM class vocabulary, tokens, Geist
fonts, responsive behavior, and no-inline-hex/no-inline-pixel discipline.

Run:

```bash
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
npm --prefix frontend run budget
```

For customer-visible changes, run a browser walkthrough or Playwright e2e
against the deployed app before signing off.

## Updating Load Baselines

The canonical operator doc is [`docs/load-baseline.md`](docs/load-baseline.md);
the machine-readable baseline is `tools/load_test/baseline.json`.

Only refresh the baseline after an intentional performance change:

```bash
MIP_LOAD_TEST_FAIL_ON_BASELINE_REGRESSION=1 bash tools/load_test/run.sh
MIP_LOAD_TEST_WRITE=1 MIP_LOAD_TEST_WRITE_BASELINE=1 bash tools/load_test/run.sh
```

Attach the CSV/HTML artifact names from `tools/load_test/results/` to the PR,
but do not commit that directory.
