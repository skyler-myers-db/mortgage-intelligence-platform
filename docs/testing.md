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

Render and rerender run inside `await act(async ...)`, so effects and the microtasks they queue have flushed when the promise resolves. Every root is tracked and a module-level `afterEach` unmounts what a test left mounted and removes the containers `mount()` created (a `{ container }` you pass in is unmounted but left in place), so the next test starts with an empty body. When your own `afterEach` must unmount before it clears something the component reads (a `QueryClient`, `sessionStorage`), call `unmount()` there first: `mount()`'s hook runs after yours. It throws a clear error in a node-environment test. `src/test/` is test-only: ESLint bans production code from importing it, as it bans `src/mocks/`. The lane-owned suites use it today (Topbar, ActivationLoopPanel, DataOperationsPanel, useMainScroll); moving the other suites onto it and deleting their per-file flag is the wave-5 codemod.

Vitest detects an agent session and switches to a reporter that hides console output; pass `--reporter=default` when you need the `stderr | file > test` lines (for example to count act warnings).

### Mocking `lib/api`: keep the real module

A factory `vi.mock('../lib/api', () => ({ api: { ... } }))` REPLACES the module, so `ApiError`, `isAbortError`, `dependencyLabel` and every other export are undefined for the whole file. That passes until some module the test loads, often a route chunk a sibling test preloaded, imports one of them; then `x instanceof ApiError` throws, depending on test order, which is how it flaked on CI (fixed in bde112b0). Spread the real module and override only what the test drives:

```ts
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));
```

`src/test/apiMockShape.test.ts` enforces it. It parses every `src/**/*.{test,test-support}.{ts,tsx}` with the TypeScript compiler, finds each `vi.mock` / `vi.doMock` whose specifier resolves (relative to that file) to `src/lib/api`, and fails with `file:line` unless there is no factory (an automock keeps every export name), the returned object spreads the real module (`...(await importOriginal())`, `...(await vi.importActual(...))`, or an identifier bound to either, as `ask-genie.turn.test-support.tsx` does), or it defines `ApiError`. The `ApiError` escape is the wave-4 criterion; the 40 factories that take it still omit the other exports (`isAbortError`, `isWarmingUpError`, `dependencyLabel`, ...), so tightening the gate to "must spread" is wave 5. `PENDING_PARTIAL_API_MOCKS` is shrink-only: it lists bare factories in files another lane owns this wave, each entry must still fail (a converted one fails as stale and is removed), and a new file is converted, never added.

## Integration tests

- API health.
- Portfolio preview.
- Borrower detail.
- Offer recommendation.
- Approval writes audit.
- Genie fallback.

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

## E2E tests

Playwright path:

1. Open home.
2. Build portfolio.
3. Select Prime Refi Candidates segment.
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
  await app.setTheme('light');          // app's own mip.theme key + prefers-color-scheme
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

### Visual regression (pixel baselines)

`fixture/visual.fixture.spec.ts` compares `toHaveScreenshot` baselines of the production build against the fixture API: every route in both themes, the nine `product: true` routes of `routes.ts` with the Console open, the shell states (evidence drawer, command palette, Genie panel, degraded, expanded row, empty queue, and the non-bannered failed read on Lead Queue and Segments), the second `.main` page of Home, Borrower 360 and the Offer detail, compact density, 1280x720, and teal / navy / red specimens of a KPI and the map legend. About 100 PNGs live in `fixture/visual.fixture.spec.ts-snapshots/`.

