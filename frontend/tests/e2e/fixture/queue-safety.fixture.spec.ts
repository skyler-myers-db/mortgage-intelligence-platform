/**
 * Rendered-layer proofs for the wave-0 Lead Queue safety work (audit
 * tables-v2 / a11y-09 hotkey scope, flow-02 / shell-06 approver gate,
 * states-v1 warm-up, tables-02 sort scope, tables-08 CSV export).
 *
 * Writes are registered per test so the test owns their timing: the
 * pessimistic-approval proof holds the approve reply and asserts the row
 * never claims "Approved" before it returns.
 */
import fs from 'node:fs';
import type { Locator, Page } from '@playwright/test';
import type { ApproveResult } from '../../../src/lib/apiTypes';
import type { SessionResponse } from '../../../src/types';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { WAREHOUSE_WARMING_UP, type MockApi } from './mockApi';
import { expect, test } from './test';

const APPROVER_EMAIL = 'approver@summit-mortgage.example';

function approveCalls(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && call.path === '/api/outreach/approve').length;
}
function draftCalls(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && call.path === '/api/outreach/draft').length;
}

interface HeldApprove {
  /** Approve POSTs RECEIVED (the mock's call log only records answered calls). */
  readonly received: number;
  release(): void;
}

/** Register an approve reply the test releases by hand. */
function registerHeldApprove(mockApi: MockApi): HeldApprove {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let received = 0;
  mockApi.register<ApproveResult>('POST', '/api/outreach/approve', async (request) => {
    received += 1;
    await gate;
    const body = request.body as { borrower_id?: string } | null;
    return { body: { approved: true, approval_id: 'apr-fixture-0001', audit_event_id: `audit-${body?.borrower_id ?? 'unknown'}` } };
  });
  return {
    get received() {
      return received;
    },
    release,
  };
}

function approvalCell(page: Page, borrowerId: string): Locator {
  return page.getByTestId(`lead-approval-cell-${borrowerId}`);
}

test.describe('A / R hotkeys are scoped to focus inside the table', () => {
  test('A on a filter button or inside the evidence drawer approves nothing; A inside the table approves once, pessimistically', async ({ app, mockApi, page }) => {
    const held = registerHeldApprove(mockApi);
    await app.gotoRoute('/lead-queue');
    const id = PRIMARY_BORROWER.borrower_id;
    await app.expandFirstLeadRow();
    const cell = approvalCell(page, id);
    await expect(cell.getByRole('button', { name: `Approve ${id}` })).toHaveText('Approve');

    // 1. Focus on a filter button (outside .tbl-wrap).
    const stateFilter = page.locator('button[aria-haspopup="listbox"][aria-label^="STATE:"]').first();
    await stateFilter.focus();
    await expect(stateFilter).toBeFocused();
    await page.keyboard.press('a');
    await expect(cell.getByRole('button', { name: `Approve ${id}` })).toHaveText('Approve');
    expect(draftCalls(mockApi)).toBe(0);
    expect(approveCalls(mockApi)).toBe(0);

    // 2. Focus inside the open evidence drawer (a dialog is open). The chip
    //    in the expanded preview keeps the row expanded (a chip in the main
    //    row would toggle it closed on the bubbled click).
    const drawer = await app.openEvidenceDrawer(page.locator('table.tbl tbody tr.tbl__expand .evidence-chip').first());
    await drawer.getByRole('tab', { name: 'Overview' }).focus();
    await page.keyboard.press('a');
    await expect(cell.getByRole('button', { name: `Approve ${id}` })).toHaveText('Approve');
    expect(draftCalls(mockApi)).toBe(0);
    expect(approveCalls(mockApi)).toBe(0);
    await drawer.getByRole('button', { name: 'Close drawer' }).click();
    await expect(drawer).not.toHaveClass(/is-open/);
    await expect(page.locator('table.tbl tbody tr.tbl__expand'), 'the row is still expanded').toHaveCount(1);

    // 3. Focus inside the table scroll region: in scope.
    await page.getByRole('region', { name: 'Ranked borrowers table scroll region' }).focus();
    await page.keyboard.press('a');
    const approveButton = cell.getByRole('button', { name: `Approve ${id}` });
    await expect(approveButton).toHaveText('Approving…');
    await expect(approveButton).toBeDisabled();
    await expect.poll(() => held.received, 'exactly one approve POST left the browser').toBe(1);
    expect(draftCalls(mockApi)).toBe(1);
    // The reply is still held: no "Approved" chip may exist yet.
    await expect(cell).not.toContainText('Approved');
    await expect(cell.locator('.chip--success')).toHaveCount(0);
    expect(approveCalls(mockApi), 'the approve reply has not returned').toBe(0);
    // A second A while the row is in flight is a duplicate, not a second POST.
    await page.keyboard.press('a');
    expect(held.received).toBe(1);

    held.release();
    await expect(cell.locator('.chip--success')).toHaveText(/Approved/);
    expect(approveCalls(mockApi)).toBe(1);
    expect(held.received).toBe(1);
  });
});

