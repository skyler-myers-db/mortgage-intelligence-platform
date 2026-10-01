/**
 * Approval core, rendered (w5-approval-core; audit flow-03, states-06,
 * tables-07 / tables-02, wow-power-1; D-approval-flow-a1 / -d / -a3):
 *
 *   (1) a 6-row run over 2 offers: Approve stays aria-disabled with a
 *       status line until the rationale is written and every offer has a
 *       sample; Preview drafts exactly 3 (stratified); the first approve
 *       goes alone (the canary); every body carries one bulk_id, the
 *       shared rationale and bulk_sample or bulk_cohort;
 *   (2) one eligible row: the toolbar opens its review dialog, and nothing
 *       is approved until Enter;
 *   (3) a held Enter (auto-repeat) never confirms; a fresh press does;
 *   (4) bulk Reject: the canary, then the rest under one bulk_id; a row
 *       reads Rejected only after its 200, and a 500 row stays selected;
 *   (5) a canary refusal (422) sends nothing else and says why;
 *   (6) the receipt states how the certified copy was reviewed;
 *   (7) 500 loaded of 2,340: the scope line, and its campaign link lands
 *       on Portfolio Builder with the queue's states and all 11 filter
 *       keys, reading no /api/leads;
 *   (8) axe over the approve gate with samples, the reject gate and the
 *       scope line, dark and light.
 *
 * Synthetic only: masked ids in the production shape, no names or contacts.
 */
import type { Page } from '@playwright/test';
import type { DecisionReceipt, OutreachDraftResult } from '../../../src/lib/apiTypes';
import type { LeadSummary } from '../../../src/types';
import type { FixtureTheme } from './app';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { APPROVE_AUDIT_ID, RequestGate, ledgerReceipt } from './data/decisionReceipt';
import { LEAD_QUEUE_500, registerRejectRecorder } from './data/leadQueue';
import { outreachDraftFor } from './data/offers';
import { registerDraftEcho } from './data/queueKeyboard';
import { registerBulkApprove, registerDraftForRows, registerRankedQueue } from './data/queuePlace';
import { json, type MockApi } from './mockApi';
import { expect, test } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const RATIONALE = 'Q3 retention sweep, reviewed against current rules.';

function approvable(rows: readonly LeadSummary[]): LeadSummary[] {
  return rows.filter((lead) => (
    lead.approval_status === 'pending'
    && lead.marketing_eligible !== false
    && lead.dnc !== true
    && (lead.consent_status ?? 'opt_in') === 'opt_in'
  ));
}

const REFI = LEADS.find((lead) => lead.recommended_offer_code === 'refi')!;
const HELOC = LEADS.find((lead) => lead.recommended_offer_code === 'heloc')!;
/** Six approvable rows over two offers: four refi, then two HELOC. */
const SIX: readonly LeadSummary[] = approvable(LEADS).slice(0, 6).map((lead, index) => {
  const offer = index < 4 ? REFI : HELOC;
  return { ...lead, recommended_offer_code: offer.recommended_offer_code, recommended_offer: offer.recommended_offer };
});
const SIX_IDS = SIX.map((lead) => lead.borrower_id);
/** The stratified samples: the first refi and the first HELOC, then the next refi (the larger group). */
const SIX_SAMPLED = [SIX_IDS[0], SIX_IDS[1], SIX_IDS[4]];

function calls(mockApi: MockApi, method: string, path: string): number {
  return mockApi.calls.filter((call) => call.method === method && call.path === path).length;
}

async function selectAll(page: Page, count: number): Promise<void> {
  await page.getByTestId('lead-select-all').check();
  await expect(page.locator('.bulk-actions__label')).toHaveText(`${count} leads selected`);
}

/** Open the approve gate and preview its samples; resolves once every sample is ready. */
async function openGateWithSamples(page: Page, samples: number): Promise<void> {
  await page.getByTestId('lead-bulk-approve').click();
  await previewSamples(page, samples);
}

/** Preview the open gate's samples; resolves once every sample is ready. */
async function previewSamples(page: Page, samples: number): Promise<void> {
  const preview = page.getByTestId('lead-bulk-preview-samples');
  await expect(preview).toHaveText(`Preview ${samples} sample drafts (one per offer)`);
  await preview.click();
  await expect(page.locator('[data-testid="lead-bulk-samples"] li:not([aria-busy])')).toHaveCount(samples);
  await expect(preview, 'every offer has a ready sample').toHaveCount(0);
}

