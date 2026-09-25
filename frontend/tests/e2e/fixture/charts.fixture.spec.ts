/**
 * Lane w4-charts (2026-09-21 audit stack-06, visual-08, dataviz-06/07/10/v2,
 * a11y-10, responsive-v3, css-06, motion-08, a11y-07): the Analytics chart
 * foundation, rendered against the fixture harness at 1440x900.
 *
 * Pins the rendered layer: 1-2-5 axes on every numeric tick label.
 */
import type { Page } from '@playwright/test';
import { expect, test } from './test';

const CHART_TABS = [
  { name: 'executive', path: '/analytics' },
  { name: 'economics', path: '/analytics?view=economics' },
  { name: 'signals', path: '/analytics?view=signals' },
] as const;

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
  for (const tab of CHART_TABS) {
    test(`${tab.name}: every numeric axis label is a 1-2-5 multiple`, async ({ app, page }) => {
      await app.setTheme('dark');
      await app.gotoRoute(tab.path);
      await expect(page.locator('#main-content .analytics-chart__plot').first()).toBeVisible();
      const readings = await axisReadings(page);
      const numeric = readings.filter((reading) => reading.labels.some((label) => numericLabel(label) !== null));
      expect(numeric.length, `${tab.name} draws numeric axes`).toBeGreaterThan(0);
      expect(numeric.flatMap(oneTwoFiveProblems)).toEqual([]);
    });
  }
});
