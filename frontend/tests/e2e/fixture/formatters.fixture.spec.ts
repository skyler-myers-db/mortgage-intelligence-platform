/**
 * Rendered-layer proofs for the wave-1c formatting contract (2026-09-21
 * audit responsive-04 numbers, responsive-07 dates): lib/formatters and
 * lib/time as the production build paints them.
 *
 * The page clock is frozen at FIXTURE_NOW (15:00 UTC), three hours after the
 * gold snapshot (SNAPSHOT_AT, 12:00 UTC); fixture pages run in en-US and
 * America/New_York, so an unzoned or locally shifted timestamp would show.
 */
import type { Borrower360, GeographyAnalyticsResponse } from '../../../src/types';
import { analyticsFixtures } from './data/analytics';
import { PRIMARY_BORROWER } from './data/borrowers';
import { TOTALS } from './data/reference';
import { expect, test } from './test';

const count = (value: number) => value.toLocaleString('en-US');

/** Text shapes the audit rendered live and the contract forbids anywhere. */
const FORBIDDEN_NUMBER_SHAPES: ReadonlyArray<[RegExp, string]> = [
  [/\$-/, 'a sign after the currency symbol ("$-4.41M")'],
  [/\b\d+\.\d{2}[KMBT]\b/, 'a compact figure at two decimals ("18.08K")'],
  [/\b\d{4}[KMBT]\b/, 'a four-digit figure in a compact unit ("$1000K")'],
  [/-0(\.0+)?%/, 'a negative zero percent ("-0.0%")'],
];

async function expectNoForbiddenShapes(text: string, where: string): Promise<void> {
  for (const [pattern, what] of FORBIDDEN_NUMBER_SHAPES) {
    expect(text, `${where} renders ${what}`).not.toMatch(pattern);
  }
}

test.describe('one number contract across routes', () => {
  test('the addressable count renders the same text on Home and Analytics', async ({ app, page }) => {
    await app.gotoRoute('/');
    const home = page.locator('.kpi', { hasText: 'Addressable population' }).locator('.kpi__value');
    await expect(home).toHaveText(count(TOTALS.addressable));
    const homeText = (await home.textContent())?.trim();

    await app.gotoRoute('/analytics');
    const analytics = page.locator('.kpi', { hasText: 'Addressable Borrowers' }).locator('.kpi__value');
    // Analytics used to abbreviate the same metric to "89.55K".
    await expect(analytics).toHaveText(count(TOTALS.addressable));
    expect((await analytics.textContent())?.trim()).toBe(homeText);
  });

  test('chart axis ticks read at most one fraction digit', async ({ app, page }) => {
    await app.gotoRoute('/analytics');
    // The borrower-count axis (fixture max 27,620): the old two-decimal
    // compact formatter printed "6.91K / 13.81K / 20.72K / 27.62K".
    const chart = page.locator('section.surface', { has: page.getByRole('heading', { name: 'Opportunity Score Distribution' }) });
    const ticks = chart.locator('.analytics-chart__tick--y');
    await expect(ticks.first()).toBeVisible();
    const texts = (await ticks.allTextContents()).map((text) => text.trim()).filter(Boolean);
    expect(texts.length).toBeGreaterThan(1);
    for (const text of texts) expect(text).toMatch(/^\d{1,3}(\.\d)?[KMB]?$/);
  });

  test('a negative compact dollar figure puts its sign before the "$"', async ({ app, mockApi, page }) => {
    const base = analyticsFixtures.find((entry) => entry.method === 'GET' && entry.pattern === '/api/analytics/geography');
    if (!base) throw new Error('geography fixture missing');
    mockApi.register<GeographyAnalyticsResponse>('GET', '/api/analytics/geography', async (request) => {
      const reply = await base.handler(request);
      const body = reply.body as GeographyAnalyticsResponse;
      return {
        ...reply,
        body: {
          ...body,
          state_avm_values: body.state_avm_values.map((row, index) =>
            index === 0 ? { ...row, total_equity_usd: -4_410_000 } : row,
          ),
        },
      };
    });
    await app.gotoRoute('/analytics?view=geography');
    const avm = page.locator('section.surface', { has: page.getByRole('heading', { name: 'AVM Value by State' }) });
    await expect(avm.locator('.analytics-bars__sub').first()).toHaveText('-$4.4M equity');
    await expectNoForbiddenShapes((await avm.textContent()) ?? '', 'AVM Value by State');
  });

  for (const route of ['/', '/analytics', '/analytics?view=geography', '/analytics?view=economics', '/portfolio-builder']) {
    test(`${route} renders none of the audited number defects`, async ({ app, page }) => {
      await app.gotoRoute(route);
      await expectNoForbiddenShapes((await page.locator('#main-content').innerText()) ?? '', route);
    });
  }
});

