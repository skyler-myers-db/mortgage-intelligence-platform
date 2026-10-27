# Testing strategy

## Unit tests

- Scoring functions.
- Offer rules.
- Evidence formatting.
- Pydantic schema validation.
- Frontend component rendering.

### Rendering a component: `mount()` and the act flag

`frontend/src/test/setup.ts` sets `IS_REACT_ACT_ENVIRONMENT = true` once for the whole Vitest run, so a suite no longer repeats that line (with it unset, every `act()` warns "The current testing environment is not configured to support act(...)"). A happy-dom suite renders through `mount()` from `src/test/render.tsx` instead of hand-rolling `createRoot` / `act` / `unmount`:

```tsx
import { mount } from '../../test/render';

const { container, rerender, unmount } = await mount(<MemoryRouter><Topbar /></MemoryRouter>);
await rerender(<MemoryRouter><Topbar /></MemoryRouter>);
```

Render and rerender run inside `await act(async ...)`, so effects and the microtasks they queue have flushed when the promise resolves. Every root is tracked and a module-level `afterEach` unmounts what a test left mounted and removes the containers `mount()` created (a `{ container }` you pass in is unmounted but left in place), so the next test starts with an empty body. When your own `afterEach` must unmount before it clears something the component reads (a `QueryClient`, `sessionStorage`), call `unmount()` there first: `mount()`'s hook runs after yours. It throws a clear error in a node-environment test. `src/test/` is test-only: ESLint bans production code from importing it, as it bans `src/mocks/` and, since W5e (`w5-wire-types` B3), anything under a `tests/` directory (`**/tests/**`: "Production code must not import e2e fixture data: no mock fallback in the running app."). The lane-owned suites use it today (Topbar, ActivationLoopPanel, DataOperationsPanel, useMainScroll); moving the other suites onto it (W5e cut C1, not built: a body-level rewrite of ~313 suites collides with every parallel lane's test edits) is a post-wave test-infra pass.

W5e deleted the per-file flag from the 269 suites that still repeated it (`tools/test_harness/h5_act_flag.py`, re-run it after a merge), and `src/test/actEnvironmentGate.test.ts` now fails on any assignment to `IS_REACT_ACT_ENVIRONMENT` (a property or element write, or `vi.stubGlobal`) outside `setup.ts`. Measured at the W5e base (0927bb22) from a full `npx vitest run --reporter=default`: 222 "not wrapped in act(...)" warnings across 38 files and 0 "not configured to support act". The six loudest suites (ask-genie.followup-continuity 42, DistributionBoard 33, ShortcutOverlay 23, CommandPalette 18, ask-genie.growth-agent 15, CommandPalette.verbs 8: 139 in all) were fixed: their waits advance time in act-wrapped ticks (`src/test/settleInAct.ts`: `eventuallyInAct` re-runs an assertion between ticks, `settleInAct` drains in-flight work before an unmount), the palette and the `?` shortcut sheet open inside an async act (each host sets its lazily loaded chunk in a `.then`, a microtask after a sync act has ended), and registry writes run inside act. Never poll inside one long act: React flushes the work it queued in an act scope only when that scope ends, so the poll would never see the update. An act warning is only a console.error, so a suite can regress without failing: `watchActWarnings()` (`src/test/actWarnings.ts`) spies on console.error, calling through so nothing is silenced, and its stop function returns the component React named in each act warning. ShortcutOverlay.test.tsx asserts that list is empty in its afterEach, after its drain and unmount. Adopt it in a suite once that suite measures 0.

Measured after the fix (W5e fix round 1, `npm --prefix frontend run test -- --reporter=default`, 0 "not configured to support act" in every run):

| Suite | Before (base) | Alone | The six together | Full run 1 / 2 |
| --- | --- | --- | --- | --- |
| ask-genie.followup-continuity | 42 | 0 | 0 | 0 / 0 |
| DistributionBoard | 33 | 0 | 0 | 0 / 0 |
| ShortcutOverlay | 23 | 0 (two runs) | 0 | 0 / 0 |
| CommandPalette | 18 | 0 | 0 | 0 / 0 |
| ask-genie.growth-agent | 15 | 0 | 0 | 0 / 0 |
| CommandPalette.verbs | 8 | 0 | 0 | 0 / 0 |
| Suite-wide total | 222 | | | 109 / 97 |

The suite-wide total is still above the 83 target (222 minus 139). The six emit nothing in either full run; every remaining warning comes from about 30 other suites, and their count moves with host load, because timing decides whether an update lands inside an act tick. Run 1 (load average about 250) printed 109 across 29 files, led by LeadTable.export 22, AdminAuditExplorer.receipt 12, WarmingUpBlock 6, AppContext.workspace 6, AdminAuditExplorer 5 and LeadTable.reviewChunk 5. Run 2 (load about 100 to 190) printed 97 across 30 files, led by useSvgViewBox 10, LeadTable.hotkeys 9, ScoreAnatomyGate 6, AppContext.workspace 6, AdminAuditExplorer 5, LeadTable.exportReceipt 5, Toaster.actions 5 and LeadTable.reviewChunk 5. The base tree (0927bb22), re-run in the same session at load about 100 to 150, printed 234: 133 from the six and 101 from the rest. So at a comparable load the head total (97) is below the base minus the six (101), but not below the 83 the first base measurement implied. Fixing those other suites is the post-wave test-infra pass, together with the C1 `mount()` migration.

**Factories, least-privileged** (D-api-types-a3 P3, W5e `w5-wire-types` B3). `src/test/factories/<domain>.ts` builds a complete, wire-shaped object with `make<Type>(overrides?: Partial<T>): T` (`makeLeadSummary`, `makeBorrower360`, `makeSessionResponse`, `makeApproveResult`, `makeRejectResult`, `makeApprovalRequestRow`, `makeGrowthAgentRunResponse`, `makeAuditRollupResponse`), spreading the fixture harness's wire bases (`tests/e2e/fixture/data/wireBases.ts`: every key the server sends, `satisfies ApiResponse<'X'>`, dumped from the backend's own models). The defaults are least-privileged and pending, re-set even over a privileged base: `can_approve`, `can_access_admin`, `can_read_audit` and `presenter_mode` false, `approval_status` `'pending'`, `approved` / `rejected` false, nullable audit and approval ids and `approved_at` null, so a test that needs an approver or a recorded decision says so. Identity-shaped values stay synthetic (masked `B-[0-9A-Z]{13}` ids, `clip_demo_*` / `ol_demo_*`, `Owner XXXXXX`, `@summit.example`, Summit Mortgage, the `FIXTURE_DEFAULT_ACTOR` key shape); `factories.test.ts` pins both. Use a factory instead of a partial literal with a cast when a test needs a schema-shaped object: once a type is a P3 alias, every field is required and a partial literal no longer compiles.

Vitest detects an agent session and switches to a reporter that hides console output; pass `--reporter=default` when you need the `stderr | file > test` lines (for example to count act warnings).

### Mocking `lib/api`: keep the real module

A factory `vi.mock('../lib/api', () => ({ api: { ... } }))` REPLACES the module, so `ApiError`, `isAbortError`, `dependencyLabel` and every other export are undefined for the whole file. That passes until some module the test loads, often a route chunk a sibling test preloaded, imports one of them; then `x instanceof ApiError` throws, depending on test order, which is how it flaked on CI (fixed in bde112b0). Spread the real module and override only what the test drives:

```ts
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));
```

`src/test/apiMockShape.test.ts` enforces it. It parses every `src/**/*.{test,test-support}.{ts,tsx}` with the TypeScript compiler, finds each `vi.mock` / `vi.doMock` whose specifier (a string, or the `vi.mock(import('../lib/api'), ...)` form) resolves (relative to that file) to `src/lib/api`, and fails with `file:line` unless there is no factory (an automock keeps every export name) or the returned object spreads the real module: `...(await importOriginal())`, `...(await vi.importActual('<spec>'))` where `<spec>` itself resolves to `src/lib/api` (the real module of a sibling such as `../lib/apiTypes` does not count), or an identifier bound to either, as `ask-genie.turn.test-support.tsx` does. Defining `ApiError` is no longer enough (W5e, "must spread"): such a factory fails with "defines ApiError but omits the other exports". The 39 factories that took the wave-4 `ApiError` escape were converted by `tools/test_harness/h4_api_mock_spread.py`, which keeps every override a factory returned after the spread and drops only a trivial `ApiError: class extends Error {}` stub nothing else in the file names; re-run it to convert a new one. A converted suite whose graph lazily imports a lib/api consumer keeps an assertion that its api mock was called (the PR #265 async-factory bypass would otherwise pass silently), and closes the bypass one of two ways: mock the lazy importer synchronously (a factory with no `await` is never handed the original module), or import the lazy chunk in the test file's static graph below the `vi.mock` calls, so the route's `import()` resolves from the module cache with the mock in place (`borrower-360.anatomy` loads `ScoreAnatomy`, both offer-orchestrator route suites load `DecisionReceipt`). The W5e conversion used the second, because a synchronous mock of `ScoreAnatomy` or `DecisionReceipt` would replace the very component whose api calls those suites assert. `PENDING_PARTIAL_API_MOCKS` is shrink-only: it lists bare factories in files another lane owns this wave, each entry must still fail (a converted one fails as stale and is removed), and a new file is converted, never added.

## Integration tests

- API health.
- Portfolio preview.
- Borrower detail.
- Offer recommendation.
- Approval writes audit.
- Genie fallback.

### Live suite: the release gate (audit quality-03 item 3)

The live suite (`nightly.yml`, `live validation (on demand)`) stays `workflow_dispatch`-only. A release may claim its six live checks only from a green run that `tools/live_validation_gate.py` certifies: the newest completed run on `main` whose code is runtime-equivalent to the release commit (every path is runtime except docs, repo-root `*.md`, `tests/`, `frontend/tests/`, `frontend/src/test/`, `*.test.ts(x)`, `.github/`, `.claude/` and `design_files/`; `genie/sample_questions.md` is runtime; the diff runs with `--no-renames`, so a rename counts its deleted source as well as its destination and moving runtime code into `tests/` or `docs/` is still a runtime change) must be green, 14 days old or newer, and its `SQL<->Python + Lakebase + Genie live`, `Playwright (real-UC golden path)` and `Credential-kill drill (simulated targets)` jobs must each have succeeded. `tools/release_readiness.py` takes those checks only from this gate (`--live-evidence github`, the default); an operator `passed` without a certified run is `unknown`, so `cannot_claim` blocks. `prod-readiness-gate` runs the same gate. Exit codes: 0 PASS, 1 FAIL, 2 API error (treated as FAIL). The fix for a FAIL is always the same: dispatch `deploy-dev.yml` on the release commit, then `nightly.yml` on that commit. `tests/unit/test_live_validation_gate.py` pins every verdict case.

### Live endpoint verifier (`tools/verify_live.py`)

`tools/verify_live.py` probes a deployed app with the operator's own OAuth bearer (`databricks auth token -p <profile>`); its report header names the profile, never an identity. Its `outreach.approve.real` probe is an **audited approval write on dev**: it approves one real borrower's draft and writes one APPROVE row, so it runs only when an operator invokes the tool explicitly, never from `nightly.yml` or any scheduled job. It certifies the audited draft the `outreach.draft` probe just produced: the body carries that draft's `body`, `subject`, `offer_code`, `channel`, `campaign_id`, `variant_name` and its proof (`generation_id`, `response_hash`, `source_refreshed_at`) verbatim, with `review_mode: "individual"`. When the draft probe failed or its proof is missing, no approve is sent and the probe reports `draft unavailable: approve not attempted (no audited draft proof)`. Data age: a source whose `checked_at` is older than `--max-source-age-days` (default 3) is flagged with its measured age (`checked_at is stale (66 d > 3 d)`), and the threshold is printed in the report header. On dev the flag is expected: only a full `deploy.sh` run or a refresh job moves `checked_at`, and a scheduled dev refresh was rejected (W5e) because it is metered and would only move timestamps. `tests/unit/test_verify_live.py` pins the approve body, the no-draft refusal and the threshold.

### Real-PostgreSQL contracts

Two suites run the ACTUAL SQL and DDL against a disposable PostgreSQL when `MIP_TEST_POSTGRES_DSN` names one, and skip otherwise: `tests/integration/test_genie_completion_jobs_postgres.py` (the Genie completion-job store, 8 tests) and `tests/integration/test_lakebase_schema_upgrade.py` (the Lakebase migration, 11 tests). In CI the `backend-tests` job runs a pinned `postgres:16.15-bookworm` service (by digest; 16 is assumed to be the Lakebase default major) with trust auth, and only the step "pytest (real PostgreSQL contracts, serial)" gets the DSN; the parallel step never does, so the suites skip there. That step revokes PostgreSQL's default PUBLIC privileges on schema `public` first (the dedicated Lakebase database's posture, which the migration closes itself where the provider schema exists), runs every DSN suite with `-n 0`, and fails when the junit report shows zero tests or any skip. A new DSN suite named `tests/integration/test_*_postgres.py` is picked up automatically; `tests/unit/test_ci_postgres_contracts.py` fails if any file under `tests/` reads the DSN but falls outside the step. Its shrink-only `KNOWN_UNRUN_DSN_SUITES` lists what the step does not gate on (each node id is deselected and re-run last with its recorded failure text: a pass fails the step as a stale entry, and a failure without that text fails it as a new defect); never add to it to go green.

