/**
 * The Lead Queue's server-paged view, at the rendered layer (W5c,
 * D-audit-reads-a; audit tables-02 / delivery-02 / states-06;
 * deviation:lead-queue-load-next). Every served page of /api/leads writes
 * one VIEW_LEADS audit row, so the only reads are the reader's own:
 *
 *   - Load next: ONE GET per click, labelled with the next page's size, and
 *     a polite status line saying what loaded; the footer keeps its base
 *     copy and drops "capped at"; the cursor never reaches the URL.
 *   - No scroll load: scrolling the table to its end reads nothing.
 *   - A server sort reads page 0 of that order once and names the column;
 *     Load next carries the same sort.
 *   - A later page answering 409 or 422 lead_view_cursor_invalid restarts
 *     the view at page 0 and says the queue was updated; any other failure
 *     keeps the loaded rows and Retry reads the same page once.
 *   - Keyboard focus never falls to <body>: a failed Load next and a
 *     successful Retry keep it on the one paging button, and the last page
 *     (or a view restart) hands it to the count line.
 *   - A server that cannot page says "Narrow the filters to see more".
 *   - An approve and the CSV export declare the view they were taken on.
 *   - axe stays clean on the paged footer, in both themes.
 */
import fs from 'node:fs';
import type { ElementHandle, Locator, Page } from '@playwright/test';
import type { LeadExportReceipt, LeadExportReceiptRequest } from '../../../src/lib/apiClients/leadExport';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { PRIMARY_BORROWER } from './data/borrowers';
import { leadExportReceiptFor } from './data/exportAudit';
import { PAGED_QUEUE, registerPagedQueue, serverOrder } from './data/leadPages';
import { registerDraftEcho } from './data/queueKeyboard';
import { registerBulkApprove } from './data/queuePlace';
import { json } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

function footer(page: Page): Locator {
  return page.locator('.surface:has(> .tbl-wrap) > .surface__ft');
}

