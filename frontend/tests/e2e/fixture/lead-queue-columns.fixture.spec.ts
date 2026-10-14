/**
 * W5e w5-lead-queue-columns, proven in the production build at 1440x900:
 *
 *  (a) HIDE Segments + Rate in Review: the colgroup, the header and every
 *      visible row lose both cells in the same order; ?hide=segments,rate;
 *      ZERO /api/leads reads and no audited read after the toggle; Copy link
 *      carries hide; a reload restores it;
 *  (b) Console open, Review with Location + Segments + Rate hidden: the
 *      scroller has no horizontal overflow, Approval stays pinned and fully
 *      visible, and the header keeps 83px;
 *  (c) a saved view marked 'Use as my default' (Sales ops, two hidden
 *      columns) governs the columns of /lead-queue?state=TX with the URL
 *      unchanged and no extra read; ?view= wins; choosing Review with No
 *      columns writes hide=none;
 *  (e) the filter omnibox: F focuses the combobox, the readings and their
 *      sources, one debounced allowlisted count (never a borrower list or an
 *      approval-request scope), Enter applies with one page-0 read, 'Ask
 *      Genie instead' prefills without a Genie request, lower-case 'in' and
 *      'or' are never states, and F does nothing on Borrower 360 while the
 *      queue is kept alive;
 *  (f) More like this from an expanded row and on Borrower 360: one count
 *      without borrower_ids, the queue link, the Triage deck link for an
 *      approver, no POST and no approve control;
 *  (g) the filter-panel fixes: the inverted URL pair error on load, the
 *      rounding notice, 25 saved views with the form above the fold, a
 *      chunk-failure Retry that recovers, and the facet status announced.
 */
import type { Page } from '@playwright/test';
import type { SavedView, SavedViewListResponse } from '../../../src/types/leadFilters';
import { spendStaleChunkReload } from './data/errorTelemetry';
import { SNAPSHOT_AT } from './data/reference';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const ROWS = 'table.lead-table__table tbody tr[data-borrower-row]';

function leadReads(mockApi: MockApi): string[] {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/leads').map((call) => call.search);
}

function countReads(mockApi: MockApi): string[] {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/leads/count').map((call) => call.search);
}

async function headerAndCells(page: Page) {
  return page.locator('table.lead-table__table').evaluate((table, rowSelector) => {
    const header = [...table.querySelectorAll('thead tr:first-child th')].map((th) => (th.textContent ?? '').trim());
    const cols = [...table.querySelectorAll('colgroup col')].map((col) => col.className);
    const rows = [...table.querySelectorAll(rowSelector.replace('table.lead-table__table ', ''))].map((row) => row.querySelectorAll(':scope > td').length);
    return { header, cols, rows };
  }, ROWS);
}

