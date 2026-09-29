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
import type { ExecutiveAnalyticsResponse } from '../../../src/types';
import {
  ECONOMICS_THRESHOLDS,
  SCORE_BUCKETS,
  SPREAD_BUCKETS,
  THRESHOLDS_NOT_BUILT,
  THRESHOLDS_NOT_UNIFORM,
  analyticsFixtures,
  economicsBody,
} from './data/analytics';
import { TOTALS } from './data/reference';
import { json } from './mockApi';
import { expect, test } from './test';
import { expectNoSurfaceOverflow } from './visual';

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
