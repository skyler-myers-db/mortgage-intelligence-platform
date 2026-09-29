/**
 * Lane w4-charts (2026-09-21 audit stack-06, visual-08, dataviz-06/07/10/v2,
 * a11y-10, responsive-v3, css-06, motion-08, a11y-07): the Analytics chart
 * foundation, rendered against the fixture harness at 1440x900.
 *
 * Pins the rendered layer: 1-2-5 axes on every numeric tick label; the two
 * distributions as histograms whose refi-screen rule and 75+ label come from
 * governed values; the keyboard cursor and the table twin.
 */
import type { Locator, Page } from '@playwright/test';
import type { EquitySpreadPointsResponse, ExecutiveAnalyticsResponse } from '../../../src/types';
import {
  ECONOMICS_THRESHOLDS,
  SCORE_BUCKETS,
  SPREAD_BUCKETS,
  THRESHOLDS_NOT_BUILT,
  THRESHOLDS_NOT_UNIFORM,
  analyticsFixtures,
  economicsBody,
} from './data/analytics';
import { BORROWERS } from './data/borrowers';
import { TOTALS } from './data/reference';
import { expectAxeClean } from './axe';
import { json } from './mockApi';
import { asComputedRgb, contrastRatio, parseRgb, renderedColors } from './renderedColor';
import { expect, test } from './test';
import { expectNoAuditedReadSince, expectNoSurfaceOverflow, markNaturalLoad } from './visual';

/** The chart's plot, by the section title that names it. */
function plotNamed(page: Page, title: string): Locator {
  return page.getByRole('group', { name: title, exact: true });
}

/** The chart figure inside the section a title heads (it stays found while the table shows). */
function figureOf(page: Page, title: string): Locator {
  return page
    .locator('section.surface', { has: page.getByRole('heading', { name: title, exact: true }) })
    .locator('figure.chart-frame');
}

const count = (value: number) => value.toLocaleString('en-US');

/** Centre x of the x-tick label reading `label` inside a figure. */
async function tickCentre(figure: Locator, label: string): Promise<number> {
  const box = await figure.locator('.analytics-chart__tick--x', { hasText: new RegExp(`^${label}$`) }).boundingBox();
  if (!box) throw new Error(`no x tick "${label}"`);
  return box.x + box.width / 2;
}

const CHART_TABS = [
  { name: 'executive', route: 'analytics-executive', path: '/analytics' },
  { name: 'economics', route: 'analytics-economics', path: '/analytics?view=economics' },
  { name: 'signals', route: 'analytics-signals', path: '/analytics?view=signals' },
] as const;

/**
 * Linux Chromium draws Geist Mono ~6% wider than macOS (the segments-cards
 * LINUX_TEXT_EMULATION pattern): off Linux the tick labels are widened past
 * that, so a label that only fits at macOS widths fails here too.
 */
const LINUX_TEXT_EMULATION = '.analytics-chart__tick { letter-spacing: 0.5px; }';

/** Tick labels whose box leaves their `.analytics-chart__plot` box (0.5px subpixel tolerance). */
async function ticksOutsidePlot(page: Page): Promise<string[]> {
  return page.locator('#main-content').evaluate((main) =>
    [...main.querySelectorAll('.analytics-chart__plot')].flatMap((plot) => {
      const box = plot.getBoundingClientRect();
      const heading = plot.closest('section')?.querySelector('h2')?.textContent?.trim() ?? 'plot';
      return [...plot.querySelectorAll('.analytics-chart__tick')].flatMap((tick) => {
        const r = tick.getBoundingClientRect();
        if (r.left >= box.left - 0.5 && r.right <= box.right + 0.5) return [];
        return [`${heading} "${tick.textContent}" spans [${r.left.toFixed(1)}, ${r.right.toFixed(1)}] outside [${box.left.toFixed(1)}, ${box.right.toFixed(1)}]`];
      });
    }),
  );
}

interface AxisReading {
  chart: string;
  axis: 'x' | 'y';
  labels: string[];
}

/** Every tick label of every `.analytics-chart__plot` in the page, grouped by chart and axis. */
async function axisReadings(page: Page): Promise<AxisReading[]> {
  return page.locator('#main-content').evaluate((main) =>
    [...main.querySelectorAll('.analytics-chart__plot')].flatMap((plot, index) => {
      const heading = plot.closest('section')?.querySelector('h2')?.textContent?.trim() ?? `plot ${index}`;
      const labels = (axis: 'x' | 'y') =>
        [...plot.querySelectorAll(`.analytics-chart__tick--${axis}`)].map((tick) => tick.textContent?.trim() ?? '');
      return [
        { chart: heading, axis: 'x' as const, labels: labels('x') },
        { chart: heading, axis: 'y' as const, labels: labels('y') },
      ];
    }),
  );
}