test.describe('(1)-(3) approving in bulk and one row at a time', () => {
  test('(1) arming, 3 stratified samples, the canary alone, then one bulk_id with sample and cohort rows', async ({ mockApi, app, page }) => {
    registerRankedQueue(mockApi, SIX);
    const drafts = registerDraftForRows(mockApi, SIX);
    const tracker = registerBulkApprove(mockApi, { holdFirst: 1 });
    await app.gotoRoute('/lead-queue');
    await selectAll(page, 6);

    const approve = page.getByTestId('lead-bulk-approve');
    await approve.click();
    const rationale = page.locator('.bulk-actions__rationale input');
    await expect(rationale).toBeFocused();
    const arming = page.getByTestId('lead-bulk-arming');
    await expect(approve).toHaveAttribute('aria-disabled', 'true');
    expect(await approve.evaluate((button) => (button as HTMLButtonElement).disabled), 'aria-disabled, never native disabled').toBe(false);
    await expect(arming).toHaveText('Write a shared rationale and preview one draft per offer before approving.');
    await expect(approve).toHaveAttribute('aria-describedby', new RegExp(await arming.getAttribute('id') ?? 'no-arming-id'));
    // Unarmed: activating Approve (Playwright's click() waits out
    // aria-disabled, so Enter) takes the reader to what is missing.
    await approve.focus();
    await page.keyboard.press('Enter');
    await expect(rationale).toBeFocused();
    expect(drafts.calls, 'opening and pressing an unarmed gate drafts nothing').toEqual([]);

    await previewSamples(page, 3);
    expect(drafts.calls, 'one per offer, topped up to three').toEqual(SIX_SAMPLED);
    await expect(arming).toHaveText('Write a shared rationale before approving.');
    await rationale.fill(RATIONALE);
    await expect(arming).toHaveCount(0);
    await expect(approve).not.toHaveAttribute('aria-disabled', 'true');
    expect(tracker.bodies, 'nothing approves before the click').toEqual([]);

    await approve.click();
    await expect.poll(() => tracker.bodies.length).toBe(1);
    await expect(page.getByTestId('lead-bulk-run-count')).toHaveText('0 of 6');
    expect(tracker.bodies, 'the canary goes alone').toHaveLength(1);
    tracker.gate.release();

    await expect(page.getByTestId('lead-bulk-result')).toContainText('6 of 6 approved.');
    expect(tracker.bodies.map((body) => body.borrower_id).sort()).toEqual([...SIX_IDS].sort());
    expect(new Set(tracker.bodies.map((body) => body.bulk_id)).size, 'one bulk_id for the run').toBe(1);
    expect(tracker.bodies[0].bulk_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(tracker.bodies.every((body) => body.bulk_rationale === RATIONALE)).toBe(true);
    const modes = Object.fromEntries(tracker.bodies.map((body) => [body.borrower_id, body.review_mode]));
    for (const id of SIX_IDS) {
      expect(modes[id], id).toBe(SIX_SAMPLED.includes(id) ? 'bulk_sample' : 'bulk_cohort');
    }
    expect(drafts.calls.length, 'the unsampled rows were drafted in the run').toBe(6);
  });

  test('(2) one eligible row: the toolbar opens its review dialog and nothing approves until Enter', async ({ mockApi, app, page }) => {
    const echo = registerDraftEcho(mockApi);
    const tracker = registerBulkApprove(mockApi);
    await app.gotoRoute('/lead-queue');
    const id = PRIMARY_BORROWER.borrower_id;
    await page.getByTestId(`lead-select-${id}`).check();
    await page.getByTestId('lead-bulk-approve').click();

    const confirm = page.locator('dialog.lead-approve-dialog').getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeFocused();
    expect(echo.calls, 'the review drafts the row it shows').toEqual([id]);
    expect(tracker.bodies, 'zero approve POSTs before Enter').toEqual([]);

    await page.keyboard.press('Enter');
    await expect.poll(() => tracker.bodies.length).toBe(1);
    expect(tracker.bodies[0]).toMatchObject({ borrower_id: id, review_mode: 'individual', bulk_id: null });
  });

  test('(3) a held Enter never confirms the review it opened; a fresh press does', async ({ mockApi, app, page }) => {
    // The draft is held: the Enter that opened the review is still down when it lands.
    const draftGate = new RequestGate();
    mockApi.register<OutreachDraftResult>('POST', '/api/outreach/draft', async (request) => {
      await draftGate.hold();
      return json<OutreachDraftResult>(outreachDraftFor(request));
    });
    const tracker = registerBulkApprove(mockApi);
    await app.gotoRoute('/lead-queue');
    const id = PRIMARY_BORROWER.borrower_id;
    await page.getByTestId(`lead-select-${id}`).check();

    // Enter on the toolbar's Approve opens the review; held, it auto-repeats onto Confirm.
    await page.getByTestId('lead-bulk-approve').focus();
    await page.keyboard.down('Enter');
    await expect.poll(() => draftGate.received, 'the review asked for its draft').toBe(true);
    draftGate.release();
    const confirm = page.locator('dialog.lead-approve-dialog').getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeFocused();
    await page.keyboard.down('Enter');
    await page.keyboard.down('Enter');
    await page.keyboard.up('Enter');
    await expect(confirm).toBeFocused();
    expect(tracker.bodies, 'the auto-repeat approved nothing').toEqual([]);

    await page.keyboard.press('Enter');
    await expect.poll(() => tracker.bodies.length).toBe(1);
    expect(tracker.bodies[0]).toMatchObject({ borrower_id: id, review_mode: 'individual' });
  });
});

test.describe('(4)-(5) bulk Reject and the canary', () => {
  test('(4) bulk Reject: the canary, then the rest under one bulk_id; Rejected only after a 200, a 500 stays selected', async ({ hygiene, mockApi, app, page }) => {
    hygiene.allow('console.error', /status of 500/);
    const rows = SIX.slice(0, 3);
    const [first, second, failing] = rows.map((lead) => lead.borrower_id);
    registerRankedQueue(mockApi, rows);
    const recorder = registerRejectRecorder(mockApi, { holdFirst: 1, failIds: [failing] });
    await app.gotoRoute('/lead-queue');
    await selectAll(page, 3);

    await page.getByTestId('lead-bulk-reject').click();
    const gate = page.getByTestId('lead-bulk-reject-gate');
    const reason = gate.getByTestId('lead-bulk-reject-reason');
    await expect(reason).toBeFocused();
    await expect(reason).toHaveValue('');
    await expect(reason.locator('option')).not.toContainText(['Do not call']);
    const confirm = gate.getByTestId('lead-bulk-reject-confirm');
    await expect(confirm).toHaveText('Reject 3 eligible');
    await expect(confirm).toHaveAttribute('aria-disabled', 'true');
    await confirm.focus();
    await page.keyboard.press('Enter');
    await expect(reason, 'no reason: focus goes there and nothing is sent').toBeFocused();
    await reason.selectOption('low_intent');
    await gate.getByTestId('lead-bulk-reject-note').fill('Q3 sweep: no intent signal in this cohort.');
    await expect(confirm).not.toHaveAttribute('aria-disabled', 'true');
    expect(recorder.bodies).toEqual([]);

    await confirm.click();
    await expect.poll(() => recorder.bodies.length).toBe(1);
    expect(recorder.bodies[0].borrower_id, 'the canary goes alone').toBe(first);
    await expect(page.getByTestId(`lead-approval-cell-${first}`), 'pessimistic: not Rejected before the 200').not.toContainText('Rejected');
    recorder.gate.release();

    await expect(page.getByTestId('lead-bulk-result')).toContainText('2 of 3 rejected, 1 failed.');
    expect(recorder.bodies.map((body) => body.borrower_id).sort()).toEqual([first, second, failing].sort());
    expect(new Set(recorder.bodies.map((body) => body.bulk_id)).size, 'one bulk_id for the run').toBe(1);
    expect(recorder.bodies.every((body) => body.rationale_code === 'low_intent'
      && body.rationale === 'Q3 sweep: no intent signal in this cohort.')).toBe(true);
    await expect(page.getByTestId(`lead-approval-cell-${first}`)).toContainText('Rejected');
    await expect(page.getByTestId(`lead-approval-cell-${second}`)).toContainText('Rejected');
    await expect(page.getByTestId(`lead-approval-cell-${failing}`)).not.toContainText('Rejected');
    await expect(page.getByTestId(`lead-select-${failing}`), 'the 500 row stays selected').toBeChecked();
    await expect(page.locator('.bulk-actions__label')).toHaveText('1 lead selected');
  });

  test('(5) a canary refused with a 422 sends nothing else, says why and keeps every row selected', async ({ hygiene, mockApi, app, page }) => {
    hygiene.allow('console.error', /status of 422/);
    registerRankedQueue(mockApi, SIX);
    registerDraftForRows(mockApi, SIX);
    const tracker = registerBulkApprove(mockApi, { refuseIds: [SIX_IDS[0]] });
    await app.gotoRoute('/lead-queue');
    await selectAll(page, 6);
    await openGateWithSamples(page, 3);
    await page.locator('.bulk-actions__rationale input').fill(RATIONALE);
    await page.getByTestId('lead-bulk-approve').click();

    await expect(page.getByTestId('lead-bulk-canary')).toHaveText(
      `Nothing else was sent: ${SIX_IDS[0]} was refused: bulk_rationale failed the governed text policy`,
    );
    await expect(page.getByTestId('lead-bulk-result')).toContainText('Nothing else was sent.');
    expect(tracker.bodies.map((body) => body.borrower_id)).toEqual([SIX_IDS[0]]);
    await expect(page.locator('.bulk-actions__label')).toHaveText('6 leads selected');
    for (const id of SIX_IDS) await expect(page.getByTestId(`lead-select-${id}`)).toBeChecked();
    await expect(page.locator('.bulk-actions__rationale input'), 'the gate stays open with its rationale').toHaveValue(RATIONALE);
  });
});

test('(6) the receipt states how the certified copy was reviewed', async ({ mockApi, app, page }) => {
  registerDraftEcho(mockApi);
  const tracker = registerBulkApprove(mockApi);
  mockApi.register('GET', '/api/audit/receipt/:id', ({ params }) => json<DecisionReceipt>(
    ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved', { review_mode: 'individual', bulk_id: null }),
  ));
  await app.gotoRoute('/lead-queue');
  await app.expandFirstLeadRow();
  const id = PRIMARY_BORROWER.borrower_id;
  const expanded = page.locator(`tr:has([data-testid="lead-approval-cell-${id}"]) + tr.tbl__expand`);
  await page.getByTestId(`lead-approve-${id}`).click();
  await expanded.getByTestId('lead-approve-review-confirm').click();

  const receipt = expanded.getByTestId('decision-receipt');
  await expect(receipt).toHaveAttribute('data-audit-event-id', APPROVE_AUDIT_ID);
  await expect(receipt.locator('[data-receipt-field="review"]')).toHaveText('Copy shown to the approver before approval');
  expect(tracker.bodies[0]).toMatchObject({ borrower_id: id, review_mode: 'individual' });
});

test('(7) 500 loaded of 2,340: the scope line, and the campaign link carries the filters and reads no leads', async ({ mockApi, app, page }) => {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', () => json<LeadSummary[]>([...LEAD_QUEUE_500], {
    headers: { 'X-Total-Matching': '2340', 'X-Returned-Rows': String(LEAD_QUEUE_500.length) },
  }));
  await app.gotoRoute('/lead-queue?states=IL,TX');
  await page.getByTestId('lead-select-all').check();

  const scope = page.getByTestId('lead-bulk-scope');
  await expect(scope).toContainText(/All [\d,]+ loaded borrowers are selected\./);
  await expect(scope).toContainText('2,340 match these filters; bulk actions apply only to borrowers shown here.');
  const link = scope.getByTestId('lead-bulk-campaign-handoff');
  await expect(link).toHaveText('Build a campaign from these filters');
  await expect(scope.getByTestId('lead-bulk-not-carried')).toHaveCount(0);
  const href = await link.getAttribute('href');
  const target = new URL(href ?? '', 'http://fixture.test');
  expect(target.pathname).toBe('/portfolio-builder');
  expect(target.searchParams.get('states')).toBe('IL,TX');
  expect([...target.searchParams.keys()].filter((key) => key !== 'states'), 'every Portfolio Builder filter key').toHaveLength(11);
  expect(target.searchParams.get('occupancy'), 'no narrower Portfolio Builder default applies silently').toBe('All');

  const reads = calls(mockApi, 'GET', '/api/leads');
  await link.click();
  await expect(page).toHaveURL(/\/portfolio-builder\?/);
  await app.settle();
  expect(calls(mockApi, 'GET', '/api/leads'), 'the handoff reads no leads').toBe(reads);
});

test.describe('(8) axe on the new surfaces', () => {
  for (const theme of THEMES) {
    test(`${theme}: the approve gate with samples, the reject gate and the scope line are clean`, async ({ mockApi, app, page }) => {
      registerRankedQueue(mockApi, SIX);
      registerDraftForRows(mockApi, SIX);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await selectAll(page, 6);
      // Six loaded of the fixture's contactable total: the scope line shows too.
      await expect(page.getByTestId('lead-bulk-scope')).toBeVisible();
      await openGateWithSamples(page, 3);
      await expectAxeClean(page, {
        key: { route: 'lead-queue', state: 'bulk-approve-gate' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: '[data-testid="lead-bulk-actions"]',
      });

      await page.getByTestId('lead-bulk-reject').click();
      await expect(page.getByTestId('lead-bulk-reject-gate')).toBeVisible();
      await expect(page.getByTestId('lead-bulk-review')).toHaveCount(0);
      await expectAxeClean(page, {
        key: { route: 'lead-queue', state: 'bulk-reject-gate' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: '[data-testid="lead-bulk-actions"]',
      });
    });
  }
});
