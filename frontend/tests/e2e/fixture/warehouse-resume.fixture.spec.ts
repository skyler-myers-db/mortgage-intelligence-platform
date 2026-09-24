/**
 * Rendered-layer proofs for the w2-warehouse-delivery lane, at 1440x900.
 *
 *   - delivery-01: a warehouse resuming from auto-stop reads as a calm amber
 *     "Waking warehouse" pill with an elapsed timer, no banner; the resume
 *     ending flips to Live at once; a real outage stays red Degraded with the
 *     Reconnecting banner. Home keeps its KPI row and answer band mounted
 *     through a warm-up and promises no 30 s / 60 s duration.
 *   - delivery-v1: every health poll carries only an integer idle hint.
 *   - wave-1c follow-up: an ended session never reads "Live".
 *
 * Only Home's natural load and the shell's own polls run: no proof drawer,
 * no draft, no Lead Queue. Tests that watch time move install Playwright's
 * clock (the harness otherwise freezes Date while timers run), so Date and
 * timers advance together.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import { EVERY_API_PATH, EVERY_API_PATH_BUT_HEALTH, PROXY_SESSION_EXPIRED } from './data/sessionRecovery';
import { HEALTH_OK } from './data/shell';
import { HEALTH_WAREHOUSE_DOWN, HEALTH_WAREHOUSE_RESUMING, switchHealth } from './data/warehouseResume';
import { WAREHOUSE_WARMING_UP } from './mockApi';
import { asComputedRgb } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const CLOCK_START = new Date('2026-09-24T14:00:00Z');
/** HealthProvider's fast (3 s) and healthy (8 s) cadences. */
const FAST_POLL_MS = 3_000;
const HEALTHY_POLL_MS = 8_000;
/** Any "30 s", "60 seconds" or "30–60 seconds" claim. */
const DURATION_CLAIM = /\b(30|60)\s*(?:[–-]\s*60\s*)?(?:s|sec|secs|seconds)\b/i;

function pill(page: Page): Locator {
  return page.getByTestId('system-status-pill');
}

async function expectViewport(page: Page): Promise<void> {
  expect(page.viewportSize()).toEqual({ width: 1440, height: 900 });
}

async function dotColor(page: Page): Promise<string> {
  return pill(page).locator('.dot').evaluate((el) => getComputedStyle(el).backgroundColor);
}

async function tickerSeconds(page: Page): Promise<number> {
  const text = (await pill(page).locator('.mono').textContent()) ?? '';
  const match = /^(\d+)s$/.exec(text.trim());
  if (!match) throw new Error(`ticker text is not "Ns": ${JSON.stringify(text)}`);
  return Number(match[1]);
}

/** Record whether a `.degraded-banner` is ever added from now on. */
async function watchForBanner(page: Page): Promise<void> {
  await page.evaluate(() => {
    const flags = window as unknown as { __bannerMounted: boolean };
    flags.__bannerMounted = document.querySelector('.degraded-banner') !== null;
    new MutationObserver(() => {
      if (document.querySelector('.degraded-banner')) flags.__bannerMounted = true;
    }).observe(document.body, { childList: true, subtree: true });
  });
}

async function bannerEverMounted(page: Page): Promise<boolean> {
  return page.evaluate(() => (window as unknown as { __bannerMounted: boolean }).__bannerMounted);
}

test.describe('a warehouse resuming from auto-stop', () => {
  test.use({ fixtureNow: null });

  for (const theme of FIXTURE_THEMES) {
    test(`reads as a calm "Waking warehouse" pill with a running timer and no banner (${theme})`, async ({ app, page, mockApi }) => {
      await expectViewport(page);
      await page.clock.install({ time: CLOCK_START });
      switchHealth(mockApi, HEALTH_WAREHOUSE_RESUMING);
      await app.setTheme(theme);
      await app.gotoRoute('/');

      await expect(pill(page).locator('.topbar__pill-label')).toHaveText('Waking warehouse');
      await expect(pill(page)).toHaveAttribute('aria-label', 'System status: Waking warehouse.');
      expect(await dotColor(page), 'the prototype .dot.amber, not .dot.danger').toBe(
        await asComputedRgb(page, 'var(--signal-warning)'),
      );
      const ticker = pill(page).locator('.mono');
      await expect(ticker).toHaveAttribute('aria-hidden', 'true');
      const before = await tickerSeconds(page);
      await page.clock.runFor(3_000);
      await expect.poll(() => tickerSeconds(page)).toBeGreaterThanOrEqual(Math.max(3, before + 3));
      await expect(page.locator('.degraded-banner')).toHaveCount(0);

      const axe = await new AxeBuilder({ page }).include('.topbar').withTags(WCAG_TAGS).analyze();
      expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
    });

    test(`flips to Live one fast poll after the resume ends, never through a banner (${theme})`, async ({ app, page, mockApi }) => {
      await expectViewport(page);
      await page.clock.install({ time: CLOCK_START });
      const health = switchHealth(mockApi, HEALTH_WAREHOUSE_RESUMING);
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await expect(pill(page).locator('.topbar__pill-label')).toHaveText('Waking warehouse');

      await watchForBanner(page);
      health.set(HEALTH_OK);
      await page.clock.runFor(FAST_POLL_MS);

      await expect(pill(page).locator('.topbar__pill-label'), 'no 5 s down→up debounce after a resume').toHaveText('Live');
      await expect(pill(page).locator('.mono')).toHaveCount(0);
      expect(await bannerEverMounted(page), 'a finished resume is not an outage').toBe(false);
    });

    test(`a real outage stays red Degraded with the Reconnecting banner (${theme})`, async ({ app, page, mockApi }) => {
      await expectViewport(page);
      switchHealth(mockApi, HEALTH_WAREHOUSE_DOWN);
      await app.setTheme(theme);
      await page.goto('/', { waitUntil: 'domcontentloaded' });

      await expect(pill(page).locator('.topbar__pill-label')).toHaveText('Degraded');
      expect(await dotColor(page)).toBe(await asComputedRgb(page, 'var(--signal-danger)'));
      await expect(page.locator('.degraded-banner[data-degraded-dependency="warehouse"]')).toBeVisible();
    });
  }
});

