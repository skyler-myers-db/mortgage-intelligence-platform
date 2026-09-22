/**
 * The fixture harness entry point. Fixture specs import `test` and `expect`
 * from here, never from `@playwright/test` directly:
 *
 *   import { expect, test } from './test';
 *
 *   test('lead queue renders', async ({ app, page }) => {
 *     await app.setTheme('light');
 *     await app.gotoRoute('/lead-queue');
 *     await expect(page.locator('table.tbl tbody tr').first()).toBeVisible();
 *   });
 *
 * Every test automatically gets the mock API (no backend, no credentials),
 * the production CSP, a frozen clock, a browser trace that is kept only when
 * the test fails, and the hygiene gate that fails the test on an uncaught
 * error, console.error, CSP violation, failed request or unregistered API
 * call. See docs/testing.md ("Fixture harness").
 */
import fs from 'node:fs';
import path from 'node:path';
import { test as base, expect, type TestInfo } from '@playwright/test';
import { AppDriver } from './app';
import { FIXTURE_NOW } from './data/reference';
import { Hygiene, formatViolations, type HygieneCheck } from './hygiene';
import { MockApi } from './mockApi';
import { readProductionCsp } from './productionCsp';
import { defaultFixtures } from './registry';

export interface FixtureOptions {
  /** Hygiene checks this test opts out of, by name. Default: none. */
  hygieneOptOut: HygieneCheck[];
  /** Frozen `Date.now()` for the page (ISO string), or null for the real clock. */
  fixtureNow: string | null;
  /**
   * Record the trace's screencast (a screenshot per frame). Default: true.
   * A spec that runs real motion sets it false: a page that animates
   * continuously makes the screencast capture every frame, which roughly
   * doubles those tests under load. DOM snapshots and sources are always
   * recorded, so a failure trace stays debuggable.
   */
  traceScreenshots: boolean;
}

export interface FixtureHarness {
  /** Records the browser trace and keeps it only when the test failed. */
  failureTrace: void;
  mockApi: MockApi;
  hygiene: Hygiene;
  app: AppDriver;
}

function isUnexpectedOutcome(testInfo: TestInfo): boolean {
  return testInfo.status !== 'skipped' && testInfo.status !== testInfo.expectedStatus;
}

export const test = base.extend<FixtureOptions & FixtureHarness>({
  hygieneOptOut: [[], { option: true }],
  fixtureNow: [FIXTURE_NOW, { option: true }],
  traceScreenshots: [true, { option: true }],

  failureTrace: [
    async ({ context, traceScreenshots }, use, testInfo) => {
      // Fixture mode keeps Playwright's own `trace` option OFF (see
      // playwright.config.ts) and records the failure trace here. With
      // `trace: 'retain-on-failure'`, Playwright 1.59 finalizes a failed test
      // by merging the context trace and the test trace through its bundled
      // yauzl, which on Node 26 never finishes reading a zip entry over
      // 64 KiB (the failure screenshot always is one): the test then stalled
      // for the whole tracing slot and gained a spurious "Test timeout
      // exceeded". Saving a context trace to a path only writes through yazl,
      // so it completes on every Node. runner.fixture.spec.ts pins this.
      await context.tracing.start({
        screenshots: traceScreenshots,
        snapshots: true,
        sources: true,
        title: testInfo.titlePath.join(' › '),
      });
      await use();
      if (!isUnexpectedOutcome(testInfo)) {
        await context.tracing.stop();
        return;
      }
      const tracePath = testInfo.outputPath('trace.zip');
      await context.tracing.stop({ path: tracePath });
      // attach() copies the file into the result's attachments directory
      // (where the HTML report finds it as a trace); keep that single copy.
      await testInfo.attach('trace', { path: tracePath, contentType: 'application/zip' });
      await fs.promises.unlink(tracePath).catch(() => undefined);
    },
    { auto: true },
  ],

  mockApi: [
    // `failureTrace` is listed first so tracing starts before the page exists
    // and its teardown runs after the hygiene verdict (a hygiene failure is
    // then already on `testInfo` when the trace is kept or discarded).
    async ({ failureTrace: _failureTrace, page, fixtureNow }, use) => {
      // Freeze Date before the first document loads so freshness labels and
      // date-window queries do not depend on the day the suite runs. Timers
      // and animation frames keep running.
      if (fixtureNow) await page.clock.setFixedTime(new Date(fixtureNow));
      const mockApi = new MockApi(defaultFixtures());
      await mockApi.install(page);
      await use(mockApi);
    },
    { auto: true },
  ],

  hygiene: [
    async ({ page, mockApi, hygieneOptOut }, use, testInfo) => {
      const configFile = testInfo.config.configFile;
      if (!configFile) throw new Error('Fixture harness needs frontend/playwright.config.ts to locate the repo root.');
      const hygiene = new Hygiene(mockApi, readProductionCsp(path.dirname(configFile)), hygieneOptOut);
      await hygiene.attach(page);
      await use(hygiene);
      const violations = hygiene.violations();
      if (violations.length > 0) throw new Error(formatViolations(testInfo.title, violations));
    },
    { auto: true },
  ],

  app: async ({ page, mockApi }, use) => {
    await use(new AppDriver(page, mockApi));
  },
});

export { expect };
export type { HygieneCheck } from './hygiene';
export type { FixtureTheme } from './app';
