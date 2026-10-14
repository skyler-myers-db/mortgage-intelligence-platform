/**
 * Rendered-layer proofs for the W5e wire-types lane (audit quality-04 P2,
 * w5-wire-types A1 / A5 / A6), at 1440x900 in both themes, axe-clean, and
 * with no audited read beyond what the route loads or the person asks for:
 *
 * (a) a Lead Queue row whose recommended offer is a valid wire string the
 *     approve route refuses ('mystery_offer') is refused on Confirm with the
 *     route's own copy, 'Reload the app to approve'. Nothing more is sent:
 *     0 POST /api/outreach/approve, and no draft beyond the one the review
 *     generated when it opened (that review draft is its own explicit intent,
 *     made in useLeadApproveReview before any approve);
 * (b) the audit-ledger explorer renders a rollup whose fail-closed
 *     event_type is null as 'Event type not recorded', never a default;
 * (c) Sales Ops renders the now-typed standup, conversion and outcome rows
 *     exactly as before.
 */
import type { Page } from '@playwright/test';
import type { AuditRollupResponse } from '../../../src/lib/apiClients/audit';
import type { LeadSummary } from '../../../src/types';
import { expectAxeClean } from './axe';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { registerDraftEcho } from './data/queueKeyboard';
import { registerBulkApprove, registerRankedQueue } from './data/queuePlace';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { auditedReadsAfter, expectNoAuditedReadSince, markNaturalLoad } from './visual';

const MYSTERY: LeadSummary = {
  ...LEADS.find((lead) => lead.borrower_id === PRIMARY_BORROWER.borrower_id)!,
  recommended_offer_code: 'mystery_offer',
  recommended_offer: 'Unreviewed offer',
};

const toastRegion = (page: Page) => page.locator('section.toast-region[aria-label="Notifications"]');

function posts(mockApi: MockApi, path: string): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && call.path === path).length;
}

for (const theme of FIXTURE_THEMES) {
  test(`${theme}: (a) an approve naming an offer code the route refuses sends nothing and says to reload`, async ({ app, page, mockApi }) => {
    registerRankedQueue(mockApi, [MYSTERY]);
    const echo = registerDraftEcho(mockApi);
    const tracker = registerBulkApprove(mockApi);
    await app.setTheme(theme);
    await app.gotoRoute('/lead-queue');
    const naturalLoad = markNaturalLoad(mockApi);

    await page.getByTestId(`lead-select-${MYSTERY.borrower_id}`).check();
    await page.getByTestId('lead-bulk-approve').click();
    const confirm = page.locator('dialog.lead-approve-dialog').getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeFocused();
    expect(echo.calls, 'the review drafts the row it shows, on its own explicit intent').toEqual([MYSTERY.borrower_id]);
    const reviewDrafted = markNaturalLoad(mockApi);
    expect(auditedReadsAfter(mockApi.calls, naturalLoad)).toHaveLength(1);

    await page.keyboard.press('Enter');
    const toast = toastRegion(page).locator('.toast', { hasText: `Couldn't approve ${MYSTERY.borrower_id}` });
    await expect(toast).toContainText('Reload the app to approve');
    await expect(toast).not.toContainText('mystery_offer');
    expect(tracker.bodies, 'no approve POST: refused before any I/O').toEqual([]);
    expect(posts(mockApi, '/api/outreach/approve')).toBe(0);
    expect(posts(mockApi, '/api/outreach/draft'), 'the refusal drafts nothing more').toBe(1);
    expectNoAuditedReadSince(mockApi, reviewDrafted, `refused approve · ${theme}`);
    await expectAxeClean(page, { key: { route: 'lead-queue', state: 'wire-code-refused' }, theme, known: {}, include: 'section.toast-region' });
  });

  test(`${theme}: (b) a rollup whose event type was not recorded reads as not recorded`, async ({ app, page, mockApi }) => {
    const rollups: AuditRollupResponse[] = [
      { bucket_start: '2026-07-08T00:00:00Z', event_type: null, group_by: 'event_type', group_key: null, event_count: 4 },
      { bucket_start: '2026-07-08T00:00:00Z', event_type: 'VIEW_LEADS', group_by: 'event_type', group_key: 'VIEW_LEADS', event_count: 40 },
    ];
    mockApi.register<AuditRollupResponse[]>('GET', '/api/audit/rollups', () => json(rollups));
    await app.setTheme(theme);
    await app.gotoRoute('/audit-ledger');
    const naturalLoad = markNaturalLoad(mockApi);

    const tiles = page.locator('.admin-rollups__grid .admin-rollup');
    await expect(tiles).toHaveCount(2);
    await expect(tiles.nth(0).locator('span').nth(0)).toHaveText('Event type not recorded');
    await expect(tiles.nth(0).locator('span.mono')).toHaveText('—');
    await expect(tiles.nth(0)).not.toContainText('VIEW_LEADS');
    await expect(tiles.nth(1).locator('span.mono')).toHaveText('VIEW_LEADS');
    expectNoAuditedReadSince(mockApi, naturalLoad, `rollups · ${theme}`);
    await expectAxeClean(page, { key: { route: 'audit-ledger', state: 'rollup-unrecorded' }, theme, known: {}, include: '.admin-rollups' });
  });

  test(`${theme}: (c) Sales Ops renders the typed standup, conversion and outcome rows unchanged`, async ({ app, page, mockApi }) => {
    await app.setTheme(theme);
    await app.gotoRoute('/analytics?view=sales-ops');
    const naturalLoad = markNaturalLoad(mockApi);

    const standup = page.locator('.sales-ops-card').filter({ hasText: 'Yesterday standup' });
    await expect(standup).toContainText('42');
    await expect(standup).toContainText('17 reached · 6 callbacks · 3 apps.');
    const conversion = page.locator('.sales-ops-card').filter({ has: page.locator('.sales-ops-list .split-row') }).first();
    await expect(conversion.locator('.split-row').filter({ hasText: 'Loan Officer A' })).toContainText('8%');
    await expect(conversion.locator('.split-row').filter({ hasText: 'Loan Officer B' })).toContainText('8%');
    const outcomes = page.locator('.sales-ops-card').filter({ hasText: 'Imported, read-only outcome ledger' });
    await expect(outcomes).toContainText('31');
    await expect(outcomes.locator('.split-row').nth(0)).toContainText('Competitor A');
    await expect(outcomes.locator('.split-row').nth(0)).toContainText('4');
    await expect(outcomes.locator('.split-row').nth(1)).toContainText('Competitor B');
    expectNoAuditedReadSince(mockApi, naturalLoad, `sales ops · ${theme}`);
    await expectAxeClean(page, { key: { route: 'analytics-sales-ops', state: 'typed-rows' }, theme, known: {}, include: '.sales-ops-card' });
  });
}