Run them locally on a unique port, never 5432 on a shared machine:

```bash
docker run --rm -d --name mip-pg-$USER -e POSTGRES_HOST_AUTH_METHOD=trust -p 5439:5432 postgres:16.15-bookworm
psql 'host=127.0.0.1 port=5439 dbname=postgres user=postgres' -c 'REVOKE ALL ON SCHEMA public FROM PUBLIC'
MIP_TEST_POSTGRES_DSN='host=127.0.0.1 port=5439 dbname=postgres user=postgres' \
  pytest -n 0 -rs tests/integration/test_*_postgres.py tests/integration/test_lakebase_schema_upgrade.py
docker rm -f mip-pg-$USER
```

`-n 0` is required, and the two suites must never run concurrently: both `DROP SCHEMA mip_app CASCADE` and recreate it in the same database. Point the DSN only at a throwaway database.

### Growth Agent trust (wave 5)

Audit 2026-09-21 `critic-01`, `genie-09` part 1, `flow-08` slice 2 and `wow-ai-4` (backend):

- **Refusal batteries run on the reviewed-plan path.** `POST /api/growth-agent/agent/compose` no longer executes (`execute: true` is a 422 naming the retirement), so the 26 battery sites post through `post_growth_execute(client, objective, *, headers, states=None)` in `tests/unit/growth_refusal_contract.py`, which posts the objective to `POST /api/growth-agent/agent/plan/execute` with a minimal schema-valid plan and an unsigned digest. Each battery keeps its own assertions. `refusal_signature(response)` compares a guard refusal on its `refusal_reason` (and asserts the audit row) and a non-guard 422 on its fixed detail; `tests/unit/test_growth_agent_execute_parity.py` pins a 13-objective sample of the corpus plus the retirement.
- **The battery corpus.** Set `MIP_EXECUTE_PARITY_CORPUS=/path/in/your/scratchpad/corpus.jsonl` and the helper appends every objective it posts (JSONL). Unset means no write; never commit the file. The tree-swap differential replays that corpus against a FULL `git archive` of the base and the branch with `python -P` and diffs `{sha256(objective)[:16]: [status, refusal_reason]}` per path; the diff must be empty.
- **Watchlist series migration.** `tests/integration/test_growth_agent_runs_postgres.py` (a DSN suite) applies the `-- Growth Agent watchlist series ---` block TWICE through `lakebase_migrate._run_transaction` from the schema just before it, then pins the columns, foreign keys, partial index, seed backfill and one `schema_migrations` row, proves `growth_agent_runs` stays append-only (an UPDATE of `monitor_id` is 42501) and runs `WATCHLIST_SUMMARY_SQL` over seed, tagged, failed, one-off and other-actor runs. Locally, revoke PUBLIC on schema `public` first as CI does, or `test_lakebase_schema_upgrade.py::test_real_postgres_isolates_app_and_verifier_acl` fails on the default PostgreSQL grant.
- **Rendered layer.** `frontend/tests/e2e/fixture/growth-agent-trust.w5.fixture.spec.ts` (data in `data/growthAgentTrust.ts`: `registerRunWatchlistSave`, `homeWithScheduler`) proves focus on Run while pending and on Compose again / the diff summary / the Execution trace after, the objective lock, the hint geometry at 1440 and 768 px, Save as watchlist posting exactly `{tool_result_hash, cadence}` with one `/agent/run`, the three scheduled-run states, and axe in both themes.

## E2E tests

Playwright path:

1. Open home.
2. Build portfolio.
3. Select the In the Money segment.
4. Open lead queue.
5. Expand borrower.
6. Open Borrower 360.
7. Generate offer.
8. Approve outreach.
9. Confirm audit row.

### Legacy specs are typechecked and linted

The specs and helpers directly under `frontend/tests/e2e/` (the live suites and the few mock-backed ones such as `layout-stability.spec.ts`) are checked like the fixture harness. `npm --prefix frontend run typecheck:e2e` runs `tsc -p tests/e2e/tsconfig.json`, whose `compilerOptions` are the fixture tsconfig's, verbatim (`tests/unit/test_ci_frontend_gates.py` pins that); only its `include` differs: the non-recursive `./*.ts` glob, `playwright.config.ts` and `src/vite-env.d.ts`, since `fixture/` keeps its own project. `vite.config.ts` stays out: it imports `./src/lib/*.ts` with the extension, which those options reject. ESLint's `tests/e2e/*.ts` block applies the recommended TypeScript rules with the `^_` unused-variable patterns, without the fixture's type-only import ban (`layout-stability.spec.ts` imports `src/mocks/fixtureData` at runtime by design). Both run inside `npm --prefix frontend run lint`, whose chain ends `... && npm run lint:a11y && npm run typecheck:e2e`. Fix an error at its root: no `any`, no `@ts-expect-error`, no `eslint-disable`.

The live specs run only on operator dispatch (`nightly.yml` stays `workflow_dispatch`), so a selector they use for markup that no longer exists would fail only there. `src/test/e2eSourceGates.test.ts` therefore holds a retired-selector check: for each class in `RETIRED_OVERLAY_CLASSES` (`drawer-scrim` and `offer-mock-scrim`, both replaced by a native `<dialog>` and its `::backdrop` in wave 4a) that production source no longer renders, no `tests/e2e/**/*.ts` file may reference it. The evidence drawer is `dialog.drawer:not(.proof-drawer):not(.genie-proof-drawer)`; use `evidenceDrawer()` / `openEvidenceDrawer()` and `clickDialogBackdrop()` from `tests/e2e/helpers.ts` (mirrors of the fixture driver) rather than a raw selector.

## Acceptance tests

The app runs on live Unity Catalog + Lakebase; there is no mock-mode runtime path (see [CLAUDE.md](../CLAUDE.md) "Negative prompting"). Test criteria pin the live-data resilience contract instead.

- Evidence drawer opens from every KPI/score/recommendation and cites a real UC row.
- Human approval writes a real row to `mip_app.action_audit` in Lakebase.
- Borrower display fields pass PII redaction (initials only; generalized `{city}, {state} {zip}`).
- Degraded-state banner renders when warehouse / Genie / Lakebase drops, and clears on recovery — no silent mock fallback.
- Circuit breaker opens on SQL timeout; routes return 503 with `retry-after`, not stale mock data.
- Route p95 under live load stays inside the thresholds in [docs/load-baseline.md](load-baseline.md).

## Fixture harness (credential-free e2e)

`frontend/tests/e2e/fixture/` renders every route of the **production build** in Chromium with no backend and no credentials. It is the rendered-layer gate for UI work: a layout, theme, or interaction fix ships with a `*.fixture.spec.ts` assertion here. It runs on every pull request (`e2e-fixture` job in `.github/workflows/ci.yml`).

It is test infrastructure only. Nothing under `frontend/src` imports it, and the running app still has no mock path. The harness may `import type` from `frontend/src` (ESLint enforces type-only), so fixture payloads are checked against the app's own response types by `npm --prefix frontend run lint`.

### Run it

