/**
 * Rendered-layer proofs for wave-3 lane w3-queue-place (audit shell-03,
 * runtime-08, tables-09 phase 1, tables-07, states-08 bulk half, review #9),
 * at 1440x900 against the built app and the mock API.
 *
 *  (a) Sort, expanded row, cursor and the table's own scroll survive
 *      Borrower 360 and Back on a 160-row (virtualized) queue, with an
 *      approval made first so the leads cache is invalidated: after Back at
 *      most the queue's own natural GET /api/leads, never a borrower, proof
 *      or draft read.
 *  (b) The same scroll restore on the 24-row (non-virtualized) queue.
 *  (c) A ?row= naming no loaded row expands nothing and reads nothing.
 *  (d) Presets: Pending approval sends approval_status; for a listed loan
 *      officer the URL holds "me" while the request holds the email; the
 *      admin session never sees the pill; aria-current marks the active one.
 *  (e) Copy link: no row, no email, no proof keys; the toast names them.
 *  (f) Shift-click range over 9 rows, a held first batch, k of 9, Stop:
 *      exactly 3 approve POSTs, none aborted, 6 not started and selected,
 *      no draft for them.  (g) a 500 is listed and stays selected.
 *  (i) A 401 mid-run stops the loop and the dialog says bulk_approval.
 *  (j) Selection pruning after a preset.
 *  (k) J from a focused checkbox moves the cursor; an Approve waiting on a
 *      delayed review chunk is dropped by J or Escape (no draft); an open
 *      inline review whose row was virtualized away is revealed by A.
 *  (l) axe on the presets row and the run's progress, light and dark.
 *
 * Holds are RequestGates or held routes, never wall-clock waits. Bulk Reject
 * (case h) is not in this lane: cut line 2 moved it to wave 4.
 */
import type { Locator, Page } from '@playwright/test';
import type { DecisionReceipt } from '../../../src/lib/apiTypes';
import type { LeadSummary } from '../../../src/types';
import type { FixtureTheme } from './app';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { ledgerReceipt } from './data/decisionReceipt';
import { VIRTUAL_QUEUE, registerDraftEcho } from './data/queueKeyboard';
import { LO_EMAIL, LO_SESSION, registerBulkApprove, registerRankedQueue } from './data/queuePlace';
import { json, type MockApi } from './mockApi';
import { expect, test } from './test';
import { auditedReadsAfter } from './visual';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

