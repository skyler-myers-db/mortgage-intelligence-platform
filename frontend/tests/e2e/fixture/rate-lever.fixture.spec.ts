/**
 * Lane w3-rate-lever (audit wow-stage-1, runtime-06 map slice), proven in the
 * rendered page at 1440x900.
 *
 *  - Read discipline: no rate read on load or in the other colourings;
 *    exactly one after "Rate scenario" is picked; none on Segment
 *    Intelligence, where the button is aria-disabled with its reason.
 *  - Truth: at step 0 the legend total, the step-0 rows and the table total
 *    agree; the keyboard and the mouse move the legend, the exact headline
 *    and aria-valuetext through the fixture rows; the designated state
 *    changes class and paint in both themes.
 *  - Honesty: warming, failed and not-built reads draw no slider and no
 *    number and keep the borrower fill; the label is up while the read is
 *    delayed; ZIP tiles keep borrower colouring and the legend recounts the
 *    drilled state; the evidence chip names the gold table.
 *  - Runtime-06: a changed cohort keeps the previous fill, labelled
 *    "Updating…", and one state's ZIP tiles never paint under another.
 */
import type { Locator, Page } from '@playwright/test';
import type { StateRollupResponse, ZipRollupResponse } from '../../../src/types';
import type { FixtureTheme } from './app';
import { expectAxeClean } from './axe';
import {
  RATE_LEVER,
  RATE_LEVER_DESIGNATED,
  RATE_LEVER_NOT_BUILT,
  rateLeverAt,
  rateLeverTotals,
} from './data/rateLever';
import { SNAPSHOT_DATE, STATES, TOTALS } from './data/reference';
import { WAREHOUSE_WARMING_UP, type MockApi } from './mockApi';
import { expect, test } from './test';
import { expectNoAuditedReadSince, expectNoSurfaceOverflow } from './visual';

const RATE_PATH = '/api/geo/rate-sensitivity';
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const COUNT = new Intl.NumberFormat('en-US');

function rateReads(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.path === RATE_PATH).length;
}

const colouring = (page: Page, name: string): Locator =>
  page.getByRole('group', { name: 'Map coloring' }).getByRole('button', { name, exact: true });
const slider = (page: Page): Locator => page.getByRole('slider', { name: 'Par rate move' });
const legendTotal = (page: Page): Locator => page.locator('.map-legend__value');
const headline = (page: Page): Locator => page.locator('.rate-lever__sentence');
const designated = (page: Page): Locator => page.locator(`path.map-region[data-map-unit="${RATE_LEVER_DESIGNATED}"]`);
const scenarioLabel = (page: Page): Locator => page.locator('.map-legend__lever-note .chip', { hasText: 'Scenario, not a forecast' });

async function openHomeRate(page: Page, app: { gotoRoute: (path: string) => Promise<void> }): Promise<void> {
  await app.gotoRoute('/');
  await colouring(page, 'Rate scenario').click();
  await expect(slider(page)).toBeVisible();
}

/** The legend, headline and slider name for one step, from the fixture rows. */
const EXPECTED: Record<number, { headline: string; valueText: string }> = {
  0: {
    headline: "At today's 30-year par rate (6.30%), 12,840 borrowers clear this refresh's refi screen; 536 of them are contactable.",
    valueText: "Par rate 6.30%, today's rate: 12,840 borrowers in the money.",
  },
  [-50]: {
    headline:
      "If the 30-year par rate were 0.50 points lower (5.80%), 18,619 borrowers would clear this refresh's refi screen: 5,779 more than today; 778 of them are contactable.",
    valueText: 'Par rate 5.80%, down 50 basis points: 18,619 borrowers in the money.',
  },
  100: {
    headline:
      "If the 30-year par rate were 1.00 points higher (7.30%), 5,136 borrowers would clear this refresh's refi screen: 7,704 fewer than today; 215 of them are contactable.",
    valueText: 'Par rate 7.30%, up 100 basis points: 5,136 borrowers in the money.',
  },
  [-100]: {
    headline:
      "If the 30-year par rate were 1.00 points lower (5.30%), 25,680 borrowers would clear this refresh's refi screen: 12,840 more than today; 1,072 of them are contactable.",
    valueText: 'Par rate 5.30%, down 100 basis points: 25,680 borrowers in the money.',
  },
};

