/**
 * Administration -> Field performance on the rendered production build at
 * 1440x900 (D-platform-process-d1 / d2; audit 2026-09-21 runtime-09,
 * quality-07, stack-08):
 *
 *  (a) RUM on (config options say so; the session says off, so this page
 *      itself installs no RUM): the On chip, the CWV chips, a '<20 samples'
 *      cell, both secondary tables; the 7/28 toggle reads days=28, Refresh
 *      reads exactly once more, and nothing reads again while idle (no poll,
 *      no focus refetch); axe-clean;
 *  (b) RUM off: the explicit empty state, and no field-performance read;
 *      axe-clean;
 *  (c) a 503: the describeApiError callout with Retry;
 *  (d) the section below never moves while the panel's chunk and its read
 *      load (the Suspense fallback reserves the panel's block size);
 *  (e) Chromium, RUM installed: a real LCP from web-vitals on /lead-queue
 *      carries a closed lcp_element and the route template, and no captured
 *      event holds '#', a selector, 'B-' or '?'.
 */
import type { Page } from '@playwright/test';
import type { ConfigOptions, SessionResponse } from '../../../src/types';
import { expectAxeClean } from './axe';
import { enableRum, holdChunk, routeRumThroughFetch } from './data/errorTelemetry';
import { CONFIG_OPTIONS, SESSION } from './data/shell';
import { json, normalizeApiPath, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const PANEL = '#field-performance';
const RUM_LCP_ELEMENTS = new Set(['img', 'svg', 'h1', 'h2', 'h3', 'p', 'div', 'table', 'canvas', 'other']);

function fieldReads(mockApi: MockApi): string[] {
  return mockApi.calls
    .filter((call) => call.method === 'GET' && normalizeApiPath(call.path) === '/api/admin/field-performance')
    .map((call) => call.search);
}

function rumConfigOnly(mockApi: MockApi): void {
  mockApi.register<ConfigOptions>('GET', '/api/config/options', () => json({ ...CONFIG_OPTIONS, rum_enabled: true }));
  mockApi.register<SessionResponse>('GET', '/api/session', () => json({ ...SESSION, rum_enabled: false }));
}

async function openPanel(page: Page): Promise<void> {
  await page.locator(PANEL).evaluate((el) => el.scrollIntoView({ block: 'start' }));
}

/** #appearance's offset inside the scroller: moves only if something above it changes size. */
async function appearanceOffset(page: Page): Promise<number> {
  return page.locator('#appearance').evaluate((el) => {
    const main = el.closest('.main');
    const top = el.getBoundingClientRect().top - (main?.getBoundingClientRect().top ?? 0);
    return Math.round(top + (main?.scrollTop ?? 0));
  });
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`field performance (${theme})`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
    });

    test('(a) shows p75 chips, the floor and both tables; reads on toggle and Refresh only', async ({ app, mockApi, page }) => {
      rumConfigOnly(mockApi);
      await page.clock.install();
      await app.gotoRoute('/admin-config');
      await openPanel(page);
      const panel = page.locator(PANEL);

      await expect(panel.locator('.surface__hdr .chip')).toHaveText('Browser telemetry On');
      await expect(panel.getByRole('heading', { name: 'p75 by route' })).toBeVisible();
      await expect(panel.locator('.chip--success').first()).toBeVisible();
      await expect(panel.locator('.chip--warning').first()).toBeVisible();
      await expect(panel.locator('.chip--danger').first()).toBeVisible();
      await expect(panel.getByText('<20 samples').first()).toBeVisible();
      await expect(panel.getByRole('heading', { name: 'INP by interaction' })).toBeVisible();
      await expect(panel.getByRole('heading', { name: 'Client errors' })).toBeVisible();
      await expect(panel.getByRole('cell', { name: 'segment-card' })).toBeVisible();
      await expect(panel.getByRole('cell', { name: 'ChunkLoadError' })).toBeVisible();
      expect(fieldReads(mockApi)).toEqual(['days=7']);

      await expectAxeClean(page, {
        key: { route: 'admin-config', state: 'field-performance' }, theme, known: {}, include: PANEL,
      });

      await panel.getByRole('button', { name: '28 days' }).click();
      await expect(panel.getByRole('button', { name: '28 days' })).toHaveAttribute('aria-pressed', 'true');
      await expect.poll(() => fieldReads(mockApi)).toEqual(['days=7', 'days=28']);

      await panel.getByRole('button', { name: 'Refresh field performance' }).click();
      await expect.poll(() => fieldReads(mockApi)).toEqual(['days=7', 'days=28', 'days=28']);

      // Idle: ten minutes of timers, a focus and a visibility round trip read nothing.
      await page.evaluate(() => {
        window.dispatchEvent(new Event('focus'));
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await page.clock.runFor(10 * 60_000);
      await app.settle();
      expect(fieldReads(mockApi), 'no poll, no focus refetch').toEqual(['days=7', 'days=28', 'days=28']);
    });

    test('(b) RUM off: the explicit empty state, and no read', async ({ app, mockApi, page }) => {
      await app.gotoRoute('/admin-config');
      await openPanel(page);
      const panel = page.locator(PANEL);

      await expect(panel.locator('.surface__hdr .chip')).toHaveText('Browser telemetry Off');
      await expect(panel.locator('[data-empty-cause]')).toContainText('Browser telemetry is off for this deployment.');
      await expect(panel.getByRole('group', { name: 'Field performance window' })).toHaveCount(0);
      expect(fieldReads(mockApi)).toEqual([]);

      await expectAxeClean(page, {
        key: { route: 'admin-config', state: 'field-performance-off' }, theme, known: {}, include: PANEL,
      });
    });
  });
}