function tableWrap(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

/** The element keyboard focus is on is THIS (still mounted) element. */
function holdsFocus(page: Page, element: ElementHandle<HTMLElement | SVGElement>): Promise<boolean> {
  return page.evaluate((node) => node.isConnected && document.activeElement === node, element);
}

function focusIsOnBody(page: Page): Promise<boolean> {
  return page.evaluate(() => document.activeElement === null || document.activeElement === document.body);
}

test.describe('Load next', () => {
  test('reads one page per click, says what it loaded, and ends on the remainder', async ({ app, mockApi, page }) => {
    const queue = registerPagedQueue(mockApi);
    await app.gotoRoute('/lead-queue');
    await expect(footer(page)).toContainText('Showing 500 ranked borrowers of 1,284 total matching filters');
    await expect(footer(page)).not.toContainText('capped at');
    const next = page.getByTestId('lead-load-next');
    await expect(next).toHaveText('Load next 500');
    await expect(next).toHaveClass('btn btn--ghost btn--sm');
    expect(queue.reads.map((read) => read.cursor)).toEqual([null]);

    await next.click();
    await expect(footer(page)).toContainText('Showing 1,000 ranked borrowers of 1,284 total matching filters');
    await expect(page.getByTestId('lead-paging-status')).toHaveText('Loaded 500 more · showing 1,000 of 1,284');
    await expect(next, 'the last page is the remainder').toHaveText('Load next 284');
    expect(queue.reads.map((read) => read.page)).toEqual([0, 1]);

    await next.click();
    await expect(footer(page)).toContainText('Showing 1,284 ranked borrowers of 1,284 total matching filters');
    await expect(next).toHaveCount(0);
    await expect(page.getByTestId('lead-paging-narrow'), 'everything is loaded').toHaveCount(0);
    expect(queue.reads.map((read) => read.page)).toEqual([0, 1, 2]);
    expect(page.url(), 'the cursor never reaches the URL').not.toMatch(/cursor|sig/);
  });

  test('scrolling the table to its end, and J past the last row, load nothing', async ({ app, mockApi, page }) => {
    const queue = registerPagedQueue(mockApi);
    await app.gotoRoute('/lead-queue');
    await tableWrap(page).evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect.poll(() => tableWrap(page).evaluate((element) => element.scrollTop)).toBeGreaterThan(1_000);
    await app.settle();
    expect(queue.reads, 'no scroll load').toHaveLength(1);

    const last = PAGED_QUEUE[499].borrower_id;
    await page.getByRole('button', { name: `Toggle preview for lead ${last}` }).focus();
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', last);
    for (let step = 0; step < 3; step += 1) await page.keyboard.press('j');
    await app.settle();
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', last);
    expect(queue.reads, 'J at the last loaded row loads nothing').toHaveLength(1);
    await expect(page.getByTestId('lead-load-next')).toBeVisible();
  });

  test('a server sort reads page 0 of that order once, names it, and pages in it', async ({ app, mockApi, page }) => {
    const queue = registerPagedQueue(mockApi);
    await app.gotoRoute('/lead-queue');
    await page.getByRole('button', { name: 'Sort by Equity' }).click();
    await expect(page).toHaveURL(/[?&]sort=equity&dir=desc(&|$)/);
    const byEquity = serverOrder(PAGED_QUEUE, 'equity', 'desc');
    await expect(page.locator('table.tbl tbody .lead-table__borrower').first()).toHaveText(byEquity[0].borrower_id);
    await expect(page.getByTestId('lead-sort-scope')).toHaveText(' · sorted by Equity');
    expect(queue.reads.map(({ page: index, sort, dir }) => [index, sort, dir])).toEqual([
      [0, null, null],
      [0, 'equity', 'desc'],
    ]);

    await page.getByTestId('lead-load-next').click();
    await expect(footer(page)).toContainText('Showing 1,000 ranked borrowers');
    expect(queue.reads[2]).toMatchObject({ page: 1, sort: 'equity', dir: 'desc' });
  });
});

test.describe('a page that fails', () => {
  for (const [label, status, detail] of [
    ['409 (gold refreshed since page 0)', 409, 'lead_view_stale'],
    ['422 lead_view_cursor_invalid', 422, 'lead_view_cursor_invalid'],
  ] as const) {
    test(`a later page answering ${label} restarts the view at page 0 and says so`, async ({ app, hygiene, mockApi, page }) => {
      hygiene.allow('console.error', new RegExp(`status of ${status}`));
      const queue = registerPagedQueue(mockApi, { failOnce: { page: 1, status, detail } });
      await app.gotoRoute('/lead-queue');
      await page.getByTestId('lead-load-next').click();
      await expect(page.getByTestId('lead-paging-queue-updated')).toContainText('Queue updated: reloaded from the top');
      await expect(footer(page)).toContainText('Showing 500 ranked borrowers of 1,284');
      expect(queue.reads.map((read) => [read.page, read.status])).toEqual([[0, 200], [1, status], [0, 200]]);
      await expect(page.getByTestId('lead-load-next-error')).toHaveCount(0);
    });
  }

  test('any other failed Load next keeps the loaded rows; Retry reads the same page once', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 500/);
    const queue = registerPagedQueue(mockApi, { failOnce: { page: 1, status: 500, detail: 'Internal Server Error' } });
    await app.gotoRoute('/lead-queue');
    await page.getByTestId('lead-load-next').click();
    const failure = page.getByTestId('lead-load-next-error');
    await expect(failure).toHaveText("Couldn't load the next 500 ·");
    await expect(footer(page)).toContainText("Couldn't load the next 500 · Retry");
    await expect(footer(page)).toContainText('Showing 500 ranked borrowers of 1,284');
    await expect(page.locator('[role="alert"]').filter({ hasText: "Couldn't load ranked borrowers" })).toHaveCount(0);

    await page.getByRole('button', { name: 'Retry: load the next 500' }).click();
    await expect(footer(page)).toContainText('Showing 1,000 ranked borrowers of 1,284');
    expect(queue.reads.map((read) => read.cursor)).toEqual([null, queue.reads[1].cursor, queue.reads[1].cursor]);
    expect(queue.reads).toHaveLength(3);
  });

  test('a server that cannot page says to narrow the filters instead of offering Load next', async ({ app, mockApi, page }) => {
    registerPagedQueue(mockApi, { unavailable: true });
    await app.gotoRoute('/lead-queue');
    await expect(page.getByTestId('lead-load-next')).toHaveCount(0);
    await expect(page.getByTestId('lead-paging-narrow')).toContainText('Narrow the filters to see more');
  });
});

