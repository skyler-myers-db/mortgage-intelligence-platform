/**
 * The density control scale (2026-09-21 audit responsive-09 items 1-2 and 4;
 * deviation:density-control-scale). tokens.css carries --control-h,
 * --chip-h and --cell-pad-y per density; .btn and .filter take their block
 * padding from --control-h, .chip from --chip-h, the table header cells
 * from --cell-pad-y. Comfortable reproduces the prototype literals (the
 * differential of the lane proves 0 changed values there).
 *
 *  - on the Lead Queue at 1440x900, in both themes, the first ranked row, a
 *    button and a filter are each shorter in compact than in comfortable,
 *    and compact's --control-h differs from comfortable's;
 *  - compact keeps every interactive control at least 24 CSS px tall
 *    (WCAG 2.2 2.5.8): the axe wcag22aa scan (target-size included) stays
 *    clean on the Lead Queue, Home and Segments in compact, both themes.
 */
import type { Locator, Page } from '@playwright/test';
import type { AppDriver, FixtureTheme } from './app';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

interface Measures {
  row: number;
  button: number;
  filter: number;
  controlH: string;
}

const height = (locator: Locator): Promise<number> => locator.evaluate((element) => element.getBoundingClientRect().height);

async function measure(app: AppDriver, page: Page, theme: FixtureTheme, density: 'comfortable' | 'compact'): Promise<Measures> {
  await app.setTheme(theme);
  await app.setDensity(density);
  await app.gotoRoute('/lead-queue');
  await expect(page.locator('html')).toHaveAttribute('data-density', density);
  const main = page.locator('#main-content');
  const row = main.locator('table.tbl tbody tr[data-borrower-row]').first();
  await expect(row).toBeVisible();
  const button = main.locator('.btn:not(.btn--sm):visible').first();
  const filter = main.locator('.filter:visible').first();
  await expect(button).toBeVisible();
  await expect(filter).toBeVisible();
  return {
    row: await height(row),
    button: await height(button),
    filter: await height(filter),
    controlH: await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--control-h').trim()),
  };
}

for (const theme of FIXTURE_THEMES) {
  test(`${theme}: compact shortens the lead row, a button and a filter, on its own control height`, async ({ app, page }) => {
    const comfortable = await measure(app, page, theme, 'comfortable');
    const compact = await measure(app, page, theme, 'compact');
    const detail = JSON.stringify({ comfortable, compact });
    expect(compact.controlH, detail).not.toBe(comfortable.controlH);
    expect(compact.row, `the ranked row (${detail})`).toBeLessThan(comfortable.row);
    expect(compact.button, `a button (${detail})`).toBeLessThan(comfortable.button);
    expect(compact.filter, `a filter (${detail})`).toBeLessThan(comfortable.filter);
    // WCAG 2.2 2.5.8: compact controls stay at least 24 CSS px tall.
    expect(compact.button, detail).toBeGreaterThanOrEqual(24);
    expect(compact.filter, detail).toBeGreaterThanOrEqual(24);
  });

  for (const route of [
    { name: 'lead-queue', path: '/lead-queue' },
    { name: 'home', path: '/' },
    { name: 'segment-intelligence', path: '/segment-intelligence' },
  ]) {
    test(`${theme}: ${route.name} in compact passes axe wcag22aa (target size included)`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.setDensity('compact');
      await app.gotoRoute(route.path);
      await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
      await expectAxeClean(page, { key: { route: route.name, state: 'compact' }, theme, known: KNOWN_VIOLATIONS });
    });
  }
}
