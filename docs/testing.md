# Testing strategy

## Unit tests

- Scoring functions.
- Offer rules.
- Evidence formatting.
- Pydantic schema validation.
- Frontend component rendering.

## Integration tests

- API health.
- Portfolio preview.
- Borrower detail.
- Offer recommendation.
- Approval writes audit.
- Genie fallback.

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
| `MIP_PERF=1` | unset | Collects `perf-budget.fixture.spec.ts` (w2-build-currency) and writes to `test-results/perf` and `playwright-report/perf`. Only the single-worker "Run the perf budget" step of the `e2e-fixture` CI job sets it. |

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

`fixture/visual.fixture.spec.ts` compares `toHaveScreenshot` baselines of the production build against the fixture API: every route in both themes, the nine `product: true` routes of `routes.ts` with the Console open, the shell states (evidence drawer, command palette, Genie panel, degraded, expanded row, empty queue), the second `.main` page of Home, Borrower 360 and the Offer detail, compact density, 1280x720, and teal / navy / red specimens of a KPI and the map legend. About 96 PNGs live in `fixture/visual.fixture.spec.ts-snapshots/`.

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

One analyze runs the WCAG 2.0/2.1/2.2 A and AA tags plus `best-practice`. A rule with any WCAG tag gates at every impact, minor included; a best-practice-only rule is advisory: it is attached as `axe-best-practice.json` with one annotation and never fails. Nothing is excluded and no rule is disabled. The matrix scans the default state of every `routes.ts` route in both themes, the evidence drawer, command palette and Genie panel on the five core routes, the filter menu and an expanded row on Lead Queue, the degraded Home, and the teal / navy / red accents (`app.setAccent`) on Home, Lead Queue and Borrower 360.

`KNOWN_VIOLATIONS` is a ratchet keyed `route|state|rule`: each entry names the finding that owns the fix, the date, the themes and accents it reproduces in (accents default to `['bright']`) and a selector every violating node must match. An entry that stops reproducing fails as stale, so a fix retires its entry in the same change, and a key that names no scanned state fails the spec at load. Record a new violation only node-pinned and under a register id (or a new slug named in the commit); never add an exclusion. The live spec uses the same helper with an empty `LIVE_KNOWN_VIOLATIONS`, so it takes the same tags and fails on moderate and minor findings too.

### Surface overflow and audited reads

`fixture/visual.ts` also holds two guards the VRT and axe specs apply to every state they enter:

- `expectNoSurfaceOverflow(page, { route, state, theme })`: no `.surface` may have `scrollWidth > clientWidth`. Declared inner scrollers such as `.tbl-wrap` are `overflow: auto` children and do not trip it. Pre-existing overflow sits in the dated, finding-pinned, node-pinned `KNOWN_SURFACE_OVERFLOW` ratchet; stale entries fail. `smoke.fixture.spec.ts` runs it on every route and theme, so it gates every PR, not only the VRT.
- The audited-read guard (`markNaturalLoad` / `expectNoAuditedReadSince`): reads that write an audit row (`GET /api/v1/leads`, `GET /api/v1/borrowers/:id`, `GET /api/v1/borrowers/:id/proof`, `POST /api/v1/outreach/draft`, `POST /api/v1/offers/recommend`, `POST /api/v1/lookup/property-loan`) may happen only in a route's natural load (the Offer Orchestrator detail route loads recommend and draft by design). No state a spec enters afterwards (a drawer, a scroll, the Console) may call one.

`safety-net.fixture.spec.ts` proves these helpers, the accent and density seeding, and the VRT host guard.

### Perf budget

`perf-budget.fixture.spec.ts` measures timing, so it runs only in its own single-worker step of the `e2e-fixture` job (`MIP_PERF=1`, `--workers=1`) and is ignored by every other run.

### React Compiler coverage gate

The production build compiles components with React Compiler 1.0, which silently ships a function unmemoized when it bails out (try/finally, a `throw` inside try/catch, a value block inside try/catch, a `'use no memo'` pragma). The `frontend-tests` CI job runs:

```bash
node tools/react_compiler_coverage.mjs --check tools/react_compiler_allowlist.json
```

It scans every `.tsx` and `.ts` file under `frontend/src` with the build's compiler options and fails on a bailout in a file the allowlist does not list, on a count above its entry, or on a stale entry (the file is gone, or it improved). Each entry records the counts, the owning finding, an owner and a date. When you fix a bailout, lower its entry in the same change with `--ratchet`, which only ever lowers counts and refuses while anything is unlisted or grown. **Never loosen the allowlist to go green**: fix a new bailout in code (hoist the value block, move the try/finally into a helper). A manual addition needs a finding id and a reviewer sign-off in the commit body. `--write-allowlist <path>` exists only to bootstrap a new list and refuses to overwrite one.

### Fixture contract (every body against the backend's response model)