const COMPACT_UNITS: Record<string, number> = { K: 1e3, M: 1e6, B: 1e9 };

/** "27.5K" -> 27500, "-100" -> -100, a date or a rate -> null. */
function numericLabel(label: string): number | null {
  const match = /^(-?[\d,]+(?:\.\d+)?)([KMB])?$/.exec(label);
  if (!match) return null;
  return Number(match[1].replace(/,/g, '')) * (match[2] ? COMPACT_UNITS[match[2]] : 1);
}

/** Problems with one axis: its numeric labels must sit on one 1, 2 or 5 x 10^k step. */
function oneTwoFiveProblems(reading: AxisReading): string[] {
  const values = reading.labels.map(numericLabel).filter((value): value is number => value !== null).sort((a, b) => a - b);
  if (values.length < 2) return [];
  const where = `${reading.chart} ${reading.axis}-axis [${reading.labels.join(', ')}]`;
  const step = values[1] - values[0];
  const problems: string[] = [];
  const mantissa = Math.round((step / 10 ** Math.floor(Math.log10(step))) * 1e6) / 1e6;
  if (![1, 2, 5].includes(mantissa)) problems.push(`${where}: step ${step} is not a 1-2-5 multiple`);
  values.forEach((value, idx) => {
    if (Math.abs(value / step - Math.round(value / step)) > 1e-6) problems.push(`${where}: ${value} is off the ${step} grid`);
    if (idx > 0 && Math.abs(value - values[idx - 1] - step) > 1e-6 * Math.max(1, step)) {
      problems.push(`${where}: uneven gap before ${value}`);
    }
  });
  return problems;
}

test.describe('analytics charts: 1-2-5 axes', () => {
  for (const theme of ['dark', 'light'] as const) {
    for (const tab of CHART_TABS) {
      test(`${tab.name} (${theme}): every numeric axis label is a 1-2-5 multiple inside its plot, and no surface overflows`, async ({ app, page }) => {
        await app.setTheme(theme);
        await app.gotoRoute(tab.path);
        await expect(page.locator('#main-content .analytics-chart__plot').first()).toBeVisible();
        if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
        const readings = await axisReadings(page);
        const numeric = readings.filter((reading) => reading.labels.some((label) => numericLabel(label) !== null));
        expect(numeric.length, `${tab.name} draws numeric axes`).toBeGreaterThan(0);
        expect(numeric.flatMap(oneTwoFiveProblems)).toEqual([]);
        // Every label, the date axis included: the last date ends at the
        // plot's right edge instead of centring on it (stack-06).
        expect(await ticksOutsidePlot(page)).toEqual([]);
        await expectNoSurfaceOverflow(page, { route: tab.route, state: 'default', theme });
      });
    }
  }
});

/**
 * Each y label's vertical centre minus its gridline's, top to bottom (px).
 * Both lists sort by position, so the Nth label pairs with the Nth gridline.
 * Only horizontal gridlines count: the scatter also draws vertical ones.
 */
async function yLabelOffsets(chart: Locator): Promise<number[]> {
  return chart.evaluate((root) => {
    const centre = (el: Element) => {
      const box = el.getBoundingClientRect();
      return box.top + box.height / 2;
    };
    const labels = [...root.querySelectorAll('.analytics-chart__tick--y')].map(centre).sort((a, b) => a - b);
    const grid = [...root.querySelectorAll('line.analytics-chart__grid')]
      .filter((line) => line.getAttribute('y1') === line.getAttribute('y2'))
      .map(centre)
      .sort((a, b) => a - b);
    if (labels.length !== grid.length) throw new Error(`${labels.length} y labels for ${grid.length} gridlines`);
    return labels.map((label, idx) => Math.round((label - grid[idx]) * 10) / 10);
  });
}

/**
 * Every value axis on the Analytics tabs and the tab it sits on: the kit's
 * three charts, the equity scatter and both rate-window panels share the
 * y-tick column.
 */
