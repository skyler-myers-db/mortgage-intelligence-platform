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
import { SNAPSHOT_DATE, STATES, TOTALS } from './data/reference';
import { WAREHOUSE_WARMING_UP, json, type MockApi } from './mockApi';
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

/**
 * Each state is its own load: drop the tab's persisted aggregate snapshot
 * (lib/queryPersist, 'mip.queryCache.v1') so a value a previous state served
 * cannot be restored into this one.
 */
async function forgetPersistedAggregates(page: Page): Promise<void> {
  await page.evaluate(() => {
    try {
      window.sessionStorage.removeItem('mip.queryCache.v1');
    } catch {
      // about:blank before the first navigation has no storage.
    }
  });
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
            await forgetPersistedAggregates(page);
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

const statePath = (page: Page, id: string) => page.locator(`path.map-region[data-map-unit="${id}"]`);
const focusedState = (page: Page) => page.locator('path[data-map-unit]:focus');
const COUNT = new Intl.NumberFormat('en-US');
/** The fixture book draws 51 shapes and has borrowers in 8 states. */
const SKIPPED = 51 - STATES.length;

test.describe('Escape backs out one level (dataviz-10)', () => {
  test('Escape hides the card, then leaves the ZIP level and focuses the state it left; Back returns to the drill', async ({ app, page }) => {
    await app.gotoRoute('/');
    await statePath(page, 'az').focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/geo_state=AZ/);
    const firstZip = page.getByRole('list', { name: 'ZIPs in Arizona' }).getByRole('button').first();
    await expect(firstZip).toBeFocused();
    await expect(page.locator('.map-tip')).toHaveCount(1);
    await page.keyboard.press('Escape');
    await expect(page.locator('.map-tip')).toHaveCount(0);
    await expect(page).toHaveURL(/geo_state=AZ/);
    await page.keyboard.press('Escape');
    await expect(page).not.toHaveURL(/geo_state=/);
    await expect(focusedState(page)).toHaveAttribute('data-map-unit', 'az');
    await expect(page.locator('.map-crumbs__trail button').first()).not.toBeFocused();
    // A history push, like the US crumb.
    await page.goBack();
    await expect(page).toHaveURL(/geo_state=AZ/);
  });
});

test.describe('populated-only roving and activation (dataviz-10, WCAG 2.1.1)', () => {
  test('Tab and the arrows visit populated states, a click on an empty one does nothing, and its card still opens', async ({ app, page }) => {
    await app.gotoRoute('/');
    await page.getByRole('button', { name: 'View as table' }).focus();
    await page.keyboard.press('Tab');
    // Alabama has no borrowers in the fixture book: Arizona is the first stop.
    await expect(focusedState(page)).toHaveAttribute('data-map-unit', 'az');
    await page.keyboard.press('ArrowLeft');
    await expect(focusedState(page)).toHaveAttribute('data-map-unit', 'wa');
    await page.keyboard.press('Home');
    await expect(focusedState(page)).toHaveAttribute('data-map-unit', 'az');

    const alabama = statePath(page, 'al');
    await expect(alabama).toHaveAttribute('role', 'img');
    await expect(alabama).toHaveAttribute('tabindex', '-1');
    await expect(alabama).not.toHaveAttribute('data-populated');
    await expect(alabama).toHaveCSS('cursor', 'default');
    await alabama.hover();
    await expect(page.locator('.map-tip .map-tip__name')).toHaveText('Alabama');
    await alabama.click();
    await expect(page).not.toHaveURL(/geo_state=/);
    await expect(page.locator('ul.zip-tiles')).toHaveCount(0);
  });

  test('the state stage says how many states the keys skip', async ({ app, page }) => {
    await app.gotoRoute('/');
    const stage = page.locator('svg.map-svg-stage');
    const describedBy = await stage.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    await expect(page.locator(`[id="${describedBy}"]`)).toHaveText(
      `${SKIPPED} states have no borrowers in this selection and are skipped; the table view lists them.`,
    );
    await expect(stage).toHaveAccessibleDescription(/states have no borrowers in this selection/);
  });
});

/** The fixture book plus Puerto Rico and the US Virgin Islands, which the map cannot draw. */
const OFF_MAP = [
  { code: 'PR', addressable: 640, contactable: 21, inTheMoney: 90, avgScore: 70, unattended: 7 },
  { code: 'VI', addressable: 85, contactable: 3, inTheMoney: 12, avgScore: 66, unattended: 2 },
] as const;

