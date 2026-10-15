/**
 * W5e w5-lead-table-compile, flow-v2: revoke an approval from the Lead Queue,
 * rendered against the production build at 1440x900.
 *
 *  - The Approved row: an approver's icon-only Revoke opens the lazy panel,
 *    which reads the lifecycle ONCE (audit-free) to name the approval; one
 *    POST with the exact body; the row reads pending only after the 200, the
 *    toast names the audit row, focus returns to the row.
 *  - The in-session queue receipt: 'Revoke approval' revokes the receipt's
 *    approval with no lifecycle read.
 *  - A campaign-bound refusal (409 reason campaign_bound) reads its closed
 *    sentence and withdraws Confirm.
 *  - Negatives: a non-approver and a campaign-bound queue see no Revoke (the
 *    bound queue's provenance row says why).
 *  - Layout: the Approval cell keeps 8 px of slack in its 156 px pin and the
 *    row height, and eight rows stay above the fold with the Console open,
 *    in dark and light.
 * The panel's axe scan is axe.fixture.spec.ts's 'lead-revoke-panel' state.
 */
import type { Page } from '@playwright/test';
import type { FixtureTheme } from './app';
import { NON_APPROVER_SESSION } from './data/approvalRequests';
import { PRIMARY_BORROWER } from './data/borrowers';
import { APPROVE_AUDIT_ID, approveResult, ledgerReceipt } from './data/decisionReceipt';
import { boundSearch, registerBoundCampaign } from './data/paletteOfferSalesLoop';
import { APPROVED_LEAD, REVOKED_APPROVAL_ID, approvedLifecycle, revokeRefused, revokedBody } from './data/revoke';
import { json, type MockApi } from './mockApi';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const ID = APPROVED_LEAD.borrower_id;
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const REASON = 'Offer code was mis-keyed; needs a second review.';

function lifecycleCalls(mockApi: MockApi, borrowerId = ID): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path.endsWith(`/borrowers/${borrowerId}/lifecycle`)).length;
}

/** Answer every revoke as recorded, and keep the bodies the page sent. */
function recordRevokes(mockApi: MockApi): Array<Record<string, string>> {
  const bodies: Array<Record<string, string>> = [];
  mockApi.register('POST', '/api/outreach/revoke', ({ body }) => {
    const sent = body as Record<string, string>;
    bodies.push(sent);
    return json(revokedBody(sent.approval_id));
  });
  return bodies;
}

async function openRevoke(page: Page): Promise<void> {
  await page.getByTestId(`lead-revoke-${ID}`).filter({ visible: true }).click();
  await expect(page.getByTestId('revoke-approval-panel').getByLabel('Reason for revoking')).toBeFocused();
}

