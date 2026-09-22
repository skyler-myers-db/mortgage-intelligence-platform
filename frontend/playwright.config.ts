import { defineConfig, devices } from '@playwright/test';

const liveE2E = process.env.E2E_LIVE === '1';
const browserMatrix = process.env.E2E_BROWSER_MATRIX === '1';
const liveFailureArtifacts = process.env.E2E_LIVE_FAILURE_ARTIFACTS === '1' && !process.env.CI;

// Credential-free fixture mode (E2E_FIXTURE=1): runs ONLY *.fixture.spec.ts
// against a production build served by `vite preview`, with every API call
// answered by tests/e2e/fixture/mockApi.ts. It never boots uvicorn. The port
// comes from E2E_FIXTURE_PORT (strict) so parallel agents and CI never
// collide, and the server is never reused so a run cannot attach to someone
// else's build. See docs/testing.md ("Fixture harness").
const fixtureE2E = process.env.E2E_FIXTURE === '1';
const fixturePort = Number(process.env.E2E_FIXTURE_PORT || 4273);
const fixtureWorkers = Number(process.env.E2E_FIXTURE_WORKERS || 4);
const FIXTURE_SPEC = /.*\.fixture\.spec\.ts$/;
// Internal to the harness self-test: tests/e2e/fixture/runner.fixture.spec.ts
// spawns a nested Playwright run (E2E_FIXTURE_NESTED=1) that collects ONLY
// tests/e2e/fixture/nested/*.nested.ts, which fail on purpose and need no web
// server. Nothing else sets this variable.
const nestedFixtureRun = fixtureE2E && process.env.E2E_FIXTURE_NESTED === '1';
const NESTED_SPEC = /.*\.nested\.ts$/;

/**
 * Playwright config for the Module 0 product golden path.
 *
 * Both the config and the test specs live under `frontend/` so that
 * `@playwright/test` imports — from the config AND from every spec
 * file — resolve against `frontend/node_modules/`. Node ESM package
 * resolution walks up from the importing file's directory; if the
 * specs lived at the repo root, their imports would miss the frontend
 * node_modules entirely.
 *
 * Python test suites (`tests/unit/`, `tests/integration/`,
 * `tests/fixtures/`) stay at the repo root for pytest.
 *
 * The webServer block boots a real uvicorn + vite pair when not already
 * up. `cwd: '..'` on the uvicorn entry runs the Python process from the
 * repo root so `backend.main:app` resolves. `reuseExistingServer: !CI`
 * means locally we reuse whatever you have running, and CI always starts
 * fresh. Both servers must be up simultaneously because the test makes
 * a direct fetch against the backend audit endpoint to verify the
 * human-approval round-trip wrote an audit event.
 *
 * CI posture: the `ci.yml` offline job parses every spec, then serves the
 * production frontend and runs the route-fulfilled Growth Agent handoff
 * contract without booting the credential-gated backend. The nightly
 * workflow runs the live suite with real credentials against the deployed app.
 * The `e2e-fixture` job runs fixture mode (E2E_FIXTURE=1, above): every route
 * rendered against typed fixtures, with the hygiene gate on.
 */
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: nestedFixtureRun ? NESTED_SPEC : fixtureE2E ? FIXTURE_SPEC : /.*\.spec\.ts$/,
  // Fixture specs need the fixture web server and mock API, so every other
  // mode (local, live, browser matrix, `--list`) must never collect them.
  testIgnore: fixtureE2E ? [] : FIXTURE_SPEC,
  timeout: fixtureE2E ? 60_000 : liveE2E ? 90_000 : 30_000,
  ...(fixtureE2E ? { expect: { timeout: 10_000 } } : {}),
  snapshotPathTemplate: '{testDir}/{testFilePath}-snapshots/{arg}{-projectName}{ext}',
  fullyParallel: fixtureE2E,
  workers: fixtureE2E ? fixtureWorkers : 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: fixtureE2E && process.env.CI
    ? [['list'], ['github'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : process.env.CI ? [['list'], ['github']] : 'list',
  use: {
    baseURL: fixtureE2E ? `http://127.0.0.1:${fixturePort}` : 'http://localhost:5173',
    viewport: { width: 1440, height: 900 },
    // Fixture mode keeps a failure screenshot and a failure trace (DOM
    // snapshots + network); always-on video encoding roughly doubles wall
    // time on a loaded runner and adds nothing the trace does not show.
    video: fixtureE2E || (liveE2E && !liveFailureArtifacts) ? 'off' : 'retain-on-failure',
    screenshot: liveE2E && !liveFailureArtifacts ? 'off' : 'only-on-failure',
    // Fixture mode records its failure trace in tests/e2e/fixture/test.ts
    // (`failureTrace`) and must keep Playwright's own trace OFF: with
    // 'retain-on-failure', Playwright 1.59 merges the context and test traces
    // through its bundled yauzl, which on Node 26 never finishes reading a
    // zip entry over 64 KiB (the failure screenshot always is one), so every
    // failing test stalled for the whole test timeout and reported a spurious
    // "Test timeout exceeded". runner.fixture.spec.ts pins the fast failure.
    trace: fixtureE2E || (liveE2E && !liveFailureArtifacts) ? 'off' : 'retain-on-failure',
    actionTimeout: liveE2E ? 20_000 : 10_000,
    navigationTimeout: liveE2E || fixtureE2E ? 30_000 : 15_000,
  },
  projects: fixtureE2E
    ? [
        {
          name: 'fixture-chromium',
          use: {
            ...devices['Desktop Chrome'],
            viewport: { width: 1440, height: 900 },
            // Pinned so formatted dates and numbers match on every machine.
            locale: 'en-US',
            timezoneId: 'America/New_York',
            contextOptions: { reducedMotion: 'reduce' },
          },
        },
      ]
    : browserMatrix
    ? [
        {
          name: 'chromium',
          grep: /@desktop|@a11y/,
          use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
        },
        {
          name: 'firefox',
          grep: /@desktop/,
          use: { ...devices['Desktop Firefox'], viewport: { width: 1440, height: 900 } },
        },
        {
          name: 'webkit',
          grep: /@desktop/,
          use: { ...devices['Desktop Safari'], viewport: { width: 1440, height: 900 } },
        },
        {
          name: 'mobile-chrome',
          grep: /@device/,
          use: { ...devices['Pixel 7'] },
        },
        {
          name: 'mobile-safari',
          grep: /@device/,
          use: { ...devices['iPhone 15'] },
        },
        {
          name: 'tablet-safari',
          grep: /@device/,
          use: { ...devices['iPad Pro 11 landscape'] },
        },
      ]
    : [
        {
          name: 'chromium',
          use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
        },
      ],
  // Only boot local uvicorn + vite when the spec isn't already pointing
  // at a deployed origin. A Playwright run against a Databricks App URL
  // doesn't need (and can't use) a local backend.
  webServer: nestedFixtureRun
    ? undefined
    : fixtureE2E
    ? {
        // Serves frontend/dist; `npm run build` must already have run.
        command: `node ./node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port ${fixturePort} --strictPort`,
        url: `http://127.0.0.1:${fixturePort}`,
        reuseExistingServer: false,
        timeout: 120_000,
      }
    : process.env.MIP_APP_URL
    ? undefined
    : [
        {
          command: '.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000',
          cwd: '..',
          url: 'http://localhost:8000/api/health',
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
        {
          command: 'npm --prefix frontend run dev',
          cwd: '..',
          url: 'http://localhost:5173',
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
      ],
});