async function stubClipboard(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const written: string[] = [];
    (window as unknown as { __copied: string[] }).__copied = written;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: (text: string) => { written.push(text); return Promise.resolve(); } },
    });
  });
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    test('(a) HIDE drops the same columns from the colgroup, the header and every row, reads nothing, and rides the URL', async ({ app, page, mockApi }) => {
      await stubClipboard(page);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await expect(page.locator(ROWS).first()).toBeVisible();
      // The omnibox pill leads the core filter row without wrapping it at 1440,
      // with Linux Chromium's wider Geist Mono emulated off Linux.
      await page.addStyleTag({ content: '.filter-row--lead-queue .filter__value { letter-spacing: 0.5px; }' });
      const tops = await page.locator('.filter-row--lead-queue > *:not(.filter-row__spacer)').evaluateAll(
        (items) => [...new Set(items.map((item) => Math.round(item.getBoundingClientRect().top)))],
      );
      expect(tops, 'the core filter row stays one line').toHaveLength(1);
      const reads = leadReads(mockApi).length;
      const t0 = markNaturalLoad(mockApi);
      const menu = await app.openFilterMenu('HIDE');
      await menu.getByRole('option', { name: 'Segments' }).click();
      await menu.getByRole('option', { name: 'Rate Δ (bps)' }).click();
      await page.keyboard.press('Escape');
      await expect.poll(() => new URL(page.url()).searchParams.get('hide')).toBe('segments,rate');
      const layout = await headerAndCells(page);
      expect(layout.header).not.toContain('Segments');
      expect(layout.header.some((label) => label.startsWith('Rate'))).toBe(false);
      expect(layout.cols).toHaveLength(layout.header.length);
      expect(layout.cols).not.toContain('lead-table__col-segments');
      expect(layout.rows.length).toBeGreaterThan(0);
      for (const cells of layout.rows) expect(cells, 'row / header parity').toBe(layout.header.length);
      expect(layout.header).toEqual(expect.arrayContaining(['Primary offer', 'Score', 'Signal', 'Approval']));
      expect(leadReads(mockApi), 'hiding a column reads nothing').toHaveLength(reads);
      expectNoAuditedReadSince(mockApi, t0, 'HIDE');

      await page.getByTestId('lead-queue-copy-link').click();
      const copied = await page.evaluate(() => (window as unknown as { __copied: string[] }).__copied);
      expect(new URL(copied[0]).searchParams.get('hide')).toBe('segments,rate');

      await page.reload();
      await app.settle();
      await expect(page.locator('button[aria-label^="HIDE:"]')).toHaveAttribute('aria-label', 'HIDE: 2 columns');
      expect((await headerAndCells(page)).header).not.toContain('Segments');
    });

    test('(b) with the Console open and three columns hidden, nothing scrolls sideways and Approval stays pinned', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?hide=location,segments,rate');
      await app.openConsole();
      await app.settle();
      const wrap = page.locator('.tbl-wrap');
      await expect.poll(() => wrap.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
      const approval = page.locator('th.lead-table__approval-header');
      const [cell, region] = await Promise.all([approval.boundingBox(), wrap.boundingBox()]);
      expect(cell && region && cell.x + cell.width <= region.x + region.width + 0.5).toBe(true);
      const header = page.locator('.lead-table__header');
      expect(Math.round((await header.boundingBox())?.height ?? 0)).toBeLessThanOrEqual(84);
    });
  });
}

const DEFAULT_VIEW: SavedView = {
  view_id: '5a1e0000-0000-4000-8000-0000000000dd',
  name: 'Ops desk columns',
  params: 'hide=relationship%2Clast-touch&state=TX&view=sales-ops',
  created_at: SNAPSHOT_AT,
  updated_at: SNAPSHOT_AT,
};

test('(c) the default saved view governs the columns only; ?view= wins; Review + No columns writes hide=none', async ({ app, page, mockApi }) => {
  mockApi.register<SavedViewListResponse>('GET', '/api/workspace/saved-views', () => json<SavedViewListResponse>({ saved_views: [DEFAULT_VIEW] }));
  await app.gotoRoute('/lead-queue');
  await page.getByTestId('lead-queue-saved-views').click();
  const toggle = page.getByRole('button', { name: 'Use as my default: Ops desk columns' });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Escape');

  await app.gotoRoute('/lead-queue?state=IL');
  const reads = leadReads(mockApi).length;
  await expect(page.locator('button[aria-label^="VIEW:"]')).toHaveAttribute('aria-label', 'VIEW: Sales ops');
  const header = (await headerAndCells(page)).header;
  expect(header).not.toContain('Relationship');
  expect(header).not.toContain('Last touch');
  expect(header).toContain('Assigned to');
  expect(new URL(page.url()).search, 'the default never writes the URL or adds a filter').toBe('?state=IL');
  expect(leadReads(mockApi).every((search) => !search.includes('TX'))).toBe(true);
  await app.settle();
  expect(leadReads(mockApi)).toHaveLength(reads);

  await app.gotoRoute('/lead-queue?state=IL&view=default');
  await expect(page.locator('button[aria-label^="VIEW:"]')).toHaveAttribute('aria-label', 'VIEW: Review');

  await app.gotoRoute('/lead-queue?state=IL');
  const view = await app.openFilterMenu('VIEW');
  await view.getByRole('option', { name: 'Review' }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get('hide')).toBe('none');
  await expect(page.locator('button[aria-label^="HIDE:"]')).toHaveAttribute('aria-label', 'HIDE: No columns');
});