const VALUE_AXES: ReadonlyArray<{ title: string; path: string; locate: (page: Page) => Locator }> = [
  { title: 'Opportunity Score Distribution', path: '/analytics', locate: (page) => figureOf(page, 'Opportunity Score Distribution') },
  { title: 'Rate window: 30-year fixed', path: '/analytics', locate: (page) => page.getByTestId('rate-window-rates') },
  { title: 'Rate window: liens in the money', path: '/analytics', locate: (page) => page.getByTestId('rate-window-itm') },
  { title: 'Rate Spread Distribution', path: '/analytics?view=economics', locate: (page) => figureOf(page, 'Rate Spread Distribution') },
  { title: 'Equity vs Rate Spread', path: '/analytics?view=economics', locate: (page) => page.locator('.analytics-scatter-wrap').first() },
  { title: 'Evidence Events Per Day', path: '/analytics?view=signals', locate: (page) => figureOf(page, 'Evidence Events Per Day') },
];

test.describe('analytics charts: y labels on their gridlines (dataviz-07)', () => {
  for (const theme of ['dark', 'light'] as const) {
    test(`${theme}: every y label of the kit charts, the equity scatter and the rate window sits within 1px of its gridline`, async ({ app, page }) => {
      await app.setTheme(theme);
      let path = '';
      for (const axis of VALUE_AXES) {
        if (axis.path !== path) await app.gotoRoute(axis.path);
        path = axis.path;
        const chart = axis.locate(page);
        await expect(chart.locator('line.analytics-chart__grid').first()).toBeAttached();
        const offsets = await yLabelOffsets(chart);
        expect(offsets.length, `${axis.title} draws a y axis`).toBeGreaterThanOrEqual(3);
        expect(offsets.filter((offset) => Math.abs(offset) > 1), `${axis.title} label - gridline offsets (px): ${offsets.join(', ')}`).toEqual([]);
      }
    });
  }
});

test.describe('analytics charts: evidence per day', () => {
  /** The fixture's seven days, summed across signals as buildDailyEvidenceTotals does. */
  const DAYS = ['Jul 8', 'Jul 9', 'Jul 10', 'Jul 11', 'Jul 12', 'Jul 13', 'Jul 14'];
  const dayTotal = (index: number) => 1840 + index * 120 + 1260 + index * 80 + 640 + index * 30;

  test('signals: each date sits at its day offset, the first and last labels edge-anchored', async ({ app, page }) => {
    await app.gotoRoute('/analytics?view=signals');
    const figure = figureOf(page, 'Evidence Events Per Day');
    const ticks = figure.locator('.analytics-chart__tick--x');
    // categoricalTickIndexes(7) labels every other day, 0, 2, 4 and 6.
    const labelled = [0, 2, 4, 6];
    await expect(ticks).toHaveText(labelled.map((index) => DAYS[index]));
    const positions = await ticks.evaluateAll((spans) => spans.map((span) => (span as HTMLElement).style.getPropertyValue('--tick-pos')));
    expect(positions.map((pos) => Number.parseFloat(pos).toFixed(2))).toEqual(labelled.map((index) => ((index / 6) * 100).toFixed(2)));
    await expect(ticks.first()).toHaveClass(/analytics-chart__tick--edge-start/);
    await expect(ticks.last()).toHaveClass(/analytics-chart__tick--edge-end/);
    const plot = await figure.locator('.analytics-chart__plot').boundingBox();
    const last = await ticks.last().boundingBox();
    if (!plot || !last) throw new Error('evidence chart not painted');
    expect(last.x + last.width).toBeLessThanOrEqual(plot.x + plot.width + 0.5);
  });

  test('signals: Tab reaches the plot, the tip shows on focus, the arrows announce each day', async ({ app, page }) => {
    await app.gotoRoute('/analytics?view=signals');
    const figure = figureOf(page, 'Evidence Events Per Day');
    const plot = plotNamed(page, 'Evidence Events Per Day');
    await figure.getByRole('button', { name: 'View as table' }).focus();
    await page.keyboard.press('Tab');
    await expect(plot).toBeFocused();
    await expect(figure.locator('.analytics-chart__tip')).toBeVisible();
    await expect(figure.locator('.analytics-chart__tip-x')).toHaveText(DAYS[0]);
    const live = figure.locator('[aria-live="polite"]');
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');
    await expect(live).toHaveText(`${DAYS[2]}: ${count(dayTotal(2))} events`);
    await page.keyboard.press('End');
    await expect(live).toHaveText(`${DAYS[6]}: ${count(dayTotal(6))} events`);
    await expect(figure.locator('figcaption')).toContainText(`the busiest day was Jul 14, 2026 with ${count(dayTotal(6))}`);
  });

  test('signals: View as table lists the plotted days', async ({ app, page }) => {
    await app.gotoRoute('/analytics?view=signals');
    const figure = figureOf(page, 'Evidence Events Per Day');
    await figure.locator('button.chart-frame__toggle').click();
    const cells = await figure.locator('table.analytics-table tbody tr').evaluateAll((trs) => trs.map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent?.trim())));
    expect(cells).toEqual(DAYS.map((day, index) => [`${day}, 2026`, count(dayTotal(index))]));
  });
});

