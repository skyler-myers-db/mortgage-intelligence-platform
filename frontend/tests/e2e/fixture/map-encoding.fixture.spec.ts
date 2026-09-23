/**
 * Lane map-encoding (audit dataviz-02 / responsive-05 / dataviz-04 / a11y-04):
 * the geography hero, proven where the defects lived, in the rendered page.
 *
 *  - Encoding: the painted fills, the ZIP tiles and the legend swatches are
 *    the same five ramp steps, and adjacent steps differ by >= 0.06 OKLab L
 *    in both themes (computed from the browser's own computed colours).
 *  - Resilience: a warming warehouse shows the WarmingUpBlock inside the map
 *    stage and an em dash, never "0", and the map recovers on its own.
 *  - URL: a drill is `?geo_state=TX`, Back restores the national view, a
 *    shared link opens the drill, and "Clear geography" resets map and table
 *    together.
 *  - Keyboard: one Tab stop reaches the states, arrows move between them with
 *    the card showing, and "View as table" lists the map's numbers.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import type { FixtureTheme } from './app';
import { MAP_ALL_CLASSES_COUNTS, mapAllClassesFixture } from './data/mapEncoding';
import { STATES, TOTALS, stateByCode } from './data/reference';
import { WAREHOUSE_WARMING_UP } from './mockApi';
import { contrastRatio, type Rgb } from './renderedColor';
import { expect, test } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/** OKLab lightness of an 8-bit sRGB colour (Björn Ottosson's matrices). */
function oklabL([r, g, b]: Rgb): number {
  const lin = (v: number) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const [lr, lg, lb] = [lin(r), lin(g), lin(b)];
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
}

interface MapPaint {
  /** Legend swatches lvl-0..4 as 8-bit sRGB. */
  swatches: Rgb[];
  /** Every painted state path: its class and its computed fill. */
  states: Array<{ id: string; cls: number; fill: Rgb }>;
  /** Every ZIP tile: its class, its computed background and text colour. */
  zips: Array<{ zip: string; cls: number; background: Rgb; ink: Rgb }>;
}

/**
 * Read what the browser computed for the swatches, the state fills and the
 * ZIP tiles, normalised to sRGB through a 1x1 canvas (a color-mix() in oklab
 * computes to an oklab() value; the canvas paints whatever CSS colour the
 * engine resolved). Fails on a colour the canvas does not accept.
 */
async function readMapPaint(page: Page): Promise<MapPaint> {
  return page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('2d canvas unavailable');
    const toRgb = (css: string): [number, number, number] => {
      for (const sentinel of ['#010203', '#040506']) {
        ctx.fillStyle = sentinel;
        ctx.fillStyle = css;
        if (ctx.fillStyle === sentinel) continue;
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillRect(0, 0, 1, 1);
        const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
        return [r, g, b];
      }
      throw new Error(`canvas rejected colour: ${css}`);
    };
    const swatches = [0, 1, 2, 3, 4].map((cls) => {
      const el = document.querySelector(`.map-legend__bar .lvl-${cls}`);
      if (!el) throw new Error(`legend swatch lvl-${cls} missing`);
      return toRgb(getComputedStyle(el).backgroundColor);
    });
    const states = [...document.querySelectorAll<SVGPathElement>('path.map-region[data-map-unit]')].map((el) => ({
      id: el.getAttribute('data-map-unit') ?? '',
      cls: Number(el.getAttribute('data-map-class')),
      fill: toRgb(getComputedStyle(el).fill),
    }));
    const zips = [...document.querySelectorAll<HTMLElement>('button.zip-tile[data-map-unit]')].map((el) => ({
      zip: el.getAttribute('data-map-unit') ?? '',
      cls: Number(el.getAttribute('data-map-class')),
      background: toRgb(getComputedStyle(el).backgroundColor),
      ink: toRgb(getComputedStyle(el.querySelector('.zip-tile__code') ?? el).color),
    }));
    return { swatches, states, zips };
  });
}