test.describe('revoke an approval (flow-v2)', () => {
  test('the Approved row: one lifecycle read on open, one POST, pending only after the 200', async ({ app, page, mockApi }) => {
    mockApi.register('GET', `/api/borrowers/${ID}/lifecycle`, () => json(approvedLifecycle()));
    const sent = recordRevokes(mockApi);
    await app.gotoRoute('/lead-queue');
    const cell = page.getByTestId(`lead-approval-cell-${ID}`);
    await expect(cell.locator('.chip', { hasText: 'Approved' })).toBeVisible();
    const revoke = page.getByTestId(`lead-revoke-${ID}`);
    await expect(revoke).toHaveAccessibleName(`Revoke approval for ${ID}`);
    await expect(revoke).toHaveAccessibleDescription(/Revoke approval/);
    await expect(revoke).not.toHaveAttribute('title');
    await expect(revoke).not.toHaveAttribute('aria-keyshortcuts');
    const naturalLoad = markNaturalLoad(mockApi);
    expect(lifecycleCalls(mockApi), 'nothing reads the lifecycle before the explicit open').toBe(0);

    await openRevoke(page);
    expect(lifecycleCalls(mockApi), 'one audit-free lifecycle read, on the open').toBe(1);
    const panel = page.getByTestId('revoke-approval-panel');
    await expect(panel.getByRole('heading', { level: 3 })).toHaveText(`Revoke the approval for ${ID}?`);
    await expect(panel).toContainText('The borrower returns to pending. Nothing is sent; an unworked assignment is released. The approval stays in the audit ledger.');
    await panel.getByLabel('Reason for revoking').fill(REASON);
    await panel.getByTestId('revoke-approval-confirm').click();

    await expect(page.getByTestId(`lead-approve-${ID}`).filter({ visible: true })).toBeVisible();
    await expect(cell.locator('.chip', { hasText: 'Approved' })).toHaveCount(0);
    await expect(page.locator('.toast-region')).toContainText(`Approval revoked for ${ID}`);
    await expect(page.locator(`#lead-row-toggle-${ID}`)).toBeFocused();
    expect(sent).toHaveLength(1);
    expect(Object.keys(sent[0]).sort()).toEqual(['approval_id', 'borrower_id', 'rationale', 'request_id']);
    expect(sent[0]).toMatchObject({ borrower_id: ID, approval_id: REVOKED_APPROVAL_ID, rationale: REASON });
    expect(lifecycleCalls(mockApi)).toBe(1);
    // The revoke stale-marks the queue; nothing audited is read again.
    expectNoAuditedReadSince(mockApi, naturalLoad, 'revoke from the Approved row');
  });

  test('the queue receipt: Revoke approval revokes the receipt\'s approval, with no lifecycle read', async ({ app, page, mockApi }) => {
    const borrowerId = PRIMARY_BORROWER.borrower_id;
    mockApi.register('POST', '/api/outreach/approve', () => approveResult(APPROVE_AUDIT_ID));
    mockApi.register('GET', '/api/audit/receipt/:id', ({ params }) => json(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')));
    const sent = recordRevokes(mockApi);
    await app.gotoRoute('/lead-queue');
    await app.expandFirstLeadRow();
    const expanded = page.locator(`tr:has([data-testid="lead-approval-cell-${borrowerId}"]) + tr.tbl__expand`);
    await page.getByTestId(`lead-approve-${borrowerId}`).click();
    await expanded.getByTestId('lead-approve-review-confirm').click();
    const receipt = expanded.getByTestId('decision-receipt');
    await expect(receipt).toBeVisible();
    const naturalLoad = markNaturalLoad(mockApi);

    await receipt.getByTestId('decision-receipt-revoke').click();
    const panel = page.getByTestId('revoke-approval-panel');
    await expect(panel.getByLabel('Reason for revoking')).toBeFocused();
    expect(lifecycleCalls(mockApi, borrowerId), 'the receipt names the approval: no lifecycle read').toBe(0);
    await panel.getByLabel('Reason for revoking').fill(REASON);
    await panel.getByTestId('revoke-approval-confirm').click();

    await expect(page.locator('.toast-region')).toContainText(`Approval revoked for ${borrowerId}`);
    await expect(expanded.getByTestId('decision-receipt')).toHaveCount(0);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ borrower_id: borrowerId, approval_id: 'apr-fixture-0001' });
    expectNoAuditedReadSince(mockApi, naturalLoad, 'revoke from the receipt');
  });

  test('a campaign-bound refusal reads its closed sentence and withdraws Confirm', async ({ app, page, mockApi, hygiene }) => {
    // The 409 is this test's own doing; the browser logs every 4xx response.
    hygiene.allow('console.error', /status of 409 \(Conflict\) \(http:\/\/[^)]+\/api\/v1\/outreach\/revoke\)/);
    mockApi.register('GET', `/api/borrowers/${ID}/lifecycle`, () => json(approvedLifecycle()));
    mockApi.register('POST', '/api/outreach/revoke', () => revokeRefused('campaign_bound', 'A campaign-bound approval cannot be revoked in this release.'));
    await app.gotoRoute('/lead-queue');
    await openRevoke(page);
    const panel = page.getByTestId('revoke-approval-panel');
    await panel.getByLabel('Reason for revoking').fill(REASON);
    await panel.getByTestId('revoke-approval-confirm').click();
    await expect(panel.getByTestId('revoke-approval-failure')).toHaveText("Campaign-bound approvals can't be revoked in this release.");
    await expect(panel.getByTestId('revoke-approval-confirm')).toHaveCount(0);
    await expect(panel.getByLabel('Reason for revoking')).toHaveValue(REASON);
    await expect(page.getByTestId(`lead-approval-cell-${ID}`).locator('.chip', { hasText: 'Approved' })).toBeVisible();
  });

  test('no Revoke for a non-approver, nor on a campaign-bound queue (whose provenance row says why)', async ({ app, page, mockApi }) => {
    mockApi.register('GET', '/api/session', () => json(NON_APPROVER_SESSION));
    await app.gotoRoute('/lead-queue');
    await expect(page.getByTestId(`lead-approval-cell-${ID}`).locator('.chip', { hasText: 'Approved' })).toBeVisible();
    await expect(page.getByTestId(`lead-revoke-${ID}`)).toHaveCount(0);

    mockApi.register('GET', '/api/session', () => json({ ...NON_APPROVER_SESSION, can_approve: true, can_access_admin: true }));
    registerBoundCampaign(mockApi);
    await app.gotoRoute(`/lead-queue?${boundSearch()}`);
    const provenance = page.getByTestId('campaign-operational-provenance');
    await expect(provenance).toContainText("Campaign-bound approvals can't be revoked in this release.");
    await expect(page.getByTestId(`lead-revoke-${ID}`)).toHaveCount(0);
  });

  for (const theme of THEMES) {
    test(`the Approval cell keeps its 156 px pin, 8 px of slack and the row height; eight rows above the fold with the Console open (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const cell = page.getByTestId(`lead-approval-cell-${ID}`);
      await expect(page.getByTestId(`lead-revoke-${ID}`)).toBeVisible();
      const box = await cell.evaluate((td) => {
        const style = getComputedStyle(td);
        const content = td.getBoundingClientRect().width - Number.parseFloat(style.paddingLeft) - Number.parseFloat(style.paddingRight);
        const kids = [...td.children].map((child) => child.getBoundingClientRect());
        const used = Math.max(...kids.map((rect) => rect.right)) - Math.min(...kids.map((rect) => rect.left));
        const row = td.closest('tr')!;
        const pending = row.parentElement?.parentElement?.querySelector('tr[data-borrower-row]:not(:has(.chip--success))');
        return {
          width: td.getBoundingClientRect().width,
          content,
          used,
          rowHeight: row.getBoundingClientRect().height,
          pendingHeight: pending?.getBoundingClientRect().height ?? 0,
        };
      });
      expect(Math.round(box.width), 'the pinned Approval column').toBe(156);
      expect(box.content - box.used, 'slack beside the Approved chip and Revoke').toBeGreaterThanOrEqual(8);
      expect(box.rowHeight, 'the Approved row keeps the one-line row height').toBe(box.pendingHeight);

      await app.openConsole();
      await app.settle();
      const aboveFold = await page.locator('.lead-table__table tr[data-borrower-row]').evaluateAll((rows) =>
        rows.filter((row) => row.getBoundingClientRect().bottom <= window.innerHeight).length);
      expect(aboveFold, 'eight rows above the fold with the Console open').toBeGreaterThanOrEqual(8);
    });
  }
});