test.describe('analytics charts: governed histograms', () => {
  test('the fixture score bins reconcile with the governed totals', () => {
    const sum = (rows: ReadonlyArray<readonly [number, number]>) => rows.reduce((total, [, n]) => total + n, 0);
    expect(sum(SCORE_BUCKETS)).toBe(TOTALS.addressable);
    expect(sum(SCORE_BUCKETS.filter(([start]) => start >= 75))).toBe(TOTALS.highOpportunity);
    expect(sum(SPREAD_BUCKETS)).toBe(TOTALS.addressable);
  });

  test('executive: the score histogram draws bins, and its 75+ rule carries the governed count', async ({ app, page }) => {
    await app.gotoRoute('/analytics');
    const figure = figureOf(page, 'Opportunity Score Distribution');
    await expect(figure.locator('rect.chart-hist__bar')).toHaveCount(SCORE_BUCKETS.length);
    await expect(figure.locator('polyline')).toHaveCount(0);
    await expect(figure.locator('.chart-hist__rule-label')).toHaveText(`75+ · ${count(TOTALS.highOpportunity)} borrowers`);
    const past = await figure.locator('rect.chart-hist__bar').evaluateAll((bars) => bars.map((bar) => bar.classList.contains('chart-hist__bar--past')));
    expect(past).toEqual(SCORE_BUCKETS.map(([start]) => start >= 75));
    await expect(figure.locator('figcaption')).toContainText(`${count(TOTALS.addressable)} borrowers scored`);
  });

  test('executive: when the bins at 75+ disagree with the totals, the label still prints the governed count', async ({ app, mockApi, page }) => {
    const base = analyticsFixtures.find((entry) => entry.method === 'GET' && entry.pattern === '/api/analytics/executive');
    if (!base) throw new Error('executive fixture missing');
    mockApi.register<ExecutiveAnalyticsResponse>('GET', '/api/analytics/executive', async (request) => {
      const reply = await base.handler(request);
      const body = reply.body as ExecutiveAnalyticsResponse;
      return json<ExecutiveAnalyticsResponse>({
        ...body,
        score_distribution: body.score_distribution.map((row) => (row.score_bucket === 80 ? { ...row, borrower_count: row.borrower_count + 5_000 } : row)),
      });
    });
    await app.gotoRoute('/analytics');
    const figure = figureOf(page, 'Opportunity Score Distribution');
    await expect(figure.locator('.chart-hist__rule-label')).toHaveText(`75+ · ${count(TOTALS.highOpportunity)} borrowers`);
    await expect(figure).not.toContainText(count(TOTALS.highOpportunity + 5_000));
  });

  test('executive: Tab reaches the score plot, the tip shows on focus and the arrows announce each bin', async ({ app, page }) => {
    await app.gotoRoute('/analytics');
    const figure = figureOf(page, 'Opportunity Score Distribution');
    const plot = plotNamed(page, 'Opportunity Score Distribution');
    await figure.getByRole('button', { name: 'View as table' }).focus();
    await page.keyboard.press('Tab');
    await expect(plot).toBeFocused();
    await expect(plot).toHaveAttribute('aria-describedby', /.+/);
    await expect(figure.locator('.analytics-chart__tip')).toBeVisible();
    const live = figure.locator('[aria-live="polite"]');
    for (let i = 0; i < 3; i += 1) await page.keyboard.press('ArrowRight');
    const [fourthStart, fourthCount] = SCORE_BUCKETS[3];
    await expect(live).toHaveText(`${fourthStart}–${fourthStart + 4}: ${count(fourthCount)} borrowers`);
    await expect(figure.locator('.analytics-chart__tip-x')).toHaveText(`${fourthStart}–${fourthStart + 4}`);
    await page.keyboard.press('End');
    const [lastStart, lastCount] = SCORE_BUCKETS[SCORE_BUCKETS.length - 1];
    await expect(live).toHaveText(`${lastStart}–${lastStart + 4}: ${count(lastCount)} borrowers`);
    await page.keyboard.press('Home');
    await expect(live).toHaveText(`${SCORE_BUCKETS[0][0]}–${SCORE_BUCKETS[0][0] + 4}: ${count(SCORE_BUCKETS[0][1])} borrowers`);
  });

  test('executive: View as table lists exactly the plotted bins, and the toggle names the other view', async ({ app, page }) => {
    await app.gotoRoute('/analytics');
    const figure = figureOf(page, 'Opportunity Score Distribution');
    await expect(figure.locator('rect.chart-hist__bar')).toHaveCount(SCORE_BUCKETS.length);
    const toggle = figure.locator('button.chart-frame__toggle');
    await expect(toggle).toHaveText('View as table');
    await expect(toggle).not.toHaveAttribute('aria-pressed', /.*/);
    await toggle.click();
    await expect(toggle).toHaveText('View as chart');
    await expect(toggle).not.toHaveAttribute('aria-pressed', /.*/);
    const rows = figure.locator('table.analytics-table tbody tr');
    await expect(rows).toHaveCount(SCORE_BUCKETS.length);
    const cells = await rows.evaluateAll((trs) => trs.map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent?.trim())));
    expect(cells).toEqual(SCORE_BUCKETS.map(([start, n]) => [`${start}–${start + 4}`, count(n), start >= 75 ? 'Yes' : 'No']));
    await toggle.click();
    await expect(figure.locator('rect.chart-hist__bar')).toHaveCount(SCORE_BUCKETS.length);
  });

  test('economics: the refi-screen rule sits at the payload threshold and partitions by lower edge', async ({ app, mockApi, page }) => {
    mockApi.register('GET', '/api/analytics/economics', () =>
      json(economicsBody({ ...ECONOMICS_THRESHOLDS, min_spread_bps: 100, min_equity_pct: 25 })),
    );
    await app.gotoRoute('/analytics?view=economics');
    const figure = figureOf(page, 'Rate Spread Distribution');
    const rule = figure.locator('line.chart-hist__rule');
    await expect(rule).toHaveCount(1);
    const ruleBox = await rule.boundingBox();
    if (!ruleBox) throw new Error('rule not painted');
    expect(Math.abs(ruleBox.x + ruleBox.width / 2 - (await tickCentre(figure, '100')))).toBeLessThanOrEqual(1);
    await expect(figure.locator('.chart-hist__rule-label')).toHaveText('Refi screen ≥ 100 bps');
    const past = await figure.locator('rect.chart-hist__bar').evaluateAll((bars) => bars.map((bar) => bar.classList.contains('chart-hist__bar--past')));
    expect(past).toEqual(SPREAD_BUCKETS.map(([start]) => start >= 100));
    const pastCount = SPREAD_BUCKETS.filter(([start]) => start >= 100).reduce((total, [, n]) => total + n, 0);
    await expect(figure.locator('figcaption')).toContainText(`${count(pastCount)} (`);
    await expect(figure.locator('figcaption')).toContainText('at or past the 100 bps refi screen');
  });

  for (const [name, thresholds, notice] of [
    ['not built', THRESHOLDS_NOT_BUILT, 'The refi-screen threshold is not built in this refresh.'],
    ['not uniform', THRESHOLDS_NOT_UNIFORM, 'No single refi-screen threshold applies to this refresh.'],
  ] as const) {
    test(`economics: a ${name} screen draws no rule and says why`, async ({ app, mockApi, page }) => {
      mockApi.register('GET', '/api/analytics/economics', () => json(economicsBody(thresholds)));
      await app.gotoRoute('/analytics?view=economics');
      const figure = figureOf(page, 'Rate Spread Distribution');
      await expect(figure.locator('rect.chart-hist__bar')).toHaveCount(SPREAD_BUCKETS.length);
      await expect(figure.locator('.chart-hist__rule')).toHaveCount(0);
      await expect(figure.locator('.chart-frame__notice')).toHaveText(notice);
      const inked = await figure.locator('rect.chart-hist__bar').evaluateAll((bars) => bars.every((bar) => bar.classList.contains('chart-hist__bar--past')));
      expect(inked).toBe(true);
    });
  }
});