**State catalogue.** There is no separate component workbench: this fixture VRT is the component and state catalogue (audit quality-06). It renders the real production build inside the AppShell token cascade, so a capture proves more than an isolated component render would, and the baselines under `frontend/tests/e2e/fixture/visual.fixture.spec.ts-snapshots/` are browsable on GitHub, one PNG per route, state and theme. Adding a state means: a `FixtureState` in `fixture/fixtureStates.ts`, its `prepareState` (a LOAD state registers or degrades reads before navigation) or `enterState` (an OVERLAY state opens after the natural load, never an audited read), an entry in the spec's matrix and, where axe should scan it, in `axe.fixture.spec.ts`'s `EXTRA_STATES`; then regenerate with `tools/update_visual_baselines.sh` in the pinned container. `read-failed` is the non-bannered failed read (Lead Queue fails `GET /api/leads`; Segments fails `GET /api/segments` and `GET /api/geo/state-rollups`) with `WAREHOUSE_OUTAGE_503` (a 503 `retries_exhausted`, no Retry-After, planned terminal by `lib/retryPlan.ts`) while `/api/health` stays OK; the bannered outage stays in `error-surfaces.fixture.spec.ts`. Fixture data never reaches the running app: the backstop is the pytest architecture gate (`tests/unit/test_architecture_boundaries.py::test_production_runtime_has_no_test_import_or_mock_mode`), with ESLint's import-hygiene block as the fast first line, and a pytest pin over the tracked tree (`tests/unit/test_no_*` for the retired workbench) keeps its wording, config and artefacts out.

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

`fixture/aria-snapshots.fixture.spec.ts` pins the structure of keyboard-critical surfaces with Playwright's `toMatchAriaSnapshot`: roles, accessible names and states (expanded, selected), never classes or pixels. Today it covers the Lead Queue table header (every columnheader and sort control, with the `aria-sort` values pinned beside it because they are outside the snapshot vocabulary) and the STATE filter's expanded trigger and listbox, at 1440x900 in the dark theme only (the tree is theme-independent), and it asserts `expectNoAuditedReadSince`: opening a filter never re-reads `GET /leads`. Snapshots are **inline** in the spec, so a reviewer reads the expected tree in the diff; there are no `.aria.yml` files. Pin only what Linux Geist Mono widths cannot move (no virtualized rows, no truncated text). `toMatchAriaSnapshot` needs Playwright 1.49 or later; the repo pins `@playwright/test` 1.63.0 (Chromium 153), and the VRT image follows that pin. Update only deliberately, then review every rewritten literal:

```bash
E2E_FIXTURE_PORT=<port> npm --prefix frontend run e2e:fixture -- aria-snapshots --update-snapshots --update-source-method=overwrite
```

### Forced colors: automated in Chromium and Firefox

