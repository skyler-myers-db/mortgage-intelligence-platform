/**
 * Rendered-layer proofs for the wave-0 access and keyboard fixes (audit
 * critic-03 evidence drawer / asset detail for non-admins, shell-06 admin
 * deep link, critic-02 offer review copy, a11y-v1 state picker keyboard).
 */
import type { Page } from '@playwright/test';
import type { OutreachDraftResult } from '../../../src/lib/apiTypes';
import type { SessionResponse } from '../../../src/types';
import { PRIMARY_BORROWER } from './data/borrowers';
import { PRIMARY_ASSET_KEY } from './data/dataEstate';
import { outreachDraftFor } from './data/offers';
import { STATES } from './data/reference';
import { expect, test } from './test';

const NON_ADMIN: SessionResponse = {
  can_access_admin: false,
  can_approve: true,
  actor_email: 'growth@summit-mortgage.example',
};

/** Tab from the start of the document until `predicate` holds (keyboard only). */
async function tabUntil(page: Page, predicate: string, maxTabs = 80): Promise<void> {
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  for (let i = 0; i < maxTabs; i += 1) {
    await page.keyboard.press('Tab');
    if (await page.evaluate((selector) => document.activeElement?.matches(selector) ?? false, predicate)) return;
  }
  throw new Error(`Tab never reached ${predicate} within ${maxTabs} presses`);
}