async function waitForStateFills(page: Page): Promise<void> {
  await expect(page.locator('path.map-region.has-data').first()).toBeVisible();
  await expect(page.locator('.map-levels')).toHaveAttribute('aria-busy', 'false');
}

test.describe('ramp and legend encoding (dataviz-02 / responsive-05)', () => {
  for (const theme of THEMES) {
    test(`${theme}: painted fills, ZIP tiles and legend swatches are one ramp with clear steps`, async ({ app, mockApi, page }) => {
      mockApi.register(mapAllClassesFixture.method, mapAllClassesFixture.pattern, mapAllClassesFixture.handler);
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await waitForStateFills(page);

      const national = await readMapPaint(page);
      // Adjacent steps (no data -> lvl-1 -> ... -> lvl-4) are clearly apart.
      const lightness = national.swatches.map(oklabL);
      for (let step = 1; step < lightness.length; step += 1) {
        const delta = Math.abs(lightness[step] - lightness[step - 1]);
        expect(delta, `${theme} step ${step - 1} -> ${step}: L ${lightness.map((l) => l.toFixed(3)).join(' ')}`).toBeGreaterThanOrEqual(0.06);
      }
      // ...and monotone, so "Higher" really is further from the base.
      const direction = Math.sign(lightness[4] - lightness[0]);
      for (let step = 1; step < lightness.length; step += 1) {
        expect(Math.sign(lightness[step] - lightness[step - 1])).toBe(direction);
      }
      // Not a pale wash: the top step stands off the empty base.
      expect(contrastRatio(national.swatches[4], national.swatches[0])).toBeGreaterThanOrEqual(3);

      // Every class 1-4 is painted, and every painted state equals its swatch.
      const expected: Record<string, number> = { il: 4, tx: 4, ca: 3, fl: 3, az: 2, wa: 2, co: 1, ga: 1 };
      for (const [id, cls] of Object.entries(expected)) {
        const state = national.states.find((s) => s.id === id);
        expect(state?.cls, `${id} (${MAP_ALL_CLASSES_COUNTS[id.toUpperCase()]})`).toBe(cls);
        expect(state?.fill, `${id} fill equals legend lvl-${cls}`).toEqual(national.swatches[cls]);
      }
      const empty = national.states.find((s) => s.id === 'ny');
      expect(empty?.cls).toBe(0);
      expect(empty?.fill, 'an unpopulated state paints the first swatch').toEqual(national.swatches[0]);
      // The legend prints the sqrt breaks between Lower and Higher.
      await expect(page.locator('.map-legend__range')).toHaveText(/^Lower\s*2\.5K\s*10K\s*22\.5K\s*Higher$/);

      // The ZIP drill paints the same ramp, with AA text on every step.
      await app.gotoRoute('/?geo_state=IL');
      await expect(page.locator('button.zip-tile').first()).toBeVisible();
      const drilled = await readMapPaint(page);
      expect(new Set(drilled.zips.map((z) => z.cls))).toEqual(new Set([1, 2, 3, 4]));
      for (const tile of drilled.zips) {
        expect(tile.background, `ZIP ${tile.zip} equals legend lvl-${tile.cls}`).toEqual(drilled.swatches[tile.cls]);
        expect(contrastRatio(tile.ink, tile.background), `ZIP ${tile.zip} code on lvl-${tile.cls}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
});

test.describe('resilience (dataviz-04)', () => {
  test('a warming warehouse shows the WarmingUpBlock in the map stage and an em dash, then recovers', async ({ app, page }) => {
    await app.setTheme('dark');
    const recover = app.degrade('/api/geo/state-rollups', WAREHOUSE_WARMING_UP);
    await app.gotoRoute('/');

    const block = page.locator('.map-wrap .map-stage [data-testid="warming-up-block"]');
    await expect(block).toBeVisible({ timeout: 20_000 });
    await expect(block).toContainText('State borrower rollups');
    await expect(page.locator('.map-legend__value')).toHaveText('—');
    await expect(page.locator('.map-wrap')).not.toContainText(/Borrowers in selection\s*0\b/);
    await expect(page.locator('.map-levels')).toHaveAttribute('aria-busy', 'false');
    // The home KPI still renders from its own payload.
    await expect(page.locator('.kpi .kpi__value').first()).toHaveText(TOTALS.addressable.toLocaleString('en-US'));

    // The dependency recovers: the retry loop repaints without a reload.
    recover();
    await waitForStateFills(page);
    await expect(block).toHaveCount(0);
    await expect(page.locator('.map-legend__value')).toHaveText(TOTALS.addressable.toLocaleString('en-US'));
  });
});

test.describe('controlled, deep-linkable drill (dataviz-04)', () => {
  test('drilling TX puts ?geo_state=TX in the URL and Back restores the national view', async ({ app, page }) => {
    await app.setTheme('light');
    await app.gotoRoute('/');
    await waitForStateFills(page);

    await page.locator('path[data-map-unit="tx"]').click();
    await expect(page).toHaveURL(/[?&]geo_state=TX(&|$)/);
    await expect(page.getByRole('list', { name: 'ZIPs in Texas' })).toBeVisible();

    await page.goBack();
    await expect(page).not.toHaveURL(/geo_state=/);
    await expect(page.locator('ul.zip-tiles')).toHaveCount(0);
    await expect(page.locator('path[data-map-unit="tx"]')).toBeVisible();

    await page.goForward();
    await expect(page).toHaveURL(/[?&]geo_state=TX(&|$)/);
    await expect(page.getByRole('list', { name: 'ZIPs in Texas' })).toBeVisible();
  });

  test('a shared link opens the drill, and Clear geography resets map and table together', async ({ app, page }) => {
    const texas = stateByCode('TX');
    await app.setTheme('dark');
    await app.gotoRoute('/segment-intelligence?geo_state=TX');

    const total = page.locator('.segment-mode-control__total .num');
    await expect(page.getByRole('list', { name: 'ZIPs in Texas' })).toBeVisible();
    await expect(page.locator('.chip', { hasText: 'state: TX' })).toBeVisible();
    await expect(total).toHaveText((texas?.contactable ?? 0).toLocaleString('en-US'));

    await page.getByRole('button', { name: 'Clear geography' }).click();
    await expect(page).not.toHaveURL(/geo_state=/);
    await expect(page.locator('ul.zip-tiles')).toHaveCount(0);
    await expect(page.locator('path[data-map-unit="tx"]')).toBeVisible();
    await expect(page.locator('.chip', { hasText: 'state: TX' })).toHaveCount(0);
    await expect(total).toHaveText(TOTALS.contactable.toLocaleString('en-US'));
  });
});

test.describe('keyboard, screen reader and table access (a11y-04)', () => {
  for (const theme of THEMES) {
    test(`${theme}: one Tab reaches the states, arrows move with the card, the drill is axe clean`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await waitForStateFills(page);
      await expect(page.locator('path[data-map-unit][tabindex="0"]')).toHaveCount(1);

      // The control just before the map, then one Tab into it.
      await page.getByRole('button', { name: 'View as table' }).focus();
      await page.keyboard.press('Tab');
      const focused = page.locator('path[data-map-unit]:focus');
      await expect(focused).toHaveAttribute('data-map-unit', 'az');
      await expect(focused).toHaveAttribute('aria-label', /^Arizona, 9,040 marketable borrowers, average opportunity score 78/);
      await expect(page.locator('.map-tip .map-tip__name')).toHaveText('Arizona');

      await page.keyboard.press('ArrowRight');
      await expect(page.locator('path[data-map-unit]:focus')).toHaveAttribute('data-map-unit', 'ar');
      await expect(page.locator('.map-tip .map-tip__name')).toHaveText('Arkansas');
      // The card sits over the focused state, not at the pointer (it
      // re-anchors on the frame after the focus scroll, so poll for it).
      await expect(async () => {
        const [card, shape] = await Promise.all([
          page.locator('.map-tip').boundingBox(),
          page.locator('path[data-map-unit="ar"]').boundingBox(),
        ]);
        if (!card || !shape) throw new Error('card or state not laid out');
        expect(Math.abs(card.x + card.width / 2 - (shape.x + shape.width / 2))).toBeLessThan(2);
        expect(card.y + card.height).toBeLessThanOrEqual(shape.y + 1);
      }).toPass();

      const focusedMap = await new AxeBuilder({ page }).include('.map-wrap').include('.map-tip').withTags(WCAG_TAGS).analyze();
      expect(focusedMap.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);

      // One more Tab leaves the map (51 states were 51 Tab stops).
      await page.keyboard.press('Tab');
      await expect(page.locator('path[data-map-unit]:focus')).toHaveCount(0);
      await expect(page.locator('.map-tip')).toHaveCount(0);
      // Shift+Tab comes back to the state the arrows left.
      await page.keyboard.press('Shift+Tab');
      await expect(page.locator('path[data-map-unit]:focus')).toHaveAttribute('data-map-unit', 'ar');

      // Enter on a populated state drills and moves focus to its first ZIP.
      await page.keyboard.press('Home');
      await expect(page.locator('path[data-map-unit]:focus')).toHaveAttribute('data-map-unit', 'al');
      await page.keyboard.press('ArrowRight');
      await page.keyboard.press('ArrowRight');
      await expect(page.locator('path[data-map-unit]:focus')).toHaveAttribute('data-map-unit', 'az');
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(/geo_state=AZ/);
      const firstZip = page.getByRole('list', { name: 'ZIPs in Arizona' }).getByRole('button').first();
      await expect(firstZip).toBeFocused();
      await expect(firstZip).toHaveAccessibleName(/^ZIP \d{5}, [\d,]+ borrowers, average opportunity score \d+/);
      await expect(page.locator('.map-tip .map-tip__name')).toHaveText(/^ZIP \d{5}, Arizona$/);
      await page.keyboard.press('ArrowRight');
      await expect(page.getByRole('list', { name: 'ZIPs in Arizona' }).getByRole('button').nth(1)).toBeFocused();

      const drilled = await new AxeBuilder({ page }).include('.map-wrap').withTags(WCAG_TAGS).analyze();
      expect(drilled.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
    });

    test(`${theme}: View as table lists the map's numbers and its total equals the legend`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await waitForStateFills(page);
      const legendTotal = TOTALS.addressable.toLocaleString('en-US');
      await expect(page.locator('.map-legend__value')).toHaveText(legendTotal);

      await page.getByRole('button', { name: 'View as table' }).click();
      const table = page.getByTestId('map-table').locator('table');
      await expect(table).toBeVisible();
      await expect(page.locator('svg.map-svg-stage')).toHaveCount(0);
      const rows = table.locator('tbody tr');
      await expect(rows).toHaveCount(STATES.length);
      await expect(page.getByTestId('map-table-total')).toHaveText(legendTotal);
      // Sorted by count, largest first; the header says so.
      const sortedNames = [...STATES].sort((a, b) => b.addressable - a.addressable).map((s) => s.name);
      await expect(rows.locator('th')).toHaveText(sortedNames);
      await expect(table.locator('th[aria-sort]')).toHaveAttribute('aria-sort', 'descending');
      await table.getByRole('button', { name: /Marketable borrowers/ }).click();
      await expect(table.locator('th[aria-sort]')).toHaveAttribute('aria-sort', 'ascending');
      await expect(rows.locator('th')).toHaveText([...sortedNames].reverse());

      const scan = await new AxeBuilder({ page }).include('.map-wrap').withTags(WCAG_TAGS).analyze();
      expect(scan.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);

      // A state row drills like the map does.
      await table.getByRole('button', { name: 'Texas' }).click();
      await expect(page).toHaveURL(/geo_state=TX/);
      await expect(page.getByTestId('map-table-total')).toHaveText((stateByCode('TX')?.addressable ?? 0).toLocaleString('en-US'));
      await page.getByRole('button', { name: 'View as map' }).click();
      await expect(page.getByRole('list', { name: 'ZIPs in Texas' })).toBeVisible();
    });
  }
});