function tableWrap(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

function leadReads(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/leads').length;
}

function lastLeadsSearch(mockApi: MockApi): string {
  const reads = mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/leads');
  return reads[reads.length - 1]?.search ?? '';
}

function registerReceiptRead(mockApi: MockApi): void {
  mockApi.register<DecisionReceipt>('GET', '/api/audit/receipt/:id', ({ params }) =>
    json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')),
  );
}

/** Rows that are pending, contactable and consented: selectable AND approvable. */
function approvable(rows: readonly LeadSummary[]): LeadSummary[] {
  return rows.filter((lead) => (
    lead.approval_status === 'pending'
    && lead.marketing_eligible !== false
    && lead.dnc !== true
    && (lead.consent_status ?? 'opt_in') === 'opt_in'
  ));
}

/** A 12-row queue of approvable rows, in rank order (real dossier ids). */
const BULK_QUEUE: readonly LeadSummary[] = approvable(LEADS).slice(0, 12);
const BULK_IDS = BULK_QUEUE.slice(0, 9).map((lead) => lead.borrower_id);

async function scrollTopOf(page: Page): Promise<number> {
  return tableWrap(page).evaluate((element) => element.scrollTop);
}

/** Select row 0 with a plain click, then Shift-click row 8: a 9-row range. */
async function selectNineByRange(page: Page): Promise<void> {
  await page.getByTestId(`lead-select-${BULK_IDS[0]}`).click();
  await page.getByTestId(`lead-select-${BULK_IDS[8]}`).click({ modifiers: ['Shift'] });
  await expect(page.locator('.bulk-actions__label')).toHaveText('9 leads selected');
}

async function startBulkApprove(page: Page): Promise<void> {
  const approve = page.getByTestId('lead-bulk-approve');
  await approve.click();
  await page.locator('.bulk-actions__rationale input').fill('Q3 retention sweep');
  await approve.click();
}

test.describe('(a)-(c) the reader keeps their place', () => {
  test('(a) sort, expanded row, cursor and table scroll survive Borrower 360 and Back on a virtualized queue', async ({ app, mockApi, page }) => {
    test.slow();
    mockApi.register<LeadSummary[]>('GET', '/api/leads', () => json<LeadSummary[]>([...VIRTUAL_QUEUE], {
      headers: { 'X-Total-Matching': String(VIRTUAL_QUEUE.length), 'X-Returned-Rows': String(VIRTUAL_QUEUE.length) },
    }));
    const echo = registerDraftEcho(mockApi);
    registerBulkApprove(mockApi);
    registerReceiptRead(mockApi);
    await app.gotoRoute('/lead-queue');

    // Sort by Equity: the URL gets sort and dir; no /api/leads call follows.
    const readsBeforeSort = leadReads(mockApi);
    await page.getByRole('button', { name: 'Sort by Equity' }).click();
    await expect(page).toHaveURL(/[?&]sort=equity&dir=desc(&|$)/);
    expect(leadReads(mockApi), 'a sort is not a new query').toBe(readsBeforeSort);

    // The on-screen order (a stable sort, like the table's) and a real
    // dossier row about 2,000px down.
    const onScreen = [...VIRTUAL_QUEUE].sort((a, b) => b.equity_estimate - a.equity_estimate);
    const realIds = new Set(LEADS.map((lead) => lead.borrower_id));
    const targetIndex = onScreen.findIndex((lead, index) => index >= 40 && realIds.has(lead.borrower_id) && lead.approval_status === 'pending');
    expect(targetIndex, 'precondition: a real dossier row sits deep in the sorted queue').toBeGreaterThan(0);
    const target = onScreen[targetIndex].borrower_id;

    // Invalidate the leads cache first: approve a DIFFERENT row (the first
    // pending row on screen) through its review.
    const other = onScreen.find((lead) => lead.approval_status === 'pending' && realIds.has(lead.borrower_id) && lead.borrower_id !== target);
    expect(other).toBeDefined();
    await page.getByTestId(`lead-approve-${other!.borrower_id}`).click();
    const confirm = page.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect(page.getByTestId(`lead-approval-cell-${other!.borrower_id}`).locator('.chip--success')).toBeVisible();

    // Bring the target in, expand it: the URL gets row, no new history entry.
    await tableWrap(page).evaluate((element, top) => {
      element.scrollTop = top;
    }, targetIndex * 44 - 120);
    const targetRow = page.locator(`tr[data-borrower-row="${target}"]`);
    await expect(targetRow).toBeVisible();
    const historyBefore = await page.evaluate(() => window.history.length);
    await targetRow.getByRole('button', { name: `Toggle preview for lead ${target}` }).click();
    await expect(page).toHaveURL(new RegExp(`[?&]row=${target}(&|$)`));
    expect(await page.evaluate(() => window.history.length), 'expand replaces, it never pushes').toBe(historyBefore);

    // About 2,000px down, with the dossier link on screen.
    const link = page.locator(`tr.tbl__expand a[href="/borrower-360/${target}"]`);
    await tableWrap(page).evaluate((element, rowSelector) => {
      const row = element.querySelector<HTMLElement>(rowSelector);
      if (row) element.scrollTop = row.offsetTop - 60;
    }, `tr[data-borrower-row="${target}"]`);
    await link.scrollIntoViewIfNeeded();
    const savedTop = await scrollTopOf(page);
    expect(savedTop, 'precondition: the table is scrolled well down').toBeGreaterThan(1500);

    await link.click();
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${target}$`));
    await app.settle();
    const beforeBack = mockApi.calls.length;
    await page.goBack();
    await app.settle();

    await expect(page).toHaveURL(new RegExp(`/lead-queue\\?.*sort=equity&dir=desc&row=${target}`));
    await expect(page.locator('th[aria-sort="descending"]')).toContainText('Equity');
    await expect(page.locator(`tr.is-expanded[data-borrower-row="${target}"]`)).toBeVisible();
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', target);
    await expect.poll(async () => Math.abs((await scrollTopOf(page)) - savedTop), 'table scroll restored').toBeLessThanOrEqual(44);

    const audited = auditedReadsAfter(mockApi.calls, beforeBack);
    expect(audited.every((read) => read.startsWith('GET /api/leads')), audited.join('\n')).toBe(true);
    expect(audited.length, 'at most the queue\'s own natural read').toBeLessThanOrEqual(1);
    expect(echo.calls, 'one draft: the approve review\'s, none on restore').toEqual([other!.borrower_id]);
  });

  test('(b) the 24-row queue restores its scroll with scrollTop', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await expect(page.locator('table.tbl tbody tr[data-borrower-row]')).toHaveCount(LEADS.length);
    const target = LEADS[6].borrower_id;
    await page.getByRole('button', { name: `Toggle preview for lead ${target}` }).click();
    await expect(page).toHaveURL(new RegExp(`[?&]row=${target}(&|$)`));
    const link = page.locator(`tr.tbl__expand a[href="/borrower-360/${target}"]`);
    await link.scrollIntoViewIfNeeded();
    const savedTop = await scrollTopOf(page);
    expect(savedTop, 'precondition: the table scrolled').toBeGreaterThan(100);

    await link.click();
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${target}$`));
    await app.settle();
    await page.goBack();
    await app.settle();

    await expect(page.locator(`tr.is-expanded[data-borrower-row="${target}"]`)).toBeVisible();
    await expect.poll(async () => Math.abs((await scrollTopOf(page)) - savedTop)).toBeLessThanOrEqual(44);
  });

  test('(c) a row param for an id that is not loaded expands nothing and reads nothing', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/lead-queue?row=B-ZZZZZZZZZZZZZ');
    await expect(page.locator('table.tbl tbody tr[data-borrower-row]').first()).toBeVisible();
    await expect(page.locator('tr.tbl__expand')).toHaveCount(0);
    const audited = auditedReadsAfter(mockApi.calls, 0);
    expect(audited).toHaveLength(1);
    expect(audited[0]).toMatch(/^GET \/api\/leads/);
    expect(audited[0], 'the place never reaches the request').not.toContain('row=');
  });
});

