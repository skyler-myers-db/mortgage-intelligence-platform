/**
 * Rendered-layer proof of the contract-tooling lane's catalogue state (wave
 * 5a, audit quality-06), at 1440x900 in both themes on fixture-chromium:
 * `read-failed` (fixtureStates.ts) is the NON-bannered failed read. The
 * route's reads answer the backend's 503 `retries_exhausted` body while
 * /api/health stays OK, so:
 *
 *  - each route shows its own failure surface (an AsyncFailure callout, not
 *    the bannered calm line) and no DegradedBanner;
 *  - nothing counts down and nothing re-sends: lib/retryPlan.ts plans the
 *    body terminal, so advancing the clock 30 s sends no second read;
 *  - no server detail string reaches the page (FIXTURE_DETAIL_STRINGS);
 *  - no audited read happens after the natural load.
 *
 * The Offer-with-Console axe state and the Console `.audit-panel` focus
 * proof are deferred with their fix (oxlint jsx-a11y/no-noninteractive-
 * tabindex on the overflow-conditional tabIndex; the lane may not suppress).
 */
import type { Page } from '@playwright/test';
import { FIXTURE_DETAIL_STRINGS } from './data/errorSurfaces';
import { FAILED_READ_SURFACE, READ_FAILED_ENDPOINTS, enterState, prepareState } from './fixtureStates';
import type { MockApi } from './mockApi';
import { FIXTURE_ROUTES, FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const reads = (mockApi: MockApi, endpoint: string) =>
  mockApi.calls.filter((call) => call.method === 'GET' && call.path === endpoint).length;

async function mainText(page: Page): Promise<string> {
  return (await page.locator('#main-content').textContent()) ?? '';
}

for (const theme of FIXTURE_THEMES) {
  for (const name of Object.keys(READ_FAILED_ENDPOINTS)) {
    const route = FIXTURE_ROUTES.find((candidate) => candidate.name === name);
    test(`${theme} · ${name} · read-failed: its own failure surface, no banner, no countdown, no re-send`, async ({ app, mockApi, page }) => {
      if (!route) throw new Error(`no fixture route ${name}`);
      await app.setTheme(theme);
      prepareState(mockApi, 'read-failed', name);
      await app.gotoRoute(route.path);
      const naturalLoad = markNaturalLoad(mockApi);
      await enterState(app, page, 'read-failed');

      const surfaces = page.locator(FAILED_READ_SURFACE);
      await expect(surfaces.first()).toBeVisible();
      await expect(surfaces.first(), 'an alert callout, not the bannered calm line').toHaveAttribute('role', 'alert');
      await expect(page.locator('.degraded-banner'), 'health is OK: no banner').toHaveCount(0);
      await expect(page.locator('#main-content [data-async-status="bannered"]')).toHaveCount(0);
      await expect(page.locator('#main-content').getByText(/Try again in/), 'a terminal plan never counts down').toHaveCount(0);

      const endpoints = READ_FAILED_ENDPOINTS[name];
      const before = endpoints.map((endpoint) => reads(mockApi, endpoint));
      expect(before.every((count) => count >= 1), `non-vacuity: every failed read was sent (${before.join(', ')})`).toBe(true);
      await page.clock.runFor(30_000);
      await app.settle();
      expect(endpoints.map((endpoint) => reads(mockApi, endpoint)), 'nothing re-sends a terminal failure').toEqual(before);

      const text = await mainText(page);
      for (const detail of FIXTURE_DETAIL_STRINGS) expect(text, `server detail "${detail}" reached the page`).not.toContain(detail);
      expectNoAuditedReadSince(mockApi, naturalLoad, `${name} · read-failed`);
    });
  }
}