async function expectStep(page: Page, step: number): Promise<void> {
  await expect(slider(page)).toHaveValue(String(step));
  await expect(slider(page)).toHaveAttribute('aria-valuetext', EXPECTED[step].valueText);
  await expect(headline(page)).toHaveText(EXPECTED[step].headline);
  // The fill and the legend follow the deferred step: wait for them to land.
  await expect(page.locator('.map-wrap[data-scenario-pending]')).toHaveCount(0);
  await expect(legendTotal(page)).toHaveText(COUNT.format(rateLeverTotals(step).inTheMoney));
}

test.describe('Rate Lever on the geography hero', () => {
  test('(a) reads the grid only after Rate scenario is picked, exactly once', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/');
    expect(rateReads(mockApi), 'no rate read on Home load').toBe(0);
    await colouring(page, 'Unattended leads').click();
    await expect(colouring(page, 'Unattended leads')).toHaveAttribute('aria-pressed', 'true');
    await colouring(page, 'Borrowers').click();
    await expect(colouring(page, 'Borrowers')).toHaveAttribute('aria-pressed', 'true');
    // Pointing at the button warms the control chunk only: no API call.
    await colouring(page, 'Rate scenario').hover();
    await app.settle();
    expect(rateReads(mockApi), 'no rate read in Borrowers or Unattended mode, nor on hover').toBe(0);

    await colouring(page, 'Rate scenario').click();
    await expect(slider(page)).toBeVisible();
    await app.settle();
    expect(rateReads(mockApi)).toBe(1);
    await expect(colouring(page, 'Rate scenario')).toHaveAttribute('aria-pressed', 'true');
  });

  test('(b) the scenario label is up at once, while the read is still delayed', async ({ app, page, mockApi }) => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi.register('GET', RATE_PATH, async () => {
      await held;
      return { body: RATE_LEVER };
    });
    await app.gotoRoute('/');
    await colouring(page, 'Rate scenario').click();
    await expect(scenarioLabel(page)).toBeVisible();
    await expect(page.locator('.map-legend__lever-note')).toContainText("The Lead Queue and campaigns use today's par rate.");
    await expect(slider(page)).toHaveCount(0);
    await expect(legendTotal(page)).toHaveText('—');
    release();
    await expect(slider(page)).toBeVisible();
    await expect(scenarioLabel(page)).toBeVisible();
  });

  test('(c) at step 0 the legend total, the step-0 rows and the table total agree', async ({ app, page }) => {
    await openHomeRate(page, app);
    const stepZero = RATE_LEVER.states.reduce((sum, state) => sum + state.in_the_money[rateLeverAt(0)], 0);
    expect(stepZero).toBe(TOTALS.inTheMoney);
    await expectStep(page, 0);
    await page.getByRole('button', { name: 'View as table' }).click();
    const table = page.getByTestId('map-table');
    await expect(table.getByRole('columnheader', { name: 'In the money at 6.30%' })).toBeVisible();
    await expect(page.getByTestId('map-table-extra-total')).toHaveText(COUNT.format(stepZero));
    await expect(legendTotal(page)).toHaveText(COUNT.format(stepZero));
  });

  test('(d) the keyboard walks the grid: legend, headline and value text follow the rows', async ({ app, page }) => {
    await openHomeRate(page, app);
    await expect(designated(page)).toHaveAttribute('data-map-class', '3');
    await slider(page).focus();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    await expectStep(page, -50);
    await expect(designated(page)).toHaveAttribute('data-map-class', '4');
    await page.keyboard.press('End');
    await expectStep(page, 100);
    await expect(designated(page)).toHaveAttribute('data-map-class', '2');
    await page.keyboard.press('Home');
    await expectStep(page, -100);
    await expect(designated(page)).toHaveAttribute('data-map-class', '4');
    await page.getByRole('button', { name: 'Reset to today' }).click();
    await expectStep(page, 0);
    await expect(designated(page)).toHaveAttribute('data-map-class', '3');
  });

  test('(e) a click on the track lands on the step under the pointer', async ({ app, page }) => {
    await openHomeRate(page, app);
    await slider(page).scrollIntoViewIfNeeded();
    const box = await slider(page).boundingBox();
    if (!box) throw new Error('slider has no box');
    // A quarter of the way along the track is -50 bps (-100 .. +100).
    await page.mouse.click(box.x + box.width * 0.25, box.y + box.height / 2);
    await expectStep(page, -50);
  });

  for (const theme of THEMES) {
    test(`(f) the designated state repaints between steps (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await openHomeRate(page, app);
      const fill = () => designated(page).evaluate((el) => getComputedStyle(el).fill);
      const today = await fill();
      await slider(page).focus();
      await page.keyboard.press('Home');
      await expectStep(page, -100);
      await expect.poll(fill).not.toBe(today);
      const swatch = await page.locator('.map-legend__bar .lvl-4').evaluate((el) => getComputedStyle(el).backgroundColor);
      const painted = await designated(page).evaluate((el, colour) => {
        const probe = document.createElement('div');
        probe.style.backgroundColor = getComputedStyle(el).fill;
        document.body.append(probe);
        const resolved = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return resolved === colour;
      }, swatch);
      expect(painted, 'the class-4 swatch is the paint at -100 bps').toBe(true);
    });
  }

  test('(g) a warming read shows why, draws no slider or number, then recovers', async ({ app, page }) => {
    await app.gotoRoute('/');
    const borrowerClass = await designated(page).getAttribute('data-map-class');
    const recover = app.degrade(RATE_PATH, WAREHOUSE_WARMING_UP);
    await colouring(page, 'Rate scenario').click();
    const lever = page.locator('.map-legend__lever');
    await expect(lever).toContainText('Rate scenarios: Warehouse warming up. Retrying automatically');
    await expect(scenarioLabel(page)).toBeVisible();
    await expect(slider(page)).toHaveCount(0);
    await expect(legendTotal(page)).toHaveText('—');
    await expect(designated(page)).toHaveAttribute('data-map-class', borrowerClass ?? '');
    recover();
    await expect(slider(page)).toBeVisible({ timeout: 20_000 });
    await expect(legendTotal(page)).toHaveText(COUNT.format(TOTALS.inTheMoney));
  });

  test('(h) a failed read says it could not load, with Retry, and no numbers', async ({ app, page }) => {
    await app.gotoRoute('/');
    const borrowerClass = await designated(page).getAttribute('data-map-class');
    const restore = app.degrade(RATE_PATH, { status: 500, body: { detail: 'fixture failure' } });
    await colouring(page, 'Rate scenario').click();
    const lever = page.locator('.map-legend__lever');
    await expect(lever).toContainText('Rate scenarios could not load.');
    await expect(lever.getByRole('button', { name: 'Retry' })).toBeVisible();
    await expect(slider(page)).toHaveCount(0);
    await expect(legendTotal(page)).toHaveText('—');
    await expect(designated(page)).toHaveAttribute('data-map-class', borrowerClass ?? '');
    restore();
    await lever.getByRole('button', { name: 'Retry' }).click();
    await expect(slider(page)).toBeVisible();
  });

  test('(i) an unbuilt grid says the refresh builds it', async ({ app, page, mockApi }) => {
    mockApi.register('GET', RATE_PATH, () => ({ body: RATE_LEVER_NOT_BUILT }));
    await app.gotoRoute('/');
    await colouring(page, 'Rate scenario').click();
    await expect(page.locator('.map-legend__lever')).toContainText(
      'Rate scenarios are not built yet: the gold refresh job builds them (deploy or Admin > Data operations).',
    );
    await expect(slider(page)).toHaveCount(0);
    await expect(legendTotal(page)).toHaveText('—');
  });

  test('(j) drilled in rate mode, ZIP tiles keep borrower classes and the legend recounts the state', async ({ app, page }) => {
    await app.gotoRoute('/?geo_state=IL');
    const tiles = page.locator('button.zip-tile[data-map-unit]');
    await expect(tiles.first()).toBeVisible();
    const borrowerClasses = await tiles.evaluateAll((els) => els.map((el) => el.getAttribute('data-map-class')));
    await colouring(page, 'Rate scenario').click();
    await expect(slider(page)).toBeVisible();
    const illinois = RATE_LEVER.states.find((state) => state.state === 'IL');
    await expect(legendTotal(page)).toHaveText(COUNT.format(illinois?.in_the_money[rateLeverAt(0)] ?? -1));
    await expect(page.locator('.map-legend__caption')).toContainText('ZIP tiles show borrowers; scenarios are computed per state.');
    expect(await tiles.evaluateAll((els) => els.map((el) => el.getAttribute('data-map-class')))).toEqual(borrowerClasses);
    await slider(page).focus();
    await page.keyboard.press('Home');
    await expect(legendTotal(page)).toHaveText(COUNT.format(illinois?.in_the_money[rateLeverAt(-100)] ?? -1));
    expect(await tiles.evaluateAll((els) => els.map((el) => el.getAttribute('data-map-class')))).toEqual(borrowerClasses);
  });

  test('(k) Segment Intelligence: aria-disabled with its reason, a click does nothing, no read', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/segment-intelligence');
    const rate = colouring(page, 'Rate scenario');
    await expect(rate).toHaveAttribute('aria-disabled', 'true');
    await expect(rate).toHaveAccessibleDescription(
      'Rate scenarios cover the whole book; clear segment and portfolio filters to use them.',
    );
    // aria-disabled keeps it focusable and clickable: the click must do nothing.
    await rate.click({ force: true });
    await app.settle();
    await expect(rate).toHaveAttribute('aria-pressed', 'false');
    await expect(colouring(page, 'Borrowers')).toHaveAttribute('aria-pressed', 'true');
    await expect(slider(page)).toHaveCount(0);
    expect(rateReads(mockApi)).toBe(0);
  });

  test('(l) the headline evidence chip opens the drawer on the gold grid; no audited read', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/');
    const naturalLoadEnd = mockApi.calls.length;
    await colouring(page, 'Rate scenario').click();
    await expect(slider(page)).toBeVisible();
    await page.locator('.rate-lever__headline').getByRole('button', { name: /Rate scenario grid/ }).click();
    const drawer = page.getByRole('dialog').filter({ hasText: 'Rate scenario: in the money if par moved' });
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText('mip.gold.rate_sensitivity_rollup');
    expectNoAuditedReadSince(mockApi, naturalLoadEnd, 'rate lever');
  });

  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 1280, height: 720 },
  ]) {
    test(`(n) the control sits inside the map and the stage keeps its height at ${viewport.width}x${viewport.height}`, async ({ app, page }) => {
      await page.setViewportSize(viewport);
      await openHomeRate(page, app);
      const wrap = await page.locator('.map-wrap').boundingBox();
      const lever = await page.locator('.rate-lever').boundingBox();
      const stage = await page.locator('.map-levels').boundingBox();
      if (!wrap || !lever || !stage) throw new Error('map, lever or stage has no box');
      expect(lever.x).toBeGreaterThanOrEqual(wrap.x);
      expect(lever.y).toBeGreaterThanOrEqual(wrap.y);
      expect(lever.x + lever.width).toBeLessThanOrEqual(wrap.x + wrap.width + 0.5);
      expect(lever.y + lever.height).toBeLessThanOrEqual(wrap.y + wrap.height + 0.5);
      expect(stage.height).toBeGreaterThanOrEqual(280);
      for (const theme of THEMES) {
        await expectNoSurfaceOverflow(page, { route: 'home', state: 'default', theme });
      }
    });
  }

  for (const theme of THEMES) {
    test(`(o) the map is axe-clean in rate mode with the slider focused (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await openHomeRate(page, app);
      await slider(page).focus();
      await expectAxeClean(page, { key: { route: 'home', state: 'rate-lever' }, theme, known: {}, include: '.map-wrap' });
    });
  }

  test('(p) with the slider focused, j, k, a and ? trigger no global shortcut', async ({ app, page }) => {
    await openHomeRate(page, app);
    await slider(page).focus();
    const url = page.url();
    for (const key of ['j', 'k', 'a', '?']) await page.keyboard.press(key);
    await expect(slider(page)).toBeFocused();
    await expect(slider(page)).toHaveValue('0');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(page.url()).toBe(url);
  });
});