test.describe('a non-admin session', () => {
  test('gets the evidence proof without the admin-only asset link, and a 403 surface on the admin deep link', async ({ app, mockApi, page }) => {
    mockApi.register<SessionResponse>('GET', '/api/session', () => ({ body: NON_ADMIN }));
    await app.gotoRoute('/');
    const drawer = await app.openEvidenceDrawer();
    // The proof itself stays: the governed asset chips and their names.
    const assets = drawer.locator('.governed-assets__list .lineage-node__chip');
    await expect(assets).not.toHaveCount(0);
    await expect(assets.first()).toBeVisible();
    await expect(drawer.getByText('Governed assets')).toBeVisible();
    await expect(drawer.getByRole('link', { name: 'View asset details' })).toHaveCount(0);
    // The admin-only metadata read is never issued for this session.
    expect(mockApi.calls.filter((call) => /^\/api\/admin\/assets\//.test(call.path))).toEqual([]);
    await drawer.getByRole('button', { name: 'Close drawer' }).click();

    await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Admin' })).toHaveCount(0);
    await app.gotoRoute('/admin-config');
    await expect(page).toHaveURL(/\/admin-config$/);
    const denied = page.getByTestId('admin-access-denied');
    await expect(denied).toBeVisible();
    await expect(denied.getByTestId('access-denied-role')).toContainText('Required role: Administrator.');
    await expect(denied).toContainText('403 · Access denied');
    await expect(denied.getByRole('link', { name: 'Go to Home' })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Admin' })).toHaveCount(0);
    expect(mockApi.calls.filter((call) => /^\/api\/admin\//.test(call.path))).toEqual([]);
  });

  test('an admin session keeps the asset link', async ({ app }) => {
    await app.gotoRoute('/');
    const drawer = await app.openEvidenceDrawer();
    await expect(drawer.getByRole('link', { name: 'View asset details' })).toBeVisible();
  });
});

test.describe('asset detail denied by the server', () => {
  const FORBIDDEN = { status: 403, body: { detail: 'Admin access required (fixture).' }, method: 'GET' as const };

  test('reached from the Lead Queue, "Back to previous page" returns there', async ({ app, page }) => {
    app.degrade('/api/admin/assets/:assetKey/metadata', FORBIDDEN);
    await app.gotoRoute('/lead-queue');
    await app.expandFirstLeadRow();
    const drawer = await app.openEvidenceDrawer(page.locator('table.tbl tbody tr.tbl__expand .evidence-chip').first());
    await drawer.getByRole('link', { name: 'View asset details' }).click();
    await expect(page).toHaveURL(/\/data-estate\/assets\//);
    const denied = page.getByTestId('asset-access-denied');
    await expect(denied).toBeVisible();
    await expect(denied.getByTestId('access-denied-role')).toContainText('Required role: Administrator.');
    const back = denied.getByRole('button', { name: 'Back to previous page' });
    await expect(back).toHaveClass(/btn--primary/);
    await expect(denied.getByRole('link', { name: 'Go to Home' })).toHaveClass(/btn--ghost/);
    await back.click();
    // Back lands on the Lead Queue with its place kept: since wave 3
    // (w3-queue-place) the focused row rides in the URL.
    await expect(page).toHaveURL(/\/lead-queue(\?row=B-[0-9A-Z]{13})?$/);
    await expect(page.locator('#main-content h1')).toBeVisible();
  });

  test('opened cold, "Go to Home" is the primary way out', async ({ app, page }) => {
    app.degrade('/api/admin/assets/:assetKey/metadata', FORBIDDEN);
    await app.gotoRoute(`/data-estate/assets/${PRIMARY_ASSET_KEY}`);
    const denied = page.getByTestId('asset-access-denied');
    await expect(denied).toBeVisible();
    await expect(denied.getByRole('button', { name: 'Back to previous page' })).toHaveCount(0);
    const home = denied.getByRole('link', { name: 'Go to Home' });
    await expect(home).toHaveClass(/btn--primary/);
    await home.click();
    await expect(page).toHaveURL(/\/$/);
  });
});

test.describe('Offer Orchestrator review copy', () => {
  test('shows a long subject and the whole body read-only, without clipping', async ({ app, mockApi, page }) => {
    const subject = 'A quick review of your mortgage options before rates move again this year at no obligation';
    expect(subject.length).toBe(90);
    mockApi.register<OutreachDraftResult>('POST', '/api/outreach/draft', (request) => ({
      body: { ...outreachDraftFor(request), subject },
    }));
    await app.gotoRoute(`/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`);
    const subjectBox = page.getByTestId('outreach-subject');
    await expect(subjectBox).toHaveText(subject);
    await expect(subjectBox).toHaveAttribute('role', 'group');
    await expect(subjectBox).toHaveAttribute('aria-label', 'Outreach subject — review only');
    const subjectBoxMetrics = await subjectBox.evaluate((node) => ({
      scrollWidth: node.scrollWidth,
      clientWidth: node.clientWidth,
      scrollHeight: node.scrollHeight,
      clientHeight: node.clientHeight,
      tag: node.tagName,
    }));
    expect(subjectBoxMetrics.tag).not.toBe('INPUT');
    expect(subjectBoxMetrics.scrollWidth).toBeLessThanOrEqual(subjectBoxMetrics.clientWidth);
    expect(subjectBoxMetrics.scrollHeight).toBeLessThanOrEqual(subjectBoxMetrics.clientHeight);

    const draft = page.getByTestId('outreach-draft');
    await expect(draft).toContainText('Equal Housing Lender');
    const draftMetrics = await draft.evaluate((node) => ({
      scrollHeight: node.scrollHeight,
      clientHeight: node.clientHeight,
      tag: node.tagName,
      editable: (node as HTMLElement).isContentEditable,
    }));
    expect(draftMetrics.tag).not.toBe('TEXTAREA');
    expect(draftMetrics.editable).toBe(false);
    expect(draftMetrics.scrollHeight).toBeLessThanOrEqual(draftMetrics.clientHeight);
  });
});

test.describe('Portfolio Builder state picker', () => {
  test('is operable with the keyboard alone', async ({ app, page }) => {
    await app.gotoRoute('/portfolio-builder');
    const trigger = page.locator('button[aria-haspopup="listbox"][aria-label^="GEO:"]');
    await tabUntil(page, 'button[aria-haspopup="listbox"][aria-label^="GEO:"]');
    await expect(trigger).toBeFocused();
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');

    await page.keyboard.press('ArrowDown');
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const listbox = page.getByRole('listbox', { name: 'GEO' });
    await expect(listbox).toBeVisible();
    // a11y-02: the open listbox holds focus and carries aria-activedescendant.
    await expect(listbox).toBeFocused();
    const activeBefore = await listbox.getAttribute('aria-activedescendant');
    expect(activeBefore).toMatch(/-option-0$/);

    await page.keyboard.press('ArrowDown');
    const activeAfter = await listbox.getAttribute('aria-activedescendant');
    expect(activeAfter).toMatch(/-option-1$/);
    const firstState = listbox.locator(`[role="option"][id="${activeAfter}"]`);
    await expect(firstState).toHaveText(STATES[0].name);
    await expect(firstState).toHaveAttribute('aria-selected', 'false');

    await page.keyboard.press('Space');
    await expect(firstState).toHaveAttribute('aria-selected', 'true');
    await expect(trigger).toHaveAttribute('aria-label', `GEO: ${STATES[0].name}`);
    await expect(listbox).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(listbox).toHaveCount(0);
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await expect(trigger).toBeFocused();
  });
});
