/**
 * W5c w5-zcta-watchlist: the ZIP rung as a map (audit dataviz-01 / visual-09
 * / motion-10; D-dataviz-geo-e; deviation:zcta-level), proven in the
 * production build at 1440x900, both themes, drilling Illinois and Texas from
 * Segment Intelligence.
 *
 *  - Polygons: committed ZCTAs painted with the ramp classes, the viewBox
 *    fitted to the populated ones; Zoom in / Zoom out / Fit move it;
 *    ctrl+wheel is prevented (the page never zooms) while a plain wheel is
 *    left to the page; Escape backs out; the card paints over the Genie
 *    panel; a ZCTA click is one audited GET /api/leads (state + zip) and
 *    nothing else; one geometry fetch per drilled state, a hashed
 *    /assets/<USPS>.topo-*.json; a ZIP with no ZCTA is in the reconcile
 *    note; forced colours keep the outline; axe clean; report-only TX
 *    drill-to-paint at 4x CPU ('[zcta-drill]').
 *  - Always (no geometry needed): Home never loads the rung or any geometry;
 *    the national hover on Segments warms JS only; a keyboard drill into a
 *    state with an empty ZIP rollup keeps the tiles' empty card (focus on
 *    its Lead Queue action, no status line, no rung load); a geometry read
 *    that fails (or a state with no file) falls back to the densest-ZIP
 *    tiles with their status line.
 *
 * Every polygon-dependent case is a fixme until the approved operator build
 * commits frontend/src/geo/zcta (it lifts itself once manifest.json exists).
 */
import fs from 'node:fs';
import path from 'node:path';
import type { Page, Request } from '@playwright/test';
import type { ZipRollupResponse } from '../../../src/types';
import type { FixtureTheme } from './app';
import { expectAxeClean } from './axe';
import { emptyZipRollupsFixture } from './data/mapEncoding';
import { json } from './mockApi';
import { expect, test } from './test';
import { auditedReadsAfter, markNaturalLoad } from './visual';

const GEO_DIR = path.resolve(process.cwd(), 'src', 'geo', 'zcta');
const GEOMETRY_COMMITTED = fs.existsSync(path.join(GEO_DIR, 'manifest.json'));
const PENDING = 'geometry not committed: operator build pending';
const SEGMENTS = '/segment-intelligence?marketing_eligibility=Any';
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const TILE_STATUS = 'ZIP boundaries could not load; showing the densest ZIPs as tiles.';
const GEOMETRY_URL = /\/assets\/([A-Z]{2})\.topo-[^/]+\.json$/;
const RUNG_CHUNK = /\/assets\/USChoroplethMapZctaLevel-[^/]+\.js$/;
/** A Chicago PO-box ZIP: no ZCTA (verified against IL's committed file in-spec). */
const NO_BOUNDARY_ZIP = '60690';

/** Every request whose URL matches `pattern`, in order. */
function track(page: Page, pattern: RegExp): string[] {
  const urls: string[] = [];
  page.on('request', (request: Request) => {
    const url = new URL(request.url()).pathname;
    if (pattern.test(url)) urls.push(url);
  });
  return urls;
}

const statePath = (page: Page, id: string) => page.locator(`#main-content path[data-map-unit="${id}"]`);
const zctaStage = (page: Page) => page.locator('#main-content svg.map-zcta');

async function drill(page: Page, state: 'il' | 'tx'): Promise<void> {
  await statePath(page, state).click();
  await expect(page).toHaveURL(new RegExp(`geo_state=${state.toUpperCase()}`));
}

/** The viewBox once the drill tween has settled (two equal reads a frame apart). */
async function settledViewBox(page: Page): Promise<number[]> {
  let previous = '';
  await expect.poll(async () => {
    const current = (await zctaStage(page).getAttribute('viewBox')) ?? '';
    const same = current !== '' && current === previous;
    previous = current;
    return same;
  }, { intervals: [120] }).toBe(true);
  return previous.split(' ').map(Number);
}