test('(e) the omnibox reads one allowlisted count, applies on Enter, and hands unrecognized text to Genie as a prefill', async ({ app, page, mockApi }) => {
  await app.gotoRoute('/lead-queue?borrower_ids=B-0123456789ABC&approval_request_batch=0f8fad5b-d9cb-469f-a165-70867728950e&view=sales-ops&hide=rate');
  await page.keyboard.press('f');
  const input = page.getByTestId('lead-queue-omnibox-input');
  await expect(input).toBeFocused();
  // One input event: the count waits for the typing to pause (200 ms).
  await input.fill('TX IL heloc aged>7 zyrplax in or');
  await expect(page.locator('.lead-queue-omnibox__chip')).toHaveText([
    'STATE · TX (from "TX")', 'STATE · IL (from "IL")', 'PRODUCT · HELOC (from "heloc")', 'AGING · Aged >7d (from "aged>7")',
  ]);
  await expect(page.getByTestId('lead-queue-omnibox-unrecognized')).toHaveText('Not recognized: zyrplax, in, or');
  await expect(page.locator('.lead-queue-omnibox__count')).toHaveText(/borrowers? match/);
  await page.waitForTimeout(400);
  const counts = countReads(mockApi);
  expect(counts, 'one debounced count').toHaveLength(1);
  expect([...new URLSearchParams(counts[0])]).toEqual([['states', 'IL,TX'], ['product', 'HELOC'], ['aged_days', '7']]);

  await page.getByTestId('lead-queue-omnibox-ask-genie').click();
  const genie = page.getByRole('dialog', { name: 'Genie chat' });
  await expect(genie.getByRole('textbox', { name: 'Ask Genie' })).toHaveValue('TX IL heloc aged>7 zyrplax in or');
  // A prefill only: nothing was asked (opening the panel starts its session; no question is sent).
  expect(mockApi.calls.filter((call) => call.path.startsWith('/api/genie/message'))).toEqual([]);
  await page.keyboard.press('Escape');

  await page.getByTestId('lead-queue-omnibox').click();
  await page.getByTestId('lead-queue-omnibox-input').fill('TX heloc');
  const reads = leadReads(mockApi).length;
  await page.getByTestId('lead-queue-omnibox-input').press('Enter');
  await expect.poll(() => new URL(page.url()).search).toBe('?view=sales-ops&hide=rate&state=TX&product=HELOC');
  await app.settle();
  expect(leadReads(mockApi).slice(reads), 'one page-0 read').toHaveLength(1);
  await expect(page.getByTestId('lead-queue-omnibox')).toBeFocused();
});

