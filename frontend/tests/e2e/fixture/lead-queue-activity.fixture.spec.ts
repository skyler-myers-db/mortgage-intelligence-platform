/**
 * The Lead Queue keep-alive slot, at the rendered layer (W5c, audit
 * runtime-08; w5b_rulings[3]). app.tsx renders the queue under <Activity>
 * outside the keyed route boundary, so a Borrower 360 visit HIDES it rather
 * than unmounting it. Every served /api/leads page writes a VIEW_LEADS
 * audit row, so the return must read nothing:
 *
 *   - queue -> dossier -> Back, and queue -> dossier -> the Leads nav link:
 *     the sort, the expanded ?row=, the selection, two loaded pages and the
 *     table's scroll offset survive, with exactly 0 /api/leads GETs (the
 *     mockApi call log) and one painted route marker throughout;
 *   - the hidden queue's effects are unmounted: J/K on Borrower 360 step the
 *     dossier pager, and neither they nor A/R move, open or decide anything
 *     in the hidden queue.
 */
import type { Locator, Page } from '@playwright/test';
import type { LeadSummary } from '../../../src/types';
import { LEADS } from './data/borrowers';
import { PAGED_QUEUE, registerPagedQueue, serverOrder } from './data/leadPages';
import { registerDraftEcho } from './data/queueKeyboard';
import { registerBulkApprove } from './data/queuePlace';
import type { MockApi } from './mockApi';
import { expect, test } from './test';

const REAL_IDS = new Set(LEADS.map((lead) => lead.borrower_id));

/** A row with a selection checkbox: pending, contactable and consented. */
function selectable(lead: LeadSummary): boolean {
  return lead.approval_status === 'pending'
    && lead.marketing_eligible !== false
    && lead.dnc !== true
    && (lead.consent_status ?? 'opt_in') === 'opt_in';
}