function registerOffMapBook(mockApi: MockApi): void {
  mockApi.register('GET', '/api/geo/state-rollups', () =>
    json<StateRollupResponse>({
      rollups: [...STATES, ...OFF_MAP.map((row) => ({ ...row, topTier: 1, topSegment: 'itm' }))].map((state) => ({
        state: state.code,
        addressable: state.addressable,
        contactable: state.contactable,
        in_the_money: state.inTheMoney,
        top_tier_opportunities: state.topTier,
        avg_score: state.avgScore,
        zip_unassigned_count: 0,
        top_segment_code: state.topSegment,
      })),
      snapshot_date: SNAPSHOT_DATE,
    }),
  );
  mockApi.register('GET', RATE_PATH, () =>
    json<RateSensitivityResponse>({
      ...RATE_LEVER,
      states: [
        ...RATE_LEVER.states,
        ...OFF_MAP.map((row) => ({
          state: row.code,
          addressable: row.addressable,
          rate_movable: row.addressable,
          in_the_money: RATE_LEVER.steps_bps.map(() => row.inTheMoney),
        })),
      ],
    }),
  );
  mockApi.register('GET', OVERLAY_PATH, () => {
    const rows = [
      ...STATES.map((state) => ({
        code: state.code,
        leads: state.contactable,
        unattended: state.contactable - Math.round(state.contactable * 0.4),
      })),
      ...OFF_MAP.map((row) => ({ code: row.code, leads: row.contactable, unattended: row.unattended })),
    ];
    const units = rows.map((row) => ({
      unit_id: row.code,
      lead_count: row.leads,
      assigned_count: row.leads - row.unattended,
      unattended_count: row.unattended,
      covering_officer_count: 1,
      covering_officers: ['Loan Officer A'],
    }));
    return json<GeoAssignmentOverlayResponse>({
      level: 'state',
      state: null,
      county_fips: null,
      units,
      total_leads: units.reduce((sum, unit) => sum + unit.lead_count, 0),
      total_assigned: units.reduce((sum, unit) => sum + unit.assigned_count, 0),
      total_unattended: units.reduce((sum, unit) => sum + unit.unattended_count, 0),
      lead_definition: 'Marketing-eligible borrowers in mip.gold.borrower_360 without an active assignment.',
    });
  });
}

test.describe('the whole book in the table and the legend (wow-stage-1)', () => {
  test('PR and VI are listed, the legend says so, and the footer equals the legend in all three colourings', async ({ app, mockApi, page }) => {
    registerOffMapBook(mockApi);
    await app.gotoRoute('/');
    const legendValue = page.locator('.map-legend__value');
    const caption = page.locator('.map-legend__caption');
    const offMap = OFF_MAP.reduce((sum, row) => sum + row.addressable, 0);
    await expect(legendValue).toHaveText(COUNT.format(TOTALS.addressable + offMap));
    await expect(caption).toContainText(`Includes ${COUNT.format(offMap)} in PR, VI (not drawn on the map)`);

    await page.getByRole('button', { name: 'View as table' }).click();
    const table = page.getByTestId('map-table').locator('table');
    await expect(table.locator('tr.map-table__group th')).toHaveText([
      'Not drawn on the map (2)',
      `No borrowers in this selection (${SKIPPED})`,
    ]);
    await expect(table.locator('tbody').nth(1).locator('tr[data-map-row] th')).toHaveText(['PR', 'VI']);
    await expect(table.locator('tbody').nth(1).locator('button')).toHaveCount(0);
    await expect(table.locator('tfoot th')).toHaveText(`Total (${STATES.length + 2})`);
    await expect(page.getByTestId('map-table-total')).toHaveText(await legendValue.innerText());
    // The fixture footprint is the eight states with borrowers, so Alabama says why.
    const alabama = table.locator('tr[data-map-row="al"]');
    await expect(alabama.locator('.map-table__note')).toHaveText('Outside Cotality evaluation scope');
    await expect(alabama.locator('td.num').first()).toHaveText('—');

    await colouring(page, 'Rate scenario').click();
    await expect(slider(page)).toBeVisible();
    await expect(page.locator('.map-wrap[data-scenario-pending]')).toHaveCount(0);
    await expect(page.getByTestId('map-table-extra-total')).toHaveText(await legendValue.innerText());
    const offMapItm = OFF_MAP.reduce((sum, row) => sum + row.inTheMoney, 0);
    await expect(caption).toContainText(`Includes ${COUNT.format(offMapItm)} in PR, VI (not drawn on the map)`);

    await colouring(page, 'Unattended leads').click();
    await expect(caption).toContainText('Includes 9 in PR, VI (not drawn on the map)');
    await expect(legendValue).not.toHaveText('—');
    await expect(page.getByTestId('map-table-extra-total')).toHaveText(await legendValue.innerText());
  });
});