test.describe('focus through the paging transitions, by keyboard', () => {
  test('a failed Load next and a successful Retry keep focus on the one paging button', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 500/);
    const queue = registerPagedQueue(mockApi, { failOnce: { page: 1, status: 500, detail: 'Internal Server Error' } });
    await app.gotoRoute('/lead-queue');
    const control = page.getByTestId('lead-load-next');
    await control.focus();
    const button = await control.elementHandle();
    if (button === null) throw new Error('no paging button');

    await page.keyboard.press('Enter');
    await expect(page.getByTestId('lead-load-next-error')).toHaveText("Couldn't load the next 500 ·");
    await expect(control).toHaveAccessibleName('Retry: load the next 500');
    await expect(control, 'after a failed Load next').toBeFocused();
    expect(await holdsFocus(page, button), 'the same button, never remounted').toBe(true);

    await page.keyboard.press('Enter');
    await expect(footer(page)).toContainText('Showing 1,000 ranked borrowers of 1,284');
    await expect(control).toHaveText('Load next 284');
    await expect(control, 'after a successful Retry').toBeFocused();
    expect(await holdsFocus(page, button), 'the same button, never remounted').toBe(true);
    expect(queue.reads.map((read) => [read.page, read.status])).toEqual([[0, 200], [1, 500], [1, 200]]);
  });

  test('the last page hands focus to the count line, never to <body>', async ({ app, mockApi, page }) => {
    const queue = registerPagedQueue(mockApi);
    await app.gotoRoute('/lead-queue');
    const control = page.getByTestId('lead-load-next');
    await control.focus();

    await page.keyboard.press('Enter');
    await expect(control).toHaveText('Load next 284');
    await expect(control, 'a page that is not the last keeps focus').toBeFocused();

    await page.keyboard.press('Enter');
    await expect(footer(page)).toContainText('Showing 1,284 ranked borrowers of 1,284 total matching filters');
    await expect(control).toHaveCount(0);
    await expect(page.getByTestId('lead-paging-count'), 'after the last page').toBeFocused();
    expect(await focusIsOnBody(page)).toBe(false);
    expect(queue.reads.map((read) => read.page)).toEqual([0, 1, 2]);
  });

  test('a view restart on 409 keeps focus off <body>', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 409/);
    registerPagedQueue(mockApi, { failOnce: { page: 1, status: 409, detail: 'lead_view_stale' } });
    await app.gotoRoute('/lead-queue');
    await page.getByTestId('lead-load-next').focus();

    await page.keyboard.press('Enter');
    await expect(page.getByTestId('lead-paging-queue-updated')).toBeVisible();
    await expect(page.getByTestId('lead-load-next')).toHaveText('Load next 500');
    expect(await focusIsOnBody(page)).toBe(false);
  });
});

test.describe('decisions and the export declare the view', () => {
  test('an approve carries lead_view_id', async ({ app, mockApi, page }) => {
    const queue = registerPagedQueue(mockApi);
    registerDraftEcho(mockApi);
    const tracker = registerBulkApprove(mockApi);
    await app.gotoRoute('/lead-queue');
    const id = PRIMARY_BORROWER.borrower_id;
    await page.getByTestId(`lead-approve-${id}`).click();
    const confirm = page.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect.poll(() => tracker.bodies.length).toBe(1);
    expect(tracker.bodies[0]).toMatchObject({ borrower_id: id, review_mode: 'individual', lead_view_id: queue.viewId() });
    expect(queue.reads, 'pessimistic: the decision re-reads no page').toHaveLength(1);
  });

  test('the CSV export receipt carries lead_view_id and the loaded pages', async ({ app, mockApi, page }) => {
    const queue = registerPagedQueue(mockApi);
    const declarations: LeadExportReceiptRequest[] = [];
    mockApi.register<LeadExportReceipt>('POST', '/api/leads/export-receipt', ({ body }) => {
      declarations.push(body as LeadExportReceiptRequest);
      return json<LeadExportReceipt>(leadExportReceiptFor(body as LeadExportReceiptRequest));
    });
    await app.gotoRoute('/lead-queue');
    await page.getByTestId('lead-load-next').click();
    await expect(footer(page)).toContainText('Showing 1,000 ranked borrowers');

    const downloading = page.waitForEvent('download');
    await page.getByTestId('lead-export').click();
    const download = await downloading;
    const csv = fs.readFileSync((await download.path())!, 'utf8');
    const dataRows = csv.split('\n').filter((line) => line.length > 0 && !line.startsWith('#')).slice(1);
    expect(dataRows, 'the export covers both loaded pages').toHaveLength(1_000);
    expect(declarations).toHaveLength(1);
    expect(declarations[0]).toMatchObject({ lead_view_id: queue.viewId(), pages_loaded: 2 });
  });
});

test.describe('axe on the paged footer', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`Load next, the status line and the queue-updated note are clean (${theme})`, async ({ app, hygiene, mockApi, page }) => {
      hygiene.allow('console.error', /status of 409/);
      registerPagedQueue(mockApi, { failOnce: { page: 1, status: 409, detail: 'lead_view_stale' } });
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await page.getByTestId('lead-load-next').click();
      await expect(page.getByTestId('lead-paging-queue-updated')).toBeVisible();
      await expectAxeClean(page, {
        key: { route: 'lead-queue', state: 'paged-footer' },
        theme,
        include: '.surface:has(> .tbl-wrap) > .surface__ft',
        known: KNOWN_VIOLATIONS,
      });
    });
  }
});