/** A client point inside the first populated ZCTA (its box centre can be a neighbour's). */
async function pointInside(page: Page): Promise<{ x: number; y: number; zip: string }> {
  return zctaStage(page).evaluate((svg) => {
    for (const path of svg.querySelectorAll<SVGPathElement>('path[data-populated]')) {
      const box = path.getBoundingClientRect();
      for (let i = 1; i < 8; i += 1) {
        for (let j = 1; j < 8; j += 1) {
          const x = box.left + (box.width * i) / 8;
          const y = box.top + (box.height * j) / 8;
          if (document.elementFromPoint(x, y) === path) return { x, y, zip: path.getAttribute('data-map-unit') ?? '' };
        }
      }
    }
    throw new Error('no populated ZCTA is hittable');
  });
}

function manifestStateBox(usps: string): number[] {
  const manifest = JSON.parse(fs.readFileSync(path.join(GEO_DIR, 'manifest.json'), 'utf8')) as {
    files: Record<string, { state_bbox: number[] }>;
  };
  return manifest.files[`${usps}.topo.json`].state_bbox;
}

test.describe('ZIP areas (needs the committed geometry)', () => {
  test.beforeEach(() => {
    test.fixme(!GEOMETRY_COMMITTED, PENDING);
  });

  for (const theme of THEMES) {
    for (const state of ['il', 'tx'] as const) {
      test(`${state.toUpperCase()} paints ZCTA polygons fitted to its populated areas, and the buttons zoom (${theme})`, async ({ app, page }) => {
        await app.setTheme(theme);
        await app.gotoRoute(SEGMENTS);
        await drill(page, state);
        await expect(zctaStage(page)).toBeVisible();
        const populated = zctaStage(page).locator('path[data-populated]');
        expect(await populated.count()).toBeGreaterThan(0);
        await expect(populated.first()).toHaveClass(/map-region (has-data lvl-[1-4]|is-empty)/);
        await expect(page.locator('.map-legend__caption')).toContainText('ZIP areas are Census 2020 ZCTAs');
        const [, , fittedW] = await settledViewBox(page);
        const [x0, , x1] = manifestStateBox(state.toUpperCase());
        expect(fittedW).toBeGreaterThan(0);
        expect(fittedW).toBeLessThanOrEqual(x1 - x0 + 0.01);
        await page.getByRole('button', { name: 'Zoom in' }).click();
        expect((await settledViewBox(page))[2]).toBeLessThan(fittedW);
        await page.getByRole('button', { name: 'Fit to the populated ZIP areas' }).click();
        expect((await settledViewBox(page))[2]).toBeCloseTo(fittedW, 2);
        await expect(page.getByRole('button', { name: 'Zoom out' })).toHaveAttribute('aria-disabled', 'true');
      });
    }
  }

  test('ctrl+wheel zooms without zooming the page; a plain wheel is left to the page', async ({ app, page }) => {
    await app.gotoRoute(SEGMENTS);
    await drill(page, 'il');
    await settledViewBox(page);
    const prevented = await zctaStage(page).evaluate((svg) => {
      const fire = (ctrlKey: boolean) => {
        const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -60, ctrlKey, clientX: 400, clientY: 300 });
        svg.dispatchEvent(event);
        return event.defaultPrevented;
      };
      return { ctrl: fire(true), plain: fire(false) };
    });
    expect(prevented).toEqual({ ctrl: true, plain: false });
  });

  test('Escape hides the card, then backs out to the national view', async ({ app, page }) => {
    await app.gotoRoute(SEGMENTS);
    await drill(page, 'il');
    await zctaStage(page).locator('path[tabindex="0"]').focus();
    await expect(page.locator('.map-tip')).toHaveCount(1);
    await expect(page.locator('.map-tip')).toContainText('Counts are Illinois borrowers in this ZIP area.');
    await page.keyboard.press('Escape');
    await expect(page.locator('.map-tip')).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(page).not.toHaveURL(/geo_state=/);
  });

  test('the ZIP card paints over the floating Genie panel', async ({ app, page }) => {
    await app.gotoRoute(SEGMENTS);
    await drill(page, 'il');
    await settledViewBox(page);
    const genie = await app.openGenie();
    const panel = await genie.boundingBox();
    if (!panel) throw new Error('Genie panel has no box');
    const at = { x: Math.round(panel.x + panel.width / 2), y: Math.round(panel.y + panel.height / 2) + 60 };
    await zctaStage(page).locator('path[data-populated]').first().evaluate((path, point) => {
      for (const type of ['pointerover', 'pointermove']) path.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: point.x, clientY: point.y }));
    }, at);
    const tip = page.locator('.map-tip');
    await expect(tip).toHaveCount(1);
    expect(await tip.evaluate((card) => card.matches(':popover-open'))).toBe(true);
  });

  test('a ZCTA click is one audited GET /api/leads for its state and ZIP, and nothing else', async ({ app, mockApi, page }) => {
    await app.gotoRoute(SEGMENTS);
    await drill(page, 'il');
    await settledViewBox(page);
    const point = await pointInside(page);
    const mark = markNaturalLoad(mockApi);
    await page.mouse.click(point.x, point.y);
    await expect(page).toHaveURL(new RegExp(`/lead-queue\\?state=IL&zip=${point.zip}`));
    await app.settle();
    const audited = auditedReadsAfter(mockApi.calls, mark);
    expect(audited).toHaveLength(1);
    expect(audited[0]).toMatch(new RegExp(`^GET /api/leads\\?.*state=IL.*zip=${point.zip}.*\\(VIEW_LEADS\\)$`));
    const writes = mockApi.calls.slice(mark).filter((call) => call.method !== 'GET');
    expect(writes).toEqual([]);
  });

  test('one geometry fetch per drilled state, a hashed same-origin asset, none on Home or the national hover', async ({ app, page }) => {
    const geometry = track(page, GEOMETRY_URL);
    await app.gotoRoute('/');
    await statePath(page, 'il').hover();
    await app.gotoRoute(SEGMENTS);
    await statePath(page, 'tx').hover();
    expect(geometry).toEqual([]);
    await drill(page, 'il');
    await expect(zctaStage(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await page.locator('.map-crumbs__trail button').first().click();
    await expect(page).not.toHaveURL(/geo_state=/);
    await drill(page, 'il');
    await expect(zctaStage(page)).toBeVisible();
    expect(geometry).toHaveLength(1);
    expect(geometry[0]).toMatch(/^\/assets\/IL\.topo-[^/]+\.json$/);
  });

  test('a ZIP with borrowers and no ZCTA is in the reconcile note', async ({ app, mockApi, page }) => {
    const il = fs.readFileSync(path.join(GEO_DIR, 'IL.topo.json'), 'utf8');
    expect(il.includes(`"id":"${NO_BOUNDARY_ZIP}"`), 'precondition: the ZIP has no ZCTA in IL').toBe(false);
    mockApi.register('GET', '/api/geo/zip-rollups', () => json<ZipRollupResponse>({
      state: 'IL',
      fips_5: null,
      snapshot_date: '2026-07-14',
      rollups: [
        { zip: '60611', state: 'IL', county_fips_5: null, addressable_borrowers: 900, avg_opportunity_score: 80, top_segment_code: 'itm', sample_borrower_id: null },
        { zip: NO_BOUNDARY_ZIP, state: 'IL', county_fips_5: null, addressable_borrowers: 37, avg_opportunity_score: 70, top_segment_code: 'itm', sample_borrower_id: null },
      ],
    }));
    await app.gotoRoute(SEGMENTS);
    await drill(page, 'il');
    await expect(zctaStage(page)).toBeVisible();
    await expect(page.locator('#main-content .map-wrap [role="note"]')).toContainText(
      '1 ZIP (37 borrowers) has no Census ZIP-area boundary (PO box or unique ZIP)',
    );
  });

  test('forced colours keep the state outline, and the drilled map is axe clean', async ({ app, page }) => {
    await app.gotoRoute(SEGMENTS);
    await drill(page, 'il');
    await settledViewBox(page);
    for (const theme of THEMES) {
      await page.emulateMedia({ colorScheme: theme });
      await expectAxeClean(page, { key: { route: 'zcta-drill', state: 'polygons' }, theme, known: {}, include: '.map-wrap' });
    }
    await page.emulateMedia({ forcedColors: 'active' });
    const outline = await page.locator('.map-zcta__outline').evaluate((path) => {
      const probe = document.createElement('span');
      probe.style.color = 'CanvasText';
      document.body.append(probe);
      const canvasText = getComputedStyle(probe).color;
      probe.remove();
      return { stroke: getComputedStyle(path).stroke, canvasText };
    });
    expect(outline.stroke).toBe(outline.canvasText);
  });

  test('report-only: TX drill-to-paint at 4x CPU', async ({ app, browserName, page }) => {
    test.skip(browserName !== 'chromium', 'CPU throttling is a Chromium DevTools protocol call');
    await app.gotoRoute(SEGMENTS);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
    const started = Date.now();
    await drill(page, 'tx');
    await expect(zctaStage(page).locator('path[data-populated]').first()).toBeVisible();
    console.log(`[zcta-drill] TX drill-to-paint at 4x CPU: ${Date.now() - started} ms`);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  });
});

