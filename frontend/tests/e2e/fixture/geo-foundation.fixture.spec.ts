/**
 * Lane w5-geo-foundation (D-dataviz-geo-d1 / -d2; audits dataviz-10,
 * runtime-07, css-03, wow-stage-1), proven in the rendered page at 1440x900.
 *
 *  - Keyboard: Escape backs out of a ZIP level onto the state it left; the
 *    keys, Tab and the pointer activate populated states only, while every
 *    drawn state keeps its hover card; the state stage says how many states
 *    the keys skip.
 *  - Whole book: the state table lists the states the map cannot draw (PR,
 *    VI) and the ones it skips, the legend says what its total includes, and
 *    the footer equals the legend in all three colourings.
 *  - URL: `?map_mode=rate&rate_step=-50` opens that scenario after exactly one
 *    rate read on Home and reads nothing on Segments (cohort filter); an
 *    unattended link reads only the overlay; a scrub writes its step with a
 *    replace.
 *  - Lever slot: the legend reserves the tallest state of the lazy control,
 *    so neither the chunk nor a status line moves the stage.
 *  - Top layer: the hover card paints over the floating Genie panel.
 */
import type { Page } from '@playwright/test';
import type { StateRollupResponse } from '../../../src/types';
import type { RateSensitivityResponse } from '../../../src/types/rateScenario';
import type { GeoAssignmentOverlayResponse } from '../../../src/lib/apiTypes';
import type { FixtureTheme } from './app';
import { expectAxeClean } from './axe';
import { RATE_LEVER, RATE_LEVER_NOT_BUILT } from './data/rateLever';
import { WAREHOUSE_WARMING_UP } from './mockApi';
import { expect, test } from './test';

const RATE_PATH = '/api/geo/rate-sensitivity';
const OVERLAY_PATH = '/api/geo/assignment-overlay';
const CONTROL_CHUNK = /\/assets\/RateScenarioControl-[^/]+\.(js|css)$/;
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
/** Segment Intelligence with every filter cleared (Contactability Any): rate mode is available there. */
const SEGMENTS_CLEARED = '/segment-intelligence?marketing_eligibility=Any';

const colouring = (page: Page, name: string) =>
  page.getByRole('group', { name: 'Map coloring' }).getByRole('button', { name, exact: true });
const slider = (page: Page) => page.getByRole('slider', { name: 'Par rate move' });
const reads = (calls: ReadonlyArray<{ path: string }>, path: string) => calls.filter((call) => call.path === path).length;

/**
 * Linux Chromium draws Geist Mono about 6% wider (docs: LINUX_TEXT_EMULATION
 * pattern); off Linux the lever's mono text is widened past that so the
 * measured heights are the CI runner's or taller.
 */
const LINUX_TEXT_EMULATION = '.map-legend__lever .rate-lever__output, .map-legend__lever .evidence-chip { letter-spacing: 0.5px; }';

/** Open or close the Console rail (its open state persists across navigations). */
async function setConsole(page: Page, open: boolean): Promise<void> {
  const body = page.getByRole('complementary', { name: 'Workspace console' }).locator('.tweaks__body');
  if ((await body.isVisible().catch(() => false)) !== open) {
    await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();
  }
  if (open) await expect(body).toBeVisible();
  else await expect(body).toBeHidden();
}

type LeverState = 'warming' | 'failed' | 'not-built' | 'slider';
const LEVER_STATES: readonly LeverState[] = ['warming', 'failed', 'not-built', 'slider'];

test.describe('measured lever slot (D-dataviz-geo-d2 2(iv))', () => {
  test('the slot reserves the tallest state of the control, on Home and Segments, both themes, Console open and closed', async ({ app, mockApi, page }) => {
    test.setTimeout(300_000);
    const measured: Array<{ route: string; theme: FixtureTheme; console: boolean; state: LeverState; control: number; slot: number }> = [];
    for (const route of ['/', SEGMENTS_CLEARED]) {
      for (const theme of THEMES) {
        for (const consoleOpen of [false, true]) {
          for (const state of LEVER_STATES) {
            await app.setTheme(theme);
            let restore: () => void = () => undefined;
            if (state === 'warming') restore = app.degrade(RATE_PATH, WAREHOUSE_WARMING_UP);
            if (state === 'failed') restore = app.degrade(RATE_PATH, { status: 500, body: { detail: 'fixture failure' } });
            mockApi.register('GET', RATE_PATH, () => ({ body: state === 'not-built' ? RATE_LEVER_NOT_BUILT : RATE_LEVER }));
            await app.gotoRoute(route);
            await setConsole(page, consoleOpen);
            if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
            await colouring(page, 'Rate scenario').click();
            const lever = page.locator('.map-legend__lever');
            if (state === 'warming') await expect(lever).toContainText('Warehouse warming up', { timeout: 20_000 });
            if (state === 'failed') await expect(lever).toContainText('Rate scenarios could not load.');
            if (state === 'not-built') await expect(lever).toContainText('Rate scenarios are not built yet');
            if (state === 'slider') {
              await expect(slider(page)).toBeVisible();
              // The longest sentence the fixture grid writes.
              await slider(page).focus();
              await page.keyboard.press('Home');
              await expect(page.locator('.map-wrap[data-scenario-pending]')).toHaveCount(0);
            }
            // The slot's RESERVED block size (its min-block-size), against the
            // natural height of what the control draws in this state.
            const box = await page.locator('.map-legend__lever-slot').evaluate((slot) => ({
              slot: parseFloat(getComputedStyle(slot).minBlockSize),
              control: slot.firstElementChild?.getBoundingClientRect().height ?? 0,
            }));
            measured.push({ route, theme, console: consoleOpen, state, ...box });
            restore();
          }
        }
      }
    }
    for (const row of measured) console.log(`[lever-slot] ${JSON.stringify(row)}`);
    const max = Math.max(...measured.map((row) => row.control));
    const slot = measured[0].slot;
    expect(new Set(measured.map((row) => row.slot)).size, 'one reserved size in every state').toBe(1);
    const sp2 = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sp-2')));
    for (const row of measured) expect(row.slot, JSON.stringify(row)).toBeGreaterThanOrEqual(row.control);
    expect(slot - max, `slot ${slot} against the tallest control ${max}`).toBeLessThan(sp2);
  });

  test('a held control chunk does not move the stage when it arrives', async ({ app, page }) => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(CONTROL_CHUNK, async (route) => {
      await held;
      await route.continue();
    });
    await app.gotoRoute('/');
    await colouring(page, 'Rate scenario').click();
    await expect(page.locator('.map-legend__lever-note')).toBeVisible();
    const before = await page.locator('.map-levels').boundingBox();
    release();
    await expect(slider(page)).toBeVisible();
    await expect(page.locator('.map-wrap[data-scenario-pending]')).toHaveCount(0);
    const after = await page.locator('.map-levels').boundingBox();
    expect(after).toEqual(before);
  });
});
