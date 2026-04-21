import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for the Module 0 DAIS golden path.
 *
 * Pins the booth-demo narrative at 1440×900 (the presenter's viewport). The
 * webServer block will boot uvicorn + vite if they aren't already up;
 * `reuseExistingServer: !CI` means locally we reuse whatever you have running,
 * and CI always starts fresh. Both servers must be up simultaneously because
 * the test makes a direct fetch against the backend audit endpoint to verify
 * the human-approval round-trip wrote an audit event.
 */
export default defineConfig({
  testDir: 'tests/e2e',
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
  webServer: [
    {
      command: 'uvicorn backend.main:app --host 0.0.0.0 --port 8000',
      url: 'http://localhost:8000/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'npm --prefix frontend run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
