/**
 * The Triage deck (D-approval-flow-a2; deviation:triage-deck), proven on the
 * production build at 1440x900, in both themes with the Console closed and
 * open:
 *
 *  - entering, J x3, K, Skip, Esc and the ?row= restore open no audited read
 *    (AUDITED_READS: VIEW_LEADS, VIEW_BORROWER, VIEW_BORROWER_PROOF,
 *    DRAFT_OUTREACH, RECOMMEND_OFFER, PROPERTY_LOOKUP);
 *  - A is exactly one POST /api/outreach/draft; Enter is one approve with
 *    review_mode 'triage' and the shown generation_id; the next card shows
 *    only after the (held) 200; Confirm is inside the viewport;
 *  - an Enter held from A across the draft landing approves nothing, a fresh
 *    Enter approves once;
 *  - R, a reason and Confirm rejects and advances; the summary appears and
 *    Back to table focuses the last row;
 *  - a 409 offers "Review draft again";
 *  - a loan officer's session shows no Triage entry and ?mode=triage is
 *    stripped; Copy link never carries the mode;
 *  - a filter change while the deck is open never re-snapshots it: the deck
 *    stays mounted on the placeholder rows (GET /api/leads?state=TX held)
 *    and the settled rows only drop cards (brief 6.4);
 *  - axe is clean on the deck with the draft on screen, both themes: the
 *    draft copy is its own scroll region with no tabindex (the oxlint
 *    ratchet bans one), reachable through its evidence chips
 *    (scrollable-region-focusable).
 */
import type { Page } from '@playwright/test';
import type { SessionResponse } from '../../../src/types';
import { LEADS } from './data/borrowers';
import { leadFixtures } from './data/leads';
import { SESSION } from './data/shell';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { RequestGate } from './data/decisionReceipt';
import { registerTriageWrites, triageDraft, triageGeneration } from './data/triage';
import { json, type FixtureRequest, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

/** The deck's cards: every pending row, in rank order (rows 1 and 9 are decided). */
const CARDS = LEADS.filter((lead) => lead.approval_status === 'pending').map((lead) => lead.borrower_id);

const position = (page: Page) => page.getByTestId('triage-position');
const heading = (page: Page) => page.locator('.triage__card .triage__name');

async function enterDeck(page: Page): Promise<void> {
  await page.getByTestId('lead-triage-enter').click();
  await expect(page.getByTestId('triage-deck')).toBeVisible();
  await expect(heading(page)).toBeFocused();
}

/** The default GET /api/leads, holding the `?state=<code>` page until released. */
function holdLeadsForState(mockApi: MockApi, code: string): RequestGate {
  const defaultLeads = leadFixtures.find((entry) => entry.method === 'GET' && entry.pattern === '/api/leads');
  if (!defaultLeads) throw new Error('the default GET /api/leads fixture is missing');
  const gate = new RequestGate();
  mockApi.register('GET', '/api/leads', async (request: FixtureRequest) => {
    const states = [request.query.get('state'), ...(request.query.get('states')?.split(',') ?? [])];
    if (states.includes(code)) await gate.hold();
    return defaultLeads.handler(request);
  });
  return gate;
}

function draftCalls(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && /\/outreach\/draft$/.test(call.path)).length;
}

