/**
 * Decision receipt (lane decision-receipt: wow-stage-3, flow-03, states-07).
 *
 * The approval moment ends in the real ledger row: after the approve /
 * reject POST resolves with an audit id, the app reads the row BACK and only
 * then renders the receipt. Pinned here at the rendered layer:
 *
 *  - Offer Orchestrator: while the approve POST is held open nothing of the
 *    decision renders (no receipt, no recording skeleton, no Approved chip,
 *    no read-back request); once it resolves, a "Recording decision…"
 *    skeleton holds the slot while the read-back is held open; only then the
 *    receipt renders with the POST's audit id and LEDGER-ONLY values
 *    (approver, copy hash, correlation id) the POST body never carried, one
 *    EvidenceChip per cited asset and the audit-explorer deep link. The old
 *    `audit: <uuid>` mono and `.burst` are gone; the stagger finishes under
 *    1.2 s and is instant under reduced motion; print clones the receipt only.
 *  - Lead Queue: the same pessimistic sequence in the expanded row after a
 *    row approve, with the lead payload's score line; the reveal plays once
 *    per decision (a collapse + re-expand shows the receipt finished).
 *  - Reject renders a rejected receipt with its reason code.
 *  - A refused read-back (403) renders the neutral "Recorded; receipt
 *    unavailable" state with the audit id still shown; a 404 does not claim
 *    the row is recorded and keeps "Retry read-back". Either way the page
 *    still states the decision: a durable rejection another approver opens
 *    keeps its "Rejected" chip when the receipt read is refused.
 *  - A failed write keeps the existing failure surface and reads nothing back.
 *  - Borrower 360 offers "Latest decision" when the lifecycle row carries an
 *    audit id, and the audit explorer honours `?audit_event_id=`; Clear (or
 *    the pinned chip's dismiss) drops that param and re-reads unpinned.
 *
 * Holds are test-controlled gates (RequestGate), never wall-clock delays, so
 * the in-flight assertions hold under any machine load.
 *
 * Mutation check (reported in the lane summary): rendering the receipt from
 * the POST body instead of the read-back fails the "reads back the ledger
 * row" tests on the ledger-only fields.
 */
import type { Locator, Page } from '@playwright/test';
import type { BorrowerLifecycle } from '../../../src/types';
import type { DecisionReceipt } from '../../../src/lib/apiTypes';
import { PRIMARY_BORROWER } from './data/borrowers';
import {
  APPROVE_AUDIT_ID,
  LEDGER_APPROVER,
  LEDGER_COPY_HASH,
  LEDGER_CORRELATION_ID,
  LEDGER_EVIDENCE_ASSETS,
  REJECT_AUDIT_ID,
  RequestGate,
  approveResult,
  decidedLifecycle,
  ledgerReceipt,
  rejectResult,
} from './data/decisionReceipt';
import { SNAPSHOT_AT } from './data/reference';
import { json, type MockApi } from './mockApi';
import { expect, test } from './test';

const BORROWER_ID = PRIMARY_BORROWER.borrower_id;
const EXPLORER_HREF = `/admin-config?audit_event_id=${APPROVE_AUDIT_ID}#audit`;
/** The Approved decision chip, not any chip that merely mentions approval. */
const APPROVED_CHIP = /^\s*Approved\b/;
/** Longest the receipt stagger may run (brief: "under 1.2 s"). */
const STAGGER_BUDGET_MS = 1_200;

interface HeldApproveFlow {
  approveGate: RequestGate;
  receiptGate: RequestGate;
  /** Ordered request log: when each write / read reached or left the ledger. */
  log: string[];
}

/** Hold the approve POST and the receipt GET open until the test releases them. */
function registerHeldApproveFlow(mockApi: MockApi): HeldApproveFlow {
  const flow: HeldApproveFlow = { approveGate: new RequestGate(), receiptGate: new RequestGate(), log: [] };
  mockApi.register('POST', '/api/outreach/approve', async () => {
    flow.log.push('approve:received');
    await flow.approveGate.hold();
    flow.log.push('approve:replied');
    return approveResult(APPROVE_AUDIT_ID);
  });
  mockApi.register('GET', '/api/audit/receipt/:id', async ({ params }) => {
    flow.log.push(`receipt:requested:${params.id}`);
    await flow.receiptGate.hold();
    return json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved'));
  });
  return flow;
}

function receiptCalls(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.path.includes('/audit/receipt/')).length;
}

