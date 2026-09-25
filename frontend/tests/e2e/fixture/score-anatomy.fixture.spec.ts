/**
 * Score anatomy (lane w3-score-anatomy: wow-stage-2, wow-ai-1, wow-stage-3
 * remainder, carryovers #11 / #12) at the rendered layer, 1440 x 900.
 *
 * THE TRAP this spec guards: every GET /api/borrowers/{id}/proof writes a
 * VIEW_BORROWER_PROOF audit row. No natural load, scroll, hover, row expand,
 * remount, approve, back / forward or J / K may read it; only an explicit
 * click on a disclosure (or opening the proof drawer) does, once.
 *
 *  - Borrower 360: the natural load reads no proof; "Score anatomy" reads it
 *    once; five labelled segments, the recompute seal for a trusted proof, the
 *    gaps (no seal) for an untrusted one, the margins and their not-a-credit-
 *    decision note; a segment opens the proof drawer on Math with that card
 *    focused, with no new read; away and back re-renders from the cache.
 *  - Lead Queue: expand / collapse / re-expand reads nothing; the preview's
 *    disclosure reads once; nothing overflows; with the chunk's drawer open,
 *    A and R do nothing.
 *  - Offer Orchestrator: the natural load reads no proof; "What would change
 *    this?" reads once; after Approve, focus sits on the receipt heading,
 *    never <body>; the routing line outlives the toast; a warm cache seals the
 *    receipt's score, a cold one keeps "from the lead payload" and reads none.
 *  - Approve, then Borrower 360, back / forward and J / K: still one read.
 *  - Admin explorer: an expanded APPROVE row reads its receipt once, compact,
 *    with no explorer link and no audited read; a VIEW_LEADS row reads none.
 *  - Both themes: the segment hues resolve to the --score-part-* tokens, and
 *    the anatomy region, the open drawer and the explorer receipt are
 *    axe-clean with no KNOWN_VIOLATIONS entry.
 */
import type { Locator, Page } from '@playwright/test';
import { expectAxeClean, type AxeTheme } from './axe';
import { PRIMARY_BORROWER } from './data/borrowers';
import { RequestGate } from './data/decisionReceipt';
import { ROUTED_FOLLOW_UP_AT, ROUTED_LOAN_OFFICER } from './data/feedbackGuard';
import {
  ANATOMY_PAR,
  ANATOMY_SEAL,
  EXPLORER_DECISION_ROW,
  EXPLORER_VIEW_ROW,
  UNTRUSTED_GAPS,
  registerAnatomyProof,
  registerExplorerDecisionRows,
  registerRoutedApprove,
} from './data/scoreAnatomy';
import type { MockApi } from './mockApi';
import { expectNoAuditedReadSince, expectNoSurfaceOverflow, markNaturalLoad } from './visual';
import { expect, test } from './test';

const BORROWER_ID = PRIMARY_BORROWER.borrower_id;
const B360 = `/borrower-360/${BORROWER_ID}`;
const OFFER = `/offer-orchestrator/${BORROWER_ID}`;
const PART_LABELS = ['Economic incentive', 'Intent trigger', 'Product fit', 'Relationship', 'Evidence coverage'];
const MARGINS_NOTE = 'Marketing prioritization, not a credit decision.';
/** The dark / light --score-part-* values (tokens.css tail), in weight order. */
const PART_HUES: Record<AxeTheme, readonly string[]> = {
  dark: ['rgb(57, 135, 229)', 'rgb(217, 89, 38)', 'rgb(25, 158, 112)', 'rgb(201, 133, 0)', 'rgb(213, 81, 129)'],
  light: ['rgb(42, 120, 214)', 'rgb(199, 84, 31)', 'rgb(15, 138, 106)', 'rgb(154, 106, 0)', 'rgb(196, 69, 122)'],
};
const PART_MODIFIERS = ['economic', 'intent', 'fit', 'relationship', 'evidence'];

function proofCalls(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => /^\/api(?:\/v1)?\/borrowers\/[^/]+\/proof$/.test(call.path)).length;
}

function receiptCalls(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.path.includes('/audit/receipt/')).length;
}

function spineGate(scope: Page | Locator): Locator {
  return scope.locator('[data-testid="score-anatomy-spine"]');
}

async function openSpine(scope: Page | Locator): Promise<Locator> {
  const gate = spineGate(scope);
  await gate.getByRole('button', { name: 'Score anatomy' }).click();
  const spine = gate.getByTestId('score-spine');
  await expect(spine).toBeVisible();
  return gate;
}

