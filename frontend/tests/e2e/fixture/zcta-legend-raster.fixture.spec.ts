/**
 * The drilled ZCTA map's legend draws the same pixels whatever sub-pixel
 * phase it held before (deviation:zcta-legend-flat; the e2e-visual flake of
 * `dark > segment-intelligence · zcta drill`, CI runs 41043090091 and
 * 41867598276).
 *
 * The drill re-reads the ranked table above the map, which moves the map by
 * a fractional number of pixels (293.5px at 1440x900) and flips the legend's
 * sub-pixel phase. While the legend carried `backdrop-filter` it was its own
 * compositor surface, and Chromium kept that surface's raster across the
 * flip: the class bar drew the old phase's edge row, 7px tall instead of 8
 * (byte-identical in both failed CI runs, and reproduced here off the pinned
 * host). Painted with the page, it re-rasters like everything else.
 *
 * The test moves the map by half a pixel with a spacer above it (the table
 * re-read's effect, without its timing), captures the class bar, makes the
 * legend paint from scratch (display none, then back) and captures it again:
 * the two captures must be byte-identical, in both themes and both phases.
 */
import type { Page } from '@playwright/test';
import type { FixtureTheme } from './app';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad, parkPointerAndBlur } from './visual';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const SPACER_ID = 'zcta-legend-phase-spacer';

async function frames(page: Page, count = 3): Promise<void> {
  await page.evaluate(
    (n) =>
      new Promise<void>((resolve) => {
        let left = n;
        const step = () => (--left <= 0 ? resolve() : requestAnimationFrame(step));
        requestAnimationFrame(step);
      }),
    count,
  );
}

/** The class bar with a 2px margin of the legend around it (inside the legend's padding). */
async function barShot(page: Page): Promise<Buffer> {
  const box = await page.locator('#main-content .map-legend__bar').boundingBox();
  if (!box) throw new Error('the legend class bar is not rendered');
  const clip = {
    x: Math.floor(box.x) - 2,
    y: Math.floor(box.y) - 2,
    width: Math.ceil(box.width) + 4,
    height: Math.ceil(box.height) + 4,
  };
  return page.screenshot({ clip, animations: 'disabled', caret: 'hide', scale: 'css' });
}

/** The legend's top edge, as laid out (fractional). */
async function legendTop(page: Page): Promise<number> {
  return page.locator('#main-content .map-legend').evaluate((el) => el.getBoundingClientRect().top);
}

for (const theme of THEMES) {
  test(`${theme}: the ZCTA legend is painted with the page and its class bar survives a sub-pixel move`, async ({ app, mockApi, page }) => {
    await app.setTheme(theme);
    await app.gotoRoute('/segment-intelligence');
    const map = page.locator('#main-content .map-wrap');
    const legend = map.locator('.map-legend');

    // Non-vacuity: the national overlay legend keeps the prototype's blur.
    await expect(legend).toBeVisible();
    expect(await legend.evaluate((el) => getComputedStyle(el).backdropFilter), 'the state map overlay legend blurs the map under it').toBe('blur(8px)');

    await page.locator('path[data-map-unit="il"]').click();
    await expect(page).toHaveURL(/geo_state=IL/);
    await expect(map.locator('svg.map-zcta')).toBeVisible();
    await expect(map.locator('.map-levels')).toHaveAttribute('aria-busy', 'false');
    await app.settle();
    const drilled = markNaturalLoad(mockApi);

    await map.scrollIntoViewIfNeeded();
    await app.settle();
    await parkPointerAndBlur(page);
    await map.evaluate((wrap, id) => {
      const spacer = document.createElement('div');
      spacer.id = id;
      spacer.style.blockSize = '0px';
      wrap.before(spacer);
    }, SPACER_ID);

    const start = await legendTop(page);
    for (const shift of [0.5, 0]) {
      await page.evaluate(([id, px]) => {
        (document.getElementById(id) as HTMLElement).style.blockSize = `${px}px`;
      }, [SPACER_ID, shift] as const);
      await frames(page);
      // Non-vacuity: the legend really moved by the shift, a half-pixel phase flip.
      expect(await legendTop(page), `the spacer moved the legend by ${shift}px`).toBeCloseTo(start + shift, 3);
      const moved = await barShot(page);
      await legend.evaluate((el) => {
        (el as HTMLElement).style.display = 'none';
      });
      await frames(page);
      await legend.evaluate((el) => {
        (el as HTMLElement).style.display = '';
      });
      await frames(page);
      const fresh = await barShot(page);
      expect(moved.equals(fresh), `${theme}, legend moved by ${shift}px: the class bar after the move matches a fresh paint`).toBe(true);
    }

    await page.evaluate((id) => document.getElementById(id)?.remove(), SPACER_ID);

    // Over the ZIP areas the legend is the solid --bg-1 panel: no backdrop-filter, so no surface of its own.
    const style = await legend.evaluate((el) => {
      const probe = document.createElement('div');
      probe.style.background = 'var(--bg-1)';
      el.append(probe);
      const bg1 = getComputedStyle(probe).backgroundColor;
      probe.remove();
      const computed = getComputedStyle(el);
      return { backdrop: computed.backdropFilter, background: computed.backgroundColor === bg1 ? 'var(--bg-1)' : computed.backgroundColor };
    });
    expect(style, 'the drilled legend is a solid --bg-1 panel with no backdrop-filter').toEqual({ backdrop: 'none', background: 'var(--bg-1)' });
    expectNoAuditedReadSince(mockApi, drilled, `${theme} zcta legend raster`);
  });
}