test.describe('the rung without geometry (always)', () => {
  test('Home never loads the rung or any geometry, and keeps its plain tiles', async ({ app, page }) => {
    const rung = track(page, RUNG_CHUNK);
    const geometry = track(page, GEOMETRY_URL);
    await app.gotoRoute('/');
    await statePath(page, 'il').hover();
    await drill(page, 'il');
    await expect(page.locator('#main-content ul.zip-tiles')).toBeVisible();
    await expect(page.locator('#main-content .zip-tiles__status')).toHaveCount(0);
    await app.settle();
    expect(rung).toEqual([]);
    expect(geometry).toEqual([]);
  });

  test('the national hover on Segments warms only the rung chunk, never geometry', async ({ app, page }) => {
    const rung = track(page, RUNG_CHUNK);
    const geometry = track(page, GEOMETRY_URL);
    await app.gotoRoute(SEGMENTS);
    await statePath(page, 'tx').hover();
    await expect.poll(() => rung.length).toBe(1);
    await app.settle();
    expect(geometry).toEqual([]);
  });

  test('a keyboard drill into a state with an empty ZIP rollup focuses its Lead Queue action, with no status line and no rung load', async ({ app, mockApi, page }) => {
    const rung = track(page, RUNG_CHUNK);
    mockApi.register(emptyZipRollupsFixture.method, emptyZipRollupsFixture.pattern, emptyZipRollupsFixture.handler);
    await app.gotoRoute(SEGMENTS);
    await expect(page.locator('#main-content path.map-region.has-data').first()).toBeVisible();
    await expect(page.locator('#main-content .map-levels')).toHaveAttribute('aria-busy', 'false');
    // The keyboard drill: no pointer reaches the national stage, so nothing warms the rung either.
    await statePath(page, 'az').focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/geo_state=AZ/);
    await expect(page.locator('#main-content .map-wrap')).toContainText('No ZIP-level rollup for Arizona.');
    await expect(page.getByRole('button', { name: 'Open Lead Queue for Arizona' })).toBeFocused();
    await expect(page.locator('#main-content .zip-tiles__status')).toHaveCount(0);
    await app.settle();
    expect(rung).toEqual([]);
  });

  test('a geometry read that fails falls back to the densest-ZIP tiles with their status line', async ({ app, hygiene, page }) => {
    // The aborted geometry reads (the first try and its two retries) are the point of the test.
    hygiene.allow('request-failed', /\/assets\/[A-Z]{2}\.topo-[^/]+\.json failed: net::ERR_FAILED/);
    hygiene.allow('console.error', /Failed to load resource: net::ERR_FAILED \(http:\/\/[^)]+\/assets\/[A-Z]{2}\.topo-/);
    await page.route(GEOMETRY_URL, (route) => route.abort());
    await app.gotoRoute(SEGMENTS);
    await drill(page, 'il');
    await expect(page.locator('#main-content ul.zip-tiles')).toBeVisible();
    await expect(page.locator('#main-content .zip-tiles__status[role="status"]')).toHaveText(TILE_STATUS);
    await expect(zctaStage(page)).toHaveCount(0);
    await expect(page.locator('.map-legend__caption')).not.toContainText('ZCTAs');
    await expect(page.getByRole('button', { name: 'Zoom in' })).toHaveCount(0);
  });
});