test.describe('one date contract', () => {
  test('the Home freshness chip reads relative age with an absolute UTC title', async ({ app, page }) => {
    await app.gotoRoute('/');
    const chip = page.locator('.chip', { hasText: 'Refreshed' });
    await expect(chip).toHaveCount(1);
    const time = chip.locator('time');
    await expect(time).toHaveText('3 hours ago');
    await expect(chip).toHaveText('Refreshed 3 hours ago');
    await expect(time).toHaveAttribute('datetime', '2026-07-14T12:00:00.000Z');
    await expect(time).toHaveAttribute('title', 'Jul 14, 2026, 12:00 PM UTC');
  });

  test('the Analytics snapshot date is formatted, never the raw wire string', async ({ app, page }) => {
    await app.gotoRoute('/analytics');
    const main = page.locator('#main-content');
    await expect(page.locator('.kpi', { hasText: 'Addressable Borrowers' }).locator('.kpi__delta')).toHaveText('Snapshot Jul 14');
    const snapshot = main.locator('.analytics-panel-note time[datetime="2026-07-14"]');
    await expect(snapshot).toHaveText('Jul 14');
    await expect(snapshot).toHaveAttribute('title', 'Jul 14, 2026');
    await expect(main).not.toContainText('2026-07-14');
  });

  test('Borrower 360 reads a naive UTC wire timestamp as UTC and names the zone', async ({ app, mockApi, page }) => {
    // The wire's naive SQL shape means UTC. The duplicated formatDateTimeShort
    // parsed it as viewer-local and printed no zone: "Jul 14, 12:00 PM" for
    // what was 8:00 AM in this New York page.
    const stamped: Borrower360 = {
      ...PRIMARY_BORROWER,
      outreach_status: 'queued',
      outreach_at: '2026-07-14 12:00:00',
      latest_disposition_outcome: 'connected',
      latest_disposition_at: '2026-07-14T13:30:00Z',
    };
    mockApi.register<Borrower360>('GET', '/api/borrowers/:id', () => ({ body: stamped }));
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    const field = (label: string) =>
      page.locator('.field__label', { hasText: label }).locator('xpath=..').locator('.field__value');
    await expect(field('Outreach status')).toHaveText('Queued · Jul 14, 8:00 AM EDT');
    await expect(field('Latest disposition')).toHaveText('Connected · Jul 14, 9:30 AM EDT');
  });

  test('Borrower 360 trigger rows read the prototype narrow age with the instant attached', async ({ app, page }) => {
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    const when = page.locator('.trig__when time');
    await expect(when.first()).toBeVisible();
    // Fixture triggers at Jul 12, Jul 10 and Jul 2 (06:12 UTC) against a
    // Jul 14 15:00 UTC clock. The hand-rolled relativeWhen printed bare text
    // with no instant and "1y ago" for anything older than a year.
    expect(PRIMARY_BORROWER.trigger_timeline.map((event) => event.timestamp)).toEqual([
      '2026-07-12T06:12:00Z',
      '2026-07-10T06:12:00Z',
      '2026-07-02T06:12:00Z',
    ]);
    await expect(when).toHaveText(['2d ago', '4d ago', '12d ago']);
    // design_files/Design System.html `.trig__when`: uppercase mono ("2D AGO").
    await expect(page.locator('.trig__when').first()).toHaveCSS('text-transform', 'uppercase');
    for (const item of await when.all()) {
      await expect(item).toHaveAttribute('title', /^[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2} [AP]M UTC$/);
      await expect(item).toHaveAttribute('datetime', /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    }
  });
});
