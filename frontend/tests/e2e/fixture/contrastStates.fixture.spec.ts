/**
 * Light-theme states the axe colour-contrast loop in theme.fixture.spec.ts
 * never renders on a healthy / or /lead-queue, proven at the rendered layer
 * (2026-09-21 audit a11y-01):
 *
 *  a. warning copy paints `--signal-warning-ink`, not the amber FILL hue
 *     (#F59E0B on white was 2.15:1): the topbar search error and the Genie
 *     history error, each rendered from a degraded endpoint, plus a computed
 *     sweep over every partial rule that paints warning text or an amber
 *     glyph, because some of them have no state the fixture population can
 *     reach (`.seg-card__meta--pending`, `.genie-proof__gap`,
 *     `.bulk-actions__toast--warn`, `.audit__ico.amber`);
 *  b. amber icon glyphs clear WCAG 1.4.11 3:1 on their tinted tiles: the
 *     Home approval queue and the degraded-dependency banner;
 *  c. the active evidence-drawer tab paints `--accent-ink` on its fill for
 *     every accent (it painted `--accent`, 1.75:1 in light + bright).
 */
import type { Locator, Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import { HEALTH_OK } from './data/shell';
import { json } from './mockApi';
import { asComputedRgb, contrastRatio, parseRgb, renderedColors, tokenValue } from './renderedColor';
import { expect, test } from './test';

const AA_TEXT = 4.5;
const AA_UI = 3;
const ACCENTS = ['bright', 'teal', 'navy', 'red'] as const;

/**
 * Every partial rule that paints warning copy or an amber glyph (the list
 * tokenUsage.test.ts pins at source level), as the class list of a probe.
 */
const WARNING_INK_CONSUMERS: readonly string[] = [
  'topbar__search-status topbar__search-status--error',
  'seg-card__meta seg-card__meta--pending',
  'bulk-actions__toast bulk-actions__toast--warn',
  'genie-history__state genie-history__state--error',
  'genie-proof__gap',
  'approval__ico',
  'degraded-banner__ico',
  'audit__ico amber',
];

/** Assert the element paints the warning ink and clears `min` against what is really behind it. */
async function expectWarningInk(page: Page, target: Locator, min: number): Promise<void> {
  const painted = await renderedColors(target);
  const ratio = contrastRatio(painted.fg, painted.bg);
  expect(ratio, `${painted.color} on rgb(${painted.bg.join(', ')}) = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
  const ink = await asComputedRgb(page, await tokenValue(target, '--signal-warning-ink'));
  expect(painted.color, 'paints --signal-warning-ink, not the amber fill').toBe(ink);
}

async function seedAccent(page: Page, accent: string): Promise<void> {
  await page.addInitScript((value) => {
    try {
      window.localStorage.setItem('mip.accent', value);
    } catch {
      // about:blank has no storage; the next document seeds it.
    }
  }, accent);
}

test('light: the topbar search error paints the warning ink at AA', async ({ app, page }) => {
  app.degrade('/api/borrowers/search', { status: 500, body: { detail: 'fixture: search is down' } });
  await app.setTheme('light');
  await app.gotoRoute('/');

  await page.getByRole('banner').getByRole('textbox', { name: 'Search borrowers' }).fill('B-');
  const status = page.locator('.topbar__search-status--error');
  await expect(status).toBeVisible();
  await expectWarningInk(page, status, AA_TEXT);
});

test('light: the Genie history error paints the warning ink at AA', async ({ app, page }) => {
  app.degrade('/api/genie/sessions', { status: 500, body: { detail: 'fixture: history is down' } });
  await app.setTheme('light');
  await app.gotoRoute('/');

  const genie = await app.openGenie();
  await genie.getByRole('button', { name: 'Genie conversation history' }).click();
  const state = genie.locator('.genie-history__state--error');
  await expect(state).toHaveText('History unavailable');
  await expectWarningInk(page, state, AA_TEXT);
});

test('light: every warning-ink consumer computes the ink, not the amber fill', async ({ app, page }) => {
  await app.setTheme('light');
  await app.gotoRoute('/');
  const root = page.locator('html');
  const ink = await asComputedRgb(page, await tokenValue(root, '--signal-warning-ink'));
  const fill = await asComputedRgb(page, await tokenValue(root, '--signal-warning'));
  expect(ink, 'the light theme separates the text ink from the fill hue').not.toBe(fill);

  const painted = await page.evaluate((classLists) => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const colors = classLists.map((className) => {
      const probe = document.createElement('div');
      probe.className = className;
      probe.textContent = 'Warning';
      host.appendChild(probe);
      return [className, getComputedStyle(probe).color] as const;
    });
    host.remove();
    return Object.fromEntries(colors);
  }, WARNING_INK_CONSUMERS);
  expect(painted).toEqual(Object.fromEntries(WARNING_INK_CONSUMERS.map((className) => [className, ink])));

  const surface = parseRgb(await asComputedRgb(page, await tokenValue(root, '--bg-1')));
  expect(contrastRatio(parseRgb(ink), surface)).toBeGreaterThanOrEqual(AA_TEXT);
});

test('light: amber icon glyphs clear 3:1 on their tinted tiles', async ({ app, mockApi, page }) => {
  // Genie is a dependency Home does not read from, so the banner shows and
  // every panel still loads.
  mockApi.register('GET', '/api/health', () =>
    json<HealthPayload>({ ...HEALTH_OK, dependencies: { ...HEALTH_OK.dependencies, genie: 'down' } }),
  );
  await app.setTheme('light');
  await app.gotoRoute('/');

  const approvalIcon = page.getByRole('region', { name: 'Approval queue' }).locator('.approval__ico');
  await expect(approvalIcon).toBeVisible();
  await expectWarningInk(page, approvalIcon, AA_UI);

  const bannerIcon = page.locator('.degraded-banner[data-degraded-dependency="genie"] .degraded-banner__ico');
  await expect(bannerIcon).toBeVisible();
  await expectWarningInk(page, bannerIcon, AA_UI);
});

for (const accent of ACCENTS) {
  test(`light + ${accent}: the active evidence-drawer tab reads AA on its fill`, async ({ app, page }) => {
    // The drawer only opens from an evidence chip, so the axe loop never sees the tab.
    await app.setTheme('light');
    await seedAccent(page, accent);
    await app.gotoRoute('/');
    await expect(page.locator('html')).toHaveAttribute('data-accent', accent);
    await page.locator('.kpi__source .evidence-chip').first().click();
    const active = page.getByRole('dialog').locator('.drawer__tab.is-active');
    await expect(active).toBeVisible();
    await expect(active).toHaveAttribute('aria-selected', 'true');

    const painted = await renderedColors(active);
    const ratio = contrastRatio(painted.fg, painted.bg);
    expect(ratio, `${painted.color} on rgb(${painted.bg.join(', ')}) = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_TEXT);
    expect(painted.color, 'paints --accent-ink').toBe(await asComputedRgb(page, await tokenValue(active, '--accent-ink')));
  });
}
