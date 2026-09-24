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
 * Every non-Live pill also proves it fits the 1440 actions track: a whole
 * 6x6 dot, no clipped content, clear of the next icon button, the icon
 * buttons at full size, and the tenant pill clear of the search's ⌘K badge.
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

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('element has no bounding box');
  return { left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height };
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

/**
 * The status pill fits the actions cluster's side track at 1440x900: the
 * dot keeps its 6x6 box, nothing inside the pill is clipped, the pill ends
 * before the next icon button starts, the icon buttons keep their full box,
 * and the elastic tenant pill ellipsizes inside its own box, still naming
 * the tenant, clear of the search's ⌘K badge.
 */
async function expectPillFits(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const status = pill(page);
  const dot = await boxOf(status.locator('.dot'));
  expect({ width: dot.right - dot.left, height: dot.bottom - dot.top }, 'the status dot keeps its 6x6 box').toEqual({ width: 6, height: 6 });
  const clipped = await status.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(clipped, 'nothing inside the pill overflows it').toBeLessThanOrEqual(0);
  const pillBox = await boxOf(status);
  const nextButton = await boxOf(page.locator('[data-testid="system-status-pill"] ~ .topbar__icon-btn').first());
  expect(pillBox.right, 'the pill ends before the next icon button').toBeLessThanOrEqual(nextButton.left);
  for (const button of await page.locator('.topbar__actions .topbar__icon-btn').all()) {
    const box = await boxOf(button);
    expect(box.right - box.left, 'an icon button was squeezed').toBe(34);
  }
  const tenantPill = page.locator('.topbar__pill:has(> .topbar__pill-tenant)');
  const tenantClipped = await tenantPill.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(tenantClipped, 'the tenant pill ellipsizes its name, never clips its own box').toBeLessThanOrEqual(0);
  const tenantChars = await tenantPill.locator('.topbar__pill-tenant').evaluate((el) => {
    const perChar = el.scrollWidth / Math.max(1, (el.textContent ?? '').length);
    return el.clientWidth / perChar;
  });
  expect(tenantChars, 'the ellipsized tenant still names itself (five characters or more)').toBeGreaterThanOrEqual(5);
  const tenant = await boxOf(tenantPill);
  const kbd = await boxOf(page.locator('.topbar__search-kbd'));
  expect(overlaps(tenant, kbd), 'the tenant pill covers the search\'s ⌘K badge').toBe(false);
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

      // Two- and three-digit timers still fit the 1440 actions track.
      await page.clock.runFor(9_000);
      await expect.poll(() => tickerSeconds(page)).toBeGreaterThanOrEqual(12);
      await expectPillFits(page);
      await page.clock.runFor(90_000);
      await expect.poll(() => tickerSeconds(page)).toBeGreaterThanOrEqual(100);
      await expectPillFits(page);

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
      await expectPillFits(page);
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

for (const theme of FIXTURE_THEMES) {
  test(`an ended session never reads "Live": 401 {} on every /api path shows "Session ended" (${theme})`, async ({ app, page }) => {
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
    await app.setTheme(theme);
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await expect(pill(page).locator('.topbar__pill-label')).toHaveText('Session ended');
    const labels = await page.evaluate(() => (window as unknown as { __pillLabels: string[] }).__pillLabels);
    expect(labels, 'the pill never presented the dead session as healthy').not.toContain('Live');
    await expectPillFits(page);
  });

  test(`an ended session met by a page read replaces the last healthy "Live" with "Session ended" (${theme})`, async ({ app, page }) => {
    // The wave-1c defect exactly: the health probe had last answered healthy,
    // a page read then met the 401, polling stopped, and "Live" stayed behind
    // the session dialog because the pill read only the last health payload.
    await expectViewport(page);
    app.degrade(EVERY_API_PATH_BUT_HEALTH, PROXY_SESSION_EXPIRED);
    await app.setTheme(theme);
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await expect(page.getByRole('alertdialog', { name: 'Your session ended' })).toBeVisible();
    await expect(pill(page).locator('.topbar__pill-label')).toHaveText('Session ended');
    await expect(pill(page).locator('.dot')).toHaveClass('dot amber');
    await expectPillFits(page);
  });
}
