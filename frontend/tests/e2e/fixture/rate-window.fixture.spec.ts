/**
 * Lane rate-window (dataviz-08 / dataviz-06): the "Why now" surface on the
 * Analytics Executive tab, rendered against the synthetic 60-week series in
 * data/rateWindow.ts. Pins the rendered layer: two stacked panels on one
 * x-axis, the spread sentence for the fixture's numbers, the labelled spread
 * screen line, the mark colours against their legend, the axis geometry at
 * 1440 / 1280 / 390, the parallel request, the evidence chip's drawer
 * destination, the warming-up degraded state and the table alternative.
 */
import type { Page } from '@playwright/test';
import type { RateWindowResponse } from '../../../src/types';
import { analyticsFixtures } from './data/analytics';
import { RATE_WINDOW, RATE_WINDOW_EXPECTED, RATE_WINDOW_WEEK_COUNT } from './data/rateWindow';
import { json, WAREHOUSE_WARMING_UP } from './mockApi';
import { expect, test } from './test';

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface AxisGeometry {
  panel: Box;
  canvases: Box[];
  xTicks: Box[];
  yTicks: Box[];
  threshold: Box | null;
  current: Box | null;
}

/** Rendered boxes of the rate window's axis furniture; only displayed x-ticks count. */
async function axisGeometry(page: Page): Promise<AxisGeometry> {
  return page.getByTestId('rate-window').evaluate((panel) => {
    const box = (el: Element): Box => {
      const r = el.getBoundingClientRect();
      return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    };
    const shown = (el: Element) => getComputedStyle(el).display !== 'none';
    const one = (selector: string) => {
      const el = panel.querySelector(selector);
      return el ? box(el) : null;
    };
    return {
      panel: box(panel),
      canvases: [...panel.querySelectorAll('.rate-window__panel .analytics-chart__canvas')].map(box),
      xTicks: [...panel.querySelectorAll('.rate-window__panel--itm .analytics-chart__tick--x')].filter(shown).map(box),
      yTicks: [...panel.querySelectorAll('.rate-window__panel--itm .analytics-chart__tick--y')].map(box),
      threshold: one('[data-testid="rate-window-threshold"]'),
      current: one('[data-testid="rate-window-current"]'),
    };
  });
}

const intersects = (a: Box, b: Box) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
const inside = (inner: Box, outer: Box) =>
  inner.left >= outer.left && inner.right <= outer.right && inner.top >= outer.top && inner.bottom <= outer.bottom;
const fmt = (b: Box) => `[${b.left.toFixed(1)}..${b.right.toFixed(1)} x ${b.top.toFixed(1)}..${b.bottom.toFixed(1)}]`;

function expectAxisInsidePanel(geometry: AxisGeometry, label: string): void {
  expect(geometry.xTicks.length, `${label}: the shared axis shows month labels`).toBeGreaterThan(1);
  geometry.xTicks.forEach((tick, idx) => {
    expect(inside(tick, geometry.panel), `${label}: x-tick ${idx} ${fmt(tick)} inside panel ${fmt(geometry.panel)}`).toBe(true);
    geometry.yTicks.forEach((yTick, yIdx) => {
      expect(intersects(tick, yTick), `${label}: x-tick ${idx} ${fmt(tick)} clear of y-tick ${yIdx} ${fmt(yTick)}`).toBe(false);
    });
    geometry.xTicks.slice(idx + 1).forEach((other, offset) => {
      expect(intersects(tick, other), `${label}: x-ticks ${idx} and ${idx + offset + 1} do not overlap`).toBe(false);
    });
  });
}