/** The element focus sits on, as "tag.class: text". */
async function focused(page: Page): Promise<string> {
  return page.evaluate(() => {
    const active = document.activeElement;
    if (!active || active === document.body) return 'BODY';
    return `${active.tagName}.${[...active.classList].join('.')}: ${(active.textContent ?? '').trim().slice(0, 40)}`;
  });
}

test.describe('score anatomy', () => {
  test('Borrower 360: one read on the disclosure, five segments, the seal, a segment opens its math from the cache', async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'trusted');
    await app.gotoRoute(B360);
    const naturalLoad = markNaturalLoad(mockApi);
    expect(proofCalls(mockApi), 'the dossier natural load reads no proof').toBe(0);

    // Scroll and hover the card and its openers: still nothing audited.
    const card = page.locator('.surface', { has: spineGate(page) });
    await card.scrollIntoViewIfNeeded();
    await page.mouse.wheel(0, 400);
    await card.getByRole('button', { name: /Show scoring math/ }).hover();
    await spineGate(page).getByRole('button', { name: 'Score anatomy' }).hover();
    expectNoAuditedReadSince(mockApi, naturalLoad, 'borrower 360 scroll + hover');

    const gate = await openSpine(page);
    await expect.poll(() => proofCalls(mockApi), 'the disclosure read the proof once').toBe(1);
    const segments = gate.locator('button.score-spine__seg');
    await expect(segments).toHaveCount(5);
    for (const [index, label] of PART_LABELS.entries()) {
      await expect(segments.nth(index)).toHaveAccessibleName(new RegExp(`^${label} \\d+\\.\\d`));
    }
    await expect(gate.getByTestId('score-spine-seal')).toHaveText(ANATOMY_SEAL);
    await expect(gate.getByTestId('score-margins-note')).toHaveText(MARGINS_NOTE);
    await expect(gate.getByTestId('score-margins-provenance')).toContainText(`${ANATOMY_PAR.replace('par', 'Par')} as of`);
    await expect(gate.getByTestId('score-margins-provenance')).toContainText('the FRED ingest schedule ships paused');
    await expect(gate).not.toContainText(/today/i);

    // A segment opens the page's proof drawer on Math with its card focused.
    await segments.nth(2).click();
    const drawer = page.locator('.proof-drawer.is-open');
    await expect(drawer).toBeVisible();
    await expect(drawer.locator('.proof-tab.is-active')).toHaveText('Math');
    await expect.poll(() => page.evaluate(() => document.activeElement?.getAttribute('data-component-key'))).toBe('fit');
    await expect(drawer.locator('.proof-component--focused')).toHaveCount(1);
    expect(proofCalls(mockApi), 'the drawer opened over the cache').toBe(1);
    await page.keyboard.press('Escape');
    await expect(drawer).toHaveCount(0);

    // Away (in-app) and back: re-rendered from the cache, expanded, no read.
    await page.getByRole('navigation').getByRole('link', { name: /Lead Queue/ }).first().click();
    await app.settle();
    await page.goBack();
    await app.settle();
    await expect(spineGate(page).getByTestId('score-spine')).toBeVisible();
    expect(proofCalls(mockApi), 'back to the dossier re-rendered from the cache').toBe(1);
  });

  test('Borrower 360: an untrusted proof lists every gap and shows no seal', async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'untrusted');
    await app.gotoRoute(B360);
    const gate = await openSpine(page);
    await expect(gate.getByTestId('score-spine-seal')).toHaveCount(0);
    const gaps = gate.getByTestId('score-spine-gaps').locator('li');
    await expect(gaps).toHaveText([...UNTRUSTED_GAPS]);
    await expect(gate.getByTestId('score-margins-note')).toHaveText(MARGINS_NOTE);
    await expect(gate).not.toContainText(/unity catalog recomputed/i);
  });

  test('Lead Queue: expand / collapse reads nothing, the preview disclosure reads once, A and R stay inert under its drawer', async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'trusted');
    await app.gotoRoute('/lead-queue');
    const naturalLoad = markNaturalLoad(mockApi);
    const expanded = await app.expandFirstLeadRow();
    const toggle = page.locator('table.tbl tbody [aria-expanded]').first();
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(spineGate(expanded)).toBeVisible();
    expectNoAuditedReadSince(mockApi, naturalLoad, 'lead queue expand / collapse / re-expand');
    expect(proofCalls(mockApi)).toBe(0);

    const gate = await openSpine(expanded);
    await expect.poll(() => proofCalls(mockApi), 'the preview disclosure read once').toBe(1);
    await expectNoSurfaceOverflow(page, { route: 'lead-queue', state: 'expanded-row', theme: 'dark' });

    // The queue has no drawer of its own: the chunk opens one, over the cache.
    await gate.locator('button.score-spine__seg').first().click();
    const drawer = page.locator('.proof-drawer.is-open');
    await expect(drawer).toBeVisible();
    const writesBefore = mockApi.calls.filter((call) => call.method === 'POST').length;
    await page.keyboard.press('a');
    await page.keyboard.press('r');
    await expect(page.getByTestId('lead-approve-review-confirm')).toHaveCount(0);
    expect(mockApi.calls.filter((call) => call.method === 'POST').length, 'A / R did nothing under the drawer').toBe(writesBefore);
    expect(proofCalls(mockApi)).toBe(1);
  });

  test('Offer: the margins read once, Approve lands focus on the receipt heading, the routing line outlives the toast, the warm cache seals the score', async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'trusted');
    const approveGate = new RequestGate();
    registerRoutedApprove(mockApi, approveGate);
    await app.gotoRoute(OFFER);
    expect(proofCalls(mockApi), 'the offer natural load (recommend + draft) reads no proof').toBe(0);

    const margins = page.locator('[data-testid="score-anatomy-margins"]');
    await margins.getByRole('button', { name: 'What would change this?' }).click();
    await expect(margins.getByTestId('score-margins')).toBeVisible();
    await expect(margins.getByTestId('score-spine')).toHaveCount(0);
    await expect(margins.locator('.score-margins__row')).toHaveCount(5);
    await expect(margins.getByTestId('score-margins-note')).toHaveText(MARGINS_NOTE);
    await expect.poll(() => proofCalls(mockApi)).toBe(1);

    await page.getByLabel('Assign to loan officer').selectOption(ROUTED_LOAN_OFFICER.email);
    await page.getByRole('button', { name: 'Approve outreach' }).click();
    await expect.poll(() => approveGate.received).toBe(true);
    approveGate.release();

    const receipt = page.getByTestId('decision-receipt');
    await expect(receipt).toBeVisible();
    await expect.poll(() => focused(page), 'focus sits on the receipt heading').toMatch(/^DIV\.h-4: Decision receipt/);
    expect(await focused(page)).not.toBe('BODY');

    const routing = receipt.getByTestId('decision-receipt-routing');
    await expect(routing).toContainText(`Routing: Assigned to ${ROUTED_LOAN_OFFICER.email} · follow-up Jul 19 (from the approval response)`);
    expect(ROUTED_FOLLOW_UP_AT).toContain('2026-07-19');
    const toast = page.locator('section.toast-region[aria-label="Notifications"] .toast');
    await expect(toast).toContainText('Approval routed');
    await toast.getByRole('button', { name: 'Dismiss notification' }).click();
    await expect(toast).toHaveCount(0);
    await expect(routing).toBeVisible();
    await expect(page.locator('[data-testid="routing-confirm"], .outreach-routing__confirm')).toHaveCount(0);

    await expect(receipt.getByTestId('decision-receipt-seal')).toHaveText(ANATOMY_SEAL);
    expect(proofCalls(mockApi), 'the receipt sealed from the cache').toBe(1);
  });

  test('Offer: a cold cache keeps "from the lead payload" and reads no proof', async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'trusted');
    registerRoutedApprove(mockApi);
    await app.gotoRoute(OFFER);
    await page.getByRole('button', { name: 'Approve outreach' }).click();
    const receipt = page.getByTestId('decision-receipt');
    await expect(receipt).toBeVisible();
    await expect(receipt.getByTestId('decision-receipt-score-note')).toHaveText('from the lead payload');
    await expect(receipt.getByTestId('decision-receipt-seal')).toHaveCount(0);
    expect(proofCalls(mockApi)).toBe(0);
  });

  test('approve, then Borrower 360, back / forward and J / K: still the one explicit read', async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'trusted');
    registerRoutedApprove(mockApi);
    await app.gotoRoute('/lead-queue');
    const expanded = await app.expandFirstLeadRow();
    await expanded.getByRole('link', { name: 'Build offer' }).click();
    await app.settle();
    const margins = page.locator('[data-testid="score-anatomy-margins"]');
    await margins.getByRole('button', { name: 'What would change this?' }).click();
    await expect(margins.getByTestId('score-margins')).toBeVisible();
    await expect.poll(() => proofCalls(mockApi)).toBe(1);
    await page.getByRole('button', { name: 'Approve outreach' }).click();
    await expect(page.getByTestId('decision-receipt')).toBeVisible();

    // The approve invalidated ['mip', 'borrower', ...]: an invalidated cache.
    await page.goBack();
    await app.settle();
    const row = await app.expandFirstLeadRow();
    await row.getByRole('link', { name: 'Open Borrower 360' }).click();
    await app.settle();
    await expect(spineGate(page).getByTestId('score-spine')).toBeVisible();
    await page.goBack();
    await app.settle();
    await page.goForward();
    await app.settle();
    await expect(spineGate(page).getByTestId('score-spine')).toBeVisible();

    // J to the next borrower (cold: collapsed, no read), K back (cached).
    await page.keyboard.press('j');
    await expect(page).not.toHaveURL(new RegExp(`${BORROWER_ID}$`));
    await app.settle();
    await expect(spineGate(page).getByRole('button', { name: 'Score anatomy' })).toHaveAttribute('aria-expanded', 'false');
    await page.keyboard.press('k');
    await expect(page).toHaveURL(new RegExp(`${BORROWER_ID}$`));
    await app.settle();
    await expect(spineGate(page).getByTestId('score-spine')).toBeVisible();
    expect(proofCalls(mockApi), 'no passive path read the proof again').toBe(1);
  });

  test('admin explorer: an expanded APPROVE row reads its compact receipt once; a VIEW_LEADS row reads none', async ({ app, page, mockApi }) => {
    registerExplorerDecisionRows(mockApi);
    await app.gotoRoute('/admin-config#audit');
    const naturalLoad = markNaturalLoad(mockApi);
    const explorer = page.locator('#audit');
    await explorer.getByRole('button', { name: `Expand audit event ${EXPLORER_VIEW_ROW.event_id}` }).click();
    await expect(explorer.getByText('Event details')).toBeVisible();
    expect(receiptCalls(mockApi), 'a VIEW_LEADS row reads no receipt').toBe(0);

    await explorer.getByRole('button', { name: `Expand audit event ${EXPLORER_DECISION_ROW.event_id}` }).click();
    const receipt = explorer.getByTestId('audit-explorer-receipt').getByTestId('decision-receipt');
    await expect(receipt).toBeVisible();
    await expect(receipt).toHaveClass(/decision-receipt--compact/);
    await expect(receipt.getByTestId('decision-receipt-explorer-link')).toHaveCount(0);
    await expect(explorer.getByTestId('decision-receipt-announcement')).toHaveText('');
    expect(receiptCalls(mockApi), 'one receipt read for the decision row').toBe(1);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'admin explorer receipts');
  });

  for (const theme of ['dark', 'light'] as const) {
    test(`segment hues resolve to the score-part tokens and the surfaces are axe-clean (${theme})`, async ({ app, page, mockApi }) => {
      registerAnatomyProof(mockApi, 'trusted');
      registerExplorerDecisionRows(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute(B360);
      const gate = await openSpine(page);
      const hues = await gate.evaluate((root, modifiers) =>
        modifiers.map((modifier) => getComputedStyle(root.querySelector(`.score-spine__slice--${modifier}`) as Element).backgroundColor),
      PART_MODIFIERS);
      expect(hues).toEqual([...PART_HUES[theme]]);
      await expectAxeClean(page, { key: { route: 'score-anatomy', state: 'borrower-360-spine' }, theme, known: {}, include: '[data-testid="score-anatomy-spine"]' });

      await gate.locator('button.score-spine__seg').first().click();
      await expect(page.locator('.proof-drawer.is-open')).toBeVisible();
      await expectAxeClean(page, { key: { route: 'score-anatomy', state: 'proof-drawer' }, theme, known: {}, include: '.proof-drawer.is-open' });
      await page.keyboard.press('Escape');

      await app.gotoRoute('/admin-config#audit');
      await page.locator('#audit').getByRole('button', { name: `Expand audit event ${EXPLORER_DECISION_ROW.event_id}` }).click();
      await expect(page.getByTestId('audit-explorer-receipt').getByTestId('decision-receipt')).toBeVisible();
      await expectAxeClean(page, { key: { route: 'score-anatomy', state: 'explorer-receipt' }, theme, known: {}, include: '[data-testid="audit-explorer-receipt"]' });
    });
  }
});
