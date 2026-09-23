/**
 * Lane map-encoding (audit dataviz-02 / responsive-05 / dataviz-04 / a11y-04):
 * the geography hero, proven where the defects lived, in the rendered page.
 *
 *  - Encoding: the painted fills, the ZIP tiles and the legend swatches are
 *    the same five ramp steps, and adjacent steps differ by >= 0.06 OKLab L
 *    in both themes (computed from the browser's own computed colours). A
 *    segment filter on Segment Intelligence repaints nothing: every state
 *    still paints its class's swatch at full opacity.
 *  - Resilience: a warming warehouse shows the WarmingUpBlock inside the map
 *    stage and an em dash, never "0", and the map recovers on its own.
 *  - URL: a drill is `?geo_state=TX`, Back restores the national view, a
 *    shared link opens the drill, and "Clear geography" resets map and table
 *    together.
 *  - Keyboard: one Tab stop reaches the states, arrows move between them with
 *    the card showing, and "View as table" lists the map's numbers. A drill
 *    that removes the focused control (a table row, a state with no ZIP
 *    rollup, a ZIP read still warming up) hands focus to the drilled level,
 *    never to <body>.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import type { FixtureTheme } from './app';
import { MAP_ALL_CLASSES_COUNTS, emptyZipRollupsFixture, mapAllClassesFixture } from './data/mapEncoding';
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
  /**
   * Every state path: its class, its computed fill, and what else changes the
   * colour it is painted in: `alpha` (fill-opacity times the opacity of the
   * path and of every ancestor up to `.map-wrap`) and its `filter`.
   */
  states: Array<{ id: string; cls: number; fill: Rgb; alpha: number; filter: string }>;
  /** Every ZIP tile: its class, its computed background and text colour. */
  zips: Array<{ zip: string; cls: number; background: Rgb; ink: Rgb }>;
}

/**
 * Read what the browser computed for the swatches, the state fills and the
 * ZIP tiles, normalised to sRGB through a 1x1 canvas (a color-mix() in oklab
 * computes to an oklab() value; the canvas paints whatever CSS colour the
 * engine resolved). Fails on a colour the canvas does not accept. Waits for
 * the map's finite animations and transitions (the level fade, the tiles'
 * entrance, a fill transition) to finish first, so it samples the resting
 * paint.
 */
