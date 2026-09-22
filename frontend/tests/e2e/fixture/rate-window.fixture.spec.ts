/**
 * Lane rate-window (dataviz-08 / dataviz-06): the "Why now" surface on the
 * Analytics Executive tab, rendered against the synthetic 60-week series in
 * data/rateWindow.ts. Pins the rendered layer: two stacked panels on one
 * x-axis, the spread sentence for the fixture's numbers, the labelled refi
 * screen line, the evidence chip's drawer destination, the warming-up
 * degraded state and the table alternative.
 */
import { RATE_WINDOW_EXPECTED, RATE_WINDOW_WEEK_COUNT } from './data/rateWindow';
import { WAREHOUSE_WARMING_UP } from './mockApi';
import { expect, test } from './test';

test.describe('analytics executive: why-now rate window', () => {
  test('draws two stacked panels on one axis, states the spread in words and labels the refi screen', async ({ app, page }) => {
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
