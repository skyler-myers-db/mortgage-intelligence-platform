/**
 * Borrower decision history (audit flow-04 phase 2 / tables-10,
 * D-audit-reads-c2) at the rendered layer, 1440 x 900, both themes.
 *
 *  - Borrower 360: a "Decision history" surface built from the prototype's
 *    audit rows, listed in flow (never a scroll region) and axe-clean with no
 *    KNOWN_VIOLATIONS entry (no scrollable-region-focusable).
 *  - Offer Orchestrator: a collapsed "Prior decisions (n)" disclosure that
 *    expands axe-clean.
 *  - The read is audit-free and runs once per mount: exactly one GET
 *    /api/borrowers/:id/decisions, no extra dossier read, no POST
 *    /api/audit/event.
 *  - A 403 (outside the working team) hides the section.
 *  - An Offer approve re-reads the decisions only, after the write resolves.
 */
import type { Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import { PRIMARY_BORROWER } from './data/borrowers';
import { APPROVE_AUDIT_ID, approveResult, ledgerReceipt } from './data/decisionReceipt';
import { json, type MockApi } from './mockApi';
import type { DecisionReceipt } from '../../../src/lib/apiTypes';
import { expect, test } from './test';

const BORROWER_ID = PRIMARY_BORROWER.borrower_id;
const B360 = `/borrower-360/${BORROWER_ID}`;
const OFFER = `/offer-orchestrator/${BORROWER_ID}`;
const DECISIONS = /^\/api(?:\/v1)?\/borrowers\/[^/]+\/decisions$/;
const DOSSIER = /^\/api(?:\/v1)?\/borrowers\/[^/]+$/;

function reads(mockApi: MockApi, path: RegExp): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && path.test(call.path)).length;
}

function auditWrites(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && /\/audit\/event$/.test(call.path)).length;
}

function rows(page: Page, scope: string) {
  return page.locator(`${scope} ol[aria-label="Decision history"] > li.audit`);
}

test.describe('borrower decision history', () => {
  for (const theme of ['dark', 'light'] as const) {
    test(`Borrower 360 lists the decisions in flow, axe-clean, with one audit-free read (${theme})`, async ({ app, page, mockApi }) => {
      await app.setTheme(theme);
      await app.gotoRoute(B360);
      const history = page.getByTestId('borrower-decision-history');
      await expect(rows(page, '[data-testid="borrower-decision-history"]')).toHaveCount(4);
      await expect(history.getByRole('heading', { name: 'Decision history' })).toBeVisible();
      await expect(history).toContainText('Outreach rejected');
      await expect(history).toContainText('Reason: Compliance review');
      await expect(history).toContainText('Contact blocked: No marketing consent');
      await expect(history).toContainText('Summit LO 01 (Loan officer) · you');
      // In flow: never a scroll region (the Console's .audit-panel is one).
      const overflow = await history.locator('ol').evaluate((list) => getComputedStyle(list).overflowY);
      expect(overflow).toBe('visible');
      await expectAxeClean(page, {
        key: { route: 'borrower-360', state: 'decision-history' },
        theme,
        known: {},
        include: '[data-testid="borrower-decision-history"]',
      });
      expect(reads(mockApi, DECISIONS), 'one history read per mount').toBe(1);
      expect(reads(mockApi, DOSSIER), 'no extra dossier read').toBe(1);
      expect(auditWrites(mockApi), 'the history writes no audit row').toBe(0);
    });

    test(`Offer prior decisions are collapsed, then expand axe-clean (${theme})`, async ({ app, page, mockApi }) => {
      await app.setTheme(theme);
      await app.gotoRoute(OFFER);
      const disclosure = page.getByTestId('offer-prior-decisions');
      const toggle = disclosure.getByRole('button', { name: 'Prior decisions (4)' });
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await expect(rows(page, '[data-testid="offer-prior-decisions"]')).toHaveCount(0);
      await toggle.click();
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
      await expect(rows(page, '[data-testid="offer-prior-decisions"]')).toHaveCount(4);
      await expectAxeClean(page, {
        key: { route: 'offer-orchestrator', state: 'prior-decisions' },
        theme,
        known: {},
        include: '[data-testid="offer-prior-decisions"]',
      });
      expect(reads(mockApi, DECISIONS), 'one history read per mount').toBe(1);
      expect(reads(mockApi, DOSSIER), 'the snapshot reads the borrower once').toBe(1);
      expect(auditWrites(mockApi)).toBe(0);
    });
  }

  test('a viewer outside the working team (403) sees no section', async ({ app, page, mockApi }) => {
    app.degrade('/api/borrowers/:id/decisions', { method: 'GET', status: 403, body: { detail: 'forbidden' } });
    await app.gotoRoute(B360);
    await expect(page.locator('h1')).toHaveText(`Borrower ${BORROWER_ID}`);
    await expect.poll(() => reads(mockApi, DECISIONS)).toBe(1);
    await expect(page.getByTestId('borrower-decision-history')).toHaveCount(0);
    await expect(page.locator('#main-content')).not.toContainText('Decision history');
  });

  test('an Offer approve re-reads the decisions only, once the write resolves', async ({ app, page, mockApi }) => {
    mockApi.register('POST', '/api/outreach/approve', () => approveResult(APPROVE_AUDIT_ID));
    mockApi.register('GET', '/api/audit/receipt/:id', ({ params }) =>
      json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')),
    );
    await app.gotoRoute(OFFER);
    // The counted name proves the first read answered: the bare "Prior
    // decisions" toggle renders when the lazy chunk mounts, before the GET.
    await expect(page.getByTestId('offer-prior-decisions').getByRole('button', { name: 'Prior decisions (4)' })).toBeVisible();
    expect(reads(mockApi, DECISIONS), 'one history read before the approve').toBe(1);
    const dossierReads = reads(mockApi, DOSSIER);

    await page.getByTestId('offer-action-bar').getByRole('button', { name: 'Approve outreach' }).click();
    await expect(page.locator('.decision-receipt')).toBeVisible();
    await expect.poll(() => reads(mockApi, DECISIONS), 'the history re-reads once').toBe(2);
    expect(reads(mockApi, DOSSIER), 'no dossier re-read for it').toBe(dossierReads);
    expect(auditWrites(mockApi)).toBe(0);
  });
});