for (const theme of FIXTURE_THEMES) {
  for (const consoleOpen of [false, true]) {
    test.describe(`${theme}, Console ${consoleOpen ? 'open' : 'closed'}`, () => {
      test('moves write nothing; A drafts once; Enter approves once as triage; it advances only after the 200', async ({ app, mockApi, page }) => {
        const writes = registerTriageWrites(mockApi, { holdApprove: true });
        await app.setTheme(theme);
        await app.gotoRoute('/lead-queue');
        if (consoleOpen) await app.openConsole();
        const n = markNaturalLoad(mockApi);

        await enterDeck(page);
        await expect(page).toHaveURL(/[?&]mode=triage(&|$)/);
        await expect(position(page)).toHaveText(`Borrower 1 of ${CARDS.length}`);
        for (let press = 0; press < 3; press += 1) await page.keyboard.press('j');
        await expect(position(page)).toHaveText(`Borrower 4 of ${CARDS.length}`);
        await page.keyboard.press('k');
        await expect(position(page)).toHaveText(`Borrower 3 of ${CARDS.length}`);
        await page.getByTestId('triage-skip').click();
        await expect(position(page)).toHaveText(`Borrower 4 of ${CARDS.length}`);
        expectNoAuditedReadSince(mockApi, n, 'triage moves');

        const card = CARDS[3];
        await page.keyboard.press('a');
        const confirm = page.getByTestId('lead-approve-review-confirm');
        await expect(page.locator('[data-testid="lead-approve-review"][data-review-phase="ready"]')).toBeVisible();
        await expect(confirm).toBeFocused();
        expect(draftCalls(mockApi), 'A is exactly one draft').toBe(1);
        expect(writes.drafts).toEqual([card]);
        // The draft copy scrolls on its own; Confirm stays on screen.
        await expect(page.getByRole('region', { name: `Draft outreach for ${card}` })).toBeVisible();
        const box = await confirm.boundingBox();
        const viewport = page.viewportSize();
        expect(box && viewport && box.y >= 0 && box.y + box.height <= viewport.height, 'Confirm inside the viewport').toBe(true);
        if (!consoleOpen) {
          await expectAxeClean(page, {
            key: { route: 'lead-queue', state: 'triage' }, theme, known: KNOWN_VIOLATIONS, include: '[data-testid="triage-deck"]',
          });
        }

        await page.keyboard.press('Enter');
        await expect.poll(() => writes.approvals.length).toBe(1);
        expect(writes.approvals[0]).toMatchObject({
          borrower_id: card,
          draft_generation_id: triageGeneration(card),
          review_mode: 'triage',
        });
        expect(writes.approvals[0].bulk_id ?? null).toBeNull();
        await expect(position(page), 'pessimistic: no advance while the approve is held').toHaveText(`Borrower 4 of ${CARDS.length}`);
        writes.approveGate.release();
        await expect(position(page)).toHaveText(`Borrower 5 of ${CARDS.length}`);
        await expect(heading(page)).toBeFocused();
        // J x3 skipped cards 1-3; K and Skip revisited card 3.
        await expect(page.getByTestId('triage-progress')).toContainText('Approved 1 · Rejected 0 · Skipped 3');
        await expect(page.getByTestId('triage-last-receipt')).toContainText(`Approved ${card} · audit`);
        await app.settle();
        const exitMark = markNaturalLoad(mockApi);

        await page.keyboard.press('Escape');
        await expect(page.getByTestId('triage-deck')).toHaveCount(0);
        await expect(page).not.toHaveURL(/[?&]mode=/);
        await expect(page).toHaveURL(new RegExp(`[?&]row=${CARDS[4]}(&|$)`));
        await expect(page.locator(`tr[data-borrower-row="${CARDS[4]}"] .lead-table__borrower-btn`)).toBeFocused();
        expect(draftCalls(mockApi), 'the ?row= restore drafts nothing').toBe(1);
        expect(writes.approvals).toHaveLength(1);
        await app.settle();
        expectNoAuditedReadSince(mockApi, exitMark, 'Esc and the ?row= restore');
      });
    });
  }
}