function tableWrap(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

function footer(page: Page): Locator {
  return page.locator('.surface:has(> .tbl-wrap) > .surface__ft');
}

function leadReads(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/leads').length;
}

async function paintedMarkers(page: Page): Promise<string[]> {
  return page.locator('#main-content .route-transition[data-route-path]').evaluateAll(
    (nodes) => nodes.map((node) => node.getAttribute('data-route-path') ?? ''),
  );
}

/**
 * Sort by Equity (one page-0 read in that order), load a second page,
 * select one row, expand a real dossier row about 1,500px down the table,
 * and return what the reader's place is.
 */
async function buildPlace(page: Page, mockApi: MockApi): Promise<{ target: string; selected: string; scrollTop: number }> {
  await page.getByRole('button', { name: 'Sort by Equity' }).click();
  await expect(page).toHaveURL(/[?&]sort=equity&dir=desc(&|$)/);
  await page.getByTestId('lead-load-next').click();
  await expect(footer(page)).toContainText('Showing 1,000 ranked borrowers of 1,284');

  const ordered: LeadSummary[] = serverOrder(PAGED_QUEUE, 'equity', 'desc').slice(0, 1_000);
  const targetIndex = ordered.findIndex((lead, index) => index >= 30 && REAL_IDS.has(lead.borrower_id));
  expect(targetIndex, 'precondition: a real dossier row sits down the sorted queue').toBeGreaterThan(0);
  const target = ordered[targetIndex].borrower_id;
  const selectedLead = ordered.slice(targetIndex - 3, targetIndex).reverse().find(selectable);
  expect(selectedLead, 'precondition: a selectable row sits just above the target').toBeDefined();
  const selected = selectedLead!.borrower_id;

  await tableWrap(page).evaluate((element, top) => {
    element.scrollTop = top;
  }, Math.max(0, targetIndex * 44 - 160));
  const row = page.locator(`tr[data-borrower-row="${target}"]`);
  await expect(row).toBeVisible();
  await page.getByTestId(`lead-select-${selected}`).check();
  await row.getByRole('button', { name: `Toggle preview for lead ${target}` }).click();
  await expect(page).toHaveURL(new RegExp(`[?&]row=${target}(&|$)`));
  await page.locator(`tr.tbl__expand a[href="/borrower-360/${target}"]`).scrollIntoViewIfNeeded();
  const scrollTop = await tableWrap(page).evaluate((element) => element.scrollTop);
  expect(scrollTop, 'precondition: the table is scrolled well down').toBeGreaterThan(800);
  expect(leadReads(mockApi), 'rank page 0, equity page 0, equity page 1').toBe(3);
  return { target, selected, scrollTop };
}

async function expectPlaceKept(page: Page, place: { target: string; selected: string; scrollTop: number }): Promise<void> {
  await expect(page).toHaveURL(new RegExp(`/lead-queue\\?.*sort=equity&dir=desc.*row=${place.target}`));
  await expect(page.locator('th[aria-sort="descending"]')).toContainText('Equity');
  await expect(footer(page), 'both loaded pages').toContainText('Showing 1,000 ranked borrowers of 1,284');
  await expect(page.locator(`tr.is-expanded[data-borrower-row="${place.target}"]`)).toBeVisible();
  await expect(page.getByTestId(`lead-select-${place.selected}`)).toBeChecked();
  await expect(page.locator('.bulk-actions__label')).toHaveText('1 lead selected');
  await expect.poll(async () => Math.abs((await tableWrap(page).evaluate((element) => element.scrollTop)) - place.scrollTop),
    'the table scroll offset').toBeLessThanOrEqual(44);
  expect(await paintedMarkers(page)).toEqual(['/lead-queue']);
}

test.describe('the Lead Queue survives a dossier visit with no re-read', () => {
  test('queue -> dossier -> Back', async ({ app, mockApi, page }) => {
    test.slow();
    registerPagedQueue(mockApi);
    await app.gotoRoute('/lead-queue');
    const place = await buildPlace(page, mockApi);

    await page.locator(`tr.tbl__expand a[href="/borrower-360/${place.target}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${place.target}$`));
    await app.settle();
    expect(await paintedMarkers(page), 'the hidden queue names no route').toEqual([`/borrower-360/${place.target}`]);
    const before = leadReads(mockApi);

    await page.goBack();
    await app.settle();
    await expectPlaceKept(page, place);
    expect(leadReads(mockApi), 'Back reads no /api/leads page').toBe(before);
  });

  test('queue -> dossier -> the Leads nav link', async ({ app, mockApi, page }) => {
    test.slow();
    registerPagedQueue(mockApi);
    await app.gotoRoute('/lead-queue');
    const place = await buildPlace(page, mockApi);

    await page.locator(`tr.tbl__expand a[href="/borrower-360/${place.target}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${place.target}$`));
    await app.settle();
    const before = leadReads(mockApi);

    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Leads' }).click();
    await app.settle();
    await expectPlaceKept(page, place);
    expect(leadReads(mockApi), 'the Leads link reveals the kept queue: no read').toBe(before);
  });

  test('J/K/A/R on Borrower 360 never reach the hidden queue', async ({ app, mockApi, page }) => {
    test.slow();
    registerPagedQueue(mockApi);
    const echo = registerDraftEcho(mockApi);
    const approvals = registerBulkApprove(mockApi);
    await app.gotoRoute('/lead-queue');
    const place = await buildPlace(page, mockApi);
    const cursorRow = await page.locator('table.tbl tr.is-cursor').getAttribute('data-borrower-row');

    await page.locator(`tr.tbl__expand a[href="/borrower-360/${place.target}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${place.target}$`));
    await app.settle();
    const before = leadReads(mockApi);
    await page.locator('#main-content h1').first().click();
    await page.keyboard.press('a');
    await page.keyboard.press('r');
    // J steps the dossier pager to the next loaded borrower; K steps back.
    await page.keyboard.press('j');
    await expect(page).not.toHaveURL(new RegExp(`/borrower-360/${place.target}$`));
    await app.settle();
    await page.keyboard.press('k');
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${place.target}$`));
    await app.settle();

    expect(approvals.bodies, 'nothing approved').toEqual([]);
    // Back through the pager's dossier entries to the queue.
    for (let step = 0; step < 4 && !new URL(page.url()).pathname.startsWith('/lead-queue'); step += 1) {
      await page.goBack();
      await app.settle();
    }
    await expectPlaceKept(page, place);
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', cursorRow ?? place.target);
    await expect(page.locator('dialog.lead-approve-dialog'), 'the hidden queue opened no review').toHaveCount(0);
    await expect(page.locator('[data-testid="lead-approve-review-confirm"]')).toHaveCount(0);
    expect(echo.calls, 'the hidden queue drafted nothing').toEqual([]);
    expect(leadReads(mockApi)).toBe(before);
  });
});
