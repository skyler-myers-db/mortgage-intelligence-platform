import { defineConfig, devices } from '@playwright/test';

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
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['github']] : 'list',
  use: {
    baseURL: 'http://localhost:5173',
    viewport: { width: 1440, height: 900 },
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [
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
          command: 'uvicorn backend.main:app --host 0.0.0.0 --port 8000',
          cwd: '..',
          url: 'http://localhost:8000/api/health',
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
        {
          command: 'npm run dev',
          url: 'http://localhost:5173',
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
      ],
});