test.describe('Runtime-06: the map keeps the previous cohort, visibly', () => {
  test('(m) a segment toggle keeps the previous fill under an Updating pill, then repaints', async ({ app, page, mockApi }) => {
    let release: () => void = () => undefined;
    let hold = false;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    // Two cohorts that paint Texas differently; the second answer is held.
    mockApi.register('GET', '/api/geo/state-rollups', async ({ query }) => {
      const narrowed = (query.get('segment_codes') ?? '') !== '';
      if (hold && narrowed) await held;
      return {
        body: {
          rollups: STATES.map((state) => ({
            state: state.code,
            addressable: narrowed && state.code === 'TX' ? 1 : state.addressable,
            contactable: state.contactable,
            in_the_money: state.inTheMoney,
            top_tier_opportunities: state.topTier,
            avg_score: state.avgScore,
            zip_unassigned_count: 0,
            top_segment_code: state.topSegment,
          })),
          snapshot_date: SNAPSHOT_DATE,
        } satisfies StateRollupResponse,
      };
    });
    await app.gotoRoute('/segment-intelligence');
    const texas = page.locator('path.map-region[data-map-unit="tx"]');
    await expect(texas).toHaveAttribute('data-map-class', /[1-4]/);
    const before = await texas.getAttribute('data-map-class');
    hold = true;
    // Select a segment card: a new cohort key for the state rollups.
    const card = page.locator('.seg-card:not(.is-selected)').first();
    await card.click();
    await expect(page.locator('.map-levels.is-updating')).toBeVisible();
    await expect(page.locator('.map-levels .stable-refresh-status')).toHaveText('Updating…');
    await expect(page.locator('.map-legend.is-updating')).toBeVisible();
    await expect(page.locator('.map-status')).toHaveText('Updating state borrower rollups.');
    await expect(page.locator('path.map-region.is-loading')).toHaveCount(0);
    await expect(texas).toHaveAttribute('data-map-class', before ?? '');
    release();
    await expect(page.locator('.map-levels.is-updating')).toHaveCount(0);
    await expect(page.locator('.stable-refresh-status')).toHaveCount(0);
    await expect(texas).toHaveAttribute('data-map-class', '1');
  });

  test('(m) drilling one state then another never paints the first state tiles under the second', async ({ app, page, mockApi }) => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi.register('GET', '/api/geo/zip-rollups', async ({ query }) => {
      const state = query.get('state') ?? '';
      if (state === 'TX') await held;
      return {
        body: {
          state,
          fips_5: null,
          rollups: [
            {
              zip: state === 'TX' ? '77002' : '60611',
              state,
              county_fips_5: null,
              addressable_borrowers: 100,
              avg_opportunity_score: 80,
              top_segment_code: 'itm',
              sample_borrower_id: null,
            },
          ],
          snapshot_date: SNAPSHOT_DATE,
        } satisfies ZipRollupResponse,
      };
    });
    await app.gotoRoute('/segment-intelligence?geo_state=IL');
    await expect(page.locator('button.zip-tile[data-map-unit="60611"]')).toBeVisible();
    await page.locator('.map-crumbs__trail').getByRole('button', { name: 'US' }).click();
    await page.locator('path.map-region[data-map-unit="tx"]').click();
    await expect(page.locator('.map-wrap')).toContainText('ZIPs in Texas');
    await expect(page.locator('button.zip-tile[data-map-unit="60611"]')).toHaveCount(0);
    await expect(page.locator('.map-levels.is-updating')).toHaveCount(0);
    release();
    await expect(page.locator('button.zip-tile[data-map-unit="77002"]')).toBeVisible();
    await expect(page.locator('button.zip-tile[data-map-unit="60611"]')).toHaveCount(0);
  });
});

test.describe('css-hygiene review #2: forced colours keep the map edges', () => {
  test('a hovered state keeps a Highlight edge that the contrast-modes rule used to override', async ({ app, page }) => {
    await page.emulateMedia({ forcedColors: 'active' });
    await app.gotoRoute('/');
    const texas = page.locator('path.map-region[data-map-unit="tx"]');
    const stroke = (path: Locator) =>
      path.evaluate((el) => ({ colour: getComputedStyle(el).stroke, width: getComputedStyle(el).strokeWidth }));
    const resting = await stroke(texas);
    await texas.hover();
    await expect.poll(async () => (await stroke(texas)).colour).not.toBe(resting.colour);
    expect((await stroke(texas)).width).toBe('2px');
    // An unhovered neighbour keeps the CanvasText edge.
    expect((await stroke(page.locator('path.map-region[data-map-unit="il"]'))).colour).toBe(resting.colour);
  });
});