test.describe('Triage deck decisions', () => {
  test('an Enter held from Review draft (A) across the landing approves nothing; a fresh Enter approves once', async ({ app, mockApi, page }) => {
    const writes = registerTriageWrites(mockApi);
    const draftGate = new RequestGate();
    mockApi.register('POST', '/api/outreach/draft', async (request) => {
      writes.drafts.push(CARDS[0]);
      await draftGate.hold();
      return json(triageDraft(request));
    });
    await app.gotoRoute('/lead-queue');
    await enterDeck(page);
    // Enter on Review draft (A) opens the review; held, it auto-repeats onto Confirm.
    await page.getByTestId('triage-review').focus();
    await page.keyboard.down('Enter');
    await expect.poll(() => draftGate.received, 'the review asked for its draft').toBe(true);
    draftGate.release();
    const confirm = page.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeFocused();
    await page.keyboard.down('Enter');
    await page.keyboard.down('Enter');
    await page.keyboard.up('Enter');
    expect(writes.approvals, 'the auto-repeat approved nothing').toHaveLength(0);
    await page.keyboard.press('Enter');
    await expect.poll(() => writes.approvals.length).toBe(1);
    expect(writes.drafts).toHaveLength(1);
  });

  test('R, a reason and Confirm rejects and advances; the summary and Back to table land on the last row', async ({ app, mockApi, page }) => {
    const writes = registerTriageWrites(mockApi);
    await app.gotoRoute('/lead-queue');
    await enterDeck(page);
    await page.keyboard.press('r');
    const reason = page.getByTestId('lead-reject-reason');
    await expect(reason).toBeFocused();
    // R then Enter on Confirm (aria-disabled, no reason yet): nothing is sent.
    await page.getByTestId('lead-reject-confirm').focus();
    await page.keyboard.press('Enter');
    await expect(reason, 'the missing reason takes focus').toBeFocused();
    expect(writes.rejects, 'no reason: nothing sent').toHaveLength(0);
    await reason.selectOption({ index: 1 });
    await page.getByTestId('lead-reject-confirm').click();
    await expect.poll(() => writes.rejects.length).toBe(1);
    await expect(position(page)).toHaveText(`Borrower 2 of ${CARDS.length}`);
    await expect(page.getByTestId('triage-progress')).toContainText('Rejected 1');

    // K back to the decided card: its outcome, no gate.
    await page.keyboard.press('k');
    await expect(page.getByTestId('triage-outcome')).toBeVisible();
    await expect(page.getByTestId('triage-gate')).toHaveCount(0);
    // Skip to the end.
    for (let index = 0; index < CARDS.length; index += 1) await page.keyboard.press('j');
    const summary = page.getByTestId('triage-summary');
    await expect(summary).toContainText(`Approved 0 · Rejected 1 · Skipped ${CARDS.length - 1}`);
    await expect(page.getByTestId('triage-review-skipped')).toHaveText(`Review skipped (${CARDS.length - 1})`);
    await page.getByTestId('triage-summary-exit').click();
    const last = CARDS[CARDS.length - 1];
    await expect(page).toHaveURL(new RegExp(`[?&]row=${last}(&|$)`));
    await expect(page.locator(`tr[data-borrower-row="${last}"] .lead-table__borrower-btn`)).toBeFocused();
  });

  test('a 409 offers "Review draft again" and re-drafts only when it is clicked', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 409/);
    const writes = registerTriageWrites(mockApi, { conflictOnce: true });
    await app.gotoRoute('/lead-queue');
    await enterDeck(page);
    await page.keyboard.press('a');
    await expect(page.locator('[data-testid="lead-approve-review"][data-review-phase="ready"]')).toBeVisible();
    await page.keyboard.press('Enter');
    const redraft = page.getByTestId('lead-approve-review-redraft');
    await expect(redraft).toHaveText('Review draft again');
    expect(writes.drafts, 'never re-drafted automatically').toHaveLength(1);
    await expect(position(page)).toHaveText(`Borrower 1 of ${CARDS.length}`);
    await redraft.click();
    await expect.poll(() => writes.drafts.length).toBe(2);
  });

  test('a filter change never re-snapshots the deck: it stays on the placeholder rows, then only drops cards', async ({ app, mockApi, page }) => {
    registerTriageWrites(mockApi);
    await app.gotoRoute('/lead-queue');
    await enterDeck(page);
    await page.keyboard.press('a');
    await expect(page.locator('[data-testid="lead-approve-review"][data-review-phase="ready"]')).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(position(page)).toHaveText(`Borrower 2 of ${CARDS.length}`);
    await page.keyboard.press('j');
    await expect(position(page)).toHaveText(`Borrower 3 of ${CARDS.length}`);
    const progress = page.getByTestId('triage-progress');
    const receipt = page.getByTestId('triage-last-receipt');
    await expect(progress).toContainText('Approved 1 · Rejected 0 · Skipped 1');
    await expect(receipt).toContainText(`Approved ${CARDS[0]} · audit`);

    // STATE=TX while its GET /api/leads is held: the table holds placeholder rows.
    const held = holdLeadsForState(mockApi, 'TX');
    const menu = await app.openFilterMenu('STATE');
    await menu.getByRole('option', { name: 'TX', exact: true }).click();
    await expect.poll(() => held.received, 'the TX page was asked for').toBe(true);
    await expect(page).toHaveURL(/[?&]mode=triage(&|$)/);
    await expect(page.getByTestId('triage-deck'), 'placeholder rows never unmount the deck').toBeVisible();
    await expect(page.getByTestId('triage-deck-loading')).toHaveCount(0);
    await expect(progress).toContainText('Approved 1 · Rejected 0 · Skipped 1');
    held.release();

    // Settled: the TX rows hold one pending card; the approved card stays counted.
    const texas = CARDS.filter((id) => LEADS.find((lead) => lead.borrower_id === id)?.state === 'TX');
    expect(texas, 'the fixture has one pending TX card').toHaveLength(1);
    await expect(position(page)).toHaveText('Borrower 2 of 2');
    await expect(page.locator(`[data-testid="triage-card-${texas[0]}"]`)).toBeVisible();
    await expect(progress).toContainText('Approved 1 · Rejected 0 · Skipped 0');
    await expect(receipt).toContainText(`Approved ${CARDS[0]} · audit`);
    // Previous (K; focus sits in the STATE filter, outside the deck, where
    // the letter keys are inert): the approved card, out of the loaded rows
    // now, still shows its outcome.
    await page.getByTestId('triage-back').click();
    await expect(position(page)).toHaveText('Borrower 1 of 2');
    await expect(page.locator(`[data-testid="triage-card-${CARDS[0]}"] [data-testid="triage-outcome"]`)).toBeVisible();
  });

  test('Copy link never carries the deck', async ({ app, page }) => {
    await page.addInitScript(() => {
      const written: string[] = [];
      (window as unknown as { __copied: string[] }).__copied = written;
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: (text: string) => { written.push(text); return Promise.resolve(); } },
      });
    });
    await app.gotoRoute('/lead-queue?state=IL');
    await enterDeck(page);
    await page.getByTestId('lead-queue-copy-link').click();
    const copied = await page.evaluate(() => (window as unknown as { __copied: string[] }).__copied);
    expect(copied).toHaveLength(1);
    expect(copied[0]).toContain('state=IL');
    expect(copied[0]).not.toContain('mode=');
  });

  test('a loan officer never sees the deck: no entry, and ?mode=triage is stripped', async ({ app, mockApi, page }) => {
    mockApi.register<SessionResponse>('GET', '/api/session', () => json<SessionResponse>({
      ...SESSION,
      can_approve: false,
      actor_email: 'lo.one@summit-mortgage.example',
    }));
    await app.gotoRoute('/lead-queue?mode=triage');
    await expect(page).not.toHaveURL(/[?&]mode=/);
    await expect(page.getByTestId('triage-deck')).toHaveCount(0);
    await expect(page.getByTestId('lead-triage-enter')).toHaveCount(0);
    await expect(page.locator('.tbl-wrap')).toBeVisible();
  });
});