The fixture suite proves the forced-colors layer (`tokens.css` `@media (forced-colors: active)` and `design-system/components/33-contrast-modes.css`) in both engines that implement it. In Chromium, `css-hygiene.forced-ink.fixture.spec.ts` and `css-hygiene.modes.fixture.spec.ts` emulate `forcedColors: 'active'` and sample real pixels. Firefox implements forced colors itself, so `forced-colors.firefox.fixture.spec.ts` runs in the `fixture-firefox-forced` project (Firefox launched with `browser.display.document_color_use = 2`, "Override colors: Always"; see "Cross-engine projects") and walks the checklist the manual pass used to, in both themes: the focus ring on the topbar search, a rail link and a Lead Queue row control paints the system Highlight (sampled from the outline band's pixels); the active rail item, a segmented button, a filter chip, a drawer tab, the command palette's active row and the filter menu's focused and selected options fill Highlight with readable text (>= 4.5:1) and glyphs (>= 3:1) through `paintedInk.ts` at `SAMPLE_SCALE`; confidence bars, the status-pill dot, map regions, legend bars and ZIP tiles keep a visible CanvasText edge; and the score chips keep their solid, dashed and dotted borders. Each test first proves `matchMedia('(forced-colors: active)')` and reads the system colours from a probe element, and a twin (`forced-color-adjust: none` plus a transparent outline on the topbar search) proves the ring check can fail.

The manual Firefox pass is retired. A check that fails for a CSS defect is annotated `test.fixme(true, '<owner lane> · a11y-10 item 4 / css-06 item 3 · <what failed>')` naming the lane that owns the CSS (w5-design-contract for `33-contrast-modes.css`, `tokens.css` and `01-app-shell.css` in W5a), and fixed in CSS by that lane, never waived; the fixme list in the spec is the record. The spec's logic is exercised in Chromium with emulated forced colors before it lands (the `browserName === 'chromium'` branch in its `beforeEach`); its Firefox verdict comes from CI's Linux runners, because Playwright Firefox does not start on the macOS 27 dev host.

### Cross-engine projects

The fixture harness runs in Chromium on every pull request; the `e2e-cross-engine` CI job ("e2e (fixture, WebKit + Firefox forced colors)", Linux, no secrets) adds WebKit and Firefox with `MIP_CROSS_ENGINE=1`, which makes `playwright.config.ts` build ONLY two projects, both at the fixture pins (1440x900, device scale 1, `en-US`, `America/New_York`, reduced motion):

- `fixture-webkit` (`devices['Desktop Safari']`) collects `WEBKIT_SPEC`: every `*.cross-engine.fixture.spec.ts` and every `*.webkit.fixture.spec.ts`.
- `fixture-firefox-forced` (`devices['Desktop Firefox']` with `firefoxUserPrefs {'browser.display.document_color_use': 2}`, Firefox's "Override colors: Always") collects `FIREFOX_FORCED_SPEC`: every `*.firefox.fixture.spec.ts` and `genie-pagehide.cross-engine.fixture.spec.ts`.

The testMatch contract: a `*.cross-engine.fixture.spec.ts` runs in fixture-chromium on every normal run AND in fixture-webkit; `ENGINE_ONLY_SPEC` (`*.webkit` / `*.firefox`) is ignored by every run except the cross-engine one. A lane's WebKit-only proof (w5-approval-core's pinned-focus fix may land as one) is a new `*.webkit.fixture.spec.ts` and is collected by fixture-webkit once both lanes merge. `tests/unit/test_ci_frontend_gates.py` pins the job, that no other job sets `MIP_CROSS_ENGINE`, and which file names each regex collects.

**Walks are focus()-driven.** WebKit's Tab skips buttons, links and checkboxes on macOS and Linux defaults (Safari's "Press Tab to highlight each item" is off), so a keyboard Tab walk proves nothing there. `fixture/focusWalk.ts` moves focus with `element.focus()` to the next or previous tabbable in DOM order (tabindex >= 0, not disabled, not inert, rendered and visible), waits two frames, and reports which sticky chrome (route nav, sticky header, pinned column, bulk bar) fully covers the focused box. A browser's focus scroll for `focus()` is the same "scroll into view if needed" a Tab performs. `queue-clearance.cross-engine.fixture.spec.ts` ports the Lead Queue clearance walks this way, each with its non-vacuity twin. `genie-pagehide.cross-engine.fixture.spec.ts` runs in all three engines: a reload while polling resumes the same turn once, a reload during the complete call never completes again, and a second page seeded with a wholesale copy of the first page's sessionStorage does not resume while the first holds the turn's Web Lock (`mip-genie-turn:<id>`), then resumes once after the first closes; an engine without `navigator.locks` takes the fail-closed path, annotated. A reload or close cancels requests on purpose, and WebKit names that `cancelled` and Firefox `NS_BINDING_ABORTED` where the harness only knows Chromium's `ERR_ABORTED`, so the spec allows exactly those messages for the reads it cancels. Note that WebKit reveals a focused control at the next rendering update, not synchronously inside `focus()` as Chromium does: a twin that must see the reveal reads Chromium's scrollLeft synchronously and WebKit's peak through a window capture-phase scroll listener, so a restore added later cannot hide it.

**A known engine defect is a `test.fixme`, never a silent skip.** Its description names the owning lane and the finding id, for example `test.fixme(browserName === 'webkit', 'w5-approval-core · manual-check-2026-09-30 WebKit pinned-focus scroll: ...')`. The fixme list is the record; the owner lifts its fixme in the change that fixes the defect.