test.describe('analytics charts: forced colors (a11y-10 / responsive-v3 / css-06)', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ forcedColors: 'active' });
  });

  /** Computed fill, edge style and edge width of an element: the forced-colors cue. */
  const cue = (target: Locator) =>
    target.evaluate((el) => {
      const style = getComputedStyle(el);
      return `${style.backgroundColor} | ${style.borderTopStyle} | ${style.borderTopWidth}`;
    });

  test('economics: the scatter bands keep pairwise-distinct cues, apart from the chip edge', async ({ app, page }) => {
    await app.gotoRoute('/analytics?view=economics');
    const bin = (band: string) => page.locator(`#main-content .analytics-scatter__bin.score--${band}`).first();
    await expect(bin('high')).toBeVisible();
    const cues = [await cue(bin('high')), await cue(bin('med')), await cue(bin('low'))];
    expect(new Set(cues).size, `three distinct cues: ${cues.join(' / ')}`).toBe(3);
    expect(cues[0]).toBe(`${await asComputedRgb(page, 'CanvasText')} | none | 0px`);
    // The chip rule is scoped to .score: the high bin does not take its solid
    // focus-ring-width edge, and a real legend chip still does.
    const chip = (band: string) => page.locator(`#main-content .score.score--${band}.analytics-scatter-legend__band`);
    const ringWidth = await chip('high').evaluate((el) => getComputedStyle(el).getPropertyValue('--focus-ring-width').trim());
    expect(await chip('high').evaluate((el) => `${getComputedStyle(el).borderTopStyle} ${getComputedStyle(el).borderTopWidth}`)).toBe(`solid ${ringWidth}`);
    expect(await chip('med').evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe('dashed');
    expect(await bin('high').evaluate((el) => getComputedStyle(el).borderTopStyle)).not.toBe('solid');
    // A scatter mark with no forced rule of its own (the cluster list's
    // score dot, a probe here: the fixture overview opens no cluster) must
    // not pick up the chip's band edge either.
    const probe = await page.locator('#main-content .analytics-chart-panel--scatter').evaluate((panel) => {
      const dot = document.createElement('span');
      dot.className = 'analytics-scatter__cluster-score score--high';
      panel.appendChild(dot);
      const edge = getComputedStyle(dot).borderTopStyle;
      dot.remove();
      return edge;
    });
    expect(probe).toBe('none');
  });

  test('economics zoom: the borrower dots keep per-band cues, and a cluster marker edges its band at 2px', async ({ app, page }) => {
    await app.gotoRoute('/analytics?view=economics');
    const cells = page.locator('#main-content .analytics-scatter__bin');
    await expect(cells.first()).toBeVisible();
    await cells.first().click();
    const dots = page.locator('#main-content .analytics-scatter__dot--band');
    await expect(dots.first()).toBeAttached();
    const read = await page.locator('#main-content .analytics-chart-panel--scatter').evaluate((panel) => {
      const BANDS = ['high', 'med', 'low'] as const;
      const cueOf = (el: Element) => {
        const style = getComputedStyle(el);
        return `${style.backgroundColor} | ${style.borderTopStyle} | ${style.borderTopWidth}`;
      };
      // One probe per band (the zoom may not hold every band), read in the
      // panel that carries the forced band tokens, then removed.
      const probe = (tag: string, className: string) => {
        const el = document.createElement(tag);
        el.className = className;
        panel.appendChild(el);
        const style = getComputedStyle(el);
        const cue = { cue: cueOf(el), edge: `${style.borderTopStyle} ${style.borderTopWidth}` };
        el.remove();
        return cue;
      };
      // A system colour as rgb: the probe opts out, or forced colours would
      // repaint its `color` as CanvasText whatever it names.
      const system = (value: string) => {
        const el = document.createElement('span');
        el.style.forcedColorAdjust = 'none';
        el.style.color = value;
        panel.appendChild(el);
        const rgb = getComputedStyle(el).color;
        el.remove();
        return rgb;
      };
      return {
        canvas: system('Canvas'),
        canvasText: system('CanvasText'),
        dotProbes: Object.fromEntries(BANDS.map((band) => [band, probe('a', `analytics-scatter__dot analytics-scatter__dot--band score--${band}`).cue])),
        markerEdges: BANDS.map((band) => probe('button', `analytics-scatter__cluster-marker score--${band}`).edge),
        dots: [...panel.querySelectorAll('.analytics-scatter__dot--band')].map((el) => ({
          band: BANDS.find((band) => el.classList.contains(`score--${band}`)) ?? 'none',
          cue: cueOf(el),
        })),
      };
    });
    expect(read.canvas).not.toBe(read.canvasText);
    expect(read.dotProbes).toEqual({
      high: `${read.canvasText} | none | 0px`,
      med: `${read.canvas} | solid | 2px`,
      low: `${read.canvas} | dashed | 1px`,
    });
    // Every real zoom dot paints its own band's cue.
    expect(read.dots.length).toBeGreaterThan(0);
    for (const dot of read.dots) expect(dot.cue, `a ${dot.band} dot`).toBe(read.dotProbes[dot.band as 'high' | 'med' | 'low']);
    // Cluster markers stay forced (they carry their count) and edge the band.
    expect(read.markerEdges).toEqual(['solid 2px', 'dashed 2px', 'dotted 2px']);
  });

  test('lead queue: a confidence bar on and off differ in fill and in edge style', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const on = page.locator('#main-content .conf__bar.on').first();
    const off = page.locator('#main-content .conf__bar:not(.on)').first();
    await expect(on).toBeVisible();
    const [onFill, onStyle] = (await cue(on)).split(' | ');
    const [offFill, offStyle] = (await cue(off)).split(' | ');
    expect(onFill).not.toBe(offFill);
    expect([onStyle, offStyle]).toEqual(['solid', 'dashed']);
  });
});

