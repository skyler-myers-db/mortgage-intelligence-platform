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
 * the production CSP, a frozen clock, and the hygiene gate that fails the
 * test on an uncaught error, console.error, CSP violation, failed request or
 * unregistered API call. See docs/testing.md ("Fixture harness").
 */
import path from 'node:path';
import { test as base, expect } from '@playwright/test';
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
}

export interface FixtureHarness {
  mockApi: MockApi;
  hygiene: Hygiene;
  app: AppDriver;
}

export const test = base.extend<FixtureOptions & FixtureHarness>({
  hygieneOptOut: [[], { option: true }],
  fixtureNow: [FIXTURE_NOW, { option: true }],

  mockApi: [
    async ({ page, fixtureNow }, use) => {
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
