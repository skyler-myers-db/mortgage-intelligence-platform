/**
 * W5e w5-lead-table-compile on the production build at 1440x900:
 *
 *  - wow-power-5: 'Plan distribution' (a sales manager's own roster row
 *    only) loads the Distribution Board as its own chunk on the click and
 *    dry-runs the selected rows; past 500 selected it is aria-disabled with
 *    a spoken reason; a loan officer never sees it.
 *  - bundle-09 item 1: intent on 'Open bound offer' fetches the Offer route
 *    chunk and nothing from /api.
 *  - runtime-04 slice 3(b): the compiled shell renders the approve review
 *    that loads after one failed chunk load (the 5451de15 defect: a compiled
 *    shell must not memoize the chunk read), drafting exactly once. The
 *    failure is the review chunk's stylesheet dependency (a network blip
 *    Vite's preload helper reports and a second load recovers from); a
 *    failed module script itself stays failed in the browser's module map
 *    until a reload, which the "could not load ... Reload" copy says.
 */
import type { Page, Request } from '@playwright/test';
import type { LeadSummary, SessionResponse } from '../../../src/types';
import { LEADS, maskedBorrowerId, PRIMARY_BORROWER } from './data/borrowers';
import { boundSearch, registerBoundCampaign } from './data/paletteOfferSalesLoop';
import { registerBoard } from './data/salesLoop';
import { SESSION } from './data/shell';
import { json, type MockApi } from './mockApi';
import { expect, test } from './test';
import { markNaturalLoad } from './visual';

const MANAGER = 'manager@summit.example';

function asActor(mockApi: MockApi, actorEmail: string): void {
  mockApi.register<SessionResponse>('GET', '/api/session', () => json<SessionResponse>({ ...SESSION, actor_email: actorEmail }));
}

/** Script requests whose URL matches `pattern`, from now on. */
function scriptRequests(page: Page, pattern: RegExp): string[] {
  const seen: string[] = [];
  page.on('request', (request: Request) => {
    if (request.resourceType() === 'script' && pattern.test(request.url())) seen.push(request.url());
  });
  return seen;
}

test.describe('Plan distribution (wow-power-5)', () => {
  test('a manager opens the lazy Board on the selected rows; the chunk loads only on the click', async ({ app, page, mockApi }) => {
    asActor(mockApi, MANAGER);
    const bodies = registerBoard(mockApi);
    const boardChunks = scriptRequests(page, /\/assets\/DistributionBoard-[^/]+\.js$/);
    await app.gotoRoute('/lead-queue');
    const ids = LEADS.slice(2, 5).map((lead) => lead.borrower_id);
    for (const id of ids) await page.getByTestId(`lead-select-${id}`).filter({ visible: true }).check();
    const plan = page.getByTestId('lead-bulk-plan-distribution');
    await expect(plan).toBeVisible();
    await expect(plan).not.toHaveAttribute('aria-disabled');
    expect(boardChunks, 'nothing loads the Board before the click').toEqual([]);

    await plan.click();
    await expect(page.locator('dialog.distribution-board[open]')).toBeVisible();
    await expect.poll(() => boardChunks.length, 'the Board is its own chunk, fetched on the click').toBeGreaterThan(0);
    await expect.poll(() => bodies.filter((body) => body.dry_run).length).toBe(1);
    const dryRun = bodies.find((body) => body.dry_run) as unknown as { borrower_ids: string[] };
    expect([...dryRun.borrower_ids].sort()).toEqual([...ids].sort());
  });

  test('past the 500-borrower cap it is aria-disabled with a spoken reason, and opens nothing', async ({ app, page, mockApi }) => {
    asActor(mockApi, MANAGER);
    const loaded: LeadSummary[] = Array.from({ length: 501 }, (_, index) => ({
      ...LEADS[index % LEADS.length],
      borrower_id: maskedBorrowerId(4000 + index),
      approval_status: 'approved',
      outreach_status: 'none',
    }));
    mockApi.register<LeadSummary[]>('GET', '/api/leads', () => json<LeadSummary[]>(loaded, {
      headers: { 'X-Total-Matching': '501', 'X-Returned-Rows': '501' },
    }));
    const bodies = registerBoard(mockApi);
    await app.gotoRoute('/lead-queue');
    await page.locator('.lead-table__table thead input[type="checkbox"]').filter({ visible: true }).check();
    const plan = page.getByTestId('lead-bulk-plan-distribution');
    await expect(plan).toHaveAttribute('aria-disabled', 'true');
    await expect(plan).toHaveAccessibleDescription('Plan distribution takes at most 500 selected leads.');
    // aria-disabled (still focusable, so the reason is spoken): Playwright's
    // actionability check would wait on it forever, so the click is forced.
    await plan.click({ force: true });
    await expect(page.locator('dialog.distribution-board[open]')).toHaveCount(0);
    expect(bodies).toEqual([]);
  });

  test('a loan officer (a non-empty scoped roster) never sees it', async ({ app, page, mockApi }) => {
    asActor(mockApi, 'lo.alpha@summit.example');
    await app.gotoRoute('/lead-queue');
    await page.getByTestId(`lead-select-${LEADS[2].borrower_id}`).filter({ visible: true }).check();
    await expect(page.getByTestId('lead-bulk-actions')).toBeVisible();
    await expect(page.getByTestId('lead-bulk-plan-distribution')).toHaveCount(0);
  });
});

