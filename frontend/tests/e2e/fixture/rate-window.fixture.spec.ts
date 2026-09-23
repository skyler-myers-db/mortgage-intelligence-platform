/**
 * Lane rate-window (dataviz-08 / dataviz-06): the "Why now" surface on the
 * Analytics Executive tab, rendered against the synthetic 60-week series in
 * data/rateWindow.ts. Pins the rendered layer: two stacked panels on one
 * x-axis, the spread sentence for the fixture's numbers, the labelled spread
 * screen line, the mark colours against their legend, the spread screen's
 * contrast in both themes, the axis geometry at
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

type Rgb = [number, number, number];

interface ScreenInk {
  surface: Rgb;
  bandOverSurface: Rgb;
  label: Rgb;
  line: Rgb;
  swatch: Rgb;
  market: Rgb;
  median: Rgb;
  itm: Rgb;
  bandSwatch: Rgb;
}

/**
 * The painted colours around the spread screen, resolved to 8-bit sRGB by a
 * canvas so computed color-mix()/oklab() values compare like hex ones. The
 * surface is the first painted background at or above the panel (it must be
 * opaque); every mark is composited over it, and the book band is too,
 * because the reference label can sit on the band.
 */
async function screenInk(page: Page): Promise<ScreenInk> {
  return page.getByTestId('rate-window').evaluate((root) => {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) throw new Error('2d canvas unavailable');
    const paint = (layers: string[]): [number, number, number, number] => {
      ctx.clearRect(0, 0, 1, 1);
      for (const css of layers) {
        ctx.fillStyle = 'rgb(1, 2, 3)';
        const sentinel = ctx.fillStyle;
        ctx.fillStyle = css;
        if (ctx.fillStyle === sentinel) throw new Error(`canvas could not parse the colour ${css}`);
        ctx.fillRect(0, 0, 1, 1);
      }
      const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a];
    };
    let surface: string | null = null;
    for (let el: Element | null = root; el && surface === null; el = el.parentElement) {
      const background = getComputedStyle(el).backgroundColor;
      const alpha = paint([background])[3];
      if (alpha === 0) continue;
      if (alpha < 255) throw new Error(`the first painted background behind the rate window is translucent: ${background}`);
      surface = background;
    }
    if (surface === null) throw new Error('no painted background behind the rate window');
    const base = surface;
    const style = (selector: string) => {
      const el = root.querySelector(selector);
      if (!el) throw new Error(`missing ${selector}`);
      return getComputedStyle(el);
    };
    const over = (...layers: string[]): [number, number, number] => {
      const [r, g, b] = paint([base, ...layers]);
      return [r, g, b];
    };
    return {
      surface: over(),
      bandOverSurface: over(style('polygon.rate-window__band').fill),
      label: over(style('[data-testid="rate-window-threshold"]').color),
      line: over(style('line.rate-window__threshold').stroke),
      swatch: over(style('.rate-window__swatch--threshold').backgroundColor),
      market: over(style('polyline.rate-window__market').stroke),
      median: over(style('polyline.rate-window__median').stroke),
      itm: over(style('polyline.rate-window__itm-line').stroke),
      bandSwatch: over(style('.rate-window__swatch--band').backgroundColor),
    };
  });
}

/** WCAG 2.x contrast ratio between two opaque sRGB colours. */
function contrastRatio(a: Rgb, b: Rgb): number {
  const luminance = (rgb: Rgb) => {
    const [r, g, bl] = rgb.map((channel) => {
      const s = channel / 255;
      return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Largest per-channel difference: a coarse "reads as a different colour" floor. */
const channelGap = (a: Rgb, b: Rgb) => Math.max(...a.map((channel, idx) => Math.abs(channel - b[idx])));
const fmtRgb = (c: Rgb) => `rgb(${c.join(', ')})`;

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

  // dataviz-06: the labelled threshold is the deliverable, so it must be
  // readable in both themes. --status-warning-ink alone measured 1.44:1 on the
  // light theme's white panel.
  for (const theme of ['light', 'dark'] as const) {
    test(`${theme} theme: the spread screen's label and line clear WCAG contrast and stay distinct from the market and median`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/analytics');
      await expect(page.getByTestId('rate-window-threshold')).toBeVisible();

      const ink = await screenInk(page);
      const labelOnPanel = contrastRatio(ink.label, ink.surface);
      const labelOnBand = contrastRatio(ink.label, ink.bandOverSurface);
      const lineOnPanel = contrastRatio(ink.line, ink.surface);
      expect(labelOnPanel, `label ${fmtRgb(ink.label)} on panel ${fmtRgb(ink.surface)}: ${labelOnPanel.toFixed(2)}:1 (WCAG 1.4.3)`).toBeGreaterThanOrEqual(4.5);
      expect(labelOnBand, `label ${fmtRgb(ink.label)} over the book band ${fmtRgb(ink.bandOverSurface)}: ${labelOnBand.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
      expect(lineOnPanel, `line ${fmtRgb(ink.line)} on panel ${fmtRgb(ink.surface)}: ${lineOnPanel.toFixed(2)}:1 (WCAG 1.4.11)`).toBeGreaterThanOrEqual(3);

      // One encoding: the label, the line and the legend swatch are one colour.
      expect(ink.label, 'label and line share the spread-screen ink').toEqual(ink.line);
      expect(ink.swatch, 'legend swatch names the line colour').toEqual(ink.line);
      expect(channelGap(ink.line, ink.market), `spread screen ${fmtRgb(ink.line)} vs market ${fmtRgb(ink.market)}`).toBeGreaterThanOrEqual(48);
      expect(channelGap(ink.line, ink.median), `spread screen ${fmtRgb(ink.line)} vs median ${fmtRgb(ink.median)}`).toBeGreaterThanOrEqual(48);
    });

    // The default `bright` accent overrides the light theme's navy --accent
    // with #66C5FF, which drew the market line at 1.91:1 on white and the
    // book band (#66C5FF at 14%) at about 1.07:1, i.e. invisible.
    test(`${theme} theme: the market and in-the-money lines clear 3:1 and the book band reads against the panel`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/analytics');
      await expect(page.getByTestId('rate-window').locator('.rate-window__panel svg')).toHaveCount(2);

      const ink = await screenInk(page);
      const market = contrastRatio(ink.market, ink.surface);
      const itm = contrastRatio(ink.itm, ink.surface);
      const band = contrastRatio(ink.bandOverSurface, ink.surface);
      expect(market, `market line ${fmtRgb(ink.market)} on panel ${fmtRgb(ink.surface)}: ${market.toFixed(2)}:1 (WCAG 1.4.11)`).toBeGreaterThanOrEqual(3);
      expect(itm, `in-the-money line ${fmtRgb(ink.itm)} on panel ${fmtRgb(ink.surface)}: ${itm.toFixed(2)}:1 (WCAG 1.4.11)`).toBeGreaterThanOrEqual(3);
      // The band is a range fill under the lines, not a sole carrier of
      // meaning (the table and the p25/p75 columns carry it), so it needs to
      // read as a tint, not 3:1: the dark theme's measures about 1.33:1.
      expect(band, `book band ${fmtRgb(ink.bandOverSurface)} on panel ${fmtRgb(ink.surface)}: ${band.toFixed(2)}:1`).toBeGreaterThanOrEqual(1.25);
      expect(ink.bandSwatch, 'band legend swatch names the band fill').toEqual(ink.bandOverSurface);
    });
  }

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