test.describe('(d)-(e) preset views and Copy link', () => {
  test('(d) Pending approval sends approval_status; the admin session never sees Assigned to me', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/lead-queue');
    const presets = page.getByRole('group', { name: 'Queue presets' });
    await expect(presets.getByRole('link', { name: 'All' })).toHaveAttribute('aria-current', 'true');
    await expect(presets.getByRole('link', { name: 'Assigned to me' })).toHaveCount(0);

    await presets.getByRole('link', { name: 'Pending approval' }).click();
    await expect(page).toHaveURL(/[?&]approval_status=pending(&|$)/);
    await expect(presets.getByRole('link', { name: 'Pending approval' })).toHaveAttribute('aria-current', 'true');
    await expect(presets.getByRole('link', { name: 'All' })).not.toHaveAttribute('aria-current', 'true');
    await expect.poll(() => lastLeadsSearch(mockApi)).toContain('approval_status=pending');
  });

  test('(d) for a listed loan officer the URL holds "me" while the request holds the email', async ({ app, mockApi, page }) => {
    mockApi.register('GET', '/api/session', () => json(LO_SESSION));
    await app.gotoRoute('/lead-queue');
    const presets = page.getByRole('group', { name: 'Queue presets' });
    await presets.getByRole('link', { name: 'Assigned to me' }).click();

    await expect(page).toHaveURL(/[?&]assigned_to=me(&|$)/);
    expect(page.url()).not.toContain('%40');
    await expect.poll(() => lastLeadsSearch(mockApi)).toContain(`assigned_to=${encodeURIComponent(LO_EMAIL)}`);
    await expect(presets.getByRole('link', { name: 'Assigned to me' })).toHaveAttribute('aria-current', 'true');
    await expect(page.getByRole('group', { name: 'Active filters' })).toContainText('Me');
  });

  test('(e) Copy link carries no row, no email and no proof, and says what it left out', async ({ app, page }) => {
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    const row = LEADS[0].borrower_id;
    // A Growth Agent handoff's proof key rides in the address bar too: the
    // copied link must drop it (the recipient's queue is re-verified).
    await app.gotoRoute(
      `/lead-queue?sort=equity&dir=asc&row=${row}&assigned_to=lo.bravo%40summit.example`
      + '&growth_agent_run_id=11111111-1111-4111-8111-111111111111',
    );
    expect(page.url(), 'precondition: the proof key is in the address bar').toContain('growth_agent_run_id=');
    await page.getByTestId('lead-queue-copy-link').click();
    await expect(page.getByText('Queue link copied')).toBeVisible();
    await expect(page.getByText('Left out: the open row, the assignee email, the Growth Agent proof.')).toBeVisible();

    const copied = await page.evaluate(() => navigator.clipboard.readText());
    const origin = new URL(page.url()).origin;
    expect(copied).toBe(`${origin}/lead-queue?sort=equity&dir=asc`);
    expect(copied).not.toContain('row=');
    expect(copied).not.toContain('@');
    expect(copied).not.toContain('%40');
    expect(copied).not.toMatch(/growth_agent|tool_result_hash|actionable_/);
  });
});