test('analytics: the view tabs are content-sized, not a full-width tray (w3 motion-nav #131)', async ({ app, page }) => {
  await app.gotoRoute('/analytics');
  const tablist = page.getByRole('tablist', { name: 'Analytics views' });
  // Against the parent's CONTENT box: a stretched tray fills it exactly, so
  // the parent's padding must not make a full-width tray look narrower.
  const widths = await tablist.evaluate((el) => {
    const parent = el.parentElement as HTMLElement;
    const style = getComputedStyle(parent);
    return {
      own: el.getBoundingClientRect().width,
      content: parent.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
    };
  });
  expect(widths.own, `tray ${widths.own}px in a ${widths.content}px column`).toBeLessThan(widths.content - 1);
});

test.describe('analytics charts: data ink (dataviz-09 / visual-08 item 3)', () => {
  /** WCAG contrast of an SVG mark's painted fill or stroke on the surface behind it. */
  const inkContrast = async (mark: Locator, property: 'fill' | 'stroke') => {
    await expect(mark).toBeAttached();
    const ink = parseRgb(await mark.evaluate((el, prop) => getComputedStyle(el).getPropertyValue(prop), property));
    return contrastRatio(ink, (await renderedColors(mark)).bg);
  };

  for (const accent of ['bright', 'red'] as const) {
    test(`light + ${accent}: the past bins, the evidence line and the Sankey bars clear 3:1 on their surface`, async ({ app, page }) => {
      await app.setTheme('light');
      await app.setAccent(accent);
      await app.gotoRoute('/analytics');
      const past = figureOf(page, 'Opportunity Score Distribution').locator('rect.chart-hist__bar--past').first();
      expect(await inkContrast(past, 'fill'), 'past-bin fill').toBeGreaterThanOrEqual(3);
      expect(await inkContrast(page.locator('#main-content .funnel-sankey__bar').first(), 'fill'), 'Sankey bar').toBeGreaterThanOrEqual(3);
      await app.gotoRoute('/analytics?view=signals');
      const line = figureOf(page, 'Evidence Events Per Day').locator('polyline');
      expect(await inkContrast(line, 'stroke'), 'evidence polyline').toBeGreaterThanOrEqual(3);
    });
  }
});