test.describe('approver gate', () => {
  test('a session without can_approve sees disabled, explained controls and A does nothing', async ({ app, mockApi, page }) => {
    mockApi.register<SessionResponse>('GET', '/api/session', () => ({
      body: { can_access_admin: true, can_approve: false, actor_email: 'analyst@summit-mortgage.example' },
    }));
    await app.gotoRoute('/lead-queue');
    const id = PRIMARY_BORROWER.borrower_id;
    await app.expandFirstLeadRow();
    const approve = page.getByTestId(`lead-approve-${id}`);
    const reject = page.getByTestId(`lead-reject-${id}`);
    await expect(approve).toBeDisabled();
    await expect(reject).toBeDisabled();
    await expect(approve).toHaveAttribute('title', 'Requires approver role');
    await expect(reject).toHaveAttribute('title', 'Requires approver role');
    await expect(page.getByTestId('lead-approving-as')).toHaveCount(0);

    await page.getByTestId(`lead-select-${id}`).check();
    const bulk = page.getByTestId('lead-bulk-approve');
    await expect(bulk).toHaveText(/^Approve \d+ eligible$/);
    await expect(bulk).toBeDisabled();
    await expect(bulk).toHaveAttribute('title', /Requires approver role/);

    await page.getByRole('region', { name: 'Ranked borrowers table scroll region' }).focus();
    await page.keyboard.press('a');
    await page.keyboard.press('r');
    await expect(approve).toHaveText('Approve');
    expect(draftCalls(mockApi)).toBe(0);
    expect(approveCalls(mockApi)).toBe(0);
    await expect(page.locator('.table-error')).toHaveCount(0);

    // Offer Orchestrator's banner explains the gate and never claims an approver.
    await app.gotoRoute(`/offer-orchestrator/${id}`);
    const banner = page.getByRole('region', { name: 'Approval queue' });
    await expect(banner.getByTestId('approval-gate-reason')).toContainText('Requires approver role');
    await expect(banner).not.toContainText('Approving as');
    await expect(banner.getByRole('button', { name: /Approve/ })).toBeDisabled();
  });

  test('an approver session is named on both surfaces', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await expect(page.getByTestId('lead-approving-as')).toHaveText(APPROVER_EMAIL);
    await app.gotoRoute(`/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`);
    const banner = page.getByRole('region', { name: 'Approval queue' });
    await expect(banner.getByTestId('approval-actor')).toHaveText(`Approving as ${APPROVER_EMAIL}`);
    await expect(banner.getByTestId('approval-gate-reason')).toHaveCount(0);
  });
});

test.describe('warm-up', () => {
  test('a warehouse that is warming up never renders "Showing 0"', async ({ app, page }) => {
    app.degrade('/api/leads', WAREHOUSE_WARMING_UP);
    await page.goto('/lead-queue', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('warming-up-block')).toBeVisible();
    await expect(page.getByTestId('warming-up-attempt')).toContainText(/attempt \d+ of \d+/);
    await expect(page.locator('#main-content')).not.toContainText('Showing 0');
    await expect(page.locator('table.tbl')).toHaveCount(0);
  });
});

test.describe('column sort scope', () => {
  test('sorting by Equity says it covers only the loaded rows, and Reset to rank restores rank order', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const firstId = page.locator('table.tbl tbody .lead-table__borrower').first();
    await expect(firstId).toHaveText(LEADS[0].borrower_id);
    await expect(page.getByTestId('lead-sort-scope')).toHaveCount(0);

    await page.getByRole('button', { name: 'Sort by Equity' }).click();
    const byEquity = [...LEADS].sort((a, b) => b.equity_estimate - a.equity_estimate)[0];
    await expect(firstId).toHaveText(byEquity.borrower_id);
    await expect(page.getByTestId('lead-sort-scope')).toContainText('sorted within the loaded');
    await expect(page.locator('th[aria-sort="descending"]')).toContainText('Equity');

    await page.getByTestId('lead-sort-reset').click();
    await expect(firstId).toHaveText(LEADS[0].borrower_id);
    await expect(page.getByTestId('lead-sort-scope')).toHaveCount(0);
  });
});

test.describe('CSV export', () => {
  test('exports exactly the selected rows and says so', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const [first, second] = LEADS.filter((lead) => lead.approval_status === 'pending').slice(0, 2);
    await page.getByTestId(`lead-select-${first.borrower_id}`).check();
    await page.getByTestId(`lead-select-${second.borrower_id}`).check();
    const exportButton = page.getByTestId('lead-export');
    await expect(exportButton).toHaveText('Export 2 selected');
    await expect(exportButton).toHaveAttribute('aria-label', 'Export 2 selected as CSV');

    const downloading = page.waitForEvent('download');
    await exportButton.click();
    const download = await downloading;
    expect(download.suggestedFilename()).toMatch(/^mip-leads-\d{4}-\d{2}-\d{2}\.csv$/);
    const csv = fs.readFileSync((await download.path())!, 'utf8');
    const lines = csv.split('\n').filter((line) => line.length > 0 && !line.startsWith('#'));
    const [header, ...dataRows] = lines;
    expect(header.startsWith('borrower_id,')).toBe(true);
    expect(dataRows).toHaveLength(2);
    expect(dataRows.map((row) => row.split(',')[0]).sort()).toEqual([first.borrower_id, second.borrower_id].sort());
    expect(csv).toContain('# export_scope=selected_rows');
    expect(csv).toContain('# exported_rows=2');
    await expect(page.getByTestId('lead-export-notice')).toContainText('Exported 2 selected leads in rank order.');
  });
});
