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

Fixture pages run at 1440x900, `prefers-reduced-motion: reduce`, locale `en-US`, timezone `America/New_York`, with `Date` frozen at `2026-07-14T15:00:00Z` (`test.use({ fixtureNow: null })` restores the real clock). Rebuild after changing anything under `frontend/src`; the harness never rebuilds for you.

### Failure artifacts

A failed fixture test leaves, under `frontend/test-results/<test>/`, the failure screenshot, `error-context.md` (the ARIA snapshot Playwright writes for the error) and, in `attachments/`, a `trace-*.zip` that `npx playwright show-trace` opens and the CI HTML report links. The trace is recorded by the harness's own `failureTrace` fixture (`test.ts`) and discarded when a test's outcome matches its expectation, so a `test.fail()` pin costs nothing. Fixture mode deliberately keeps Playwright's `trace` option **off**: with `retain-on-failure`, Playwright 1.59 finalizes a failed test by merging two trace zips through its bundled yauzl, which on Node 26 never finishes reading an entry over 64 KiB (the failure screenshot always is one), so every failing test stalled for the whole test timeout and gained a spurious "Test timeout exceeded". `runner.fixture.spec.ts` pins that a failing test fails with its own error only, that the run terminates, and that the trace is attached; do not re-enable `trace` for fixture specs, not even per file.

### Write a spec

```ts
import { expect, test } from './test';   // never '@playwright/test' directly

test('lead queue shows the ranked borrowers', async ({ app, page }) => {
  await app.setTheme('light');          // app's own mip.theme key + prefers-color-scheme
  await app.gotoRoute('/lead-queue');   // waits for h1, no aria-busy in <main>, API quiet, fonts
  await expect(page.locator('table.tbl tbody tr').first()).toBeVisible();
});
```

`app` also provides `openConsole()`, `openGenie()` (the topbar toggle; `.genie__fab` is hidden above 720px), `openCommandPalette()`, `expandFirstLeadRow()`, `settle()` and `degrade()`. `routes.ts` lists every route plus each Analytics tab; iterate it rather than re-listing paths. The app scrolls inside `.main`, so Playwright's `fullPage` screenshot option does nothing; scroll or resize `.main` instead.

### Hygiene: what fails a test

Every fixture test fails, after its own assertions, on any of:

| Check name | Trigger |
| --- | --- |
| `pageerror` | Uncaught exception or unhandled rejection in the page. |
| `console.error` | Any `console.error`. An error thrown inside a timer callback also lands here, because the frozen clock runs timers itself. |
| `csp` | A `securitypolicyviolation`. The harness serves every document with the production policy, read at test time from `SecurityHeadersMiddleware._CSP` in `backend/services/security_headers.py` (falling back to `backend/main.py`). |
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
[unregistered-api] GET /api/sales/aging?older_than_days=7 has no registered fixture.
```

It is never answered with a retryable 503, because the app would render a believable "warming up" state and fixture drift would pass unnoticed. To fix it, add a typed entry to the matching module under `fixture/data/` (one module per API domain; keep each well under 400 lines):

```ts
fixture('GET', '/api/sales/aging', () => json<SalesAgingLead[]>(AGING)),
```

The explicit type argument is the contract check: `tsc` rejects a payload that does not match the frontend's response type. Patterns are Express-style (`/api/borrowers/:id/lifecycle`), `/api/v1/...` is matched as `/api/...`, the more specific pattern wins, and registering the same `METHOD pattern` twice throws.

Only reads are registered by default. A test that exercises a write (approve, reject, assign, save) registers its own handler with `mockApi.register(...)`, so it controls the response timing; that is what a pessimistic-approval assertion needs. Fixture data is synthetic only: masked ids matching `B-[0-9A-Z]{13}`, lender `Summit Mortgage`, no names or contact fields. Headline numbers reconcile across panels (`data/reference.ts`), and `harness.fixture.spec.ts` pins that.

### Degraded states are opt-in

A route never renders degraded by accident. Ask for it:

```ts
import { WAREHOUSE_WARMING_UP } from './mockApi';

app.degrade('/api/leads', WAREHOUSE_WARMING_UP);              // the backend's retryable 503 body
app.degrade(/^\/api\/analytics\//, { status: 500, body: { detail: 'boom' } });
```

The browser's "Failed to load resource" line for a degraded call is allowed automatically; anything else the degraded UI logs still fails the test.

### Out of scope for now

Pixel baselines (`toHaveScreenshot`) need Linux baselines from a pinned container and arrive in a later wave. Fixtures are typed against the frontend's hand-written types, not yet validated against the backend's OpenAPI baseline; that follows the type-codegen work, because the two are known to have drifted.