async function readMapPaint(page: Page): Promise<MapPaint> {
  return page.evaluate(async () => {
    const wrap = document.querySelector('.map-wrap');
    if (!wrap) throw new Error('.map-wrap missing');
    await Promise.all(
      wrap
        .getAnimations({ subtree: true })
        .filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity)
        .map((animation) => animation.finished.catch(() => undefined)),
    );
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
    const alphaOf = (el: Element): number => {
      let alpha = Number(getComputedStyle(el).fillOpacity);
      for (let node: Element | null = el; node; node = node === wrap ? null : node.parentElement) {
        alpha *= Number(getComputedStyle(node).opacity);
      }
      return alpha;
    };
    const states = [...document.querySelectorAll<SVGPathElement>('path.map-region[data-map-unit]')].map((el) => ({
      id: el.getAttribute('data-map-unit') ?? '',
      cls: Number(el.getAttribute('data-map-class')),
      fill: toRgb(getComputedStyle(el).fill),
      alpha: alphaOf(el),
      filter: getComputedStyle(el).filter,
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

/** Focus a state path and press Enter: the keyboard drill. */
async function keyboardDrill(page: Page, stateId: string): Promise<void> {
  await page.locator(`path[data-map-unit="${stateId}"]`).focus();
  await page.keyboard.press('Enter');
}

/** Fraction of a state's fill (sampled on a 40 x 40 grid) that the legend box covers. */
async function legendCoverage(page: Page, stateId: string): Promise<number> {
  return page.evaluate((id) => {
    const legend = document.querySelector('.map-legend')?.getBoundingClientRect();
    const shape = document.querySelector<SVGPathElement>(`path[data-map-unit="${id}"]`);
    const ctm = shape?.getScreenCTM();
    if (!legend || !shape || !ctm) throw new Error(`legend or ${id} not laid out`);
    const box = shape.getBBox();
    let inside = 0;
    let covered = 0;
    for (let i = 0; i < 40; i += 1) {
      for (let j = 0; j < 40; j += 1) {
        const point = new DOMPoint(box.x + (box.width * (i + 0.5)) / 40, box.y + (box.height * (j + 0.5)) / 40);
        if (!shape.isPointInFill(point)) continue;
        inside += 1;
        const screen = point.matrixTransform(ctm);
        if (screen.x >= legend.left && screen.x <= legend.right && screen.y >= legend.top && screen.y <= legend.bottom) covered += 1;
      }
    }
    return inside === 0 ? 1 : covered / inside;
  }, stateId);
}

/** Focus is on exactly one element inside the map, not lost to <body>. */
async function expectFocusInMap(page: Page): Promise<void> {
  await expect(page.locator('.map-wrap :focus')).toHaveCount(1);
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false);
}

test.describe('ramp and legend encoding (dataviz-02 / responsive-05)', () => {
  for (const theme of THEMES) {
    test(`${theme}: painted fills, ZIP tiles and legend swatches are one ramp with clear steps`, async ({ app, mockApi, page }) => {
      mockApi.register(mapAllClassesFixture.method, mapAllClassesFixture.pattern, mapAllClassesFixture.handler);
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await waitForStateFills(page);

      const national = await readMapPaint(page);
      // Adjacent painted steps (no data -> lvl-1 -> ... -> lvl-4), measured on
      // the state fills themselves, are clearly apart.
      const painted = [0, 1, 2, 3, 4].map((cls) => {
        const state = national.states.find((s) => s.cls === cls);
        if (!state) throw new Error(`no state painted class ${cls}`);
        return state.fill;
      });
      const lightness = painted.map(oklabL);
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
      expect(contrastRatio(painted[4], painted[0])).toBeGreaterThanOrEqual(3);

      // Every class 1-4 is painted, and every painted state equals its swatch.
      const expected: Record<string, number> = { il: 4, tx: 4, ca: 3, fl: 3, az: 2, wa: 2, co: 1, ga: 1 };
      for (const [id, cls] of Object.entries(expected)) {
        const state = national.states.find((s) => s.id === id);
        expect(state?.cls, `${id} (${MAP_ALL_CLASSES_COUNTS[id.toUpperCase()]})`).toBe(cls);
        expect(state?.fill, `${id} fill equals legend lvl-${cls}`).toEqual(national.swatches[cls]);
        expect(state?.alpha, `${id} paints at full opacity`).toBe(1);
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

test.describe('segment filter keeps the legend encoding (dataviz-02)', () => {
  for (const theme of THEMES) {
    test(`${theme}: with a segment selected, every state paints its legend swatch at full opacity`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/segment-intelligence?segment=itm');
      await waitForStateFills(page);
      await expect(page.locator('.map-legend__caption')).toContainText('opportunity within');

      const paint = await readMapPaint(page);
      // Non-vacuous: populated states whose top segment is not the selected
      // one are on the map (the removed dim cue faded exactly these), and
      // unpopulated states paint the base step.
      const otherTop = STATES.filter((s) => s.topSegment !== 'itm').map((s) => s.code.toLowerCase());
      expect(otherTop.length).toBeGreaterThan(0);
      for (const id of otherTop) {
        expect(paint.states.find((s) => s.id === id)?.cls, `${id} is populated`).toBeGreaterThan(0);
      }
      expect(paint.states.some((s) => s.cls === 0)).toBe(true);
      for (const state of paint.states) {
        expect(
          { fill: state.fill, alpha: state.alpha, filter: state.filter },
          `${theme} ${state.id} paints legend lvl-${state.cls}`,
        ).toEqual({ fill: paint.swatches[state.cls], alpha: 1, filter: 'none' });
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
      await expect(focused).toHaveAttribute('aria-label', /^Arizona: 9,040 marketable borrowers, average opportunity score 78/);
      await expect(page.locator('.map-tip .map-tip__name')).toHaveText('Arizona');
      // The keyboard hint the focus reveals wraps inside the legend instead of
      // widening it over the first tab stop (it covered ~45% of Arizona).
      await expect(page.locator('.map-legend__hint')).toBeVisible();
      expect(await legendCoverage(page, 'az')).toBe(0);

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

      // Escape hides the card and stops there, so the same keypress never also
      // reaches a window listener (an open menu's); with no card it does.
      await page.evaluate(() => {
        const probe = window as Window & { mapEscapes?: number };
        probe.mapEscapes = 0;
        window.addEventListener('keydown', (event) => {
          if (event.key === 'Escape') probe.mapEscapes = (probe.mapEscapes ?? 0) + 1;
        });
      });
      const windowEscapes = () => page.evaluate(() => (window as Window & { mapEscapes?: number }).mapEscapes);
      await page.keyboard.press('Escape');
      await expect(page.locator('.map-tip')).toHaveCount(0);
      expect(await windowEscapes()).toBe(0);
      await page.keyboard.press('Escape');
      expect(await windowEscapes()).toBe(1);

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
      await expect(firstZip).toHaveAccessibleName(/^ZIP \d{5}: [\d,]+ borrowers, average opportunity score \d+/);
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

      // A state row drills like the map does. The row's button goes with the
      // drill, so focus moves to the ZIP table (named by its caption).
      await table.getByRole('button', { name: 'Texas' }).focus();
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(/geo_state=TX/);
      await expect(page.getByTestId('map-table-total')).toHaveText((stateByCode('TX')?.addressable ?? 0).toLocaleString('en-US'));
      await expect(page.getByRole('table', { name: /^Marketable borrowers by ZIP in Texas/ })).toBeFocused();
      await expectFocusInMap(page);
      // The ZIP rows scroll inside the map; the total row stays in view.
      const scroller = page.getByTestId('map-table');
      expect(await scroller.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
      const [scrollBox, totalBox] = await Promise.all([scroller.boundingBox(), page.getByTestId('map-table-total').boundingBox()]);
      if (!scrollBox || !totalBox) throw new Error('ZIP table not laid out');
      expect(totalBox.y + totalBox.height).toBeLessThanOrEqual(scrollBox.y + scrollBox.height + 1);
      await page.getByRole('button', { name: 'View as map' }).click();
      await expect(page.getByRole('list', { name: 'ZIPs in Texas' })).toBeVisible();
    });
  }
});

test.describe('a drill never drops focus to <body> (a11y-04)', () => {
  test('Enter on a state with no ZIP rollup focuses its Lead Queue action', async ({ app, mockApi, page }) => {
    mockApi.register(emptyZipRollupsFixture.method, emptyZipRollupsFixture.pattern, emptyZipRollupsFixture.handler);
    await app.setTheme('dark');
    await app.gotoRoute('/');
    await waitForStateFills(page);

    await keyboardDrill(page, 'az');
    await expect(page).toHaveURL(/geo_state=AZ/);
    await expect(page.locator('.map-wrap')).toContainText('No ZIP-level rollup for Arizona.');
    await expect(page.getByRole('button', { name: 'Open Lead Queue for Arizona' })).toBeFocused();
    await expectFocusInMap(page);
  });

  test('a ZIP read still warming up holds focus on the stage, then hands it to the first ZIP', async ({ app, page }) => {
    await app.setTheme('light');
    await app.gotoRoute('/');
    await waitForStateFills(page);
    const recover = app.degrade('/api/geo/zip-rollups', WAREHOUSE_WARMING_UP);

    await keyboardDrill(page, 'az');
    const stage = page.getByRole('group', { name: 'ZIP rollups for Arizona' });
    await expect(stage.getByTestId('warming-up-block')).toBeVisible({ timeout: 20_000 });
    await expect(stage).toBeFocused();

    recover();
    const firstZip = page.getByRole('list', { name: 'ZIPs in Arizona' }).getByRole('button').first();
    await expect(firstZip).toBeFocused({ timeout: 20_000 });
    await expect(page.locator('.map-tip .map-tip__name')).toHaveText(/^ZIP \d{5}, Arizona$/);
  });

  test('a failed ZIP read focuses Retry, and Retry hands focus to the first ZIP', async ({ app, page }) => {
    await app.setTheme('dark');
    await app.gotoRoute('/');
    await waitForStateFills(page);
    const recover = app.degrade('/api/geo/zip-rollups', { status: 500, body: { detail: 'fixture: ZIP rollups are down' } });

    await keyboardDrill(page, 'az');
    const retry = page.locator('.map-wrap').getByRole('button', { name: 'Retry' });
    await expect(retry).toBeFocused();
    await expectFocusInMap(page);

    recover();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('list', { name: 'ZIPs in Arizona' }).getByRole('button').first()).toBeFocused();
  });
});