test('(e) F does nothing on Borrower 360 while the queue is kept alive behind it', async ({ app, page }) => {
  await app.gotoRoute('/lead-queue');
  const row = await app.expandFirstLeadRow();
  await row.getByRole('link', { name: 'Open Borrower 360' }).click();
  await app.settle();
  await expect(page).toHaveURL(/\/borrower-360\//);
  await page.keyboard.press('f');
  await page.waitForTimeout(300);
  await expect(page.getByTestId('lead-queue-omnibox-panel')).toHaveCount(0);
  await expect(page.getByTestId('lead-queue-omnibox-input')).toHaveCount(0);
});

test('(f) More like this offers links only, after one count without a borrower list, in the row and on Borrower 360', async ({ app, page, mockApi }) => {
  await app.gotoRoute('/lead-queue');
  const row = await app.expandFirstLeadRow();
  const t0 = markNaturalLoad(mockApi);
  await row.getByRole('button', { name: 'More like this' }).click();
  const panel = row.locator('.similar-cohort');
  await expect(panel.locator('[role="status"]')).toHaveText(/match this pattern/);
  expect(countReads(mockApi)).toHaveLength(1);
  expect(new URLSearchParams(countReads(mockApi)[0]).has('borrower_ids')).toBe(false);
  await expect(panel.getByTestId('lead-similar-open-queue')).toHaveAttribute('href', /^\/lead-queue\?/);
  await expect(panel.getByTestId('lead-similar-open-triage')).toHaveAttribute('href', /mode=triage$/);
  await expect(panel.getByRole('button')).toHaveCount(0);
  expect(mockApi.calls.slice(t0).filter((call) => call.method === 'POST' && /approv|outreach|leads|assign/.test(call.path))).toEqual([]);
  expectNoAuditedReadSince(mockApi, t0, 'More like this');

  await row.getByRole('link', { name: 'Open Borrower 360' }).click();
  await app.settle();
  const before = countReads(mockApi).length;
  await page.getByTestId('borrower-more-like-this').click();
  // The kept-alive queue still holds its own (hidden) panel: read the dossier's.
  const dossier = page.locator('.chip-row:has([data-testid="borrower-more-like-this"])');
  await expect(dossier.locator('.similar-cohort [role="status"]')).toHaveText(/match this pattern/);
  // The same pattern's count is served from the 60 s cache: at most one more read.
  expect(countReads(mockApi).length).toBeLessThanOrEqual(before + 1);
});

test('(g) the filter-panel fixes: inverted pair, rounding, 25 views above the fold, chunk Retry, announced facet status', async ({ app, page, mockApi, hygiene }) => {
  await app.gotoRoute('/lead-queue?min_opportunity_score=91&max_opportunity_score=90');
  await page.getByTestId('lead-queue-more-filters').click();
  await expect(page.getByTestId('lead-queue-range-min_opportunity_score')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByTestId('lead-queue-range-max_opportunity_score')).toHaveAttribute('aria-invalid', 'true');
  await page.getByTestId('lead-queue-range-min_opportunity_score').fill('7.5');
  await page.keyboard.press('Enter');
  await expect(page.getByText('Rounded to a whole number; set to 8.')).toBeVisible();

  const views: SavedView[] = Array.from({ length: 25 }, (_, index) => ({
    view_id: `5a1e0000-0000-4000-8000-${String(index).padStart(12, '0')}`,
    name: `View ${index + 1}`,
    params: `state=IL&aged_days=${(index % 3) * 7 + 7}`,
    created_at: SNAPSHOT_AT,
    updated_at: SNAPSHOT_AT,
  }));
  mockApi.register<SavedViewListResponse>('GET', '/api/workspace/saved-views', () => json<SavedViewListResponse>({ saved_views: views }));
  // The panel chunk fails ONCE. Retry re-attempts the import; a browser that
  // kept the failed module fetch fails it again, and the same button then
  // offers Reload, which recovers (the queue's state is its URL).
  hygiene.allow('request-failed', /lead-queue\.savedViews-[^/]+\.js/);
  hygiene.allow('console.error', /\[mip\] client error/);
  hygiene.allow('console.error', /Failed to load resource|Failed to fetch dynamically imported module/);
  let failed = false;
  await page.route(/\/assets\/lead-queue\.savedViews-[^/]+\.js$/, async (route) => {
    if (!failed) {
      failed = true;
      await route.abort();
    } else {
      await route.continue();
    }
  });
  await app.gotoRoute('/lead-queue');
  await spendStaleChunkReload(page);
  await page.getByTestId('lead-queue-saved-views').click();
  const retry = page.getByTestId('lead-queue-saved-views-retry');
  await expect(retry).toHaveText('Retry');
  await retry.click();
  const panel = page.getByTestId('lead-queue-saved-views-panel');
  await expect(panel.or(retry.filter({ hasText: 'Reload' }))).toBeVisible();
  if (!(await panel.isVisible())) {
    await expect(retry).toBeFocused();
    await retry.click();
    await app.settle();
    await expect(page).toHaveURL(/\/lead-queue$/);
    await page.getByTestId('lead-queue-saved-views').click();
  }
  await expect(panel).toBeVisible();
  const save = page.getByTestId('lead-queue-saved-views-save');
  const fold = page.viewportSize()?.height ?? 900;
  expect(((await save.boundingBox())?.y ?? fold) + ((await save.boundingBox())?.height ?? 0)).toBeLessThanOrEqual(fold);
  await page.keyboard.press('Escape');

  const restore = app.degrade('/api/leads/facets', { status: 503, body: { detail: 'down' } });
  await app.openFilterMenu('STATE');
  const status = page.locator('.filter-root [role="status"].sr-only').first();
  await expect(status).toHaveText('Counts unavailable');
  await expect(page.getByRole('listbox', { name: 'STATE' })).not.toHaveAttribute('aria-busy', 'true');
  restore();
});