/** The explorer's ledger page reads, in request order. */
function explorerPageCalls(mockApi: MockApi): MockApi['calls'] {
  return mockApi.calls.filter((call) => call.path.endsWith('/audit/events/page'));
}

/** The ledger-only values: present only when the receipt was read back from the row. */
async function expectLedgerRow(receipt: Locator): Promise<void> {
  await expect(receipt).toHaveAttribute('data-audit-event-id', APPROVE_AUDIT_ID);
  await expect(receipt.locator('[data-receipt-field="audit"]')).toHaveText(APPROVE_AUDIT_ID);
  await expect(receipt.locator('[data-receipt-field="approver"]')).toHaveText(LEDGER_APPROVER);
  await expect(receipt.locator('[data-receipt-field="copy-hash"]')).toHaveText(LEDGER_COPY_HASH);
  await expect(receipt.locator('[data-receipt-field="correlation"]')).toHaveText(LEDGER_CORRELATION_ID);
  await expect(receipt.locator('[data-testid="decision-receipt-evidence"] .evidence-chip')).toHaveCount(
    LEDGER_EVIDENCE_ASSETS.length,
  );
}

/** Latest end (delay + duration) of any receipt reveal animation, and the animation names in use. */
async function revealMotion(receipt: Locator): Promise<{ maxEndMs: number; names: string[] }> {
  return receipt.evaluate((card) => {
    const seconds = (value: string) => Math.max(...value.split(',').map((part) => Number.parseFloat(part) || 0));
    const animated = card.querySelectorAll('.decision-receipt__row, .decision-receipt__section, .surface__ft');
    let maxEndMs = 0;
    const names = new Set<string>();
    animated.forEach((element) => {
      const style = getComputedStyle(element);
      names.add(style.animationName);
      maxEndMs = Math.max(maxEndMs, (seconds(style.animationDelay) + seconds(style.animationDuration)) * 1000);
    });
    return { maxEndMs, names: [...names] };
  });
}

async function approvedChips(page: Page): Promise<number> {
  return page.locator('#main-content .chip', { hasText: APPROVED_CHIP }).count();
}