test.describe('(f)-(j) bulk selection and honest runs', () => {
  test('(f) Shift range over 9 rows, a held batch, k of 9, then Stop: 3 POSTs, none aborted, 6 not started', async ({ app, mockApi, page }) => {
    registerRankedQueue(mockApi, BULK_QUEUE);
    const echo = registerDraftEcho(mockApi);
    const tracker = registerBulkApprove(mockApi, { holdFirst: 3 });
    const failed: string[] = [];
    page.on('requestfailed', (request) => failed.push(`${request.method()} ${request.url()} ${request.failure()?.errorText ?? ''}`));
    await app.gotoRoute('/lead-queue');
    await selectNineByRange(page);
    await startBulkApprove(page);

    await expect.poll(() => tracker.bodies.length).toBe(3);
    await expect(page.getByTestId('lead-bulk-approve')).toHaveText('Approving…');
    await expect(page.getByTestId('lead-bulk-run-count')).toHaveText('0 of 9');
    await expect(page.locator('progress.bulk-actions__progress')).toHaveAttribute('max', '9');
    await expect(page.getByTestId('lead-bulk-run-eta')).toHaveText(/about \d+ min left/);

    await page.getByTestId('lead-bulk-stop').click();
    await expect(page.getByTestId('lead-bulk-stop')).toHaveText('Stopping after this batch…');
    tracker.gate.release();

    const result = page.getByTestId('lead-bulk-result');
    await expect(result).toContainText('3 of 9 approved, 6 not started. Stopped.');
    expect(tracker.bodies.map((body) => body.borrower_id)).toEqual(BULK_IDS.slice(0, 3));
    expect(new Set(tracker.bodies.map((body) => body.request_id)).size).toBe(3);
    expect(echo.calls.sort(), 'drafts only for the rows that started').toEqual(BULK_IDS.slice(0, 3).sort());
    expect(failed, 'no request was aborted').toEqual([]);
    await expect(page.locator('.bulk-actions__label')).toHaveText('6 leads selected');
    for (const id of BULK_IDS.slice(3)) await expect(page.getByTestId(`lead-select-${id}`)).toBeChecked();
  });

  test('(g) an approve that returns 500 is listed and stays selected', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 500/);
    registerRankedQueue(mockApi, BULK_QUEUE);
    registerDraftEcho(mockApi);
    const tracker = registerBulkApprove(mockApi, { failIds: [BULK_IDS[4]] });
    await app.gotoRoute('/lead-queue');
    await selectNineByRange(page);
    await startBulkApprove(page);

    const result = page.getByTestId('lead-bulk-result');
    await expect(result).toContainText('8 of 9 approved, 1 failed.');
    await result.locator('summary').click();
    await expect(result.locator(`li[data-outcome="backend"]`)).toContainText(BULK_IDS[4]);
    expect(tracker.bodies).toHaveLength(9);
    await expect(page.locator('.bulk-actions__label')).toHaveText('1 lead selected');
    await expect(page.getByTestId(`lead-select-${BULK_IDS[4]}`)).toBeChecked();
  });

  test('(i) a 401 mid-run stops the loop and the dialog says a bulk approval was partly recorded', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 401/);
    registerRankedQueue(mockApi, BULK_QUEUE);
    registerDraftEcho(mockApi);
    const tracker = registerBulkApprove(mockApi, { expireOn: BULK_IDS[1] });
    await app.gotoRoute('/lead-queue');
    await selectNineByRange(page);
    await startBulkApprove(page);

    const warn = page.locator('dialog.session-dialog [data-session-unrecorded]');
    await expect(warn).toHaveAttribute('data-session-unrecorded', 'bulk_approval');
    await expect(warn).toContainText('Rows already approved stay approved; the rest were not recorded.');
    expect(tracker.bodies.map((body) => body.borrower_id), 'the loop stopped after that batch').toEqual(BULK_IDS.slice(0, 3));
  });

  test('(j) a preset prunes the selection to the rows still on screen', async ({ app, mockApi, page }) => {
    const approved = { ...BULK_QUEUE[2], approval_status: 'approved' } as LeadSummary;
    const rows = BULK_QUEUE.map((lead, index) => (index === 2 ? approved : lead));
    registerRankedQueue(mockApi, rows);
    await app.gotoRoute('/lead-queue');
    for (const lead of rows.slice(0, 3)) await page.getByTestId(`lead-select-${lead.borrower_id}`).click();
    await expect(page.locator('.bulk-actions__label')).toHaveText('3 leads selected');

    await page.getByRole('group', { name: 'Queue presets' }).getByRole('link', { name: 'Pending approval' }).click();
    await expect(page.locator(`tr[data-borrower-row="${approved.borrower_id}"]`)).toHaveCount(0);
    await expect(page.locator('.bulk-actions__label')).toHaveText('2 leads selected');
    await expect(page.getByTestId('lead-bulk-approve')).toHaveText('Approve 2 eligible');
  });
});