**Firefox on the dev host.** Playwright Firefox 155 does not start on the macOS 27 host ("Could not find profile folder"), so the Firefox project runs on CI's Linux runners, or locally inside the pinned `mcr.microsoft.com/playwright:v1.63.0-noble` container. WebKit runs locally:

```bash
npm --prefix frontend run build
MIP_CROSS_ENGINE=1 E2E_FIXTURE_PORT=<port> npm --prefix frontend run e2e:fixture -- --project=fixture-webkit
```

### Surface overflow and audited reads

`fixture/visual.ts` also holds two guards the VRT and axe specs apply to every state they enter:

- `expectNoSurfaceOverflow(page, { route, state, theme })`: no `.surface` may have `scrollWidth > clientWidth`. Declared inner scrollers such as `.tbl-wrap` are `overflow: auto` children and do not trip it. Pre-existing overflow sits in the dated, finding-pinned, node-pinned `KNOWN_SURFACE_OVERFLOW` ratchet; stale entries fail. `smoke.fixture.spec.ts` runs it on every route and theme, so it gates every PR, not only the VRT.
- The audited-read guard (`markNaturalLoad` / `expectNoAuditedReadSince`): reads that write an audit row (`GET /api/v1/leads`, `GET /api/v1/borrowers/:id`, `GET /api/v1/borrowers/:id/proof`, `POST /api/v1/outreach/draft`, `POST /api/v1/offers/recommend`, `POST /api/v1/lookup/property-loan`) may happen only in a route's natural load (the Offer Orchestrator detail route loads recommend and draft by design). No state a spec enters afterwards (a drawer, a scroll, the Console) may call one.

`safety-net.fixture.spec.ts` proves these helpers, the accent and density seeding, and the VRT host guard.

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

Audit quality-08. `npm --prefix frontend run budget` (tools/check_frontend_budgets.mjs) holds two kinds of number. Its **gates** are a ratchet and a change detector: the measured actual plus ~5% headroom, bumped only with a named feature and measured actuals (the policy at the top of the file). **Targets** in `tools/frontend_budget_targets.json` are the destination: an absolute KiB (brotli q11) per dimension (`initialJsBr`, `initialCssBr`, `totalJsBr`, and exactly one entry per `budgets.routes` key; a missing or extra key fails), each `{targetKiB, finding, waiver}`. `targetKiB: null` means no target yet; only the budget owner (the integrator) sets a number. A dimension over its target fails unless it carries an unexpired waiver `{until: YYYY-MM-DD, finding, owner, reason}`; an expired waiver fails, and a waiver on a dimension back under its target (or with no target) fails as stale, so waivers are dated and shrink-only. The rules live in `tools/frontend_budget_report.mjs` and `src/lib/budgetTargets.test.ts` pins every problem kind.

The checker's flags: `--json <path>` writes the per-chunk report (`{actuals, chunks: [{file, raw, gzip, br, initial, routeKeys}]}`; the `frontend-tests` job writes `frontend-budget.json`), `--base <json>` adds base and delta columns, `--report-only` never fails on a gate, and `--dist` / `--build-meta` measure another build. With `GITHUB_STEP_SUMMARY` set it appends the table `dimension | actual | gate | target` (plus `base | delta` with a base) to the job summary. The `bundle-delta` job (pull requests only, informational: it fails only on a tool error) builds the head, builds the merge-base with `origin/<base_ref>` in a `git worktree`, measures the base with the head's checker (`--report-only --dist --build-meta --json`) and prints head vs base in the step summary. It posts no PR comment and needs no token, so a fork PR gets the same table. The bundle treemap (`rollup-plugin-visualizer`) is a later wave's.

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