```bash
npm --prefix frontend run build                              # once; the harness serves frontend/dist
E2E_FIXTURE_PORT=5191 npm --prefix frontend run e2e:fixture  # whole suite
E2E_FIXTURE_PORT=5191 npm --prefix frontend run e2e:fixture -- smoke --grep "lead-queue"
npm --prefix frontend run e2e:fixture:ci                     # CI posture: forbidOnly, 1 retry, HTML report
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `E2E_FIXTURE=1` | set by the npm scripts | Selects fixture mode in `playwright.config.ts`: only `*.fixture.spec.ts`, `vite preview` web server, never uvicorn. Every other mode ignores `*.fixture.spec.ts`. |
| `E2E_FIXTURE_PORT` | `4273` | Port for `vite preview` (`--strictPort`, never reused). Give each parallel agent or job its own port; a busy port fails the run instead of attaching to someone else's build. |
| `E2E_FIXTURE_WORKERS` | `4` | Playwright workers. Lower it on a loaded machine. |
| `E2E_FIXTURE_NESTED=1` | unset | Internal to `runner.fixture.spec.ts`, which spawns a nested run that collects only `fixture/nested/*.nested.ts` (tests that fail on purpose) and starts no web server. Never set it by hand. |
| `MIP_VRT=1` | unset | Collects `visual.fixture.spec.ts` (every other fixture run ignores it) and writes artifacts to `test-results/vrt` and `playwright-report/vrt`. Only the `e2e-visual` CI job and `tools/update_visual_baselines.sh` set it; see "Visual regression". |
| `MIP_VRT_IMAGE` | unset | The Playwright image the run is inside. The VRT refuses to capture unless it is `mcr.microsoft.com/playwright:v<installed @playwright/test>-noble` on linux/x64. |
| `MIP_CROSS_ENGINE=1` | unset | Replaces fixture-chromium with the two cross-engine projects, `fixture-webkit` (`*.cross-engine.fixture.spec.ts` and `*.webkit.fixture.spec.ts`) and `fixture-firefox-forced` (`*.firefox.fixture.spec.ts` and `genie-pagehide.cross-engine`), and writes to `test-results/cross-engine` and `playwright-report/cross-engine`. Only the `e2e-cross-engine` CI job sets it; see "Cross-engine projects". A Playwright harness variable, not an app setting. |
| `MIP_PERF=1` | unset | Collects the `PERF_SPEC` specs, `perf-budget.fixture.spec.ts` (bundle-08) and `interaction-budget.fixture.spec.ts` (runtime-09), and writes to `test-results/perf` and `playwright-report/perf`. Only the single-worker "Run the perf and interaction budgets" step of the `e2e-fixture` CI job sets it, with the file filters `perf-budget interaction-budget` (without them MIP_PERF=1 also collects every normal fixture spec). See "Perf budget". |

Fixture pages run at 1440x900, `prefers-reduced-motion: reduce`, locale `en-US`, timezone `America/New_York`, with `Date` frozen at `2026-07-14T15:00:00Z` (`test.use({ fixtureNow: null })` restores the real clock). Rebuild after changing anything under `frontend/src`; the harness never rebuilds for you.

### Failure artifacts

A failed fixture test leaves, under `frontend/test-results/<test>/`, the failure screenshot, `error-context.md` (the ARIA snapshot Playwright writes for the error) and, in `attachments/`, a `trace-*.zip` that `npx playwright show-trace` opens and the CI HTML report links. The trace is recorded by the harness's own `failureTrace` fixture (`test.ts`) and discarded when a test's outcome matches its expectation, so a `test.fail()` pin costs nothing. A spec that runs real motion (`reducedMotion: 'no-preference'`) sets `test.use({ traceScreenshots: false })`: continuously animating pages make the trace screencast capture every frame, which roughly doubles those tests under load; DOM snapshots stay on, so the trace remains debuggable. Fixture mode deliberately keeps Playwright's `trace` option **off**: with `retain-on-failure`, Playwright 1.59 finalizes a failed test by merging two trace zips through its bundled yauzl, which on Node 26 never finishes reading an entry over 64 KiB (the failure screenshot always is one), so every failing test stalled for the whole test timeout and gained a spurious "Test timeout exceeded". `runner.fixture.spec.ts` pins that a failing test fails with its own error only, that the run terminates, and that the trace is attached; do not re-enable `trace` for fixture specs, not even per file.

### Write a spec

```ts
import { expect, test } from './test';   // never '@playwright/test' directly

test('lead queue shows the ranked borrowers', async ({ app, page }) => {
  await app.setTheme('light');          // app's own mip.theme key + mip.themeChosen + prefers-color-scheme
  await app.gotoRoute('/lead-queue');   // waits for the URL's route to be painted (data-route-path), h1, no aria-busy in <main>, API quiet, fonts
  await expect(page.locator('table.tbl tbody tr').first()).toBeVisible();
});
```

`app` also provides `openConsole()`, `openGenie()` (the topbar toggle; `.genie__fab` is hidden above 720px), `openCommandPalette()`, `expandFirstLeadRow()`, `settle()`, `degrade()` (returns a function that lifts the degraded state again, for recovery tests), `genieToggle()` and `geniePanel()` (the launcher and the docked panel, open or not), `evidenceDrawer()` / `openEvidenceDrawer(chip?)`, `openFilterMenu(label)` and `askGenie(question)`. Nothing answers a Genie turn by default: a spec that asks one first calls `registerGenieTurn(mockApi, script)` from `data/genieTurn.ts`, which scripts submit, progress and completion and returns counters plus `finishGenieTurn()` / `releaseComplete()` so the test owns the timing. `routes.ts` lists every route plus each Analytics tab; iterate it rather than re-listing paths. The app scrolls inside `.main`, so Playwright's `fullPage` screenshot option does nothing; scroll or resize `.main` instead.

### Hygiene: what fails a test

Every fixture test fails, after its own assertions, on any of:

| Check name | Trigger |
| --- | --- |
| `pageerror` | Uncaught exception or unhandled rejection in the page. |
| `console.error` | Any `console.error`. An error thrown inside a timer callback also lands here, because the frozen clock runs timers itself. |
| `csp` | A `securitypolicyviolation`. The harness serves every document with the production policy, read at test time from `SecurityHeadersMiddleware._CSP` in `backend/main.py`. |
| `request-failed` | A same-origin request that failed (aborted requests are ignored). |
| `unregistered-api` | An API call with no registered fixture. |

Opt out **by name**, as narrowly as possible, and say why in the test:

```ts
hygiene.allow('console.error', /ResizeObserver loop/);   // one known message
test.use({ hygieneOptOut: ['console.error'] });            // the whole check, for this file or describe
```

### The no-unregistered-endpoint rule

An API call with no fixture is answered `501`, recorded, and **fails the test with its method and path**:

```
[unregistered-api] GET /api/v1/sales/aging?older_than_days=7 has no registered fixture.
```

It is never answered with a retryable 503, because the app would render a believable "warming up" state and fixture drift would pass unnoticed. To fix it, add a typed entry to the matching module under `fixture/data/` (one module per API domain; keep each well under 400 lines):

```ts
fixture('GET', '/api/v1/sales/aging', () => json<SalesAgingLead[]>(AGING)),
```

The explicit type argument is the contract check: `tsc` rejects a payload that does not match the frontend's response type. Patterns are Express-style (`/api/v1/borrowers/:id/lifecycle`); both patterns and requests are version-normalized, so `/api/v1/...` and `/api/...` match each other, the more specific pattern wins, and registering the same `METHOD pattern` twice throws.

Only reads are registered by default. A test that exercises a write (approve, reject, assign, save) registers its own handler with `mockApi.register(...)`, so it controls the response timing; that is what a pessimistic-approval assertion needs. Fixture data is synthetic only: masked ids matching `B-[0-9A-Z]{13}`, lender `Summit Mortgage`, no names or contact fields. Headline numbers reconcile across panels (`data/reference.ts`), and `harness.fixture.spec.ts` pins that.

### Degraded states are opt-in

A route never renders degraded by accident. Ask for it:

```ts
import { WAREHOUSE_WARMING_UP } from './mockApi';

app.degrade('/api/v1/leads', WAREHOUSE_WARMING_UP);           // the backend's retryable 503 body
app.degrade(/^\/api\/analytics\//, { status: 500, body: { detail: 'boom' } });
```

The browser's "Failed to load resource" line for a degraded call is allowed automatically; anything else the degraded UI logs still fails the test.

### Owned storage in fixture specs

The app's actor gate (`frontend/src/lib/actorScope.ts`, [docs/security/identity-boundary.md](security/identity-boundary.md)) removes private browser data it finds without an owner stamp, so a spec that seeds pins, a Genie transcript, a conversation id or an in-flight record before boot seeds it owned:

```ts
import { FIXTURE_DEFAULT_ACTOR, seedOwnedStorage } from './ownedStorage';

await seedOwnedStorage(page, FIXTURE_DEFAULT_ACTOR, { session: { 'mip-genie-conversation-v1': JSON.stringify(turns) } });
```

`seedOwnedStorage(page, owner, {local, session})` writes both stamps (`mip.actorOwner`, `mip.actorCacheKey`) and the entries in one init script, once per tab. The default fixtures are a REAL actor, `FIXTURE_DEFAULT_ACTOR` (`actor_dddddddddddddddd`, D-identity-review-a4): `SESSION` and `HEALTH_OK` carry that key with `actor_cache_key_aliases: []`, so the gate opens for an owner and the app binds its requests to it, as it does live. Every default session variant (`PRESENTER_SESSION`, `AUDITOR_ONLY_SESSION`, `WORKSPACE_USER_SESSION`, `LO_SESSION`, `SIGNED_IN_APPROVER`) carries the SAME key, because a different one would read as a proven actor change at the first probe; `test_e2e_fixture_contract.py` checks that every sampled `/session` body pairs a null key with a null `actor_email` and carries `actor_cache_key_aliases`. A spec that needs another actor, or changes it, serves `GET /api/v1/session` and `GET /api/v1/health` from one variable with `serveActor(mockApi, () => actor, () => aliases)` (`data/shell.ts`; the session's `actor_email` is null exactly when the key is, and the aliases ride health only) and uses the well-formed `FIXTURE_ACTOR_A` / `FIXTURE_ACTOR_B` keys: the shell ignores a key that is not `actor_` plus 16 lowercase hex characters. `FIXTURE_NOBODY` (`~nobody`) is for a spec that serves no key. A spec that writes storage after boot (the stamps then exist) needs neither.

### Actor binding in fixture specs

Once the gate opens for a real actor, every app request carries `X-MIP-Actor-Key` (`frontend/src/lib/apiActorBinding.ts`); the mock API ignores it. A spec about the binding installs the server emulation AFTER the mock API, so it runs first:

```ts
import { installActorBinding } from './actorBinding';

hygiene.allow('console.error', /status of 409/);   // the browser logs each refused request
const binding = await installActorBinding(page, () => actor, () => aliases);
```

For a non-exempt `/api` request whose key names neither the served actor nor an alias it answers the real `409 actor_changed` body; everything else falls back to the mock API (health, session and telemetry are exempt, the server's closed set, and a request without the header passes). `binding.log` records method, path, key, the actor served at the time and the outcome (`exempt`, `passed`, `refused`); `binding.bound(from)` filters out the exempt routes. `actor-binding.fixture.spec.ts` is the lane spec; `identity-boundary.fixture.spec.ts` uses it in the mid-session and two-page cases.

Every new browser-storage key registers in `ACTOR_SCOPE_REGISTRY` (`frontend/src/lib/actorScope.ts`), and only the files listed in `actorScope.registry.test.ts` may touch storage: that AST gate fails on an unregistered `mip` key, an unreviewed storage call, or a whole-area `.clear()`.

### Visual regression (pixel baselines)

`fixture/visual.fixture.spec.ts` compares `toHaveScreenshot` baselines of the production build against the fixture API: every route in both themes, the nine `product: true` routes of `routes.ts` with the Console open, the shell states (evidence drawer, command palette, Genie panel, degraded, expanded row, empty queue, and the non-bannered failed read on Lead Queue and Segments), the second `.main` page of Home, Borrower 360 and the Offer detail, compact density, 1280x720, and teal / navy / red specimens of a KPI and the map legend. About 100 PNGs live in `fixture/visual.fixture.spec.ts-snapshots/`.

**State catalogue.** There is no separate component workbench: this fixture VRT is the component and state catalogue (audit quality-06). It renders the real production build inside the AppShell token cascade, so a capture proves more than an isolated component render would, and the baselines under `frontend/tests/e2e/fixture/visual.fixture.spec.ts-snapshots/` are browsable on GitHub, one PNG per route, state and theme. Adding a state means: a `FixtureState` in `fixture/fixtureStates.ts`, its `prepareState` (a LOAD state registers or degrades reads before navigation) or `enterState` (an OVERLAY state opens after the natural load, never an audited read), an entry in the spec's matrix and, where axe should scan it, in `axe.fixture.spec.ts`'s `EXTRA_STATES`; if its open popover holds the focus that IS the state (a menu or listbox that closes on a blur, a tip that unmounts on one), an entry in `HELD_FOCUS_STATES` (Analytics: `ANALYTICS_W5E_HELD_FOCUS`) so its capture keeps that focus instead of blurring, and if it opens content past the fold, a scroll in `enterState` and that content in `stateInViewport`, asserted on screen at capture time; then regenerate with `tools/update_visual_baselines.sh` in the pinned container. `read-failed` is the non-bannered failed read (Lead Queue fails `GET /api/v1/leads`; Segments fails `GET /api/v1/segments` and `GET /api/v1/geo/state-rollups`) with `WAREHOUSE_OUTAGE_503` (a 503 `retries_exhausted`, no Retry-After, planned terminal by `lib/retryPlan.ts`) while `/api/v1/health` stays OK; the bannered outage stays in `error-surfaces.fixture.spec.ts`. Fixture data never reaches the running app: the backstop is the pytest architecture gate (`tests/unit/test_architecture_boundaries.py::test_production_runtime_has_no_test_import_or_mock_mode`), with ESLint's import-hygiene block as the fast first line, and a pytest pin over the tracked tree (`tests/unit/test_no_*` for the retired workbench) keeps its wording, config and artefacts out.

Baselines are **amd64-Linux renders from the pinned Playwright image** (`mcr.microsoft.com/playwright:v<@playwright/test pin>-noble`). The spec runs only with `MIP_VRT=1`, and every test fails before capturing unless `MIP_VRT_IMAGE` names that image and the host is linux/x64, so a macOS or arm64 run can never write or compare a baseline. The defaults are strict: `animations: 'disabled'`, `caret: 'hide'`, `scale: 'css'`, `threshold: 0.2` and `maxDiffPixels: 0` (a ratio such as 0.002 is about 2,600 px at 1440x900, enough to hide a changed word). Nothing is masked by default: the clock is frozen, motion is reduced, fonts are self-hosted and there is no canvas. A region is masked only when a double run proves it unstable, with a comment naming the cause; today that is the map legend caption (a `backdrop-filter` layer whose caption lands 0.016px past a pixel boundary). The `e2e-visual` CI job runs the spec inside the image with `--retries=0`; on failure it uploads the HTML diff report (expected / actual / diff per capture).

Regenerate or compare with the script; never by hand and never outside the container:

```bash
bash tools/update_visual_baselines.sh           # regenerate every baseline
bash tools/update_visual_baselines.sh --check   # compare only; exit 1 on a diff
```

It runs `docker run --platform linux/amd64` on the image of the exact `@playwright/test` pin (on Apple Silicon, `colima start --arch x86_64` or `--vm-type vz --vz-rosetta`; emulation is 3-5x slower). It never bind-mounts the repo: the working tree (tracked plus untracked, non-ignored files, uncommitted edits included) is streamed in with `tar`, and only an empty temporary directory is mounted for the results, so a container `npm ci` can never touch your `node_modules`. Update mode empties the snapshots directory first, so orphans disappear, then prints the short status of that directory; on a failure the diff report lands in `frontend/playwright-report/vrt`.

The flow for a change that moves pixels on purpose: run the script, review every changed PNG (the report or an image diff), run it again to confirm the result is deterministic (the snapshots directory stays unchanged), and commit the PNGs with the change that caused them. **A PNG merge conflict is resolved only by regenerating on the merged tree**, never by picking a side.

### axe gate

`fixture/axe.ts` is the one axe helper, shared by the PR matrix (`axe.fixture.spec.ts`) and the live scan (`tests/e2e/accessibility.spec.ts`):

```ts
await expectAxeClean(page, { key: { route: 'home', state: 'default' }, theme, accent, known: KNOWN_VIOLATIONS });
```

One analyze runs the WCAG 2.0/2.1/2.2 A and AA tags plus `best-practice`. A rule with any WCAG tag gates at every impact, minor included; a best-practice-only rule is advisory: it is attached as `axe-best-practice.json` with one annotation and never fails. Nothing is excluded and no rule is disabled. The matrix scans the default state of every `routes.ts` route in both themes, the evidence drawer, command palette and Genie panel on the five core routes, the filter menu and an expanded row on Lead Queue, the degraded Home, the non-bannered failed read (`read-failed`) on Lead Queue and Segment Intelligence, and the teal / navy / red accents (`app.setAccent`) on Home, Lead Queue and Borrower 360.

`KNOWN_VIOLATIONS` is a ratchet keyed `route|state|rule`: each entry names the finding that owns the fix, the date, the themes and accents it reproduces in (accents default to `['bright']`) and a selector every violating node must match. An entry that stops reproducing fails as stale, so a fix retires its entry in the same change, and a key that names no scanned state fails the spec at load. Record a new violation only node-pinned and under a register id (or a new slug named in the commit); never add an exclusion. The live spec uses the same helper with an empty `LIVE_KNOWN_VIOLATIONS`, so it takes the same tags and fails on moderate and minor findings too.

`expectAxeClean` is the **only** way a spec scans (audit a11y-05). A feature spec that checks one state calls it with its own key and, usually, `known: {}`: `{ key: { route: '<spec slug>', state: '<entered state>' }, theme, known: {}, include: '.map-wrap' }`. `include` is one CSS selector; to scan two regions, make two calls (while the state that shows both is still on screen), never a wider selector. The helper returns the `AxeResults`, so a spec that needs more than "no gating violation" asserts on them, for example that a rule is absent from both `results.violations` and `results.incomplete`. A new violation found this way is fixed, not ratcheted. `src/test/e2eSourceGates.test.ts` (Vitest, node environment) walks `tests/e2e/**/*.ts` (skipping `*-snapshots`) and fails with `file:line` on any `new AxeBuilder(`, any `@axe-core/playwright` import, or the axe engine reached directly outside `fixture/axe.ts` (a runtime import, `require`, `import()` or `require.resolve` of `axe-core` or an `axe-core/...` subpath, an `addScriptTag(...)` whose argument names axe, or `axe.run(` / `axe.source` / `window.axe`; a type-only `import type ... from 'axe-core'` is allowed), except:

- `PERMANENT_RAW_AXE`: `safety-net.fixture.spec.ts`, count 1, a raw-engine probe of the ratchet's pure functions on a planted page that must see violations, which the gate would otherwise refuse.
- `PENDING_AXE_MIGRATION`: shrink-only `{ file: { count, owner, reason } }` for private scans in files another lane owns this wave. Each entry must still show exactly its count; a migrated file fails as stale and its entry is removed in the same change. Never add a file: migrate its scan.

### Accessibility-tree snapshots

`fixture/aria-snapshots.fixture.spec.ts` pins the structure of keyboard-critical surfaces with Playwright's `toMatchAriaSnapshot`: roles, accessible names and states (expanded, selected), never classes or pixels. Today it covers the Lead Queue table header (every columnheader and sort control, with the `aria-sort` values pinned beside it because they are outside the snapshot vocabulary) and the STATE filter's expanded trigger and listbox, at 1440x900 in the dark theme only (the tree is theme-independent), and it asserts `expectNoAuditedReadSince`: opening a filter never re-reads `GET /leads`. Snapshots are **inline** in the spec, so a reviewer reads the expected tree in the diff; there are no `.aria.yml` files. Pin only what Linux Geist Mono widths cannot move (no virtualized rows, no truncated text). `toMatchAriaSnapshot` needs Playwright 1.49 or later; the repo pins `@playwright/test` 1.64.0 (Chromium 156; W5e tail T3), and the VRT image follows that pin. Update only deliberately, then review every rewritten literal:

```bash
E2E_FIXTURE_PORT=<port> npm --prefix frontend run e2e:fixture -- aria-snapshots --update-snapshots --update-source-method=overwrite
```

### Virtual screen-reader transcripts

`frontend/src/test/screenReader/` drives `@guidepup/virtual-screen-reader` (an exact devDependency) over the real components in Vitest (happy-dom) and records what it speaks, step by step, as committed golden transcripts in `transcripts/<flow>.txt`: the shell's skip link, landmarks and route announcement, the Lead Queue table, a row's actions and the approval review, the evidence drawer, the command palette, the identity menu, the Genie panel's answer and provenance, and a form with validation errors. Each suite also asserts the phrases a user needs (names, roles, states, live-region text), and `transcripts.test.ts` keeps one golden per suite. `docs/accessibility/VPAT.md` cites them; `tests/unit/test_vpat_contract.py` fails when a cited file or transcript goes missing.

The harness is `virtualReader.ts`: `startReader()`, `reader.step(title, action)`, `next` / `nextUntil` / `previousForm` / `walkLandmarks` for the virtual cursor, `press` and `reportFocus` for focus, and `expectTranscript(name, reader)`. A changed transcript fails the suite; review the new text, then rewrite it on purpose (CI never writes one):

```bash
npm --prefix frontend run test -- src/test/screenReader -u
```

The reader is a model, not a screen reader, and each suite's header says what it cannot show: pronunciation (U+2212 stays the "−" character), `aria-activedescendant` (re-read the combobox with `reportFocus()`), an inserted `role="alert"` element (pin it on the DOM), native `showModal()` modality, a `<textarea>`'s typed text, speech queueing, and layout. NVDA, JAWS and VoiceOver passes still need a person (VPAT open items).

### Forced colors: automated in Chromium and Firefox

The fixture suite proves the forced-colors layer (`tokens.css` `@media (forced-colors: active)` and `design-system/components/33-contrast-modes.css`) in both engines that implement it. In Chromium, `css-hygiene.forced-ink.fixture.spec.ts` and `css-hygiene.modes.fixture.spec.ts` emulate `forcedColors: 'active'` and sample real pixels. Firefox implements forced colors itself, so `forced-colors.firefox.fixture.spec.ts` runs in the `fixture-firefox-forced` project (Firefox launched with `browser.display.document_color_use = 2`, "Override colors: Always"; see "Cross-engine projects") and walks the checklist the manual pass used to, in both themes: the focus ring on the topbar search, a rail link and a Lead Queue row control paints the system Highlight (sampled from the outline band's pixels); the active rail item, a segmented button, a filter chip, a drawer tab, the command palette's active row and the filter menu's focused and selected options paint the system pair (W5a ruling R2), sampled through `paintedInk.ts` at `SAMPLE_SCALE`: every text run and glyph inks the system HighlightText on the system Highlight fill (both resolved on a `forced-color-adjust: none` probe), at 4.5:1 (text) and 3:1 (glyphs) unless the palette's own pair is weaker (`highlightInkFaults`: the floor is min(4.5 or 3, the pair's ratio - 0.25)). CI's headless Linux Firefox paints its standin pair, #ffffff on #3399ff at 2.94:1, in the light theme, so a bare 4.5:1 measured the palette, not the CSS (W5b run 37521773357); a real high-contrast palette still gets the full floors, and a twin that inks a drawer tab with an authored colour clearing 4.5:1 proves the pairing check can fail. `css-hygiene.forced-ink` holds Chromium's Highlight states to the same oracle; confidence bars, the status-pill dot, map regions, legend bars and ZIP tiles keep a visible CanvasText edge; and the score chips keep their solid, dashed and dotted borders. Each test first proves `matchMedia('(forced-colors: active)')` and reads the system colours from a probe element, and a twin (`forced-color-adjust: none` plus a transparent outline on the topbar search) proves the ring check can fail. Each Highlight-state run records the pair it measured as a `forced palette` annotation.

The manual Firefox pass is retired. A check that fails for a CSS defect is annotated `test.fixme(true, '<owner lane> · a11y-10 item 4 / css-06 item 3 · <what failed>')` naming the lane that owns the CSS (w5-design-contract for `33-contrast-modes.css`, `tokens.css` and `01-app-shell.css` in W5a), and fixed in CSS by that lane, never waived; the fixme list in the spec is the record. The spec's logic is exercised in Chromium with emulated forced colors before it lands (the `browserName === 'chromium'` branch in its `beforeEach`); its Firefox verdict comes from CI's Linux runners, because Playwright Firefox does not start on the macOS 27 dev host.

### Cross-engine projects

The fixture harness runs in Chromium on every pull request; the `e2e-cross-engine` CI job ("e2e (fixture, WebKit + Firefox forced colors)", Linux, no secrets) adds WebKit and Firefox with `MIP_CROSS_ENGINE=1`, which makes `playwright.config.ts` build ONLY two projects, both at the fixture pins (1440x900, device scale 1, `en-US`, `America/New_York`, reduced motion):

- `fixture-webkit` (`devices['Desktop Safari']`) collects `WEBKIT_SPEC`: every `*.cross-engine.fixture.spec.ts` and every `*.webkit.fixture.spec.ts`.
- `fixture-firefox-forced` (`devices['Desktop Firefox']` with `firefoxUserPrefs {'browser.display.document_color_use': 2}`, Firefox's "Override colors: Always") collects `FIREFOX_FORCED_SPEC`: every `*.firefox.fixture.spec.ts` and `genie-pagehide.cross-engine.fixture.spec.ts`.

The testMatch contract: a `*.cross-engine.fixture.spec.ts` runs in fixture-chromium on every normal run AND in fixture-webkit; `ENGINE_ONLY_SPEC` (`*.webkit` / `*.firefox`) is ignored by every run except the cross-engine one. A lane's WebKit-only proof (w5-approval-core's pinned-focus fix may land as one) is a new `*.webkit.fixture.spec.ts` and is collected by fixture-webkit once both lanes merge. `tests/unit/test_ci_frontend_gates.py` pins the job, that no other job sets `MIP_CROSS_ENGINE`, and which file names each regex collects.

**Walks are focus()-driven.** WebKit's Tab skips buttons, links and checkboxes on macOS and Linux defaults (Safari's "Press Tab to highlight each item" is off), so a keyboard Tab walk proves nothing there. `fixture/focusWalk.ts` moves focus with `element.focus()` to the next or previous tabbable in DOM order (tabindex >= 0, not disabled, not inert, rendered and visible), waits two frames, and reports which sticky chrome (route nav, sticky header, pinned column, bulk bar) fully covers the focused box. A browser's focus scroll for `focus()` is the same "scroll into view if needed" a Tab performs. `queue-clearance.cross-engine.fixture.spec.ts` ports the Lead Queue clearance walks this way, each with its non-vacuity twin. `genie-pagehide.cross-engine.fixture.spec.ts` runs in all three engines: a reload while polling resumes the same turn once, a reload during the complete call never completes again, and a second page seeded with a wholesale copy of the first page's sessionStorage does not resume while the first holds the turn's Web Lock (`mip-genie-turn:<id>`), then resumes once after the first closes; an engine without `navigator.locks` takes the fail-closed path, annotated. A reload or close cancels requests on purpose, and WebKit on macOS names that `cancelled` and Firefox `NS_BINDING_ABORTED` where the harness only knows Chromium's `ERR_ABORTED`, so the spec allows exactly those messages for the reads it cancels. WebKit on Linux (the CI runner) spells it `Load request cancelled`; only the reload-during-complete test allows that spelling, and only for its own held `POST /api/v1/genie/message/complete`. Note that WebKit reveals a focused control at the next rendering update, not synchronously inside `focus()` as Chromium does: a twin that must see the reveal reads Chromium's scrollLeft synchronously and WebKit's peak through a window capture-phase scroll listener, so a restore added later cannot hide it.

**A known engine defect is a `test.fixme`, never a silent skip.** Its description names the owning lane and the finding id, for example `test.fixme(browserName === 'webkit', 'w5-approval-core · manual-check-2026-09-30 WebKit pinned-focus scroll: ...')`. The fixme list is the record; the owner lifts its fixme in the change that fixes the defect.

**Firefox on the dev host.** Playwright's Firefox does not start on the macOS 27 host ("Could not find profile folder"), so the Firefox project runs on CI's Linux runners, or locally inside the pinned `mcr.microsoft.com/playwright:v1.64.0-noble` container (Playwright 1.64.0 ships Firefox 157; the host failure was recorded on 155 and is not yet re-checked on 157). WebKit runs locally:

```bash
npm --prefix frontend run build
MIP_CROSS_ENGINE=1 E2E_FIXTURE_PORT=<port> npm --prefix frontend run e2e:fixture -- --project=fixture-webkit
```

### Cascade layers (css-05)

Every stylesheet sits in a cascade layer (2026-09-21 audit css-05 / stack-v3 / bundle-06). Layer precedence decides before specificity, so a lazy sheet can no longer win or lose by the order its chunk happened to load in.

**The manifest and the order rule.** `frontend/src/design-system/layers.css` holds the one statement, `@layer reset, tokens, base, components, routes, modes, print;`, and `main.tsx` imports it FIRST, before `./appRouter`: the first `@layer` name in document order fixes its rank, and app.tsx's sheets load before tokens.css. Never declare an order anywhere else (cssLayers.test.ts fails on a second statement). The minifier (Lightning CSS) keeps the order but rewrites the statement (it merges same-named blocks into the place each name is first mentioned and keeps `@layer reset;` / `@layer routes;` for the names no block declares), so the served check reads the order of first mention, not the statement.

**Which layer.**

- `tokens` / `base`: tokens.css wraps its custom-property carriers and its element styles in place; `fonts.css` stays an unlayered `@import`, so `@font-face` stays top level.
- `components`: the initial shell (app.transitions.css, EvidenceHoverCard.css, every components.css partial imported with `layer(components)`), then print.css through `print.layer.css` (its base position: print.css decides by specificity, `:root`-prefixed overrides beat screen compounds, plain defaults yield; a print layer above components and routes would let every default win). The blocks css-05 moved out of the initial sheet into lazy chunks (14-genie-markdown-and-proof.css, ProofPanels.css, asset.css, the chart chrome at the top of charts.css, the glossary layout at the top of glossary.css) keep `components`, the layer they cascaded in.
- `routes`: every other lazy colocated sheet, wrapped in place (opener after the header comment, `}` last, inner bytes unchanged, no re-indent).
- `modes`: 33-contrast-modes.css (`layer(modes)`) and each colocated sheet's forced-colors / prefers-contrast / prefers-reduced-transparency blocks, closed out of the sheet's main layer and reopened in place.
- `print`: each colocated sheet's `@media print` blocks, in place.
- `reset`: reserved, empty.

`frontend/src/test/cssLayerMap.json` is the map both guards read (the unit test through `cssLayerMap.ts`, the fixture spec with `node:fs`, since fixture files may only `import type` from `frontend/src`): `INITIAL_COMPONENT_SHEETS`, `ROUTES_EXCEPTIONS` (a lazy sheet kept in components, with the reason), `COMPONENTS_BLOCKS` (a routes sheet that keeps one rule in an in-place `@layer components { }` block because a more specific initial rule, or a `:root` print override, must keep beating it) and `PENDING_INTEGRATOR_WRAP` (sheets another lane edits in the same wave, wrapped by the integrator at the merge).

**`@font-face` and `@property`.** Both stay outside any layer: fonts.css is imported unlayered, and a colocated sheet closes its layer block around an `@property` registration. A partial imported with `layer(components)` may register one inside the layer (01-app-shell.css's `--route-nav-block` / `--nav-clear`); the cross-engine spec proves Chromium and WebKit still register it.

**The gates.**

- `src/design-system/cssLayers.test.ts` (source): the manifest and its import position; print.css only through print.layer.css; every partial's import layer and no partial wrapping itself; tokens.css holds only tokens and base blocks; every other sheet keeps nothing outside top-level `@layer` blocks but `@property`, in the layer the map assigns, with print and mode media only in print / modes blocks; no nested `@layer`, no `&` nesting; unique `@keyframes` / `@property` names; every PENDING path exists and is still unwrapped (wrap it and the test fails until its entry is deleted, so the set ends empty).
- `tests/e2e/fixture/css-layers.cross-engine.fixture.spec.ts` (served CSSOM, fixture-chromium and fixture-webkit): the order, no unlayered style rule on any route's natural load (exempting exactly the selectors a PENDING sheet declares, annotated, until the set is empty), the !important winners (the reduced-motion reset, the forced-colors active-chip ink, print's reveal opacity), a layered `@property` still registered, and every moved block still styling its surface.
- `tools/refactor_proof.py css ORIGINAL ENTRY --strip-layers` proves a wrap is a pure move: it drops the `@layer` statement and each in-place opener / closer line before the byte compare (`tests/unit/test_refactor_proof_css_layers.py`).

**Injected style twins.** A spec's `page.addStyleTag()` rule is unlayered, so it now beats every layered normal declaration whatever its specificity, while an injected `!important` LOSES to any layered `!important` (important layers run in reverse). A twin that must out-rank a layered important rule injects it inside `@layer reset { ... }`, the first layer, whose importants beat every later layer's.

**The differential recipe (a change that moves CSS between layers or files).** Two `git archive` trees (BASE at the merge base, PREVIEW at the head plus any pending wraps applied exactly as the integrator will), the same `node_modules`, each built and served by its own `vite preview`. An uncommitted capture spec derived from `visual.fixture.spec.ts` walks every VRT state (plus forced-colors, print, motion and screen passes over the routes the change touches) and dumps a hash of `getComputedStyle` for every element and its generated `::before` / `::after`, over every longhand and every custom property the CSS names, with running animations parked at time 0 and `url(#id)` references normalized (React `useId` values follow mount order). A BASE-vs-BASE rerun is the determinism control; any state that differs there is excluded and named. Capture the trees you compare in one window, concurrently or back to back: two captures of the same unchanged tree taken about two hours apart differed on 1,259 elements of three states, so a comparison across windows measures the clock, not the change. The same run can record, per state, which rules match which elements (every dynamic pseudo-class relaxed to true), so a static inversion scan can then evaluate any layer assignment offline: per element and longhand, the winner under the unlayered base cascade against the winner under the layers, in every consistent world over print, forced colors, contrast, transparency, motion, viewport widths, container queries and hover states.

### Surface overflow and audited reads

`fixture/visual.ts` also holds two guards the VRT and axe specs apply to every state they enter:

- `expectNoSurfaceOverflow(page, { route, state, theme })`: no `.surface` may have `scrollWidth > clientWidth`. Declared inner scrollers such as `.tbl-wrap` are `overflow: auto` children and do not trip it. Pre-existing overflow sits in the dated, finding-pinned, node-pinned `KNOWN_SURFACE_OVERFLOW` ratchet; stale entries fail. `smoke.fixture.spec.ts` runs it on every route and theme, so it gates every PR, not only the VRT.
- The audited-read guard (`markNaturalLoad` / `expectNoAuditedReadSince`): reads that write an audit row (`GET /api/v1/leads`, `GET /api/v1/borrowers/:id`, `GET /api/v1/borrowers/:id/proof`, `POST /api/v1/outreach/draft`, `POST /api/v1/offers/recommend`, `POST /api/v1/lookup/property-loan`) may happen only in a route's natural load (the Offer Orchestrator detail route loads recommend and draft by design). No state a spec enters afterwards (a drawer, a scroll, the Console) may call one.

`safety-net.fixture.spec.ts` proves these helpers, the accent and density seeding, the VRT host guard, and the capture discipline off the pinned host: each held-focus state loses its popover to the plain park and keeps it through `prepareCapture` (the capture's own pre-screenshot path), and each framed state's content is on screen there.

### Perf budget

`PERF_SPEC` in `playwright.config.ts` collects the specs that measure timing: `perf-budget.fixture.spec.ts` (LCP and TBT of cold, throttled loads; bundle-08) and `interaction-budget.fixture.spec.ts` (Lead Queue interactions; runtime-09, owned by the W4b lead-queue lane, which names its spec exactly that). They run only in the single-worker step "Run the perf and interaction budgets (single worker)" of the `e2e-fixture` job and are ignored by every other run:

```bash
MIP_PERF=1 npm --prefix frontend run e2e:fixture:ci -- perf-budget interaction-budget --workers=1
```

The file filters are required: `MIP_PERF=1` only stops ignoring `PERF_SPEC`, so without them the step would also run every normal fixture spec. A filter that matches no file yet adds nothing. `perf-motion.fixture.spec.ts` is not a `PERF_SPEC`: it pins motion and performance quick wins functionally and runs in the normal suite.

The contract for a `PERF_SPEC` spec: until its ceilings are calibrated from at least 3 reference-runner medians (median x 1.2, rounded up), it is **report-only**, logging its medians and asserting only functional invariants. `perf-budget` is calibrated on the reference runner (home LCP 2600 / TBT 600 ms, lead-queue LCP 2700 / TBT 1200 ms) and gates; `interaction-budget` starts report-only. A ceiling is ratcheted down, never raised to make a run green.

**The null-ceiling contract (runtime-09, quality-08).** Each spec declares one ceiling per metric, and `null` means report-only: `INTERACTION_CEILINGS_MS` in `interaction-budget.fixture.spec.ts` (`j`, `expand`, `keystroke`, all null) and `CEILINGS` in `perf-budget.fixture.spec.ts` (LCP, TBT and CLS per route; Borrower 360 and every CLS are null). The report test gates every non-null median (`<= ceiling`) and logs `gating: ...` or `report-only`. CLS is the largest session window (1 s gap, 5 s cap) of layout-shift entries without recent input, over the same span as TBT.

**Calibration.** Every perf run writes `test-results/perf/calibration/<name>.json` (`fixture/calibration.ts`: `{name, spec, medians, samples, run: {sha, runId, runAttempt, runnerOs, imageOs}}`, numbers and CI metadata only, never a borrower id or rendered text), and the `e2e-fixture` job uploads them, pass or fail, as `perf-calibration-<run id>-<attempt>` (30 days). To set or lower a ceiling, the integrator (never a lane) downloads three reference-runner runs of ONE sha and runs the tool, which prints the literal and the runs it cites and never edits a spec:

```bash
gh run download <run id> -n perf-calibration-<run id>-<attempt> -D /tmp/cal/<run id>   # three runs, one sha
node tools/perf_ceilings.mjs /tmp/cal
```

It takes, per artifact and metric, the median of the per-run medians x 1.2, rounded up to 10 ms (interactions), 100 ms (LCP / TBT) or 0.01 (CLS), and refuses fewer than three distinct `(runId, runAttempt)` runs, a local input without a `runId` (`--allow-local` for a dry run only) and mixed shas (`--allow-mixed-sha`). Record the cited runs in the comment above the ceiling; ratchet down, never up.

### Frontend budget targets

Audit quality-08. `npm --prefix frontend run budget` (tools/check_frontend_budgets.mjs) holds two kinds of number. Its **gates** are a ratchet and a change detector: the measured actual plus ~5% headroom, bumped only with a named feature and measured actuals (the policy at the top of `tools/frontend_budget_gates.mjs`, which holds the numbers; the script measures and enforces them). **Targets** in `tools/frontend_budget_targets.json` are the destination: an absolute KiB (brotli q11) per dimension (`initialJsBr`, `initialCssBr`, `totalJsBr`, and exactly one entry per `budgets.routes` key; a missing or extra key fails), each `{targetKiB, finding, waiver}`. `targetKiB: null` means no target yet; only the budget owner (the integrator) sets a number. A dimension over its target fails unless it carries an unexpired waiver `{until: YYYY-MM-DD, finding, owner, reason}`; an expired waiver fails, and a waiver on a dimension back under its target (or with no target) fails as stale, so waivers are dated and shrink-only. The rules live in `tools/frontend_budget_report.mjs` and `src/lib/budgetTargets.test.ts` pins every problem kind.

The checker's flags: `--json <path>` writes the per-chunk report (`{actuals, chunks: [{file, raw, gzip, br, initial, routeKeys}]}`; the `frontend-tests` job writes `frontend-budget.json`), `--base <json>` adds base and delta columns, `--report-only` never fails on a gate, and `--dist` / `--build-meta` measure another build. With `GITHUB_STEP_SUMMARY` set it appends the table `dimension | actual | gate | target` (plus `base | delta` with a base) to the job summary. The `bundle-delta` job (pull requests only, informational: it fails only on a tool error) builds the head, builds the merge-base with `origin/<base_ref>` in a `git worktree`, measures the base with the head's checker (`--report-only --dist --build-meta --json`) and prints head vs base in the step summary. It posts no PR comment and needs no token, so a fork PR gets the same table. It then builds the bundle treemap (`rollup-plugin-visualizer`, audit quality-08) and uploads it as the `bundle-treemap-<sha>` artifact (14 days): open the run's Summary page, download the artifact and open `treemap.html` (raw, gzip and brotli sizes per module). Locally, `npm --prefix frontend run build:analyze` writes `frontend/bundle-analyze/treemap.html` (ignored by Git). It runs `vite build --config vite.analyze.config.ts`, which merges the production config with the plugin, refuses to load without `ANALYZE=1` and builds into `frontend/bundle-analyze/dist`, never `frontend/dist`; the production `vite.config.ts` never imports the plugin (`tests/unit/test_bundle_treemap_contract.py`).

### React Compiler coverage gate

The production build compiles components with React Compiler 1.0, which silently ships a function unmemoized when it bails out (try/finally, a `throw` inside try/catch, a value block inside try/catch, a `'use no memo'` pragma). The `frontend-tests` CI job runs:

```bash
node tools/react_compiler_coverage.mjs --check tools/react_compiler_allowlist.json
```

It scans every `.tsx` and `.ts` file under `frontend/src` with the build's compiler options and fails on a bailout in a file the allowlist does not list, on a count above its entry, or on a stale entry (the file is gone, or it improved). Each entry records the counts, the owning finding, an owner and a date. When you fix a bailout, lower its entry in the same change with `--ratchet`, which only ever lowers counts and refuses while anything is unlisted or grown. **Never loosen the allowlist to go green**: fix a new bailout in code (hoist the value block, move the try/finally into a helper). A manual addition needs a finding id and a reviewer sign-off in the commit body. `--write-allowlist <path>` exists only to bootstrap a new list and refuses to overwrite one.

A fixed bailout is not free: once a component compiles, the compiler emits memo caches for its whole body, so measure `npm run budget` against your own build of the base before landing one. In wave 4 the Topbar move alone cost +1.05 KiB br of initial JS and the ActivationLoopPanel move +0.93 KiB br on the Offer Orchestrator closure, over their caps, so both stayed allowlisted; the `useMainScroll` move cost nothing measurable and landed.

### a11y lint ratchet (oxlint jsx-a11y)

Audit a11y-05 item 2. The pinned `oxlint` (an exact devDependency) runs its built-in jsx-a11y plugin over `frontend/src` with `frontend/.oxlintrc.json`: the plugin list is exactly `["jsx-a11y"]`, every category is `off`, and every jsx-a11y rule the pinned version lists (`oxlint --rules --format json`) is named explicitly, so an oxlint bump cannot widen the gate silently (the tool fails while the installed oxlint lists a rule the config does not name). Test files, `src/test/**` and `src/mocks/**` are ignored. The `frontend-tests` CI job runs it as its own step, and `npm --prefix frontend run lint` runs it last:

```bash
npm --prefix frontend run lint:a11y     # = node ../tools/oxlint_ratchet.mjs --check oxlint-baseline.json
```

`frontend/oxlint-baseline.json` records the hits per **file + rule + count**, never by line, so moving code inside a file changes nothing. `--check` fails on an UNLISTED file+rule key, a GROWN count, a STALE entry (a lower count, or no hit left in the file), a baseline recorded with another oxlint version, and any suppression directive. It also fails closed on unparseable output or a config error, on any diagnostic that is not jsx-a11y (config drift), on a vacuous run (no file linted, or a rule count other than the config enables), and when oxlint's `number_of_files` differs from the tool's own enumeration of `frontend/src` (every `.js .mjs .cjs .jsx .ts .mts .cts .tsx` file, `.d.ts` included, minus the config's four `ignorePatterns`): oxlint also honours ignore FILES the config check cannot see, so a nested `.gitignore` or a `frontend/.eslintignore` that drops a file fails the gate, naming the skipped files. It prints the per-rule totals on every run.

- **Fix a new hit in code; never add it.** A new file+rule key or a higher count is fixed in the component, not recorded.
- **`--ratchet` only lowers.** `node tools/oxlint_ratchet.mjs --ratchet frontend/oxlint-baseline.json` lowers counts, drops files with no hit left and accepts a new `oxlintVersion`; it refuses while anything is unlisted, grown or suppressed. A fix lowers its entry in the same change.
- **A pure move is governed.** When code moves to a NEW file, `--ratchet ... --moved-from <old> --moved-to <new>` (repeatable) transfers exactly the (rule, count) pairs that went stale in `<old>` to the new file's keys, refuses a move into a file the baseline already lists or a move that carries a new hit, never lets a per-rule total grow, and records the move in the baseline's `policy.moves`.
- **No suppressions.** Any `oxlint-disable` directive under `frontend/src`, and an `eslint-disable` directive that is bare, names a `jsx-a11y/` rule, or names a jsx-a11y rule BARE (`alt-text`: oxlint honours ESLint's disable comments, bare rule names included), fails the gate. Suppressions are banned, not ratcheted. The directive scan is deliberately stricter than oxlint's own scope: it also reads the test files, `src/test/**` and `src/mocks/**` that the config ignores, so a bare disable cannot sit in a file that later moves into scope (a disable that names a non-jsx-a11y rule stays allowed there).
- **No config-level disables.** They are banned like directives. `.oxlintrc.json` may set only `$schema`, `plugins`, `categories`, `rules` and `ignorePatterns`, and `ignorePatterns` must be exactly the four patterns above. An `overrides` block, `extends`, `settings` or an extra ignore pattern would turn a rule off for a file outside the directive scan, so the tool reports it as config drift and refuses to lint (`validateConfig`, pinned in `tests/unit/test_ci_frontend_gates.py`).
- **The baseline is a generated artifact.** Never hand-edit or hand-merge it. The integrator re-runs `--ratchet` after each merge; a lane commits only `--ratchet` output, in a separate final commit. `--write-baseline <new path>` exists only to bootstrap and refuses to overwrite.

Rules set to `off`, each with its reason:

- `jsx-a11y/prefer-tag-over-role`: its only remedy swaps a `div role="status|group|dialog|region|img|button"` for a native tag, which contradicts the prototype markup contract (`design_files/Module 0 Prototype.html` renders `div.genie role="dialog"`, `aside.tweaks role="dialog"` and `div.approval role="region"`; `design_files/Design System.html` renders `div.theme-toggle role="group"` and `role="button"` segment cards) and moves BEM selectors and VRT pixels. On 2026-09-25 it reported 159 hits in 81 files (80 of them `role="status"`).

The one reviewed rule option (W5e a11y-05; `REVIEWED_RULE_OPTIONS` in `tools/oxlint_ratchet_baseline.mjs`, which accepts exactly this array value and nothing else):

- `jsx-a11y/no-noninteractive-tabindex`: `["error", { "roles": ["tabpanel", "region"] }]`. A scrollable `role="region"` must take keyboard focus (WCAG 2.1.1; axe `scrollable-region-focusable`), so `region` joins the rule's interactive roles; `roles` replaces the default `["tabpanel"]`, which EvidenceDrawerBody's focusable tabpanel needs, so it stays. Only a LITERAL `role="region"` counts (oxlint cannot read an expression role), and a role-less `tabIndex` on a non-interactive element is still an error. The four focusable scrollers are the Console's `.audit-panel` (a tab stop only while it overflows, `hooks/useOverflowFocusable.ts`), the `?` sheet's `.cmdk__list`, the Triage deck's draft copy and Genie's verbatim `<pre>`.

### Lint depth (title ban, typed lint, strict TypeScript, BEM)

Audit critic-08, stack-07 items 3-5 and quality-10 steps 2-3 (W5e). Four gates, each fail-closed and shrink-only. `npm --prefix frontend run lint` runs the first two and the BEM gate; CI runs the TypeScript ratchet as its own step.

**The title= ban.** `TITLE_BAN` in `frontend/eslint.config.js` (in the formatting block's `no-restricted-syntax`, beside `FORMATTING_BAN`) makes a native `title=` on any lowercase JSX element an error, `iframe` excepted, and so is `title` on `Chip`, `Button`, `EvidenceChip`, `Link` and `NavLink`, which forward it to the DOM. SVG `<title>` elements and other components' `title` props (PageShell, SectionHeader, ...) are untouched. A native title is mouse-only, appears late in OS chrome and never on keyboard focus or touch; use `ui/Tooltip` or the Chip/Button `tooltip` prop. Migrate a site by these rules, keeping every existing `aria-label`:

- **R1 redundant:** the title repeats the accessible name or visible text: delete it.
- **R2 icon-only control:** the name goes in `aria-label` (add one if the title was the only name), plus `<Tooltip content={name}>`.
- **R3 supplementary text on a focusable control or link:** `<Tooltip>`, or the Button `tooltip` prop.
- **R4 supplementary text on a non-focusable element:** the Chip `tooltip` prop or `<Tooltip>`. A compliance fact must be visible text, never only a tooltip.
- **R5 disabled reason on a natively disabled button:** the Button `tooltip` prop or `<Tooltip>`; keep native `disabled`. Prefer a Tooltip that stays mounted (a reason while held, the description otherwise): a conditional wrapper remounts the button when it flips. A control with no description of its own while enabled keeps the conditional wrapper rather than gain one: on a natively `disabled` control (the Genie composer's Ask button, the refusal card's follow-up chips) the flip already drops focus, so the remount loses nothing; an `aria-disabled` control stays focusable, so it keeps the wrapper only where the reason flips from a control elsewhere, not from its own activation (the map's rate toggle flips on a segment or portfolio filter change).
- **R6 raw identifiers** (ids, hashes, the ISO time on `<time>`): R4. `<Timestamp>` already wraps its `<time>`.

On a list surface with dozens of rows prefer R1 wherever the row already shows the name. Tests read a tooltip through `src/test/tooltipText.ts` (unit) or `tests/e2e/fixture/tooltip.ts` (e2e), never `title`.

`TITLE_BAN_ALLOWLIST` named each file that still carried a site, as `'<owner lane> - <n> sites - <why>'`; it is shrink-only and EMPTY since the W5e integration (every owner lane migrated its own sites before the residual commit). `src/lib/titleBan.test.ts` fails when an entry's count is not its file's live hit count, so a new entry could only ever shrink; never add one. The allowlist override restates `FORMATTING_BAN` and `LEXICON_BAN` for its files (never `'off'`), and disappears through its length guard while the list is empty. The integrator removed the deprecated Chip/Button/EvidenceChip `title` passthrough in the same residual commit; `frontend/src/components/Primitives.titleProp.test.tsx` pins it with one `@ts-expect-error` per primitive, so `tsc -b` fails if a `title` prop comes back, and the compiler-lint fixture sweep's `EXPECTED_PENDING_TITLES` is empty (any native title on a swept route fails).

**Typed lint.** One block in `eslint.config.js` runs `@typescript-eslint/no-floating-promises`, `no-misused-promises` and `no-base-to-string` at their default options (no `checksVoidReturn` exemption) through `projectService`, over production source only. Fix a hit in code: `void` only for a fire-and-forget whose rejection is handled inside (react-router's `navigate`), `.catch(handler)` otherwise, and an async JSX handler becomes `() => void run()` where `run` handles its own errors. Pre-existing hits in files other W5e lanes own live in `frontend/eslint-suppressions.json`, ESLint's native bulk suppressions, applied by the CLI from `frontend/` with no flag. It only shrinks: an unused entry fails `eslint .`; prune it with `npx eslint . --prune-suppressions` (from `frontend/`). A new hit is never suppressed, the file names only those three rules, and no script or CI step may pass `--pass-on-unpruned-suppressions` or `--suppressions-location` (`src/test/eslintSuppressions.test.ts`).

**The TypeScript strict ratchet.** `frontend/tsconfig.json` (the build, `tsc -b`) targets ES2022 with `verbatimModuleSyntax`, `noImplicitReturns`, `noFallthroughCasesInSwitch`, `noUnusedLocals` and `noUnusedParameters`: every flag at zero over ALL of `src`. `frontend/tsconfig.strict.json` extends it with the flags that still have hits (`erasableSyntaxOnly`, `noImplicitOverride`, `noUncheckedIndexedAccess`) over production files only, and is not part of `tsc -b`:

```bash
node tools/ts_strict_ratchet.mjs --check tools/ts_strict_ratchet_baseline.json     # CI's 'TypeScript strict ratchet' step
node tools/ts_strict_ratchet.mjs --ratchet tools/ts_strict_ratchet_baseline.json   # after a fix: only lowers
```

The baseline is keyed by file + TS code + count, never by line. `--check` fails on an unlisted or grown hit, a stale entry, another TypeScript version, config drift (every required flag on in one of the two configs, none false, ES2022, the exact production include/exclude) and a suppression comment in production code (`@ts-ignore` and `@ts-nocheck` anywhere, `@ts-expect-error` outside `src/types/wireContract.*.check.ts`); it fails closed when the program's listed files differ from its own enumeration. A TypeScript upgrade fails `--check` on VERSION alone until `--ratchet` records the installed version, so the change that bumps TypeScript (or the integrator, after `npm ci` on a merged lock) runs `--ratchet` in the same change, beside the oxlint `--ratchet`; it refuses while a hit is unlisted or grown, so a hit the new compiler adds is fixed in code first. Fix a new hit with narrowing, `?? fallback`, an early return or an explicit tuple type; a non-null assertion only with an invariant comment saying why the index exists. **Promotion rule:** a flag moves from `tsconfig.strict.json` into `tsconfig.json` once it is zero over production AND tests.

**The BEM selector gate.** `tools/lint_css_literals.mjs` also fails on a class selector, in a rule prelude under `frontend/src/**/*.css`, that does not follow the prototype grammar `block(-word)*[__element(-word)*][--modifier(-word)*]` in lowercase ASCII. At-rule preludes (`@media`, `@supports`, `@layer`, `@keyframes` percentages, `@property`, ...), comments and strings are ignored. Never rename a prototype class to satisfy it: a departing class goes in `BEM_EXCEPTIONS` with a cite of a `design_files` line or a registered deviation id (`src/design-system/cssBemGrammar.test.ts` keeps each exception live and cited). It is dependency-free: stylelint stays out while its `braces` path has no patched release.

**The probe.** Another worktree can see its hits before these gates reach it, with no baseline, allowlist or suppression applied:

```bash
git show ux/w5e-compiler-lint:tools/lint_depth_probe.mjs > "$TMPDIR/lint_depth_probe.mjs"
node "$TMPDIR/lint_depth_probe.mjs" --repo <worktree> [--files <paths...>]
```

It lints with only `TITLE_BAN` and the three typed rules and runs tsc with all eight strict flags, loading every tool from `<repo>/frontend/node_modules`. `src/test/lintDepthProbe.test.ts` pins its embedded copies to `eslint.config.js` and `REQUIRED_FLAGS`.

### Fixture contract (every body against the backend's response model)

`tsc` checks a fixture payload only against the frontend's hand-written types, which can drift from the backend. `tests/unit/test_e2e_fixture_contract.py` closes that gap: every body the harness can serve is validated against the real FastAPI response model of the route the app would call, so a fixture UI cannot pass while the wire contract has moved.

1. `tools/export_e2e_fixtures.mjs --out <file>` exports the bodies as deterministic JSON records `{source, method, pattern, path, query, status, body}`. It runs on bare Node >= 22.18 (type stripping plus a `module.registerHooks` resolve hook for the extensionless relative imports), with no `npm ci` and no `node_modules`. It collects, in order: every `defaultFixtures()` entry, called with `PARAM_SAMPLES` for its `:params`, `BODY_SAMPLES` for its request body and once more per `QUERY_SAMPLES` query string (all in `fixture/contractSamples.ts`); then `contractSamples()` from `contractSamples.ts`; then `contractSamples()` from every `fixture/data/*.ts` module that exports one. An unknown `:param` (add it to `PARAM_SAMPLES`), a handler that throws or a malformed sample exits 1 and names its source.
2. The test resolves each sample the way Starlette dispatches it: the path is version-normalized to `/api/v1`, and the first canonical `APIRoute` in `backend.main.app.routes` whose `matches()` is `Match.FULL` owns it (so `/borrowers/search` beats `/borrowers/{id}`). A 2xx body must pass `TypeAdapter(model).validate_json(...)`, which runs the model validators (score-band canon, governed ids, name-shaped text, vocabularies). The model is `route.responses[status]["model"]` for a declared non-default 2xx (a 202 job receipt), otherwise the route's `response_model` at its declared `status_code` (200 when unset); a 2xx with neither fails. A non-2xx sample must still resolve to a route and stay synthetic, but is not validated against a model.
3. Every body must stay synthetic: each `*borrower_id` / `borrower_ids` string matches `^B-[0-9A-Z]{13}$`, every email-shaped string ends in `.example`, and any `lender_name` is `Summit Mortgage`.
4. A 2xx body may carry no key its model does not declare (`undeclared_keys`): the model drops such a key on the real wire, so a UI built on it would pass here and break live. The test validates the body, dumps the result with `mode='json'` once by alias and once by name, and walks the body against both: a dict key absent from both dumps is an extra (reported by dotted path and model), and lists are zipped by index. A `dict[str, ...]` field or an `extra="allow"` model keeps every key, so those pass. `KNOWN_EXTRAS` is shrink-only like `KNOWN_DRIFT` (`{finding, owner, recorded, why, keys}`), and each entry must still reproduce exactly its `keys` or it fails as stale. It is empty: the one hit found when the check landed, `app_env` in `HEALTH_OK` (only the admin health model declares it), was removed from the fixture.
5. Every `defaultFixtures()` key must yield at least one validated 2xx sample, and in-memory non-vacuity cases prove each rejection (missing required field, unknown path, wrong method, undeclared 2xx, unmasked id, wrong model for a declared 202, a route with no response model, a top-level extra, an extra inside a list item, a top-level omission, an omission inside a list item, a declared-202 body missing a defaulted key) and each pass (arbitrary keys in a `dict[str, ...]` field, a `/health` body missing a key its `exclude_unset` route leaves unset, a `LeadSummary` row without the `exclude=True` `row_refreshed_at`).
6. **Served 2xx bodies are complete** (quality-09 item 2, D-api-types-a4 part a4-i). Playwright serves a body verbatim, while FastAPI serializes every declared field, so a partial body renders a state live can never reach. `omitted_keys(body, model, route, status)` validates the body and dumps it the way the route itself serializes it: through the route's own flags (`by_alias`, `exclude_unset`, `exclude_defaults`, `exclude_none`, `include`, `exclude`) when the status is served by its `response_model`, or in full by alias for a declared raw status (the genie 202, which `tests/unit/test_api_types_generated.py` (c) proves is dumped in full). Every dotted path in that dump the body lacks is reported as `{Model} omits {paths} (the real server always sends them)`; lists are zipped by index, `dict[str, X]` keys come from the body, and `exclude=True` fields (`audit_sequence`, `audit_snapshot`, `LeadSummary.row_refreshed_at`) are never dumped. The partial bodies found when the check landed (44 sources, recorded 2026-09-30, owner `w5-wire-types`) sit in the shrink-only `KNOWN_OMISSIONS`: each entry must reproduce exactly its keys, a source the exporter no longer emits fails, and an entry is only ever narrowed or removed; a new partial body is completed in its fixture, never recorded. A completed field takes the value the real server would send, and PII-shaped fields take the masked shapes the model validators enforce (masked `B-` ids, `.example` emails, the sample lender). Capability fields in session fixtures (`can_approve`, `rum_enabled`, role labels) are written explicitly per scenario, never left to an implied default. Once a4-ii lands for a domain, its served bodies are typed with the generated presence view (`satisfies ApiResponse<'X'>` or `ApiOk<'GET /api/v1/...'>` from `src/types/api.gen.ts`, imported with `import type`), captured request bodies with `ApiRequest<'X'>`, and the domain's `KNOWN_OMISSIONS` entries are deleted; non-2xx bodies stay on the hand-written transport error shapes. Typed with the presence view today: `fixture/data/wireBases.ts` (the eight bases, served through `data/wireBasesContract.ts`'s `contractSamples()`, kept apart so `src/test` never pulls the harness into the app's tsc program) and `data/growthAgentTrust.ts`'s `LIVE_ANALYSIS_RUN`; the per-domain a4-ii conversions of the served bodies are the post-wave api-types completion slice.
7. **Board plans are plans the planner produces** (W5d distribution-board VRT review). A Distribution Board preview the fixture serves (`POST /api/v1/sales/distribute`, dry run or commit) is replayed by `planner_replay_problems` through the real `backend/services/sales_distribution_plan.py` on the preview's own officers (capacity, open book, region) and a pool of `pool_count` equal-score, out-of-region leads, and must come back identical: per-officer added counts, score sums and in-region counts, the placed total and `unplaced_no_capacity`. That is how `fixture/data/salesLoop.ts` builds every plan (`BOARD_PLANS`, all exported by its `contractSamples()`), so a baseline can never show a lead planned for an officer already over capacity (rule 2) or leads left unplaced beside an officer with room (rule 4). In-memory cases prove the replay accepts a reachable plan and rejects each of those two.

**A lane that adds a per-spec payload module exports `contractSamples()`** from it (the `ContractSample` type is in `contractSamples.ts`; `path`, `query` and `status` default to the pattern with `PARAM_SAMPLES` substituted, `''` and 200), so its bodies are validated without editing `contractSamples.ts` (`fixture/data/genie.ts` does this for `GENIE_HISTORY_SESSIONS`, and the test requires that sample, so this path stays exercised). The scenario builders specs register per test (decision receipts, Genie turns and refusals, degraded health, layout populations) are covered in `contractSamples.ts` today.

The exporter loads `fixture/data/**`, `registry.ts`, `mockApi.ts` and `contractSamples.ts` on bare Node, so ESLint holds those files to `consistent-type-imports` and forbids a runtime import of a bare package there: a package, or a frontend/src type, comes in only through an `import type` (or `export type`) declaration, not an inline `{ type X }` specifier, which Node keeps as a runtime import (`no-import-type-side-effects` for imports, a `no-restricted-syntax` selector for re-exports; `import()` is banned there too). `node:` builtins are fine, and so is an inline `type` beside a value import from another fixture module. `src/lib/fixtureImportRules.test.ts` pins that against the real config, and the contract test also runs the exporter on a copy of those modules alone (no `node_modules`, no `frontend/src`) and requires the same output, so a runtime import the rules miss still fails locally, not only in CI.

The rule for a fixture data module, then (w3 review #16): a module under `fixture/data/**`, or `registry.ts`, `mockApi.ts` or `contractSamples.ts`, may import at runtime only modules inside `EXPORTER_CLOSURE` (`test_e2e_fixture_contract.py`: those three files, `fixture/data/` and `frontend/package.json`), by relative path, plus `node:` builtins. Everything else, such as `app.ts`, `axe.ts`, `visual.ts`, `test.ts`, `@playwright/test` or anything in `frontend/src`, comes in only through `import type`, or the isolated exporter run fails with `ERR_MODULE_NOT_FOUND`.

Fix drift in the fixture, never in the model: a failing body gets a value the backend's own validators accept, preferring one that keeps the rendered text. `KNOWN_DRIFT` in the test is empty and shrink-only (a dated entry naming the finding and owner lane, allowed only when every valid value would change text a spec owned by another lane asserts; an entry that stops reproducing fails).

Where Node is missing or older than 22.18 the module **skips**, unless `MIP_REQUIRE_FIXTURE_CONTRACT=1`, which makes it **fail**. CI's `backend-tests` job installs Node and sets the flag on its pytest step, so the contract runs in the normal parallel suite.

```bash
node tools/export_e2e_fixtures.mjs --out /tmp/e2e-fixtures.json          # inspect what is exported
MIP_REQUIRE_FIXTURE_CONTRACT=1 pytest -q tests/unit/test_e2e_fixture_contract.py
```

`tests/unit/test_frontend_build_artifacts.py` has the same kind of switch: its served-layer and build-meta checks skip where `frontend/dist` is not built, and `MIP_REQUIRE_FRONTEND_DIST=1` (set by the `e2e-fixture` job right after its build) turns those skips into failures.

### Generated API types

`frontend/src/types/api.gen.ts` is generated from the committed OpenAPI baseline by `tools/gen_api_types.py` (decision D-api-types-a1/a2, audit quality-04 / stack-v1): a Python 3.11 standard-library emitter that reads `tests/fixtures/openapi_baseline.json` and imports neither `backend` nor another tool. It adds no npm dependency, no `overrides` block and no audit ignore. It is type-only and erases at build time, so the App source `scripts/deploy.sh` ships is unchanged in behaviour.

**Two views.** `ResponseSchemas` is the PRESENCE view of every canonical 2xx body: FastAPI serializes every declared field, so each property is present (`name: T`) and nullability comes only from an `anyOf` with `null`. `RequestSchemas` is the DECLARED view of request bodies and parameters (`required` as declared, `?:` otherwise). `ApiOperations` maps each canonical `'<METHOD> /api/v1/...'` operation to `{ pathParams, query, headers, body, ok }`, where `ok` is the union of its 2xx bodies (`POST /api/v1/genie/message/complete` is `GenieMessageResponse | GenieCompletionJobStatus`). The helpers are `ApiResponse<'X'>`, `ApiRequest<'X'>`, `ApiOk<'GET /api/v1/...'>` and `ApiBody<'POST /api/v1/...'>`. Non-2xx bodies (the 422 `HTTPValidationError`, whose `input`/`ctx` the handler strips) are never emitted; they stay on the hand-written transport error shapes.

**The presence rule is proven, not assumed.** `tests/unit/test_api_types_generated.py` (b) fails when a route's `response_model_exclude_unset`/`exclude_defaults` set differs from `ABSENT_KEY_ROOTS` (today `{HealthResponse}`, via `/api/v1/health` and its unversioned alias), and fails outright on `exclude_none`, `include`, `exclude` or `by_alias=False`, which the declared view cannot express. (c) AST-scans `backend/**/*.py` for every possibly-2xx `*Response(...)` built by hand (import aliases, module attributes and `status.HTTP_NNN_*` resolved; a non-literal status counts as 2xx) and allows only the listed genie 202 `model_dump(mode='json')` site, proven alias-free, and the two non-API `FileResponse` sites. The `$ref` closure of `ABSENT_KEY_ROOTS` keeps its declared `required` list; a schema in that closure that another operation also serves in full must be all-required (today `ForcedDegradedInfo`, 4 of 4), or the generator raises and the fix is a reviewed model split.

**Closed sets, fail-closed.** Exactly the 22 JSON Schema keywords the baseline uses, the formats `date-time`, `date` and `uuid`, the five parameter-object keys, and the reviewed header allowlist `{Idempotency-Key}` (cookie parameters and proxy identity headers raise). Anything else raises `UnsupportedSchema` with its JSON pointer; extend a set only with a golden case in `tests/unit/test_api_types_generator.py`. **Any W5a/W5b schema change must stay inside those 22 keywords** (no `Field(deprecated=...)`, no `set[...]`/`frozenset` (`uniqueItems`), no `lt=`/`gt=` (`exclusiveMaximum`), no `examples`, `maxProperties` or `multipleOf`, no new raw `Response` or 204) **and pass `screen_text`.**

**Content screen.** Schema, property and parameter descriptions become JSDoc in `frontend/src`, so they are scanned by the frontend literal gates (`test_score_threshold_guard.py`, `test_architecture_boundaries.py`). Every emitted description and string literal first passes `screen_text`, which raises on an email not ending `.example`, a masked borrower id, a phone number, an `http(s)://` URL, a score band edge, the threshold copy and the mock paths. A hit is fixed by rewording the backend docstring or `Field` description, never by exempting it. Operation summaries and descriptions are never emitted, because the baseline test does not pin them; that is why both the drift test and the baseline test are needed.

**Regenerate.** `python tools/regen_openapi_baseline.py` rewrites the baseline from the live app and then the TS module from those bytes (two status lines; commit both files). `npm --prefix frontend run gen:api` only re-renders the TS module from the committed baseline. `python tools/gen_api_types.py --check` exits 1 with the first 40 diff lines when the module is stale. `test_generated_api_types_are_current` fails with the regen command when the committed module differs byte for byte.

**Rules for the file.** Never hand-edit it. It is exempt from exactly one gate, the 900-line file-size limit, and only while its first line is the generator banner (`GENERATED_FILES` in `tools/check_file_sizes.py`, reported as `GENERATED`); every other scan (eslint, the literal gates, the oxlint ratchet, React Compiler coverage) applies. Import it only with `import type { ... }`, `import type * as` or `export type { ... }`: (d) fails an inline `{ type X }`, a value import, a side-effect import or an `import()`, and allows only interfaces, types and comments at column 0 of the module.

### Wire contract (hand types against the generated contract)

Audit quality-04 items 1-2 / stack-v1, decision D-api-types-a3 P1. The hand-written API types stay, but each one is now checked against `api.gen.ts`, and every network call site is bound to the operation its path resolves to. Two gates, both type-only (the P1 dist assets were byte-identical before and after):

- **tsc check files.** `frontend/src/types/wireContract.check.ts` holds the helpers (`Expect`, `WireFits`, `Elem`, `Shape`, `NoPhantomKeys`, `DeepNoPhantomKeys`); one `wireContract.<domain>.check.ts` per P3 domain (leads, portfolio, genie, growthAgent, analytics, admin, sales, activation) exports a `WireContract<Domain>` tuple, one element per line, checked by `tsc -b` in `npm run build`. A response pair is `Expect<WireFits<wire, hand>>` plus `Expect<NoPhantomKeys<hand, wire>>`; a request pair is `Expect<WireFits<hand, wire>>` plus `Expect<DeepNoPhantomKeys<hand, wire>>` (no undeclared key at any depth, so nothing the server would silently drop can leave the browser). The wire side is `ApiResponse<'S'>` / `ApiRequest<'S'>` for a schema-named hand type, or `ApiOk<'METHOD /api/v1/...'>` / `ApiBody<...>` for a call site whose type is not schema-named.
- **Binding pytest.** `pytest tests/unit/test_frontend_wire_contract.py -q` (no Node) proves that every schema-named hand type in `types.ts`, `types/*.ts`, `lib/apiTypes.ts` and `lib/apiClients/*.ts` has its pair, and that every `getJson` / `getJsonWithHeaders` / `postJson` / `putJson` / `patchJson` / `deleteJson` call under `frontend/src`, every raw `fetch` / `navigator.sendBeacon` of an API path (bare, `window.` / `globalThis.` / `self.`-qualified, or bracket-qualified, such as a call of `window['fetch']` or `navigator['sendBeacon']`; the URL mentions `apiPath(`, holds an `/api` string or template literal, or names a path const holding one, a function-local const resolving within its function before module scope), every `new EventSource(` / `new WebSocket(` of one (GET), every call of a same-module wrapper that forwards a parameter into one of those calls, every other `apiPath(` call (its method is the one the raw call in the same function that sends its local uses), their `.json() as H` casts and typed `.json()` assignments (`const x: H = await res.json()`, or `x = await res.json()` for a local declared `let x: H`) and the boot module's `BOOT_READS` resolve to exactly one ApiOperations key with bound type arguments (W5e `w5-wire-types` B1 added the bracket, EventSource / WebSocket, typed-assignment, local-const, apiPath-method and wrapper shapes). A raw URL the rules cannot resolve (a `${}` inside a segment, a `+` that appends anything but a `?` / `#` literal, an unknown const, an absolute URL) fails as `raw URL not statically resolvable`; a method they cannot read (an apiPath local no raw call sends, two calls sending different methods, a non-literal or spread init, a wrapper whose own call names none) fails as `method not statically known`; only a bare variable URL such as `fetch(url)` with a parameter is not a site. It prints the bound, exempt and annotated counts; the parser lives in `tests/unit/frontend_wire_contract.py` and `tests/unit/frontend_wire_raw_sites.py`, and its rules are pinned by `tests/unit/test_frontend_wire_contract_parser.py`.

Rules for a new or changed call:

- Every transport type argument is a bare identifier (optionally `[]`): never an inline object literal, `Array<{...}>`, union, intersection, `typeof` or `Record<...>`. Extract the shape as an exported type just above the client object and keep its fields exactly.
- A type named after its Pydantic schema and exported from a coverage file (`types.ts`, `types/*.ts`, `lib/apiTypes.ts`, `lib/apiClients/*.ts`) is bound by name, because the coverage test gives it a pair; any other type, including a same-named local interface in a route, needs a pair in its domain's check file against `ApiOk<'K'>` or `ApiBody<'K'>`.
- **P3 aliases** (D-api-types-a3 P3, W5e B4). A coverage-file type written as `export type X = ApiResponse<'X'>;` (or `ApiRequest<'X'>`) is its own coverage for that side only, and only when the schema key equals the exported name (`tests/unit/frontend_wire_aliases.py`); its pair would be tautological and is dropped. Any other alias form (another key, the other side, an intersection or a mapped type) keeps its pairs. Because the presence view makes every field required, an alias that breaks a test's partial literal is converted through `src/test/factories`, or the hand type and its pairs stay.
- **Vocabulary guards.** Every exported indexed-alias vocabulary in a coverage file (`export type Code = ApiResponse<'S'>['code']`, `NonNullable<ApiRequest<'S'>['f']>` and the like) needs an `Expect<string extends Code ? false : true>` line in a check file (`test_every_indexed_vocabulary_has_its_widening_guard`), so a backend field widened to `str` fails tsc instead of silently turning the vocabulary into `string`. Today: `GrowthAgentWorkflowId`, `GrowthAgentReviewedWorkflowId`, `GenieContextStateCode`, and outside the coverage files `WireOfferCode` / `RejectReasonCode` (A1).
- A raw `fetch` / `sendBeacon` of an API path, or an `apiPath(` call that builds a URL first, carries `// wire: 'METHOD /api/v1/...'` on the line directly above, as does each `BOOT_READS` entry. The annotation must equal the key the URL resolves to, and its method must be the one the call sends (`sendBeacon` POSTs; `fetch` reads `method:` from an object-literal init, a spread or a non-literal init fails). `/health` and `/telemetry/rum` are annotated, never exempted.
- `BINDING_EXEMPT` (keyed `file:function:ordinal`, never a line number) is shrink-only: today the four generic `<T>` admin response passthroughs (`adminRules`, `adminSources`, `adminOperations` and the `adminRunOperation` response; its body is bound) and `lib/bootPrime.ts`'s inferred cast. An entry that binds again fails as stale.
- **Zero wire drift** (quality-04 P2 complete, W5e `w5-wire-types` A9). Each of the 25 dated `// @ts-expect-error wire-drift quality-04 <YYYY-MM-DD> <owner-lane>: <what differs>` entries remaining at the W5e base (W5b recorded 29; W5c fixed 5, and one dated 2026-10-07 was added later) was fixed at its cause, in the backend schema or the hand type, and `DRIFT_OWNER_LANES` is an empty set: any such directive now fails as an unknown owner lane, and `test_no_wire_drift_remains` fails on any `@ts-expect-error` in a check file. A pair that fails is fixed at its cause in the same change (integrator decision 2), never parked behind an entry. Never cast and never edit a hand type just to make a pair pass. A fix that touches `approval_status`, `can_approve`, `can_access_admin`, `audit_event_id`, `approved_at`, `decision`, `approver` or an `event_type` keeps the fail-closed read (the rollups' nullable `event_type` renders 'Event type not recorded', never a default).