test.describe('analytics executive: why-now rate window', () => {
  test('draws two stacked panels on one axis, states the spread in words and labels the spread screen', async ({ app, page }) => {
    await app.setTheme('dark');
    await app.gotoRoute('/analytics');

    const panel = page.getByTestId('rate-window');
    await expect(panel).toBeVisible();
    await expect(panel.locator('.rate-window__panel svg')).toHaveCount(2);
    await expect(panel.getByTestId('rate-window-rates').locator('svg polyline.rate-window__market')).toHaveCount(1);
    await expect(panel.getByTestId('rate-window-rates').locator('svg polygon.rate-window__band')).toHaveCount(1);
    await expect(panel.getByTestId('rate-window-itm').locator('svg polyline.rate-window__itm-line')).toHaveCount(1);

    // The sentence is computed from the fixture's own numbers (7.10% median vs 6.22% print).
    const summary = panel.getByTestId('rate-window-summary');
    await expect(summary).toContainText(RATE_WINDOW_EXPECTED.spreadSentence);
    await expect(summary).toContainText(RATE_WINDOW_EXPECTED.itmSentence);

    // dataviz-06: the lender's threshold is a labelled reference line, so the chart says something.
    await expect(panel.getByTestId('rate-window-threshold')).toHaveText(RATE_WINDOW_EXPECTED.thresholdLabel);
    await expect(panel.locator('line.rate-window__threshold')).toHaveCount(1);

    // The current print is annotated, and the disclosure names today's book.
    await expect(panel.getByTestId('rate-window-current')).toContainText(RATE_WINDOW_EXPECTED.currentPrint);
    await expect(panel.getByTestId('rate-window-asof')).toContainText("Today's book");

    // One shared x-axis: only the bottom panel carries week ticks.
    await expect(panel.locator('.rate-window__panel--rates .analytics-chart__x-ticks')).toHaveCount(0);
    await expect(panel.locator('.rate-window__panel--itm .analytics-chart__tick--x').first()).toBeVisible();
  });

  test('the book median is drawn in the colour its legend swatch names, not the market accent', async ({ app, page }) => {
    await app.setTheme('dark');
    await app.gotoRoute('/analytics');

    const panel = page.getByTestId('rate-window');
    await expect(panel.locator('polyline.rate-window__median')).toHaveCount(1);
    const colours = await panel.evaluate((root) => {
      const style = (selector: string) => {
        const el = root.querySelector(selector);
        if (!el) throw new Error(`missing ${selector}`);
        return getComputedStyle(el);
      };
      return {
        median: style('polyline.rate-window__median').stroke,
        medianSwatch: style('.rate-window__swatch--median').backgroundColor,
        market: style('polyline.rate-window__market').stroke,
        marketSwatch: style('.rate-window__legend-item:first-child .rate-window__swatch').backgroundColor,
      };
    });
    expect(colours.median, 'median stroke matches its legend swatch').toBe(colours.medianSwatch);
    expect(colours.median, 'median and market are distinct encodings').not.toBe(colours.market);
    expect(colours.market, 'market stroke matches its legend swatch').toBe(colours.marketSwatch);
  });

  for (const width of [1440, 1280]) {
    test(`at ${width}px the shared axis keeps every month label inside the panel and off the y-ticks`, async ({ app, page }) => {
      await page.setViewportSize({ width, height: 900 });
      await app.setTheme('dark');
      await app.gotoRoute('/analytics');
      await expect(page.getByTestId('rate-window').locator('.rate-window__panel svg')).toHaveCount(2);

      const geometry = await axisGeometry(page);
      expectAxisInsidePanel(geometry, `${width}px`);
      // One x-axis: both plot canvases start and end at the same x.
      expect(geometry.canvases).toHaveLength(2);
      const [rates, itm] = geometry.canvases;
      expect(Math.abs(rates.left - itm.left), 'plot canvases share a left edge').toBeLessThan(0.5);
      expect(Math.abs(rates.right - itm.right), 'plot canvases share a right edge').toBeLessThan(0.5);
    });
  }

  test('at phone width the axis thins to readable months and the reference label stays clear of the print', async ({ app, page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await app.setTheme('light');
    await app.gotoRoute('/analytics');
    await expect(page.getByTestId('rate-window').locator('.rate-window__panel svg')).toHaveCount(2);

    const geometry = await axisGeometry(page);
    expectAxisInsidePanel(geometry, '390px');
    expect(geometry.threshold, 'threshold label rendered').not.toBeNull();
    expect(geometry.current, 'current print rendered').not.toBeNull();
    if (!geometry.threshold || !geometry.current) return;
    expect(inside(geometry.threshold, geometry.panel), `threshold label ${fmt(geometry.threshold)} inside panel`).toBe(true);
    expect(
      intersects(geometry.threshold, geometry.current),
      `threshold label ${fmt(geometry.threshold)} clear of the current print ${fmt(geometry.current)}`,
    ).toBe(false);
    // The detail is hidden on a narrow plot, never lost: the text and the image description keep it.
    await expect(page.getByTestId('rate-window-threshold')).toHaveText(RATE_WINDOW_EXPECTED.thresholdLabel);
  });

  test('the rate window is requested beside the executive read and says the tab filters do not apply', async ({ app, page, mockApi }) => {
    const executive = analyticsFixtures.find((entry) => entry.method === 'GET' && entry.pattern === '/api/analytics/executive');
    if (!executive) throw new Error('executive fixture missing');
    let markRateWindowRequested: () => void = () => undefined;
    const rateWindowRequested = new Promise<boolean>((resolve) => {
      markRateWindowRequested = () => resolve(true);
    });
    let requestedBeforeExecutiveAnswered = false;
    mockApi.register('GET', '/api/analytics/rate-window', () => {
      markRateWindowRequested();
      return json<RateWindowResponse>(RATE_WINDOW);
    });
    // Hold the executive answer until the rate window has been asked for; the
    // bound only ends the failing case (a waterfall never asks first).
    mockApi.register('GET', '/api/analytics/executive', async (request) => {
      requestedBeforeExecutiveAnswered = await Promise.race([
        rateWindowRequested,
        new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 5_000)),
      ]);
      return executive.handler(request);
    });

    await app.setTheme('light');
    await app.gotoRoute('/analytics?states=CA');
    await expect(page.getByTestId('rate-window')).toBeVisible();
    expect(requestedBeforeExecutiveAnswered, 'rate window requested while the executive read was still open').toBe(true);
    await expect(page.getByTestId('rate-window-unfiltered')).toHaveText('All states: filters not applied');
  });

  test('the evidence chip opens the drawer on the rate-window destination citing both source tables', async ({ app, page }) => {
    await app.setTheme('light');
    await app.gotoRoute('/analytics');

    await page.getByTestId('rate-window').locator('.evidence-chip').click();

    const dialog = page.getByRole('dialog', { name: 'Rate window: market rate against the book' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('mip.silver.market_rates_weekly');
    await expect(dialog).toContainText('mip.gold.rate_window_weekly');
  });

  test('a warming-up warehouse renders the WarmingUpBlock and no chart', async ({ app, page }) => {
    await app.setTheme('dark');
    app.degrade('/api/analytics/rate-window', WAREHOUSE_WARMING_UP);
    await app.gotoRoute('/analytics');

    // The rest of the Executive tab still renders from its own payload.
    await expect(page.locator('#main-content')).toContainText(/89[.,]55/);
    const warming = page.getByTestId('warming-up-block');
    await expect(warming).toBeVisible();
    await expect(warming).toContainText('Why now: the market rate against the book');
    await expect(page.getByTestId('rate-window')).toHaveCount(0);
    await expect(page.locator('.rate-window__panel svg')).toHaveCount(0);
  });

  test('view as table lists the same weeks the charts draw', async ({ app, page }) => {
    await app.setTheme('light');
    await app.gotoRoute('/analytics');

    const panel = page.getByTestId('rate-window');
    await panel.getByRole('button', { name: 'View as table' }).click();

    const table = panel.getByTestId('rate-window-table');
    await expect(table).toBeVisible();
    await expect(panel.locator('.rate-window__panel svg')).toHaveCount(0);
    const rows = table.locator('tbody tr');
    await expect(rows).toHaveCount(RATE_WINDOW_WEEK_COUNT);
    await expect(rows.first()).toContainText(RATE_WINDOW_EXPECTED.firstWeek);
    await expect(rows.last()).toContainText(RATE_WINDOW_EXPECTED.lastWeek);
    await expect(rows.last()).toContainText('(current)');
    await expect(rows.last()).toContainText(RATE_WINDOW_EXPECTED.currentPrint);
    await expect(rows.last()).toContainText('1,956');

    await panel.getByRole('button', { name: 'View as chart' }).click();
    await expect(panel.locator('.rate-window__panel svg')).toHaveCount(2);
  });
});