`frontend/oxlint-baseline.json` records the hits per **file + rule + count**, never by line, so moving code inside a file changes nothing. `--check` fails on an UNLISTED file+rule key, a GROWN count, a STALE entry (a lower count, or no hit left in the file), a baseline recorded with another oxlint version, and any suppression directive. It also fails closed on unparseable output or a config error, on any diagnostic that is not jsx-a11y (config drift), and on a vacuous run (no file linted, or a rule count other than the config enables). It prints the per-rule totals on every run.

- **Fix a new hit in code; never add it.** A new file+rule key or a higher count is fixed in the component, not recorded.
- **`--ratchet` only lowers.** `node tools/oxlint_ratchet.mjs --ratchet frontend/oxlint-baseline.json` lowers counts, drops files with no hit left and accepts a new `oxlintVersion`; it refuses while anything is unlisted, grown or suppressed. A fix lowers its entry in the same change.
- **A pure move is governed.** When code moves to a NEW file, `--ratchet ... --moved-from <old> --moved-to <new>` (repeatable) transfers exactly the (rule, count) pairs that went stale in `<old>` to the new file's keys, refuses a move into a file the baseline already lists or a move that carries a new hit, never lets a per-rule total grow, and records the move in the baseline's `policy.moves`.
- **No suppressions.** Any `oxlint-disable` directive under `frontend/src`, and an `eslint-disable` directive that is bare or names a `jsx-a11y/` rule (oxlint honours ESLint's disable comments too), fails the gate. Suppressions are banned, not ratcheted. The directive scan is deliberately stricter than oxlint's own scope: it also reads the test files, `src/test/**` and `src/mocks/**` that the config ignores, so a bare disable cannot sit in a file that later moves into scope (a disable that names a non-jsx-a11y rule stays allowed there).
- **No config-level disables.** They are banned like directives. `.oxlintrc.json` may set only `$schema`, `plugins`, `categories`, `rules` and `ignorePatterns`, and `ignorePatterns` must be exactly the four patterns above. An `overrides` block, `extends`, `settings` or an extra ignore pattern would turn a rule off for a file outside the directive scan, so the tool reports it as config drift and refuses to lint (`validateConfig`, pinned in `tests/unit/test_ci_frontend_gates.py`).
- **The baseline is a generated artifact.** Never hand-edit or hand-merge it. The integrator re-runs `--ratchet` after each merge; a lane commits only `--ratchet` output, in a separate final commit. `--write-baseline <new path>` exists only to bootstrap and refuses to overwrite.

Rules set to `off`, each with its reason:

- `jsx-a11y/prefer-tag-over-role`: its only remedy swaps a `div role="status|group|dialog|region|img|button"` for a native tag, which contradicts the prototype markup contract (`design_files/Module 0 Prototype.html` renders `div.genie role="dialog"`, `aside.tweaks role="dialog"` and `div.approval role="region"`; `design_files/Design System.html` renders `div.theme-toggle role="group"` and `role="button"` segment cards) and moves BEM selectors and VRT pixels. On 2026-09-25 it reported 159 hits in 81 files (80 of them `role="status"`).

### Fixture contract (every body against the backend's response model)

`tsc` checks a fixture payload only against the frontend's hand-written types, which can drift from the backend. `tests/unit/test_e2e_fixture_contract.py` closes that gap: every body the harness can serve is validated against the real FastAPI response model of the route the app would call, so a fixture UI cannot pass while the wire contract has moved.

1. `tools/export_e2e_fixtures.mjs --out <file>` exports the bodies as deterministic JSON records `{source, method, pattern, path, query, status, body}`. It runs on bare Node >= 22.18 (type stripping plus a `module.registerHooks` resolve hook for the extensionless relative imports), with no `npm ci` and no `node_modules`. It collects, in order: every `defaultFixtures()` entry, called with `PARAM_SAMPLES` for its `:params`, `BODY_SAMPLES` for its request body and once more per `QUERY_SAMPLES` query string (all in `fixture/contractSamples.ts`); then `contractSamples()` from `contractSamples.ts`; then `contractSamples()` from every `fixture/data/*.ts` module that exports one. An unknown `:param` (add it to `PARAM_SAMPLES`), a handler that throws or a malformed sample exits 1 and names its source.
2. The test resolves each sample the way Starlette dispatches it: the path is version-normalized to `/api/v1`, and the first canonical `APIRoute` in `backend.main.app.routes` whose `matches()` is `Match.FULL` owns it (so `/borrowers/search` beats `/borrowers/{id}`). A 2xx body must pass `TypeAdapter(model).validate_json(...)`, which runs the model validators (score-band canon, governed ids, name-shaped text, vocabularies). The model is `route.responses[status]["model"]` for a declared non-default 2xx (a 202 job receipt), otherwise the route's `response_model` at its declared `status_code` (200 when unset); a 2xx with neither fails. A non-2xx sample must still resolve to a route and stay synthetic, but is not validated against a model.
3. Every body must stay synthetic: each `*borrower_id` / `borrower_ids` string matches `^B-[0-9A-Z]{13}$`, every email-shaped string ends in `.example`, and any `lender_name` is `Summit Mortgage`.
4. A 2xx body may carry no key its model does not declare (`undeclared_keys`): the model drops such a key on the real wire, so a UI built on it would pass here and break live. The test validates the body, dumps the result with `mode='json'` once by alias and once by name, and walks the body against both: a dict key absent from both dumps is an extra (reported by dotted path and model), and lists are zipped by index. A `dict[str, ...]` field or an `extra="allow"` model keeps every key, so those pass. `KNOWN_EXTRAS` is shrink-only like `KNOWN_DRIFT` (`{finding, owner, recorded, why, keys}`), and each entry must still reproduce exactly its `keys` or it fails as stale. It is empty: the one hit found when the check landed, `app_env` in `HEALTH_OK` (only the admin health model declares it), was removed from the fixture.
5. Every `defaultFixtures()` key must yield at least one validated 2xx sample, and in-memory non-vacuity cases prove each rejection (missing required field, unknown path, wrong method, undeclared 2xx, unmasked id, wrong model for a declared 202, a route with no response model, a top-level extra, an extra inside a list item, a top-level omission, an omission inside a list item, a declared-202 body missing a defaulted key) and each pass (arbitrary keys in a `dict[str, ...]` field, a `/health` body missing a key its `exclude_unset` route leaves unset, a `LeadSummary` row without the `exclude=True` `row_refreshed_at`).
6. **Served 2xx bodies are complete** (quality-09 item 2, D-api-types-a4 part a4-i). Playwright serves a body verbatim, while FastAPI serializes every declared field, so a partial body renders a state live can never reach. `omitted_keys(body, model, route, status)` validates the body and dumps it the way the route itself serializes it: through the route's own flags (`by_alias`, `exclude_unset`, `exclude_defaults`, `exclude_none`, `include`, `exclude`) when the status is served by its `response_model`, or in full by alias for a declared raw status (the genie 202, which `tests/unit/test_api_types_generated.py` (c) proves is dumped in full). Every dotted path in that dump the body lacks is reported as `{Model} omits {paths} (the real server always sends them)`; lists are zipped by index, `dict[str, X]` keys come from the body, and `exclude=True` fields (`audit_sequence`, `audit_snapshot`, `LeadSummary.row_refreshed_at`) are never dumped. The partial bodies found when the check landed (44 sources, recorded 2026-09-30, owner `w5-wire-types`) sit in the shrink-only `KNOWN_OMISSIONS`: each entry must reproduce exactly its keys, a source the exporter no longer emits fails, and an entry is only ever narrowed or removed; a new partial body is completed in its fixture, never recorded. A completed field takes the value the real server would send, and PII-shaped fields take the masked shapes the model validators enforce (masked `B-` ids, `.example` emails, the sample lender). Capability fields in session fixtures (`can_approve`, `rum_enabled`, role labels) are written explicitly per scenario, never left to an implied default. Once a4-ii lands for a domain, its served bodies are typed with the generated presence view (`satisfies ApiResponse<'X'>` or `ApiOk<'GET /api/v1/...'>` from `src/types/api.gen.ts`, imported with `import type`), captured request bodies with `ApiRequest<'X'>`, and the domain's `KNOWN_OMISSIONS` entries are deleted; non-2xx bodies stay on the hand-written transport error shapes.

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

**The presence rule is proven, not assumed.** `tests/unit/test_api_types_generated.py` (b) fails when a route's `response_model_exclude_unset`/`exclude_defaults` set differs from `ABSENT_KEY_ROOTS` (today `{HealthResponse}`, via `/api/v1/health` and `/api/health`), and fails outright on `exclude_none`, `include`, `exclude` or `by_alias=False`, which the declared view cannot express. (c) AST-scans `backend/**/*.py` for every possibly-2xx `*Response(...)` built by hand (import aliases, module attributes and `status.HTTP_NNN_*` resolved; a non-literal status counts as 2xx) and allows only the listed genie 202 `model_dump(mode='json')` site, proven alias-free, and the two non-API `FileResponse` sites. The `$ref` closure of `ABSENT_KEY_ROOTS` keeps its declared `required` list; a schema in that closure that another operation also serves in full must be all-required (today `ForcedDegradedInfo`, 4 of 4), or the generator raises and the fix is a reviewed model split.

**Closed sets, fail-closed.** Exactly the 22 JSON Schema keywords the baseline uses, the formats `date-time`, `date` and `uuid`, the five parameter-object keys, and the reviewed header allowlist `{Idempotency-Key}` (cookie parameters and proxy identity headers raise). Anything else raises `UnsupportedSchema` with its JSON pointer; extend a set only with a golden case in `tests/unit/test_api_types_generator.py`. **Any W5a/W5b schema change must stay inside those 22 keywords** (no `Field(deprecated=...)`, no `set[...]`/`frozenset` (`uniqueItems`), no `lt=`/`gt=` (`exclusiveMaximum`), no `examples`, `maxProperties` or `multipleOf`, no new raw `Response` or 204) **and pass `screen_text`.**

**Content screen.** Schema, property and parameter descriptions become JSDoc in `frontend/src`, so they are scanned by the frontend literal gates (`test_score_threshold_guard.py`, `test_architecture_boundaries.py`). Every emitted description and string literal first passes `screen_text`, which raises on an email not ending `.example`, a masked borrower id, a phone number, an `http(s)://` URL, a score band edge, the threshold copy and the mock paths. A hit is fixed by rewording the backend docstring or `Field` description, never by exempting it. Operation summaries and descriptions are never emitted, because the baseline test does not pin them; that is why both the drift test and the baseline test are needed.

**Regenerate.** `python tools/regen_openapi_baseline.py` rewrites the baseline from the live app and then the TS module from those bytes (two status lines; commit both files). `npm --prefix frontend run gen:api` only re-renders the TS module from the committed baseline. `python tools/gen_api_types.py --check` exits 1 with the first 40 diff lines when the module is stale. `test_generated_api_types_are_current` fails with the regen command when the committed module differs byte for byte.

**Rules for the file.** Never hand-edit it. It is exempt from exactly one gate, the 900-line file-size limit, and only while its first line is the generator banner (`GENERATED_FILES` in `tools/check_file_sizes.py`, reported as `GENERATED`); every other scan (eslint, the literal gates, the oxlint ratchet, React Compiler coverage) applies. Import it only with `import type { ... }`, `import type * as` or `export type { ... }`: (d) fails an inline `{ type X }`, a value import, a side-effect import or an `import()`, and allows only interfaces, types and comments at column 0 of the module.