test('Home keeps its KPI row and answer band mounted beside the warming block, with no 30 s / 60 s claim', async ({ app, page }) => {
  await expectViewport(page);
  app.degrade('/api/portfolio/preview', WAREHOUSE_WARMING_UP);
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  // Wait for the warm-up itself (the transport's own retries end first).
  await expect(page.getByTestId('warming-up-block').first()).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('.kpi-row > .kpi.is-loading')).toHaveCount(4);
  await expect(page.locator('.home-answer'), 'the answer band stays mounted through a warm-up').toBeVisible();
  const mainText = (await page.locator('#main-content').innerText()).replace(/\s+/g, ' ');
  expect(mainText).not.toMatch(DURATION_CLAIM);
  expect(mainText).toContain('usually takes 2–6 seconds');
});

test.describe('the keep-warm idle hint', () => {
  test.use({ fixtureNow: null });

  test('every health poll carries only an integer idle_s that grows while idle and resets after a click', async ({ app, page, mockApi }) => {
    await expectViewport(page);
    await page.clock.install({ time: CLOCK_START });
    await app.gotoRoute('/');
    const idle = () =>
      mockApi.calls
        .filter((call) => call.method === 'GET' && call.path.replace(/^\/api\/v\d+/, '/api') === '/api/health')
        .map((call) => call.search);

    await page.clock.runFor(HEALTHY_POLL_MS * 2);
    await expect.poll(() => idle().length).toBeGreaterThanOrEqual(3);
    const idleValues = () => idle().map((search) => Number(/^idle_s=(\d{1,5})$/.exec(search)?.[1] ?? NaN));
    for (const search of idle()) expect(search, 'only idle_s=<int>, nothing else').toMatch(/^idle_s=\d{1,5}$/);
    const whileIdle = idleValues();
    expect(whileIdle[whileIdle.length - 1], 'it grows while nobody touches the page').toBeGreaterThanOrEqual(HEALTHY_POLL_MS / 1000);

    await page.locator('#main-content h1').click();
    const pollsBefore = idle().length;
    await page.clock.runFor(HEALTHY_POLL_MS);
    await expect.poll(() => idle().length).toBeGreaterThan(pollsBefore);
    const afterClick = idleValues();
    expect(afterClick[afterClick.length - 1], 'a click resets it').toBeLessThanOrEqual(HEALTHY_POLL_MS / 1000);
    expect(afterClick[afterClick.length - 1]).toBeLessThan(whileIdle[whileIdle.length - 1] + HEALTHY_POLL_MS / 1000);
    for (const search of idle()) expect(search).toMatch(/^idle_s=\d{1,5}$/);
  });
});

test('an ended session never reads "Live": 401 {} on every /api path shows "Session ended"', async ({ app, page }) => {
  await expectViewport(page);
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { __pillLabels: string[] }).__pillLabels = seen;
    new MutationObserver(() => {
      const label = document.querySelector('[data-testid="system-status-pill"] .topbar__pill-label')?.textContent;
      if (label && seen[seen.length - 1] !== label) seen.push(label);
    }).observe(document, { childList: true, subtree: true, characterData: true });
  });
  app.degrade(EVERY_API_PATH, PROXY_SESSION_EXPIRED);
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  await expect(pill(page).locator('.topbar__pill-label')).toHaveText('Session ended');
  const labels = await page.evaluate(() => (window as unknown as { __pillLabels: string[] }).__pillLabels);
  expect(labels, 'the pill never presented the dead session as healthy').not.toContain('Live');
});

test('an ended session met by a page read replaces the last healthy "Live" with "Session ended"', async ({ app, page }) => {
  // The wave-1c defect exactly: the health probe had last answered healthy,
  // a page read then met the 401, polling stopped, and "Live" stayed behind
  // the session dialog because the pill read only the last health payload.
  await expectViewport(page);
  app.degrade(EVERY_API_PATH_BUT_HEALTH, PROXY_SESSION_EXPIRED);
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  await expect(page.getByRole('alertdialog', { name: 'Your session ended' })).toBeVisible();
  await expect(pill(page).locator('.topbar__pill-label')).toHaveText('Session ended');
  await expect(pill(page).locator('.dot')).toHaveClass('dot amber');
});