test.describe('analytics charts: axe and audited reads', () => {
  for (const theme of ['dark', 'light'] as const) {
    for (const tab of CHART_TABS) {
      test(`${tab.name} (${theme}): the charts region is axe-clean with no recorded exceptions`, async ({ app, page }) => {
        await app.setTheme(theme);
        await app.gotoRoute(tab.path);
        await expect(page.locator('#main-content figure.chart-frame').first()).toBeVisible();
        await expectAxeClean(page, { key: { route: tab.route, state: 'charts' }, theme, known: {}, include: '#main-content' });
      });
    }
  }

  test('economics: hovering and focusing the scatter cells and the histogram opens no audited read', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/analytics?view=economics');
    const cells = page.locator('#main-content .analytics-scatter__bin');
    await expect(cells.first()).toBeVisible();
    const naturalLoad = markNaturalLoad(mockApi);
    for (let i = 0; i < 3; i += 1) await cells.nth(i).hover();
    await cells.first().focus();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');
    await plotNamed(page, 'Rate Spread Distribution').hover();
    await plotNamed(page, 'Rate Spread Distribution').focus();
    await page.keyboard.press('ArrowRight');
    // Zoom into a cell: its borrower dots link to Borrower 360, and hovering
    // or focusing one must never prefetch that audited read (VIEW_BORROWER).
    await cells.first().click();
    const markers = page.locator('#main-content [data-scatter-marker]');
    await expect(markers.first()).toBeVisible();
    // Dots overlap in the dense fixture window: hover wherever each one sits.
    for (let i = 0; i < Math.min(3, await markers.count()); i += 1) await markers.nth(i).hover({ force: true });
    await markers.first().focus();
    await page.keyboard.press('ArrowRight');
    expectNoAuditedReadSince(mockApi, naturalLoad, 'economics scatter + histogram hover/focus');
  });
});