test('intent on Open bound offer fetches the Offer chunk and nothing from /api (bundle-09 item 1)', async ({ app, page, mockApi }) => {
  registerBoundCampaign(mockApi);
  const offerChunks = scriptRequests(page, /\/assets\/offer-orchestrator-[^/]+\.js$/);
  await app.gotoRoute(`/lead-queue?${boundSearch()}`);
  await app.expandFirstLeadRow();
  const link = page.getByTestId('campaign-operational-provenance').getByRole('link', { name: /Open bound offer/ });
  await expect(link).toBeVisible();
  const before = mockApi.calls.length;
  await link.hover();
  await expect.poll(() => offerChunks.length, 'the Offer route chunk on intent').toBeGreaterThan(0);
  await page.waitForTimeout(250);
  expect(mockApi.calls.slice(before).map((call) => `${call.method} ${call.path}`), 'a chunk only, never a read').toEqual([]);
});

test('the compiled shell renders the review that loads after one failed chunk load, drafting once', async ({ app, page, mockApi, hygiene }) => {
  // The failed load is this test's own doing.
  hygiene.allow('request-failed', /\/assets\/LeadApproveReview-[\w-]+\.css failed: net::ERR_FAILED/);
  hygiene.allow('console.error', /LeadApproveReview-[\w-]+\.css/);
  let failedOnce = false;
  await page.route(/\/assets\/LeadApproveReview-[^/]+\.css$/, async (route) => {
    if (!failedOnce) {
      failedOnce = true;
      await route.abort('failed');
      return;
    }
    await route.continue();
  });
  await app.gotoRoute('/lead-queue');
  const naturalLoad = markNaturalLoad(mockApi);
  const id = PRIMARY_BORROWER.borrower_id;
  const approve = page.getByTestId(`lead-approve-${id}`).filter({ visible: true });
  await approve.click();
  await expect(page.locator('[data-testid="lead-approve-review-loading"][role="alert"]')).toContainText('could not load');
  const drafts = () => mockApi.calls.filter((call) => call.method === 'POST' && call.path.endsWith('/outreach/draft')).length;
  expect(drafts(), 'a chunk that failed drafts nothing').toBe(0);

  await approve.click();
  await expect(page.getByTestId('lead-approve-review')).toBeVisible();
  await expect(page.getByTestId('lead-approve-review-subject')).not.toBeEmpty();
  expect(drafts(), 'the second Approve drafts once, and its review renders').toBe(1);
  await expect(page.locator('[data-testid="lead-approve-review-loading"]')).toHaveCount(0);
  const audited = mockApi.calls.slice(naturalLoad).filter((call) => call.path.endsWith('/outreach/draft'));
  expect(audited).toHaveLength(1);
});