`tsc` checks a fixture payload only against the frontend's hand-written types, which can drift from the backend. `tests/unit/test_e2e_fixture_contract.py` closes that gap: every body the harness can serve is validated against the real FastAPI response model of the route the app would call, so a fixture UI cannot pass while the wire contract has moved.

1. `tools/export_e2e_fixtures.mjs --out <file>` exports the bodies as deterministic JSON records `{source, method, pattern, path, query, status, body}`. It runs on bare Node >= 22.18 (type stripping plus a `module.registerHooks` resolve hook for the extensionless relative imports), with no `npm ci` and no `node_modules`. It collects, in order: every `defaultFixtures()` entry, called with `PARAM_SAMPLES` for its `:params`, `BODY_SAMPLES` for its request body and once more per `QUERY_SAMPLES` query string (all in `fixture/contractSamples.ts`); then `contractSamples()` from `contractSamples.ts`; then `contractSamples()` from every `fixture/data/*.ts` module that exports one. An unknown `:param` (add it to `PARAM_SAMPLES`), a handler that throws or a malformed sample exits 1 and names its source.
2. The test resolves each sample the way Starlette dispatches it: the path is version-normalized to `/api/v1`, and the first canonical `APIRoute` in `backend.main.app.routes` whose `matches()` is `Match.FULL` owns it (so `/borrowers/search` beats `/borrowers/{id}`). A 2xx body must pass `TypeAdapter(model).validate_json(...)`, which runs the model validators (score-band canon, governed ids, name-shaped text, vocabularies). The model is `route.responses[status]["model"]` for a declared non-default 2xx (a 202 job receipt), otherwise the route's `response_model` at its declared `status_code` (200 when unset); a 2xx with neither fails. Non-2xx samples are counted and skipped.
3. Every body must stay synthetic: each `*borrower_id` / `borrower_ids` string matches `^B-[0-9A-Z]{13}$`, every email-shaped string ends in `.example`, and any `lender_name` is `Summit Mortgage`.
4. Every `defaultFixtures()` key must yield at least one validated 2xx sample, and in-memory non-vacuity cases prove each rejection (missing required field, unknown path, wrong method, undeclared 2xx, unmasked id, wrong model for a declared 202, a route with no response model).

**A lane that adds a per-spec payload module exports `contractSamples()`** from it (the `ContractSample` type is in `contractSamples.ts`; `path`, `query` and `status` default to the pattern with `PARAM_SAMPLES` substituted, `''` and 200), so its bodies are validated without editing `contractSamples.ts`. The scenario builders specs register per test (decision receipts, Genie turns and refusals, degraded health, layout populations) are covered there today.

The exporter loads `fixture/data/**`, `registry.ts`, `mockApi.ts` and `contractSamples.ts` on bare Node, so ESLint holds those files to `consistent-type-imports` and forbids a runtime import of a bare package there: a package, or a frontend/src type, comes in only through an `import type` (or `export type`) declaration, not an inline `{ type X }` specifier, which Node keeps as a runtime import (`no-import-type-side-effects` for imports, a `no-restricted-syntax` selector for re-exports; `import()` is banned there too). `node:` builtins are fine, and so is an inline `type` beside a value import from another fixture module. `src/lib/fixtureImportRules.test.ts` pins that against the real config, and the contract test also runs the exporter on a copy of those modules alone (no `node_modules`, no `frontend/src`) and requires the same output, so a runtime import the rules miss still fails locally, not only in CI.

Fix drift in the fixture, never in the model: a failing body gets a value the backend's own validators accept, preferring one that keeps the rendered text. `KNOWN_DRIFT` in the test is empty and shrink-only (a dated entry naming the finding and owner lane, allowed only when every valid value would change text a spec owned by another lane asserts; an entry that stops reproducing fails).

Where Node is missing or older than 22.18 the module **skips**, unless `MIP_REQUIRE_FIXTURE_CONTRACT=1`, which makes it **fail**. CI's `backend-tests` job installs Node and sets the flag on its pytest step, so the contract runs in the normal parallel suite.

```bash
node tools/export_e2e_fixtures.mjs --out /tmp/e2e-fixtures.json          # inspect what is exported
MIP_REQUIRE_FIXTURE_CONTRACT=1 pytest -q tests/unit/test_e2e_fixture_contract.py
```

`tests/unit/test_frontend_build_artifacts.py` has the same kind of switch: its served-layer and build-meta checks skip where `frontend/dist` is not built, and `MIP_REQUIRE_FRONTEND_DIST=1` (set by the `e2e-fixture` job right after its build) turns those skips into failures.

Generated OpenAPI types (`gen:api`, a codegen drift gate and a wire-assignability ratchet for the hand types) are not on the tree yet: the planned generator failed the `npm audit --audit-level=high` gate, so the hand-written types in `frontend/src/types*` remain the TypeScript-side contract and this test is the backend-side one.
