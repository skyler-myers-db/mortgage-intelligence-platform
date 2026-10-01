/**
 * W5b lane w5-wire-contract-deps: the wire-contract type extraction and the
 * non-major dependency batch (audit quality-04 / stack-10) move no behaviour,
 * proven in the built app at 1440x900, dark and light:
 *
 *   (a) every routes.ts route mounts one non-empty h1 with no console error,
 *       page error or unhandled rejection;
 *   (b) a same-tab reload with Home's reads held repaints the restored Home
 *       aggregates from 'mip.queryCache.v1' (the TanStack query +
 *       persist-client lockstep), and the fresh read then replaces them;
 *   (c) a 503 on /api/v1/health, the operation the `// wire:` annotations
 *       name, still renders the degraded banner, which clears on recovery.
 */
import type { Page } from '@playwright/test';
import type { PortfolioPreview } from '../../../src/types';
import { CONTACTABLE_PORTFOLIO_PREVIEW, PORTFOLIO_PREVIEW } from './data/portfolio';
import { TOTALS } from './data/reference';
import { FIXTURE_ACTOR_A, serveActor } from './data/shell';
import { json, type MockApi } from './mockApi';
import { FIXTURE_ROUTES, FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const QUERY_CACHE_KEY = 'mip.queryCache.v1';
const FRESH_ADDRESSABLE = TOTALS.addressable + 1_000;
const kpiValues = (page: Page) => page.locator('#main-content .kpi-row .kpi__value');
const kpiSkeletons = (page: Page) => page.locator('#main-content .kpi-row .kpi.is-loading');

for (const theme of FIXTURE_THEMES) {
  test(`(a) every route mounts its h1 with a clean console (${theme})`, async ({ app, hygiene, page }) => {
    test.slow();
    await app.setTheme(theme);
    for (const route of FIXTURE_ROUTES) {
      await app.gotoRoute(route.path);
      const heading = page.locator('#main-content h1');
      await expect(heading, route.name).toHaveCount(1);
      await expect(heading, route.name).not.toBeEmpty();
      expect(hygiene.violations(), `${route.name}: no console error, page error or unhandled rejection`).toEqual([]);
    }
  });

  test(`(b) a reload repaints Home from the persisted cache, then the fresh read replaces it (${theme})`, async ({ app, page, mockApi }) => {
    await app.setTheme(theme);
    serveActor(mockApi, () => FIXTURE_ACTOR_A);
    await app.gotoRoute('/');
    await expect(kpiSkeletons(page)).toHaveCount(0);
    const before = await kpiValues(page).allTextContents();
    expect(before[0]).toBe(TOTALS.addressable.toLocaleString('en-US'));
    await expect
      .poll(() => page.evaluate((key) => window.sessionStorage.getItem(key)?.includes('"preview","home"') ?? false, QUERY_CACHE_KEY))
      .toBe(true);

    const release = holdFreshPreview(mockApi);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(kpiValues(page), 'restored from the persisted snapshot while the read is held').toHaveText(before);
    await expect(kpiSkeletons(page)).toHaveCount(0);

    release();
    await expect(kpiValues(page).first(), 'the fresh read replaces the restored value').toHaveText(
      FRESH_ADDRESSABLE.toLocaleString('en-US'),
    );
    await expect(kpiValues(page).nth(1)).toHaveText(before[1]);
  });

  test(`(c) a 503 on /api/v1/health renders the degraded banner, which clears on recovery (${theme})`, async ({ app, page, mockApi }) => {
    await app.setTheme(theme);
    await app.gotoRoute('/');
    const banner = page.locator('.degraded-banner');
    await expect(banner).toHaveCount(0);

    const restore = app.degrade('/api/health', { status: 503, body: { detail: 'Service unavailable' } });
    const probes = () => mockApi.calls.filter((call) => /\/health$/.test(call.path) && call.outcome === 'degraded').length;
    for (let tick = 0; tick < 12 && (await banner.count()) === 0; tick += 1) {
      await page.clock.runFor(3_000);
    }
    expect(probes(), 'the health probe met the 503').toBeGreaterThan(0);
    await expect(banner.first()).toBeVisible();

    restore();
    for (let tick = 0; tick < 12 && (await banner.count()) > 0; tick += 1) {
      await page.clock.runFor(3_000);
    }
    await expect(banner).toHaveCount(0);
  });
}

/** Hold Home's addressable preview until `release()`, then answer a changed total. */
function holdFreshPreview(mockApi: MockApi): () => void {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  mockApi.register('POST', '/api/portfolio/preview', async (request) => {
    const criteria = (request.body as { criteria?: { marketing_eligibility?: unknown } } | null)?.criteria;
    if (criteria?.marketing_eligibility === 'Eligible only') return json<PortfolioPreview>(CONTACTABLE_PORTFOLIO_PREVIEW);
    await held;
    return json<PortfolioPreview>({ ...PORTFOLIO_PREVIEW, marketable_population: FRESH_ADDRESSABLE });
  });
  return release;
}