test.describe('(k) keyboard residuals (#9)', () => {
  test('J from a focused row checkbox moves the cursor', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await page.getByTestId(`lead-select-${LEADS[0].borrower_id}`).focus();
    await page.keyboard.press('j');
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', LEADS[0].borrower_id);
    await page.keyboard.press('j');
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveAttribute('data-borrower-row', LEADS[1].borrower_id);
  });

  test('an Approve waiting on a delayed review chunk is dropped by J and by Escape: no draft', async ({ app, mockApi, page }) => {
    registerRankedQueue(mockApi, BULK_QUEUE);
    const echo = registerDraftEcho(mockApi);
    let releaseChunk: () => void = () => undefined;
    const chunkGate = new Promise<void>((resolve) => {
      releaseChunk = resolve;
    });
    let chunkRequested = false;
    await page.route('**/assets/LeadApproveReview-*.js', async (route) => {
      chunkRequested = true;
      await chunkGate;
      await route.continue();
    });
    await app.gotoRoute('/lead-queue');
    await tableWrap(page).focus();
    await page.keyboard.press('j');
    await expect.poll(() => chunkRequested, 'the cursor asked for the review chunk').toBe(true);

    const opening = page.getByTestId('lead-approve-review-loading');
    await page.keyboard.press('a');
    await expect(opening).toHaveText(`Opening the review for ${BULK_IDS[0]}…`);
    await page.keyboard.press('j');
    await expect(opening).toHaveCount(0);

    await page.keyboard.press('a');
    await expect(opening).toHaveText(`Opening the review for ${BULK_IDS[1]}…`);
    await page.keyboard.press('Escape');
    await expect(opening).toHaveCount(0);

    const landed = page.waitForResponse((response) => /\/assets\/LeadApproveReview-.*\.js$/.test(response.url()));
    releaseChunk();
    await landed;
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(echo.calls, 'a dropped Approve drafts nothing').toEqual([]);
    expect(mockApi.calls.filter((call) => call.path === '/api/outreach/draft')).toHaveLength(0);

    // Control: with the chunk loaded, A drafts for the cursor row.
    await tableWrap(page).focus();
    await page.keyboard.press('a');
    await expect.poll(() => echo.calls).toEqual([BULK_IDS[1]]);
  });

  test('A on an open inline review whose row was virtualized away brings it back and focuses Confirm', async ({ app, mockApi, page }) => {
    mockApi.register<LeadSummary[]>('GET', '/api/leads', () => json<LeadSummary[]>([...VIRTUAL_QUEUE], {
      headers: { 'X-Total-Matching': String(VIRTUAL_QUEUE.length), 'X-Returned-Rows': String(VIRTUAL_QUEUE.length) },
    }));
    const echo = registerDraftEcho(mockApi);
    await app.gotoRoute('/lead-queue');
    const first = VIRTUAL_QUEUE[0].borrower_id;
    await tableWrap(page).focus();
    await page.keyboard.press('j');
    await page.keyboard.press('Enter');
    await page.keyboard.press('a');
    const confirm = page.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeEnabled();

    await tableWrap(page).evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect(page.locator(`tr[data-borrower-row="${first}"]`)).toHaveCount(0);
    await tableWrap(page).focus();
    await page.keyboard.press('a');

    await expect(page.locator(`tr[data-borrower-row="${first}"]`)).toBeVisible();
    await expect(confirm).toBeFocused();
    expect(echo.calls, 'the same review: no second draft').toEqual([first]);
  });
});

test.describe('(l) axe on the new surfaces', () => {
  for (const theme of THEMES) {
    test(`${theme}: the presets row and a run's progress are clean`, async ({ app, mockApi, page }) => {
      registerRankedQueue(mockApi, BULK_QUEUE);
      registerDraftEcho(mockApi);
      const tracker = registerBulkApprove(mockApi, { holdFirst: 3 });
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await expectAxeClean(page, {
        key: { route: 'lead-queue', state: 'presets' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: '[aria-label="Queue presets"]',
      });

      await selectNineByRange(page);
      await startBulkApprove(page);
      await expect.poll(() => tracker.bodies.length).toBe(3);
      await expect(page.getByTestId('lead-bulk-run')).toBeVisible();
      await expectAxeClean(page, {
        key: { route: 'lead-queue', state: 'bulk-run' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: '[data-testid="lead-bulk-actions"]',
      });
      tracker.gate.release();
      await expect(page.getByTestId('lead-bulk-run')).toHaveCount(0);
    });
  }
});