test.describe('map mode and step in the URL (wow-stage-1)', () => {
  test('a rate link on Home opens its step after exactly one rate read', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/?map_mode=rate&rate_step=-50');
    await expect(slider(page)).toHaveValue('-50');
    await expect(page.locator('.map-legend__lever-note .chip', { hasText: 'Scenario, not a forecast' })).toBeVisible();
    await expect(page.locator('.map-legend__lever-note')).toContainText("The Lead Queue and campaigns use today's par rate.");
    await expect(colouring(page, 'Rate scenario')).toHaveAttribute('aria-pressed', 'true');
    expect(reads(mockApi.calls, RATE_PATH)).toBe(1);
  });

  test('the same link on Segment Intelligence reads nothing (its default cohort filter)', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/segment-intelligence?map_mode=rate&rate_step=-50');
    await expect(colouring(page, 'Borrowers')).toHaveAttribute('aria-pressed', 'true');
    await expect(colouring(page, 'Rate scenario')).toHaveAttribute('aria-disabled', 'true');
    await expect(page.locator('.map-legend__lever')).toHaveCount(0);
    expect(reads(mockApi.calls, RATE_PATH)).toBe(0);
  });

  test('an unattended link reads only the overlay', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/?map_mode=unattended');
    await expect(colouring(page, 'Unattended leads')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.map-legend__overlay-facts')).toBeVisible();
    expect(reads(mockApi.calls, OVERLAY_PATH)).toBe(1);
    expect(reads(mockApi.calls, RATE_PATH)).toBe(0);
  });

  test('a scrub writes its step on pointerup, replacing the history entry', async ({ app, page }) => {
    await app.gotoRoute('/');
    await colouring(page, 'Rate scenario').click();
    await expect(slider(page)).toBeVisible();
    await expect(page).toHaveURL(/map_mode=rate/);
    const before = await page.evaluate(() => window.history.length);
    await slider(page).scrollIntoViewIfNeeded();
    const box = await slider(page).boundingBox();
    if (!box) throw new Error('slider has no box');
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.3, box.y + box.height / 2, { steps: 5 });
    await page.mouse.move(box.x + 2, box.y + box.height / 2, { steps: 5 });
    await page.mouse.up();
    await expect(page).toHaveURL(/rate_step=-100/);
    expect(await page.evaluate(() => window.history.length)).toBe(before);
  });
});

test.describe('the hover card in the top layer (css-03 / runtime-07)', () => {
  test('the card paints over the floating Genie panel', async ({ app, page }) => {
    await app.gotoRoute('/');
    const genie = await app.openGenie();
    const panel = await genie.boundingBox();
    if (!panel) throw new Error('Genie panel has no box');
    const point = { x: Math.round(panel.x + panel.width / 2), y: Math.round(panel.y + panel.height / 2) };
    // Control: with no card, the point is the Genie panel's.
    expect(await page.evaluate(({ x, y }) => Boolean(document.elementFromPoint(x, y)?.closest('.genie')), point)).toBe(true);
    // A pointer over Texas reported at a point inside the panel's box: the
    // card is placed there (its bottom edge sits just above the pointer).
    await statePath(page, 'tx').evaluate((path, at) => {
      for (const type of ['pointerover', 'pointermove']) {
        path.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: at.x, clientY: at.y }));
      }
    }, { x: point.x, y: point.y + 60 });
    const tip = page.locator('.map-tip');
    await expect(tip).toHaveCount(1);
    const hit = await tip.evaluate((element) => {
      const card = element as HTMLElement;
      const box = card.getBoundingClientRect();
      const previous = card.style.pointerEvents;
      card.style.pointerEvents = 'auto';
      const centre = () => document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      const overGenie = centre();
      // Only the top layer paints above the largest z-index a page can set:
      // the z-indexed fallback (z-map-tip) would lose to this sheet.
      const sheet = document.createElement('div');
      sheet.style.cssText = 'position: fixed; inset: 0; z-index: 2147483647;';
      document.body.append(sheet);
      const overSheet = centre();
      sheet.remove();
      card.style.pointerEvents = previous;
      return {
        popover: card.getAttribute('popover'),
        open: card.matches(':popover-open'),
        overGenie: Boolean(overGenie && card.contains(overGenie)),
        overAnyZIndex: Boolean(overSheet && card.contains(overSheet)),
      };
    });
    expect(hit).toEqual({ popover: 'manual', open: true, overGenie: true, overAnyZIndex: true });
  });

  for (const theme of THEMES) {
    test(`the map and its card are axe-clean (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await statePath(page, 'az').focus();
      await expect(page.locator('.map-tip .map-tip__name')).toHaveText('Arizona');
      await expectAxeClean(page, { key: { route: 'geo-foundation', state: 'state-focused' }, theme, known: {}, include: '.map-wrap' });
      await expectAxeClean(page, { key: { route: 'geo-foundation', state: 'card' }, theme, known: {}, include: '.map-tip' });
    });
  }
});
