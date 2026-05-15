import { defineConfig, devices } from '@playwright/test';

const liveE2E = process.env.E2E_LIVE === '1';
const browserMatrix = process.env.E2E_BROWSER_MATRIX === '1';
const liveFailureArtifacts = process.env.E2E_LIVE_FAILURE_ARTIFACTS === '1' && !process.env.CI;

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
 * CI posture: the `ci.yml` offline job runs `playwright test --list` for
 * a syntax + test-collection check that does NOT boot servers (the
 * cutover backend refuses to boot without live Databricks credentials,
 * which are intentionally absent in PR CI). The nightly workflow runs
 * the full spec with real credentials against the deployed app.
 */
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: /.*\.spec\.ts$/,
  timeout: liveE2E ? 90_000 : 30_000,
  snapshotPathTemplate: '{testDir}/{testFilePath}-snapshots/{arg}{-projectName}{ext}',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
  use: {
    baseURL: 'http://localhost:5173',
    viewport: { width: 1440, height: 900 },
    video: liveE2E && !liveFailureArtifacts ? 'off' : 'retain-on-failure',
    screenshot: liveE2E && !liveFailureArtifacts ? 'off' : 'only-on-failure',
    trace: liveE2E && !liveFailureArtifacts ? 'off' : 'retain-on-failure',
    actionTimeout: liveE2E ? 20_000 : 10_000,
    navigationTimeout: liveE2E ? 30_000 : 15_000,
  },
  projects: browserMatrix
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
  webServer: process.env.MIP_APP_URL
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