test('(c) a 503 shows the describeApiError callout with Retry', async ({ app, hygiene, mockApi, page }) => {
  hygiene.allow('console.error', /status of 503/);
  rumConfigOnly(mockApi);
  mockApi.register<{ detail: string }>('GET', '/api/admin/field-performance', () =>
    json({ detail: 'lakebase is temporarily unavailable' }, { status: 503 }),
  );
  await app.gotoRoute('/admin-config');
  await openPanel(page);

  const alert = page.locator(PANEL).getByRole('alert');
  await expect(alert).toContainText("Couldn't load field performance");
  await expect(alert.getByRole('button', { name: 'Retry loading field performance' })).toBeVisible();
});

test('(d) the section below never moves while the panel loads', async ({ app, mockApi, page }) => {
  rumConfigOnly(mockApi);
  const releaseChunk = await holdChunk(page, 'admin-config.field-performance');
  let releaseRead: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    releaseRead = resolve;
  });
  await page.route(
    (url) => /\/api\/(v1\/)?admin\/field-performance$/.test(url.pathname),
    async (route) => {
      await held;
      await route.fallback();
    },
  );

  await page.goto('/admin-config', { waitUntil: 'domcontentloaded' });
  const panel = page.locator(PANEL);
  await expect(panel.locator('.field-perf__skeleton')).toBeVisible();
  await expect(page.locator('#appearance')).toBeVisible();
  const withFallback = await appearanceOffset(page);

  releaseChunk();
  await expect(panel.getByTestId('field-performance-loading')).toBeVisible();
  expect(await appearanceOffset(page), 'the panel chunk arrived: nothing below moved').toBe(withFallback);

  releaseRead();
  await app.settle();
  await expect(panel.getByRole('heading', { name: 'p75 by route' })).toBeVisible();
  expect(fieldReads(mockApi)).toEqual(['days=7']);
});

test('(e) Chromium: a real LCP carries a closed element bucket and the route template', async ({ app, browserName, mockApi, page }) => {
  test.skip(browserName !== 'chromium', 'LCP and the event-timing attribution are Chromium-only here');
  await routeRumThroughFetch(page);
  const rum = enableRum(mockApi);
  await app.gotoRoute('/lead-queue');
  const trigger = page.locator('.filter-root[data-rum-target="filter"] button').first();
  await expect(trigger).toBeVisible();

  // web-vitals finalizes LCP on the first input after it registered (RUM
  // installs from an idle callback): keep interacting until one is sent.
  await expect
    .poll(
      async () => {
        await trigger.click();
        await page.keyboard.press('Escape');
        return rum.events().some((event) => event.metric === 'lcp');
      },
      { timeout: 30_000, intervals: [1_000] },
    )
    .toBe(true);

  const lcp = rum.events().find((event) => event.metric === 'lcp');
  expect(lcp?.route).toBe('/lead-queue');
  expect(RUM_LCP_ELEMENTS.has(String(lcp?.details?.lcp_element))).toBe(true);
  const payload = rum.bodies.join('\n');
  for (const forbidden of ['#', 'B-', '?', '>', 'nth-child', ':borrower_id']) {
    expect(payload, `no captured event carries ${forbidden}`).not.toContain(forbidden);
  }
});

test('(f) the section nav gains Field performance and stays one row; its width is recorded, Console closed and open', async ({ app, page }) => {
  await app.gotoRoute('/admin-config');
  const sections = page.getByRole('navigation', { name: 'Administration sections' });
  await expect(sections.getByRole('link')).toHaveCount(11);
  await expect(sections.getByRole('link', { name: 'Field performance' })).toHaveAttribute('href', /#field-performance$/);

  const measure = () =>
    sections.evaluate((nav) => {
      const links = [...nav.querySelectorAll('a')];
      const list = links[0]?.closest('ul, ol') ?? nav;
      return {
        rows: new Set(links.map((link) => Math.round(link.getBoundingClientRect().top))).size,
        scrollWidth: list.scrollWidth,
        clientWidth: list.clientWidth,
      };
    });
  const closed = await measure();
  await app.openConsole();
  const open = await measure();
  test.info().annotations.push(
    { type: 'section-nav console closed', description: JSON.stringify(closed) },
    { type: 'section-nav console open', description: JSON.stringify(open) },
  );
  console.log(`section-nav 1440x900 console closed ${JSON.stringify(closed)} / open ${JSON.stringify(open)}`);
  expect(closed.rows).toBe(1);
  expect(open.rows).toBe(1);
});