test('executive: each Pipeline Metrics bar shows exactly its share, sliding by translate, instant under reduced motion (motion-08)', async ({ app, page }) => {
  await app.gotoRoute('/analytics');
  const fills = page.locator('#main-content .analytics-bars__fill');
  await expect(fills.first()).toBeAttached();
  const bars = await fills.evaluateAll((spans) =>
    spans.map((fill) => {
      const track = (fill.parentElement as HTMLElement).getBoundingClientRect();
      const box = fill.getBoundingClientRect();
      const style = getComputedStyle(fill);
      return {
        share: Number.parseFloat((fill as HTMLElement).style.getPropertyValue('--bar-pct')) / 100,
        shown: Math.max(0, Math.min(box.right, track.right) - track.left),
        track: track.width,
        property: style.transitionProperty,
        duration: Number.parseFloat(style.transitionDuration),
        reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
      };
    }),
  );
  expect(bars.length).toBeGreaterThan(1);
  for (const bar of bars) {
    expect(Math.abs(bar.shown - bar.share * bar.track), `a ${bar.share} bar shows ${bar.shown}px of ${bar.track}px`).toBeLessThanOrEqual(1);
    expect(bar.property).toBe('translate');
    expect(bar.reduced, 'the harness runs under reduced motion').toBe(true);
    expect(bar.duration).toBeLessThan(0.001);
  }
});

test('economics zoom: an open cluster leaves Escape to the Genie composer, then still closes to its marker (a11y-07)', async ({ app, mockApi, page }) => {
  // Two borrowers at one exact coordinate in whatever window the zoom asks
  // for: the zoom renders one numbered cluster marker.
  mockApi.register<EquitySpreadPointsResponse>('GET', '/api/analytics/economics/points', ({ query }) => {
    const viewport = {
      equity_min: Number(query.get('equity_min') ?? 0),
      equity_max: Number(query.get('equity_max') ?? 80),
      spread_min: Number(query.get('spread_min') ?? 0),
      spread_max: Number(query.get('spread_max') ?? 250),
    };
    const equity = Math.round((viewport.equity_min + viewport.equity_max) / 2);
    const spread = Math.round((viewport.spread_min + viewport.spread_max) / 2);
    const points = BORROWERS.slice(0, 2).map((borrower) => ({
      borrower_id: borrower.borrower_id,
      display_name: borrower.display_name,
      segment: borrower.segment_codes[0],
      state: borrower.state,
      equity_pct: equity,
      rate_spread_bps: spread,
      opportunity_score: borrower.opportunity_score,
      coordinate_total: 2,
    }));
    return json<EquitySpreadPointsResponse>({
      points,
      total_matching: 2,
      showing: 2,
      point_cap: 500,
      truncated: false,
      viewport,
      source_table: 'mip.gold.borrower_360',
    });
  });
  await app.gotoRoute('/analytics?view=economics');
  const cells = page.locator('#main-content .analytics-scatter__bin');
  await expect(cells.first()).toBeVisible();
  await cells.first().click();
  const marker = page.locator('#main-content button.analytics-scatter__cluster-marker');
  const clusterPanel = page.locator('#main-content .analytics-scatter__cluster-panel');
  await expect(marker).toHaveCount(1);

  // Genie opens first, so its non-modal layer sits BELOW the cluster's.
  const genie = await app.openGenie();
  const composer = genie.getByRole('textbox', { name: 'Ask Genie' });
  // Open the cluster from the keyboard: the floating panel may cover it.
  await marker.focus();
  await page.keyboard.press('Enter');
  await expect(marker).toHaveAttribute('aria-expanded', 'true');
  await expect(clusterPanel).toBeVisible();

  // Back in the composer, Escape belongs to Genie: the cluster stays open
  // and focus is never pulled to the marker behind the panel.
  await composer.click();
  await expect(composer).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(genie).toBeHidden();
  await expect(marker).toHaveAttribute('aria-expanded', 'true');
  await expect(clusterPanel).toBeVisible();
  await expect(marker).not.toBeFocused();

  // Inside the cluster, Escape still closes it back to its marker.
  await clusterPanel.locator('a').first().focus();
  await page.keyboard.press('Escape');
  await expect(marker).toHaveAttribute('aria-expanded', 'false');
  await expect(clusterPanel).toBeHidden();
  await expect(marker).toBeFocused();
});