test.describe('decision receipt', () => {
  test('offer orchestrator reads back the ledger row only after the approve write resolves', async ({ app, page, mockApi }) => {
    const flow = registerHeldApproveFlow(mockApi);
    await app.gotoRoute(`/offer-orchestrator/${BORROWER_ID}`);
    const main = page.locator('#main-content');
    const gate = page.getByTestId('offer-action-bar');
    const approve = gate.getByRole('button', { name: 'Approve outreach' });
    await expect(approve).toBeEnabled();
    await approve.click();

    // The approve POST is held open: nothing of the decision renders yet.
    await expect.poll(() => flow.approveGate.received, 'the approve POST reached the ledger').toBe(true);
    await expect(main.getByTestId('decision-receipt')).toHaveCount(0);
    await expect(main.getByTestId('decision-receipt-pending')).toHaveCount(0);
    await expect(gate.locator('.approval'), 'the gate stays up while the write is held').toBeVisible();
    expect(await approvedChips(page), 'no Approved chip before the write resolves').toBe(0);
    expect(receiptCalls(mockApi), 'nothing is read back before the write resolves').toBe(0);

    // The write resolved; the read-back is held open: a recording skeleton only.
    flow.approveGate.release();
    const pending = main.getByTestId('decision-receipt-pending');
    await expect(pending).toBeVisible();
    await expect(pending).toContainText('Recording decision…');
    await expect(main.getByTestId('decision-receipt')).toHaveCount(0);
    await expect
      .poll(() => flow.log, 'the read-back was requested only after the write replied, for the POST audit id')
      .toEqual(['approve:received', 'approve:replied', `receipt:requested:${APPROVE_AUDIT_ID}`]);

    // The ledger row came back: the receipt renders from it.
    flow.receiptGate.release();
    const receipt = main.getByTestId('decision-receipt');
    await expect(receipt).toBeVisible();
    await expect(pending).toHaveCount(0);
    await expectLedgerRow(receipt);
    await expect(receipt.locator('[data-receipt-field="borrower"]')).toHaveText(BORROWER_ID);
    await expect(receipt.locator('.chip', { hasText: APPROVED_CHIP })).toBeVisible();
    await expect(receipt.getByTestId('decision-receipt-explorer-link')).toHaveAttribute('href', EXPLORER_HREF);
    await expect(receipt.getByRole('button', { name: `Copy audit id ${APPROVE_AUDIT_ID}` })).toBeVisible();
    await expect(gate, 'the decided borrower has no approval gate').toHaveCount(0);
    // The pre-receipt surfaces are retired.
    await expect(page.locator('.burst')).toHaveCount(0);
    await expect(page.getByText(/^audit: /)).toHaveCount(0);

    // A decision made here plays the one-shot reveal: instant under reduced
    // motion (the harness default), and inside the 1.2 s budget otherwise.
    await expect(receipt).toHaveClass(/decision-receipt--reveal/);
    expect((await revealMotion(receipt)).names).toEqual(['none']);
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    const motion = await revealMotion(receipt);
    expect(motion.names).toEqual(['decision-receipt-in']);
    expect(motion.maxEndMs, 'the stagger finishes inside the budget').toBeLessThan(STAGGER_BUDGET_MS);
    await page.emulateMedia({ reducedMotion: 'reduce' });

    // Print mode clones the receipt only and cleans up after afterprint.
    await page.evaluate(() => {
      window.print = () => undefined;
    });
    await receipt.getByRole('button', { name: 'Print receipt' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-print', 'decision-receipt');
    const printed = page.locator('.decision-receipt-print [data-testid="decision-receipt"]');
    await expect(printed).toHaveAttribute('data-audit-event-id', APPROVE_AUDIT_ID);
    await expect(page.locator('.decision-receipt-print')).toHaveCount(1);
    await page.evaluate(() => {
      window.dispatchEvent(new Event('afterprint'));
    });
    await expect(page.locator('.decision-receipt-print')).toHaveCount(0);
    await expect(page.locator('html')).not.toHaveAttribute('data-print', /./);
    expect(receiptCalls(mockApi), 'one read-back per decision').toBe(1);
  });

  // The Default preset fits the 1440 scrollport (queue-layout lane); the
  // Sales ops preset keeps the table wider than it, so clicking Approve
  // scrolls it right. The receipt must fit the visible width in both.
  for (const view of ['default', 'sales-ops'] as const) {
  test(`lead queue reads back the ledger row in the expanded row after a row approve (${view} view)`, async ({ app, page, mockApi }) => {
    const flow = registerHeldApproveFlow(mockApi);
    await app.gotoRoute(view === 'sales-ops' ? '/lead-queue?view=sales-ops' : '/lead-queue');
    await app.expandFirstLeadRow();
    // The expanded row that belongs to this borrower: the sibling of the row
    // holding its approval cell (the cell outlives the Approve button).
    const approvalCell = page.getByTestId(`lead-approval-cell-${BORROWER_ID}`);
    const expanded = page.locator(`tr:has([data-testid="lead-approval-cell-${BORROWER_ID}"]) + tr.tbl__expand`);
    await expect(expanded, 'the first ranked row is the primary fixture borrower').toBeVisible();

    await page.getByTestId(`lead-approve-${BORROWER_ID}`).click();
    await expect.poll(() => flow.approveGate.received, 'the row approve POST reached the ledger').toBe(true);
    await expect(expanded.getByTestId('decision-receipt')).toHaveCount(0);
    await expect(expanded.getByTestId('decision-receipt-pending')).toHaveCount(0);
    await expect(approvalCell.locator('.chip', { hasText: APPROVED_CHIP })).toHaveCount(0);
    expect(receiptCalls(mockApi), 'nothing is read back before the write resolves').toBe(0);

    flow.approveGate.release();
    await expect(expanded.getByTestId('decision-receipt-pending')).toBeVisible();
    await expect(approvalCell.locator('.chip', { hasText: APPROVED_CHIP })).toBeVisible();
    await expect(expanded.getByTestId('decision-receipt')).toHaveCount(0);
    await expect
      .poll(() => flow.log, 'the read-back was requested only after the write replied')
      .toEqual(['approve:received', 'approve:replied', `receipt:requested:${APPROVE_AUDIT_ID}`]);

    flow.receiptGate.release();
    const receipt = expanded.getByTestId('decision-receipt');
    await expect(receipt).toBeVisible();
    await expectLedgerRow(receipt);
    await expect(receipt).toHaveClass(/decision-receipt--compact/);
    await expect(receipt).toHaveClass(/decision-receipt--reveal/);
    await expect(receipt.getByTestId('decision-receipt-score')).toContainText(String(PRIMARY_BORROWER.opportunity_score));
    await expect(expanded.getByTestId('decision-receipt-announcement')).toHaveText(
      `Decision receipt recorded: Approved, audit event ${APPROVE_AUDIT_ID}`,
    );
    await expect(receipt.getByTestId('decision-receipt-explorer-link')).toHaveAttribute('href', EXPLORER_HREF);

    // The expanded cell spans every nowrap column, so the table is wider than
    // its scrollport, and clicking Approve (last column) scrolled it right.
    // The receipt must still sit inside the visible width at that offset,
    // with its explorer link reachable without scrolling back.
    const fit = await receipt.evaluate((card) => {
      const scrollport = card.closest('.tbl-wrap');
      const link = card.querySelector('[data-testid="decision-receipt-explorer-link"]');
      if (!scrollport || !link) return null;
      const port = scrollport.getBoundingClientRect();
      const box = card.getBoundingClientRect();
      return {
        tableOverflows: scrollport.scrollWidth > scrollport.clientWidth,
        portLeft: port.left,
        portRight: port.left + scrollport.clientWidth,
        cardLeft: box.left,
        cardRight: box.right,
        linkRight: link.getBoundingClientRect().right,
      };
    });
    expect(fit, 'the receipt sits inside the lead table scrollport').not.toBeNull();
    if (!fit) return;
    expect(fit.tableOverflows, 'precondition: only Sales ops is wider than its scrollport').toBe(view === 'sales-ops');
    expect(fit.cardLeft, 'the receipt starts inside the visible width').toBeGreaterThanOrEqual(fit.portLeft - 1);
    expect(fit.cardRight, 'the receipt ends inside the visible width').toBeLessThanOrEqual(fit.portRight + 1);
    expect(fit.linkRight, 'the explorer link is visible without scrolling').toBeLessThanOrEqual(fit.portRight + 1);

    // motion-06: the reveal plays once per decision. Collapse and re-expand
    // the row: the receipt comes back finished, from the same read-back.
    const rowToggle = page.locator(`tr:has([data-testid="lead-approval-cell-${BORROWER_ID}"]) [aria-expanded]`).first();
    await rowToggle.click();
    await expect(rowToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(expanded).toHaveCount(0);
    await rowToggle.click();
    await expect(rowToggle).toHaveAttribute('aria-expanded', 'true');
    const again = expanded.getByTestId('decision-receipt');
    await expect(again).toBeVisible();
    await expectLedgerRow(again);
    await expect(again, 'the re-expanded receipt does not replay the reveal').not.toHaveClass(/decision-receipt--reveal/);
    expect(receiptCalls(mockApi), 'the re-expanded receipt reuses the read-back').toBe(1);
  });
  }

  test('reject reads back a rejected receipt with its reason code', async ({ app, page, mockApi }) => {
    mockApi.register('POST', '/api/outreach/reject', () => rejectResult(REJECT_AUDIT_ID));
    mockApi.register('GET', '/api/audit/receipt/:id', ({ params }) =>
      json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'rejected')),
    );
    await app.gotoRoute(`/offer-orchestrator/${BORROWER_ID}`);

    await page.getByRole('button', { name: 'Reject', exact: true }).click();
    await page.getByRole('button', { name: 'Confirm reject' }).click();

    const receipt = page.getByTestId('decision-receipt');
    await expect(receipt).toBeVisible();
    await expect(receipt).toHaveAttribute('data-audit-event-id', REJECT_AUDIT_ID);
    await expect(receipt).toHaveClass(/decision-receipt--rejected/);
    await expect(receipt.locator('.chip', { hasText: /^\s*Rejected\b/ })).toBeVisible();
    await expect(receipt.locator('[data-receipt-field="reason"]')).toHaveText('Low intent');
    await expect(receipt.locator('[data-receipt-field="approver"]')).toHaveText(LEDGER_APPROVER);
    await expect(receipt.locator('[data-receipt-field="copy-hash"]')).toHaveCount(0);
    await expect(page.getByText(/^audit: /)).toHaveCount(0);
  });

  test('a refused read-back (403) shows "Recorded; receipt unavailable" with the audit id', async ({ app, page, mockApi }) => {
    mockApi.register('POST', '/api/outreach/approve', () => approveResult(APPROVE_AUDIT_ID));
    app.degrade('/api/audit/receipt/:id', { method: 'GET', status: 403, body: { detail: 'forbidden' } });
    await app.gotoRoute(`/offer-orchestrator/${BORROWER_ID}`);

    await page.getByRole('button', { name: 'Approve outreach' }).click();

    const unavailable = page.getByTestId('decision-receipt-unavailable');
    await expect(unavailable).toBeVisible();
    await expect(unavailable).toContainText('Recorded; receipt unavailable');
    // The approve POST resolved: the outcome it wrote stays on the card.
    await expect(unavailable.getByTestId('decision-receipt-outcome').locator('.chip--success')).toHaveText(APPROVED_CHIP);
    await expect(unavailable.getByTestId('decision-receipt-audit-id')).toContainText(APPROVE_AUDIT_ID);
    await expect(unavailable.getByRole('button', { name: 'Copy audit id' })).toBeVisible();
    await expect(unavailable.getByRole('button', { name: 'Retry read-back' })).toHaveCount(0);
    await expect(page.getByTestId('decision-receipt')).toHaveCount(0);
    await expect(page.locator('#main-content [role="alert"]')).toHaveCount(0);
  });

  test('a read-back that finds no ledger row (404) is not shown as recorded and keeps the retry', async ({ app, page, mockApi }) => {
    mockApi.register('POST', '/api/outreach/approve', () => approveResult(APPROVE_AUDIT_ID));
    app.degrade('/api/audit/receipt/:id', { method: 'GET', status: 404, body: { detail: 'audit event not found' } });
    await app.gotoRoute(`/offer-orchestrator/${BORROWER_ID}`);

    await page.getByRole('button', { name: 'Approve outreach' }).click();

    const unavailable = page.getByTestId('decision-receipt-unavailable');
    await expect(unavailable).toBeVisible();
    await expect(unavailable).toHaveAttribute('data-receipt-state', 'not-found');
    await expect(unavailable).toContainText('Ledger row not found');
    await expect(unavailable).not.toContainText('is in the audit ledger');
    await expect(unavailable).not.toContainText('Recorded');
    await expect(unavailable).toContainText('The write returned this audit id');
    await expect(unavailable.getByTestId('decision-receipt-outcome')).toHaveText(APPROVED_CHIP);
    await expect(unavailable.getByTestId('decision-receipt-audit-id')).toContainText(APPROVE_AUDIT_ID);
    await expect(unavailable.getByRole('button', { name: 'Retry read-back' })).toBeVisible();
    await expect(page.getByTestId('decision-receipt')).toHaveCount(0);
  });

  test('a durable rejection whose receipt read-back is refused (403) still says Rejected', async ({ app, page, mockApi }) => {
    mockApi.register('GET', '/api/borrowers/:id/lifecycle', ({ params }) =>
      json<BorrowerLifecycle>(decidedLifecycle(params.id, 'rejected', REJECT_AUDIT_ID, SNAPSHOT_AT)),
    );
    app.degrade('/api/audit/receipt/:id', { method: 'GET', status: 403, body: { detail: 'forbidden' } });
    await app.gotoRoute(`/offer-orchestrator/${BORROWER_ID}`);

    const unavailable = page.getByTestId('decision-receipt-unavailable');
    await expect(unavailable).toBeVisible();
    await expect(unavailable).toHaveAttribute('data-receipt-state', 'forbidden');
    // The decision itself stays on the page: nothing else here says Rejected
    // (the review panel and the approval gate are hidden).
    await expect(unavailable.getByTestId('decision-receipt-outcome').locator('.chip--danger')).toHaveText(/^\s*Rejected\s*$/);
    await expect(page.getByTestId('offer-action-bar')).toHaveCount(0);
    await expect(unavailable).toContainText('Recorded; receipt unavailable');
    await expect(unavailable.getByTestId('decision-receipt-audit-id')).toContainText(REJECT_AUDIT_ID);
    await expect(unavailable).not.toContainText('The write returned');
    await expect(page.getByTestId('decision-receipt')).toHaveCount(0);
    await expect(page.getByTestId('decision-receipt-pending')).toHaveCount(0);
    // A passive read of an earlier decision is not announced on page load.
    await expect(page.getByTestId('decision-receipt-announcement')).toHaveText('');
  });

  test('a failed approve write keeps the failure surface and reads nothing back', async ({ app, page, mockApi }) => {
    app.degrade('/api/outreach/approve', { method: 'POST', status: 500, body: { detail: 'audit write failed (fixture)' } });
    await app.gotoRoute(`/offer-orchestrator/${BORROWER_ID}`);
    const approve = page.getByRole('button', { name: 'Approve outreach' });

    await approve.click();

    const failure = page.locator('#main-content .surface--danger[role="alert"]');
    await expect(failure).toContainText("Couldn't write approval: audit write failed (fixture)");
    await expect(approve).toBeEnabled();
    await expect(page.getByTestId('decision-receipt')).toHaveCount(0);
    await expect(page.getByTestId('decision-receipt-pending')).toHaveCount(0);
    await expect(page.getByTestId('decision-receipt-unavailable')).toHaveCount(0);
    expect(await approvedChips(page)).toBe(0);
    expect(receiptCalls(mockApi)).toBe(0);
  });

  test('borrower 360 offers the latest decision receipt when the lifecycle row carries an audit id', async ({ app, page, mockApi }) => {
    mockApi.register('GET', '/api/borrowers/:id/lifecycle', ({ params }) =>
      json<BorrowerLifecycle>({
        borrower_id: params.id,
        approval_status: 'approved',
        outreach_status: 'queued',
        approval_id: 'apr-fixture-0001',
        audit_event_id: APPROVE_AUDIT_ID,
        approved_at: '2026-07-14T15:00:04Z',
        synced_at: SNAPSHOT_AT,
        assignment: null,
        latest_disposition: null,
      }),
    );
    mockApi.register('GET', '/api/audit/receipt/:id', ({ params }) =>
      json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')),
    );
    await app.gotoRoute(`/borrower-360/${BORROWER_ID}`);

    expect(receiptCalls(mockApi), 'nothing is read back until the approver asks').toBe(0);
    const toggle = page.getByTestId('latest-decision-toggle');
    await expect(toggle).toHaveText('Latest decision');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');

    const receipt = page.getByTestId('decision-receipt');
    await expect(receipt).toBeVisible();
    await expectLedgerRow(receipt);
    // A durable decision is shown finished: no reveal replays on a later
    // visit, and reading it back is not announced as a new decision.
    await expect(receipt).not.toHaveClass(/decision-receipt--reveal/);
    await expect(page.getByTestId('decision-receipt-announcement')).toHaveText('');
    await expect(page.locator(`#${await toggle.getAttribute('aria-controls')}`)).toContainText('Decision receipt');
  });

  test('borrower 360 offers no latest decision when the lifecycle row has no audit id', async ({ app, page, mockApi }) => {
    await app.gotoRoute(`/borrower-360/${BORROWER_ID}`);
    await expect(page.getByTestId('latest-decision-toggle')).toHaveCount(0);
    expect(receiptCalls(mockApi)).toBe(0);
  });

  test('the audit explorer honours the receipt deep link', async ({ app, page, mockApi }) => {
    await app.gotoRoute(EXPLORER_HREF);

    const explorer = page.locator('#audit');
    await expect(explorer.locator('.chip', { hasText: `audit event = ${APPROVE_AUDIT_ID}` })).toBeVisible();
    const pageCall = mockApi.calls.find(
      (call) => call.path.endsWith('/audit/events/page') && call.search.includes(`event_id=${APPROVE_AUDIT_ID}`),
    );
    expect(pageCall, 'the explorer asked the ledger for that one row').toBeDefined();
  });

  for (const control of ['Clear', 'the chip dismiss'] as const) {
    test(`${control} in the audit explorer drops the receipt deep link`, async ({ app, page, mockApi }) => {
      await app.gotoRoute(EXPLORER_HREF);
      const explorer = page.locator('#audit');
      const pinned = explorer.locator('.chip', { hasText: `audit event = ${APPROVE_AUDIT_ID}` });
      await expect(pinned).toBeVisible();
      const pinnedReads = explorerPageCalls(mockApi).length;
      expect(pinnedReads, 'precondition: the pinned read happened').toBeGreaterThan(0);

      const clear = explorer.getByRole('button', { name: 'Clear', exact: true });
      await expect(clear).toBeEnabled();
      if (control === 'Clear') await clear.click();
      else await pinned.getByRole('button', { name: 'Remove audit event filter' }).click();

      await expect(pinned).toHaveCount(0);
      await expect(page, 'the deep-link param is gone from the URL').not.toHaveURL(/[?&]audit_event_id=/);
      await expect(page, 'the page stays on the explorer').toHaveURL(/\/admin-config#audit$/);
      await expect
        .poll(() => explorerPageCalls(mockApi).length, 'the explorer re-read the ledger unpinned')
        .toBeGreaterThan(pinnedReads);
      const next = explorerPageCalls(mockApi)[pinnedReads];
      expect(new URLSearchParams(next.search).has('event_id'), 'the next page read is not pinned').toBe(false);
      await expect(clear, 'nothing is left to clear').toBeDisabled();
    });
  }
});
